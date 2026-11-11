"""Read-only, bounded views of the development trace database; HTTP never supplies a path.

Prompts and replies are shown exactly as recorded: this is a development aid and the console is login-protected.
Only provider error text is masked, because a gateway may echo a credential back.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3

from wxbridge.app.status.trace import FILE, decode_calls
from wxbridge.management.repository import ManagementError
from web.backend.logs import clean

ROLES = ('reply', 'participation', 'memory', 'avatar', 'group_profile')
INSTANCE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,47}\Z')
RUN = re.compile(r'[0-9A-Za-z-]{1,40}\Z')
LIST_COLUMNS = ('id, ts, run_id, instance, role, task, version, attempt, ok, error, model, elapsed_ms, tokens_in, '
                'tokens_out, finish, calls_n, summary')
# A reply that never produced text, or a call the provider cut short, is a problem even without an exception.
PROBLEM = ("(ok=0 OR error!='' OR finish IN ('length','content_filter','pause_turn','incomplete_response',"
           "'invalid_tool_calls') OR (role='reply' AND result=''))")
MAX_DETAIL_CHARS = 3_000_000


def invalid(code, message):
    return ManagementError(code, message, 400)


class TraceReader:
    """Opens the database per request, read-only; a missing or busy file is reported, never raised."""

    def __init__(self, directory):
        self.path = Path(directory)/FILE if directory else None

    def _connect(self):
        if self.path is None:
            raise FileNotFoundError()
        if self.path.is_symlink():
            raise PermissionError()
        db = sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro', uri=True, timeout=3)
        try:
            db.execute('PRAGMA query_only=ON')
            db.row_factory = sqlite3.Row
        except BaseException:
            db.close()
            raise
        return db

    def _read(self, work, empty):
        """Run work(db); map file problems to an `error` code the page can explain."""
        try:
            db = self._connect()
        except FileNotFoundError:
            return dict(empty, available=False, error='missing')
        except PermissionError:
            return dict(empty, available=False, error='denied')
        except sqlite3.OperationalError:  # A read-only open of a missing file fails this way.
            return dict(empty, available=False, error='unavailable' if self.path and self.path.exists() else 'missing')
        try:
            return dict(work(db), available=True, error='')
        except sqlite3.Error:
            return dict(empty, available=False, error='unavailable')
        finally:
            db.close()

    def list(self, instance='', role='', task=None, run='', problems=False, q='', limit=100, before=None):
        if instance and not INSTANCE.fullmatch(instance):
            raise invalid('invalid_trace_instance', '实例名称无效。')
        if role and role not in ROLES:
            raise invalid('invalid_trace_role', '不支持的追踪类型。')
        if run and not RUN.fullmatch(run):
            raise invalid('invalid_trace_run', '运行编号无效。')
        if not isinstance(q, str) or len(q) > 200:
            raise invalid('invalid_trace_query', '搜索内容过长。')
        limit = max(1, min(int(limit), 200))
        where, args = [], []
        for column, value in (('instance', instance), ('role', role), ('run_id', run)):
            if value:
                where.append(f'{column}=?')
                args.append(value)
        if task is not None:
            where.append('task=?')
            args.append(int(task))
        if problems:
            where.append(PROBLEM)
        needle = q.strip()
        if needle:
            like = '%'+needle.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')+'%'
            where.append("(summary LIKE ? ESCAPE '\\' OR result LIKE ? ESCAPE '\\' OR error LIKE ? ESCAPE '\\')")
            args += [like, like, like]
        clause = ' WHERE '+' AND '.join(where) if where else ''

        def work(db):
            total = db.execute('SELECT COUNT(*) FROM jobs'+clause, args).fetchone()[0]
            page = ' AND id<?' if before is not None else ''
            rows = db.execute(f'SELECT {LIST_COLUMNS} FROM jobs{clause or " WHERE 1=1"}{page} ORDER BY id DESC LIMIT ?',
                              [*args, *([int(before)] if before is not None else []), limit]).fetchall()
            return dict(rows=[dict(row) for row in rows], total=total, limit=limit,
                        instances=[row[0] for row in db.execute('SELECT DISTINCT instance FROM jobs ORDER BY instance')])

        return self._read(work, dict(rows=[], total=0, limit=limit, instances=[]))

    def get(self, job_id):
        try:
            job_id = int(job_id)
        except (TypeError, ValueError):
            raise invalid('invalid_trace_id', '追踪编号无效。') from None

        def work(db):
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row is None:
                return dict(job=None)
            job = dict(row)
            calls, truncated, used = [], False, 0
            for call in decode_calls(job.pop('calls')):
                used += len(json.dumps(call, ensure_ascii=False))
                if used > MAX_DETAIL_CHARS:
                    truncated = True
                    break
                if call.get('error_detail'):
                    call['error_detail'] = clean(str(call['error_detail']), 2000)
                calls.append(call)
            for key in ('meta', 'context'):
                try:
                    job[key] = json.loads(job[key]) if job[key] else None
                except ValueError:
                    job[key] = None
            same = 'instance=? AND run_id=?'
            related = [dict(r) for r in db.execute(
                f'SELECT id, ts, attempt, version, ok, error FROM jobs WHERE {same} AND role=? AND task IS ? ORDER BY id',
                (job['instance'], job['run_id'], job['role'], job['task']))] if job['task'] is not None else []
            if job['role'] == 'participation':
                events = db.execute(f'SELECT * FROM events WHERE {same} AND kind LIKE ? AND ts>=? AND ts<=? ORDER BY id',
                                    (job['instance'], job['run_id'], 'participation%', job['ts']-1,
                                     job['ts']+job['elapsed_ms']/1000+30)).fetchall()
            elif job['task'] is not None:
                events = db.execute(f'SELECT * FROM events WHERE {same} AND task=? ORDER BY id',
                                    (job['instance'], job['run_id'], job['task'])).fetchall()
            else:
                events = []
            parsed = []
            for event in events:
                item = dict(event)
                try:
                    item['detail'] = json.loads(item['detail'])
                except ValueError:
                    item['detail'] = {}
                parsed.append(item)
            return dict(job=dict(job, calls=calls, truncated=truncated), related=related, events=parsed)

        return self._read(work, dict(job=None, related=[], events=[]))

    def locate(self, instance, role, task, at=None):
        """The job a log line is about, for links from the log page.

        Task ids restart with the runtime directory, so the newest job that started no later than the log line
        wins (a few seconds of slack for the line being written just after the job was queued).
        """
        if not INSTANCE.fullmatch(instance or '') or role not in ROLES:
            raise invalid('invalid_trace_reference', '追踪引用无效。')

        def work(db):
            limit, args = '', [instance, role, int(task)]
            if at is not None:
                limit = ' AND ts<=?'
                args.append(float(at)+5)
            row = db.execute(f'SELECT id FROM jobs WHERE instance=? AND role=? AND task=?{limit} ORDER BY ts DESC, id DESC LIMIT 1',
                             args).fetchone()
            return dict(id=row[0] if row else None)

        return self._read(work, dict(id=None))
