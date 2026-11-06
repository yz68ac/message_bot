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

    def test_host_frontend_build_stays_out_of_the_build_context(self):
        ignored = [line.strip() for line in (ROOT/'.dockerignore').read_text(encoding='utf-8').splitlines()]
        self.assertIn('src/web/frontend/dist', ignored)
        # The exclusion must follow the src/** allowlist, which would otherwise re-include it.
        self.assertGreater(ignored.index('src/web/frontend/dist'), ignored.index('!src/**'))
        self.assertTrue(re.search(r'COPY --from=frontend /frontend/dist/ src/web/frontend/dist/', DOCKERFILE))


if __name__ == '__main__':
    unittest.main()
