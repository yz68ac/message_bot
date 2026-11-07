"""Synthetic log files only: grouping, masking, fixed sources and the supervisor log."""
from pathlib import Path
import logging
import os
import tempfile
import time
import unittest
from unittest.mock import MagicMock

from web.backend.logs import WINDOW, LogReader, clean, parse
from wxbridge.management.repository import ManagementError

SECRET = 'sk-ant-api03-'+'Q'*24
TRACE = ('Traceback (most recent call last):\n'
         '  File "/opt/wxbridge/src/wxbridge/app/execution/workers.py", line 88, in run\n'
         '    options = settings["models"]["reply"]\n'
         "KeyError: 'reply'\n")


class ParseTests(unittest.TestCase):
    def test_traceback_stays_with_its_error_and_ordinary_lines_keep_only_codes(self):
        text = ('10-30 09:18:31 INFO 开始生成 task=13 text=PRIVATE_CHAT 你好\n'
                '10-30 09:18:32 ERROR 生成未启动 task=13 category=KeyError\n'+TRACE+
                '10-30 09:18:33 INFO 停止监听\n')
        rows = parse(text, 'bot')
        self.assertEqual([row['level'] for row in rows], ['INFO', 'ERROR', 'INFO'])
        self.assertEqual(rows[0]['text'], '开始生成 task=13')
        self.assertTrue(rows[0]['hidden'])
        self.assertEqual(rows[1]['text'], '生成未启动 task=13 category=KeyError')
        self.assertIn("KeyError: 'reply'", rows[1]['detail'])
        self.assertIn('workers.py", line 88', rows[1]['detail'])
        self.assertEqual(rows[1]['stamp'], '10-30 09:18:32')

    def test_bare_crashes_become_errors_named_by_their_exception_and_repeats_collapse(self):
        text = '10-30 09:18:30 INFO 停止监听\n'+TRACE*3+'\nDuring handling of the above exception, another exception occurred:\n\n'+TRACE
        rows = parse(text, 'bot')
        # Two identical crashes collapse; the third carries a chained traceback and stays separate.
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[2]['detail'].count('Traceback'), 2)
        self.assertEqual(rows[1]['level'], 'ERROR')
        self.assertEqual(rows[1]['text'], "KeyError: 'reply'")
        self.assertEqual(rows[1]['count'], 2)
        self.assertTrue(rows[1]['approx'])
        self.assertEqual(rows[1]['at'], rows[0]['at'])
        chained = parse(TRACE+'\nDuring handling of the above exception, another exception occurred:\n\n'+TRACE, 'bot')
        self.assertEqual(len(chained), 1)
        self.assertEqual(chained[0]['detail'].count('Traceback'), 2)

    def test_a_traceback_under_an_info_line_does_not_reveal_that_line(self):
        rows = parse('10-30 09:18:31 INFO 开始生成 task=13 text=PRIVATE_CHAT\n'+TRACE, 'bot')
        self.assertEqual([row['level'] for row in rows], ['INFO', 'ERROR'])
        self.assertNotIn('PRIVATE_CHAT', rows[0]['text']+rows[1]['text'])

    def test_secrets_tokens_and_terminal_controls_are_masked_but_paths_survive(self):
        line = ('10-30 09:18:32 ERROR 请求失败 url=https://api.example.com/v1?key=AIzaSyA1234567890abcdefghijkl&alt=json '
                'Authorization: Bearer abcdefghijklmnop '+SECRET+' api_key="plain-secret" '
                'db=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef '
                'token=Tok1abcDEF2ghiJKL3mnoPQR4stuVWX5yzABC6defGHI \x1b[2Jdone\n')
        text = parse(line+'  File "/opt/wxbridge/.venv/lib/python3.13/site-packages/uvicorn/protocols/http/httptools_impl.py"\n', 'bot')[0]['text']
        for secret in ('AIzaSy', 'abcdefghijklmnop', SECRET, 'plain-secret', '0123456789abcdef', 'Tok1abc', '\x1b'):
            self.assertNotIn(secret, text)
        self.assertIn('alt=json', text)
        self.assertIn('done', text)
        path = 'lib/python3.13/site-packages/uvicorn/protocols/http/httptools_impl.py test_events_only_project_known_fields_and_mask'
        self.assertEqual(clean(path), path)

    def test_public_sources_show_info_and_uvicorn_lines_parse(self):
        rows = parse('2026-11-07 10:00:00 INFO Started server process [42]\nINFO:     Application startup complete.\n', 'web')
        self.assertEqual([row['text'] for row in rows], ['Started server process [42]', 'Application startup complete.'])
        self.assertTrue(rows[1]['approx'])
        private = parse('hello PRIVATE_CHAT text\n{"ready": false, "error": "keys_missing"}\n', 'recovery')
        self.assertEqual(private[0]['level'], 'INFO')
        self.assertNotIn('PRIVATE_CHAT', private[0]['text'])
        self.assertEqual(private[1]['level'], 'ERROR')

    def test_short_stamps_never_land_in_the_future(self):
        now = time.mktime((2026, 1, 1, 0, 30, 0, 0, 0, -1))
        row = parse('12-31 23:59:00 WARNING 记忆延后 category=x\n', 'bot', now=now)[0]
        self.assertLess(row['at'], now)
        self.assertEqual(time.localtime(row['at']).tm_year, 2025)


class ReaderTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1]/'.test-tmp'
        parent.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_query_validates_fixed_sources_filters_and_searches(self):
        stamp = time.strftime('%m-%d %H:%M:%S')
        (self.root/'bot-live.log').write_text(f'{stamp} INFO 停止监听\n{stamp} WARNING 记忆延后 category=Timeout\n'
                                              f'{stamp} ERROR 生成未启动 category=KeyError\n'+TRACE, encoding='utf-8')
        reader = LogReader(self.root)
        for source, level in (('../etc/passwd', 'all'), ('bot', 'verbose')):
            with self.subTest(source=source), self.assertRaises(ManagementError):
                reader.query(source, level)
        self.assertEqual(reader.query('bot')['total'], 3)
        self.assertEqual([row['level'] for row in reader.query('bot', 'warning')['entries']], ['ERROR', 'WARNING'])
        self.assertEqual(reader.query('bot', 'error', 'reply')['total'], 1)
        self.assertEqual(reader.query('bot', 'all', 'nothing-like-this')['total'], 0)
        merged = reader.query('all')
        self.assertEqual({info['id']: info['error'] for info in merged['sources']}['web'], 'missing')
        problems = reader.problems()
        self.assertEqual((problems['count'], problems['errors']), (2, 1))
        self.assertEqual(problems['latest'][0]['text'], '生成未启动 category=KeyError')

    def test_bot_falls_back_to_the_event_log_and_large_files_read_only_the_tail(self):
        events = self.root/'events.log'
        events.write_text('x'*50+'\n'+('10-30 09:18:30 INFO 停止监听\n'*((WINDOW//28)+50)), encoding='utf-8')
        reader = LogReader(self.root, fallback=events)
        result = reader.query('bot', limit=500)
        self.assertTrue(result['sources'][0]['truncated'])
        self.assertEqual(result['total'], 1)  # Identical lines collapse into one counted entry.
        self.assertLess(result['entries'][0]['count'], WINDOW//28+50)
        self.assertNotIn('x'*50, ''.join(row['text'] for row in result['entries']))

    @unittest.skipUnless(hasattr(os, 'symlink') and os.name == 'posix', 'POSIX links')
    def test_linked_log_files_are_refused(self):
        target = self.root/'secret.txt'
        target.write_text('10-30 09:18:30 ERROR PRIVATE\n', encoding='utf-8')
        (self.root/'web.log').symlink_to(target)
        result = LogReader(self.root).query('web')
        self.assertEqual(result['sources'][0]['error'], 'denied')
        self.assertFalse(result['entries'])


class SupervisorLogTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1]/'.test-tmp'
        parent.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_supervisor_log_is_bounded_and_readable_by_the_console(self):
        from wxbridge.host.supervisor import BoundedLog
        handler = BoundedLog(self.root/'supervisor.log', limit=400)
        logger = logging.getLogger('synthetic-supervisor-log')
        logger.propagate = False
        logger.addHandler(handler)
        try:
            for index in range(20):
                logger.warning('component_exited name=bot code=%s intentional=False', index)
        finally:
            logger.removeHandler(handler)
            handler.close()
        size = (self.root/'supervisor.log').stat().st_size
        self.assertLessEqual(size, 400)
        rows = LogReader(self.root).query('supervisor')['entries']
        self.assertEqual(rows[0]['text'], 'component_exited name=bot code=19 intentional=False')
        self.assertIsNotNone(rows[0]['at'])

    @unittest.skipUnless(os.name == 'posix', 'POSIX links')
    def test_supervisor_log_refuses_links(self):
        from wxbridge.host.supervisor import BoundedLog
        target = self.root/'target'
        target.write_text('', encoding='utf-8')
        (self.root/'supervisor.log').symlink_to(target)
        with self.assertRaises(OSError):
            BoundedLog(self.root/'supervisor.log')
        os.unlink(self.root/'supervisor.log')
        os.link(target, self.root/'supervisor.log')
        with self.assertRaises(ValueError):
            BoundedLog(self.root/'supervisor.log')

    def test_unexpected_exit_is_a_warning_and_intentional_stop_is_info(self):
        from wxbridge.host.supervisor import Child
        for stopping, code, level in ((False, 1, 'WARNING'), (True, -15, 'INFO'), (False, 0, 'INFO')):
            child = Child('bot', ['true'])
            child.process = MagicMock()
            child.process.poll.return_value = code
            child.process.stdout = None
            child.process.pid = 0
            child.stopping, child.started = stopping, time.monotonic()
            child._signal = MagicMock()
            with self.subTest(code=code), self.assertLogs('wxbridge.service', level='INFO') as captured:
                child.reap()
            self.assertTrue(captured.output[-1].startswith(level+':'))


class WebEntryTests(unittest.TestCase):
    def test_web_server_logs_carry_timestamps(self):
        from web.backend.__main__ import log_config
        config = log_config()
        self.assertIn('%(asctime)s', config['formatters']['default']['fmt'])
        self.assertEqual(config['root']['handlers'], ['default'])


if __name__ == '__main__':
    unittest.main()
