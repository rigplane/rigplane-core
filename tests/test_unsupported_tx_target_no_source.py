"""MOR-4433: a radio with no TX-target source serves "unsupported".

Owner decision: "remove the prohibition, transmit must work" for radios with
one transceiver and no readable TX target (Xiegu G7250 on Icom CI-V, and the
sourceless single-receiver Yaesu shape). One shared mechanism: the backend
publishes the sticky ``UnknownTxTarget(reason="unsupported")`` fact through
its existing publication path, so the served state tells "no source exists"
apart from "a source exists but is unobserved".

Both tests start the REAL backend's production acquisition with only the
hardware faked, then read ``unsupported`` through the real HTTP API
(``GET /api/v1/state``) parsed through the producer's ``ServerStatePublic``
model — never a raw dict, never a private publish helper, never a seed.
Each test fails when its backend's new publication is removed (proven by a
scratch revert of that backend's change, not committed).

Scope note (gate round 1 split): the managed-station WS key/unkey, TUNE and
SDK/rigctld pins moved to follow-up PR B (Linear MOR-4472).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from rigplane.backends.factory import create_radio
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.cli import _build_backend_config, _build_parser
from rigplane.core import serial_open
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.profiles import resolve_radio_profile
from rigplane.profiles.rig_loader import RigConfig, load_rig
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.state_schema import ServerStatePublic, UnknownTxTargetPublic
from rigplane_testkit.sim.adapters.yaesu_cat import YaesuCatSerialFake
from test_web_server import _addr, _http_get
from testkit.test_g7250_session_wire import (
    _ANSWER_WINDOW_S,
    _DEVICE,
    _G7250BenchFake,
)

ROOT = Path(__file__).resolve().parents[1]
FT991A_RIG = ROOT / "rigs" / "ft991a.toml"
FT991A_SIM = ROOT / "rigplane_testkit" / "sim" / "profiles" / "ft991a.toml"

#: Model name stamped on the synthetic sourceless profile, so refusal
#: messages name it instead of the real FT-991A it is derived from.
_SYNTHETIC_MODEL = "FT-991A-NO-TX-SRC"


def _sourceless_yaesu_config() -> RigConfig:
    """A sourceless Yaesu profile built from the committed FT-991A config.

    FT-991A gained a real TX-target source (#4584), so the sourceless
    set keeps a Yaesu member through this synthetic: the same loaded
    config with the source removed through the producer's own loader
    output — both TX verbs deleted from ``commands``, ``tx_func``
    cleared, and the ``tx_target`` polling capability (plus its field
    policy) dropped from the acquisition profile via
    ``dataclasses.replace`` (which rebuilds the path index in
    ``__post_init__``). A fresh ``load_rig`` per call, so no test mutates
    a shared config; the model is renamed so refusal messages name the
    synthetic, not the real FT-991A.
    """
    from dataclasses import replace

    # RigConfig is frozen: derive a modified copy through the producer's
    # own dataclass constructor rather than assigning into the loaded
    # one (a fresh ``load_rig`` per call, so no test mutates shared
    # loader output either way).
    loaded = load_rig(FT991A_RIG)
    acquisition = loaded.state_acquisition
    assert acquisition is not None
    dropped = FieldPath.global_("tx_state", "tx_target")
    return replace(
        loaded,
        commands={
            verb: spec
            for verb, spec in loaded.commands.items()
            if verb not in ("get_tx_func", "set_tx_func")
        },
        tx_func=None,
        model=_SYNTHETIC_MODEL,
        state_acquisition=replace(
            acquisition,
            capabilities=tuple(
                capability
                for capability in acquisition.capabilities
                if capability.path != dropped
            ),
            field_policies={
                path: policy
                for path, policy in acquisition.field_policies.items()
                if path != dropped
            },
        ),
    )


def test_synthetic_sourceless_profile_declares_no_tx_source() -> None:
    """Premise the sourceless-Yaesu case below stands on: the synthetic
    carries no TX verb, no answer map, and no polled ``tx_target``."""
    config = _sourceless_yaesu_config()
    assert "get_tx_func" not in config.commands
    assert "set_tx_func" not in config.commands
    assert config.tx_func is None
    profile = config.to_profile()
    assert profile.model == _SYNTHETIC_MODEL
    assert not profile.supports_command("get_tx_func")
    assert not profile.supports_command("set_tx_func")
    assert not profile.supports_command("get_tx_target")
    assert profile.state_acquisition is not None
    assert FieldPath.global_("tx_state", "tx_target") not in (
        profile.state_acquisition.pollable_paths()
    )


@pytest.mark.parametrize(
    ("model", "source_command"),
    (
        ("FT-991A", "get_tx_func"),  # sourced since #4584: FT read + [vfo.tx_func]
        ("G7250", None),
        ("IC-7300", None),  # derivation radio: no native read, derives instead
        ("IC-7610", "get_tx_target"),  # native CI-V 1C 03 read (get_tx_target)
    ),
)
def test_tx_target_source_declaration_matrix(
    model: str, source_command: str | None
) -> None:
    """Pin the discriminator the fix keys off: the command each profile's
    ``tx_target`` source (or its absence) hangs on."""
    profile = resolve_radio_profile(model=model)
    if source_command is None:
        assert not profile.supports_command("get_tx_target")
        assert not profile.supports_command("get_tx_func")
    else:
        assert profile.supports_command(source_command)


@asynccontextmanager
async def _serve_sourceless_yaesu(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[WebServer, list[str]]]:
    """Serve the synthetic sourceless profile: real ``YaesuCatRadio`` on the
    CAT simulator answering the FT-991A wire shapes, real ``WebServer`` with
    its production acquisition started. Only the wire is faked."""
    radio = YaesuCatRadio("/dev/fixture", profile=_sourceless_yaesu_config())
    fake = YaesuCatSerialFake.from_profile(ROOT, FT991A_SIM, rig_path=FT991A_RIG)
    wire: list[str] = []
    handle = fake.model.handle_with_outcome

    def logged(line: str) -> Any:
        wire.append(line)
        return handle(line)

    monkeypatch.setattr(fake.model, "handle_with_outcome", logged)

    async def open_port(*args: Any, **kwargs: Any) -> Any:
        return await fake.open_serial_connection()

    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.transport.open_serial_port", open_port
    )
    await radio._transport.connect()
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    try:
        await server.start()
    except BaseException:
        await radio._transport.close()
        raise
    try:
        yield server, wire
    finally:
        await server.stop()
        await radio._transport.close()


@asynccontextmanager
async def _serve_g7250(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[WebServer, _G7250BenchFake]]:
    """Serve the G7250 the way production builds it (CLI factory over the
    bench fake) with the production acquisition started — only the CI-V
    wire is faked."""
    fake = _G7250BenchFake()

    async def opener(*, url: str, baudrate: int, **kwargs: Any) -> Any:
        return await fake.opener(url=url, baudrate=baudrate, **kwargs)

    monkeypatch.setattr(serial_open, "_open_with_idle_lines", opener)
    args = _build_parser().parse_args(
        ["--model", "G7250", "--serial-port", _DEVICE, "status"]
    )
    radio = create_radio(await _build_backend_config(args))
    radio._civ_get_timeout = _ANSWER_WINDOW_S
    radio._civ_min_interval = 0.0  # no hardware pacing sleep (MOR-4075)
    radio._enumerate_serial_ports_fn = lambda: []  # no OS port enumeration
    await asyncio.wait_for(radio.connect(), 30)
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    try:
        await server.start()
    except BaseException:
        await radio.disconnect()
        raise
    try:
        yield server, fake
    finally:
        await server.stop()
        await radio.disconnect()


async def _wait_served_state(
    host: str,
    port: int,
    ready: Callable[[ServerStatePublic], bool],
    description: str,
    timeout: float = 30.0,
) -> ServerStatePublic:
    """Poll the served state until ``ready``; every poll parses through the
    producer's ``ServerStatePublic`` model. Bounded ``asyncio.timeout`` plus
    the poll-interval sleep only (the ``wait_for_state_value`` shape)."""
    async with asyncio.timeout(timeout):
        while True:
            status, _, raw = await _http_get(host, port, "/api/v1/state")
            assert status == 200, f"GET /api/v1/state: HTTP {status}"
            state = ServerStatePublic.model_validate_json(raw)
            if ready(state):
                return state
            await asyncio.sleep(0.05)
    raise AssertionError(f"unreachable: timed out waiting for {description}")


def _is_unsupported(state: ServerStatePublic) -> bool:
    """The served target is the sticky ``unsupported`` fact."""
    target = state.txTarget
    return isinstance(target, UnknownTxTargetPublic) and target.reason == "unsupported"


def _unsupported_with_freq(freq_hz: int) -> Callable[[ServerStatePublic], bool]:
    """Ready when the served target is ``unsupported`` and the poller has
    read that exact real frequency around it (not vacuous: acquisition ran,
    and it ran against this radio's wire personality)."""

    def ready(state: ServerStatePublic) -> bool:
        return _is_unsupported(state) and state.main.freqHz == freq_hz

    return ready


def _unsupported_with_any_freq(state: ServerStatePublic) -> bool:
    """Ready when the served target is ``unsupported`` and the poller has
    observed some real frequency around it (the simulator's default answer
    is not pinned here, only that acquisition ran)."""
    return _is_unsupported(state) and state.main.freqHz is not None


@pytest.mark.realtime(reason="real HTTP socket and the production Yaesu acquisition")
@pytest.mark.asyncio
async def test_yaesu_sourceless_serves_unsupported_over_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The synthetic sourceless Yaesu profile serves the sticky
    ``unsupported`` TX target through production acquisition: the medium
    lane publishes it with no CAT I/O of its own, while freq carries the
    simulator's real answer next to it."""
    async with _serve_sourceless_yaesu(monkeypatch) as (web, wire):
        host, port = _addr(web)
        state = await _wait_served_state(
            host, port, _unsupported_with_any_freq, "sourceless txTarget"
        )
        assert wire, "the poller never touched the CAT wire"
        target = state.txTarget
        assert isinstance(target, UnknownTxTargetPublic)
        assert target.reason == "unsupported"


@pytest.mark.realtime(reason="real HTTP socket and the production CI-V acquisition")
@pytest.mark.asyncio
async def test_g7250_serves_unsupported_over_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G7250 serves the sticky ``unsupported`` TX target through production
    acquisition: the poller falls through to ``_compute_tx_target`` (no
    native ``get_tx_target``, no selected/unselected readback) while the
    bench fake's real 7.138 MHz answer is served next to it."""
    async with _serve_g7250(monkeypatch) as (web, fake):
        host, port = _addr(web)
        state = await _wait_served_state(
            host, port, _unsupported_with_freq(7_138_000), "G7250 txTarget"
        )
        assert fake.commands, "the poller never spoke CI-V"
        target = state.txTarget
        assert isinstance(target, UnknownTxTargetPublic)
        assert target.reason == "unsupported"
