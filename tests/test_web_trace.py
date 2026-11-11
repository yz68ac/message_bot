"""Console side of the development trace: the read-only reader, log links and the persistent problems source."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from web.backend import trace as reader_module
from web.backend.demo import seed_trace
from web.backend.logs import PROBLEMS, LogReader, parse, trace_ref
from web.backend.trace import TraceReader
from wxbridge.app.status import trace
from wxbridge.management.repository import ManagementError

SECRET = 'sk-ant-api03-'+'Q'*24
TEMP = Path(__file__).resolve().parents[1]/'.test-tmp'


def job_row(**changes):
    row = dict(ts=time.time(), run_id='r1', instance='alpha', role='reply', task=1, version=1, attempt=1, meta='{}',
               ok=1, error='', traceback='', model='m', elapsed_ms=1.0, tokens_in=0, tokens_out=0, finish='stop',
               calls_n=1, summary='', result='答', context='', calls=trace.zlib.compress(b'[]'), size=10)
    row.update(changes)
    return row


class ReaderCase(unittest.TestCase):
    def setUp(self):
        TEMP.mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=TEMP)
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)/'trace'
        self.reader = TraceReader(self.directory)

    def write(self, jobs=(), events=()):
        self.directory.mkdir(exist_ok=True)
        store = trace.TraceStore(self.directory)
        for row in jobs:
            store.submit('job', row)
        for row in events:
            store.submit('event', row)
        self.assertTrue(store.flush())
        store.close()


class ReaderTests(ReaderCase):
    def test_demo_data_lists_newest_first_and_filters(self):
        seed_trace(self.directory)
        page = self.reader.list()
        self.assertTrue(page['available'])
        self.assertEqual((page['total'], page['instances']), (3, ['private_demo']))
        self.assertEqual([row['id'] for row in page['rows']], [3, 2, 1])
        self.assertNotIn('calls', page['rows'][0])
        self.assertEqual([row['id'] for row in self.reader.list(role='reply')['rows']], [2, 1])
        self.assertEqual([row['id'] for row in self.reader.list(task=11)['rows']], [2, 1])
        self.assertEqual([row['id'] for row in self.reader.list(problems=True)['rows']], [1])
        self.assertEqual([row['id'] for row in self.reader.list(q='咖啡豆')['rows']], [2, 1])
        self.assertEqual(self.reader.list(instance='nobody')['total'], 0)
        self.assertEqual([row['id'] for row in self.reader.list(limit=1)['rows']], [3])
        self.assertEqual([row['id'] for row in self.reader.list(before=3, limit=1)['rows']], [2])
        self.assertEqual(self.reader.list(limit=10_000)['limit'], 200)

    def test_inputs_are_validated_and_search_wildcards_are_literal(self):
        for arguments in (dict(role='admin'), dict(instance='../x'), dict(run='a b'), dict(q='x'*201)):
            with self.subTest(arguments), self.assertRaises(ManagementError) as caught:
                self.reader.list(**arguments)
            self.assertEqual(caught.exception.status, 400)
        self.write([job_row(summary='涨了 100%_真的'), job_row(summary='1000x 不相关'), job_row(summary='a\\b')])
        self.assertEqual(self.reader.list(q='100%_')['total'], 1)
        self.assertEqual(self.reader.list(q='%')['total'], 1)
        self.assertEqual(self.reader.list(q='a\\b')['total'], 1)

    def test_failures_and_cut_off_calls_count_as_problems_but_a_textless_success_does_not(self):
        self.write([job_row(result=''), job_row(finish='length'), job_row(role='memory', result=''),
                    job_row(), job_row(ok=0, error='x')])
        self.assertEqual([row['id'] for row in self.reader.list(problems=True)['rows']], [5, 2])

    def event_row(self, kind, task=None, ts=None, run_id='r1', **detail):
        return dict(ts=time.time() if ts is None else ts, run_id=run_id, instance='alpha', task=task, version=1,
                    kind=kind, detail=json.dumps(detail))

    def test_a_memory_job_does_not_show_the_reply_events_of_the_same_task_number(self):
        self.write([job_row(role='memory', task=5), job_row(task=5)],
                   [self.event_row('reply_final', task=5, chunks=['好'])])
        self.assertEqual(self.reader.get(1)['events'], [])
        self.assertEqual([item['kind'] for item in self.reader.get(2)['events']], ['reply_final'])

    def test_participation_events_are_matched_by_their_messages_not_only_by_time(self):
        now = time.time()
        self.write([job_row(role='participation', task=None, ts=now-20, meta=json.dumps(dict(source_ids=[1, 2]))),
                    job_row(role='participation', task=None, ts=now-10, meta=json.dumps(dict(source_ids=[3])))],
                   [self.event_row('participation_decision', ts=now-19, source_ids=[1, 2], outcome='silent'),
                    self.event_row('participation_decision', ts=now-9, source_ids=[3], outcome='approved')])
        self.assertEqual([item['detail']['outcome'] for item in self.reader.get(1)['events']], ['silent'])
        self.assertEqual([item['detail']['outcome'] for item in self.reader.get(2)['events']], ['approved'])

    def test_detail_has_calls_attempts_and_owner_events(self):
        seed_trace(self.directory)
        detail = self.reader.get(2)
        job = detail['job']
        self.assertEqual((job['role'], job['task'], job['attempt'], job['ok']), ('reply', 11, 2, 1))
        self.assertEqual(job['meta'], dict(source_ids=[9]))
        self.assertEqual(job['context']['selected'], 9)
        self.assertEqual(len(job['calls']), 2)
        self.assertEqual(job['calls'][1]['request']['messages'][-1]['role'], 'tool')
        self.assertEqual(job['calls'][0]['response']['tool_calls'][0]['function']['name'], 'web_search')
        user = json.loads(job['calls'][0]['request']['messages'][1]['content'])
        self.assertEqual(user['source_ids'], [9])
        self.assertNotIn('calls', {key for key in detail if key != 'job'})
        self.assertEqual([item['id'] for item in detail['related']], [1, 2])
        self.assertEqual([item['kind'] for item in detail['events']], ['reply_failed', 'reply_final'])
        self.assertEqual(detail['events'][1]['detail']['chunks'], ['查了一下，生豆这周大概涨了 5% 左右。'])
        failed = self.reader.get(1)['job']
        self.assertEqual((failed['ok'], failed['error'], failed['calls'][0]['error']), (0, 'empty_reply', 'empty_reply'))
        participation = self.reader.get(3)
        self.assertEqual([item['kind'] for item in participation['events']], ['participation_decision'])

    def test_unknown_and_invalid_ids(self):
        seed_trace(self.directory)
        self.assertIsNone(self.reader.get(99)['job'])
        with self.assertRaises(ManagementError) as caught:
            self.reader.get('abc')
        self.assertEqual(caught.exception.status, 400)

    def test_provider_error_text_is_masked_but_prompts_are_not(self):
        calls = [dict(request=dict(messages=[dict(role='user', content='我的密钥是 '+SECRET)]), error='http_401',
                      error_detail='Authorization: Bearer '+SECRET)]
        self.write([job_row(calls=trace.zlib.compress(json.dumps(calls).encode()))])
        call = self.reader.get(1)['job']['calls'][0]
        self.assertNotIn(SECRET, call['error_detail'])
        self.assertIn('已隐藏', call['error_detail'])
        self.assertIn(SECRET, call['request']['messages'][0]['content'])

    def test_oversized_details_are_cut_with_a_flag(self):
        calls = [dict(request=dict(messages=['x'*2000]), n=n) for n in range(5)]
        self.write([job_row(calls=trace.zlib.compress(json.dumps(calls).encode()))])
        with patch.object(reader_module, 'MAX_DETAIL_CHARS', 5000):
            job = self.reader.get(1)['job']
        self.assertTrue(job['truncated'])
        self.assertEqual(len(job['calls']), 2)

    def test_missing_corrupt_or_linked_files_are_reported_not_raised(self):
        self.assertEqual((self.reader.list()['available'], self.reader.list()['error']), (False, 'missing'))
        self.assertEqual(TraceReader(None).list()['error'], 'missing')
        self.assertEqual(self.reader.get(1)['error'], 'missing')
        self.directory.mkdir()
        (self.directory/trace.FILE).write_bytes(b'not a database'*50)
        self.assertEqual(self.reader.list()['error'], 'unavailable')
        (self.directory/trace.FILE).unlink()
        real = self.directory.parent/'real.sqlite3'
        real.write_bytes(b'')
        try:
            os.symlink(real, self.directory/trace.FILE)
        except (OSError, NotImplementedError):
            self.skipTest('symbolic links are not available')
        self.assertEqual(self.reader.list()['error'], 'denied')

    def test_connection_cannot_write(self):
        seed_trace(self.directory)
        db = self.reader._connect()
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute('DELETE FROM jobs')
        finally:
            db.close()
        self.assertEqual(self.reader.list()['total'], 3)

    def test_locate_prefers_the_job_that_started_before_the_log_line(self):
        now = time.time()
        self.write([job_row(ts=now-1000, task=5, run_id='old'), job_row(ts=now-100, task=5, run_id='new'),
                    job_row(ts=now-50, task=6)])
        self.assertEqual(self.reader.locate('alpha', 'reply', 5)['id'], 2)
        self.assertEqual(self.reader.locate('alpha', 'reply', 5, at=now-900)['id'], 1)
        self.assertEqual(self.reader.locate('alpha', 'reply', 5, at=now-95)['id'], 2)
        self.assertIsNone(self.reader.locate('alpha', 'reply', 5, at=now-5000)['id'])
        self.assertIsNone(self.reader.locate('alpha', 'memory', 5)['id'])
        with self.assertRaises(ManagementError):
            self.reader.locate('../x', 'reply', 5)
        with self.assertRaises(ManagementError):
            self.reader.locate('alpha', 'nope', 5)

    def test_locate_stays_within_the_run_of_the_log_line_and_does_not_reach_the_retry(self):
        now = time.time()
        marker = lambda ago, run: dict(ts=now-ago, run_id=run, instance='alpha', task=None, version=None,
                                       kind=trace.RUN_STARTED, detail='{}')
        self.write([job_row(ts=now-1000, task=3, run_id='old'),
                    job_row(ts=now-100, task=11, run_id='new', attempt=1), job_row(ts=now-97, task=11, run_id='new', attempt=2)],
                   [marker(2000, 'old'), marker(500, 'new')])
        # Task 3 of the new run is still running, so nothing is stored for it yet.
        self.assertIsNone(self.reader.locate('alpha', 'reply', 3, at=now-50)['id'])
        self.assertEqual(self.reader.locate('alpha', 'reply', 3, at=now-900)['id'], 1)
        # The failure line of attempt 1, written before the retry was queued three seconds later.
        self.assertEqual(self.reader.locate('alpha', 'reply', 11, at=now-99)['id'], 2)
        self.assertEqual(self.reader.locate('alpha', 'reply', 11, at=now-90)['id'], 3)


class LogLinkTests(unittest.TestCase):
    def test_lines_about_model_tasks_get_a_trace_reference_with_their_role(self):
        cases = {
            '生成失败 task=11 version=1 category=empty_reply instance=alpha': dict(instance='alpha', role='reply', task=11),
            '开始生成 task=3 version=1 questions=1 references=0 instance=b_2': dict(instance='b_2', role='reply', task=3),
            '记忆延后 task=8 category=ValueError instance=alpha': dict(instance='alpha', role='memory', task=8),
            '模型任务异常 instance=alpha kind=participation task=None category=KeyError': None,
            '模型任务异常 instance=alpha kind=generate task=4 category=KeyError': dict(instance='alpha', role='reply', task=4),
            '模型调用失败 instance=alpha kind=group_profile task=2 error=http_500 status=500': dict(instance='alpha', role='group_profile', task=2),
            '生成失败 task=11 version=1 category=x': None,
            '接收判定 instance=alpha task=3': None,
            '停止监听 instance=alpha': None,
        }
        for message, expected in cases.items():
            with self.subTest(message):
                self.assertEqual(trace_ref(message), expected)

    def test_parsed_entries_and_the_problem_summary_carry_the_reference(self):
        text = '10-30 09:18:32 WARNING 生成失败 task=11 version=1 category=empty_reply instance=alpha\n'
        (row,) = parse(text, 'bot')
        self.assertEqual(row['trace'], dict(instance='alpha', role='reply', task=11))


class ProblemsSourceTests(ReaderCase):
    def setUp(self):
        super().setUp()
        self.runtime = self.directory.parent/'run'
        self.runtime.mkdir()
        self.directory.mkdir()
        stamp = time.strftime('%Y-%m-%d %H:%M:%S')
        (self.directory/trace.PROBLEMS).write_text(
            f'{stamp} WARNING 模型任务异常 instance=alpha kind=generate task=4 category=KeyError\n'
            'Traceback (most recent call last):\n  File "x.py", line 1, in f\nKeyError: boom\n', encoding='utf-8')
        (self.runtime/'bot-live.log').write_text(f'{stamp} WARNING 生成失败 task=4 category=KeyError instance=alpha\n', encoding='utf-8')
        self.logs = LogReader(self.runtime, problems=self.directory/trace.PROBLEMS)

    def test_the_persistent_source_is_selectable_with_traceback_and_link(self):
        page = self.logs.query(PROBLEMS)
        (entry,) = page['entries']
        self.assertEqual((entry['source'], entry['level'], entry['trace']['task']), (PROBLEMS, 'WARNING', 4))
        self.assertIn('KeyError: boom', entry['detail'])
        self.assertEqual([info['id'] for info in page['sources']], [PROBLEMS])
        self.assertTrue(page['sources'][0]['available'])

    def test_it_is_not_part_of_all_and_is_not_counted_twice(self):
        self.assertEqual({row['source'] for row in self.logs.query('all')['entries']}, {'bot'})
        summary = self.logs.problems()
        self.assertEqual((summary['count'], summary['by_source']), (1, {'bot': 1}))
        self.assertEqual(summary['latest'][0]['trace']['task'], 4)

    def test_unknown_sources_are_still_refused_and_a_missing_file_is_reported(self):
        with self.assertRaises(ManagementError):
            self.logs.query('problems.log')
        missing = LogReader(self.runtime, problems=self.directory/'absent.log').query(PROBLEMS)
        self.assertEqual((missing['entries'], missing['sources'][0]['error']), ([], 'missing'))
