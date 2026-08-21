"""Cross-reference check for the agent instruction files under `.claude/`.

`.claude/commands/*.md` and `.claude/agents/*.md` are executed by agents, not
by a compiler, so a reference to a role or command file that is not in the
tree fails only at run time — and the observed failure modes are recoverable
ones (a "file does not exist" read error, or an "agent type not found" dispatch
error that helpfully lists the real roles), which invites an agent to improvise
a substitute rather than stop. At the review step that is how the rule that an
implementation agent never reviews its own work stops holding silently.

Scope of this check, and its limits:

* Only `.claude/commands/` and `.claude/agents/` are scanned. `.claude/skills/`
  is excluded because it contains deliberate negative references — prose that
  names a path in order to record that the path is gone (see the operator notes
  in `.claude/skills/release/SKILL.md`), which an existence check cannot tell
  apart from drift.
* Only references that must resolve *before* a command runs are checked: role
  files, command files, and slash-command names. Paths a command writes to are
  not checked, because they are outputs and are legitimately absent up front.
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

#: Files this check scans. Skills are deliberately out of scope (see module docstring).
SCANNED_DIRS = (COMMANDS_DIR, AGENTS_DIR)

_BACKTICKED = re.compile(r"`([^`\n]+)`")
_AGENT_REF = re.compile(r"^\.claude/agents/([A-Za-z0-9_-]+)\.md$")
_COMMAND_REF = re.compile(r"^\.claude/commands/([A-Za-z0-9_-]+)\.md$")
_SLASH_REF = re.compile(r"^/([a-z][a-z0-9-]*)$")
_FRONTMATTER_NAME = re.compile(r"^name:\s*(\S+)\s*$", re.MULTILINE)


def _scanned_files() -> list[Path]:
    files = [p for d in SCANNED_DIRS for p in sorted(d.glob("*.md"))]
    assert files, "no instruction files found to check"
    return files


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
