"""Readiness, bounded context and release planning without service mutations."""
from tests.support.configuration import replace_config, update_config, snapshot
from tests.support.model_support import default_config
from contextlib import nullcontext
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from wxbridge.config import default_document
from wxbridge.ai.context.selection import ContextError
from wxbridge.ai.reply.prompt import build_prompt
from wxbridge.storage.memory_selection import MemoryTopic
from adapter_wc.native import helper
import tests.support.boot as recovery_tests
from tests.support.synthetic import NOW, msg



class ReviewOperationTests(unittest.TestCase):
    def test_native_recovery_uses_helper_readiness_without_constructing_gui(self):
        cfg=default_config();cfg = replace_config(cfg, 'account.ui.backend', 'native_service');cfg = replace_config(cfg, 'chat', 'synthetic@chatroom')
        store=Mock(self_wxid='wxid_synthetic');store.resolve_chat.return_value=('synthetic@chatroom','synthetic')
        store.last_local_id.return_value=10
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent/'.test-tmp') as folder:
            root=Path(folder)
            cfg = replace_config(cfg, 'paths.queue_file', str(root/'queue.sqlite3'))
            cfg = replace_config(cfg, 'paths.state_file', str(root/'state.json'))
            active=copy.deepcopy(cfg)
            cfg = replace_config(cfg, 'instances', [dict(id='synthetic',enabled=True,config=active)])
            with patch.object(recovery_tests.boot,'RUN',root),\
                 patch('wxbridge.config.load_config',return_value=cfg),patch('wxbridge.wechat.store.WeChatStore',return_value=store),\
                 patch('wxbridge.wechat.ui.A11y') as gui,patch('wxbridge.native.sender.readiness',return_value={'ready':True}) as ready:
                self.assertTrue(recovery_tests.boot.probe()['ready'])
            gui.assert_not_called();ready.assert_called_once_with(cfg)

    def test_helper_health_checks_client_but_never_attaches_or_sends(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent/'.test-tmp') as folder:
            root=Path(folder);(root/'profile.json').write_text(json.dumps(dict(sha256='synthetic')))
            with patch.object(helper,'find_client',return_value=123) as find:
                result=helper.health(dict(uid=995,assets=str(root)))
            self.assertTrue(result['result']['client_ready']);find.assert_called_once_with(995,'synthetic')

    def test_optional_context_degradation_preserves_full_question_and_reports_omissions(self):
        cfg=default_config();cfg = update_config(cfg, 'models.reply.options', system_prompt_file='', system_prompt='synthetic')
        cfg = update_config(cfg, 'target', context_max_chars=1200, context_budget_strategy='strict')
        rows=[msg(i,NOW-20+i,'OLD_'+str(i)+'x'*150) for i in range(1,11)]
        trigger=msg(20,NOW,'CURRENT_QUESTION_UNCHANGED')
        with self.assertRaises(ContextError):build_prompt(cfg,rows,trigger,'synthetic')
        cfg = replace_config(cfg, 'target.context_budget_strategy', 'recent')
        content=build_prompt(cfg,rows,trigger,'synthetic')[1]['content']
        self.assertIn(trigger['text'],content);self.assertGreater(json.loads(content)['history_omitted'],0)
        self.assertLessEqual(len(content),1200);self.assertNotIn('OLD_1x',content)
        self.assertIn('OLD_10x',content)

    def test_manual_aliases_match_same_entity_without_activating_unrelated_topics(self):
        aliases=[['warframe','星际战甲']]
        entry=dict(summary='上周聊过Warframe',kind='event')
        self.assertGreater(MemoryTopic('今天讨论星际战甲',aliases=aliases).score(entry),0)
        self.assertEqual(MemoryTopic('今天讨论JOJO',aliases=aliases).score(entry),0)
