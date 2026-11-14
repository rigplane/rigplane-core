"""MOR-4433 follow-up: a radio with no TX-target source reports "unsupported".

Owner decision: "remove the prohibition, transmit must work" for radios with
one transceiver and no readable TX target (FT-991A, Xiegu G7250). One shared
mechanism for all radios and both backends: the backend publishes the sticky
``UnknownTxTarget(reason="unsupported")`` fact through the existing
publication path, so the projection — and every TX-adjacent gate on it — can
tell "no source exists" apart from "a source exists but is unobserved". No
per-radio branch, no new guard, no second authority.

Failing-first: the ``unsupported`` publication tests (Yaesu medium lane for
FT-991A, CI-V poller for G7250, HTTP state projection for both) fail on the
base commit — nothing publishes the field, so the projection falls back to
``not-observed``. The IC-7300/IC-7610 cases and the PTT/TUNE pins pass before
and after: transmit was never gated on ``tx_target`` and must stay that way.

Harness limits, stated plainly: the Yaesu/CIV fakes only speak the FTX-1 and
IC-7300 wire shapes, so FT-991A/G7250 run as loaded profiles over
``SerialMockRadio`` (deterministic stub, real profile data) rather than over a
wire simulator. The WS managed-key path needs the production managed-TX port
(``web_startup`` assembly) and is orthogonal to this change — the backend
never gates PTT on ``tx_target`` — so PTT is pinned here at the layers that
run without it: rigctld ``T 1`` / ``T 0`` over a real TCP socket, the SDK
``set_ptt`` call, and the real HTTP command/state surface. Existing managed
suites own the WS key path.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.backends.yaesu_cat.observations import YaesuObservationAdapter
from rigplane.backends.yaesu_cat.radio import FilterWidthReading
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.core.tx_observation import TxStateReading
from rigplane.core.tx_target import KnownTxTarget, UnknownTxTarget
from rigplane.profiles import get_radio_profile, resolve_radio_profile
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer
from rigplane.web.radio_poller import CommandQueue, RadioPoller
from rigplane.web.server import WebConfig, WebServer
from serial_stub import SerialMockRadio
from support.command_consumer import run_command_consumer
from test_web_server import _addr, _http_get


_FIXED_NOW = 1_000_000.0


def _clock() -> float:
    return _FIXED_NOW


_TX_TARGET_PATH = FieldPath.global_("tx_state", "tx_target")


# ── profile premise: which radios declare a tx_target source ──────────────


@pytest.mark.parametrize(
    ("model", "declares_source"),
    (
        ("FT-991A", False),
        ("G7250", False),
        ("IC-7300", False),  # derivation radio: no native read, derives instead
        ("IC-7610", True),  # native CI-V 1C 03 read (get_tx_target)
    ),
)
def test_tx_target_source_declaration_matrix(model: str, declares_source: bool) -> None:
    """Pin the discriminator the fix keys off: only IC-7610 declares a
    native ``tx_target`` source command. FT-991A/G7250 declare none — no
    ``get_tx_target``/``get_tx_func`` — and IC-7300 derives instead."""
    profile = resolve_radio_profile(model=model)
    assert profile.supports_command("get_tx_target") is declares_source
    acquisition = profile.state_acquisition
    declared_paths = (
        {str(capability.path) for capability in acquisition.capabilities}
        if acquisition is not None
        else set()
    )
    assert ("global.tx_state.tx_target" in declared_paths) is declares_source


# ── Yaesu backend: FT-991A publishes unsupported through the medium lane ───


def _ft991a_observation_radio() -> MagicMock:
    """Duck-typed Yaesu radio carrying the real FT-991A profile.

    Mirrors the ``_make_radio`` double in
    ``test_yaesu_cat_observation_adapter.py`` but with the FT-991A profile —
    which declares no ``get_tx_func`` verb, so the attribute stays absent
    exactly as on the real radio object.
    """
    profile = get_radio_profile("FT-991A")
    radio = MagicMock(name="ft991a-radio")
    radio.profile = profile
    radio.capabilities = set(profile.capabilities)
    radio.read_freq = AsyncMock(return_value=14_074_000)
    radio.read_mode = AsyncMock(return_value=("USB", None))
    radio.read_transmit_state = AsyncMock(
        return_value=TxStateReading(False, "rx", "yaesu_poll_response", True)
    )
    radio.read_filter_width = AsyncMock(return_value=500)
    radio.read_filter_width_reading = AsyncMock(
        return_value=FilterWidthReading(500, False)
    )
    radio.read_narrow = AsyncMock(return_value=True)
    # The FT-991A profile declares no TX-function read verb: the attribute
    # stays absent (a bare MagicMock would auto-create it, which would hide
    # the exact no-verb branch the production radio takes).
    radio.get_tx_func = None
    assert getattr(radio, "get_tx_func", None) is None
    return radio


@pytest.mark.asyncio
async def test_yaesu_medium_lane_publishes_unsupported_without_source() -> None:
    """FAILING FIRST (MOR-4433): the FT-991A medium lane must carry the
    sticky ``unsupported`` tx_target observation. On the base commit the
    ``_can_poll`` gate withholds the field entirely, so the projection can
    only read it as ``not-observed``."""
    radio = _ft991a_observation_radio()
    adapter = YaesuObservationAdapter.from_radio(radio, clock=_clock)

    observations = await adapter.poll_medium()

    by_path = {str(item.path): item.value for item in observations}
    assert str(_TX_TARGET_PATH) in by_path, (
        f"no tx_target observation published for a sourceless profile: {sorted(by_path)}"
    )
    assert by_path[str(_TX_TARGET_PATH)] == UnknownTxTarget(reason="unsupported")


# ── CI-V poller: G7250 publishes unsupported; IC-7610 stays native-owned ────


def _poller_radio(model: str) -> MagicMock:
    """MagicMock radio carrying a real resolved profile (mirrors
    ``_make_radio`` in ``test_radio_poller_coverage.py``)."""
    profile = resolve_radio_profile(model=model)
    radio = MagicMock(name=f"{model}-poller-radio")
    radio.profile = profile
    radio.model = profile.model
    radio.capabilities = set(profile.capabilities)
    radio.managed_tx = None
    radio.send_civ = AsyncMock()
    radio.set_ptt = AsyncMock()
    return radio


def _apply_input(
    store: StateStore, path: FieldPath, value: Any, *, generation: int
) -> None:
    store.apply(
        Observation(
            path=path,
            value=value,
            source=SourceMetadata(source="test", provider="test"),
            timestamp_monotonic=_FIXED_NOW,
            max_age=60.0,
            provider_generation=generation,
        )
    )


def _seed_ic7300_derivation_inputs(store: StateStore, generation: int) -> None:
    """Seed identity/split/selected+unselected freq — all four derivation
    inputs — plus the sibling mode leaves the retention bootstrap needs
    (mirrors ``_seed_tx_target_ready`` in ``test_radio_poller_coverage.py``,
    pinned to the fixed test clock)."""
    _apply_input(store, FieldPath.active_slot("0"), "A", generation=generation)
    _apply_input(
        store, FieldPath.global_("tx_state", "split"), False, generation=generation
    )
    for builder, freq in (
        (FieldPath.active, 14_250_000),
        (FieldPath.unselected, 7_150_000),
    ):
        _apply_input(
            store, builder("0", "freq_mode", "freq_hz"), freq, generation=generation
        )
        _apply_input(
            store, builder("0", "freq_mode", "mode"), "USB", generation=generation
        )


def _published_tx_target(store: StateStore) -> UnknownTxTarget | KnownTxTarget | None:
    try:
        field = store.field(_TX_TARGET_PATH)
    except KeyError:
        return None
    value = field.value
    assert isinstance(value, (UnknownTxTarget, KnownTxTarget)), (
        f"tx_target holds a non-contract value: {value!r}"
    )
    return value


@pytest.mark.asyncio
async def test_civ_poller_publishes_unsupported_for_g7250() -> None:
    """FAILING FIRST (MOR-4433): the G7250 poller must publish the sticky
    ``unsupported`` fact instead of writing nothing. On the base commit the
    ``vfo_readback`` early return skips the store write entirely."""
    radio = _poller_radio("G7250")
    store = StateStore()
    store.begin_provider_generation()
    poller = RadioPoller(radio, CommandQueue(), state_store=store, clock=_clock)

    poller._publish_tx_target()  # noqa: SLF001

    assert _published_tx_target(store) == UnknownTxTarget(reason="unsupported")


@pytest.mark.asyncio
async def test_civ_poller_leaves_native_ic7610_target_alone() -> None:
    """No regression: IC-7610 declares the native 1C 03 read, which owns
    the field through the CI-V observation path — the poller must not become
    a second writer for it."""
    radio = _poller_radio("IC-7610")
    store = StateStore()
    store.begin_provider_generation()
    poller = RadioPoller(radio, CommandQueue(), state_store=store, clock=_clock)

    poller._publish_tx_target()  # noqa: SLF001

    assert _published_tx_target(store) is None


@pytest.mark.asyncio
async def test_civ_poller_still_derives_ic7300_target() -> None:
    """No regression: the IC-7300 derivation still produces a known target
    from fresh inputs."""
    radio = _poller_radio("IC-7300")
    store = StateStore()
    store.begin_provider_generation()
    poller = RadioPoller(radio, CommandQueue(), state_store=store, clock=_clock)
    generation = store.provider_generation
    _seed_ic7300_derivation_inputs(store, generation)

    poller._publish_tx_target()  # noqa: SLF001

    assert _published_tx_target(store) == KnownTxTarget(
        receiver="MAIN", slot="A", frequency_hz=14_250_000
    )


# ── station: real HTTP state + real rigctld T 1/T 0 over real sockets ─────────


@asynccontextmanager
async def _station(
    model: str,
) -> AsyncIterator[tuple[WebServer, SerialMockRadio, RadioPoller]]:
    """A loaded-profile station: ``SerialMockRadio`` (real profile data,
    deterministic stub I/O) behind a real ``WebServer`` on a real socket,
    with a queue consumer executing each submitted command (the role the
    running poller loop plays in production). Mirrors ``_station`` in
    ``test_band_4m.py`` minus the band-plan overlay this file does not need.
    """
    radio = SerialMockRadio(model=model)
    await radio.connect()
    web = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    poller = RadioPoller(
        radio, web.command_queue, state_store=web.command_state_store, clock=_clock
    )
    web._server = await asyncio.start_server(  # noqa: SLF001
        web._accept_client, "127.0.0.1", 0  # noqa: SLF001
    )
    entries: list[Any] = []
    executed = asyncio.Event()
    consumer = asyncio.create_task(
        run_command_consumer(
            web.command_queue,
            poller._execute_queued_entry,  # noqa: SLF001
            entries=entries,
            executed=executed,
        )
    )
    try:
        yield web, radio, poller
    finally:
        web._server.close()
        await web._server.wait_closed()
        web._server = None
        tasks = (consumer, *tuple(web._client_tasks))
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await radio.disconnect()


async def _state_over_http(web: WebServer) -> dict[str, Any]:
    host, port = _addr(web)
    status, _, body = await _http_get(host, port, "/api/v1/state")
    assert status == 200, f"GET /api/v1/state: HTTP {status}"
    return json.loads(body)


async def _capabilities_over_http(web: WebServer) -> dict[str, Any]:
    host, port = _addr(web)
    status, _, body = await _http_get(host, port, "/api/v1/capabilities")
    assert status == 200, f"GET /api/v1/capabilities: HTTP {status}"
    return json.loads(body)


async def _rigctld_exchange(host: str, port: int, line: bytes) -> bytes:
    """One rigctld command over a real TCP socket; returns the first reply line."""
    reader, writer = await asyncio.open_connection(host, port)
    try:
        writer.write(line + b"\n")
        await writer.drain()
        return (await asyncio.wait_for(reader.readline(), timeout=5.0)).strip()
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ("FT-991A", "G7250"))
async def test_http_state_projects_unsupported_tx_target(model: str) -> None:
    """FAILING FIRST (MOR-4433): the HTTP state projection must carry the
    ``unsupported`` reason for a sourceless profile — not an absent field
    read back as ``not-observed``."""
    async with _station(model) as (web, _radio, poller):
        poller._publish_tx_target()  # noqa: SLF001

        state = await _state_over_http(web)

    assert state["txTarget"] == {"status": "unknown", "reason": "unsupported"}


@pytest.mark.asyncio
async def test_http_state_still_derives_ic7300_target() -> None:
    """No regression: the IC-7300 HTTP projection still derives a known
    target once its inputs are observed."""
    async with _station("IC-7300") as (web, _radio, poller):
        generation = web.command_state_store.provider_generation
        _seed_ic7300_derivation_inputs(web.command_state_store, generation)
        poller._publish_tx_target()  # noqa: SLF001

        state = await _state_over_http(web)

    target = state["txTarget"]
    assert target["status"] == "known"
    assert target["receiver"] == "MAIN"
    assert target["frequencyHz"] == 14_250_000


def _seed_observed_rx(store: StateStore) -> None:
    """State the RF premise the TX interlock seat requires: the radio was
    observed receiving. Mirrors ``_seed_fresh_rx`` in ``test_web_server.py``;
    without it the seat correctly fails closed (``RPRT -9``) on UNKNOWN."""
    store.apply(
        Observation(
            path=FieldPath.global_("tx_state", "ptt"),
            value=False,
            source=SourceMetadata(source="test", provider="test"),
            timestamp_monotonic=store.snapshot().generated_at_monotonic,
            max_age=60.0,
            provider_generation=store.provider_generation,
        )
    )


def _record_ptt(radio: SerialMockRadio) -> list[bool]:
    """Record SDK-level PTT writes; ``SerialMockRadio`` only keeps the last
    state, so wrap the method to observe the wire order."""
    calls: list[bool] = []
    original = radio.set_ptt

    async def recording(on: bool) -> None:
        calls.append(on)
        await original(on)

    radio.set_ptt = recording  # type: ignore[method-assign]
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ("FT-991A", "G7250", "IC-7300"))
async def test_rigctld_key_unkey_stays_open(model: str) -> None:
    """No regression, and the point of the change: rigctld ``T 1`` / ``T 0``
    key and unkey the radio over a real TCP socket on profiles with and
    without a TX-target source. Transmit was never gated on ``tx_target``."""
    async with _station(model) as (_web, radio, _poller):
        ptt_calls = _record_ptt(radio)
        server = RigctldServer(
            radio,  # type: ignore[arg-type]
            RigctldConfig(host="127.0.0.1", port=0),
            allow_not_ready=True,
        )
        await server.start()
        try:
            _seed_observed_rx(server._rig_handler._state_store)  # noqa: SLF001
            sockets = server._server.sockets  # noqa: SLF001
            port = sockets[0].getsockname()[1]
            assert await _rigctld_exchange("127.0.0.1", port, b"T 1") == b"RPRT 0"
            assert await _rigctld_exchange("127.0.0.1", port, b"T 0") == b"RPRT 0"
        finally:
            await server.stop()

    assert ptt_calls == [True, False]


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ("FT-991A", "G7250", "IC-7300"))
async def test_sdk_ptt_stays_open(model: str) -> None:
    """The Python ``set_ptt`` path stays open on every profile — it never
    consults ``tx_target``."""
    radio = SerialMockRadio(model=model)
    await radio.connect()
    try:
        ptt_calls = _record_ptt(radio)
        await radio.set_ptt(True)
        await radio.set_ptt(False)
    finally:
        await radio.disconnect()

    assert ptt_calls == [True, False]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "has_tuner"),
    (("FT-991A", False), ("G7250", False), ("IC-7300", True)),
)
async def test_tune_present_only_where_declared(model: str, has_tuner: bool) -> None:
    """TUNE is a tuner-capability question, not a TX-target question: the
    IC-7300 declares a tuner, FT-991A/G7250 declare none — so tune is
    *absent* there, never *blocked*. Pinned at the profile and at the real
    HTTP capabilities payload the frontend's TUNE affordance keys off."""
    profile = resolve_radio_profile(model=model)
    assert ("tuner" in set(profile.capabilities)) is has_tuner

    async with _station(model) as (web, _radio, _poller):
        capabilities = await _capabilities_over_http(web)

    # The /api/v1/capabilities payload carries the tag list the frontend
    # TUNE affordance keys off under the top-level "capabilities" key.
    assert ("tuner" in capabilities["capabilities"]) is has_tuner
