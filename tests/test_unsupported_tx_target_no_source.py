"""MOR-4433 follow-up: a radio with no TX-target source reports "unsupported".

Owner decision: "remove the prohibition, transmit must work" for radios with
one transceiver and no readable TX target (Xiegu G7250 on Icom CI-V, and the
sourceless single-receiver Yaesu shape). One shared mechanism for all radios
and both backends: the backend publishes the sticky
``UnknownTxTarget(reason="unsupported")`` fact through the existing
publication path, so the projection — and every TX-adjacent gate on it — can
tell "no source exists" apart from "a source exists but is unobserved". No
per-radio branch, no new guard, no second authority.

Adaptation note (MOR-4433, after #4584): FT-991A gained a real TX-target
source (``get_tx_func``/``set_tx_func`` + ``[vfo.tx_func]``), so it no
longer belongs to the sourceless set — its real-target coverage lives in
``test_ft991a_profile.py`` and ``test_startup_state_gate.py``. The
sourceless-Yaesu cases here run on a synthetic profile built from the
committed FT-991A config with the source removed (both TX verbs,
``tx_func``, the ``tx_target`` polling path), through the producer's own
loader output — never a raw dict. G7250 keeps covering the Icom CI-V side.

Failing-first: the ``unsupported`` publication tests (Yaesu medium lane for
the synthetic profile, CI-V poller for G7250, HTTP state projection for
both) fail on the base commit — nothing publishes the field, so the
projection falls back to ``not-observed``. The IC-7300/IC-7610 cases and
the PTT/TUNE pins pass before and after: transmit was never gated on
``tx_target`` and must stay that way.

Harness limits, stated plainly: the Yaesu/CIV fakes only speak the FTX-1 and
IC-7300 wire shapes, so G7250 and the synthetic profile run as loaded
profiles over ``SerialMockRadio`` (deterministic stub, real profile data)
rather than over a wire simulator. The WS managed-key path needs the
production managed-TX port (``web_startup`` assembly) and is orthogonal to
this change — the backend never gates PTT on ``tx_target`` — so PTT is
pinned here at the layers that run without it: rigctld ``T 1`` / ``T 0``
over a real TCP socket, the SDK ``set_ptt`` call, and the real HTTP
command/state surface. Existing managed suites own the WS key path.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.backends.yaesu_cat.observations import YaesuObservationAdapter
from rigplane.backends.yaesu_cat.radio import FilterWidthReading
from rigplane.core.command_dispatch import CommandUnsupportedError
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.core.tx_observation import TxStateReading
from rigplane.core.tx_target import KnownTxTarget, UnknownTxTarget
from rigplane.profiles import RadioProfile, resolve_radio_profile
from rigplane.profiles.rig_loader import RigConfig, load_rig
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.runtime.managed_tx_state import (
    ActuationOperation,
    ActuationResult,
    EffectToken,
)
from rigplane.web.radio_poller import CommandQueue, RadioPoller
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.web_startup import (
    _validate_managed_tx,
    attach_managed_tx_composition,
)
from serial_stub import SerialMockRadio
from support.command_consumer import run_command_consumer
from test_web_server import (
    _addr,
    _close_ws,
    _http_get,
    _read_http_response,
    _ws_connect,
    _ws_recv_cmd_response,
    _ws_send_text,
)


ROOT = Path(__file__).resolve().parents[1]
FT991A_RIG = ROOT / "rigs" / "ft991a.toml"

#: Model name stamped on the synthetic sourceless profile, so refusal
#: messages name it instead of the real FT-991A it is derived from.
_SYNTHETIC_MODEL = "FT-991A-NO-TX-SRC"

_FIXED_NOW = 1_000_000.0


def _clock() -> float:
    return _FIXED_NOW


_TX_TARGET_PATH = FieldPath.global_("tx_state", "tx_target")


# ── profile premise: which radios declare a tx_target source ──────────────


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


def _profile_for(name: str) -> RadioProfile:
    """Registry profile by model, or the synthetic sourceless Yaesu one."""
    if name == _SYNTHETIC_MODEL:
        return _sourceless_yaesu_config().to_profile()
    return resolve_radio_profile(model=name)


def test_synthetic_sourceless_profile_declares_no_tx_source() -> None:
    """Premise the sourceless-Yaesu cases below stand on: the synthetic
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
    ``tx_target`` source (or its absence) hangs on. FT-991A sources
    through ``get_tx_func``, IC-7610 through the native ``get_tx_target``;
    G7250 declares neither verb, and IC-7300 derives instead of reading."""
    profile = resolve_radio_profile(model=model)
    if source_command is None:
        assert not profile.supports_command("get_tx_target")
        assert not profile.supports_command("get_tx_func")
    else:
        assert profile.supports_command(source_command)
    acquisition = profile.state_acquisition
    declared_paths = (
        {str(capability.path) for capability in acquisition.capabilities}
        if acquisition is not None
        else set()
    )
    assert ("global.tx_state.tx_target" in declared_paths) is (
        source_command is not None
    )


# ── Yaesu backend: sourceless profile publishes unsupported ────────────────


def _sourceless_observation_radio() -> MagicMock:
    """Duck-typed Yaesu radio carrying the synthetic sourceless profile.

    Mirrors the ``_make_radio`` double in
    ``test_yaesu_cat_observation_adapter.py`` but with the synthetic
    profile — which declares no ``get_tx_func`` verb, so the attribute
    stays absent exactly as on the real radio object. FT-991A itself is
    sourced since #4584; this double keeps the no-source branch covered.
    """
    profile = _sourceless_yaesu_config().to_profile()
    radio = MagicMock(name="sourceless-yaesu-radio")
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
    # The synthetic profile declares no TX-function read verb: the
    # attribute stays absent (a bare MagicMock would auto-create it,
    # which would hide the exact no-verb branch the production radio
    # takes).
    radio.get_tx_func = None
    assert getattr(radio, "get_tx_func", None) is None
    return radio


@pytest.mark.asyncio
async def test_yaesu_medium_lane_publishes_unsupported_without_source() -> None:
    """FAILING FIRST (MOR-4433): the sourceless-Yaesu medium lane must
    carry the sticky ``unsupported`` tx_target observation. On the base
    commit the ``_can_poll`` gate withholds the field entirely, so the
    projection can only read it as ``not-observed``."""
    radio = _sourceless_observation_radio()
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

    ``model`` is a registry model or the synthetic sourceless name, which
    resolves to the synthetic Yaesu profile.
    """
    radio = SerialMockRadio(profile=_profile_for(model), model=None)
    await radio.connect()
    web = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    poller = RadioPoller(
        radio, web.command_queue, state_store=web.command_state_store, clock=_clock
    )
    web._server = await asyncio.start_server(  # noqa: SLF001
        web._accept_client,
        "127.0.0.1",
        0,  # noqa: SLF001
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
@pytest.mark.parametrize("model", ("G7250", _SYNTHETIC_MODEL))
async def test_http_state_projects_unsupported_tx_target(model: str) -> None:
    """FAILING FIRST (MOR-4433): the HTTP state projection must carry the
    ``unsupported`` reason for a sourceless profile — not an absent field
    read back as ``not-observed``. FT-991A left this set when it gained a
    real source; the synthetic keeps the Yaesu side covered."""
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
@pytest.mark.parametrize("model", ("FT-991A", "G7250", _SYNTHETIC_MODEL, "IC-7300"))
async def test_rigctld_key_unkey_stays_open(model: str) -> None:
    """No regression, and the point of the change: rigctld ``T 1`` / ``T 0``
    key and unkey the radio over a real TCP socket on profiles with and
    without a TX-target source — FT-991A is sourced since #4584, G7250 and
    the synthetic stay sourceless. Transmit was never gated on
    ``tx_target``."""
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
@pytest.mark.parametrize("model", ("FT-991A", "G7250", _SYNTHETIC_MODEL, "IC-7300"))
async def test_sdk_ptt_stays_open(model: str) -> None:
    """The Python ``set_ptt`` path stays open on every profile — it never
    consults ``tx_target``."""
    radio = SerialMockRadio(profile=_profile_for(model), model=None)
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
    (
        ("FT-991A", False),
        ("G7250", False),
        (_SYNTHETIC_MODEL, False),
        ("IC-7300", True),
    ),
)
async def test_tune_present_only_where_declared(model: str, has_tuner: bool) -> None:
    """TUNE is a tuner-capability question, not a TX-target question: the
    IC-7300 declares a tuner, FT-991A/G7250/the synthetic declare none —
    so tune is *absent* there, never *blocked*. Pinned at the profile and
    at the real HTTP capabilities payload the frontend's TUNE affordance
    keys off."""
    profile = _profile_for(model)
    assert ("tuner" in set(profile.capabilities)) is has_tuner

    async with _station(model) as (web, _radio, _poller):
        capabilities = await _capabilities_over_http(web)

    # The /api/v1/capabilities payload carries the tag list the frontend
    # TUNE affordance keys off under the top-level "capabilities" key.
    assert ("tuner" in capabilities["capabilities"]) is has_tuner


# ── managed station: real authority, real WS/HTTP, mock radio ───────────────


class _StrictMockRadio(SerialMockRadio):
    """Mock station radio speaking only its profile's declared commands.

    Production parity for the intent path: ``prepare_command_intent``
    asks ``radio.supports_command`` before executing, but the lax
    ``SerialMockRadio`` answers every setter. The double reads support
    from the real loaded profile and refuses the rest, so a refusal the
    test observes is the profile's answer, not the double's generosity.
    """

    def supports_command(self, command_name: str, receiver: int = 0) -> bool:
        del receiver
        return self._profile.supports_command(command_name)

    async def set_tuner_status(self, value: int) -> None:
        if not self._profile.supports_command("set_tuner_status"):
            raise CommandUnsupportedError(
                "command 'set_tuner_status' is not supported by active profile "
                f"{self._profile.model}"
            )
        await super().set_tuner_status(value)


class _MockStationActuator:
    """Managed-TX actuator driving the mock station's recorded PTT writes.

    The production effect lane's only radio seam is ``actuate``; the
    double maps the semantic TX operations onto the mock's real
    ``set_ptt`` (captured before ``install_managed_tx_composition``
    guards it) so the key/unkey order is observable without a wire
    simulator.
    """

    def __init__(
        self,
        set_ptt: Callable[[bool], Awaitable[None]],
        calls: list[bool],
    ) -> None:
        self._set_ptt = set_ptt
        self._calls = calls

    async def actuate(
        self,
        token: EffectToken,
        operation: ActuationOperation,
        *,
        is_current: Callable[[], bool],
    ) -> ActuationResult:
        del token, is_current
        on = operation in (ActuationOperation.PTT_ON, ActuationOperation.TRANSMIT_ON)
        self._calls.append(on)
        await self._set_ptt(on)
        return ActuationResult.ACCEPTED


@asynccontextmanager
async def _managed_station(
    model: str, tmp_path: Path
) -> AsyncIterator[
    tuple[WebServer, _StrictMockRadio, ManagedTxComposition, RadioPoller, list[bool]]
]:
    """Loaded-profile station with the production managed-TX port attached.

    The ``ManagedTxComposition`` + ``install`` + ``bind`` + ``attach``
    assembly mirrors ``joined_station`` in
    ``test_web_controller_activation.py`` (the nearest existing harness
    for the ``web_startup`` managed-TX port); the effect lane drives the
    mock's ``set_ptt`` through ``_MockStationActuator``, the poller
    carries the real authority for the managed currency seat, and the
    queue consumer drains submissions exactly like the running poller
    loop does in production. ``model`` is a registry model or the
    synthetic sourceless name.
    """
    radio = _StrictMockRadio(profile=_profile_for(model), model=None)
    await radio.connect()
    # A stable connection identity: the managed currency seat refuses a
    # queue entry whose captured generation is missing (None).
    radio._civ_epoch = 1  # type: ignore[attr-defined]
    calls: list[bool] = []
    actuator = _MockStationActuator(radio.set_ptt, calls)
    composition = ManagedTxComposition(
        actuator,  # type: ignore[arg-type]
        config_path=tmp_path / f"managed-tx-{model}.json",
    )
    install_managed_tx_composition(radio, composition)
    web = WebServer(radio, WebConfig(host="127.0.0.1", port=0))  # type: ignore[arg-type]
    await composition.transport_ready(radio)
    await composition.bind_state_store(web.command_state_store)
    attach_managed_tx_composition(web, composition)
    # Deterministic construction: the managed authority must own a current
    # provider before the first key. The production validator proves the
    # wiring identity and provider currency here — or fails fast — instead
    # of letting a half-assembled station refuse the key later with
    # "managed transmit authority unavailable" for no product reason.
    assert _validate_managed_tx(web) is composition
    poller = RadioPoller(
        radio,  # type: ignore[arg-type]
        web.command_queue,
        state_store=web.command_state_store,
        clock=_clock,
        managed_tx_authority=composition.authority,
    )
    web._server = await asyncio.start_server(  # noqa: SLF001
        web._accept_client,
        "127.0.0.1",
        0,  # noqa: SLF001
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
        yield web, radio, composition, poller, calls
    finally:
        web._server.close()
        await web._server.wait_closed()
        web._server = None
        tasks = (consumer, *tuple(web._client_tasks))
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await composition.shutdown(asyncio.Event())
        await radio.disconnect()


async def _ws_ptt(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    cmd_id: str,
    state: bool,
) -> dict[str, Any]:
    """Send one WS ``cmd ptt`` frame; return the matching response envelope."""
    await _ws_send_text(
        writer,
        json.dumps(
            {"type": "cmd", "name": "ptt", "id": cmd_id, "params": {"state": state}}
        ),
    )
    return await _ws_recv_cmd_response(reader)


#: The exact refusal the server sends when its 10s admission budget expires
#: before the background consumer drains the entry (production
#: ``enqueue_managed_positive_tx``) — a scheduling stall, never a TX-target
#: gate. Only this surface is retried; any other refusal fails fast.
_STALL_SURFACE = "managed transmit authority unavailable"


async def _ws_ptt_stall_tolerant(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    cmd_id: str,
    state: bool,
    *,
    calls: list[bool],
    actuated: list[bool],
) -> dict[str, Any] | None:
    """Key/unkey over WS, tolerating scheduling stalls but nothing else.

    Returns the response envelope to assert strictly, or ``None`` when a
    refused attempt observably actuated anyway (``calls == actuated``): the
    admission budget expired after the effect landed, so no gate fired and
    the caller asserts on the wire instead of the envelope. A refused
    attempt with no actuation completed server-side (its envelope was
    consumed), so resending cannot double-actuate; any other refusal is a
    product signal and returns at once. A client-side timeout never
    resends — the attempt is still live — it waits out the late envelope
    within the outer bound.
    """
    async with asyncio.timeout(120.0):
        while True:
            try:
                response = await _ws_ptt(reader, writer, cmd_id, state)
            except asyncio.TimeoutError:
                return await _ws_recv_cmd_response(reader, timeout=120.0)
            if response.get("ok") is True:
                return response
            if response.get("message") != _STALL_SURFACE:
                return response
            if calls == actuated:
                return None
            # Stall surface with no actuation: the entry completed without
            # running anything — safe to resend.


async def _http_post_stall_tolerant(
    web: WebServer, path: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """POST one JSON body, retrying only scheduling stalls.

    A stalled loop can push the server's reply past the client's 10s read
    (``asyncio.TimeoutError``) or make the batch step report 504. Only
    those two stall surfaces are retried, within the bound; any other
    status — the 409 refusal pin, the 200 execution pin, or a real 500 —
    returns at once for the caller to assert strictly. ``set_tuner_status``
    is idempotent, so resending is safe.
    """
    async with asyncio.timeout(120.0):
        while True:
            try:
                status, body = await _http_post(web, path, payload)
            except asyncio.TimeoutError:
                continue
            if status == 504:
                continue
            return status, body


async def _http_post(
    web: WebServer, path: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """POST one JSON body to the station over a real socket."""
    host, port = _addr(web)
    body = json.dumps(payload).encode()
    reader, writer = await asyncio.open_connection(host, port)
    try:
        writer.write(
            (
                f"POST {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
                "Connection: close\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n\r\n"
            ).encode()
            + body
        )
        await writer.drain()
        status, _, raw = await asyncio.wait_for(
            _read_http_response(reader), timeout=10.0
        )
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
    return status, json.loads(raw)


@pytest.mark.realtime(reason="real WebSocket socket and managed-TX settlement")
@pytest.mark.asyncio
@pytest.mark.parametrize("model", ("FT-991A", "G7250", _SYNTHETIC_MODEL, "IC-7300"))
async def test_ws_managed_key_unkey_per_model(model: str, tmp_path: Path) -> None:
    """Owner point (MOR-4433): transmit works with no TX-target source.

    A real ``ManagedTxAuthority`` (production ``ManagedTxComposition``
    assembly) wired to the mock station: WS ``cmd ptt`` on/off answers
    ``ok: true`` and keys/unkeys the radio's ``set_ptt`` in order on
    every profile — FT-991A is sourced since #4584, G7250 and the
    synthetic stay sourceless. The HTTP state still projects ``txTarget``
    ``unsupported`` where no source exists (the two sourceless profiles;
    FT-991A's real target is pinned in ``test_ft991a_profile.py`` and
    ``test_startup_state_gate.py``). Not red-first: the backend never
    gated PTT on ``tx_target`` — this pins the point end to end, like the
    rigctld/SDK pins above.
    """
    async with _managed_station(model, tmp_path) as (
        web,
        _radio,
        _composition,
        poller,
        calls,
    ):
        host, port = _addr(web)
        reader, writer, _accept = await _ws_connect(host, port, "/api/v1/ws")
        try:
            keyed = await _ws_ptt_stall_tolerant(
                reader, writer, "key-1", True, calls=calls, actuated=[True]
            )
            if keyed is None:
                # The refused attempt keyed the rig before the budget
                # expired: no gate fired — the wire proves admission.
                assert calls == [True]
            else:
                assert keyed["ok"] is True, f"key refused: {keyed}"
                assert keyed["id"] == "key-1"
                assert keyed["result"] == {"state": True}
            unkeyed = await _ws_ptt_stall_tolerant(
                reader, writer, "key-2", False, calls=calls, actuated=[True, False]
            )
            if unkeyed is None:
                assert calls == [True, False]
            else:
                assert unkeyed["ok"] is True, f"unkey refused: {unkeyed}"
                assert unkeyed["id"] == "key-2"
                assert unkeyed["result"] == {"state": False}
        finally:
            await _close_ws(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

        assert calls == [True, False]

        if model in ("G7250", _SYNTHETIC_MODEL):
            poller._publish_tx_target()  # noqa: SLF001
            state = await _state_over_http(web)
            assert state["txTarget"] == {"status": "unknown", "reason": "unsupported"}


@pytest.mark.realtime(reason="real HTTP socket and intent execution to the mock")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "refused"),
    (
        ("FT-991A", True),
        ("G7250", True),
        (_SYNTHETIC_MODEL, True),
        ("IC-7300", False),
    ),
)
async def test_http_tuner_refused_unsupported_where_undeclared(
    model: str, refused: bool, tmp_path: Path
) -> None:
    """TUNE is absent, never TX-target-blocked, at the execution level.

    The strict double speaks only its profile's declared commands, so
    the observation is the production refusal: POSTing
    ``set_tuner_status`` on FT-991A/G7250/the synthetic answers 409
    ``unsupported_command`` naming the profile — never a tx-target
    reason — while the IC-7300 executes the write. Red first: against
    the lax ``SerialMockRadio`` (no ``supports_command``) the same POST
    collapses to a 500 ``command_failed`` instead of the 409.
    """
    async with _managed_station(model, tmp_path) as (
        web,
        radio,
        _composition,
        _poller,
        _calls,
    ):
        status, body = await _http_post_stall_tolerant(
            web,
            "/api/v1/commands",
            {"name": "set_tuner_status", "params": {"value": 1}, "id": "tune-1"},
        )

        if refused:
            assert status == 409, f"expected 409, got {status}: {body}"
            assert body["ok"] is False
            assert body["error"] == "unsupported_command"
            assert "not supported by active profile" in body["message"]
            for key in ("tx_target", "tx-target", "txTarget"):
                assert key not in body["message"], (
                    f"tuner refusal must never name the TX target: {body['message']}"
                )
            assert radio._tuner_status == 0  # type: ignore[attr-defined]
        else:
            assert status == 200, f"expected 200, got {status}: {body}"
            assert body["ok"] is True
            assert radio._tuner_status == 1  # type: ignore[attr-defined]
