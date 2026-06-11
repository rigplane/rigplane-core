"""Tests for ``rigplane validate --provider hamlib-external`` (MOR-638).

Runs the validation matrix against an ARBITRARY external Hamlib ``rigctld``
with no RigPlane profile. The rigctld side is an in-process
:class:`FakeRigctldServer`; no real ``rigctld`` and no real hardware.
"""

from __future__ import annotations

import argparse
from typing import Any

from fake_rigctld import FakeRigctldServer

from rigplane.cli import _validate
from rigplane.validation import OperatorSafetyBlock


def _hw_args(server: FakeRigctldServer, **overrides: Any) -> argparse.Namespace:
    ns = argparse.Namespace(
        provider="hamlib-external",
        rigctld_host=server.host,
        rigctld_port=server.port,
        rigctld_model="My External Rig",
        template=None,
        model=None,
        read_only=False,
        timeout=2.0,
        compare=None,
        output=None,
        json=False,
    )
    for key, value in overrides.items():
        setattr(ns, key, value)
    return ns


def test_provider_flag_accepts_hamlib_external() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    _validate.add_subparser(sub)
    args = parser.parse_args(
        [
            "validate",
            "--provider",
            "hamlib-external",
            "--rigctld-host",
            "10.0.0.5",
            "--rigctld-port",
            "4599",
        ]
    )
    assert args.provider == "hamlib-external"
    assert args.rigctld_host == "10.0.0.5"
    assert args.rigctld_port == 4599
    assert args.rigctld_model is None


async def test_hamlib_external_runs_matrix_no_profile() -> None:
    """End-to-end: the matrix executes against an arbitrary rig with no profile.

    The fake advertises freq/mode/PTT/RF/AF/PREAMP/ATT/NB/NR. Expect the
    backend-implemented controls to PASS (read-modify-verify-restore) and the
    unimplemented registry checks to resolve to UNSUPPORTED — never crash.
    """
    safety = OperatorSafetyBlock()
    async with FakeRigctldServer() as server:
        exit_code, artifact = await _validate._run_hardware_hamlib_external(
            _hw_args(server), safety
        )

    assert exit_code == 0
    assert artifact.metadata["provider"] == "hamlib-external"

    statuses = {
        c.check_id: c.status.value for lvl in artifact.levels for c in lvl.checks
    }
    # Controls the backend implements + the fake supports must PASS.
    for cid in (
        "discovery.identify",
        "freq.write",
        "af_level.set",
        "preamp.set",
        "attenuator.set",
        "nb.set",
        "nr.set",
    ):
        assert statuses.get(cid) == "pass", f"{cid} -> {statuses.get(cid)}"

    # A genuinely-unimplemented control resolves to unsupported, not a crash.
    assert statuses.get("agc.set") == "unsupported"
    assert statuses.get("filter_width.set") == "unsupported"

    # A meaningful fraction of the registry actually executed (pass or fail).
    executed = sum(1 for v in statuses.values() if v in {"pass", "fail"})
    assert executed >= 7, f"only {executed} checks executed: {statuses}"


async def test_hamlib_external_connect_failure_is_transport_artifact() -> None:
    """A dead port yields exit 3 + a transport-domain DISCOVERY failure."""
    safety = OperatorSafetyBlock()
    async with FakeRigctldServer() as server:
        dead_port = server.port
    # Server is now stopped; the port no longer accepts connections.

    args = argparse.Namespace(
        provider="hamlib-external",
        rigctld_host="127.0.0.1",
        rigctld_port=dead_port,
        rigctld_model=None,
        template=None,
        model=None,
        read_only=False,
        timeout=0.5,
        compare=None,
        output=None,
        json=False,
    )
    exit_code, artifact = await _validate._run_hardware_hamlib_external(args, safety)

    assert exit_code == 3
    assert artifact.metadata["provider"] == "hamlib-external"
    checks = [c for lvl in artifact.levels for c in lvl.checks]
    assert any(
        c.failure_domain is not None and c.failure_domain.value == "transport"
        for c in checks
    )


async def test_hamlib_external_read_only_skips_writes() -> None:
    """--read-only: write checks SKIP; pure-read discovery still PASSes."""
    safety = OperatorSafetyBlock()
    async with FakeRigctldServer() as server:
        exit_code, artifact = await _validate._run_hardware_hamlib_external(
            _hw_args(server, read_only=True), safety
        )

    assert exit_code == 0
    statuses = {
        c.check_id: c.status.value for lvl in artifact.levels for c in lvl.checks
    }
    assert statuses.get("discovery.identify") == "pass"
    assert statuses.get("freq.write") == "skip"
    assert statuses.get("nb.set") == "skip"


def test_run_dispatches_hamlib_external_without_profile(monkeypatch: Any) -> None:
    """run() with provider=hamlib-external + no --model/--template still works.

    The dry-run path must build a profile-free template and emit an artifact.
    """
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        _validate,
        "_emit_artifact",
        lambda artifact, args: captured.setdefault("artifact", artifact),
    )

    args = argparse.Namespace(
        provider="hamlib-external",
        rigctld_host="127.0.0.1",
        rigctld_port=4532,
        rigctld_model=None,
        template=None,
        model=None,
        hardware=False,
        allow_hardware=False,
        tx_allowed=False,
        tuner_allowed=False,
        read_only=False,
        compare=None,
        operator_id=None,
        output=None,
        json=True,
    )
    rc = _validate.run(args)

    assert rc == 0
    artifact = captured["artifact"]
    assert artifact.radio.profile_id == "hamlib_external"
    # The full registry capability set was used to build the upfront template.
    check_ids = {c.check_id for lvl in artifact.levels for c in lvl.checks}
    assert "rf_gain.set" in check_ids
    assert "discovery.identify" in check_ids
