"""重启恢复的元数据边界；不读真实配置/聊天、不连接服务器、不调用模型。"""
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.support.boot import boot


class BootRecoveryTests(unittest.TestCase):
    def test_probe_uses_packaged_python_after_adapter_extraction(self):
        from contextlib import nullcontext

        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.test-tmp') as name:
            root = Path(name)
            settings = root/'settings.json'
            settings.write_text('{}')
            (root/'keys.json').write_text('{"message/message_0.db":{}}')
            (root/'keyscan-session.json').write_text('{"pid":123}')
            with patch.object(boot, 'CODE', root), patch.object(boot, 'RUN', root), \
                 patch.object(boot, 'SENSITIVE', root), patch.object(boot, 'SETTINGS', settings), \
                 patch.object(boot, 'validate_runtime'), \
                 patch.object(boot, 'process_token', return_value={'pid': 123}), \
                 patch.object(boot, 'database_locks', return_value=nullcontext()), \
                 patch.object(boot, 'as_user', return_value=SimpleNamespace(returncode=0, stdout='{"ready":true}')) as execute, \
                 patch.object(boot, 'atomic_json'), patch.object(boot, 'owner'), \
                 patch.object(boot, 'safe_status', side_effect=lambda phase, **kw: dict(phase=phase, **kw)), \
                 patch.object(boot.subprocess, 'run') as scanner:
                result = boot.prepare()
            scanner.assert_not_called()
            self.assertTrue(result['ready'])
            self.assertEqual(execute.call_args.args[0][0], str(root/'.venv/bin/python'))

    def test_directory_preparation_needs_no_instance_or_message_probe(self):
        from contextlib import nullcontext
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.test-tmp') as name:
            root=Path(name);settings=root/'settings.json';settings.write_text('{"db_dir":"synthetic"}')
            (root/'keys.json').write_text('{"contact/contact.db":{},"session/session.db":{}}')
            (root/'keyscan-session.json').write_text('{"pid":123}')
            with patch.object(boot,'SETTINGS',settings),patch.object(boot,'RUN',root),patch.object(boot,'SENSITIVE',root), \
                 patch.object(boot,'validate_runtime'),patch.object(boot,'process_token',return_value={'pid':123}), \
                 patch.object(boot,'database_locks',return_value=nullcontext()),patch.object(boot,'safe_status',side_effect=lambda phase,**kw:dict(phase=phase,**kw)), \
                 patch.object(boot,'as_user') as probe,patch.object(boot.subprocess,'run') as scanner:
                result=boot.prepare(metadata_only=True)
            probe.assert_not_called();scanner.assert_not_called()
            self.assertFalse(result['ready']);self.assertEqual(result['phase'],'contacts_prepared')

    def setUp(self):
        parent = Path(__file__).resolve().parents[1]/'.test-tmp'
        parent.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temporary.cleanup)
        application = patch.object(boot, 'APP', Path(temporary.name))
        application.start()
        self.addCleanup(application.stop)

    def test_busy_database_stops_preparation_before_scanning_or_probing(self):
        parent=Path(__file__).resolve().parents[1]/'.test-tmp';parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as name:
            directory=Path(name);settings=directory/'settings.json';settings.write_text('{}')
            with patch.object(boot,'RUN',directory),patch.object(boot,'SETTINGS',settings),\
                 patch.object(boot,'validate_runtime'),patch.object(boot,'process_token',return_value={'pid':123}),\
                 patch.object(boot,'database_locks',side_effect=BlockingIOError),patch.object(boot.subprocess,'run') as run:
                with self.assertRaises(BlockingIOError):boot.prepare()
                run.assert_not_called()
