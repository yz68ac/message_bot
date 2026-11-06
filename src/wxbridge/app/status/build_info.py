"""安全的运行版本标识；开发目录使用 Git 提交号，无法确定时为 unknown。"""
from functools import lru_cache
import re
import subprocess

from wxbridge.config import PROJECT_ROOT
from wxbridge.paths import SOURCE_ROOT


@lru_cache(maxsize=1)
def code_revision():
    try:
        if SOURCE_ROOT is None:
            return 'unknown'
        result = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=PROJECT_ROOT,
                                capture_output=True, text=True, timeout=3, check=True)
        revision = result.stdout.strip()
        return revision if re.fullmatch('[a-f0-9]{40}', revision) else 'unknown'
    except (OSError, ValueError, subprocess.SubprocessError):
        return 'unknown'


def runtime_identity(cfg):
    from wxbridge.config import configuration_hash
    return dict(commit=code_revision(), config_hash=configuration_hash(cfg))
