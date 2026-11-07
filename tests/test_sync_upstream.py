"""Exercise actual Git objects and three-way merges, without network or credentials."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest
import zipfile

SPEC = importlib.util.spec_from_file_location('sync_upstream', Path(__file__).resolve().parents[1] / 'scripts/sync_upstream.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'core.autocrlf', 'false')
        self.text = b'one\ntwo\nthree\nfour\nfive\n'
        self.put('raw/codex/skill.md', self.text)
        self.put('raw/claude/skill.md', self.text)
        self.base = self.commit()

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True).stdout.decode().strip()

    def put(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def commit(self):
        self.git('add', '--all')
        self.git('commit', '-qm', 'fixture')
        return self.git('rev-parse', 'HEAD')

    def setup_hosts(self, ours=None):
        mappings = []
        for host in ('codex', 'claude'):
            self.put(f'plugins/{host}/skill.md', ours or self.text)
            self.put(f'plugins/{host}/plugin.json', b'{"version":"1.0.0"}\n')
            mappings.append({'source': f'raw/{host}', 'target': f'plugins/{host}',
                             'version_files': [f'plugins/{host}/plugin.json'],
                             'archive': f'packages/{host}-{{version}}.zip'})
        self.put('.upstream/state.json', json.dumps({'repository': 'fixture', 'commit': self.base, 'mappings': mappings}).encode())
        self.commit()

    def sync(self, revision, dry=False):
        with contextlib.redirect_stdout(io.StringIO()):
            return MODULE.sync(self.root, revision, dry)

    def test_both_hosts_retain_adaptations_and_package_updated_content(self):
        for host in ('codex', 'claude'):
            self.put(f'raw/{host}/skill.md', self.text.replace(b'five', b'upstream'))
        revision = self.commit()
        self.setup_hosts(self.text.replace(b'one', b'host adapter'))
        self.assertEqual(self.sync(revision), 0)
        for host in ('codex', 'claude'):
            data = (self.root / f'plugins/{host}/skill.md').read_bytes()
            self.assertEqual(data, b'host adapter\ntwo\nthree\nfour\nupstream\n')
            with zipfile.ZipFile(self.root / f'packages/{host}-1.0.1.zip') as archive:
                self.assertEqual(archive.read('skill.md'), data)
                self.assertEqual(json.loads(archive.read('plugin.json'))['version'], '1.0.1')
        self.assertEqual(json.loads((self.root / '.upstream/state.json').read_text())['commit'], revision)

    def test_conflict_preserves_every_host_and_baseline(self):
        self.put('raw/codex/skill.md', self.text.replace(b'one', b'upstream'))
        self.put('raw/claude/skill.md', self.text.replace(b'five', b'clean upstream'))
        revision = self.commit()
        ours = self.text.replace(b'one', b'adapted')
        self.setup_hosts(ours)
        self.assertEqual(self.sync(revision), 2)
        for host in ('codex', 'claude'):
            self.assertEqual((self.root / f'plugins/{host}/skill.md').read_bytes(), ours)
            self.assertEqual(json.loads((self.root / f'plugins/{host}/plugin.json').read_text())['version'], '1.0.0')
        self.assertEqual(json.loads((self.root / '.upstream/state.json').read_text())['commit'], self.base)
        self.assertTrue((self.root / '.upstream/INCOMING.patch').stat().st_size)

    def test_dry_run_and_unchanged_upstream_do_not_write(self):
        self.put('raw/codex/skill.md', self.text.replace(b'five', b'new'))
        revision = self.commit()
        self.setup_hosts()
        self.assertEqual(self.sync(revision, True), 0)
        self.assertEqual(self.git('status', '--porcelain'), '')
        self.assertEqual(self.sync(self.base), 0)
        self.assertEqual(self.git('status', '--porcelain'), '')

    def test_existing_edits_are_rejected(self):
        self.setup_hosts()
        self.put('user-work.txt', b'keep')
        with self.assertRaisesRegex(ValueError, 'clean checkout'):
            self.sync(self.base)
        self.assertEqual((self.root / 'user-work.txt').read_bytes(), b'keep')

    def test_rename_addition_and_deletion_are_applied(self):
        (self.root / 'raw/codex/skill.md').unlink()
        self.put('raw/codex/renamed.md', self.text)
        self.put('raw/claude/new.md', b'new skill')
        revision = self.commit()
        self.setup_hosts()
        self.assertEqual(self.sync(revision), 0)
        self.assertFalse((self.root / 'plugins/codex/skill.md').exists())
        self.assertEqual((self.root / 'plugins/codex/renamed.md').read_bytes(), self.text)
        self.assertEqual((self.root / 'plugins/claude/new.md').read_bytes(), b'new skill')

    def test_conflicting_deletion_and_binary_edits_require_review(self):
        for incoming in (None, b'\0new'):
            with self.subTest(incoming=incoming):
                with self.assertRaises(ValueError):
                    MODULE.merge_bytes(b'\0ours', b'\0base', incoming)

    def test_paths_cannot_escape_or_enter_git_metadata(self):
        for name in ('../outside', '/outside', '.git/config', 'C:/outside', 'a\\b'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                MODULE.safe_path(self.root, name)

    def test_windows_host_text_merges_with_lf_upstream(self):
        ours = self.text.replace(b'one', b'host').replace(b'\n', b'\r\n')
        incoming = self.text.replace(b'five', b'upstream')
        self.assertEqual(MODULE.merge_bytes(ours, self.text, incoming),
                         b'host\r\ntwo\r\nthree\r\nfour\r\nupstream\r\n')

    def test_latest_inventory_removes_adapted_skills_and_adapts_new_skills(self):
        skill = b'---\nname: obsolete\ndescription: old workflow\n---\n\nOld workflow\n'
        for host in ('codex', 'claude'):
            self.put(f'raw/{host}/skills/obsolete/SKILL.md', skill)
        self.base = self.commit()
        for host in ('codex', 'claude'):
            (self.root / f'raw/{host}/skills/obsolete/SKILL.md').unlink()
        self.put('raw/codex/skills/latest/SKILL.md', skill.replace(b'obsolete', b'latest'))
        revision = self.commit()
        self.setup_hosts()
        state_path = self.root / '.upstream/state.json'
        state = json.loads(state_path.read_text())
        state['align_skill_sets'] = True
        for host, mapping in zip(('codex', 'claude'), state['mappings']):
            mapping['host'] = host
            mapping['skills'] = ['obsolete']
            self.put(f'plugins/{host}/skills/obsolete/SKILL.md', MODULE.host_adapter(skill, host))
            self.put(f'plugins/{host}/skills/obsolete/host-only.txt', b'adaptation')
        state_path.write_text(json.dumps(state), encoding='utf-8')
        self.commit()
        self.assertEqual(self.sync(revision), 0)
        for host in ('codex', 'claude'):
            self.assertFalse((self.root / f'plugins/{host}/skills/obsolete/SKILL.md').exists())
            self.assertFalse((self.root / f'plugins/{host}/skills/obsolete/host-only.txt').exists())
            text = (self.root / f'plugins/{host}/skills/latest/SKILL.md').read_text()
            self.assertIn(f'../../docs/harness/{"codex" if host == "codex" else "claude-code"}.md', text)
        self.assertTrue(all(m['skills'] == ['latest'] for m in json.loads(state_path.read_text())['mappings']))
        self.commit()
        self.assertEqual(self.sync(revision), 0)
        self.assertEqual(self.git('status', '--porcelain'), '')
        (self.root / 'raw/codex/skills/latest/SKILL.md').unlink()
        self.put('raw/codex/skills/keep/SKILL.md', skill.replace(b'obsolete', b'keep'))
        newer = self.commit()
        self.assertEqual(self.sync(newer), 0)
        for host in ('codex', 'claude'):
            self.assertFalse((self.root / f'plugins/{host}/skills/latest/SKILL.md').exists())
            self.assertTrue((self.root / f'plugins/{host}/skills/keep/SKILL.md').exists())

    def test_binary_replacement_preserves_every_byte_and_conflicts_require_review(self):
        for base, incoming in ((b'\x89PNG\r\n\x1a\n\0old', b'\x89PNG\r\n\x1a\n\0new'),
                               (b'\0old\r\n', b'\0new\r\n'),
                               (b'\xffold\r\n', b'\xffnew\r\n')):
            with self.subTest(base=base):
                self.assertEqual(MODULE.merge_bytes(base, base, incoming), incoming)
                with self.assertRaises(ValueError):
                    MODULE.merge_bytes(base + b'ours', base, incoming)

    def test_rebuilding_same_update_ignores_mtime_and_preserves_candidate_tree(self):
        self.put('raw/codex/skill.md', self.text.replace(b'five', b'updated'))
        revision = self.commit()
        self.setup_hosts()
        self.put('plugins/codex/static.txt', b'unchanged resource')
        self.commit()
        with tempfile.TemporaryDirectory() as temp:
            other = Path(temp) / 'copy'
            shutil.copytree(self.root, other)
            for root, stamp in ((self.root, 1700000000), (other, 1800000000)):
                os.utime(root / 'plugins/codex/static.txt', (stamp, stamp))
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(MODULE.sync(root, revision), 0)
                subprocess.run(['git', '-C', str(root), 'add', '--all'], check=True, capture_output=True)
            self.assertEqual((self.root / 'packages/codex-1.0.1.zip').read_bytes(),
                             (other / 'packages/codex-1.0.1.zip').read_bytes())
            left = self.git('write-tree')
            right = subprocess.run(['git', '-C', str(other), 'write-tree'], check=True, capture_output=True).stdout.decode().strip()
            self.assertEqual(left, right)


if __name__ == '__main__':
    unittest.main()
