"""Development trace: sanitizing, the single-writer store, per-job capture and the problems file."""
from dataclasses import replace
import json
import logging
from pathlib import Path
import sqlite3
import tempfile
from threading import Event
import time
from unittest import TestCase
from unittest.mock import Mock, patch

from provider import ModelError, ModelResult
from provider.client import _metrics
from tests.support.configuration import application, replace_config, update_config
from tests.support.model_support import default_config
from wxbridge.app.execution.model_runtime import ConversationModels
from wxbridge.app.status import trace
from wxbridge.app.status.logging_context import problem_file
from wxbridge.app.status.trace import TraceStore, decode_calls, record_event, sanitize, summarize, trace_directory

TEMP = Path(__file__).resolve().parents[1]/'.test-tmp'


def job_row(**changes):
    row = dict(ts=time.time(), run_id='r1', instance='alpha', role='reply', task=1, version=1, attempt=1, meta='{}',
               ok=1, error='', traceback='', model='m', elapsed_ms=1.0, tokens_in=0, tokens_out=0, finish='stop',
               calls_n=0, summary='', result='', context='', calls=b'x', size=10)
    row.update(changes)
    return row


class TempCase(TestCase):
    def setUp(self):
        TEMP.mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=TEMP)
        self.addCleanup(temp.cleanup)
        self.addCleanup(trace.close_stores)  # Runs before the directory is removed.
        self.root = Path(temp.name)

    def rows(self, sql, directory=None):
        db = sqlite3.connect((directory or self.root/'trace')/trace.FILE)
        try:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(sql)]
        finally:
            db.close()

    def traced(self, **sections):
        cfg = replace(default_config(), id='alpha')
        cfg = replace_config(cfg, 'search.enabled', False)
        cfg = replace_config(cfg, 'memory.directory', str(self.root/'memory'))
        return update_config(cfg, 'trace', enabled=True, **sections)


class SanitizeTests(TestCase):
    def test_data_uris_signatures_bytes_and_long_strings_are_replaced(self):
        value = sanitize(dict(
            content=[dict(type='image_url', image_url=dict(url='data:image/png;base64,'+'A'*4096)), dict(type='text', text='你好')],
            signature='x'*50, nested=dict(encrypted_content='y'*10), raw=b'12345', nan=float('nan'), none=None,
            long='z'*(trace.MAX_STRING+10), other=object))
        self.assertEqual(value['content'][0]['image_url']['url'], '[图片 image/png, 3 KB]')
        self.assertEqual(value['content'][1]['text'], '你好')
        self.assertEqual(value['signature'], '[已省略 50 字符]')
        self.assertEqual(value['nested']['encrypted_content'], '[已省略 10 字符]')
        self.assertEqual(value['raw'], '[5 字节]')
        self.assertEqual(value['nan'], 'nan')
        self.assertIsNone(value['none'])
        self.assertTrue(value['long'].endswith(f'共 {trace.MAX_STRING+10} 字符）'))
        self.assertIsInstance(value['other'], str)
        json.dumps(value)

    def test_sanitize_is_a_copy_and_bounds_nesting(self):
        source = dict(a=[1, 2])
        copy = sanitize(source)
        source['a'].append(3)
        self.assertEqual(copy['a'], [1, 2])
        deep = current = {}
        for _ in range(40):
            current['x'] = current = {}
        self.assertIn('嵌套过深', json.dumps(sanitize(deep), ensure_ascii=False))

    def test_summary_shows_the_new_messages_and_the_answer(self):
        payload = json.dumps(dict(messages=[dict(id=1, text='旧消息'), dict(id=2, text='现在几点'), dict(id=3, text='还有一问')],
                                  source_ids=[2, 3]), ensure_ascii=False)
        messages = [dict(role='system', content='规则'), dict(role='user', content=payload)]
        self.assertEqual(summarize(messages, '三点\n整'), '现在几点 / 还有一问 → 三点 整')
        self.assertEqual(summarize([dict(role='user', content=[dict(type='text', text='不是 JSON')])]), '不是 JSON')
        self.assertEqual(summarize(None), '')

    def test_trace_directory_follows_the_memory_directory_like_memory_does(self):
        absolute = Path(tempfile.gettempdir())/'wxbridge'/'memory'
        self.assertEqual(trace_directory(str(absolute)), absolute.parent/'trace')
        from wxbridge.config import PROJECT_ROOT
        self.assertEqual(trace_directory('memory'), PROJECT_ROOT/'trace')


class StoreTests(TempCase):
    def make(self, cls=TraceStore, **options):
        (self.root/'trace').mkdir()
        store = cls(self.root/'trace', **options)
        self.addCleanup(store.close)
        return store

    def test_jobs_and_events_round_trip_through_the_writer_thread(self):
        store = self.make()
        store.submit('job', job_row(task=7, calls=trace.zlib.compress(json.dumps([dict(a=1)]).encode())))
        store.submit('event', dict(ts=time.time(), run_id='r1', instance='alpha', task=7, version=1, kind='reply_final',
                                   detail=json.dumps(dict(chunks=['好']))))
        self.assertTrue(store.flush())
        jobs, events = self.rows('SELECT * FROM jobs'), self.rows('SELECT * FROM events')
        self.assertEqual((jobs[0]['task'], jobs[0]['instance']), (7, 'alpha'))
        self.assertEqual(decode_calls(jobs[0]['calls']), [dict(a=1)])
        self.assertEqual((events[0]['kind'], json.loads(events[0]['detail'])), ('reply_final', dict(chunks=['好'])))
        self.assertEqual(self.rows('PRAGMA auto_vacuum')[0]['auto_vacuum'], 2)

    def test_file_is_not_held_open_between_writes(self):
        store = self.make()
        store.submit('job', job_row())
        self.assertTrue(store.flush())
        (self.root/'trace'/trace.FILE).replace(self.root/'moved.sqlite3')  # Fails on Windows if still open.

    def test_prune_drops_old_rows_then_oldest_rows_over_the_size_cap(self):
        store = self.make(keep_seconds=1000, max_bytes=250)
        now = time.time()
        for age, size in ((5000, 10), (30, 100), (20, 100), (10, 100)):
            store.submit('job', job_row(ts=now-age, size=size))
        store.submit('event', dict(ts=now-5000, run_id='r', instance='a', task=1, version=1, kind='old', detail='{}'))
        self.assertTrue(store.flush())
        db = store._open()
        try:
            store.prune(db)
        finally:
            db.close()
        self.assertEqual([row['size'] for row in self.rows('SELECT size, ts FROM jobs ORDER BY id')], [100, 100])
        self.assertEqual(self.rows('SELECT COUNT(*) AS n FROM events')[0]['n'], 0)

    def test_full_queue_drops_records_instead_of_blocking_the_caller(self):
        gate = Event()

        class Stalled(TraceStore):
            def _write(self, rows, prune):
                gate.wait(5)
                super()._write(rows, prune)

        store = self.make(Stalled, queue_size=2)
        self.addCleanup(gate.set)
        store.submit('job', job_row(task=1))
        deadline = time.monotonic()+2
        while not store.queue.empty() and time.monotonic() < deadline:
            time.sleep(0.01)
        for task in range(2, 7):
            store.submit('job', job_row(task=task))
        self.assertEqual(store.dropped, 3)
        gate.set()
        self.assertTrue(store.flush())
        self.assertEqual(self.rows('SELECT COUNT(*) AS n FROM jobs')[0]['n'], 3)

    def test_a_broken_database_never_raises_and_warns_once(self):
        store = TraceStore(self.root/'missing'/'dir')
        self.addCleanup(store.close)
        with self.assertLogs('wxbridge.trace', 'WARNING') as logs:
            for _ in range(3):
                store.submit('job', job_row())
                self.assertTrue(store.flush())
        self.assertEqual(len(logs.records), 1)
        self.assertGreaterEqual(store.failures, 3)

    def test_one_bad_row_does_not_lose_the_rest_of_the_batch(self):
        store = self.make()
        store.submit('job', {'ts': 1})  # Missing columns.
        store.submit('job', job_row(task=9))
        with self.assertLogs('wxbridge.trace', 'WARNING'):
            self.assertTrue(store.flush())
        self.assertEqual([row['task'] for row in self.rows('SELECT task FROM jobs')], [9])


class RecordedModelTests(TempCase):
    def runtime(self, cfg, generate):
        def create(*args, **kwargs):
            client = Mock()
            client.generate.side_effect = generate
            return client
        patcher = patch('wxbridge.app.execution.model_runtime.create_client', side_effect=create)
        patcher.start()
        self.addCleanup(patcher.stop)
        runtime = ConversationModels(cfg)
        self.addCleanup(runtime.close)
        return runtime

    @staticmethod
    def metric(**values):
        _metrics.get().append(dict(provider='synthetic', elapsed_ms=12.5, finish_reason='stop',
                                   usage=dict(prompt_tokens=30, completion_tokens=4), error='', **values))

    def finish(self, runtime, kind='generate', payload=None, **meta):
        cfg = runtime.cfg
        job = runtime.submit(kind, payload or (cfg, [dict(role='system', content='规则'), dict(role='user', content='问')], None),
                             timeout=5, meta=meta or None)
        result = job.future.result(timeout=3)
        self.assertTrue(trace.store_for(cfg).flush())
        return result

    def test_reply_is_recorded_with_prompt_images_response_usage_and_labels(self):
        cfg = self.traced()
        def generate(request, **options):
            self.metric()
            return ModelResult({'content': '三点'}, 'stop', dict(prompt_tokens=30, completion_tokens=4))
        runtime = self.runtime(cfg, generate)
        user = json.dumps(dict(messages=[dict(id=5, text='几点了')], source_ids=[5]), ensure_ascii=False)
        prompt = [dict(role='system', content='系统规则'),
                  dict(role='user', content=[dict(type='text', text=user),
                                             dict(type='image_url', image_url=dict(url='data:image/jpeg;base64,'+'Q'*2048))])]
        result = self.finish(runtime, payload=(cfg, prompt, None), task=5, version=2, attempt=3, source_ids=[5])
        self.assertTrue(result['ok'])
        (job,) = self.rows('SELECT * FROM jobs')
        self.assertEqual((job['instance'], job['role'], job['task'], job['version'], job['attempt'], job['ok'],
                          job['error']), ('alpha', 'reply', 5, 2, 3, 1, ''))
        self.assertEqual((job['result'], job['finish'], job['tokens_in'], job['tokens_out'], job['calls_n']),
                         ('三点', 'stop', 30, 4, 1))
        self.assertEqual(job['summary'], '几点了 → 三点')
        self.assertEqual(json.loads(job['meta']), dict(source_ids=[5]))
        (call,) = decode_calls(job['calls'])
        sent = call['request']['messages']
        # The prompt as the model got it: the base text plus the output rules the reply pipeline appends.
        self.assertTrue(sent[0]['content'].startswith('系统规则'))
        self.assertIn('只输出发给群友的回复正文', sent[0]['content'])
        self.assertIn('几点了', sent[1]['content'][0]['text'])
        self.assertEqual(sent[1]['content'][1]['image_url']['url'], '[图片 image/jpeg, 1 KB]')
        self.assertEqual((call['response'], call['usage']['prompt_tokens'], call['elapsed_ms'], call['error']),
                         ({'content': '三点'}, 30, 12.5, ''))
        self.assertEqual(call['model'], cfg.models.reply.model)

    def test_every_tool_round_is_recorded_and_later_mutation_does_not_alter_earlier_rounds(self):
        cfg = self.traced()
        rounds = []
        def generate(request, **options):
            self.metric()
            rounds.append(len(request.messages))
            if len(rounds) == 1:
                return ModelResult({'content': None, 'tool_calls': [dict(id='c1', type='function', function=dict(name='web_search', arguments='{}'))]},
                                   'tool_calls', {})
            return ModelResult({'content': '查到了'}, 'stop', {})
        runtime = self.runtime(cfg, generate)
        cfg2 = replace_config(cfg, 'search.enabled', True)
        runtime.cfg = cfg2
        with patch('wxbridge.ai.reply.runner.dispatch_tool_call', return_value=Mock(output=dict(results=['网页摘要']))), \
             patch('wxbridge.ai.reply.runner.validate_tool_calls'):
            self.finish(runtime)
        (job,) = self.rows('SELECT * FROM jobs')
        calls = decode_calls(job['calls'])
        self.assertEqual(len(calls), 2)
        self.assertEqual((len(calls[0]['request']['messages']), len(calls[1]['request']['messages'])), (rounds[0], rounds[1]))
        self.assertGreater(rounds[1], rounds[0])
        self.assertEqual(calls[1]['request']['messages'][-1]['role'], 'tool')
        self.assertIn('网页摘要', calls[1]['request']['messages'][-1]['content'])

    def test_empty_reply_found_by_check_complete_is_recorded_on_the_call_and_the_job(self):
        cfg = self.traced()
        def generate(request, **options):
            self.metric()
            return ModelResult({'content': '  '}, 'stop', {})
        runtime = self.runtime(cfg, generate)
        self.assertEqual(self.finish(runtime, task=1)['error'], 'empty_reply')
        (job,) = self.rows('SELECT * FROM jobs')
        (call,) = decode_calls(job['calls'])
        self.assertEqual((job['ok'], job['error'], call['error']), (0, 'empty_reply', 'empty_reply'))
        self.assertIn('ModelError', job['traceback'])

    def test_provider_error_detail_and_status_are_kept_on_the_call(self):
        cfg = self.traced()
        def generate(request, **options):
            error = ModelError('http_429', retryable=True, status=429)
            error.detail = 'rate limited'
            raise error
        runtime = self.runtime(cfg, generate)
        self.finish(runtime)
        (job,) = self.rows('SELECT * FROM jobs')
        (call,) = decode_calls(job['calls'])
        self.assertEqual((call['error'], call['error_detail'], call['status'], job['error']), ('http_429', 'rate limited', 429, 'http_429'))

    def test_unexpected_exception_stores_traceback_and_logs_a_warning_with_it(self):
        cfg = self.traced()
        runtime = self.runtime(cfg, Mock(side_effect=KeyError('boom')))
        with self.assertLogs('wxbridge', 'WARNING') as logs:
            self.assertEqual(self.finish(runtime, task=4)['error'], 'KeyError')
        (job,) = self.rows('SELECT * FROM jobs')
        self.assertIn('KeyError', job['traceback'])
        record = logs.records[0]
        self.assertIn('instance=alpha', record.getMessage())
        self.assertIn('task=4', record.getMessage())
        self.assertIsNotNone(record.exc_info)

    def test_model_errors_log_at_info_without_a_traceback(self):
        cfg = self.traced()
        runtime = self.runtime(cfg, Mock(side_effect=ModelError('http_500', status=500)))
        with self.assertLogs('wxbridge', 'INFO') as logs:
            self.finish(runtime, task=2)
        record = logs.records[0]
        self.assertEqual(record.levelno, logging.INFO)
        self.assertIn('error=http_500 status=500', record.getMessage())
        self.assertIsNone(record.exc_info)

    def test_other_roles_use_their_own_role_and_labels(self):
        cfg = self.traced()
        def generate(request, **options):
            self.metric()
            return ModelResult({'content': '{"reply_to":[1]}'}, 'stop', {})
        runtime = self.runtime(cfg, generate)
        prompt = [dict(role='system', content='判断'), dict(role='user', content='{}')]
        self.finish(runtime, 'participation', (cfg, prompt, dict(source_ids=[1])), source_ids=[1])
        (job,) = self.rows('SELECT * FROM jobs')
        self.assertEqual((job['role'], json.loads(job['meta'])), ('participation', dict(source_ids=[1])))
        self.assertIn('reply_to', job['result'])

    def test_switched_off_instances_write_nothing_and_log_no_traceback(self):
        cfg = update_config(self.traced(), 'trace', enabled=False)
        runtime = self.runtime(cfg, Mock(side_effect=KeyError('boom')))
        with self.assertLogs('wxbridge', 'WARNING') as logs:
            result = runtime.submit('generate', (cfg, [dict(role='user', content='问')], None), timeout=5).future.result(timeout=3)
        self.assertEqual(result['error'], 'KeyError')
        self.assertFalse(logs.records[0].exc_info)
        self.assertIsNone(trace.store_for(cfg))
        self.assertFalse((self.root/'trace').exists())

    def test_owner_events_are_recorded_and_never_raise(self):
        cfg = self.traced()
        record_event(cfg, 'reply_final', task=3, version=1, chunks=['一', '二'])
        self.assertTrue(trace.store_for(cfg).flush())
        (event,) = self.rows('SELECT * FROM events')
        self.assertEqual((event['instance'], event['task'], event['kind'], json.loads(event['detail'])),
                         ('alpha', 3, 'reply_final', dict(chunks=['一', '二'])))
        record_event(update_config(cfg, 'trace', enabled=False), 'reply_final', task=4)
        record_event(object(), 'reply_final')
        self.assertTrue(trace.store_for(cfg).flush())
        self.assertEqual(len(self.rows('SELECT * FROM events')), 1)

    def test_unavailable_directory_disables_tracing_without_breaking_the_job(self):
        blocker = self.root/'memory'
        blocker.parent.mkdir(parents=True, exist_ok=True)
        (self.root/'trace').write_text('a file where the directory should be')
        cfg = self.traced()
        runtime = self.runtime(cfg, lambda request, **options: ModelResult({'content': '好'}, 'stop', {}))
        with self.assertLogs('wxbridge.trace', 'WARNING'):
            result = runtime.submit('generate', (cfg, [dict(role='user', content='问')], None), timeout=5).future.result(timeout=3)
        self.assertTrue(result['ok'])


class ProblemFileTests(TempCase):
    def test_warnings_and_tracebacks_reach_the_persistent_file_but_info_does_not(self):
        app = application(replace(default_config(), id='alpha'))
        app = replace_config(app, 'account.memory_directory', str(self.root/'memory'))
        app = replace_config(app, 'instances.0.config.trace', dict(enabled=True))
        handler = problem_file(app)
        self.assertIsNotNone(handler)
        log = logging.getLogger('wxbridge.test_problem_file')
        log.propagate, log.level = False, logging.DEBUG
        log.addHandler(handler)
        try:
            log.info('只在日志里 task=1')
            try:
                raise KeyError('boom')
            except KeyError:
                log.warning('生成未启动 task=7 category=KeyError', exc_info=True)
        finally:
            log.removeHandler(handler)
            handler.close()
        text = (self.root/'trace'/trace.PROBLEMS).read_text(encoding='utf-8')
        self.assertNotIn('只在日志里', text)
        self.assertRegex(text, r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} WARNING 生成未启动 task=7')
        self.assertIn('Traceback (most recent call last):', text)
        self.assertIn('KeyError', text)

    def test_nothing_is_created_when_no_instance_traces(self):
        app = application(replace(default_config(), id='alpha'))
        app = replace_config(app, 'account.memory_directory', str(self.root/'memory'))
        self.assertIsNone(problem_file(app))
        self.assertFalse((self.root/'trace').exists())
