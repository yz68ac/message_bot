"""Image export/load is tied to a commit and verified before docker load; docker is faked."""
import gzip
import io
import json
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest

from tests.support.paths import ROOT

IMAGE = runpy.run_path(str(ROOT/'scripts/container-image.py'))
COMMIT = 'c'*40


class FakeSave:
    def __init__(self, body=b'image layers', code=0):
        self.body, self.code, self.killed = body, code, False

    def __call__(self, args, stdout):
        self.args, self.stdout, self.returncode = args, io.BytesIO(self.body), self.code
        return self

    def wait(self):
        return self.code

    def kill(self):
        self.killed = True


class ContainerImageBundleTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT/'.test-tmp'
        parent.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temporary.cleanup)
        self.out = Path(temporary.name)/'images'
        self.calls = []

    def docker(self, args, **kwargs):
        self.calls.append(args)
        return subprocess.CompletedProcess(args, 0)

    def status(self, *args, root):
        return '' if args[0] == 'status' else COMMIT

    def export(self, save=None):
        fake = save or FakeSave()
        return IMAGE['export'](self.out, run=self.docker, status=self.status,
                               save=lambda tag, path: IMAGE['save_compressed'](tag, path, popen=fake)), fake

    def test_export_tags_by_commit_and_describes_a_verifiable_archive(self):
        value, fake = self.export()
        self.assertEqual(self.calls, [['docker', 'build', '--build-arg', 'WXBRIDGE_COMMIT='+COMMIT,
                                       '-t', 'wxbridge:cccccccccccc', '-t', 'wxbridge:local', str(ROOT)]])
        self.assertEqual(fake.args, ['docker', 'save', 'wxbridge:cccccccccccc'])
        archive = self.out/'wxbridge-image-cccccccccccc.tar.gz'
        self.assertEqual(gzip.decompress(archive.read_bytes()), b'image layers')
        described = json.loads((self.out/'wxbridge-image-cccccccccccc.json').read_text(encoding='utf-8'))
        self.assertEqual(described, value)
        self.assertEqual((value['commit'], value['sha256']), (COMMIT, IMAGE['sha256'](archive)))
        pinned = json.loads((ROOT/'dependencies/adapter_wc.json').read_text(encoding='utf-8'))['version']
        self.assertEqual(value['adapter_wc'], pinned)
        with self.assertRaisesRegex(ValueError, 'image_export_exists'):
            self.export()

    def test_dirty_checkout_is_refused_before_building(self):
        with self.assertRaisesRegex(ValueError, 'commit_changes_before_export'):
            IMAGE['export'](self.out, run=self.docker, status=lambda *a, root: ' M src/x.py')
        self.assertEqual(self.calls, [])

    def test_failed_save_leaves_no_partial_archive(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.export(FakeSave(code=1))
        self.assertEqual(list(self.out.iterdir()), [])

    def test_load_verifies_the_archive_then_loads_and_tags_local(self):
        value, _ = self.export()
        self.calls.clear()
        description = self.out/'wxbridge-image-cccccccccccc.json'
        self.assertEqual(IMAGE['load'](description, run=self.docker), value)
        self.assertEqual(self.calls, [['docker', 'load', '-i', str(self.out/value['archive'])],
                                      ['docker', 'tag', 'wxbridge:cccccccccccc', 'wxbridge:local']])
        self.calls.clear()
        (self.out/value['archive']).write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'image_archive_hash_mismatch'):
            IMAGE['load'](description, run=self.docker)
        description.write_text(json.dumps(dict(value, archive='../elsewhere.tar.gz')), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'invalid_image_description'):
            IMAGE['load'](description, run=self.docker)
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
