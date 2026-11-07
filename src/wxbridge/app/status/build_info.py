"""安全的运行版本标识：开发目录读取 Git，容器读取构建时写入的 BUILD_COMMIT；无法确定时为 unknown。"""
from functools import lru_cache
import re
import subprocess

from wxbridge.paths import SOURCE_ROOT

# Written by the Docker build, whose context excludes .git; see Dockerfile.
BUILD_COMMIT = 'BUILD_COMMIT'
COMMIT = re.compile('[a-f0-9]{40}')


def git_revision(root):
    result = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root,
                            capture_output=True, text=True, timeout=3, check=True)
    return result.stdout.strip()


def stamped_revision(root):
    with open(root/BUILD_COMMIT, encoding='ascii') as stream:
        return stream.read(128).strip()


@lru_cache(maxsize=1)
def code_revision():
    if SOURCE_ROOT is None:
        return 'unknown'
    for read in (git_revision, stamped_revision):
        try:
            revision = read(SOURCE_ROOT)
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
        if COMMIT.fullmatch(revision):
            return revision
    return 'unknown'


def runtime_identity(cfg):
    from wxbridge.config import configuration_hash
    return dict(commit=code_revision(), config_hash=configuration_hash(cfg))
