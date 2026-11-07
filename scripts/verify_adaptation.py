"""Validate both marketplace entries and explicit invocation for every skill."""
import json
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]


def verify(root=ROOT):
    codex = json.loads((root / '.codex-plugin/plugin.json').read_text(encoding='utf-8'))
    codex_package = root / 'plugins/supervisor-skills'
    assert codex == json.loads((codex_package / '.codex-plugin/plugin.json').read_text(encoding='utf-8'))
    claude_root = root / 'plugins/supervisor-skills-claude'
    claude = json.loads((claude_root / '.claude-plugin/plugin.json').read_text(encoding='utf-8'))
    cm = json.loads((root / '.agents/plugins/marketplace.json').read_text(encoding='utf-8'))
    am = json.loads((root / '.claude-plugin/marketplace.json').read_text(encoding='utf-8'))
    assert codex['name'] == claude['name'] == 'supervisor-skills'
    assert codex['version'] == claude['version'] == am['plugins'][0]['version']
    assert cm['name'] == 'supervisor-skills-local'
    assert cm['plugins'][0]['source']['path'] == './plugins/supervisor-skills'
    assert am['name'] == 'supervisor-skills-dual'
    assert am['plugins'][0]['source'] == './plugins/supervisor-skills-claude'
    skills = sorted((root / 'skills').glob('*/SKILL.md'))
    state = json.loads((root / '.upstream/state.json').read_text(encoding='utf-8'))
    expected_skills = state['mappings'][0]['skills']
    assert expected_skills and {p.parent.name for p in skills} == set(expected_skills), 'Upstream skill inventory drift'
    for path in skills:
        text = path.read_text(encoding='utf-8')
        fm = yaml.safe_load(text.split('---', 2)[1])
        assert 'disable-model-invocation' not in fm, 'Keep Claude frontmatter out of Codex skills'
        claude_text = (claude_root / 'skills' / path.parent.name / 'SKILL.md').read_text(encoding='utf-8')
        claude_fm = yaml.safe_load(claude_text.split('---', 2)[1])
        assert claude_fm.pop('disable-model-invocation') is True, path
        assert claude_fm == fm, path
        assert claude_text.split('---', 2)[2] == text.split('---', 2)[2], path
        expected = f"Explicit invocation only: use when the user names {path.parent.name} or explicitly requests Supervisor-Skills for this task. "
        assert fm['description'].startswith(expected), path
        assert 'Invocation boundary: Do not activate from task similarity alone.' in text, path
        policy = yaml.safe_load((path.parent / 'agents/openai.yaml').read_text(encoding='utf-8'))
        assert policy['policy']['allow_implicit_invocation'] is False, path
    assert not (root / '.upstream/INCOMING.patch').exists(), 'Resolve upstream conflicts before merging'
    return {'skills': len(skills), 'codex_implicit_invocation': False, 'claude_model_invocation': False}


if __name__ == '__main__':
    print(json.dumps(verify(), indent=2))
