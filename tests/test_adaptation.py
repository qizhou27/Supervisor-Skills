"""Reject actual packaged mutations that would enable implicit invocation or mix hosts."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('verify_adaptation', ROOT / 'scripts/verify_adaptation.py')
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)
BUILD_SPEC = importlib.util.spec_from_file_location('build_plugins', ROOT / 'scripts/build_plugins.py')
BUILD = importlib.util.module_from_spec(BUILD_SPEC)
BUILD_SPEC.loader.exec_module(BUILD)


class InvocationTests(unittest.TestCase):
    def test_package_text_is_independent_of_checkout_and_binary_is_exact(self):
        self.assertEqual(BUILD.package_bytes(b'line\r\nnext\r\n'), b'line\nnext\n')
        self.assertEqual(BUILD.package_bytes(b'line\nnext\n'), b'line\nnext\n')
        for binary in (b'\x89PNG\r\n\x1a\n\0payload', b'\0line\r\n', b'\xffline\r\n'):
            self.assertEqual(BUILD.package_bytes(binary), binary)

    def test_new_upstream_skill_gets_explicit_policy_without_losing_workflow(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill = root / 'skills/new-research'
            skill.mkdir(parents=True)
            (skill / 'SKILL.md').write_text('---\nname: new-research\ndescription: New research workflow.\n---\n\n# Preserve this workflow\n', encoding='utf-8')
            BUILD.adapt_source(root)
            content = (skill / 'SKILL.md').read_text(encoding='utf-8')
            self.assertIn('Explicit invocation only:', content)
            self.assertIn('Invocation boundary:', content)
            self.assertIn('# Preserve this workflow', content)
            self.assertIn('allow_implicit_invocation: false', (skill / 'agents/openai.yaml').read_text())
            BUILD.adapt_source(root)
            self.assertEqual((skill / 'SKILL.md').read_text(encoding='utf-8'), content)

    def test_good_package_passes_and_bad_invocation_or_host_routes_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('.codex-plugin', '.claude-plugin', '.agents', '.upstream', 'skills', 'plugins'):
                shutil.copytree(ROOT / name, root / name)
            expected = json.loads((root / '.upstream/state.json').read_text(encoding='utf-8'))['mappings'][0]['skills']
            self.assertTrue(expected)
            self.assertEqual(VERIFY.verify(root)['skills'], len(expected))
            name = expected[0]
            mutations = [
                (f'skills/{name}/agents/openai.yaml', 'false', 'true'),
                (f'plugins/supervisor-skills-claude/skills/{name}/SKILL.md',
                 'disable-model-invocation: true', 'disable-model-invocation: false'),
                ('.claude-plugin/marketplace.json', './plugins/supervisor-skills-claude', './'),
            ]
            for name, old, new in mutations:
                with self.subTest(name=name):
                    path = root / name
                    original = path.read_text(encoding='utf-8')
                    path.write_text(original.replace(old, new), encoding='utf-8')
                    with self.assertRaises(AssertionError):
                        VERIFY.verify(root)
                    path.write_text(original, encoding='utf-8')


if __name__ == '__main__':
    unittest.main()
