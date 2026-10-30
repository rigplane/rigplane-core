"""CI contract: no workflow job may run on a GitHub-hosted runner (MOR-3875).

Owner rule (2026-10-28): no job on ubuntu/macos/windows-latest, or any other
GitHub-hosted label, in any repository. Every job runs on a self-hosted runner
so no hosted minutes are ever spent and no job queues behind a hosted pool.

For every file under `.github/workflows/` this checks that:

* every mention of `runs-on` is a plain `runs-on:` key line (a quoted key or a
  flow mapping such as `{runs-on: ...}` cannot be verified, so it fails);
* the value never names a GitHub-hosted label (`ubuntu-latest`, `ubuntu-24.04`,
  `macos-14`, `windows-2022`, `ubuntu-24.04-arm`, ...) and is never a runner
  group or an object;
* a bare scalar is only `self-hosted`, and a label list contains `self-hosted`
  unless every label is in `CUSTOM_ONLY_LABELS`;
* an expression is only a `matrix` reference (`matrix.key` or `matrix['key']`),
  and every value written for that key -- in the owning job, else anywhere in
  the file -- passes the same self-hosted rule. `fromJSON(format('["self-hosted", ...',
  ...))` is accepted. Any other expression, or a matrix key with no literal
  value in the file, fails because it cannot be proven.

The adversary is a careless edit that copies `ubuntu-latest` from a GitHub
example, in any of its ordinary spellings. It is a text check: it needs no YAML
package, ignores comments and, for `runs-on`, block scalars (`run: |`), and does not
look inside reusable workflows that live in another repository (those are
checked in that repository). A matrix produced at run time from an external
input cannot be seen; the literal text of the key is all that can be checked.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

# Custom labels that are valid WITHOUT the `self-hosted` label because the
# registered runner carries only these. Keep empty unless a runner needs it.
CUSTOM_ONLY_LABELS: frozenset[str] = frozenset(
    {
        # fleet-native-focused.yml selects a runner by its single fleet label.
        "fleet-gtr7-x64",
        "fleet-mac-home-arm64",
        "fleet-mac-server-arm64",
        "fleet-nsu-x64",
    }
)

_HOSTED = re.compile(
    r"(?<![\w.-])(?:ubuntu|macos|windows)-(?:latest|slim|\d)[\w.-]*", re.IGNORECASE
)
_KEY_LINE = re.compile(r"^(?P<indent> *)runs-on:[ \t]*(?P<rest>.*)$")
_BLOCK_SCALAR = re.compile(r"(?:^|\s)(?:-\s+)?(?:[\w'\"-]+):\s+[|>][+-]?\d?\s*$")
_MATRIX_ONLY = re.compile(
    r"""^\[?\s*['"]?\$\{\{\s*matrix
        (?:\.(?P<dot>[\w-]+)|\[\s*['"](?P<idx>[\w-]+)['"]\s*\])
        (?:\.[\w-]+|\[\s*['"][\w-]+['"]\s*\])*
        \s*\}\}['"]?\s*\]?$""",
    re.VERBOSE,
)


# `fromJSON(format('["self-hosted", ...]', ...))`: the template itself starts the
# label list with the literal self-hosted label, whatever the arguments add.
_SELF_HOSTED_FORMAT = re.compile(
    r"""^\$\{\{\s*fromJSON\(\s*format\(\s*'\[\s*"self-hosted"\s*,.*\)\)\s*\}\}$"""
)


def _strip_comment(line: str) -> str:
    quote = ""
    for pos, char in enumerate(line):
        if quote:
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char == "#" and (pos == 0 or line[pos - 1] in " \t"):
            return line[:pos].rstrip()
    return line.rstrip()


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _clean_lines(text: str) -> list[str]:
    """Comment-free lines with the bodies of block scalars blanked out."""
    lines = [_strip_comment(raw) for raw in text.splitlines()]
    skip_above = -1
    for idx, line in enumerate(lines):
        if skip_above >= 0:
            if not line.strip() or _indent(line) > skip_above:
                lines[idx] = ""
                continue
            skip_above = -1
        if _BLOCK_SCALAR.search(line):
            key_col = _indent(line)
            if line.lstrip().startswith("- "):
                key_col += 2
            skip_above = key_col
    return lines


def _balanced(lines: list[str], idx: int, col: int) -> str:
    """Text of the flow collection that opens at lines[idx][col]."""
    opener = lines[idx][col]
    closer = "]" if opener == "[" else "}"
    depth = 0
    quote = ""
    out: list[str] = []
    for row in range(idx, len(lines)):
        for char in lines[row][col if row == idx else 0 :]:
            out.append(char)
            if quote:
                if char == quote:
                    quote = ""
            elif char in "'\"":
                quote = char
            elif char in "[{":
                depth += 1
            elif char in "]}":
                depth -= 1
                if depth == 0 and char == closer:
                    return "".join(out)
        out.append(" ")
    return "".join(out)


def _block_after(lines: list[str], idx: int, key_indent: int) -> str:
    """Block mapping/sequence under lines[idx], indentless sequences included."""
    out: list[str] = []
    for line in lines[idx + 1 :]:
        if not line.strip():
            continue
        indent = _indent(line)
        if indent > key_indent or (
            indent == key_indent and line.lstrip().startswith("- ")
        ):
            out.append(line)
        else:
            break
    return " ".join(out)


def _value_at(lines: list[str], idx: int, col: int, key_indent: int) -> str:
    """The YAML value that starts at lines[idx][col:], possibly multi-line."""
    rest = lines[idx][col:].strip()
    if not rest:
        return _block_after(lines, idx, key_indent)
    if rest[0] in "[{":
        return _balanced(lines, idx, col + (len(lines[idx][col:]) - len(rest)))
    return rest


def _job_range(lines: list[str], idx: int) -> tuple[int, int]:
    """Line range of the job that owns lines[idx]; the whole file if unknown."""
    try:
        jobs = next(n for n, line in enumerate(lines) if line.rstrip() == "jobs:")
        job_indent = next(_indent(line) for line in lines[jobs + 1 :] if line.strip())
    except StopIteration:
        return 0, len(lines)
    start = jobs + 1
    for n in range(idx, jobs, -1):
        if lines[n].strip() and _indent(lines[n]) == job_indent:
            start = n
            break
    end = len(lines)
    for n in range(idx + 1, len(lines)):
        if lines[n].strip() and _indent(lines[n]) <= job_indent:
            end = n
            break
    return start, end


def _is_axis(lines: list[str], row: int, col: int) -> bool:
    """True when the key at lines[row][col] is a matrix axis, not an include entry.

    GitHub runs one job per value of an axis, so each value is its own runner
    selection; a value inside an `include` entry is the one selection as a whole.
    """
    line = lines[row]
    key_indent = _indent(line)
    if line.lstrip().startswith("- ") or col > key_indent:
        return False
    for above in range(row - 1, -1, -1):
        if lines[above].strip() and _indent(lines[above]) < key_indent:
            return lines[above].strip().rstrip(":").lower().endswith("matrix")
    return False


def _split_top_level(text: str) -> list[str]:
    """Split a flow sequence body on top-level commas (quotes and brackets kept)."""
    items: list[str] = []
    depth = 0
    quote = ""
    current: list[str] = []
    for char in text:
        if quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        elif char == "," and depth == 0:
            items.append("".join(current))
            current = []
            continue
        current.append(char)
    items.append("".join(current))
    return [item.strip() for item in items if item.strip()]


def _alternatives(fragment: str) -> list[str]:
    """The separate runner selections of a matrix axis value."""
    text = fragment.strip()
    if text.startswith("["):
        return _split_top_level(text[1:].rsplit("]", 1)[0])
    if re.match(r"^-\s", text):
        items = re.split(r"(?:^|\s)-\s+", text)
        return [item.strip() for item in items if item.strip()]
    return [text]


def _matrix_values(lines: list[str], idx: int, key: str) -> list[str]:
    """Every runner selection written for `key`, in the owning job else the file."""
    pattern = re.compile(
        r"""(?<![\w-])["']?""" + re.escape(key) + r"""["']?[ \t]*:""", re.IGNORECASE
    )
    start, end = _job_range(lines, idx)
    for lo, hi in ((start, end), (0, len(lines))):
        found: list[str] = []
        for row in range(lo, hi):
            for match in pattern.finditer(lines[row]):
                key_indent = _indent(lines[row])
                value = _value_at(lines, row, match.end(), key_indent)
                if _is_axis(lines, row, match.start()):
                    found.extend(_alternatives(value))
                else:
                    found.append(value)
        if found:
            return found
    return []


def _labels(value: str) -> list[str]:
    bare = re.sub(r"(^|\s)-\s+", " ", value.replace("'", " ").replace('"', " "))
    return [token for token in re.split(r"[\s,\[\]]+", bare) if token]


def _permitted(value: str) -> bool:
    """True for a label list that is self-hosted or only custom-only labels."""
    if _HOSTED.search(value):
        return False
    labels = _labels(value)
    if "self-hosted" in {label.lower() for label in labels}:
        return True
    return bool(labels) and set(labels) <= CUSTOM_ONLY_LABELS


def _check_value(raw: list[str], idx: int, value: str, where: str) -> list[str]:
    flat = " ".join(value.split())
    if _HOSTED.search(value):
        return [f"{where}: GitHub-hosted runner label in runs-on: {flat}"]
    if re.search(r"\bgroup['\"]?\s*:", value) or value.startswith("{"):
        return [f"{where}: runner group or object in runs-on: {flat}"]
    if "${{" in value:
        if _SELF_HOSTED_FORMAT.match(value.strip()):
            return []
        match = _MATRIX_ONLY.match(value.strip())
        if not match:
            return [f"{where}: runs-on expression cannot be verified: {flat}"]
        key = match.group("dot") or match.group("idx")
        values = _matrix_values(raw, idx, key)
        if not values:
            return [f"{where}: matrix.{key} has no literal value in this file"]
        return [
            f"{where}: matrix.{key} is not a self-hosted runner: {' '.join(v.split())}"
            for v in values
            if "${{" in v or not _permitted(v)
        ]
    if not _labels(value):
        return [f"{where}: empty runs-on"]
    if not _permitted(value):
        return [f"{where}: runs-on lacks the self-hosted label: {flat}"]
    return []


def find_violations(text: str, name: str = "<workflow>") -> list[str]:
    """Return one message per hosted-runner use found in workflow `text`."""
    lines = _clean_lines(text)
    # Matrix values may be built by a script step (a block scalar), so the
    # lookup reads the lines with block scalars intact.
    raw = [_strip_comment(line) for line in text.splitlines()]
    problems: list[str] = []
    for idx, line in enumerate(lines):
        if "runs-on" not in line:
            continue
        where = f"{name}:{idx + 1}"
        match = _KEY_LINE.match(line)
        if not match:
            problems.append(f"{where}: runs-on is not a plain key line: {line.strip()}")
            continue
        col = match.start("rest")
        value = _value_at(lines, idx, col, len(match.group("indent")))
        problems.extend(_check_value(raw, idx, value, where))
    return problems


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".github" / "workflows").is_dir():
            return parent
    raise RuntimeError("no .github/workflows directory above " + str(here))


def _job(snippet: str, *, extra: str = "") -> str:
    return "jobs:\n  j:\n    " + snippet + "\n" + extra


class SelfHostedOnlyContract(unittest.TestCase):
    def test_every_workflow_runs_on_self_hosted_runners(self) -> None:
        workflows = sorted(
            path
            for pattern in ("*.yml", "*.yaml")
            for path in (_repo_root() / ".github" / "workflows").glob(pattern)
        )
        self.assertTrue(workflows, "no workflow files found; the contract is vacuous")
        problems: list[str] = []
        for path in workflows:
            text = path.read_text(encoding="utf-8")
            problems.extend(find_violations(text, path.name))
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_detector_rejects_hosted_runners(self) -> None:
        matrix_os = "    strategy:\n      matrix:\n"
        bad = {
            "scalar": _job("runs-on: ubuntu-latest"),
            "quoted value": _job("runs-on: 'ubuntu-24.04'"),
            "flow list": _job("runs-on: [ubuntu-latest]"),
            "windows list": _job("runs-on: [windows-latest]"),
            "macos": _job("runs-on: macos-14"),
            "arm image": _job("runs-on: ubuntu-24.04-arm"),
            "block list": _job("runs-on:\n      - ubuntu-22.04"),
            "indentless list": _job("runs-on:\n    - ubuntu-22.04"),
            "group": _job("runs-on: { group: hosted-pool }"),
            "bare custom label": _job("runs-on: some-custom-label"),
            "custom label list": _job("runs-on: [some-custom-label]"),
            "quoted key": _job('"runs-on": ubuntu-latest'),
            "flow mapping job": "jobs:\n  j: {runs-on: ubuntu-latest, steps: []}\n",
            "other expression": _job("runs-on: ${{ inputs.runner }}"),
            "matrix axis mixing permitted and custom alternatives": _job(
                "runs-on: ${{ matrix.os }}",
                extra=matrix_os + "        os: [self-hosted, some-custom-label]\n",
            ),
            "matrix block axis mixing alternatives": _job(
                "runs-on: ${{ matrix.os }}",
                extra=matrix_os
                + "        os:\n          - self-hosted\n          - custom-label\n",
            ),
            "format expression indexed away from self-hosted": _job(
                "runs-on: ${{ fromJSON(format('[\"self-hosted\", \"{0}\"]', 'a'))[1] }}"
            ),
            "matrix alternative from an input": _job(
                "runs-on: ${{ matrix.os }}",
                extra=matrix_os + "        os: [self-hosted, '${{ inputs.runner }}']\n",
            ),
            "interpolated label": _job(
                "runs-on: ubuntu-${{ matrix.v }}",
                extra=matrix_os + "        v: ['22.04']\n",
            ),
            "quoted group key": _job('runs-on: {"group": pool, labels: [self-hosted]}'),
            "matrix custom label": _job(
                "runs-on: ${{ matrix.os }}",
                extra=matrix_os + "        os: [xcode-27]\n",
            ),
            "matrix mixed case": _job(
                "runs-on: ${{ matrix.OS }}",
                extra=matrix_os + "        os: [ubuntu-latest]\n",
            ),
            "format expression with a hosted label": _job(
                "runs-on: ${{ fromJSON(format('[\"ubuntu-latest\", {0}]', 'a')) }}"
            ),
            "undefined matrix key": _job("runs-on: ${{ matrix.os }}"),
            "matrix list": _job(
                "runs-on: ${{ matrix.os }}",
                extra=matrix_os + "        os: [ubuntu-latest]\n",
            ),
            "matrix bracket access": _job(
                "runs-on: ${{ matrix['os'] }}",
                extra=matrix_os + "        os: [ubuntu-latest]\n",
            ),
            "matrix quoted bracket list": _job(
                "runs-on: [\"${{ matrix['os'] }}\"]",
                extra=matrix_os + "        os: [ubuntu-latest]\n",
            ),
            "matrix multiline flow": _job(
                "runs-on: ${{ matrix.os }}",
                extra=matrix_os + "        os: [\n          ubuntu-latest,\n"
                "          windows-latest\n        ]\n",
            ),
            "matrix indentless block": _job(
                "runs-on: ${{ matrix.os }}",
                extra=matrix_os + "        os:\n        - ubuntu-latest\n",
            ),
            "matrix include": _job(
                "runs-on: ${{ matrix.os }}",
                extra=matrix_os + "        include:\n          - os: windows-2022\n",
            ),
            "matrix nested mapping": _job(
                "runs-on: ${{ matrix.runner.labels }}",
                extra=matrix_os
                + "        runner: {name: linux, labels: ubuntu-latest}\n",
            ),
        }
        for label, text in bad.items():
            with self.subTest(label):
                self.assertTrue(find_violations(text), text)

    def test_detector_accepts_self_hosted_runners(self) -> None:
        matrix = "    strategy:\n      matrix:\n"
        good = {
            "bare": _job("runs-on: self-hosted"),
            "flow list": _job("runs-on: [self-hosted, linux, build]"),
            "comment": _job("runs-on: [self-hosted, Linux]  # not ubuntu-latest"),
            "quoted hash": _job("runs-on: ['self-hosted', 'a #b']"),
            "block list": _job("runs-on:\n      - self-hosted\n      - linux"),
            "matrix include": _job(
                "runs-on: ${{ matrix.runner_label }}",
                extra=matrix
                + "        include:\n          - runner_label: [self-hosted, x64]\n",
            ),
            "matrix quoted selector": _job(
                "runs-on: ['${{ matrix.selector }}']",
                extra=matrix + "        include:\n          - selector: self-hosted\n",
            ),
            "matrix bracket access": _job(
                "runs-on: ${{ matrix['os'] }}",
                extra=matrix + "        os: [self-hosted]\n",
            ),
            "matrix axis of label arrays": _job(
                "runs-on: ${{ matrix.os }}",
                extra=matrix + "        os: [[self-hosted, a], [self-hosted, b]]\n",
            ),
            "include entry keeps its label array whole": _job(
                "runs-on: ${{ matrix.os }}",
                extra=matrix + "        include:\n          - name: a\n"
                "            os: [self-hosted, some-custom-label]\n",
            ),
            "unrelated env of the same name": (
                "env:\n  os: ubuntu-latest\n"
                + _job(
                    "runs-on: ${{ matrix.os }}",
                    extra=matrix + "        os: [self-hosted]\n",
                )
            ),
            "other job has the same key": (
                _job(
                    "runs-on: ${{ matrix.os }}",
                    extra=matrix + "        os: [self-hosted]\n",
                )
                + "  k:\n    runs-on: self-hosted\n    strategy:\n      matrix:\n"
                "        os: [ubuntu-latest]\n"
            ),
            "format expression with a literal self-hosted label": _job(
                "runs-on: ${{ fromJSON(format('[\"self-hosted\", {0}]', 'a')) }}"
            ),
            "matrix built by a script step": _job(
                "runs-on: ${{ matrix.runner_label }}",
                extra="    steps:\n      - run: |\n          echo 'x'\n"
                "          runner_label: ['self-hosted', 'x64']\n",
            ),
            "runner-looking text in a script": _job(
                "runs-on: self-hosted",
                extra="    steps:\n      - run: |\n          echo 'a'\n"
                "          runs-on: ubuntu-latest\n",
            ),
        }
        for label, text in good.items():
            with self.subTest(label):
                self.assertEqual(find_violations(text), [], text)


if __name__ == "__main__":
    unittest.main()
