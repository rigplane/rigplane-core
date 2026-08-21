"""Cross-reference check for the agent instruction files under `.claude/`.

`.claude/commands/*.md` and `.claude/agents/*.md` are executed by agents, not
by a compiler, so a reference to a role or command file that is not in the
tree fails only at run time — and the observed failure modes are recoverable
ones (a "file does not exist" read error, or an "agent type not found" dispatch
error that helpfully lists the real roles), which invites an agent to improvise
a substitute rather than stop. At the review step that is how the rule that an
implementation agent never reviews its own work stops holding silently.

Scope of this check, and its limits:

* Only references that must resolve *before* a command runs are checked. Paths
  a command writes to are not, because they are outputs and are legitimately
  absent up front. Every exemption below rests on that distinction.
* Role-file, command-file, and slash-command references are checked by shape
  (`test_referenced_agent_roles_exist`, `test_referenced_command_files_exist`,
  `test_referenced_slash_commands_exist`), scanning only `.claude/commands/`
  and `.claude/agents/`. `.claude/skills/` is excluded from these three
  because it contains deliberate negative references — prose that names a
  path in order to record that the path is gone (see the operator notes in
  `.claude/skills/release/SKILL.md`), which an existence check cannot tell
  apart from drift.
* Every other `.claude/**` file reference is checked by
  `test_referenced_dot_claude_files_exist`, which also scans `.claude/skills/`
  (its file references do not share the negative-reference problem above).
  That check is a **regression pin**, not live coverage: it currently
  evaluates zero tokens, because every `.claude/**` file token in the tree is
  either already covered by the three checks above or exempted by
  `_EXEMPT_STATE_PREFIXES`. It fails when a *new* reference is added outside
  those prefixes and does not resolve. `.claude/skills/` contributes three
  tokens, two silenced by the prefix exemption and one as a command reference,
  so that arm of the scan is inert today too.
* `_EXEMPT_STATE_PREFIXES` names a bookkeeping mechanism — per-run workflow
  files, an issue queue, a metrics counter, a knowledge store — that is not
  tracked in git and so is absent from any fresh checkout. Two things it is
  not. It is not uncreated: commands do instruct its creation (`scan-issues.md`
  writes the queue, `regression-check.md` the regression file and the metrics
  counter, `audit-ui.md` its findings file, and others). And it is not unused:
  absence from a checkout says nothing about whether a command ever ran, since
  `.claude/*` is gitignored and work happens on more than one machine —
  rigplane-core#601 ends with a source line naming
  `.claude/workflow/audit-findings.md`, so `audit-ui` has produced one. Every
  reference to these prefixes is a write, an output declaration, which the
  first bullet says not to check; they are exempted by prefix rather than
  flagged. The prefix tuple below is the authority on which they are — this
  docstring deliberately does not enumerate the referring files, because a
  hand-maintained census in prose rots silently.
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

#: See the module docstring's last bullet: an untracked bookkeeping mechanism
#: whose references are all writes, so out of scope for this module.
_EXEMPT_STATE_PREFIXES = (
    ".claude/workflow/",
    ".claude/queue/",
    ".claude/metrics.json",
    ".claude/knowledge/",
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

    This evaluates zero tokens at present and is a pin against future drift;
    `test_dot_claude_file_check_is_currently_a_pin` records that fact so it
    cannot become false silently.
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


def _dot_claude_tokens_evaluated() -> list[str]:
    """The tokens `test_referenced_dot_claude_files_exist` actually resolves."""
    evaluated: list[str] = []
    for path in (*_scanned_files(), *_skill_files()):
        for lineno, token in _references(path):
            if not _DOT_CLAUDE_FILE_REF.match(token):
                continue
            if _AGENT_REF.match(token) or _COMMAND_REF.match(token):
                continue
            if any(token.startswith(prefix) for prefix in _EXEMPT_STATE_PREFIXES):
                continue
            evaluated.append(f"{path.relative_to(REPO_ROOT)}:{lineno} -> {token}")
    return evaluated


def test_dot_claude_file_check_is_currently_a_pin() -> None:
    """`test_referenced_dot_claude_files_exist` resolves nothing at present.

    The module docstring describes that check as a regression pin rather than
    live coverage. That is a claim about the tree, not about the code, so it
    would otherwise go stale in silence. This is not a defect to fix: gaining a
    live token is fine and expected. It means the docstring's characterisation
    is out of date and should be corrected in the same change.
    """
    evaluated = _dot_claude_tokens_evaluated()
    assert not evaluated, (
        "the .claude file check now resolves live references — this is not a "
        "failure of the tree but of the module docstring, which calls the "
        "check a pin that evaluates zero tokens. Update it:\n"
        + "\n".join(evaluated)
    )
