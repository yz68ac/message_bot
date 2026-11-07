"""The image keeps slow, rarely changing layers ahead of the application source."""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT/'Dockerfile').read_text(encoding='utf-8')


def runtime_stage():
    return DOCKERFILE.split('\nFROM ${BASE_IMAGE}\n', 1)[1]


class ContainerImageTests(unittest.TestCase):
    def test_source_is_copied_after_the_slow_layers(self):
        stage = runtime_stage()
        self.assertNotIn('COPY . .', DOCKERFILE)
        source = stage.index('COPY src/ src/')
        for slow in ('WeChatLinux_x86_64.deb', '--no-install-project', 'pip install uv=='):
            self.assertLess(stage.index(slow), source, slow)

    def test_wechat_installer_is_bind_mounted_not_copied(self):
        self.assertIn('--mount=type=bind,source=resources/wechat/linux-x86_64/4.1.13.26/installer/WeChatLinux_x86_64.deb',
                      DOCKERFILE)
        for line in DOCKERFILE.splitlines():
            if line.startswith('COPY'):
                self.assertNotIn('installer', line)

    def test_copy_sources_exist(self):
        for line in DOCKERFILE.splitlines():
            if line.startswith('COPY') and '--from=' not in line:
                for source in line.split()[1:-1]:
                    self.assertTrue((ROOT/source).exists(), source)

    def test_build_commit_is_stamped_last_from_a_build_argument(self):
        stage = runtime_stage()
        declared = stage.index('ARG WXBRIDGE_COMMIT=')
        self.assertGreater(declared, stage.index('COPY src/ src/'))
        self.assertGreater(declared, stage.index('install-native-helper.py'))
        self.assertIn('"$WXBRIDGE_COMMIT" > /opt/wxbridge/BUILD_COMMIT', stage[declared:])
        compose = (ROOT/'compose.yaml').read_text(encoding='utf-8')
        self.assertEqual(compose.count('WXBRIDGE_COMMIT: ${WXBRIDGE_COMMIT:-}'), 2)

    def test_host_frontend_build_stays_out_of_the_build_context(self):
        ignored = [line.strip() for line in (ROOT/'.dockerignore').read_text(encoding='utf-8').splitlines()]
        self.assertIn('src/web/frontend/dist', ignored)
        # The exclusion must follow the src/** allowlist, which would otherwise re-include it.
        self.assertGreater(ignored.index('src/web/frontend/dist'), ignored.index('!src/**'))


class BuildRevisionTests(unittest.TestCase):
    COMMIT = 'a'*40

    def revision(self, *, git, stamp=None):
        import subprocess
        import tempfile
        from unittest.mock import patch
        from wxbridge.app.status import build_info
        (ROOT/'.test-tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'.test-tmp') as directory:
            root = Path(directory)
            if stamp is not None:
                (root/'BUILD_COMMIT').write_bytes(stamp)
            if isinstance(git, Exception):
                run = patch.object(build_info.subprocess, 'run', side_effect=git)
            else:
                run = patch.object(build_info.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout=git+'\n'))
            with patch.object(build_info, 'SOURCE_ROOT', root), run:
                build_info.code_revision.cache_clear()
                try:
                    return build_info.code_revision()
                finally:
                    build_info.code_revision.cache_clear()

    def test_git_checkout_reports_its_head(self):
        self.assertEqual(self.revision(git=self.COMMIT, stamp=b'b'*40+b'\n'), self.COMMIT)

    def test_image_without_git_reports_the_build_stamp(self):
        self.assertEqual(self.revision(git=FileNotFoundError('git'), stamp=self.COMMIT.encode()+b'\n'), self.COMMIT)

    def test_missing_empty_or_malformed_stamp_is_unknown(self):
        for stamp in (None, b'\n', b'abc123\n', b'A'*40, ('a'*39+'é').encode()):
            with self.subTest(stamp=stamp):
                self.assertEqual(self.revision(git=FileNotFoundError('git'), stamp=stamp), 'unknown')
        self.assertTrue(re.search(r'COPY --from=frontend /frontend/dist/ src/web/frontend/dist/', DOCKERFILE))


if __name__ == '__main__':
    unittest.main()
