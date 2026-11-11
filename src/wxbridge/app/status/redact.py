"""Masking of credentials and terminal control characters in text kept or shown for diagnosis.

Shared by the web console's log and trace views and by the trace recorder, which masks provider error text
before it is written to disk. Chat prompts are not passed through here.
"""
import re

MAX_TEXT = 2000
CONTROL = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07?|[\x00-\x08\x0b-\x1f\x7f]')
SECRETS = (
    (re.compile(r'(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}'), r'\1 [已隐藏]'),
    (re.compile(r'(?i)\b((?:x-)?(?:goog-)?api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|passwd'
                r'|authorization|cookie|session)(["\']?\s*[:=]\s*["\']?)[^\s"\'&,;]+'), r'\1\2[已隐藏]'),
    (re.compile(r'(?i)([?&](?:key|sig|signature|token|access_token|api_key|auth)=)[^&\s"\']+'), r'\1[已隐藏]'),
    # user:password@ in a URL, such as a proxy address echoed back in a connection error.
    (re.compile(r'(?<=://)[^/\s:@]+:[^@\s/]+@'), '[已隐藏]@'),
    (re.compile(r'\b(?:sk-(?:ant-)?|gh[pousr]_|github_pat_|xox[abprs]-|AIza)[A-Za-z0-9_-]{8,}'), '[密钥已隐藏]'),
    (re.compile(r'\b[0-9a-fA-F]{32,}\b'), '[长串已隐藏]'),
)
# Token-like runs mix digits with both letter cases; identifiers and paths do not.
TOKEN = re.compile(r'[A-Za-z0-9+_-]{40,}={0,2}')


def clean(value, limit=MAX_TEXT):
    value = CONTROL.sub('', value).replace('\t', '    ')
    for pattern, replacement in SECRETS:
        value = pattern.sub(replacement, value)
    value = TOKEN.sub(lambda match: '[长串已隐藏]' if all(re.search(kind, match.group()) for kind in ('[0-9]', '[a-z]', '[A-Z]'))
                      else match.group(), value)
    return value if len(value) <= limit else value[:limit]+'…'
