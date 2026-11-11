"""Development trace: full prompts, raw model replies and owner-loop outcomes, kept across restarts.

The bot process writes ``trace.sqlite3`` through one writer thread; the web console reads it
read-only. Nothing here may raise into the bot or block it: a full queue drops the record.
Recorded text contains chat content on purpose (development aid); ``trace.enabled`` switches it off.
"""
from __future__ import annotations

from contextvars import ContextVar
import json
import logging
import math
import os
from pathlib import Path
import queue
import re
import sqlite3
import threading
import time
import traceback as traceback_module
import uuid
import zlib

log = logging.getLogger('wxbridge.trace')

FILE = 'trace.sqlite3'
PROBLEMS = 'problems.log'
# Task ids restart from 1 when the runtime directory is wiped, so every record names its process run.
RUN_ID = time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:4]
KEEP_SECONDS, MAX_BYTES, PRUNE_SECONDS, QUEUE_SIZE = 14*86400, 300*1024*1024, 300, 256
MAX_STRING, MAX_CALLS, MAX_TRACEBACK = 200_000, 40, 20_000
JOB_ROLES = {'generate': 'reply', 'memory': 'memory', 'participation': 'participation', 'group_profile': 'group_profile'}
DATA_URI = re.compile(r'^data:([\w.+/-]+);base64,')
OPAQUE_KEYS = re.compile(r'(?i)^(signature|encrypted_content|encrypted)$')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, run_id TEXT NOT NULL, instance TEXT NOT NULL,
    role TEXT NOT NULL, task INTEGER, version INTEGER, attempt INTEGER, meta TEXT NOT NULL DEFAULT '{}',
    ok INTEGER NOT NULL, error TEXT NOT NULL DEFAULT '', traceback TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '', elapsed_ms REAL NOT NULL DEFAULT 0, tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0, finish TEXT NOT NULL DEFAULT '', calls_n INTEGER NOT NULL DEFAULT 0,
    summary TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '', context TEXT NOT NULL DEFAULT '',
    calls BLOB NOT NULL, size INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS jobs_ts ON jobs(ts);
CREATE INDEX IF NOT EXISTS jobs_task ON jobs(instance, role, task);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, run_id TEXT NOT NULL, instance TEXT NOT NULL,
    task INTEGER, version INTEGER, kind TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS events_task ON events(instance, task);
'''
JOB_COLUMNS = ('ts', 'run_id', 'instance', 'role', 'task', 'version', 'attempt', 'meta', 'ok', 'error', 'traceback',
               'model', 'elapsed_ms', 'tokens_in', 'tokens_out', 'finish', 'calls_n', 'summary', 'result',
               'context', 'calls', 'size')
EVENT_COLUMNS = ('ts', 'run_id', 'instance', 'task', 'version', 'kind', 'detail')


def trace_directory(memory_directory):
    """Persistent trace directory next to the memory directory, resolved the way MemoryController does."""
    from wxbridge.config import PROJECT_ROOT
    directory = Path(memory_directory)
    if not directory.is_absolute():
        directory = PROJECT_ROOT/directory
    return directory.parent/'trace'


def sanitize(value, depth=0):
    """JSON-safe copy: data URIs and opaque signatures become placeholders, long strings are capped."""
    if depth > 24:
        return '[嵌套过深]'
    if isinstance(value, str):
        match = DATA_URI.match(value)
        if match:
            kind = '图片' if match.group(1).startswith('image/') else '数据'
            return f'[{kind} {match.group(1)}, {len(value)*3//4//1024} KB]'
        return value if len(value) <= MAX_STRING else value[:MAX_STRING]+f'…（已截断，共 {len(value)} 字符）'
    if isinstance(value, dict):
        return {str(key): (f'[已省略 {len(item)} 字符]' if OPAQUE_KEYS.match(str(key)) and isinstance(item, str)
                           else sanitize(item, depth+1)) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(item, depth+1) for item in value]
    if isinstance(value, (bytes, bytearray)):
        return f'[{len(value)} 字节]'
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    return str(value)[:500]


def message_text(message):
    content = message.get('content') if isinstance(message, dict) else None
    if isinstance(content, list):
        return ' '.join(part.get('text', '') for part in content if isinstance(part, dict) and part.get('type') == 'text')
    return content if isinstance(content, str) else ''


def summarize(messages, result=''):
    """One line for the list view: the new chat messages (or the last user text) and the answer."""
    question = ''
    user = next((m for m in reversed(messages or ()) if isinstance(m, dict) and m.get('role') == 'user'), None)
    if user is not None:
        text = message_text(user)
        try:
            payload = json.loads(text)
            ids = set(payload.get('source_ids') or ())
            question = ' / '.join(str(m.get('text') or '') for m in payload.get('messages') or () if m.get('id') in ids)
        except (ValueError, AttributeError, TypeError):
            question = text
    question, answer = ' '.join(question.split())[:160], ' '.join(str(result or '').split())[:160]
    return f'{question} → {answer}' if answer else question


class TraceStore:
    """Single writer thread over one SQLite file; submit() never blocks and never raises."""

    def __init__(self, directory, *, queue_size=QUEUE_SIZE, keep_seconds=KEEP_SECONDS, max_bytes=MAX_BYTES,
                 prune_seconds=PRUNE_SECONDS):
        self.path = Path(directory)/FILE
        self.keep_seconds, self.max_bytes, self.prune_seconds = keep_seconds, max_bytes, prune_seconds
        self.queue, self.dropped, self.failures = queue.Queue(queue_size), 0, 0
        self.thread = threading.Thread(target=self._run, name='trace-writer', daemon=True)
        self.thread.start()

    def submit(self, kind, row):
        try:
            self.queue.put_nowait((kind, row))
        except queue.Full:
            self.dropped += 1

    def flush(self, timeout=10):
        done = threading.Event()
        try:
            self.queue.put(('flush', done), timeout=timeout)
        except queue.Full:
            return False
        return done.wait(timeout)

    def close(self, timeout=5):
        try:
            self.queue.put(('stop', None), timeout=timeout)
        except queue.Full:
            return
        self.thread.join(timeout)

    def _open(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute('PRAGMA auto_vacuum=INCREMENTAL')  # only effective before the first table exists
            db.execute('PRAGMA journal_mode=DELETE')
            db.execute('PRAGMA synchronous=NORMAL')
            db.executescript(SCHEMA)
            if os.name != 'nt':
                os.chmod(self.path, 0o600)
        except BaseException:
            db.close()
            raise
        return db

    def _run(self):
        """Open the database only while writing a batch, so the file is never held between writes."""
        next_prune = 0
        while True:
            try:
                batch = [self.queue.get(timeout=self.prune_seconds)]
            except queue.Empty:
                batch = []
            while len(batch) < 50:
                try:
                    batch.append(self.queue.get_nowait())
                except queue.Empty:
                    break
            rows = [item for item in batch if item[0] in ('job', 'event')]
            waiters = [item[1] for item in batch if item[0] == 'flush']
            stop = any(item[0] == 'stop' for item in batch)
            due = time.monotonic() >= next_prune
            if rows or due:
                self._write(rows, due)
                if due:
                    next_prune = time.monotonic()+self.prune_seconds
            for _ in batch:
                self.queue.task_done()
            for event in waiters:
                event.set()
            if stop:
                return

    def _write(self, rows, prune):
        try:
            db = self._open()
        except Exception as exc:
            self._failed('trace_open_failed', exc)
            return
        try:
            for kind, row in rows:
                try:
                    self._insert(db, kind, row)
                except Exception as exc:
                    self._failed('trace_write_failed', exc)
            if prune:
                try:
                    self.prune(db)
                except Exception as exc:
                    self._failed('trace_prune_failed', exc)
        finally:
            db.close()

    def _failed(self, event, exc):
        self.failures += 1
        if self.failures == 1:  # Once: a broken store must not flood the log it also feeds.
            log.warning('%s category=%s', event, type(exc).__name__)

    @staticmethod
    def _insert(db, kind, row):
        table, columns = ('jobs', JOB_COLUMNS) if kind == 'job' else ('events', EVENT_COLUMNS)
        with db:
            db.execute(f'INSERT INTO {table} ({",".join(columns)}) VALUES ({",".join("?"*len(columns))})',
                       [row[name] for name in columns])

    def prune(self, db):
        cutoff = time.time()-self.keep_seconds
        with db:
            db.execute('DELETE FROM jobs WHERE ts<?', (cutoff,))
            db.execute('DELETE FROM events WHERE ts<?', (cutoff,))
        with db:
            # Keep the newest rows whose running size fits the cap; the row that crosses it and all older ones go.
            db.execute('DELETE FROM jobs WHERE id IN (SELECT id FROM '
                       '(SELECT id, SUM(size) OVER (ORDER BY id DESC) AS running FROM jobs) WHERE running>?)',
                       (self.max_bytes,))
        db.execute('PRAGMA incremental_vacuum')


_stores, _lock = {}, threading.Lock()


def store_for(cfg):
    """The process-wide store for an instance config, or None when tracing is off or unavailable."""
    if not getattr(getattr(cfg, 'trace', None), 'enabled', False):
        return None
    directory = trace_directory(cfg.paths.memory_directory)
    with _lock:
        if directory not in _stores:
            try:
                from wxbridge.storage.person_memory import private_directory
                _stores[directory] = TraceStore(private_directory(directory))
            except (OSError, ValueError) as exc:
                log.warning('trace_unavailable category=%s', type(exc).__name__)
                _stores[directory] = None
        return _stores[directory]


def close_stores(timeout=5):
    """Write what is still queued and stop the writer threads (bot shutdown, tests)."""
    with _lock:
        stores = [store for store in _stores.values() if store is not None]
        _stores.clear()
    for store in stores:
        store.close(timeout)


def record_event(cfg, kind, *, task=None, version=None, **detail):
    """Owner-loop outcome (final reply, failure fallback, discarded generation, ...). Never raises."""
    try:
        store = store_for(cfg)
        if store is not None:
            store.submit('event', dict(ts=time.time(), run_id=RUN_ID, instance=cfg.id, task=task, version=version,
                                       kind=kind, detail=json.dumps(sanitize(detail), ensure_ascii=False)))
    except Exception as exc:
        log.warning('trace_event_failed category=%s', type(exc).__name__)


_current = ContextVar('trace_job', default=None)


def current():
    return _current.get()


def activate(job):
    return _current.set(job)


def deactivate(token):
    _current.reset(token)


class JobTrace:
    """Everything one model job sent and received; written as one row when the job ends."""

    def __init__(self, store, meta):
        self.store, self.meta, self.calls, self.started = store, dict(meta), [], time.time()

    def begin(self, request, *, role, channel):
        """Serialize a request now; the conversation list is mutated by later tool rounds."""
        try:
            if len(self.calls) >= MAX_CALLS:
                return None
            tools = [(tool.get('function') or {}).get('name') for tool in request.tools or () if isinstance(tool, dict)]
            call = dict(role=role, channel=channel, model=request.model, at=time.time(), _metrics=[],
                        request=sanitize(dict(messages=request.messages, tools=tools, tool_choice=request.tool_choice,
                                              native_search=request.native_search, options=request.options,
                                              json_output=request.json_output)))
            self.calls.append(call)
            return call
        except Exception:
            return None

    @staticmethod
    def response(call, result):
        try:
            call.update(response=sanitize(result.message), finish_reason=result.finish_reason,
                        native_finish_reason=result.native_finish_reason, usage=sanitize(result.usage))
            if result.search:
                call['search'] = sanitize(result.search)
        except Exception:
            pass

    @staticmethod
    def failure(call, exc):
        try:
            call.update(error=getattr(exc, 'code', None) or type(exc).__name__,
                        error_detail=sanitize(getattr(exc, 'detail', '') or ''), status=getattr(exc, 'status', None))
        except Exception:
            pass

    def _calls(self):
        calls = []
        for call in self.calls:
            metrics = [m for m in call.pop('_metrics', None) or () if isinstance(m, dict)]
            last = metrics[-1] if metrics else {}
            # check_complete() rewrites the metrics error after request() returned, so read it only now.
            call['error'] = last.get('error') or call.get('error') or ''
            if last.get('finish_reason') and not call.get('finish_reason'):
                call['finish_reason'] = last['finish_reason']
            usage = call.get('usage') or last.get('usage') or {}
            call['usage'] = usage if isinstance(usage, dict) else {}
            call['elapsed_ms'] = round(sum(m.get('elapsed_ms') or 0 for m in metrics
                                           if isinstance(m.get('elapsed_ms'), (int, float))), 2)
            call['attempts'] = [sanitize({key: m.get(key) for key in ('provider', 'elapsed_ms', 'finish_reason',
                                                                       'native_finish_reason', 'error')}) for m in metrics]
            calls.append(call)
        return calls

    def finish(self, *, kind, ok, result=None, exc=None, context=None, elapsed_ms=None):
        """Write the job row. Called once, from the model lane thread; never raises.

        elapsed_ms defaults to the wall time since the job was created; seeded demo data passes its own.
        """
        try:
            calls = self._calls()
            error = '' if ok else (getattr(exc, 'code', None) or (type(exc).__name__ if exc else '') or 'failed')
            text = ''
            if kind == 'generate' and isinstance(result, dict):
                text = str(result.get('reply') or '')
            elif isinstance(result, dict):
                text = json.dumps(sanitize({k: v for k, v in result.items() if k != 'model_metrics'}), ensure_ascii=False)
            blob = zlib.compress(json.dumps(calls, ensure_ascii=False, default=str).encode('utf-8'), 6)
            trace = ''.join(traceback_module.format_exception(exc))[-MAX_TRACEBACK:] if exc is not None else ''
            def tokens(key):
                return sum(c['usage'].get(key, 0) for c in calls if type(c['usage'].get(key, 0)) is int)
            last = calls[-1] if calls else {}
            first = (calls[0].get('request') or {}).get('messages') if calls else None
            summary = summarize(first, text)
            context_json = json.dumps(sanitize(context), ensure_ascii=False) if context else ''
            meta = {key: value for key, value in self.meta.items() if key not in ('role', 'instance', 'task', 'version', 'attempt')}
            self.store.submit('job', dict(
                ts=self.started, run_id=RUN_ID, instance=self.meta.get('instance', ''), role=self.meta.get('role', JOB_ROLES.get(kind, kind)),
                task=self.meta.get('task'), version=self.meta.get('version'), attempt=self.meta.get('attempt'),
                meta=json.dumps(sanitize(meta), ensure_ascii=False), ok=int(bool(ok)), error=str(error), traceback=trace,
                model=str(last.get('model') or ''), elapsed_ms=round((time.time()-self.started)*1000, 2) if elapsed_ms is None else elapsed_ms,
                tokens_in=tokens('prompt_tokens'), tokens_out=tokens('completion_tokens'),
                finish=str(last.get('finish_reason') or ''), calls_n=len(calls), summary=summary, result=text,
                context=context_json, calls=blob, size=len(blob)+len(text)+len(trace)+len(summary)+len(context_json)))
        except Exception as exc:
            log.warning('trace_job_failed category=%s', type(exc).__name__)


def decode_calls(blob):
    return json.loads(zlib.decompress(blob).decode('utf-8'))
