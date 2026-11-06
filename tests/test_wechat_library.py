"""Business presentation retains the adapter's source identity and raw types."""
import ast
import unittest
import importlib.metadata
import json
from pathlib import Path
import adapter_wc
from adapter_wc.storage import WeChatStore
from wxbridge.wechat.messages import display_message
from tests.support.paths import ROOT

CHAT = 'synthetic@chatroom'


class BoundaryTests(unittest.TestCase):
    def test_application_imports_only_adapter_public_packages(self):
        public = {'adapter_wc', 'adapter_wc.storage', 'adapter_wc.messages',
                  'adapter_wc.media', 'adapter_wc.native', 'adapter_wc.desktop',
                  'adapter_wc.runtime', 'adapter_wc.deployment'}
        for path in (ROOT/'src').rglob('*.py'):
            if 'provider' in path.relative_to(ROOT/'src').parts:
                continue
            tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith('adapter_wc'):
                    self.assertIn(node.module, public, str(path))
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith('adapter_wc'):
                            self.assertIn(alias.name, public, str(path))

    def test_adapter_is_the_pinned_installed_distribution(self):
        manifest=json.loads((ROOT/'dependencies/adapter_wc.json').read_text(encoding='utf-8'))
        self.assertEqual(importlib.metadata.version('adapter_wc'),manifest['version'])
        self.assertFalse(Path(adapter_wc.__file__).resolve().is_relative_to(ROOT/'src'))

    def test_bot_presentation_does_not_mutate_adapter_records(self):
        store = WeChatStore.__new__(WeChatStore)
        xml = '<msg><appmsg><type>57</type><title>question</title></appmsg></msg>'
        raw = store._parse_message(CHAT, dict(local_id=1, local_type=(57 << 32) | 49,
                                  create_time=1, message_content=xml), {}, {})
        self.assertEqual(raw['type'], 49)
        self.assertEqual(raw['reference']['reason'], 'reference_missing')
        rendered = display_message(raw)
        self.assertEqual(rendered['type'], 1)
        self.assertEqual(raw['type'], 49)
        self.assertEqual(raw['reference']['reason'], 'reference_missing')
        image = store._parse_message(CHAT, dict(local_id=2, local_type=3, create_time=1,
                                    message_content='<msg><img md5="'+'a'*32+'"/></msg>'), {}, {})
        self.assertEqual(image['text'], '')
        self.assertEqual(display_message(image)['text'], '[图片]')
