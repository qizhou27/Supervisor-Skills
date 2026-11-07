"""Generate isolated Codex and Claude packages from the canonical research skills."""
import argparse
import json
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {'codex': ROOT / 'plugins/supervisor-skills',
           'claude': ROOT / 'plugins/supervisor-skills-claude'}


def package_bytes(content):
    """Canonical text prevents checkout line endings from changing host packages."""
    if b'\0' not in content:
        try:
            content.decode('utf-8')
        except UnicodeDecodeError:
            pass
        else:
            return content.replace(b'\r\n', b'\n')
    return content


def adapt_source(root=ROOT):
    """Apply the existing explicit-only policy to newly added upstream skills."""
    boundary = ('Invocation boundary: Do not activate from task similarity alone. Use only when the user explicitly '
                'invokes this skill or requests Supervisor-Skills for the current task. References to sibling skills '
                'do not authorize implicit activation.')
    for path in sorted((root / 'skills').glob('*/SKILL.md')):
        text = path.read_text(encoding='utf-8')
        _, front, body = text.split('---', 2)
        metadata = yaml.safe_load(front)
        prefix = (f'Explicit invocation only: use when the user names {path.parent.name} '
                  'or explicitly requests Supervisor-Skills for this task. ')
        changed = False
        if not metadata['description'].startswith(prefix):
            metadata['description'] = prefix + metadata['description']
            changed = True
        if 'disable-model-invocation' in metadata:
            metadata.pop('disable-model-invocation')
            changed = True
        if boundary not in body:
            body = '\n\n' + boundary + '\n\n' + body.lstrip()
            changed = True
        if changed:
            path.write_text('---\n' + yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True) + '---' + body, encoding='utf-8')
        policy_path = path.parent / 'agents/openai.yaml'
        policy = yaml.safe_load(policy_path.read_text(encoding='utf-8')) if policy_path.exists() else {}
        if policy.get('policy', {}).get('allow_implicit_invocation') is not False:
            policy.setdefault('policy', {})['allow_implicit_invocation'] = False
            policy_path.parent.mkdir(exist_ok=True)
            policy_path.write_text(yaml.safe_dump(policy, sort_keys=False, allow_unicode=True), encoding='utf-8')


def expected_files(host):
    manifest = json.loads((ROOT / '.codex-plugin/plugin.json').read_text(encoding='utf-8'))
    metadata = {'name': manifest['name'], 'version': manifest['version'],
                'author': manifest['author'],
                'description': 'Supervisor-Skills research workflows; invoke skills explicitly.',
                'homepage': manifest['homepage'], 'license': 'CC-BY-NC-SA-4.0'}
    result = {'LICENSE': package_bytes((ROOT / 'LICENSE').read_bytes())}
    if host == 'codex':
        result['.codex-plugin/plugin.json'] = package_bytes((ROOT / '.codex-plugin/plugin.json').read_bytes())
    else:
        result['.claude-plugin/plugin.json'] = (json.dumps(metadata, indent=2) + '\n').encode()
    for skill in sorted((ROOT / 'skills').iterdir()):
        if not (skill / 'SKILL.md').is_file():
            continue
        for path in sorted(skill.rglob('*')):
            if not path.is_file() or '__pycache__' in path.parts or path.suffix == '.pyc':
                continue
            content = package_bytes(path.read_bytes())
            if path.name == 'SKILL.md' and host == 'claude':
                text = path.read_text(encoding='utf-8')
                assert text.startswith('---\n'), path
                content = text.replace('---\n', '---\ndisable-model-invocation: true\n', 1).encode()
            result[path.relative_to(ROOT).as_posix()] = content
    return result


def build(check=False):
    if not check:
        adapt_source()
    for host, target in TARGETS.items():
        expected = expected_files(host)
        actual = {p.relative_to(target).as_posix(): p for p in target.rglob('*') if p.is_file()}
        if check:
            assert set(actual) == set(expected), f'{host} package inventory differs from generator'
            differences = [n for n, data in expected.items() if actual[n].read_bytes() != data]
            assert not differences, f'{host} package content drift: {differences[:5]}'
        else:
            # Only remove generated files; root skills and history remain intact.
            for name in actual.keys() - expected.keys():
                actual[name].unlink()
            for name, data in expected.items():
                destination = target / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
        print(json.dumps({'host': host, 'files': len(expected), 'check': check}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    build(parser.parse_args().check)
