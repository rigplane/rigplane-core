"""Cross-reference check for the agent instruction files under `.claude/`.

`.claude/commands/*.md` and `.claude/agents/*.md` are executed by agents, not
by a compiler, so a reference to a role or command file that is not in the
tree fails only at run time — and the observed failure modes are recoverable
ones (a "file does not exist" read error, or an "agent type not found" dispatch
error that helpfully lists the real roles), which invites an agent to improvise
a substitute rather than stop. At the review step that is how the rule that an
implementation agent never reviews its own work stops holding silently.

Scope of this check, and its limits:

* Role-file, command-file, and slash-command references are checked by shape
  (`test_referenced_agent_roles_exist`, `test_referenced_command_files_exist`,
  `test_referenced_slash_commands_exist`), scanning only `.claude/commands/`
  and `.claude/agents/`. `.claude/skills/` is excluded from these three
  because it contains deliberate negative references — prose that names a
  path in order to record that the path is gone (see the operator notes in
  `.claude/skills/release/SKILL.md`), which an existence check cannot tell
  apart from drift.
* Every other `.claude/**` file reference is checked by
  `test_referenced_dot_claude_files_exist`, which also scans
  `.claude/skills/` — none of its file references share the negative-reference
  problem above, since that check skips anything already shaped like a
  role/command reference.
* `.claude/workflow/`, `.claude/queue/`, and `.claude/metrics.json` name a
  bookkeeping mechanism referenced across six files (`decompose-issue.md`,
  `generate-tests.md`, `refactor.md`, `regression-check.md`, `scan-issues.md`,
  `release/SKILL.md`) that this repo has never had: no command anywhere in
  the tree creates those paths, and none of them exist. That is a wider defect
  than the isolated `.claude/knowledge/` reads this module was extended to
  catch, so `_EXEMPT_STATE_PREFIXES` names these three and
  `test_referenced_dot_claude_files_exist` exempts them rather than flagging
  them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CLAUDE_DIR = REPO_ROOT / ".claude"
AGENTS_DIR = CLAUDE_DIR / "agents"
COMMANDS_DIR = CLAUDE_DIR / "commands"
SKILLS_DIR = CLAUDE_DIR / "skills"

#: Files the role/command/slash checks scan. Skills are deliberately out of
#: scope for those three (see module docstring); `test_referenced_dot_claude_files_exist`
#: scans skills separately, below.
SCANNED_DIRS = (COMMANDS_DIR, AGENTS_DIR)

_BACKTICKED = re.compile(r"`([^`\n]+)`")
_AGENT_REF = re.compile(r"^\.claude/agents/([A-Za-z0-9_-]+)\.md$")
_COMMAND_REF = re.compile(r"^\.claude/commands/([A-Za-z0-9_-]+)\.md$")
_SLASH_REF = re.compile(r"^/([a-z][a-z0-9-]*)$")
_FRONTMATTER_NAME = re.compile(r"^name:\s*(\S+)\s*$", re.MULTILINE)

#: Any backticked token shaped like a `.claude/` file reference (a path plus
#: a file extension — bare directory references like `.claude/agents/` don't
#: match and aren't checked).
_DOT_CLAUDE_FILE_REF = re.compile(r"^\.claude/\S+\.\w+$")

#: See the module docstring's third bullet: a referenced-but-never-created
#: bookkeeping mechanism, out of scope for this module, exempted by prefix.
_EXEMPT_STATE_PREFIXES = (
    ".claude/workflow/",
    ".claude/queue/",
    ".claude/metrics.json",
)


def _scanned_files() -> list[Path]:
    files = [p for d in SCANNED_DIRS for p in sorted(d.glob("*.md"))]
    assert files, "no instruction files found to check"
    return files


def _skill_files() -> list[Path]:
    return sorted(SKILLS_DIR.glob("**/*.md"))


def _references(path: Path) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        for token in _BACKTICKED.findall(line):
            out.append((lineno, token.strip()))
    return out


def _frontmatter_name(path: Path) -> str | None:
    match = _FRONTMATTER_NAME.search(path.read_text())
    return match.group(1) if match else None


@pytest.mark.parametrize(
    "agent_file", sorted(AGENTS_DIR.glob("*.md")), ids=lambda p: p.name
)
def test_agent_frontmatter_name_matches_filename(agent_file: Path) -> None:
    """A role file must declare the name it is dispatched under.

    Roles are dispatched by name, so a file whose frontmatter `name` differs
    from its stem is reachable under neither: the path resolves but holds a
    different role than the reference claims.
    """
    assert _frontmatter_name(agent_file) == agent_file.stem


def test_referenced_agent_roles_exist() -> None:
    """Every `.claude/agents/<role>.md` reference names a role that exists."""
    dangling: list[str] = []
    for path in _scanned_files():
        for lineno, token in _references(path):
            match = _AGENT_REF.match(token)
            if match is None:
                continue
            role = match.group(1)
            target = AGENTS_DIR / f"{role}.md"
            if not target.is_file():
                known = sorted(p.stem for p in AGENTS_DIR.glob("*.md"))
                dangling.append(
                    f"{path.relative_to(REPO_ROOT)}:{lineno} -> {token} "
                    f"(no such role; roles are {known})"
                )
            elif _frontmatter_name(target) != role:
                dangling.append(
                    f"{path.relative_to(REPO_ROOT)}:{lineno} -> {token} "
                    f"(file exists but declares name {_frontmatter_name(target)!r})"
                )
    assert not dangling, "dangling agent-role references:\n" + "\n".join(dangling)


def test_referenced_command_files_exist() -> None:
    """Every `.claude/commands/<name>.md` reference resolves."""
    dangling = [
        f"{path.relative_to(REPO_ROOT)}:{lineno} -> {token}"
        for path in _scanned_files()
        for lineno, token in _references(path)
        if (m := _COMMAND_REF.match(token))
        and not (COMMANDS_DIR / f"{m.group(1)}.md").is_file()
    ]
    assert not dangling, "dangling command-file references:\n" + "\n".join(dangling)


def test_referenced_slash_commands_exist() -> None:
    """Every `/name` reference resolves to a command file or a skill."""
    dangling: list[str] = []
    for path in _scanned_files():
        for lineno, token in _references(path):
            match = _SLASH_REF.match(token)
            if match is None:
                continue
            name = match.group(1)
            if (COMMANDS_DIR / f"{name}.md").is_file():
                continue
            if (SKILLS_DIR / name / "SKILL.md").is_file():
                continue
            dangling.append(f"{path.relative_to(REPO_ROOT)}:{lineno} -> {token}")
    assert not dangling, "dangling slash-command references:\n" + "\n".join(dangling)


def test_referenced_dot_claude_files_exist() -> None:
    """Every other `.claude/**` file reference resolves.

    Generalizes the three checks above to any `.claude/` file reference, not
    just the role/command/slash shapes those match, and scans
    `.claude/skills/` in addition to commands and agents (see module
    docstring). References already covered above are skipped here so each
    dangling reference is reported by exactly one test.
    """
    dangling: list[str] = []
    for path in (*_scanned_files(), *_skill_files()):
        for lineno, token in _references(path):
            if not _DOT_CLAUDE_FILE_REF.match(token):
                continue
            if _AGENT_REF.match(token) or _COMMAND_REF.match(token):
                continue
            if any(token.startswith(prefix) for prefix in _EXEMPT_STATE_PREFIXES):
                continue
            if not (REPO_ROOT / token).is_file():
                dangling.append(f"{path.relative_to(REPO_ROOT)}:{lineno} -> {token}")
    assert not dangling, "dangling .claude file references:\n" + "\n".join(dangling)
