"""Tests for ``rigplane validate --provider hamlib`` plumbing (PR-B).

No real ``rigctld`` and no real hardware: ``HamlibBridge`` is replaced with a
small async-context fake, and the rigctld-client side is pointed at an
in-process :class:`FakeRigctldServer`. The native side uses a minimal
dataclass-style fake CI-V radio (per CLAUDE.md: no MagicMock radios).
"""

from __future__ import annotations

import argparse
import asyncio
import socket
from typing import Any

from fake_rigctld import FakeRigctldServer

from rigplane.backends.config import RigctldBackendConfig
from rigplane.backends.hamlib_models import HamlibCaps
from rigplane.backends.rigctld_client import RigctldClientRadio
from rigplane.cli import _validate
from rigplane.profiles import get_radio_profile
from rigplane.validation import (
    CapabilityDeclaration,
    CapabilityDeclarationEntry,
    MatrixTemplate,
    OperatorSafetyBlock,
    RadioTarget,
    ValidationLevel,
)
from rigplane.validation.schema import (
    SCHEMA_VERSION,
    TOOL_NAME,
)


def _identify_template(profile_id: str = "xiegu_x6200") -> MatrixTemplate:
    """In-memory template with a single read-only ``discovery.identify`` check."""
    entry = CapabilityDeclarationEntry(
        check_id="discovery.identify",
        capability="",
        level=ValidationLevel.DISCOVERY,
        declaration=CapabilityDeclaration.SUPPORTED,
        summary="Identify the radio",
    )
    return MatrixTemplate(
        radio=RadioTarget(model="X6200", profile_id=profile_id),
        entries=[entry],
    )


class _FakeNativeRadio:
    """Minimal async-context fake CI-V native radio (raw-pipe surface stubbed)."""

    def __init__(self) -> None:
        self.entered = False

    async def __aenter__(self) -> _FakeNativeRadio:
        self.entered = True
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.entered = False


class _FakeBridge:
    """Async-context stand-in for :class:`HamlibBridge` — start/stop no-ops."""

    instances: list[_FakeBridge] = []

    def __init__(self, radio: Any, **kwargs: Any) -> None:
        self.radio = radio
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        _FakeBridge.instances.append(self)

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


class _PartialStartBridge(_FakeBridge):
    """Bridge whose start() fails after a partial resource acquire.

    Mirrors HamlibBridge.start() binding the listener / beginning the CAT
    session in open_transport() and then raising in spawn_rigctld() when
    rigctld is missing — stop() must still run to release everything.
    """

    async def start(self) -> None:
        self.started = True  # partial acquire happened before the failure
        raise OSError("rigctld not found on PATH")


def _base_args(**overrides: Any) -> argparse.Namespace:
    ns = argparse.Namespace(
        provider="hamlib",
        model="X6200",
        read_only=True,
        compare=None,
        output=None,
        json=False,
    )
    for key, value in overrides.items():
        setattr(ns, key, value)
    return ns


def test_provider_flag_defaults_native() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    _validate.add_subparser(sub)
    args = parser.parse_args(["validate", "--template", "x.json"])
    assert args.provider == "native"
    assert args.compare is None


def test_to_profile_carries_hamlib_model_id() -> None:
    profile = get_radio_profile("X6200")
    assert profile.hamlib_model_id == 3091


async def test_run_hardware_hamlib_orchestration(monkeypatch: Any) -> None:
    _FakeBridge.instances.clear()
    template = _identify_template()
    safety = OperatorSafetyBlock()

    async with FakeRigctldServer() as server:
        # Native config: model="X6200" with radio_addr so the hamlib path can
        # read both. Using a rigctld config here only as a typed carrier of
        # .model/.host/.port — create_radio is monkeypatched, so the backend
        # kind is irrelevant; the fake returns a CI-V native radio for it.
        native_config = RigctldBackendConfig(
            host="127.0.0.1", port=server.port, model="X6200"
        )

        captured: dict[str, Any] = {}

        async def fake_build_backend_config(_args: Any) -> Any:
            return native_config

        def fake_create_radio(config: Any) -> Any:
            if (
                isinstance(config, RigctldBackendConfig)
                and config.host == "127.0.0.1"
                and config.port != server.port
            ):
                # The hamlib-side config points at the bridge front port; route
                # it instead at the in-process fake rigctld server.
                captured["front_port"] = config.port
                return RigctldClientRadio(host=server.host, port=server.port)
            return _FakeNativeRadio()

        monkeypatch.setattr(
            _validate,
            "_emit_artifact",
            lambda artifact, args: captured.setdefault("artifact", artifact),
        )
        monkeypatch.setattr(
            "rigplane.cli._build_backend_config", fake_build_backend_config
        )
        monkeypatch.setattr("rigplane.backends.factory.create_radio", fake_create_radio)
        monkeypatch.setattr("rigplane.hamlib_bridge.HamlibBridge", _FakeBridge)

        # The fake bridge never actually binds the front port, so skip the
        # real TCP poll and return True immediately.
        async def fake_await_tcp_ready(
            host: str, port: int, *, timeout: float = 10.0
        ) -> bool:
            return True

        monkeypatch.setattr(_validate, "_await_tcp_ready", fake_await_tcp_ready)

        exit_code = await _validate._run_hardware_hamlib(_base_args(), template, safety)

    assert exit_code == 0
    assert len(_FakeBridge.instances) == 1
    bridge = _FakeBridge.instances[0]
    assert bridge.started is True
    assert bridge.stopped is True
    assert bridge.kwargs["model"] == "3091"  # hamlib_model_id for X6200
    artifact = captured["artifact"]
    assert artifact.metadata["provider"] == "hamlib"
    assert artifact.metadata["hamlib_model_id"] == 3091


async def test_hamlib_provider_partial_start_is_torn_down(monkeypatch: Any) -> None:
    """A bridge.start() that fails mid-way must still be stopped, exit 3."""
    _FakeBridge.instances.clear()
    template = _identify_template()
    safety = OperatorSafetyBlock()

    native_config = RigctldBackendConfig(host="127.0.0.1", port=4532, model="X6200")
    captured: dict[str, Any] = {}

    async def fake_build_backend_config(_args: Any) -> Any:
        return native_config

    def fake_create_radio(config: Any) -> Any:
        return _FakeNativeRadio()

    monkeypatch.setattr(
        _validate,
        "_emit_artifact",
        lambda artifact, args: captured.setdefault("artifact", artifact),
    )
    monkeypatch.setattr("rigplane.cli._build_backend_config", fake_build_backend_config)
    monkeypatch.setattr("rigplane.backends.factory.create_radio", fake_create_radio)
    monkeypatch.setattr("rigplane.hamlib_bridge.HamlibBridge", _PartialStartBridge)

    exit_code = await _validate._run_hardware_hamlib(_base_args(), template, safety)

    assert exit_code == 3
    assert len(_FakeBridge.instances) == 1
    bridge = _FakeBridge.instances[0]
    assert bridge.started is True  # partial start happened
    assert bridge.stopped is True  # ...and was still torn down
    # The failure is recorded as a transport-domain artifact.
    artifact = captured["artifact"]
    checks = [c for lvl in artifact.levels for c in lvl.checks]
    assert any(c.failure_domain is not None for c in checks)
    assert artifact.metadata["profile_id"] == "xiegu_x6200"


async def test_hamlib_provider_rejects_non_civ(monkeypatch: Any) -> None:
    _FakeBridge.instances.clear()
    template = _identify_template(profile_id="ftx1")
    safety = OperatorSafetyBlock()

    # A non-CI-V config (Yaesu). _build_backend_config returns it; profile
    # resolves to protocol_type != "civ".
    yaesu_config = RigctldBackendConfig(host="127.0.0.1", port=4532, model="FTX-1")

    async def fake_build_backend_config(_args: Any) -> Any:
        return yaesu_config

    captured: dict[str, Any] = {}

    monkeypatch.setattr("rigplane.cli._build_backend_config", fake_build_backend_config)
    monkeypatch.setattr("rigplane.hamlib_bridge.HamlibBridge", _FakeBridge)
    monkeypatch.setattr(
        _validate,
        "_emit_artifact",
        lambda artifact, args: captured.setdefault("artifact", artifact),
    )

    exit_code = await _validate._run_hardware_hamlib(
        _base_args(model="FTX-1"), template, safety
    )

    assert exit_code == 3
    assert _FakeBridge.instances == []  # bridge never constructed/started
    artifact = captured["artifact"]
    assert artifact.metadata["provider"] == "hamlib"
    checks = [c for level in artifact.levels for c in level.checks]
    assert any(
        c.status.value == "fail"
        and c.failure_domain is not None
        and c.failure_domain.value == "transport"
        and c.level == ValidationLevel.DISCOVERY
        for c in checks
    )


def test_compare_artifacts_diffs_status() -> None:
    def _artifact_dict(freq_status: str, mode_status: str) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "tool": TOOL_NAME,
            "mode": "hardware",
            "core_version": "0",
            "radio": {"model": "X6200", "profile_id": "xiegu_x6200"},
            "transport": {"backend": "rigctld"},
            "safety": {"tx_allowed": False, "tuner_allowed": False},
            "levels": [
                {
                    "level": 1,
                    "checks": [
                        {
                            "check_id": "freq.write",
                            "capability": "",
                            "level": 1,
                            "status": freq_status,
                            "declaration": "supported",
                            "summary": "freq",
                            **(
                                {"failure_domain": "transport"}
                                if freq_status in {"fail", "blocked"}
                                else {}
                            ),
                        },
                        {
                            "check_id": "mode.set",
                            "capability": "",
                            "level": 1,
                            "status": mode_status,
                            "declaration": "supported",
                            "summary": "mode",
                            **(
                                {"failure_domain": "transport"}
                                if mode_status in {"fail", "blocked"}
                                else {}
                            ),
                        },
                    ],
                }
            ],
            "metadata": {"provider": "native"},
        }

    from rigplane.validation.schema import validate_artifact_dict

    this = validate_artifact_dict(_artifact_dict("pass", "pass"))

    import json
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        other_file = Path(tmp) / "other.json"
        other_file.write_text(
            json.dumps(_artifact_dict("pass", "fail")), encoding="utf-8"
        )
        comp = _validate._compare_artifacts(this, str(other_file))

    assert comp["other_provider"] == "native"
    rows = {row["check_id"]: row for row in comp["rows"]}
    assert rows["freq.write"]["agree"] is True
    assert rows["mode.set"]["agree"] is False
    assert rows["mode.set"]["this"] == "pass"
    assert rows["mode.set"]["other"] == "fail"


async def test_await_tcp_ready_true() -> None:
    """_await_tcp_ready returns True when a listener is up on the target port."""
    from rigplane.cli._validate import _await_tcp_ready

    # Start a throwaway server on an ephemeral port.
    server = await asyncio.start_server(
        lambda r, w: w.close(), host="127.0.0.1", port=0
    )
    port = server.sockets[0].getsockname()[1]
    try:
        result = await _await_tcp_ready("127.0.0.1", port, timeout=5.0)
    finally:
        server.close()
        await server.wait_closed()

    assert result is True


async def test_await_tcp_ready_timeout() -> None:
    """_await_tcp_ready returns False when nothing is listening on the port."""
    from rigplane.cli._validate import _await_tcp_ready

    # Bind a socket to obtain a free port, then close it immediately (not listening).
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        closed_port = s.getsockname()[1]
    # Port is now closed; _await_tcp_ready should time out quickly.
    result = await _await_tcp_ready("127.0.0.1", closed_port, timeout=1.0)
    assert result is False


async def test_run_hardware_forwards_write_only_capabilities(
    monkeypatch: Any,
) -> None:
    """_run_hardware (native) forwards profile.write_only_controls to the
    hardware runner as write_only_capabilities (MOR-208)."""
    template = _identify_template()
    safety = OperatorSafetyBlock()
    native_config = RigctldBackendConfig(host="127.0.0.1", port=4532, model="X6200")
    captured: dict[str, Any] = {}

    async def fake_build_backend_config(_args: Any) -> Any:
        return native_config

    def fake_create_radio(config: Any) -> Any:
        return _FakeNativeRadio()

    async def fake_execute(*args: Any, **kwargs: Any) -> Any:
        captured["write_only_capabilities"] = kwargs.get("write_only_capabilities")
        return []

    monkeypatch.setattr("rigplane.cli._build_backend_config", fake_build_backend_config)
    monkeypatch.setattr("rigplane.backends.factory.create_radio", fake_create_radio)
    monkeypatch.setattr(
        "rigplane.validation.hardware.execute_hardware_checks", fake_execute
    )
    monkeypatch.setattr(_validate, "_emit_artifact", lambda artifact, args: None)

    await _validate._run_hardware(_base_args(read_only=False), template, safety)

    assert captured["write_only_capabilities"] == frozenset({"rit", "xit", "notch"})


async def test_run_hardware_hamlib_forwards_write_only_capabilities(
    monkeypatch: Any,
) -> None:
    """_run_hardware_hamlib forwards profile.write_only_controls too."""
    _FakeBridge.instances.clear()
    template = _identify_template()
    safety = OperatorSafetyBlock()
    native_config = RigctldBackendConfig(host="127.0.0.1", port=4532, model="X6200")
    captured: dict[str, Any] = {}

    async def fake_build_backend_config(_args: Any) -> Any:
        return native_config

    def fake_create_radio(config: Any) -> Any:
        return _FakeNativeRadio()

    async def fake_execute(*args: Any, **kwargs: Any) -> Any:
        captured["write_only_capabilities"] = kwargs.get("write_only_capabilities")
        return []

    async def fake_await_tcp_ready(
        host: str, port: int, *, timeout: float = 10.0
    ) -> bool:
        return True

    monkeypatch.setattr("rigplane.cli._build_backend_config", fake_build_backend_config)
    monkeypatch.setattr("rigplane.backends.factory.create_radio", fake_create_radio)
    monkeypatch.setattr("rigplane.hamlib_bridge.HamlibBridge", _FakeBridge)
    monkeypatch.setattr(
        "rigplane.validation.hardware.execute_hardware_checks", fake_execute
    )
    monkeypatch.setattr(_validate, "_await_tcp_ready", fake_await_tcp_ready)
    monkeypatch.setattr(_validate, "_emit_artifact", lambda artifact, args: None)

    await _validate._run_hardware_hamlib(_base_args(), template, safety)

    assert captured["write_only_capabilities"] == frozenset({"rit", "xit", "notch"})


# ---------------------------------------------------------------------------
# Generator B (_hamlib_caps_to_tokens) unit tests (MOR-211)
# ---------------------------------------------------------------------------


def test_hamlib_caps_to_tokens_levels_and_funcs() -> None:
    """Levels and funcs sets are all unioned into the token set."""
    caps = HamlibCaps(
        get_levels=frozenset({"RF", "STRENGTH"}),
        set_levels=frozenset({"AF"}),
        get_funcs=frozenset({"NB"}),
        set_funcs=frozenset({"NR"}),
    )
    tokens = _validate._hamlib_caps_to_tokens(caps)
    assert {"RF", "STRENGTH", "AF", "NB", "NR"}.issubset(tokens)


def test_hamlib_caps_to_tokens_has_set_freq_true() -> None:
    """has_set_freq=True adds 'f' token."""
    caps = HamlibCaps(has_set_freq=True)
    tokens = _validate._hamlib_caps_to_tokens(caps)
    assert "f" in tokens


def test_hamlib_caps_to_tokens_has_set_freq_false() -> None:
    """has_set_freq=False does NOT add 'f' token."""
    caps = HamlibCaps(has_set_freq=False)
    tokens = _validate._hamlib_caps_to_tokens(caps)
    assert "f" not in tokens


def test_hamlib_caps_to_tokens_modes_nonempty() -> None:
    """Non-empty modes adds 'm' token."""
    caps = HamlibCaps(modes=frozenset({"USB", "LSB"}))
    tokens = _validate._hamlib_caps_to_tokens(caps)
    assert "m" in tokens


def test_hamlib_caps_to_tokens_modes_empty() -> None:
    """Empty modes does NOT add 'm' token."""
    caps = HamlibCaps(modes=frozenset())
    tokens = _validate._hamlib_caps_to_tokens(caps)
    assert "m" not in tokens


def test_hamlib_caps_to_tokens_ptt_type_present() -> None:
    """ptt_type != None adds 't' token."""
    caps = HamlibCaps(ptt_type="RIG")
    tokens = _validate._hamlib_caps_to_tokens(caps)
    assert "t" in tokens


def test_hamlib_caps_to_tokens_ptt_type_none() -> None:
    """ptt_type=None does NOT add 't' token."""
    caps = HamlibCaps(ptt_type=None)
    tokens = _validate._hamlib_caps_to_tokens(caps)
    assert "t" not in tokens


def test_hamlib_caps_to_tokens_empty_caps() -> None:
    """All-default (degraded) HamlibCaps yields an empty frozenset."""
    caps = HamlibCaps()
    tokens = _validate._hamlib_caps_to_tokens(caps)
    assert tokens == frozenset()


# ---------------------------------------------------------------------------
# CLI Gen-B generation — dry-run, no hardware (MOR-211)
# ---------------------------------------------------------------------------


def _make_dry_run_args(**kwargs: Any) -> argparse.Namespace:
    """Namespace with safe defaults for _validate.run() dry-run."""
    defaults = dict(
        template=None,
        model="X6200",
        hardware=False,
        allow_hardware=False,
        tx_allowed=False,
        tuner_allowed=False,
        read_only=False,
        provider="hamlib",
        compare=None,
        operator_id=None,
        output=None,
        json=True,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_cli_genb_dry_run_hamlib_provider(monkeypatch: Any) -> None:
    """validate --model X6200 --provider hamlib (no --template) calls Generator B.

    With the supplied HamlibCaps providing RF/AF/PREAMP/ATT/NB/NR/f/m/t tokens,
    the expected SUPPORTED checks are:
      rf_gain.set, af_level.set, preamp.set, attenuator.set, nb.set, nr.set,
      freq.write, mode.set.
    Checks with hamlib_token=None (discovery.identify, agc.set, rit.set) must
    be UNSUPPORTED_PENDING_EVIDENCE.
    """
    fake_caps = HamlibCaps(
        get_levels=frozenset({"RF", "AF", "PREAMP", "ATT"}),
        set_levels=frozenset({"RF", "AF", "PREAMP", "ATT"}),
        get_funcs=frozenset({"NB", "NR"}),
        set_funcs=frozenset({"NB", "NR"}),
        modes=frozenset({"USB"}),
        has_set_freq=True,
        ptt_type="RIG",
    )
    monkeypatch.setattr(
        "rigplane.backends.hamlib_models.load_hamlib_caps",
        lambda model_id: fake_caps,
    )

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        _validate,
        "_emit_artifact",
        lambda artifact, args: captured.setdefault("artifact", artifact),
    )

    args = _make_dry_run_args()
    rc = _validate.run(args)

    assert rc == 0
    artifact = captured["artifact"]
    entries_by_id = {c.check_id: c for level in artifact.levels for c in level.checks}

    # Checks that should be SUPPORTED (token present + cap declared on X6200)
    expected_supported = {
        "rf_gain.set",
        "af_level.set",
        "preamp.set",
        "attenuator.set",
        "nb.set",
        "nr.set",
        "freq.write",
        "mode.set",
    }
    for cid in expected_supported:
        assert cid in entries_by_id, f"Missing check_id {cid!r}"
        assert entries_by_id[cid].declaration == CapabilityDeclaration.SUPPORTED, (
            f"{cid!r}: expected SUPPORTED, got {entries_by_id[cid].declaration!r}"
        )

    # Checks with hamlib_token=None → always UNSUPPORTED_PENDING_EVIDENCE in Gen-B
    expected_pending = {"discovery.identify", "agc.set", "rit.set"}
    for cid in expected_pending:
        assert cid in entries_by_id, f"Missing check_id {cid!r}"
        assert (
            entries_by_id[cid].declaration
            == CapabilityDeclaration.UNSUPPORTED_PENDING_EVIDENCE
        ), (
            f"{cid!r}: expected UNSUPPORTED_PENDING_EVIDENCE, got {entries_by_id[cid].declaration!r}"
        )


def test_cli_genb_dry_run_degraded_caps_warns(monkeypatch: Any, capsys: Any) -> None:
    """When HamlibCaps is degraded, a warning is printed to stderr."""
    degraded_caps = HamlibCaps(
        degraded_reason="dump_caps unavailable: tool not found",
    )
    monkeypatch.setattr(
        "rigplane.backends.hamlib_models.load_hamlib_caps",
        lambda model_id: degraded_caps,
    )
    monkeypatch.setattr(
        _validate,
        "_emit_artifact",
        lambda artifact, args: None,
    )

    args = _make_dry_run_args()
    rc = _validate.run(args)

    assert rc == 0
    captured = capsys.readouterr()
    assert "Warning" in captured.err
    assert "dump_caps unavailable" in captured.err or "N/A" in captured.err


def test_cli_genb_native_provider_unchanged(monkeypatch: Any) -> None:
    """--provider native (no --template) still uses Gen-A; discovery.identify SUPPORTED."""
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        _validate,
        "_emit_artifact",
        lambda artifact, args: captured.setdefault("artifact", artifact),
    )

    args = _make_dry_run_args(provider="native")
    rc = _validate.run(args)

    assert rc == 0
    artifact = captured["artifact"]
    entries_by_id = {c.check_id: c for level in artifact.levels for c in level.checks}
    # Gen-A: discovery.identify is SUPPORTED (structural, no hamlib_token gating)
    assert (
        entries_by_id["discovery.identify"].declaration
        == CapabilityDeclaration.SUPPORTED
    )
