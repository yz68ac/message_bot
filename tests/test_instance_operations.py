"""Group-aware boot, release and CLI metadata, using private synthetic files."""
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import Mock, patch

from tests.support.instances import InstanceFixture, CHATS
from wxbridge.app.status.paths import configured_instances, live_status_path
from wxbridge.storage.journal import Journal
import tests.support.boot as test_boot_recovery

boot = test_boot_recovery.boot


class InstanceOperationsTests(InstanceFixture):
    def setUp(self):
        super().setUp()
        self.runtime = self.root/'runtime'; self.runtime.mkdir(mode=0o700)
        self.data['queue']['file'] = str(self.runtime/'queue.sqlite3')
        self.data['state_file'] = str(self.runtime/'state.json')
        self.data['helper_transport'] = dict(enabled=True, allowed_chat_ids=list(CHATS))
        self.data['ui'] = dict(backend='native_service')
        self.cfg = self.load()
        p = patch.object(boot, 'APP', self.root); p.start(); self.addCleanup(p.stop)
        p = patch.object(boot, 'RUN', self.runtime); p.start(); self.addCleanup(p.stop)
        p = patch('wxbridge.storage.journal.checked_path', side_effect=lambda v, **kw:Path(v))
        p.start(); self.addCleanup(p.stop)

    def create_queues(self):
        for i, entry in enumerate(self.cfg.instances.values()):
            path = Path(entry.paths.queue_file); path.parent.mkdir(parents=True, mode=0o700)
            journal = Journal(str(path), dict(account='wxid_bot', chat=entry.chat), i+5)
            with journal.db:
                journal.enqueue_memory('activity', f'synthetic:{i}', [dict(local_id=1, time=123)], [0, 123])
            journal.close()

    def test_live_status_is_account_scoped(self):
        self.assertEqual(live_status_path(self.root, self.runtime), self.runtime/'account-status.json')

    def test_probe_uses_one_store_and_starts_each_group_from_the_latest_message(self):
        store = Mock()
        store.self_wxid = 'wxid_bot'
        store.resolve_chat.side_effect = lambda chat:(chat, '合成群')
        store.last_local_id.return_value = 20
        with patch('wxbridge.wechat.store.WeChatStore', return_value=store) as factory, \
             patch('wxbridge.native.sender.readiness'):
            result = boot.probe()
        factory.assert_called_once()
        self.assertTrue(result['ready'])
        for entry in self.cfg.instances.values():
            self.assertEqual(result['instances'][entry.id], dict(ready=True, latest_local_id=20))
            self.assertFalse(Path(entry.paths.state_file).exists())

    def test_instance_events_remain_parseable_and_filter_out_other_groups(self):
        import logging
        from wxbridge.app.status.logging_context import InstanceLog
        from wxbridge.app.status.snapshot import safe_events
        path = self.root/'events.log'
        logger = logging.getLogger('synthetic-instance-events')
        logger.setLevel(logging.INFO)
        handler = logging.FileHandler(path, encoding='utf-8')
        handler.setFormatter(logging.Formatter('10-27 10:20:30 %(levelname)s %(message)s'))
        logger.addHandler(handler)
        try:
            for name in ('alpha', 'beta'):
                InstanceLog(logger, dict(instance=name)).info('接收判定 metadata=%s', json.dumps(dict(local_id=7, reason='accepted')))
        finally:
            logger.removeHandler(handler); handler.close()
        rows, error = safe_events(path, instance='alpha')
        self.assertFalse(error)
        self.assertEqual(len(rows), 1)
        self.assertIn('instance=alpha', rows[0])
        self.assertIn('reason=accepted', rows[0])

    def test_database_check_gets_exclusive_owner_before_creating_reader(self):
        from wxbridge_cli.diagnostics import check
        from adapter_wc.errors import OwnerBusyError
        with patch('wxbridge.app.execution.locks.instance_lock', side_effect=OwnerBusyError('resource_busy')), \
             patch('wxbridge_cli.diagnostics.WeChatStore') as store:
            with self.assertRaises(OwnerBusyError): check(list(self.cfg.instances.values())[0])
            store.assert_not_called()


if __name__ == '__main__': unittest.main()
