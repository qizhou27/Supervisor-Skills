"""Prepare a review branch by three-way merging upstream into each host tree.

No network, commits, pushes, installs, or upstream code execution happens here.
Requires Python 3.10+ and Git. Run from a clean checkout after fetching both SHAs.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import zipfile


def git(root, *args, check=True):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=check)


def safe_path(root, name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or ".git" in path.parts or "\\" in name or ":" in name:
        raise ValueError(f"Unsafe repository path: {name}")
    dest = root / path
    if not dest.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Path escapes repository: {name}")
    if any(p.is_symlink() for p in [dest, *dest.parents] if p.is_relative_to(root)):
        raise ValueError(f"Symlink in destination: {name}")
    return dest


def tree(root, revision, source):
    result = {}
    prefix = source.rstrip("/") + "/" if source else ""
    args = ["ls-tree", "-rz", revision]
    if source:
        args += ["--", source]
    for record in git(root, *args).stdout.split(b"\0"):
        if not record:
            continue
        meta, name = record.split(b"\t", 1)
        mode, kind, sha = meta.decode().split()
        name = name.decode("utf-8")
        if prefix and not name.startswith(prefix):
            continue
        if mode not in {"100644", "100755"} or kind != "blob":
            raise ValueError(f"Unsupported upstream entry: {name} ({mode})")
        result[name[len(prefix):]] = (mode, sha)
    if not result:
        raise ValueError(f"Empty upstream tree: {revision}:{source}")
    return result


def merge_bytes(ours, base, theirs):
    # Imported host trees use Windows line endings; upstream uses LF.
    # Compare and merge text independently of that packaging detail.
    def is_text(content):
        if content is None or b'\0' in content:
            return False
        try:
            content.decode('utf-8')
        except UnicodeDecodeError:
            return False
        return True
    def normalized(content):
        return content.replace(b'\r\n', b'\n') if is_text(content) else content
    original_ours = ours
    ours, base, theirs = map(normalized, (ours, base, theirs))
    def output(content):
        if is_text(content) and is_text(original_ours) and b'\r\n' in original_ours:
            return content.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
        return content
    if ours == base or ours == theirs:
        return output(theirs)
    if theirs == base:
        return original_ours
    if not all(is_text(b) for b in (ours, base, theirs)):
        raise ValueError("Conflicting addition, deletion, or binary change")
    with tempfile.TemporaryDirectory() as temp:
        paths = [Path(temp) / n for n in ("ours", "base", "upstream")]
        for path, content in zip(paths, (ours, base, theirs)):
            path.write_bytes(content)
        run = subprocess.run(["git", "merge-file", "-p", *map(str, paths)], capture_output=True)
    if run.returncode:
        raise ValueError("Overlapping host adaptation and upstream edits")
    return output(run.stdout)


def host_adapter(content, host):
    text = content.decode('utf-8')
    if host == 'codex':
        marker = '../../docs/harness/codex.md'
        preface = ("## Codex compatibility\n\nBefore this workflow, read [Codex host adaptation](../../docs/harness/codex.md). "
                   "Then read [Codex execution](../../docs/harness/codex-execution.md) for delegation, model selection, and evidence rules. "
                   "Keep this skill's upstream workflow; use these concrete Codex mappings.\n\n")
    else:
        marker = '../../docs/harness/claude-code.md'
        preface = ("Before this workflow, read [the Claude Code adapter](../../docs/harness/claude-code.md). "
                   "Its host mappings govern this skill and all referenced prompts and playbooks. "
                   "Preserve the upstream workflow while applying those mappings.\n\n")
    if marker in text:
        return content
    match = re.match(r'\A---\r?\n.*?\r?\n---\r?\n', text, re.S)
    if not match:
        raise ValueError('New skill has invalid frontmatter')
    return (text[:match.end()] + '\n' + preface + text[match.end():].lstrip()).encode('utf-8')


def write_archive(root, source, archive_path):
    """Make retries byte-identical; retain executable bits recorded by Git."""
    modes = {}
    for entry in git(root, 'ls-files', '--stage', '-z').stdout.split(b'\0'):
        if entry:
            meta, name = entry.split(b'\t', 1)
            modes[name.decode('utf-8')] = int(meta.split()[0], 8)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob('*')):
            if path.is_file():
                info = zipfile.ZipInfo(path.relative_to(source).as_posix(), (1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = modes[path.relative_to(root).as_posix()] << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes())


def sync(root, revision, dry_run=False):
    config_path = root / ".upstream/state.json"
    state = json.loads(config_path.read_text(encoding="utf-8"))
    base = state["commit"]
    for sha in (base, revision):
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("Expected a full Git commit SHA")
        git(root, "cat-file", "-e", f"{sha}^{{commit}}")
    if git(root, "status", "--porcelain", "--untracked-files=all").stdout:
        raise ValueError("Use a clean checkout; existing edits must be committed first")
    writes, conflicts, changed_mappings = {}, [], []
    trees = [(tree(root, base, m['source']), tree(root, revision, m['source'])) for m in state['mappings']]
    def aligned(side):
        primary = trees[0][side]
        canonical = {n.split('/')[1] for n in primary if re.fullmatch(r'skills/[^/]+/SKILL.md', n)}
        if not canonical:
            raise ValueError('Upstream plugin has no skills; migration needs review')
        result = []
        for pair in trees:
            current = {n: item for n, item in pair[side].items() if not n.startswith('skills/') or len(n.split('/')) < 3 or n.split('/')[1] in canonical}
            for skill in canonical:
                if f'skills/{skill}/SKILL.md' not in current:
                    current.update({n: item for n, item in primary.items() if n.startswith(f'skills/{skill}/')})
            result.append(current)
        return result
    if state.get('align_skill_sets'):
        # Fallback must exist in both sides of the three-way merge, including
        # retries and later deletion of a skill the Claude harness never had.
        trees = list(zip(aligned(0), aligned(1)))
    for mapping, (before, after) in zip(state["mappings"], trees):
        after = dict(after)
        previous_skills = {n.split('/')[1] for n in before if re.fullmatch(r'skills/[^/]+/SKILL.md', n)}
        incoming_skills = {n.split('/')[1] for n in after if re.fullmatch(r'skills/[^/]+/SKILL.md', n)}
        removed = previous_skills - incoming_skills if mapping.get('host') or mapping.get('track_skills') else set()
        if mapping.get('host') or mapping.get('track_skills'):
            mapping['skills'] = sorted(incoming_skills)
            mapping['agents'] = sorted(n for n in after if re.fullmatch(r'agents/[^/]+\.md', n))
        changed = False
        for skill in removed:
            folder = safe_path(root, (PurePosixPath(mapping['target']) / 'skills' / skill).as_posix())
            for path in folder.rglob('*'):
                if path.is_file():
                    writes[path.relative_to(root).as_posix()] = (None, None)
                    changed = True
        for name in sorted(before.keys() | after.keys()):
            if before.get(name) == after.get(name):
                continue
            target = (PurePosixPath(mapping["target"]) / name).as_posix()
            dest = safe_path(root, target)
            old = git(root, "cat-file", "blob", before[name][1]).stdout if name in before else None
            new = git(root, "cat-file", "blob", after[name][1]).stdout if name in after else None
            ours = dest.read_bytes() if dest.is_file() else None
            try:
                removed_skill = (name.startswith('skills/') and name.split('/')[1] in removed) or (new is None and mapping.get('follow_upstream_removals'))
                merged = None if removed_skill else merge_bytes(ours, old, new)
                if merged is not None and mapping.get('host') and re.fullmatch(r'skills/[^/]+/SKILL.md', name):
                    merged = host_adapter(merged, mapping['host'])
                if not removed_skill and before.get(name, (None,))[0] != after.get(name, (None,))[0] and ours != old:
                    raise ValueError("Mode change on an adapted file")
            except ValueError as exc:
                conflicts.append(f"{target}: {exc}")
                continue
            if merged != ours or before.get(name, (None,))[0] != after.get(name, (None,))[0]:
                writes[target] = (merged, after.get(name, (None,))[0])
                changed = True
        if changed:
            changed_mappings.append(mapping)
    report = [f"# Upstream update {revision[:12]}", "", f"Source: {state['repository']}",
              f"Baseline: `{base}`", f"Candidate: `{revision}`", ""]
    if conflicts:
        report += ["BLOCKED: resolve these conflicts before merging. Installed files are unchanged.", ""]
        report += [f"- {item}" for item in conflicts]
    else:
        report += [f"Three-way merge prepared {len(writes)} file changes; existing host edits were retained.", "",
                   "Review both host behavior and CI before merging. Automated checks do not prove model behavior."]
    if dry_run or (not writes and not conflicts):
        print(json.dumps({"files": len(writes), "conflicts": conflicts, "dry_run": dry_run}))
        return 2 if conflicts else 0
    metadata = root / ".upstream"
    metadata.mkdir(exist_ok=True)
    (metadata / "REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    if conflicts:
        sources = [m["source"] for m in state["mappings"] if m["source"]]
        patch = git(root, "diff", "--binary", base, revision, "--", *sources).stdout
        (metadata / "INCOMING.patch").write_bytes(patch)
        print(json.dumps({"files": 0, "conflicts": conflicts}))
        return 2
    # Every merge is planned before the first host file is changed.
    for name, (content, mode) in writes.items():
        dest = safe_path(root, name)
        if content is None:
            dest.unlink(missing_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
            git(root, "update-index", "--add", "--cacheinfo", mode,
                git(root, "hash-object", "-w", "--", name).stdout.decode().strip(), name)
            dest.chmod(0o755 if mode == "100755" else 0o644)
    for mapping in changed_mappings:
        manifest = safe_path(root, mapping["version_files"][0])
        old = json.loads(manifest.read_text(encoding="utf-8"))["version"]
        prefix, number = old.rsplit(".", 1)
        version = f"{prefix}.{int(number) + 1}"
        for name in mapping["version_files"]:
            path = safe_path(root, name)
            path.write_text(path.read_text(encoding="utf-8").replace(old, version), encoding="utf-8")
        if mapping.get("archive"):
            archive_path = safe_path(root, mapping["archive"].format(version=version))
            source = safe_path(root, mapping["target"])
            write_archive(root, source, archive_path)
    state["commit"] = revision
    config_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"files": len(writes), "conflicts": []}))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("revision", help="Fetched upstream commit SHA")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        raise SystemExit(sync(Path(__file__).resolve().parents[1], args.revision, args.dry_run))
    except (ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Upstream sync failed: {exc}\n")
