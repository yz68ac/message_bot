"""Fixed account-container layout inherited by supervised components."""
import os
from pathlib import Path


def code_root():
    return Path('/opt/wxbridge')


def config_root():
    return Path('/var/lib/wxbridge-config')


def sensitive_root():
    return Path('/run/wxbridge-sensitive')


def environment():
    values = {'WXBRIDGE_RUNTIME': 'container'}
    for prefix in ('OPENAI','ANTHROPIC','DEEPSEEK','GEMINI'):
        for suffix in ('BASE_URL','PROXY_URL','IMAGE_BASE_URL'):
            name = prefix+'_'+suffix
            if name in os.environ:
                values[name] = os.environ[name]
    return values
