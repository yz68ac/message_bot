"""A tagged SDK wheel is imported independently and tampering fails before writes."""
import json
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
UPDATE = runpy.run_path(str(ROOT/'scripts/update-wechat-adapter.py'))
IMPORT = UPDATE['import_release']


class AdapterUpdateTests(unittest.TestCase):
    def test_tagged_wheel_import_and_tampered_record_are_checked(self):
        manifest=json.loads((ROOT/'dependencies/adapter_wc.json').read_text(encoding='utf-8'))
        version=manifest['version']
        wheel=ROOT/'dependencies'/manifest['wheel']
        temporary=ROOT/'.test-tmp';temporary.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary) as directory:
            base=Path(directory);repo=base/'adapter';app=base/'app'
            repo.mkdir();(app/'scripts').mkdir(parents=True)
            shutil.copyfile(ROOT/'scripts/adapter-wheel.py',app/'scripts/adapter-wheel.py')
            (repo/'pyproject.toml').write_text('[project]\nname="adapter_wc"\nversion="'+version+'"\n',encoding='utf-8')
            (app/'pyproject.toml').write_text('[project]\ndependencies=["adapter_wc==0.2.0"]\n'
                '[tool.uv.sources]\nadapter_wc = { path = "dependencies/adapter_wc-0.2.0-py3-none-any.whl" }\n',encoding='utf-8')
            with zipfile.ZipFile(wheel) as archive:
                for name in archive.namelist():
                    if not name.startswith('adapter_wc/'):continue
                    target=repo/'src'/name.removeprefix('adapter_wc/');target.parent.mkdir(parents=True,exist_ok=True)
                    target.write_bytes(archive.read(name))
            def git(*args):
                return subprocess.check_output(['git','-c','user.name=Synthetic','-c','user.email=synthetic@example.com',
                    '-c','commit.gpgsign=false',*args],cwd=repo,text=True).strip()
            git('init','-q');git('add','.');git('commit','-qm','synthetic');git('tag','v'+version)
            result=IMPORT(wheel,repo,root=app)
            self.assertEqual(result['source']['commit'],git('rev-parse','HEAD'))
            self.assertEqual(result['source']['tag'],'v'+version)
            self.assertIn('adapter_wc=='+version,(app/'pyproject.toml').read_text(encoding='utf-8'))
            before=(app/'dependencies/adapter_wc.json').read_bytes()
            rebuilt=base/'rebuilt'/wheel.name;rebuilt.parent.mkdir()
            with zipfile.ZipFile(wheel) as source,zipfile.ZipFile(rebuilt,'w',compression=zipfile.ZIP_STORED) as target:
                for item in source.infolist():target.writestr(item.filename,source.read(item.filename))
            self.assertNotEqual(wheel.read_bytes(),rebuilt.read_bytes())
            self.assertEqual(IMPORT(rebuilt,repo,root=app)['sha256'],result['sha256'])
            self.assertEqual((app/'dependencies/adapter_wc.json').read_bytes(),before)
            bad=base/'bad'/wheel.name;bad.parent.mkdir()
            with zipfile.ZipFile(wheel) as source,zipfile.ZipFile(bad,'w') as target:
                for item in source.infolist():
                    value=source.read(item.filename)
                    if item.filename=='adapter_wc/session.py':value+=b'\n# tampered\n'
                    target.writestr(item,value)
            with self.assertRaisesRegex(ValueError,'adapter_record_mismatch'):
                IMPORT(bad,repo,root=app)
            self.assertEqual((app/'dependencies/adapter_wc.json').read_bytes(),before)


class AdapterReleaseCommandTests(unittest.TestCase):
    def test_release_runs_the_adapter_script_then_imports_relocks_syncs_and_tests(self):
        calls, imported = [], []
        def run(args, **kwargs):
            calls.append((args, Path(kwargs['cwd'])))
            return subprocess.CompletedProcess(args, 0)
        def importer(wheel, repository, *, root):
            imported.append((wheel, repository, root))
            return dict(name='adapter_wc', version='9.9.9', source=dict(commit='a'*40))
        repo, app = Path('/synthetic/adapter_wc'), Path('/synthetic/app')
        UPDATE['release_and_import']('9.9.9', repo, root=app, run=run, importer=importer)
        repo, app = repo.resolve(), app.resolve()
        self.assertEqual(calls[0], ([calls[0][0][0], str(repo/'scripts/release.py'), '9.9.9'], repo))
        self.assertEqual(imported, [(repo/'dist/adapter_wc-9.9.9-py3-none-any.whl', repo, app)])
        self.assertEqual([args[1] if args[0] != 'uv' else ' '.join(args[:2]) for args, _ in calls[1:]],
                         ['scripts/dependencies.py', 'uv sync', 'uv run'])
        self.assertIn('scripts/test-offline.py', calls[-1][0])
        self.assertTrue(all(cwd == app for _, cwd in calls[1:]))

    def test_failed_adapter_release_imports_nothing(self):
        def run(args, **kwargs):
            raise subprocess.CalledProcessError(1, args)
        def importer(*args, **kwargs):
            raise AssertionError('must not import')
        with self.assertRaises(subprocess.CalledProcessError):
            UPDATE['release_and_import']('9.9.9', Path('/synthetic/adapter_wc'), root=Path('/synthetic/app'),
                                         run=run, importer=importer)

    def test_wheel_and_release_are_mutually_exclusive(self):
        for argv in (['--source-repo', 'x'], ['w.whl', '--release', '9.9.9', '--source-repo', 'x']):
            with self.assertRaises(SystemExit):
                UPDATE['main'](argv)


if __name__=='__main__':
    unittest.main()
