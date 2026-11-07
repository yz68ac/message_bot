"""Bounded, redacted views of the fixed runtime log files; HTTP never supplies a path.

Warnings, errors and tracebacks are shown in full after secrets are masked. Ordinary
INFO/DEBUG lines from components that handle chats only keep their event name and
code-like key=value fields, so normal message flow never reaches the page.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import threading
import time

from wxbridge.app.status.snapshot import EVENTS, read_bytes
from wxbridge.control import BOT, HELPER, RECOVERY, SESSION, WEB
from wxbridge.management.repository import ManagementError

SUPERVISOR = 'supervisor'
RUNTIME_DIR = Path('/run/wxbridge-trial')
# Component id -> file written by the supervisor in the runtime directory.
FILES = {BOT: 'bot-live.log', SESSION: 'session-supervisor.log', HELPER: 'sender.log',
         WEB: 'web.log', RECOVERY: 'recovery.log', SUPERVISOR: 'supervisor.log'}
SOURCES = tuple(FILES)
# The web console and the supervisor never see chat text; the other components may.
PUBLIC = frozenset((WEB, SUPERVISOR))
LEVELS = {'DEBUG': 10, 'INFO': 20, 'WARNING': 30, 'ERROR': 40, 'CRITICAL': 50}
FILTERS = {'all': 0, 'warning': 30, 'error': 40}
WINDOW = 512 * 1024
MAX_TEXT, MAX_DETAIL_LINES, MAX_DETAIL_CHARS = 2000, 160, 20000

_LEVEL = r'(DEBUG|INFO|WARNING|ERROR|CRITICAL)'
SHORT = re.compile(r'^(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2}) '+_LEVEL+r' ?(.*)$')
LONG = re.compile(r'^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:[,.]\d+)? '+_LEVEL+r':? ?(.*)$')
PREFIX = re.compile(r'^'+_LEVEL+r':\s+(.*)$')
TRACEBACK = ('Traceback (most recent call last):', 'During handling of the above exception',
             'The above exception was the direct cause')
ERROR_WORDS = re.compile(r'(?i)\b(error|exception|fatal|critical|panic|failed|failure|denied|refused|segmentation fault|killed)\b')
WARNING_WORDS = re.compile(r'(?i)\bwarn(?:ing)?\b|Warning:')
# Structural fields that ordinary lines may keep; free-text keys such as text/body never match.
KEYS = re.compile(r'instance|task|version|chunks|images|questions|references|part|revision|attempt|messages|kind|mode'
                  r'|reason|category|error|retryable|code|pid|status|phase|state|model|provider|chars|count'
                  r'|round|rounds|depth|size|limit|cursor|workers|intentional|finish|native_finish|elapsed'
                  r'|[a-z]+_(?:id|count|seconds|ms|chars|code|reason|state|phase|error|kind|mode|limit|size|index)')
FIELD = re.compile(r'(?<![\w.])([A-Za-z_][A-Za-z0-9_]{0,31})=([A-Za-z0-9_.:/@+-]{1,96})(?=[\s,，]|$)')
CONTROL = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07?|[\x00-\x08\x0b-\x1f\x7f]')
SECRETS = (
    (re.compile(r'(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}'), r'\1 [已隐藏]'),
    (re.compile(r'(?i)\b((?:x-)?(?:goog-)?api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|passwd'
                r'|authorization|cookie|session)(["\']?\s*[:=]\s*["\']?)[^\s"\'&,;]+'), r'\1\2[已隐藏]'),
    (re.compile(r'(?i)([?&](?:key|sig|signature|token|access_token|api_key|auth)=)[^&\s"\']+'), r'\1[已隐藏]'),
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


def stamp_time(match, now):
    """Return epoch seconds for a log header; the short bot format has no year."""
    groups = [int(value) for value in match.groups()[:-2]]
    try:
        if len(groups) == 5:
            year = time.localtime(now).tm_year
            value = time.mktime((year, *groups, 0, 0, -1))
            if value > now+86400:
                value = time.mktime((year-1, *groups, 0, 0, -1))
            return value
        return time.mktime((*groups, 0, 0, -1))
    except (OverflowError, ValueError):
        return None


def project(message, structured):
    """Event name plus code-like fields; never the free text of an ordinary line.

    A logging header starts with a developer-written event name; a raw output line may
    start with anything, so only known events or identifier-like words are kept there.
    """
    name = next((event for event in EVENTS if message == event or message.startswith((event+' ', event+'，'))), None)
    if name is None:
        name = re.split(r'[\s，,:：]', message.strip(), maxsplit=1)[0][:64]
        if '=' in name or not structured and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]*', name):
            name = ''
    parts = [name] if name else []
    parts += [key+'='+value for key, value in FIELD.findall(message) if KEYS.fullmatch(key)]
    if 'metadata=' in message:
        try:
            metadata = json.loads(message.split('metadata=', 1)[1])
        except (ValueError, RecursionError):
            metadata = None
        if isinstance(metadata, dict):
            parts += [f'{key}={metadata[key]}' for key in ('local_id', 'reason', 'error', 'kind', 'category')
                      if type(metadata.get(key)) in (str, int) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,96}', str(metadata[key]))]
    return clean(' '.join(parts) or '（内容已隐藏）', 400)


def raw_level(line):
    try:
        value = json.loads(line)
    except (ValueError, RecursionError):
        value = None
    if isinstance(value, dict):
        return 'ERROR' if value.get('error') or value.get('ok') is False else 'INFO'
    return 'ERROR' if ERROR_WORDS.search(line) else 'WARNING' if WARNING_WORDS.search(line) else 'INFO'


def parse(text, source, *, now=None):
    """Group raw lines into entries, oldest first. Tracebacks stay with their error."""
    now = time.time() if now is None else now
    entries, current, inside, last_at = [], None, False, None

    def start(level, message, raw, at=None, stamp=''):
        entry = dict(level=level, message=message, at=at if stamp else last_at, stamp=stamp,
                     approx=not stamp and last_at is not None, raw=raw, detail=[], crash=False)
        entries.append(entry)
        return entry

    for line in text.splitlines():
        header = SHORT.match(line) or LONG.match(line)
        if header:
            at = stamp_time(header, now)
            last_at = at if at is not None else last_at
            current = start(header.group(header.re.groups-1), header.group(header.re.groups), False,
                            at, line[:14] if header.re is SHORT else line[:19])
            inside = False
            continue
        prefix = PREFIX.match(line)
        if prefix:
            current, inside = start(prefix.group(1), prefix.group(2), False), False
            continue
        if line.startswith(TRACEBACK):
            chained = not line.startswith(TRACEBACK[0])
            # log.exception() puts the traceback right under a WARNING/ERROR header.
            attach = current is not None and (inside or chained and current['detail'] or
                     not current['detail'] and not current['raw'] and LEVELS[current['level']] >= 30)
            if not attach:
                current = start('ERROR', '', True)
                current['crash'] = True
            current['detail'].append(line)
            inside = True
            continue
        if inside and current is not None:
            current['detail'].append(line)
            if line and not line[0].isspace():
                inside = False  # The exception line closes the traceback.
                if current['crash']:
                    current['message'] = line
            continue
        if not line.strip():
            continue
        current = start(raw_level(line), line, True)
    for entry in entries:
        if not entry['message']:
            entry['message'] = entry['detail'][0] if entry['detail'] else ''
    return [present(entry, source) for entry in collapse(entries)]


def collapse(entries):
    """Crash loops repeat the same failure; keep one entry with a count."""
    result = []
    for entry in entries:
        previous = result[-1] if result else None
        if (previous and previous['level'] == entry['level'] and previous['message'] == entry['message']
                and previous['detail'] == entry['detail']):
            previous['count'] += 1
            previous.update(at=entry['at'] if entry['at'] is not None else previous['at'],
                            stamp=entry['stamp'] or previous['stamp'], approx=entry['approx'])
        else:
            result.append(dict(entry, count=1))
    return result


def present(entry, source):
    full = source in PUBLIC or LEVELS[entry['level']] >= 30
    detail = entry['detail']
    if len(detail) > MAX_DETAIL_LINES:
        detail = [*detail[:8], f'…（省略 {len(detail)-MAX_DETAIL_LINES} 行）…', *detail[-(MAX_DETAIL_LINES-8):]]
    detail = clean('\n'.join(detail).rstrip('\n'), MAX_DETAIL_CHARS) if full else ''
    return dict(source=source, level=entry['level'], at=entry['at'], stamp=entry['stamp'], approx=entry['approx'],
                text=clean(entry['message']) if full else project(entry['message'], not entry['raw']),
                detail=detail, count=entry['count'], hidden=not full)


class LogReader:
    """Reads only fixed file names in one runtime directory and caches each parse."""

    def __init__(self, directory, fallback=None):
        self.directory = Path(directory)
        self.fallback = Path(fallback) if fallback else None
        self.lock, self.cache, self.summary = threading.Lock(), {}, None

    def path(self, source):
        path = self.directory/FILES[source]
        if source == BOT and self.fallback and not os.path.lexists(path):
            return self.fallback  # Without the supervisor the bot only writes its event log.
        return path

    def read(self, source):
        """Return (entries oldest first, info) for one source; never raises for file problems."""
        path = self.path(source)
        info = dict(id=source, available=False, size=0, updated_at=None, truncated=False, error='')
        try:
            if path.is_symlink():
                raise PermissionError()
            status = path.stat()
            key = (status.st_ino, status.st_size, status.st_mtime_ns)
            info.update(size=status.st_size, updated_at=status.st_mtime, truncated=status.st_size > WINDOW)
            with self.lock:
                cached = self.cache.get(source)
            if cached and cached[0] == key:
                info['available'] = True
                return cached[1], info
            data = read_bytes(path, WINDOW, tail=True)
            entries = parse(data.decode('utf-8', errors='replace'), source)
            with self.lock:
                self.cache[source] = (key, entries)
            info['available'] = True
            return entries, info
        except FileNotFoundError:
            info['error'] = 'missing'
        except PermissionError:
            info['error'] = 'denied'
        except (OSError, ValueError):
            info['error'] = 'unavailable'
        return [], info

    def query(self, source='all', level='all', q='', limit=200):
        if source != 'all' and source not in FILES:
            raise ManagementError('invalid_log_source', '不支持的日志来源。', 400)
        if level not in FILTERS:
            raise ManagementError('invalid_log_level', '不支持的日志级别。', 400)
        if not isinstance(q, str) or len(q) > 200:
            raise ManagementError('invalid_log_query', '搜索内容过长。', 400)
        limit = max(1, min(int(limit), 500))
        selected = SOURCES if source == 'all' else (source,)
        rows, infos = [], []
        for name in selected:
            entries, info = self.read(name)
            infos.append(info)
            rows += entries
        minimum, needle = FILTERS[level], q.strip().casefold()
        rows = [row for row in rows if LEVELS[row['level']] >= minimum
                and (not needle or needle in (row['text']+'\n'+row['detail']).casefold())]
        if source == 'all':
            # Untimed lines (no timestamp anywhere before them) sort after timed ones.
            rows.sort(key=lambda row: row['at'] if row['at'] is not None else float('-inf'))
        total = len(rows)
        rows = list(reversed(rows[-limit:]))
        return dict(source=source, level=level, entries=rows, total=total, limit=limit, sources=infos)

    def problems(self, *, now=None, window=86400, latest=5, reuse=10):
        """Warnings and errors from every source; the overview polls every 2 s, so reuse briefly."""
        started = time.monotonic()
        with self.lock:
            if now is None and self.summary and started-self.summary[0] < reuse:
                return self.summary[1]
        value = self._problems(time.time() if now is None else now, window, latest)
        with self.lock:
            self.summary = (started, value)
        return value

    def _problems(self, now, window, latest):
        rows, counts, errors = [], {}, {}
        for name in SOURCES:
            entries, info = self.read(name)
            if info['error'] and info['error'] != 'missing':
                errors[name] = info['error']
            for row in entries:
                if LEVELS[row['level']] >= 30 and row['at'] is not None and row['at'] >= now-window:
                    rows.append(row)
                    counts[name] = counts.get(name, 0)+row['count']
        rows.sort(key=lambda row: row['at'])
        recent = [dict(source=row['source'], level=row['level'], stamp=row['stamp'], approx=row['approx'],
                       at=row['at'], text=row['text'].splitlines()[0][:200] if row['text'] else '',
                       count=row['count']) for row in reversed(rows[-latest:])]
        return dict(count=sum(counts.values()), errors=sum(row['count'] for row in rows if LEVELS[row['level']] >= 40),
                    by_source=counts, latest=recent, read_errors=errors, window_seconds=window)
