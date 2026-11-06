"""Adapter deployment contract and native helper install options; no service mutations."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import json


ROOT = Path(__file__).resolve().parents[1]


def script(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT/'scripts'/name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = script('install-native-helper.py')
adapter = script('adapter-wheel.py')


class AdapterDeploymentContractTests(unittest.TestCase):
    def test_scripts_read_the_same_contract_as_the_installed_adapter(self):
        from adapter_wc import deployment as contract
        self.assertEqual(installer.TEXT_ASSETS, contract.HELPER_ASSETS)
        self.assertEqual(installer.IMAGE_ASSETS, contract.IMAGE_ASSETS)
        self.assertEqual(str(installer.IMAGE_RUNTIME_DIRECTORY), contract.IMAGE_RUNTIME_DIRECTORY)
        self.assertEqual(tuple(adapter.adapter_deployment(ROOT)['recovery_modules']), contract.RECOVERY_MODULES)
        # compose.yaml is static YAML and cannot import the constant.
        compose = (ROOT/'compose.yaml').read_text(encoding='utf-8')
        self.assertIn(contract.IMAGE_RUNTIME_DIRECTORY+':size=16m,mode=0700', compose)

    def test_malformed_contracts_are_rejected(self):
        valid = dict(schema_version=1, helper_assets=['helper.py'], image_assets=['image_worker.py'],
                     native_profile='native/profile.json', image_runtime_directory='/run/images',
                     recovery_modules=['__init__.py'])
        self.assertEqual(adapter.deployment_contract(json.dumps(valid)), valid)
        for change in (dict(schema_version=2), dict(helper_assets=['../helper.py']),
                       dict(image_assets=['native/image_worker.py']), dict(recovery_modules=[]),
                       dict(native_profile='/etc/profile.json'), dict(image_runtime_directory='/run/a/b'),
                       dict(image_runtime_directory='/tmp/images')):
            with self.assertRaisesRegex(ValueError, 'invalid_adapter_contract'):
                adapter.deployment_contract(json.dumps({**valid, **change}))
        with self.assertRaisesRegex(ValueError, 'invalid_adapter_contract'):
            adapter.deployment_contract(b'not json')


class NativeHelperImageOptionTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'.test-tmp').mkdir(exist_ok=True)

    def test_images_stay_disabled_unless_explicitly_enabled(self):
        self.assertIsNone(installer.image_options(False, None, None, 995))
        with self.assertRaisesRegex(ValueError, 'require_enable'):
            installer.image_options(False, 'wxid_test', None, 995)

    def test_explicit_account_root_must_match_owner_and_account(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.test-tmp') as folder:
            path = Path(folder).resolve()/'wxid_test'
            path.mkdir()
            uid = path.stat().st_uid
            self.assertEqual(installer.image_options(True, 'wxid_test', path, uid),
                             dict(enabled=True, expected_account='wxid_test', account_root=str(path), timeout_seconds=30))
            with self.assertRaisesRegex(ValueError, 'root_invalid'):
                installer.image_options(True, 'wxid_test', path, uid+1)
            with self.assertRaisesRegex(ValueError, 'root_invalid'):
                installer.image_options(True, 'wxid_other', path, uid)
            with self.assertRaisesRegex(ValueError, 'account_required'):
                installer.image_options(True, None, path, uid)


if __name__ == '__main__':
    unittest.main()
