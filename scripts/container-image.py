"""Build the container image once and move it to other hosts without rebuilding.

    python scripts/container-image.py export ../images      # on a build host, from a clean commit
    python scripts/container-image.py load ../images/wxbridge-image-<commit>.json   # on each target host

export builds the image, tags it wxbridge:<commit> and wxbridge:local, and writes
a compressed `docker save` archive with a JSON description (commit, adapter
version, SHA-256). load checks the archive against that description, loads it and
tags it wxbridge:local, so `docker compose up -d --no-build` uses it. Target hosts
need this checkout at the same commit (for compose.yaml), but not the WeChat
installer, Node or a build toolchain.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'wxbridge'


def git(*args, root=ROOT):
    return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def sha256(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save_compressed(tag, destination, *, popen=subprocess.Popen):
    """Stream `docker save` into a new gzip file; never leave a partial archive behind."""
    process = popen(['docker', 'save', tag], stdout=subprocess.PIPE)
    try:
        with gzip.open(destination, 'xb', compresslevel=6) as archive:
            shutil.copyfileobj(process.stdout, archive, 1024*1024)
    except BaseException:
        process.kill()
        Path(destination).unlink(missing_ok=True)
        raise
    finally:
        process.stdout.close()
    if process.wait() != 0:
        Path(destination).unlink(missing_ok=True)
        raise subprocess.CalledProcessError(process.returncode, ['docker', 'save', tag])


def export(out, *, root=ROOT, run=subprocess.run, save=save_compressed, status=git):
    root, out = Path(root), Path(out)
    if status('status', '--porcelain', '--untracked-files=all', root=root):
        raise ValueError('commit_changes_before_export')
    commit = status('rev-parse', 'HEAD', root=root)
    short = commit[:12]
    tag = f'{REPOSITORY}:{short}'
    adapter = json.loads((root/'dependencies/adapter_wc.json').read_text(encoding='utf-8'))['version']
    out.mkdir(parents=True, exist_ok=True)
    archive, description = out/f'wxbridge-image-{short}.tar.gz', out/f'wxbridge-image-{short}.json'
    if archive.exists() or description.exists():
        raise ValueError('image_export_exists')
    run(['docker', 'build', '-t', tag, '-t', f'{REPOSITORY}:local', str(root)], check=True)
    save(tag, archive)
    value = dict(format=1, image=tag, commit=commit, adapter_wc=adapter,
                 archive=archive.name, sha256=sha256(archive), bytes=archive.stat().st_size)
    with open(description, 'x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
    return value


def load(description, *, run=subprocess.run):
    description = Path(description)
    value = json.loads(description.read_text(encoding='utf-8'))
    if (value.get('format') != 1 or not re.fullmatch(rf'{REPOSITORY}:[a-f0-9]{{12}}', str(value.get('image')))
            or not re.fullmatch(r'wxbridge-image-[a-f0-9]{12}\.tar\.gz', str(value.get('archive')))):
        raise ValueError('invalid_image_description')
    archive = description.with_name(value['archive'])
    if sha256(archive) != value['sha256']:
        raise ValueError('image_archive_hash_mismatch')
    run(['docker', 'load', '-i', str(archive)], check=True)
    run(['docker', 'tag', value['image'], f'{REPOSITORY}:local'], check=True)
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='action', required=True)
    commands.add_parser('export', help='build and save the image from a clean commit').add_argument('out', type=Path)
    commands.add_parser('load', help='verify and load an exported image').add_argument('description', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == 'export':
            value = export(args.out)
            print(f"Exported {value['image']} ({value['bytes']} bytes) to {args.out}.")
            print('Copy both wxbridge-image-* files to the target host, then run: python scripts/container-image.py load <json>')
        else:
            value = load(args.description)
            print(f"Loaded {value['image']} (commit {value['commit'][:12]}, adapter_wc {value['adapter_wc']}) as wxbridge:local.")
            print(f"Check out commit {value['commit'][:12]} here, then run: docker compose up -d --no-build")
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f'container-image failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
