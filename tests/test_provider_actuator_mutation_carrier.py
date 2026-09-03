"""Ephemeral Mini-only mutation carrier for the provider actuator candidate."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

import pytest


_CANDIDATE_SHA = "fab73c0d7389691c2b450b051890221002448736"
_CANDIDATE_TREE = "0805ba96647210f565a93c4f9516e42b3d498eeb"
_HARNESS_PATH = "tests/test_provider_actuator_mutation_carrier.py"
_PRODUCT_HASHES = {
    "src/rigplane/backends/rigctld_client/transport.py": (
        "efdc6c77058d613d527fd1cff920410f56539c593e53979be54e447635e4076d"
    ),
    "src/rigplane/backends/yaesu_cat/radio.py": (
        "1af535b63a03e351704bb732dee9eb4ac92d654751c0433eb3e004ca52ae8fee"
    ),
    "src/rigplane/backends/yaesu_cat/transport.py": (
        "3f9279a64895595c5c19d08c0d2d973902e2d8a5904ae5fb6edebbdc0558b26b"
    ),
    "src/rigplane/commands/commander.py": (
        "97c165d81f75240eeefacb3dca5dff74db1dacb7c452d339829f814fd3d82a01"
    ),
    "src/rigplane/core/priority_exchange.py": (
        "0965e8737ecc1d98626c874158626c9229086e60cb6cc24b313ca4296862a4f9"
    ),
    "src/rigplane/runtime/_civ_rx.py": (
        "a8c2145776224828045d93187f1eda5452985bc3f1616f7f4d12a55df0eb324e"
    ),
    "src/rigplane/runtime/radio.py": (
        "ae4c132a0e72df2b9bca0c324388c804a574915ea0dbd90e1652414847b64240"
    ),
    "tests/test_icom_managed_tx_actuator.py": (
        "c72b20e121e00397b57fc60bb50579fb310dc93ae87259c072f2afd58774694b"
    ),
    "tests/test_yaesu_cat_transport.py": (
        "d6ab5cb4f27d5f909615ec1a9937e19b2d2aa1bb761651db62f00a7a51ad4a25"
    ),
    "tests/test_yaesu_managed_tx_actuator.py": (
        "b9565ead2d988c68a3fa83db14b5ac7c372d3d6120fba6f67ad45f0f8f4db8df"
    ),
}
_PRODUCT_BLOBS = {
    "src/rigplane/backends/rigctld_client/transport.py": (
        "db36a204785c651619386f950b995bb62e3c04f1"
    ),
    "src/rigplane/backends/yaesu_cat/radio.py": (
        "de7c761a0aee672f6540f93d184785940e7af66b"
    ),
    "src/rigplane/backends/yaesu_cat/transport.py": (
        "4d3cd254eef63710f01aa706de6d3cdb45594d69"
    ),
    "src/rigplane/commands/commander.py": ("4ff27ffaf7d271756013fb8193b7a455cc265dc9"),
    "src/rigplane/core/priority_exchange.py": (
        "8320303c1727020f5b1536ab59807eef0bc202c9"
    ),
    "src/rigplane/runtime/_civ_rx.py": ("e4d4a37a032aad5d7021705f59b95360aac6d5e6"),
    "src/rigplane/runtime/radio.py": ("6c993565fc998f06e765cfa43859867ec22e6093"),
    "tests/test_icom_managed_tx_actuator.py": (
        "f3bf9ba6955c0845e1844b30cbaf27c114951b4c"
    ),
    "tests/test_yaesu_cat_transport.py": ("3ed550d5b0fe28d103775d17e343d1258b48c760"),
    "tests/test_yaesu_managed_tx_actuator.py": (
        "8c9a863eeeb5107371e3b4bcbfc91317be69322c"
    ),
}

_BASELINE_TARGETS = (
    "tests/test_yaesu_cat_transport.py::TestYaesuCatTransport::"
    "test_urgent_write_waits_for_active_frame_then_overtakes_ordinary_fifo",
    "tests/test_yaesu_cat_transport.py::TestYaesuCatTransport::"
    "test_cancelled_urgent_waiter_releases_ordinary_exchange",
    "tests/test_yaesu_cat_transport.py::TestYaesuCatTransport::"
    "test_force_release_overtakes_queued_aborts_after_provider_failure",
    "tests/test_yaesu_cat_transport.py::TestYaesuCatTransport::"
    "test_final_currency_check_suppresses_stale_write",
    "tests/test_rigctld_client_backend.py::"
    "test_urgent_exchange_preserves_active_frame_and_each_fifo",
    "tests/test_rigctld_client_backend.py::"
    "test_cancelled_admission_releases_only_its_reservation",
    "tests/test_rigctld_client_backend.py::"
    "test_write_currency_is_checked_after_stale_drain",
    "tests/test_icom_managed_tx_actuator.py",
    "tests/test_commander.py::test_priority_ordering",
    "tests/test_radio.py::TestPtt::test_set_ptt_uses_immediate_priority",
    "tests/test_radio.py::TestCapturedExecuteLifetime::"
    "test_generation_retirement_retrieves_detached_blocking_exception[pacing]",
    "tests/test_yaesu_managed_tx_actuator.py",
    "tests/test_ftx1_radio.py::test_set_ptt_on",
    "tests/test_ftx1_radio.py::test_set_ptt_off",
    "tests/test_ftx1_radio.py::test_send_cw_text_empty_sends_ky_clear",
    "tests/test_ftx1_radio.py::test_stop_cw_text_sends_ky_clear",
    "tests/test_ftx1_radio.py::test_set_tuner_status_delegates_to_set_tuner",
)

_PHASE_PLUGIN = """
import json
import os
from pathlib import Path

collected = []
phases = []


def pytest_collection_finish(session):
    collected.extend(item.nodeid for item in session.items)


def pytest_runtest_logreport(report):
    phases.append(
        {
            "nodeid": report.nodeid,
            "phase": report.when,
            "outcome": report.outcome,
            "longrepr": str(report.longrepr)[:2000] if report.failed else None,
        }
    )


def pytest_sessionfinish(session, exitstatus):
    Path(os.environ["PHASE_REPORT"]).write_text(
        json.dumps(
            {
                "collected": collected,
                "exitstatus": int(exitstatus),
                "phases": phases,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
"""


@dataclass(frozen=True)
class _Mutation:
    mutation_id: str
    path: str
    old: str
    new: str
    targets: tuple[str, ...]
    expected_collected: int
    failure_fragments: tuple[str, ...]
    minimum_failed_calls: int = 1


_MUTATIONS = (
    _Mutation(
        "M1-force-below-abort",
        "src/rigplane/core/priority_exchange.py",
        "        for tier in ExchangeTier:\n",
        (
            "        for tier in (\n"
            "            ExchangeTier.ABORT,\n"
            "            ExchangeTier.FORCE_RELEASE,\n"
            "            ExchangeTier.URGENT,\n"
            "            ExchangeTier.ORDINARY,\n"
            "        ):\n"
        ),
        (
            "tests/test_yaesu_cat_transport.py::TestYaesuCatTransport::"
            "test_force_release_overtakes_queued_aborts_after_provider_failure",
        ),
        1,
        ("test_force_release_overtakes_queued_aborts_after_provider_failure",),
    ),
    _Mutation(
        "M2-cancel-releases-active",
        "src/rigplane/core/priority_exchange.py",
        "            if not waiter.cancelled():\n                self._release()\n",
        "            if waiter.cancelled():\n                self._release()\n",
        (
            "tests/test_yaesu_cat_transport.py::TestYaesuCatTransport::"
            "test_cancelled_urgent_waiter_releases_ordinary_exchange",
        ),
        1,
        ("test_cancelled_urgent_waiter_releases_ordinary_exchange",),
    ),
    _Mutation(
        "M3-provider-write-error-swallowed",
        "src/rigplane/backends/yaesu_cat/transport.py",
        '            raise CatTransportError(f"Write failed: {exc}") from exc\n',
        "            return\n",
        (
            "tests/test_yaesu_cat_transport.py::TestYaesuCatTransport::"
            "test_force_release_overtakes_queued_aborts_after_provider_failure",
        ),
        1,
        ("test_force_release_overtakes_queued_aborts_after_provider_failure",),
    ),
    _Mutation(
        "M4-aborts-demoted-to-ordinary",
        "src/rigplane/backends/yaesu_cat/radio.py",
        (
            "        elif operation is AbortOperation.STOP_CW:\n"
            "            command, params, tier = (\n"
            '                "send_cw",\n'
            '                {"type": " ", "mem": ""},\n'
            "                ExchangeTier.ABORT,\n"
            "            )\n"
            "        elif operation is AbortOperation.STOP_TUNE:\n"
            "            command, params, tier = (\n"
            '                "set_tuner",\n'
            '                {"src": "0", "type": "0", "state": "0"},\n'
            "                ExchangeTier.ABORT,\n"
            "            )\n"
        ),
        (
            "        elif operation is AbortOperation.STOP_CW:\n"
            "            command, params, tier = (\n"
            '                "send_cw",\n'
            '                {"type": " ", "mem": ""},\n'
            "                ExchangeTier.ORDINARY,\n"
            "            )\n"
            "        elif operation is AbortOperation.STOP_TUNE:\n"
            "            command, params, tier = (\n"
            '                "set_tuner",\n'
            '                {"src": "0", "type": "0", "state": "0"},\n'
            "                ExchangeTier.ORDINARY,\n"
            "            )\n"
        ),
        (
            "tests/test_yaesu_managed_tx_actuator.py::"
            "test_managed_abort_uses_urgent_profile_command",
        ),
        2,
        ("test_managed_abort_uses_urgent_profile_command",),
        2,
    ),
    _Mutation(
        "M5-late-on-promoted-to-force",
        "src/rigplane/backends/yaesu_cat/transport.py",
        "        tier: ExchangeTier = ExchangeTier.ORDINARY,\n",
        "        tier: ExchangeTier = ExchangeTier.FORCE_RELEASE,\n",
        (
            "tests/test_yaesu_cat_transport.py::TestYaesuCatTransport::"
            "test_force_release_overtakes_queued_aborts_after_provider_failure",
        ),
        1,
        ("test_force_release_overtakes_queued_aborts_after_provider_failure",),
    ),
    _Mutation(
        "M6-icom-force-collapsed-to-abort",
        "src/rigplane/runtime/radio.py",
        "                priority = Priority.FORCE_RELEASE\n",
        "                priority = Priority.ABORT\n",
        (
            "tests/test_icom_managed_tx_actuator.py::"
            "test_release_and_abort_operations_use_profile_bytes_at_strict_priority",
            "tests/test_icom_managed_tx_actuator.py::"
            "test_force_release_overtakes_queued_abort_without_preempting_active",
        ),
        4,
        (
            "test_release_and_abort_operations_use_profile_bytes_at_strict_priority",
            "test_force_release_overtakes_queued_abort_without_preempting_active",
        ),
        2,
    ),
    _Mutation(
        "M7-yaesu-final-currency-removed",
        "src/rigplane/backends/yaesu_cat/transport.py",
        (
            "        self._require_write_currency(is_current)\n"
            "        try:\n"
            '            self._writer.write(command.encode("ascii"))\n'
        ),
        '        try:\n            self._writer.write(command.encode("ascii"))\n',
        (
            "tests/test_yaesu_cat_transport.py::TestYaesuCatTransport::"
            "test_final_currency_check_suppresses_stale_write",
        ),
        1,
        ("test_final_currency_check_suppresses_stale_write",),
    ),
    _Mutation(
        "M8-icom-final-currency-removed",
        "src/rigplane/runtime/_civ_rx.py",
        (
            "                check_current()\n"
            "                pkt = self._wrap_civ(civ_frame)\n"
            "                await transport.send_tracked(pkt)\n"
        ),
        (
            "                pkt = self._wrap_civ(civ_frame)\n"
            "                await transport.send_tracked(pkt)\n"
        ),
        (
            "tests/test_icom_managed_tx_actuator.py::"
            "test_provider_replacement_cannot_retarget_queued_on",
            "tests/test_icom_managed_tx_actuator.py::"
            "test_stale_attempt_is_refused_at_the_last_write_seam",
        ),
        2,
        (
            "test_provider_replacement_cannot_retarget_queued_on",
            "test_stale_attempt_is_refused_at_the_last_write_seam",
        ),
        2,
    ),
)


def _run(command: list[str], repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _product_hashes(repo: Path) -> dict[str, str]:
    return {path: _sha256(repo / path) for path in _PRODUCT_HASHES}


def _product_blobs(repo: Path) -> dict[str, str]:
    blobs = {}
    for path in _PRODUCT_BLOBS:
        indexed = _run(["git", "ls-files", "-s", "--", path], repo)
        assert indexed.returncode == 0 and indexed.stdout.endswith(f"\t{path}\n")
        blobs[path] = indexed.stdout.split()[1]
    return blobs


def _assert_clean_product(repo: Path) -> None:
    assert _product_hashes(repo) == _PRODUCT_HASHES
    assert _product_blobs(repo) == _PRODUCT_BLOBS
    clean = _run(["git", "diff", "--exit-code", "--", *_PRODUCT_HASHES], repo)
    assert clean.returncode == 0, clean.stdout + clean.stderr


def _candidate_tree_without_harness(repo: Path, temporary: Path) -> str:
    index_path = temporary / "candidate.index"
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = str(index_path)
    read = subprocess.run(
        ["git", "read-tree", "HEAD"], cwd=repo, env=env, check=False, text=True
    )
    assert read.returncode == 0
    remove = subprocess.run(
        ["git", "update-index", "--force-remove", _HARNESS_PATH],
        cwd=repo,
        env=env,
        check=False,
        text=True,
    )
    assert remove.returncode == 0
    tree = subprocess.run(
        ["git", "write-tree"],
        cwd=repo,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert tree.returncode == 0, tree.stderr
    return tree.stdout.strip()


def _run_pytest(
    repo: Path,
    temporary: Path,
    label: str,
    targets: tuple[str, ...],
    *,
    timeout: int,
) -> dict[str, Any]:
    run_dir = temporary / label
    run_dir.mkdir()
    plugin = run_dir / "phase_plugin.py"
    report_path = run_dir / "phases.json"
    junit_path = run_dir / "junit.xml"
    plugin.write_text(_PHASE_PLUGIN, encoding="utf-8")
    env = os.environ.copy()
    env["PHASE_REPORT"] = str(report_path)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPYCACHEPREFIX"] = str(run_dir / "pycache")
    env["PYTHONPATH"] = os.pathsep.join((str(run_dir), env.get("PYTHONPATH", "")))
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--tb=short",
        "--timeout=30",
        "--timeout-method=thread",
        "-p",
        "no:cacheprovider",
        "-p",
        "phase_plugin",
        f"--junitxml={junit_path}",
        *targets,
    ]
    completed = subprocess.run(
        command,
        cwd=repo,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert report_path.exists(), completed.stdout + completed.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return {
        "label": label,
        "targets": targets,
        "argv": command,
        "returncode": completed.returncode,
        "collected": report["collected"],
        "exitstatus": report["exitstatus"],
        "phases": report["phases"],
        "junit_sha256": _sha256(junit_path),
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
    }


def _assert_phase_integrity(result: dict[str, Any], expected_collected: int) -> None:
    collected = result["collected"]
    assert len(collected) == expected_collected, result
    assert len(set(collected)) == expected_collected, result
    phases = result["phases"]
    assert len(phases) == expected_collected * 3, result
    for nodeid in collected:
        node_phases = [phase for phase in phases if phase["nodeid"] == nodeid]
        assert [phase["phase"] for phase in node_phases] == [
            "setup",
            "call",
            "teardown",
        ], result
        assert node_phases[0]["outcome"] == "passed", result
        assert node_phases[2]["outcome"] == "passed", result


def _assert_green(result: dict[str, Any], expected_collected: int) -> None:
    _assert_phase_integrity(result, expected_collected)
    assert result["returncode"] == 0 and result["exitstatus"] == 0, result
    assert all(phase["outcome"] == "passed" for phase in result["phases"]), result


def _assert_mutant_killed(result: dict[str, Any], mutation: _Mutation) -> None:
    _assert_phase_integrity(result, mutation.expected_collected)
    assert result["returncode"] == 1 and result["exitstatus"] == 1, result
    failed_calls = [
        phase
        for phase in result["phases"]
        if phase["phase"] == "call" and phase["outcome"] == "failed"
    ]
    assert len(failed_calls) >= mutation.minimum_failed_calls, result
    assert all(
        any(fragment in phase["nodeid"] for fragment in mutation.failure_fragments)
        for phase in failed_calls
    ), result


def _emit(terminal: Any, evidence: dict[str, Any]) -> None:
    terminal.write_line(
        "MUTATION_EVIDENCE "
        + json.dumps(evidence, separators=(",", ":"), sort_keys=True)
    )


def test_provider_actuator_candidate_green_and_mutation_proven(
    pytestconfig: pytest.Config,
) -> None:
    repo = Path(__file__).resolve().parents[1]
    terminal = pytestconfig.pluginmanager.get_plugin("terminalreporter")
    assert terminal is not None
    assert _run(["git", "status", "--porcelain=v1"], repo).stdout == ""
    _assert_clean_product(repo)

    with tempfile.TemporaryDirectory(prefix="provider-actuator-carrier-") as raw:
        temporary = Path(raw)
        assert _candidate_tree_without_harness(repo, temporary) == _CANDIDATE_TREE
        baseline = _run_pytest(
            repo,
            temporary,
            "baseline",
            _BASELINE_TARGETS,
            timeout=180,
        )
        _assert_green(baseline, 41)
        _assert_clean_product(repo)
        _emit(
            terminal,
            {
                "phase": "baseline",
                "candidate_sha": _CANDIDATE_SHA,
                "candidate_tree": _CANDIDATE_TREE,
                "product_blobs": _PRODUCT_BLOBS,
                "product_hashes": _PRODUCT_HASHES,
                "result": baseline,
                "restored": True,
            },
        )

        for mutation in _MUTATIONS:
            path = repo / mutation.path
            original = path.read_bytes()
            original_text = original.decode("utf-8")
            replacement_count = original_text.count(mutation.old)
            assert replacement_count == 1, mutation
            try:
                path.write_text(
                    original_text.replace(mutation.old, mutation.new),
                    encoding="utf-8",
                )
                patch = _run(["git", "diff", "--", mutation.path], repo)
                assert patch.returncode == 0 and patch.stdout, mutation
                result = _run_pytest(
                    repo,
                    temporary,
                    mutation.mutation_id,
                    mutation.targets,
                    timeout=60,
                )
                _assert_mutant_killed(result, mutation)
            finally:
                path.write_bytes(original)
            _assert_clean_product(repo)
            _emit(
                terminal,
                {
                    "phase": "mutation",
                    "candidate_sha": _CANDIDATE_SHA,
                    "candidate_tree": _CANDIDATE_TREE,
                    "mutation_id": mutation.mutation_id,
                    "path": mutation.path,
                    "replacement_count": replacement_count,
                    "patch": patch.stdout,
                    "result": result,
                    "restore_sha256": _sha256(path),
                    "restored": True,
                },
            )

    _assert_clean_product(repo)
    assert _run(["git", "status", "--porcelain=v1"], repo).stdout == ""
    _emit(
        terminal,
        {
            "phase": "final",
            "candidate_sha": _CANDIDATE_SHA,
            "candidate_tree": _CANDIDATE_TREE,
            "product_blobs": _product_blobs(repo),
            "product_hashes": _product_hashes(repo),
            "restored": True,
            "status": "clean",
        },
    )
