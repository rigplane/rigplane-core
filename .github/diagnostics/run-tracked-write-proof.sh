#!/usr/bin/env bash
set -euo pipefail

base_sha=f6f78f4510656ec216a285404acc66f0abf59ed8
source_sha=bb5f4719055d34ee039ed2954b08ac92c32b08d5
artifact_dir=${ARTIFACT_DIR:?}
base_dir="$(mktemp -d "$RUNNER_TEMP/icom-base-XXXXXX")"
source_files=(src/rigplane/core/transport.py tests/test_transport.py)
export SOURCE_PIN="$source_sha"
test "$(git rev-parse HEAD)" = "$source_sha"
test -z "$(git status --porcelain=v1 --untracked-files=all)"
printf 'base_sha=%s\nsource_sha=%s\n' "$base_sha" "$source_sha" \
  | tee "$artifact_dir/revisions.txt"
git diff --exit-code "$source_sha" -- "${source_files[@]}" \
  > "$artifact_dir/source-diff-before.txt"
git worktree add --detach "$base_dir" "$base_sha"
trap 'git worktree remove "$base_dir"' EXIT

run_extra_mypy() {
  local label=$1 directory=$2 result
  set +e
  (cd "$directory" && uv sync --frozen --all-extras && \
    uv run --no-sync mypy src/rigplane/core/transport.py) \
    > "$artifact_dir/$label-mypy.log" 2>&1
  result=$?
  set -e
  printf '%s\n' "$result" > "$artifact_dir/$label-mypy.rc"
  cat "$artifact_dir/$label-mypy.log"
}

run_extra_mypy base "$base_dir"
base_result=$(cat "$artifact_dir/base-mypy.rc")
run_extra_mypy source .
source_result=$(cat "$artifact_dir/source-mypy.rc")
test "$base_result" -eq "$source_result"
test "$(grep -c '\[no-any-return\]' "$artifact_dir/base-mypy.log")" -eq \
  "$(grep -c '\[no-any-return\]' "$artifact_dir/source-mypy.log")"
uv run --no-sync ruff check src/ tests/ | tee "$artifact_dir/ruff.log"
uv run --no-sync ruff format --check src/ tests/ | tee "$artifact_dir/format.log"
uv run --no-sync lint-imports | tee "$artifact_dir/imports.log"
uv run --no-sync mypy --strict src/rigplane/web | tee "$artifact_dir/web-mypy.log"

# Reuses the exact-identity/per-test JUnit pattern from the rigctld proof.
uv run --no-sync python - <<'PY'
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

artifacts = Path(os.environ["ARTIFACT_DIR"])
pin = os.environ["SOURCE_PIN"]
source = "src/rigplane/core/transport.py"
test_file = "tests/test_transport.py"
suite = test_file + "::TestTrackedWriteGuard"
class_name = "tests.test_transport.TestTrackedWriteGuard"
assert sys.version_info[:2] == (3, 13), sys.version


def git(*args):
    return subprocess.check_output(["git", *args])


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def status():
    return git("status", "--porcelain=v1", "--untracked-files=all").decode()


assert git("rev-parse", "HEAD").decode().strip() == pin
assert not status(), "source checkout is not clean"
originals = {file: git("show", f"{pin}:{file}") for file in (source, test_file)}
original_hashes = {file: hashlib.sha256(data).hexdigest() for file, data in originals.items()}
for file, data in originals.items():
    assert Path(file).read_bytes() == data, file
    saved = artifacts / "originals" / file
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_bytes(data)
write_json(artifacts / "original-sha256.json", original_hashes)
write_json(artifacts / "metadata.json", {
    "source_pin": pin, "diagnostic_head": os.environ["GITHUB_SHA"],
    "run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
    "python": sys.version, "runner": os.environ["RUNNER_NAME"],
    "planned_invocations": 12, "planned_case_executions": 93,
    "claim": "Local submission/retransmission fake proof; not managed cutover or RF proof",
})

names = [
    "test_initial_suppression_has_no_send_side_effects[False]",
    "test_initial_suppression_has_no_send_side_effects[True]",
    "test_current_send_and_replay_do_not_yield_before_raw_send",
    "test_stale_on_replay_after_current_off_is_suppressed[False]",
    "test_stale_on_replay_after_current_off_is_suppressed[True]",
    "test_multi_replay_rechecks_each_entry",
    "test_replay_guard_exception_is_suppressed[False]",
    "test_replay_guard_exception_is_suppressed[True]",
    "test_guard_metadata_follows_fifo_eviction",
    "test_guard_metadata_clears_on_rollover",
    "test_duplicate_sequence_replaces_guard_and_keeps_bytes_compatible",
]
assert len(names) == len(set(names)) == 11
green = {name: None for name in names}
initial_guard = (
    "        if is_current is not None and not is_current():\n"
    '            raise CommandError("Tracked write is no longer current")\n'
)
mutants = [
    ("initial", initial_guard, "", {
        names[0]: ("Failed: DID NOT RAISE <class 'rigplane.core.exceptions.CommandError'>",
                   "with pytest.raises(expected) as caught:"),
        names[1]: ("Failed: DID NOT RAISE <class 'RuntimeError'>",
                   "with pytest.raises(expected) as caught:"),
    }),
    ("single", "            self._retransmit(seq)\n",
     "            if seq in self.tx_buffer:\n                self._raw_send(self.tx_buffer[seq])\n", {
         names[3]: ("assert [b'ON', b'OFF', b'ON'] == [b'ON', b'OFF']",
                    "assert [packet[CONTROL_SIZE:] for packet in sent] == ("),
     }),
    ("multi", "                self._retransmit(rseq)\n",
     "                if rseq in self.tx_buffer:\n                    self._raw_send(self.tx_buffer[rseq])\n", {
         names[4]: ("assert [b'ON', b'OFF', b'ON', b'OFF'] == [b'ON', b'OFF', b'OFF']",
                    "assert [packet[CONTROL_SIZE:] for packet in sent] == ("),
     }),
    ("eviction", "            self._tx_guards.pop(evicted, None)\n", "", {
        names[8]: ("assert ", "assert set(transport._tx_guards) == set(transport.tx_buffer)"),
    }),
]
equivalent = ("positive-equivalent", initial_guard,
              '        if not (is_current is None or is_current()):\n'
              '            raise CommandError("Tracked write is no longer current")\n', green)
preflight = []
for label, old, new, expected in [*mutants, equivalent]:
    text = originals[source].decode()
    assert old != new and text.count(old) == 1, (label, "anchor is not unique")
    assert set(expected) <= set(green), label
    replacement = text.replace(old, new, 1)
    assert replacement != text, label
    compile(replacement, source, "exec")
    preflight.append({"label": label, "anchor_count": 1,
                      "mutated_sha256": hashlib.sha256(replacement.encode()).hexdigest()})
write_json(artifacts / "preflight.json", preflight)
assert len(preflight) == 5
records = []


def snapshot(directory, label):
    hashes = {file: hashlib.sha256(Path(file).read_bytes()).hexdigest() for file in originals}
    write_json(directory / f"{label}-sha256.json", hashes)
    (directory / f"{label}-status.txt").write_text(status())
    (directory / f"{label}.diff").write_bytes(git("diff", "--", *originals))
    return hashes


def restore(directory):
    for file, data in originals.items():
        Path(file).write_bytes(data)
    assert snapshot(directory, "restored") == original_hashes
    assert not status(), "restoration left a dirty source checkout"


def run_cases(label, patch, expected):
    evidence = artifacts / label
    evidence.mkdir()
    restore(evidence)
    write_json(evidence / "expected-cases.json", [
        {"classname": class_name, "name": name, "signature": signature}
        for name, signature in expected.items()
    ])
    failures_expected = sum(signature is not None for signature in expected.values())
    assert (len(expected), failures_expected) in {(11, 0), (2, 2), (1, 1)}
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPYCACHEPREFIX"] = tempfile.mkdtemp(prefix=f"icom-{label}-", dir=env["RUNNER_TEMP"])
    (evidence / "pycache.txt").write_text(env["PYTHONPYCACHEPREFIX"] + "\n")
    try:
        if patch is not None:
            old, new = patch
            Path(source).write_text(originals[source].decode().replace(old, new, 1))
        changed = git("diff", "--name-only").decode().splitlines()
        assert changed == ([] if patch is None else [source]), changed
        applied_hashes = snapshot(evidence, "applied")
        nodes = [suite] if len(expected) == 11 else [suite + "::" + name for name in expected]
        command = [sys.executable, "-m", "pytest", *nodes, "-q", "-o", "addopts=",
                   "--tb=short", "--color=no", "--timeout=15", "--timeout-method=thread",
                   f"--junitxml={evidence / 'junit.xml'}"]
        write_json(evidence / "command.json", command)
        with (evidence / "pytest.log").open("w") as log:
            try:
                result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=90)
            except subprocess.TimeoutExpired:
                (evidence / "pytest-exit.txt").write_text("TIMEOUT\n")
                raise
        (evidence / "pytest-exit.txt").write_text(str(result.returncode) + "\n")
        print((evidence / "pytest.log").read_text(), end="")
        assert snapshot(evidence, "after-pytest") == applied_hashes, "pytest changed source bytes"
        assert result.returncode == (1 if failures_expected else 0), (label, result.returncode)
        report = ET.parse(evidence / "junit.xml").getroot()
        cases = report.findall(".//testcase")
        actual = [(case.get("classname"), case.get("name")) for case in cases]
        identities = {(class_name, name) for name in expected}
        assert len(actual) == len(set(actual)) == len(identities), (label, actual)
        assert set(actual) == identities, (label, set(actual), identities)
        assert not report.findall(".//error"), (label, "setup/teardown error")
        assert not report.findall(".//skipped"), (label, "skipped case")
        assert len(report.findall(".//failure")) == failures_expected, label
        summaries = report.findall(".//testsuite")
        assert len(summaries) == 1, (label, "unexpected suite structure")
        summary = summaries[0]
        assert tuple(int(summary.get(key, "-1")) for key in ("tests", "failures", "errors", "skipped")) == (
            len(expected), failures_expected, 0, 0
        ), (label, summary.attrib)
        for case in cases:
            name = case.get("name")
            signature = expected[name]
            failures = case.findall("failure")
            if signature is None:
                assert not failures, (label, name, "green control failed")
                continue
            assert len(failures) == 1, (label, name, "expected one CALL failure")
            message = failures[0].get("message", "")
            body = failures[0].text or ""
            prefix, statement = signature
            # pytest emits plain assertion messages for some asserts and
            # AssertionError-prefixed messages for others; neither needs a type attribute.
            normalized = message.removeprefix("AssertionError: ")
            assert normalized.startswith(prefix), (label, name, message)
            assert statement in body, (label, name, body)
            if label == "eviction":
                assert "Extra items in the left set:" in body and "\nE     1\n" in body, body
        record = {"invocation": label, "cases": len(cases), "expected_failures": failures_expected}
        restore(evidence)
        records.append(record)
        write_json(evidence / "verdict.json", {"status": "PASS", **record})
    except BaseException as error:
        write_json(evidence / "verdict.json", {
            "status": "FAIL", "error": type(error).__name__, "detail": str(error),
        })
        raise
    finally:
        snapshot(evidence, "before-restoration")
        restore(evidence)


try:
    run_cases("control-before", None, green)
    for name, old, new, expected in mutants:
        run_cases(name, (old, new), expected)
        run_cases(name + "-restored-control", None, green)
    _, old, new, expected = equivalent
    run_cases("positive-equivalent-control", (old, new), expected)
    run_cases("positive-equivalent-restored-control", None, green)
    run_cases("control-after", None, green)
    assert len(records) == 12 and sum(row["cases"] for row in records) == 93
    assert sum(row["expected_failures"] for row in records) == 5
    (artifacts / "oracle.txt").write_text("PASS: 12 invocations, 93 cases, 88 PASS and 5 expected CALL failures; zero errors/skips.\n")
finally:
    restore(artifacts)
    write_json(artifacts / "completed-invocations.json", records)
PY
