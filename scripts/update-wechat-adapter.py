"""Import a tagged local adapter release; never import its development checkout.

With --release VERSION, first run the adapter's own release script (bump, test,
commit, tag, build), then import its wheel, relock, resync and run the offline tests.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def git(repository, *args):
    return subprocess.check_output(['git', *args], cwd=repository, text=True).strip()


def import_release(wheel, repository, *, root=ROOT):
    root, repository, wheel = Path(root).resolve(), Path(repository).resolve(), Path(wheel).resolve(strict=True)
    project = tomllib.loads((repository/'pyproject.toml').read_text(encoding='utf-8'))['project']
    version = project['version']
    if project['name'] != 'adapter_wc' or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('invalid_adapter_project')
    filename = f'adapter_wc-{version}-py3-none-any.whl'
    if wheel.name != filename or git(repository, 'status', '--porcelain', '--untracked-files=all'):
        raise ValueError('adapter_release_requires_clean_repository')
    commit, tag = git(repository, 'rev-parse', 'HEAD'), 'v'+version
    if git(repository, 'rev-parse', tag+'^{commit}') != commit:
        raise ValueError('adapter_release_tag_mismatch')
    sha256 = hashlib.sha256(wheel.read_bytes()).hexdigest()
    try:
        origin = os.path.relpath(repository, root).replace('\\', '/')
    except ValueError:
        origin = repository.name
    manifest = dict(name='adapter_wc', version=version, wheel=filename, sha256=sha256,
                    source=dict(repository=origin, commit=commit, tag=tag))
    verify = runpy.run_path(str(root/'scripts/adapter-wheel.py'))['adapter_wheel']
    temporary = root/'.test-tmp'
    temporary.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='adapter-import-', dir=temporary) as directory:
        candidate = Path(directory)/'dependencies'
        candidate.mkdir()
        shutil.copyfile(wheel, candidate/filename)
        (candidate/'adapter_wc.json').write_text(json.dumps(manifest), encoding='utf-8')
        verify(Path(directory))
    sources = repository/'src'  # adapter_wc's package directory is src/ itself
    expected = {'adapter_wc/'+p.relative_to(sources).as_posix(): p.read_bytes().replace(b'\r\n', b'\n')
                for p in sources.rglob('*') if p.is_file() and p.suffix in ('.py', '.js', '.json', '.c')
                and not any(part in ('__pycache__','build') for part in p.relative_to(sources).parts)}
    with zipfile.ZipFile(wheel) as archive:
        actual = {name: archive.read(name).replace(b'\r\n', b'\n') for name in archive.namelist()
                  if name.startswith('adapter_wc/')}
    if not expected or actual != expected:
        raise ValueError('adapter_wheel_does_not_match_source')
    project_file = root/'pyproject.toml'
    text = project_file.read_text(encoding='utf-8')
    text, count = re.subn(r'"adapter_wc==[0-9]+\.[0-9]+\.[0-9]+"',
                         '"adapter_wc=='+version+'"', text)
    if count != 1:
        raise ValueError('adapter_dependency_pin_missing')
    text, count = re.subn(r'adapter_wc = \{ path = "dependencies/adapter_wc-[^"/]+\.whl" \}',
                         'adapter_wc = { path = "dependencies/'+filename+'" }', text)
    if count != 1:
        raise ValueError('adapter_dependency_source_missing')
    destination = root/'dependencies'
    destination.mkdir(exist_ok=True)
    target = destination/filename
    old_file = destination/'adapter_wc.json'
    old = json.loads(old_file.read_text(encoding='utf-8')) if old_file.exists() else None
    if target.is_symlink():
        raise ValueError('adapter_version_already_exists_with_different_content')
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != sha256:
        # ZIP timestamps/compression may differ across builds. Retain the pinned
        # bytes only when the existing manifest verifies and every entry agrees.
        if not old or old.get('wheel') != filename:
            raise ValueError('adapter_version_already_exists_with_different_content')
        verify(root)
        with zipfile.ZipFile(target) as existing, zipfile.ZipFile(wheel) as rebuilt:
            if (set(existing.namelist()) != set(rebuilt.namelist()) or any(
                    existing.read(name) != rebuilt.read(name) for name in existing.namelist())):
                raise ValueError('adapter_version_already_exists_with_different_content')
        manifest['sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
    if not target.exists():
        shutil.copyfile(wheel, target)
    old_file.write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    project_file.write_text(text, encoding='utf-8')
    if old and old.get('wheel') != filename:
        old_name = old.get('wheel', '')
        if re.fullmatch(r'adapter_wc-[0-9]+\.[0-9]+\.[0-9]+-py3-none-any\.whl', old_name):
            old_path = destination/old_name
            if (not old_path.is_symlink() and old_path.is_file()
                    and hashlib.sha256(old_path.read_bytes()).hexdigest() == old.get('sha256')):
                old_path.unlink()
    return manifest


SYNC = ('uv', 'sync', '--locked', '--extra', 'database', '--extra', 'login', '--group', 'test')


def release_and_import(version, repository, *, root=ROOT, run=subprocess.run, importer=import_release):
    """Release the adapter at version from its clean checkout and adopt it here."""
    root, repository = Path(root).resolve(), Path(repository).resolve()
    run([sys.executable, str(repository/'scripts/release.py'), version], cwd=repository, check=True)
    value = importer(repository/'dist'/f'adapter_wc-{version}-py3-none-any.whl', repository, root=root)
    for step in ((sys.executable, 'scripts/dependencies.py'), SYNC,
                 ('uv', 'run', '--locked', '--no-sync', 'python', '-X', 'utf8', 'scripts/test-offline.py')):
        print('+', ' '.join(step), flush=True)
        run(list(step), cwd=root, check=True)
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('wheel', type=Path, nargs='?', help='a wheel already built from a clean release tag')
    source.add_argument('--release', metavar='VERSION', help='release this adapter version first, then import it')
    parser.add_argument('--source-repo', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.release:
        value = release_and_import(args.release, args.source_repo)
        print(f"Released and imported {value['name']} {value['version']} ({value['source']['commit'][:7]}); offline tests passed.")
        print('Review pyproject.toml, uv.lock, requirements*.txt and dependencies/ before committing.')
        return
    value = import_release(args.wheel, args.source_repo)
    print(f"Imported {value['name']} {value['version']} ({value['source']['commit'][:7]}).")
    print('Run python scripts/dependencies.py, then ' + ' '.join(SYNC) + '.')


if __name__ == '__main__':
    main()
