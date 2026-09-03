"""Isolated Mini-only proof for the pinned Icom lower-executor slice.

Reuses the structural JUnit oracle from the rigctld/Icom diagnostic lanes.
Also loaded as a pytest plugin to record actual CALL exception checkpoints.
This is selected mock evidence, not whole-setter, ACK, or physical-radio proof.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

import pytest


PIN = "4e4c1782bf90ba5517d5d193c0344a23c4c8ff66"
SOURCE_BASE = "786a6e0d663d37f9a46c5948562d921a265ae6ea"
SOURCE_TREE = "2c6c74005327bb818907ce00d7ccae8cc84124ce"
CFILE = "tests/test_commander.py"
RFILE = "tests/test_radio.py"
C = CFILE + "::"
R = RFILE + "::TestCapturedExecuteLifetime::"
COMMANDER = "src/rigplane/commands/commander.py"
RUNTIME = "src/rigplane/runtime/_civ_rx.py"
OWNED = (COMMANDER, RUNTIME, CFILE, RFILE)
BLOBS = {
    COMMANDER: "0b8c3f8de482d7438dbe428defac8bc9b07fd4c6",
    RUNTIME: "5e3822cc2d0a53d7e066afabf6802dd54f0bbd1d",
    CFILE: "fdaa860c1704c7207d5a6b8178157a338e2125a3",
    RFILE: "9a658283603e3ced73868b6e828fc7c74bc1ab63",
}

ROWS = [
    (C + "test_stop_joins_cancel_resistant_execute_before_worker_exit", ["returns", "raises"]),
    (C + "test_overlapping_stop_keeps_actual_worker_until_execute_unwinds", ["concurrent", "cancel-waiter"]),
    (C + "test_old_stop_preserves_restart_from_worker_done_callback", [None]),
    (C + "test_send_refuses_stopping_or_dead_worker", ["stopping", "dead"]),
    (R + "test_stale_execute_refuses_before_formatter_and_preserves_tracker", [
        f"{change}-{pause}"
        for change in ("transport", "epoch", "transport-and-tracker")
        for pause in ("pacing-ff", "pacing-blocking", "ack-grace")
    ]),
    (R + "test_retired_execute_does_not_account_against_replacement", ["send-return", "response-timeout"]),
    (R + "test_retired_ack_grace_preserves_same_tracker_current_sink", [None]),
    (R + "test_generation_retirement_retrieves_detached_blocking_exception", ["pacing", "send"]),
    (R + "test_cancelled_fire_and_forget_cleans_captured_ack_sink", ["pacing", "send"]),
]
CASES = [node if suffix is None else f"{node}[{suffix}]" for node, suffixes in ROWS for suffix in suffixes]
CONTROLS = [
    C + "test_priority_ordering",
    C + "test_wait_dispatch_true_still_awaits_result",
    C + "test_dedupe_returns_existing_future",
    RFILE + "::TestResponseDeadlineOpensAtSend::test_pacing_gap_is_not_charged_to_the_answer_window",
    C + "test_stop_accepts_terminal_execute_without_release",
]

# Each edit is (file, exact old text, exact replacement), checked before pytest.
MUTANTS = [
    ("M1-worker-exits", [
        (COMMANDER,
         "                    if worker.cancelling():\n                        raise asyncio.CancelledError\n                    self._last_send = asyncio.get_running_loop().time()\n",
         "                    self._last_send = asyncio.get_running_loop().time()\n"),
        (COMMANDER,
         "                    _fail_item(item, exc)\n                    if worker.cancelling():\n                        raise asyncio.CancelledError\n",
         "                    _fail_item(item, exc)\n"),
    ], {C + f"test_stop_joins_cancel_resistant_execute_before_worker_exit[{suffix}]":
        (97, "cancel-resistant execute left stopping worker parked") for suffix in ("returns", "raises")}),
    ("M2-unshielded-join", [
        (COMMANDER, "                await asyncio.shield(worker)\n", "                await worker\n"),
    ], {C + "test_overlapping_stop_keeps_actual_worker_until_execute_unwinds[cancel-waiter]":
        (139, "cancelled stop waiter added a worker cancellation request")}),
    ("M3-restart-identity", [
        (COMMANDER,
         "            if self._worker is worker and (worker is None or worker.done()):\n",
         "            if worker is None or worker.done():\n"),
    ], {C + "test_old_stop_preserves_restart_from_worker_done_callback":
        (231, "old stop erased restarted worker")}),
    ("M4-grace-check", [
        (RUNTIME,
         "            await asyncio.sleep(0.005)\n            if check_current is not None:\n                check_current()\n",
         "            await asyncio.sleep(0.005)\n"),
    ], {R + "test_retired_ack_grace_preserves_same_tracker_current_sink":
        (1594, "retired ACK grace dropped current-generation sink")}),
    ("M5-live-tracker", [
        (RUNTIME, "                tracker.unregister(pending)\n",
         "                self._host._civ_request_tracker.unregister(pending)\n"),
    ], {R + "test_stale_execute_refuses_before_formatter_and_preserves_tracker[transport-and-tracker-pacing-blocking]":
        (1425, "stale execute leaked its waiter/sink")}),
    ("M6-cancel-rollback", [
        (RUNTIME, "            except (Exception, asyncio.CancelledError) as exc:\n",
         "            except Exception as exc:\n"),
    ], {R + f"test_cancelled_fire_and_forget_cleans_captured_ack_sink[{suffix}]":
        (1733, "cancelled fire-and-forget leaked ACK sink") for suffix in ("pacing", "send")}),
    ("M7-pending-cleanup", [
        (RUNTIME,
         "                if not pending.done():\n                    pending.cancel()\n                elif not pending.cancelled():\n                    pending.exception()\n", ""),
    ], {R + f"test_generation_retirement_retrieves_detached_blocking_exception[{suffix}]":
        (1667, "retired blocking waiter exception was not retrieved") for suffix in ("pacing", "send")}),
    ("M8-early-join-success", [
        (COMMANDER, "                await asyncio.shield(worker)\n", "                await asyncio.sleep(0)\n"),
    ], {C + f"test_stop_joins_cancel_resistant_execute_before_worker_exit[{suffix}]":
        (42, "successful stop did not join captured worker and execute") for suffix in ("returns", "raises")}),
]
EQUIVALENT = [(RUNTIME, "                or self._host._civ_epoch != epoch\n",
               "                or not (self._host._civ_epoch == epoch)\n")]


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def command(args, cwd):
    return subprocess.check_output(args, cwd=cwd, stderr=subprocess.STDOUT, timeout=60)


def snapshot(root):
    return {file: sha((root / file).read_bytes()) for file in OWNED}


def status(root):
    return command(["git", "status", "--porcelain=v1", "--untracked-files=all"], root)


def process_group_exists(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


def run_pytest(args, root, env, stdout, stderr, evidence, state):
    state["quiescent"] = False
    process = subprocess.Popen(args, cwd=root, env=env, stdout=stdout, stderr=stderr,
                               start_new_session=True)
    state["pid"] = state["pgid"] = process.pid
    try:
        returncode = process.wait(timeout=180)
    finally:
        group_alive = process_group_exists(process.pid)
        state["group_alive_after_wait"] = group_alive
        if group_alive:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=10)
        deadline = time.monotonic() + 5
        while process_group_exists(process.pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        state["returncode"] = process.returncode
        state["quiescent"] = process.poll() is not None and not process_group_exists(process.pid)
        write_json(evidence / "process-quiescence.json", state)
        if not state["quiescent"]:
            raise RuntimeError("test process group not confirmed stopped; restoration forbidden")
    if group_alive:
        raise RuntimeError("pytest left live process descendants")
    return returncode


def junit_key(node):
    file, *names = node.split("::")
    return (".".join([file.removesuffix(".py").replace("/", "."), *names[:-1]]), names[-1])


def pytest_sessionstart(session):
    """Require both production modules to load from this invocation's scratch."""
    root = Path(os.environ["PROOF_SOURCE"]).resolve()
    spec = json.loads(Path(os.environ["PROOF_EXPECTED"]).read_text())
    provenance = {}
    for module_name, file in (
        ("rigplane.commands.commander", COMMANDER),
        ("rigplane.runtime._civ_rx", RUNTIME),
    ):
        module = importlib.import_module(module_name)
        actual = Path(module.__file__).resolve()
        assert actual == root / file, (module_name, actual, root)
        assert sha(actual.read_bytes()) == spec["hashes"][file], file
        provenance[module_name] = str(actual)
    write_json(Path(os.environ["PROOF_EVIDENCE"]) / "imports.json", provenance)


def pytest_collection_finish(session):
    spec = json.loads(Path(os.environ["PROOF_EXPECTED"]).read_text())
    actual = [item.nodeid for item in session.items]
    assert len(actual) == len(set(actual)) == len(spec["cases"]), actual
    assert set(actual) == set(spec["cases"]), (actual, spec["cases"])
    root = Path(os.environ["PROOF_SOURCE"]).resolve()
    for item in session.items:
        file = item.nodeid.split("::")[0]
        assert Path(item.path).resolve() == root / file
        assert Path(item.module.__file__).resolve() == root / file
        assert sha((root / file).read_bytes()) == spec["hashes"][file]
    write_json(Path(os.environ["PROOF_EVIDENCE"]) / "collected.json", actual)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    row = {"node": item.nodeid, "phase": report.when, "outcome": report.outcome,
           "xfail": getattr(report, "wasxfail", None)}
    if call.excinfo is not None:
        row["exception"] = f"{call.excinfo.type.__module__}.{call.excinfo.type.__name__}"
        row["message"] = str(call.excinfo.value)
        row["traceback"] = [{"path": str(Path(entry.path).resolve()), "line": entry.lineno + 1}
                            for entry in call.excinfo.traceback]
    with (Path(os.environ["PROOF_EVIDENCE"]) / "phases.jsonl").open("a") as output:
        output.write(json.dumps(row) + "\n")


def validate(evidence, root, expected, rc):
    failures_expected = sum(value is not None for value in expected.values())
    assert rc == (1 if failures_expected else 0), ("unexpected pytest exit", rc)
    report = ET.parse(evidence / "junit.xml").getroot()
    cases = report.findall(".//testcase")
    keys = [(case.get("classname"), case.get("name")) for case in cases]
    by_key = {junit_key(node): node for node in expected}
    assert len(keys) == len(set(keys)) == len(by_key) == len(expected), keys
    assert set(keys) == set(by_key), {"actual": keys, "expected": list(by_key)}
    assert not report.findall(".//error"), "JUnit setup/teardown/collection error"
    assert not report.findall(".//skipped"), "JUnit skip/xfail"
    for case in cases:
        node = by_key[(case.get("classname"), case.get("name"))]
        checkpoint = expected[node]
        failures = case.findall("failure")
        if checkpoint is None:
            assert not failures, (node, "unexpected JUnit failure")
        else:
            assert len(failures) == 1, (node, "expected one assertion")
            assert failures[0].get("message", "").startswith("AssertionError: " + checkpoint[1]), (node, failures[0].attrib)
    phases = [json.loads(line) for line in (evidence / "phases.jsonl").read_text().splitlines()]
    actual_phases = {(row["node"], row["phase"]): row for row in phases}
    wanted_phases = {(node, phase) for node in expected for phase in ("setup", "call", "teardown")}
    assert len(actual_phases) == len(phases) == len(wanted_phases)
    assert set(actual_phases) == wanted_phases
    for (node, phase), row in actual_phases.items():
        assert row["xfail"] is None, (node, "xfail/xpass")
        checkpoint = expected[node] if phase == "call" else None
        if checkpoint is None:
            assert row["outcome"] == "passed" and "exception" not in row, row
        else:
            line, message = checkpoint
            assert row["outcome"] == "failed", row
            assert row["exception"] == "builtins.AssertionError", row
            assert row["message"].splitlines()[0] == message, row
            assert row["traceback"][-1] == {"path": str(root / node.split("::")[0]), "line": line}, row
    return {"cases": len(cases), "passed": len(cases) - failures_expected,
            "expected_call_failures": failures_expected}


def main():
    seed = Path.cwd().resolve()
    artifacts = Path(os.environ["ARTIFACT_DIR"]).resolve()
    variants = Path(os.environ["PROOF_VARIANTS"]).resolve()
    assert os.environ["SOURCE_PIN"] == PIN
    assert os.environ["SOURCE_BASE"] == SOURCE_BASE
    assert os.environ["SOURCE_TREE"] == SOURCE_TREE
    assert sys.version_info[:3] == (3, 11, 16), sys.version
    assert sys.implementation.name == "cpython" and sys.platform == "linux"
    assert os.environ["GITHUB_REF"] == "refs/heads/codex/icom-lower-executor-proof"
    assert os.environ["GITHUB_EVENT_NAME"] == "workflow_dispatch"
    command(["git", "merge-base", "--is-ancestor", SOURCE_BASE, PIN], seed)
    for file, blob in BLOBS.items():
        assert command(["git", "rev-parse", f"{PIN}:{file}"], seed).decode().strip() == blob, file
    originals = {file: command(["git", "show", f"{PIN}:{file}"], seed) for file in OWNED}
    hashes = {file: sha(data) for file, data in originals.items()}
    assert command(["git", "rev-parse", "HEAD"], seed).decode().strip() == PIN
    source_tree = command(["git", "rev-parse", "HEAD^{tree}"], seed)
    assert source_tree.decode().strip() == SOURCE_TREE
    assert snapshot(seed) == hashes and not status(seed)
    assert len(CASES) == len(set(CASES)) == 23
    assert len(CONTROLS) == len(set(CONTROLS)) == 5
    assert not set(CASES) & set(CONTROLS)
    assert len(MUTANTS) == len({label for label, _, _ in MUTANTS}) == 8
    full = {node: None for node in [*CASES, *CONTROLS]}
    plan = [("baseline", [], full)]
    for label, edits, failures in MUTANTS:
        assert set(failures) <= set(CASES)
        plan.append((label, edits, {**dict.fromkeys(CONTROLS), **failures}))
    plan += [("equivalent", EQUIVALENT, full), ("restored", [], full)]
    assert len(plan) == 11 and sum(len(expected) for _, _, expected in plan) == 136
    assert sum(value is not None for _, _, expected in plan for value in expected.values()) == 12
    prepared = {}
    for label, edits, expected in plan:
        contents = dict(originals)
        for file, old, new in edits:
            assert file in (COMMANDER, RUNTIME) and old != new
            assert originals[file].decode().count(old) == 1, (label, "anchor not unique", old)
            assert contents[file].decode().count(old) == 1, (label, "overlapping anchors")
            contents[file] = contents[file].decode().replace(old, new, 1).encode()
        for file in (COMMANDER, RUNTIME):
            compile(contents[file], file, "exec")
        for node, checkpoint in expected.items():
            if checkpoint is not None:
                line, message = checkpoint
                file = node.split("::")[0]
                lines = originals[file].decode().splitlines()
                assert "assert " in lines[line - 1], (label, node, line)
                assert message in "\n".join(lines[line - 1:line + 3]), (label, node, message)
        prepared[label] = contents
    write_json(artifacts / "preflight.json", {"source": PIN, "source_base": SOURCE_BASE,
        "source_tree": SOURCE_TREE, "blobs": BLOBS, "hashes": hashes,
        "plan": plan, "anchors": sum(len(edits) for _, edits, _ in plan),
        "invocations": 11, "executions": 136, "expected_pass": 124, "expected_call_failures": 12})
    write_json(artifacts / "environment.json", {"python": sys.version, "implementation": sys.implementation.name, "executable": sys.executable,
        "pytest": pytest.__version__, "script": str(Path(__file__).resolve()),
        "script_sha256": sha(Path(__file__).read_bytes()),
        **{key: os.environ.get(key) for key in ("GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "RUNNER_NAME", "GITHUB_REF")}})
    records = []
    try:
        for label, edits, expected in plan:
            evidence = artifacts / label
            evidence.mkdir()
            root = variants / label
            assert snapshot(seed) == hashes and not status(seed)
            command(["git", "clone", "--shared", "--no-checkout", str(seed), str(root)], variants)
            command(["git", "-c", "core.hooksPath=/dev/null", "checkout", "--detach", PIN], root)
            contents = prepared[label]
            process_state = {"quiescent": True, "pid": None, "pgid": None}
            try:
                (evidence / "source-head.txt").write_bytes(command(["git", "rev-parse", "HEAD"], root))
                (evidence / "source-tree.txt").write_bytes(command(["git", "rev-parse", "HEAD^{tree}"], root))
                assert (evidence / "source-head.txt").read_text().strip() == PIN
                assert (evidence / "source-tree.txt").read_bytes() == source_tree
                write_json(evidence / "hashes-before.json", snapshot(root))
                assert snapshot(root) == hashes and not status(root)
                for file, data in contents.items():
                    if data != originals[file]:
                        (root / file).write_bytes(data)
                variant_hashes = {file: sha(data) for file, data in contents.items()}
                assert snapshot(root) == variant_hashes
                changed = command(["git", "diff", "--name-only", "HEAD"], root).decode().splitlines()
                assert set(changed) == {file for file, _, _ in edits}
                applied = command(["git", "diff", "HEAD"], root)
                (evidence / "applied.diff").write_bytes(applied)
                assert bool(applied) == bool(edits), label
                write_json(evidence / "expected.json", {"cases": expected, "hashes": variant_hashes})
                env = os.environ.copy()
                env.update({"PYTHONPATH": os.pathsep.join([str(root / "src"), str(root / "tests"), str(Path(__file__).resolve().parent)]),
                    "PYTEST_ADDOPTS": "", "PYTEST_PLUGINS": "", "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONPYCACHEPREFIX": tempfile.mkdtemp(prefix=f"{label}-bytecode-", dir=variants),
                    "PROOF_SOURCE": str(root), "PROOF_EVIDENCE": str(evidence),
                    "PROOF_EXPECTED": str(evidence / "expected.json")})
                pytest_cache = tempfile.mkdtemp(prefix=f"{label}-pytest-", dir=variants)
                args = [sys.executable, "-m", "pytest", "-q", "-o", "addopts=", "-o", f"cache_dir={pytest_cache}",
                        "-p", "icom_lower_executor_proof", "--tb=long", "--timeout=20", "--timeout-method=thread",
                        f"--junitxml={evidence / 'junit.xml'}", *expected]
                write_json(evidence / "invocation.json", {"command": args, "cwd": str(root),
                    "pycache": env["PYTHONPYCACHEPREFIX"], "pytest_cache": pytest_cache, "pythonpath": env["PYTHONPATH"]})
                with (evidence / "stdout.log").open("w") as stdout, (evidence / "stderr.log").open("w") as stderr:
                    try:
                        returncode = run_pytest(args, root, env, stdout, stderr, evidence, process_state)
                    except subprocess.TimeoutExpired:
                        (evidence / "exit.txt").write_text("INVALID: outer timeout\n")
                        raise
                (evidence / "exit.txt").write_text(str(returncode) + "\n")
                write_json(evidence / "hashes-after-test.json", snapshot(root))
                assert snapshot(root) == variant_hashes, "test changed owned source"
                record = {"invocation": label, **validate(evidence, root, expected, returncode)}
            except BaseException as error:
                write_json(evidence / "verdict.json", {"status": "INVALID", "exception": type(error).__name__, "detail": str(error)})
                raise
            finally:
                if not process_state["quiescent"]:
                    write_json(evidence / "verdict.json", {"status": "INVALID", "stage": "process-cleanup",
                        "restoration": "skipped", "process": process_state})
                    raise RuntimeError("live test processes not excluded; source restoration skipped")
                try:
                    try:
                        actual = snapshot(root)
                        matches_base = actual == hashes
                        write_json(evidence / "base-comparison-before-restore.json", {
                            "actual": actual, "base": hashes, "matches_base": matches_base,
                            "expected_matches_base": not bool(edits),
                        })
                        assert matches_base is (not bool(edits)), "base comparison negative control failed"
                        (evidence / "before-restore.diff").write_bytes(command(["git", "diff", "HEAD"], root))
                        (evidence / "before-restore.status").write_bytes(status(root))
                    finally:
                        # Never write test files, including during restoration.
                        for file in (COMMANDER, RUNTIME):
                            (root / file).write_bytes(originals[file])
                        actual = snapshot(root)
                        matches_base = actual == hashes
                        write_json(evidence / "hashes-restored.json", actual)
                        write_json(evidence / "base-comparison-restored.json", {
                            "actual": actual, "base": hashes, "matches_base": matches_base,
                            "expected_matches_base": True,
                        })
                        (evidence / "restored.diff").write_bytes(command(["git", "diff", "HEAD"], root))
                        (evidence / "restored.status").write_bytes(status(root))
                        assert matches_base and not status(root), "restoration failed"
                        command(["git", "diff", "--exit-code", "HEAD"], root)
                except BaseException as error:
                    write_json(evidence / "verdict.json", {"status": "INVALID", "stage": "restoration",
                        "exception": type(error).__name__, "detail": str(error)})
                    raise
            records.append(record)
            write_json(evidence / "verdict.json", {"status": "PASS", **record})
            print(json.dumps(record), flush=True)
        assert len(records) == 11
        assert sum(row["cases"] for row in records) == 136
        assert sum(row["passed"] for row in records) == 124
        assert sum(row["expected_call_failures"] for row in records) == 12
        write_json(artifacts / "verdict.json", {"status": "PASS", "records": records})
    finally:
        write_json(artifacts / "completed-invocations.json", records)
        assert snapshot(seed) == hashes and not status(seed), "seed source changed"


if __name__ == "__main__":
    def interrupt(signum, _frame):
        raise KeyboardInterrupt(f"diagnostic interrupted by signal {signum}")

    signal.signal(signal.SIGTERM, interrupt)
    try:
        main()
    except BaseException as error:
        write_json(Path(os.environ["ARTIFACT_DIR"]) / "verdict.json",
                   {"status": "INVALID", "exception": type(error).__name__, "detail": str(error)})
        raise
