"""The web UI is served only after the initial state acquisition completes.

Covers the four pieces of that gate:

* ``AcquisitionScheduler.unobserved_startup_paths`` /
  ``initial_acquisition_complete`` — the completion predicate over the
  declared, non-``tx_only`` paths;
* ``web/web_startup.py: _observed_paths`` — the receiver-id alias between
  what CI-V ingress writes and what the profile declares;
* ``web/web_startup.py: _start_web_server`` — poller and freshness task
  first, then the gate, then the bind;
* ``cli/__init__.py: _cmd_web`` — the ``Web UI:`` banner line prints only
  once the server reports it started.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import (
    AsyncIterator,
    Callable,
    Iterable,
    Iterator,
    Mapping,
    Sequence,
)
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rigplane.backends._icom_serial_base import _IcomSerialRadioBase
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.core.acquisition_scheduler import (
    AcquisitionRequest,
    AcquisitionScheduler,
    DeclaredCommandDefect,
    resolve_available_when,
)
from rigplane.core.civ import CivFrame
from rigplane.core.state_acquisition_policy import (
    AcquisitionPolicy,
    AvailabilityClause,
    FieldCapability,
    RadioAcquisitionProfile,
)
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
    startup_critical_path,
)
from rigplane.core.state_store import StateStore
from rigplane.core.types import bcd_encode
from rigplane.profiles import get_radio_profile, resolve_radio_profile
from rigplane.profiles.rig_loader import discover_rigs
from rigplane.radio_state import RadioState
from rigplane.runtime._civ_rx import _profile_path_for_observation
from rigplane.runtime.radio import IcomRadio
from rigplane.runtime.radio_initial_state import fetch_initial_state
from rigplane.web import web_startup
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.web_startup import (
    _acquisition_scheduler,
    _await_initial_state_acquisition,
    _observed_paths,
)

FREQ = FieldPath.active("main", "freq_mode", "freq_hz")
MODE = FieldPath.active("main", "freq_mode", "mode")
S_METER = FieldPath.receiver("main", "meters", "s_meter")
POWER = FieldPath.global_("meters", "power")
STARTUP_OPTIONAL = FieldPath.global_("slow_state", "startup_optional")

_REAL_SLEEP = asyncio.sleep


def _source() -> SourceMetadata:
    return SourceMetadata(source="poll_response", provider="test_provider")


def _observation(path: FieldPath, value: object, *, at: float) -> Observation:
    return Observation(
        path=path,
        value=value,
        timestamp_monotonic=at,
        source=_source(),
    )


# ---------------------------------------------------------------------------
# Completion predicate
# ---------------------------------------------------------------------------


def _gate_profile() -> RadioAcquisitionProfile:
    """Profile carrying one path of each shape the predicate must separate.

    * ``FREQ`` — pollable, no ``field_policies`` entry (the TOML
      ``polling_only`` shape).
    * ``MODE`` — pollable, cadence-owned ``field_policies`` entry.
    * ``S_METER`` — ``field_policies`` entry with no cadence (the shape
      ``prime_unobserved`` owns).
    * ``POWER`` — pollable, cadence-owned, ``tx_only``.
    """

    return RadioAcquisitionProfile(
        provider="test_provider",
        capabilities=(
            FieldCapability(path=FREQ, polling=True),
            FieldCapability(path=MODE, polling=True),
            FieldCapability(path=S_METER, command_response_observable=True),
            FieldCapability(path=POWER, polling=True, stream_like=True),
        ),
        field_policies={
            MODE: AcquisitionPolicy(cadence_seconds=5.0, freshness_ttl_seconds=15.0),
            S_METER: AcquisitionPolicy(
                cadence_seconds=None, freshness_ttl_seconds=15.0
            ),
            POWER: AcquisitionPolicy(
                cadence_seconds=0.2,
                freshness_ttl_seconds=0.8,
                tx_only=True,
            ),
        },
    )


def _scheduler() -> AcquisitionScheduler:
    return AcquisitionScheduler(profile=_gate_profile())


def test_predicate_is_complete_once_every_declared_non_tx_path_is_observed() -> None:
    scheduler = _scheduler()

    assert scheduler.unobserved_startup_paths((FREQ, MODE, S_METER)) == ()
    assert scheduler.initial_acquisition_complete((FREQ, MODE, S_METER)) is True


def test_unobserved_non_cadence_policy_field_keeps_the_predicate_incomplete() -> None:
    scheduler = _scheduler()

    assert scheduler.initial_acquisition_complete((FREQ, MODE)) is False
    assert scheduler.unobserved_startup_paths((FREQ, MODE)) == (S_METER,)


def test_unobserved_polling_only_path_keeps_the_predicate_incomplete() -> None:
    """The case ``has_unobserved_policy_fields`` misses.

    ``FREQ`` is pollable and carries no ``field_policies`` entry, so the
    older prime-sweep predicate reports nothing outstanding for it.
    """

    scheduler = _scheduler()
    observed = (MODE, S_METER)

    assert scheduler.has_unobserved_policy_fields(observed) is False
    assert scheduler.initial_acquisition_complete(observed) is False
    assert scheduler.unobserved_startup_paths(observed) == (FREQ,)


def test_unobserved_tx_only_path_does_not_keep_the_predicate_incomplete() -> None:
    scheduler = _scheduler()

    assert POWER in scheduler._profile.pollable_paths()
    assert scheduler.initial_acquisition_complete((FREQ, MODE, S_METER)) is True


def test_startup_optional_path_does_not_keep_the_predicate_incomplete() -> None:
    """An optional field stays pollable without blocking the listener."""

    profile = RadioAcquisitionProfile(
        provider="test_provider",
        capabilities=(
            FieldCapability(
                path=STARTUP_OPTIONAL,
                polling=True,
                startup_required=False,
            ),
        ),
        field_policies={
            STARTUP_OPTIONAL: AcquisitionPolicy(
                cadence_seconds=30.0,
                freshness_ttl_seconds=60.0,
            ),
        },
    )
    scheduler = AcquisitionScheduler(profile=profile)

    assert STARTUP_OPTIONAL in profile.pollable_paths()
    assert scheduler.unobserved_startup_paths(()) == ()
    assert scheduler.initial_acquisition_complete(()) is True


def test_outstanding_paths_are_exactly_the_unobserved_non_tx_only_paths() -> None:
    scheduler = _scheduler()

    assert set(scheduler.unobserved_startup_paths(())) == {FREQ, MODE, S_METER}


# ---------------------------------------------------------------------------
# Startup sweep
# ---------------------------------------------------------------------------


def _sweep_profile(count: int) -> RadioAcquisitionProfile:
    paths = [FieldPath.global_("operator_controls", f"field_{i}") for i in range(count)]
    return RadioAcquisitionProfile(
        provider="test_provider",
        capabilities=tuple(
            FieldCapability(path=path, command_response_observable=True)
            for path in paths
        ),
        # Distinct policies so nothing coalesces into a shared request.
        field_policies={
            path: AcquisitionPolicy(
                cadence_seconds=15.0 + index, freshness_ttl_seconds=25.0
            )
            for index, path in enumerate(paths)
        },
    )


class _GateClock:
    """Fake monotonic clock the gate's own ``sleep`` advances.

    ``stop_at`` ends the run by raising out of the sleep, so a test can
    bound a fake window without waiting for it.
    """

    def __init__(self, *, stop_at: float) -> None:
        self.now = 0.0
        self._stop_at = stop_at
        self.on_tick: Callable[[float], None] | None = None

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.now += delay
        if self.on_tick is not None:
            self.on_tick(self.now)
        if self.now >= self._stop_at:
            raise _GateWindowClosed
        await _REAL_SLEEP(0)


class _GateWindowClosed(Exception):
    pass


@contextmanager
def _fake_gate_clock(clock: _GateClock) -> Iterator[None]:
    """Rebind ``web_startup``'s own ``time``/``asyncio`` names, not the modules.

    ``_await_initial_state_acquisition`` reads ``time.monotonic`` and
    ``asyncio.sleep`` and nothing else from those two names, so swapping the
    module-level names keeps the fake out of every other caller's way.
    """

    with (
        patch.object(web_startup, "time", SimpleNamespace(monotonic=clock.monotonic)),
        patch.object(web_startup, "asyncio", SimpleNamespace(sleep=clock.sleep)),
    ):
        yield


class _PacingScheduler(AcquisitionScheduler):
    """Real scheduler that records when each prime queued which paths."""

    def __init__(self, profile: RadioAcquisitionProfile, clock: _GateClock) -> None:
        super().__init__(profile=profile)
        self._gate_clock = clock
        self.primes: list[tuple[float, tuple[FieldPath, ...]]] = []

    def prime_unobserved(
        self,
        observed_paths: Iterable[FieldPath],
        *,
        reason: str = "prime-unobserved",
        limit: int = 5,
    ) -> tuple[AcquisitionRequest, ...]:
        queued = super().prime_unobserved(observed_paths, reason=reason, limit=limit)
        for request in queued:
            self.primes.append((self._gate_clock.now, request.paths))
        return queued


_PACING_GAP = 0.125  # exact in binary, so the asserted instants are exact


def _critical_wait_profile() -> RadioAcquisitionProfile:
    """One cadence-owned, safety-critical path: ``global.tx_state.ptt``."""

    path = FieldPath.global_("tx_state", "ptt")
    return RadioAcquisitionProfile(
        provider="test_provider",
        capabilities=(FieldCapability(path=path, polling=True),),
        field_policies={
            path: AcquisitionPolicy(cadence_seconds=1.0, freshness_ttl_seconds=15.0),
        },
    )


def _pacing_server(scheduler: AcquisitionScheduler) -> WebServer:
    radio = _CivRadio(scheduler)
    radio._INITIAL_STATE_GAP_SERIAL = _PACING_GAP
    return WebServer(radio, _gated_config())


@pytest.mark.asyncio
async def test_startup_sweep_queues_one_path_per_gap() -> None:
    """One prime per ``_INITIAL_STATE_GAP_SERIAL``, not a whole-profile burst."""

    clock = _GateClock(stop_at=10 * _PACING_GAP)
    scheduler = _PacingScheduler(_sweep_profile(8), clock)
    server = _pacing_server(scheduler)

    with _fake_gate_clock(clock), pytest.raises(_GateWindowClosed):
        await _await_initial_state_acquisition(server, sweep=True)

    assert [instant for instant, _ in scheduler.primes] == [
        index * _PACING_GAP for index in range(8)
    ]
    assert all(len(paths) == 1 for _, paths in scheduler.primes)
    assert len({path for _, paths in scheduler.primes for path in paths}) == 8


@pytest.mark.asyncio
async def test_never_answered_path_is_reprimed_once_per_reprime_interval() -> None:
    """A path the radio never answers is re-requested at most once a second."""

    clock = _GateClock(stop_at=10.0)
    scheduler = _PacingScheduler(_sweep_profile(1), clock)
    server = _pacing_server(scheduler)

    def _answer_nothing(now: float) -> None:
        # What AcquisitionDrain does on a request that times out: the path is
        # freed back to "not observed, not pending" and becomes re-primeable.
        for request in scheduler.pending_requests():
            scheduler.record_acquisition_failure(
                request, reason="test_no_answer", now=now
            )

    clock.on_tick = _answer_nothing

    with _fake_gate_clock(clock), pytest.raises(_GateWindowClosed):
        await _await_initial_state_acquisition(server, sweep=True)

    # 80 gate iterations in the 10 s window; one prime per whole second.
    assert [instant for instant, _ in scheduler.primes] == [
        float(second) for second in range(10)
    ]


@pytest.mark.asyncio
async def test_outstanding_paths_are_logged_then_warned_when_nothing_arrives(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The wait-log cadence itself, on a critical path (MOR-2749).

    A non-critical path no longer waits past the 10 s deadline, so the
    5 s / 60 s log cadence is pinned on ``global.tx_state.ptt`` — a
    safety-critical path the sweep-less gate still waits on indefinitely.
    """
    clock = _GateClock(stop_at=65.0 + _PACING_GAP)
    scheduler = AcquisitionScheduler(profile=_critical_wait_profile())
    server = _pacing_server(scheduler)
    (outstanding,) = scheduler.unobserved_startup_paths(())

    with caplog.at_level(logging.INFO, logger="rigplane.web.web_startup"):
        with _fake_gate_clock(clock), pytest.raises(_GateWindowClosed):
            await _await_initial_state_acquisition(server, sweep=False)

    waiting = [
        record for record in caplog.records if "still waiting on" in record.getMessage()
    ]
    infos = [record for record in waiting if record.levelno == logging.INFO]
    warnings = [record for record in waiting if record.levelno == logging.WARNING]
    # Every 5 s from t=5 to t=65; the last two are past the 60 s no-progress
    # threshold and escalate.
    assert len(infos) == 11
    assert len(warnings) == 2
    assert str(outstanding) in infos[0].getMessage()
    assert str(outstanding) in warnings[0].getMessage()
    assert "no new field for 60s" in warnings[0].getMessage()


# ---------------------------------------------------------------------------
# Receiver-id alias between store spelling and profile spelling
# ---------------------------------------------------------------------------


def test_civ_ingress_counts_as_observed_against_profile_paths() -> None:
    """CI-V writes ``receiver.0``; the IC-7300 profile declares ``receiver.main``.

    Without the alias resolution in ``_observed_paths`` a receiver-scoped
    answer never credits the path the profile declares for it.
    """

    from rigplane.runtime.radio import IcomRadio

    radio = IcomRadio("192.168.1.100", model="IC-7300")
    server = WebServer(radio, _gated_config())
    scheduler = _acquisition_scheduler(server)
    assert scheduler is not None
    # Not a vacuous assertion below: both paths are in the predicate's domain.
    assert {MODE, FREQ} <= set(scheduler.unobserved_startup_paths(()))

    runtime = radio._civ_runtime
    runtime._apply_state_store_observations(
        CivFrame(
            to_addr=0xE0,
            from_addr=0x94,
            command=0x26,
            sub=None,
            data=bytes([0x00, 0x01, 0x01, 0x02]),
        )
    )
    runtime._apply_state_store_observations(
        CivFrame(
            to_addr=0xE0,
            from_addr=0x94,
            command=0x25,
            sub=None,
            data=bytes([0x00]) + bcd_encode(14_074_000),
        )
    )

    stored = {str(field.path) for field in server.command_state_store.snapshot().fields}
    assert {
        "receiver.0.active.freq_mode.mode",
        "receiver.0.active.freq_mode.data_mode",
        "receiver.0.active.freq_mode.filter_num",
        "receiver.0.active.freq_mode.freq_hz",
    } <= stored

    outstanding = scheduler.unobserved_startup_paths(_observed_paths(server, scheduler))
    assert MODE not in outstanding
    assert FREQ not in outstanding


def test_yaesu_observation_paths_need_no_receiver_alias() -> None:
    """FTX-1's adapter already spells receivers the way its profile does."""

    from rigplane.backends.yaesu_cat.observations import _MAIN_FREQ

    profile = resolve_radio_profile(model="FTX-1").state_acquisition
    assert profile is not None
    scheduler = AcquisitionScheduler(profile=profile)

    assert _profile_path_for_observation(profile, _MAIN_FREQ) == _MAIN_FREQ
    assert _MAIN_FREQ in scheduler.unobserved_startup_paths(())
    assert _MAIN_FREQ not in scheduler.unobserved_startup_paths((_MAIN_FREQ,))


# ---------------------------------------------------------------------------
# Web startup ordering
# ---------------------------------------------------------------------------


class _RecordingScheduler:
    """Stands in for :class:`AcquisitionScheduler` at the gate's call sites."""

    def __init__(self, required: Sequence[FieldPath]) -> None:
        self._required = tuple(required)
        # ``_observed_paths`` resolves store paths against this profile.
        self._profile = RadioAcquisitionProfile(
            provider="test_provider",
            capabilities=tuple(
                FieldCapability(path=path, polling=True) for path in required
            ),
        )
        self.prime_limits: list[int | None] = []
        self.on_prime: Callable[[], None] | None = None
        self.startup_defect: DeclaredCommandDefect | None = None

    def unobserved_startup_paths(
        self,
        observed_paths: Iterable[FieldPath],
        *,
        availability: Mapping[FieldPath, bool | None] = MappingProxyType({}),
    ) -> tuple[FieldPath, ...]:
        observed = frozenset(observed_paths)
        return tuple(
            path
            for path in self._required
            if path not in observed and availability.get(path, True) is not False
        )

    def prime_unobserved(
        self,
        observed_paths: Iterable[FieldPath],
        *,
        reason: str = "prime-unobserved",
        limit: int | None = 5,
    ) -> tuple[object, ...]:
        self.prime_limits.append(limit)
        if self.on_prime is not None:
            self.on_prime()
        return ()

    def consecutive_request_timeouts(self, _path: FieldPath) -> int:
        return 0


class _FakeSocket:
    def getsockname(self) -> tuple[str, int]:
        return ("127.0.0.1", 4242)


class _FakeAsyncServer:
    def __init__(self) -> None:
        self.sockets = [_FakeSocket()]

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None


class _CivRadio:
    """Neither ``ObservationPollable`` nor ``StatePollable`` — the drain path."""

    backend_id = "icom_civ"
    # Unresolvable model: WebServer._bootstrap_state_acquisition must not
    # replace the scheduler this test attaches.
    model = "FAKE-CIV"
    capabilities: set[str] = set()
    connected = control_connected = radio_ready = True

    def __init__(self, scheduler: _RecordingScheduler) -> None:
        self.radio_state = RadioState()
        self._acquisition_scheduler = scheduler
        self._INITIAL_STATE_GAP_SERIAL = 0.005

    def supports_command(self, _command: str) -> bool:
        return False


class _TickingObservationPoller:
    """Fills the store over ``ticks`` callbacks, like ``YaesuCatPoller``."""

    def __init__(
        self,
        callback: object,
        paths: Sequence[FieldPath],
        events: list[str],
    ) -> None:
        self._callback = callback
        self._paths = tuple(paths)
        self._events = events

    async def start(self) -> None:
        for index, path in enumerate(self._paths):
            await asyncio.sleep(0.01)
            self._events.append(f"tick-{index + 1}")
            self._callback((_observation(path, index, at=float(index)),))

    async def stop(self) -> None:
        return None

    def bind_provider_generation(self, *, capture: object, advance: object) -> None:
        return None


class _ObservationRadio:
    backend_id = "yaesu_cat"
    model = "FAKE-OBS"
    capabilities: set[str] = set()
    connected = control_connected = radio_ready = True

    def __init__(
        self,
        scheduler: _RecordingScheduler,
        paths: Sequence[FieldPath],
        events: list[str],
    ) -> None:
        self.radio_state = RadioState()
        self._state_store = StateStore()
        self._acquisition_scheduler = scheduler
        self._paths = tuple(paths)
        self._events = events

    @property
    def state_store(self) -> StateStore:
        return self._state_store

    def supports_command(self, _command: str) -> bool:
        return False

    def create_observation_poller(
        self, *, callback: object, **_kwargs: object
    ) -> object:
        return _TickingObservationPoller(callback, self._paths, self._events)


def _gated_config() -> WebConfig:
    return WebConfig(
        host="127.0.0.1",
        port=0,
        discovery=False,
        await_initial_state=True,
    )


@pytest.mark.asyncio
async def test_civ_startup_binds_only_after_the_predicate_is_satisfied() -> None:
    scheduler = _RecordingScheduler((FREQ,))
    radio = _CivRadio(scheduler)
    server = WebServer(radio, _gated_config())
    binds: list[dict[str, object]] = []
    fake_poller = MagicMock(
        drain_tx_safety_commands=AsyncMock(), select_vfo_a_on_connect=AsyncMock()
    )
    started_when_primed: list[bool] = []
    scheduler.on_prime = lambda: started_when_primed.append(fake_poller.start.called)

    async def _bind(*_args: object, **_kwargs: object) -> _FakeAsyncServer:
        binds.append(
            {
                "poller": server._radio_poller,
                "poller_started": fake_poller.start.called,
                "freshness": server._state_store_freshness_task,
            }
        )
        return _FakeAsyncServer()

    with (
        patch("rigplane.web.web_startup.asyncio.start_server", new=_bind),
        patch("rigplane.web.web_startup.RadioPoller", return_value=fake_poller),
    ):
        start = asyncio.create_task(server.start())
        await asyncio.sleep(0.05)
        assert binds == [], "listener bound before the predicate was satisfied"
        assert scheduler.prime_limits, "startup sweep never primed"

        server.command_state_store.apply(_observation(FREQ, 14_074_000, at=1.0))
        await start
        await server.stop()

    assert len(binds) == 1
    assert binds[0]["poller"] is fake_poller, "poller must be built before the bind"
    assert binds[0]["poller_started"] is True, "poller must start before the bind"
    # Stronger than start-before-bind: nothing answers a primed request until
    # the poller's drain is running, so it must be running before the gate
    # queues the first one.
    assert started_when_primed and all(started_when_primed), (
        "poller must start before the gate primes"
    )
    assert binds[0]["freshness"] is not None, "freshness task must start before bind"
    # One path per gap while the gate is open — see
    # test_startup_sweep_queues_one_path_per_gap.
    assert set(scheduler.prime_limits) == {web_startup._STARTUP_GATE_PRIME_LIMIT}


@pytest.mark.asyncio
async def test_observation_pollable_path_binds_without_a_startup_sweep() -> None:
    paths = (FREQ, MODE, S_METER)
    scheduler = _RecordingScheduler(paths)
    events: list[str] = []
    radio = _ObservationRadio(scheduler, paths, events)
    server = WebServer(radio, _gated_config())

    async def _bind(*_args: object, **_kwargs: object) -> _FakeAsyncServer:
        events.append("bind")
        return _FakeAsyncServer()

    with patch("rigplane.web.web_startup.asyncio.start_server", new=_bind):
        await server.start()
        await server.stop()

    assert events == ["tick-1", "tick-2", "tick-3", "bind"]
    # No AcquisitionDrain exists on this path, so a primed request would
    # never reach the wire; the gate is satisfied by the backend's own
    # poller instead.
    assert scheduler.prime_limits == []


# ---------------------------------------------------------------------------
# CLI banner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_ui_banner_prints_only_after_the_server_reports_started(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from rigplane.cli import _build_parser, _cmd_web

    radio = AsyncMock()
    radio.model = "IC-7300"
    before_start: list[str] = []

    class FakeWebServer:
        def __init__(self, _radio: object, _cfg: object) -> None:
            pass

        async def serve_forever(self, *, on_started: object = None) -> None:
            # The real WebServer only returns from start() after the bind;
            # resolve late so anything printed earlier lands here.
            await asyncio.sleep(0.05)
            before_start.append(capsys.readouterr().out)
            assert callable(on_started)
            on_started()
            raise asyncio.CancelledError

    args = _build_parser().parse_args(["--host", "1.2.3.4", "web", "--no-rigctld"])

    with patch("rigplane.web.server.WebServer", FakeWebServer):
        rc = await _cmd_web(radio, args)

    assert rc == 0
    assert "Web UI:" not in before_start[0]
    assert "Web UI:" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# A declared field the radio answers in another shape
# ---------------------------------------------------------------------------

SUB_S_METER = FieldPath.receiver("sub", "meters", "s_meter")


def _dual_meter_profile() -> RadioAcquisitionProfile:
    """Two declared, non-``tx_only`` meter paths — the FTX-1 pair's shape."""

    return RadioAcquisitionProfile(
        provider="yaesu_cat",
        capabilities=(
            FieldCapability(path=S_METER, polling=True, stream_like=True),
            FieldCapability(path=SUB_S_METER, polling=True, stream_like=True),
        ),
        field_policies={
            S_METER: AcquisitionPolicy(cadence_seconds=0.2, freshness_ttl_seconds=0.8),
            SUB_S_METER: AcquisitionPolicy(
                cadence_seconds=0.2, freshness_ttl_seconds=0.8
            ),
        },
    )


class _YaesuAdapterPoller:
    """Drives the real Yaesu adapter on a loop, like ``YaesuCatPoller``."""

    def __init__(
        self, callback: Callable[[Sequence[Observation]], None], radio: object
    ):
        self._callback = callback
        self._radio = radio

    async def start(self) -> None:
        from rigplane.backends.yaesu_cat.observations import YaesuObservationAdapter

        while True:
            adapter = YaesuObservationAdapter(
                self._radio,  # type: ignore[arg-type]
                profile=self._radio._acquisition_scheduler._profile,  # type: ignore[attr-defined]
            )
            self._callback(await adapter.poll_rx_meters())
            await asyncio.sleep(0.001)

    async def stop(self) -> None:
        return None

    def bind_provider_generation(self, *, capture: object, advance: object) -> None:
        return None

    def bind_managed_tx_authority(self, _authority: object) -> None:
        return None


class _MalformedSubMeterRadio:
    """Answers the MAIN meter and returns an unparseable SUB meter frame."""

    backend_id = "yaesu_cat"
    model = "FAKE-OBS"
    capabilities = {"meters", "dual_rx"}
    connected = control_connected = radio_ready = True

    def __init__(self) -> None:
        self.radio_state = RadioState()
        self._state_store = StateStore()
        self._acquisition_scheduler = AcquisitionScheduler(
            profile=_dual_meter_profile()
        )
        self._poll_warned_fields: set[str] = set()
        self._INITIAL_STATE_GAP_SERIAL = 0.005
        self.read_s_meter = AsyncMock(side_effect=self._answer_s_meter)

    @staticmethod
    async def _answer_s_meter(receiver: int = 0) -> int:
        from rigplane.backends.yaesu_cat.parser import CatParseError

        if receiver == 0:
            return 120
        raise CatParseError(
            "SM1{raw:03d};", "SM0000;", "Response does not match pattern"
        )

    @property
    def state_store(self) -> StateStore:
        return self._state_store

    def supports_command(self, _command: str) -> bool:
        return False

    def create_observation_poller(
        self, *, callback: Callable[[Sequence[Observation]], None], **_kwargs: object
    ) -> object:
        return _YaesuAdapterPoller(callback, self)

    # -- what ``cli/__init__.py: _run`` needs before it reaches the gate ----

    async def __aenter__(self) -> "_MalformedSubMeterRadio":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def actuate(
        self, _token: object, _operation: object, *, is_current: Callable[[], bool]
    ) -> object:
        from rigplane.runtime.managed_tx_state import ActuationResult

        return ActuationResult.ACCEPTED if is_current() else ActuationResult.REJECTED

    async def set_ptt(self, _on: bool) -> None:
        return None


@pytest.mark.asyncio
async def test_gate_refuses_to_bind_when_a_declared_read_never_parses() -> None:
    """No listener exists when a declared field is answered in another shape.

    ``SUB_S_METER`` is polled every cycle and every answer is unparseable, so
    no observation for it can ever reach the store. The message names the
    field, the parse template and the frame the radio sent.
    """
    radio = _MalformedSubMeterRadio()
    server = WebServer(radio, _gated_config())
    scheduler = radio._acquisition_scheduler
    assert set(scheduler.unobserved_startup_paths(())) == {S_METER, SUB_S_METER}
    binds: list[str] = []

    async def _bind(*_args: object, **_kwargs: object) -> _FakeAsyncServer:
        binds.append("bind")
        return _FakeAsyncServer()

    with patch("rigplane.web.web_startup.asyncio.start_server", new=_bind):
        with pytest.raises(RuntimeError) as caught:
            await asyncio.wait_for(server.start(), timeout=10.0)
        await server.stop()

    assert binds == []
    message = str(caught.value)
    assert message.startswith("web startup aborted: ")
    assert message.endswith("Refusing to start a half-working server.")
    assert str(SUB_S_METER) in message
    assert "SM1{raw:03d};" in message
    assert "SM0000;" in message
    assert SUB_S_METER in scheduler.unobserved_startup_paths(
        _observed_paths(server, scheduler)
    )


@pytest.mark.asyncio
async def test_gate_binds_when_a_one_off_refusal_answers_on_reread() -> None:
    """MOR-2584: one refusal followed by a normal answer does not abort startup.

    The SUB meter refuses once (the live ``SM1;`` ``?;`` during ``AG1``
    writes, 2026-09-24) and answers the single re-read, so no defect is
    recorded and the listener binds — with the field populated.
    """
    from rigplane.backends.yaesu_cat.transport import CatCommandRejected

    radio = _MalformedSubMeterRadio()
    refusal = CatCommandRejected(
        "Radio rejected command 'SM1;' (returned '?;')", command="SM1;"
    )
    radio.read_s_meter = AsyncMock(side_effect=[120, refusal, 120, 120, 120])
    server = WebServer(radio, _gated_config())
    scheduler = radio._acquisition_scheduler
    assert set(scheduler.unobserved_startup_paths(())) == {S_METER, SUB_S_METER}
    binds: list[str] = []

    async def _bind(*_args: object, **_kwargs: object) -> _FakeAsyncServer:
        binds.append("bind")
        return _FakeAsyncServer()

    with patch("rigplane.web.web_startup.asyncio.start_server", new=_bind):
        await asyncio.wait_for(server.start(), timeout=10.0)
        await server.stop()

    assert binds == ["bind"]
    assert scheduler.startup_defect is None
    assert scheduler.unobserved_startup_paths(_observed_paths(server, scheduler)) == ()


def _refuse_sm1() -> int:
    from rigplane.backends.yaesu_cat.transport import CatCommandRejected

    raise CatCommandRejected(
        "Radio rejected command 'SM1;' (returned '?;')", command="SM1;"
    )


@pytest.mark.asyncio
async def test_gate_still_refuses_a_refusal_the_radio_repeats() -> None:
    """MOR-2584: a command refused on every attempt still aborts startup.

    The SUB meter refuses both the first read and the single re-read, so the
    existing defect record — and its message — is what refuses the bind.
    """
    radio = _MalformedSubMeterRadio()
    radio.read_s_meter = AsyncMock(
        side_effect=lambda receiver=0: 120 if receiver == 0 else _refuse_sm1()
    )
    server = WebServer(radio, _gated_config())
    scheduler = radio._acquisition_scheduler
    binds: list[str] = []

    async def _bind(*_args: object, **_kwargs: object) -> _FakeAsyncServer:
        binds.append("bind")
        return _FakeAsyncServer()

    with patch("rigplane.web.web_startup.asyncio.start_server", new=_bind):
        with pytest.raises(RuntimeError) as caught:
            await asyncio.wait_for(server.start(), timeout=10.0)
        await server.stop()

    assert binds == []
    message = str(caught.value)
    assert message.startswith("web startup aborted: ")
    assert message.endswith("Refusing to start a half-working server.")
    assert str(SUB_S_METER) in message
    assert "SM1;" in message
    assert "?;" in message
    assert SUB_S_METER in scheduler.unobserved_startup_paths(
        _observed_paths(server, scheduler)
    )


# ---------------------------------------------------------------------------
# MOR-2757: an unanswered safety-critical read on the rigctld-client path
# ---------------------------------------------------------------------------


def _rigctld_critical_profile() -> RadioAcquisitionProfile:
    """The medium-lane paths a fake external rigctld must answer to open."""

    from rigplane.backends.rigctld_client.observations import (
        _FILTER,
        _FREQ,
        _MODE,
        _PTT,
    )
    from rigplane.core.tx_observation import OBSERVED_PTT_PATH

    return RadioAcquisitionProfile(
        provider="external_rigctld",
        capabilities=(
            FieldCapability(path=_FREQ, polling=True),
            FieldCapability(path=_MODE, polling=True),
            FieldCapability(path=_FILTER, polling=True),
            FieldCapability(path=_PTT, polling=True),
            FieldCapability(path=OBSERVED_PTT_PATH, polling=True),
        ),
        field_policies={
            path: AcquisitionPolicy(cadence_seconds=1.0, freshness_ttl_seconds=15.0)
            for path in (_FREQ, _MODE, _FILTER, _PTT, OBSERVED_PTT_PATH)
        },
    )


class _TcpRigctldServer:
    """A fake external rigctld served over real TCP (MOR-2757).

    ``silent="t"``: answers every command except that one — the real
    ``RigctldTransport._read_line`` then times out and closes the
    connection exactly as production does, so every counted attempt is a
    real request that reached rigctld. ``drop=True``: accepts each
    connection and closes it on the first command — a plain link drop
    (rigctld restarting) with no unanswered read at all.
    """

    _ANSWERS = {
        "f": ("14074000",),
        "m": ("USB", "2400"),
        "v": ("VFOA",),
        "t": ("0",),
        "l RF": ("128",),
        "l AF": ("128",),
        "l PREAMP": ("0",),
        "l ATT": ("0",),
        "u NB": ("0",),
        "u NR": ("0",),
    }

    def __init__(self, *, silent: str | None = None, drop: bool = False) -> None:
        self.silent = silent
        self.drop = drop
        self.t_requests = 0
        self.t_commands = 0
        self.t_connection_ids: set[int] = set()
        self.connections = 0
        self._conn_serial = 0
        self._server: asyncio.AbstractServer | None = None
        self.host = "127.0.0.1"
        self.port = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._serve, self.host, 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        conn = self._conn_serial
        self._conn_serial += 1
        self.connections += 1
        try:
            while True:
                line = await reader.readline()
                if not line:
                    return
                command = line.decode("ascii").rstrip("\r\n")
                if command == "t":
                    self.t_commands += 1
                if command == self.silent:
                    # Never answer: the real transport's read timeout fires,
                    # closing the connection from the client side.
                    self.t_requests += 1
                    self.t_connection_ids.add(conn)
                    continue
                if self.drop:
                    return
                for reply in self._ANSWERS.get(command, ("0",)):
                    writer.write(f"{reply}\n".encode("ascii"))
                await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass


def _real_rigctld_radio(fake: _TcpRigctldServer) -> "RigctldClientRadio":  # noqa: F821
    """A real rigctld-client radio on the fake TCP server, gate-ready.

    The unresolvable model keeps ``WebServer._bootstrap_state_acquisition``
    from replacing the scheduler the test attaches, and the real
    ``RigctldTransport`` (0.05 s timeout) is what times out on the silent
    ``t`` — the production close-on-timeout path the old fake-radio tests
    bypassed (MOR-2757 review).
    """

    from rigplane.backends.rigctld_client.radio import RigctldClientRadio

    radio = RigctldClientRadio(
        host=fake.host,
        port=fake.port,
        timeout=0.05,
        model="FAKE-RIGCTLD-TCP",
    )
    radio._acquisition_scheduler = AcquisitionScheduler(
        profile=_rigctld_critical_profile()
    )
    return radio


@pytest.mark.asyncio
async def test_rigctld_unanswered_ptt_ends_startup_after_exactly_three_reads() -> None:
    """MOR-2757 (a): a rigctld that never answers ``t`` ends startup after 3 reads.

    The ``t`` read is the PTT path's own read, through the real transport
    against a real TCP server. A read timeout closes the connection, so
    the poller must reconnect before the next cycle: exactly three real
    requests for ``t`` reach rigctld — each on its own connection — and
    the third records the declared-command defect that refuses the bind,
    naming the field and the command.
    """
    fake = _TcpRigctldServer(silent="t")
    await fake.start()
    radio = _real_rigctld_radio(fake)
    await radio.connect()
    server = WebServer(radio, _gated_config())
    scheduler = radio._acquisition_scheduler
    binds: list[str] = []

    async def _bind(*_args: object, **_kwargs: object) -> _FakeAsyncServer:
        binds.append("bind")
        return _FakeAsyncServer()

    try:
        with patch("rigplane.web.web_startup.asyncio.start_server", new=_bind):
            with pytest.raises(RuntimeError) as caught:
                await asyncio.wait_for(server.start(), timeout=15.0)
            await server.stop()
    finally:
        await radio.disconnect()
        await fake.stop()

    assert binds == []
    message = str(caught.value)
    assert message.startswith("web startup aborted: ")
    assert message.endswith("Refusing to start a half-working server.")
    assert "global.tx_state.ptt" in message
    assert "command 't'" in message
    assert scheduler.startup_defect is not None
    assert scheduler.startup_defect.command == "t"
    # Exactly three real requests for ``t`` reached the server — no more:
    # the limit is pinned, not merely reached. Connection drops in between
    # (a timeout closes the connection) never count as unanswered reads.
    assert fake.t_requests == 3
    # Each ``t`` arrived on its own connection: the poller reconnected the
    # transport between attempts, so three counts mean three asks of the
    # radio (MOR-2757 review, reconnect pin).
    assert len(fake.t_connection_ids) == 3


@pytest.mark.asyncio
async def test_rigctld_gate_is_unchanged_when_the_server_answers_everything() -> None:
    """MOR-2757: a rigctld server that answers everything opens as before.

    The same radio with ``t`` answering: no count ever reaches the limit,
    no defect is recorded, and the listener binds once every medium-lane
    path is observed — the pre-MOR-2757 behaviour.
    """
    fake = _TcpRigctldServer()
    await fake.start()
    radio = _real_rigctld_radio(fake)
    await radio.connect()
    server = WebServer(radio, _gated_config())
    scheduler = radio._acquisition_scheduler
    binds: list[str] = []

    async def _bind(*_args: object, **_kwargs: object) -> _FakeAsyncServer:
        binds.append("bind")
        return _FakeAsyncServer()

    try:
        with patch("rigplane.web.web_startup.asyncio.start_server", new=_bind):
            await asyncio.wait_for(server.start(), timeout=10.0)
            # Store-backed asserts run before stop(): the fallback-store
            # teardown this radio takes (no ``state_store`` capability)
            # ends the provider epoch and clears the store.
            assert binds == ["bind"]
            assert scheduler.startup_defect is None
            assert (
                scheduler.unobserved_startup_paths(_observed_paths(server, scheduler))
                == ()
            )
            assert fake.t_commands >= 1
            await server.stop()
    finally:
        await radio.disconnect()
        await fake.stop()


@pytest.mark.asyncio
async def test_rigctld_connection_drop_never_builds_a_declared_defect() -> None:
    """MOR-2757 (b): a dropped connection is link quality, not a defect.

    A server that accepts each connection and closes it on the first
    command — rigctld restarting, with no unanswered read at all — must
    never build a ``DeclaredCommandDefect``: that type is a product
    defect, and link quality reaches the backend as a transport error
    (``core/acquisition_scheduler.py: DeclaredCommandDefect``). The real
    poller runs several failing cycles, reconnecting between them; the
    critical-read tally stays empty.
    """
    from rigplane.backends.rigctld_client.radio import (
        RigctldClientObservationPoller,
    )

    fake = _TcpRigctldServer(drop=True)
    await fake.start()
    radio = _real_rigctld_radio(fake)
    scheduler = radio._acquisition_scheduler
    poller = RigctldClientObservationPoller(
        radio,
        lambda _observations: None,
        medium_interval=0.05,
        slow_interval=1.0,
    )
    try:
        await poller.start()
        await asyncio.sleep(0.5)
    finally:
        await poller.stop()
        await radio.disconnect()
        await fake.stop()

    assert scheduler.startup_defect is None
    assert radio._critical_read_timeouts == {}
    # The cycles really ran against a dropping server and reconnected.
    assert fake.connections >= 3


@pytest.mark.asyncio
async def test_rigctld_operator_disconnect_is_not_undone_by_the_poller() -> None:
    """MOR-2757 review: an intentional disconnect stays disconnected.

    ``/api/v1/radio/disconnect`` closes the transport while the
    observation poller keeps running (it is stopped only at server
    shutdown). The poller's reconnect must not reopen the transport
    after that intentional disconnect — within two medium intervals no
    new connection reaches the fake server and the radio stays
    disconnected — while ``connect()`` clears the flag and polling
    resumes. An *unintended* drop (the test above) still reconnects.
    """
    from rigplane.backends.rigctld_client.radio import (
        RigctldClientObservationPoller,
    )

    fake = _TcpRigctldServer()
    await fake.start()
    radio = _real_rigctld_radio(fake)
    await radio.connect()
    poller = RigctldClientObservationPoller(
        radio,
        lambda _observations: None,
        medium_interval=0.05,
        slow_interval=1.0,
    )
    try:
        await poller.start()
        await asyncio.sleep(0.2)
        connections_before_disconnect = fake.connections
        assert fake.connections >= 1

        await radio.disconnect()
        # More than two medium intervals: enough for a failed cycle and
        # a reconnect attempt the old head performed.
        await asyncio.sleep(0.5)
        assert radio.connected is False
        assert fake.connections == connections_before_disconnect

        await radio.connect()
        await asyncio.sleep(0.2)
        assert radio.connected is True
        assert fake.connections > connections_before_disconnect
    finally:
        await poller.stop()
        await radio.disconnect()
        await fake.stop()


# ---------------------------------------------------------------------------
# MOR-2757: an unanswered safety-critical read on the Yaesu CAT path
# ---------------------------------------------------------------------------


def _yaesu_critical_profile() -> RadioAcquisitionProfile:
    """The four safety-critical paths a fake FTX-1 must answer to open."""

    from rigplane.backends.yaesu_cat.observations import (
        _MAIN_FREQ,
        _MAIN_MODE,
        _PTT,
        _TX_TARGET,
    )

    return RadioAcquisitionProfile(
        provider="yaesu_cat",
        capabilities=(
            FieldCapability(path=_MAIN_FREQ, polling=True),
            FieldCapability(path=_MAIN_MODE, polling=True),
            FieldCapability(path=_PTT, polling=True),
            FieldCapability(path=_TX_TARGET, polling=True),
        ),
        field_policies={
            path: AcquisitionPolicy(cadence_seconds=1.0, freshness_ttl_seconds=15.0)
            for path in (_MAIN_FREQ, _MAIN_MODE, _PTT, _TX_TARGET)
        },
    )


class _YaesuCriticalPoller:
    """Drives the real Yaesu adapter's medium lane, like ``YaesuCatPoller``.

    One cycle reads every critical field; a cycle-level failure (a
    transport timeout re-raised by the adapter) is retried on the next
    interval — what ``YaesuCatPoller._run_poll_cycle`` does with it.
    """

    def __init__(
        self,
        callback: Callable[[Sequence[Observation]], None],
        radio: "_Ftx1CriticalRadio",
    ) -> None:
        self._callback = callback
        self._radio = radio
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        from rigplane.backends.yaesu_cat.observations import YaesuObservationAdapter

        while not self._stopped.is_set():
            adapter = YaesuObservationAdapter(
                self._radio,  # type: ignore[arg-type]
                profile=self._radio._acquisition_scheduler._profile,  # type: ignore[attr-defined]
            )
            try:
                observations = await adapter.poll_medium()
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.getLogger(__name__).warning(
                    "fake FTX-1 medium poll cycle failed", exc_info=True
                )
            else:
                self._callback(observations)
            await asyncio.sleep(0.001)

    async def stop(self) -> None:
        self._stopped.set()
        await asyncio.sleep(0)

    def bind_provider_generation(self, *, capture: object, advance: object) -> None:
        return None

    def bind_managed_tx_authority(self, _authority: object) -> None:
        return None


class _Ftx1CriticalRadio:
    """A fake FTX-1 on the Yaesu observation (``sweep=False``) path.

    Answers every safety-critical read except the one ``never_answers``
    command, which raises the transport's unanswered-read timeout — what
    ``YaesuCatTransport.query`` raises on a radio that stays silent
    (MOR-2757).
    """

    backend_id = "yaesu_cat"
    # Unresolvable model: WebServer must keep the scheduler this fake
    # attaches, not bootstrap its own.
    model = "FAKE-FTX1"
    capabilities = {"tx"}
    connected = control_connected = radio_ready = True

    def __init__(self, *, never_answers: str | None = None) -> None:
        self.radio_state = RadioState()
        self._state_store = StateStore()
        self._acquisition_scheduler = AcquisitionScheduler(
            profile=_yaesu_critical_profile()
        )
        self._poll_warned_fields: set[str] = set()
        self._critical_read_timeouts: dict[FieldPath, int] = {}
        self._INITIAL_STATE_GAP_SERIAL = 0.005
        self._never_answers = never_answers
        self.tx_func_reads = 0

    @property
    def profile(self) -> SimpleNamespace:
        # What ``YaesuObservationAdapter.from_radio`` reads: the radio
        # profile's state-acquisition block.
        return SimpleNamespace(state_acquisition=_yaesu_critical_profile())

    @property
    def state_store(self) -> StateStore:
        return self._state_store

    async def read_freq(self, receiver: int = 0) -> int:
        return 14_074_000

    async def read_mode(self, receiver: int = 0) -> tuple[str, int | None]:
        return "USB", None

    async def read_transmit_state(self) -> object:
        from rigplane.core.tx_observation import TxStateReading

        return TxStateReading(
            value=False,
            verified_readback=True,
            source="yaesu_poll_response",
            attributed="rx",
        )

    async def get_tx_func(self) -> int:
        from rigplane.backends.yaesu_cat.transport import CatTimeoutError

        self.tx_func_reads += 1
        if self._never_answers == "FT;":
            raise CatTimeoutError(
                "Read timeout (0.1s) waiting for ';' terminator", command="FT;"
            )
        return 0

    def supports_command(self, _command: str) -> bool:
        return False

    def create_observation_poller(
        self, *, callback: Callable[[Sequence[Observation]], None], **_kwargs: object
    ) -> object:
        return _YaesuCriticalPoller(callback, self)

    # -- what ``cli/__init__.py: _run`` needs before it reaches the gate ----

    async def __aenter__(self) -> "_Ftx1CriticalRadio":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def actuate(
        self, _token: object, _operation: object, *, is_current: Callable[[], bool]
    ) -> object:
        from rigplane.runtime.managed_tx_state import ActuationResult

        return ActuationResult.ACCEPTED if is_current() else ActuationResult.REJECTED

    async def set_ptt(self, _on: bool) -> None:
        return None


@pytest.mark.asyncio
async def test_yaesu_unanswered_critical_read_fails_startup_after_three_attempts() -> (
    None
):
    """MOR-2757 (a): a fake FTX-1 that never answers ``FT;`` ends startup.

    The ``FT;`` read is the TX target's own read. Three medium cycles with
    no answer reach the owner's attempt limit (MOR-2749, 2026-09-27), and
    the same declared-command defect record a refused read leaves on the
    scheduler refuses the bind — naming the field and the command. Before
    MOR-2757 nothing counted these reads, so the gate waited forever.
    """
    radio = _Ftx1CriticalRadio(never_answers="FT;")
    server = WebServer(radio, _gated_config())
    scheduler = radio._acquisition_scheduler
    binds: list[str] = []

    async def _bind(*_args: object, **_kwargs: object) -> _FakeAsyncServer:
        binds.append("bind")
        return _FakeAsyncServer()

    with patch("rigplane.web.web_startup.asyncio.start_server", new=_bind):
        with pytest.raises(RuntimeError) as caught:
            await asyncio.wait_for(server.start(), timeout=10.0)
        await server.stop()

    assert binds == []
    message = str(caught.value)
    assert message.startswith("web startup aborted: ")
    assert message.endswith("Refusing to start a half-working server.")
    assert "global.tx_state.tx_target" in message
    assert "FT;" in message
    assert scheduler.startup_defect is not None
    # >= 3 pins "not sooner": an abort on the first attempt would stop the
    # reads at 1. The poller keeps cycling until the gate's next poll sees
    # the defect, so a 4th read can land first — that is fine.
    assert radio.tx_func_reads >= 3


@pytest.mark.asyncio
async def test_yaesu_gate_is_unchanged_when_the_fake_answers_everything() -> None:
    """MOR-2757 (b): a fake FTX-1 that answers everything opens as before.

    The same radio with ``FT;`` answering: no count ever reaches the
    limit, no defect is recorded, and the listener binds once every
    critical path is observed — the pre-MOR-2757 behaviour.
    """
    radio = _Ftx1CriticalRadio()
    server = WebServer(radio, _gated_config())
    scheduler = radio._acquisition_scheduler
    binds: list[str] = []

    async def _bind(*_args: object, **_kwargs: object) -> _FakeAsyncServer:
        binds.append("bind")
        return _FakeAsyncServer()

    with patch("rigplane.web.web_startup.asyncio.start_server", new=_bind):
        await asyncio.wait_for(server.start(), timeout=10.0)
        await server.stop()

    assert binds == ["bind"]
    assert scheduler.startup_defect is None
    assert scheduler.unobserved_startup_paths(_observed_paths(server, scheduler)) == ()
    assert radio.tx_func_reads >= 1


@pytest.mark.asyncio
async def test_cli_web_exits_one_and_prints_the_defect(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``rigplane web`` reports the defect on stderr and exits 1.

    Same radio as the gate test above, driven through the real
    ``cli/__init__.py: _run`` so the exit code and the ``Error:`` line come
    from the CLI's own handling, not from a re-raise the test composed.
    """
    from rigplane.cli import _build_parser, _run

    radio = _MalformedSubMeterRadio()

    async def _bind(*_args: object, **_kwargs: object) -> _FakeAsyncServer:
        raise AssertionError("the listener must never bind")

    args = _build_parser().parse_args(["--host", "1.2.3.4", "web", "--no-rigctld"])
    args.web_bridge = None  # no audio bridge; the gate is what is under test
    with (
        patch("rigplane.cli.create_radio", return_value=radio),
        patch("rigplane.cli.check_ports_available"),
        patch("rigplane.web.web_startup.asyncio.start_server", new=_bind),
    ):
        rc = await asyncio.wait_for(_run(args), timeout=10.0)

    assert rc == 1
    err = capsys.readouterr().err
    assert err.startswith("Error: web startup aborted: ")
    assert str(SUB_S_METER) in err
    assert "SM1{raw:03d};" in err
    assert "SM0000;" in err
    assert "Refusing to start a half-working server." in err


# ---------------------------------------------------------------------------
# available_when: a field the profile declares absent in the current mode
# ---------------------------------------------------------------------------


NOTCH_FREQ = FieldPath.receiver("main", "operator_controls", "manual_notch_freq")


def _conditional_profile() -> RadioAcquisitionProfile:
    """``FREQ``/``MODE`` plus one field declared absent while the mode is FM."""

    return RadioAcquisitionProfile(
        provider="test_provider",
        capabilities=(
            FieldCapability(path=FREQ, polling=True),
            FieldCapability(path=MODE, polling=True),
            FieldCapability(path=NOTCH_FREQ, polling=True),
        ),
        field_policies={
            NOTCH_FREQ: AcquisitionPolicy(
                cadence_seconds=1.0,
                freshness_ttl_seconds=2.0,
                available_when=(
                    AvailabilityClause(field=MODE, operator="not_in", value=["FM"]),
                ),
            ),
        },
    )


@pytest.mark.asyncio
async def test_field_declared_absent_in_the_current_mode_does_not_hold_the_gate() -> (
    None
):
    scheduler = AcquisitionScheduler(profile=_conditional_profile())
    server = WebServer(_CivRadio(scheduler), _gated_config())
    server.command_state_store.apply(_observation(FREQ, 14_074_000, at=1.0))
    server.command_state_store.apply(_observation(MODE, "FM", at=1.0))

    # Not vacuous: the same predicate without the availability term still
    # reports the conditional field outstanding.
    assert scheduler.unobserved_startup_paths(_observed_paths(server, scheduler)) == (
        NOTCH_FREQ,
    )

    await asyncio.wait_for(
        _await_initial_state_acquisition(server, sweep=False), timeout=5.0
    )


# ---------------------------------------------------------------------------
# The FTX-1's second receiver: every declared SUB path is unconditional
# (MOR-2511; bench 2026-09-18 answered every SUB read in single receive)
# ---------------------------------------------------------------------------


DUAL_WATCH = FieldPath.global_("tx_state", "dual_watch")
FTX1_SUB_PATHS = (
    FieldPath.active("sub", "freq_mode", "freq_hz"),
    FieldPath.active("sub", "freq_mode", "mode"),
    FieldPath.active("sub", "freq_mode", "filter_width"),
    FieldPath.receiver("sub", "meters", "s_meter"),
    FieldPath.receiver("sub", "operator_controls", "af_level"),
    FieldPath.receiver("sub", "operator_controls", "rf_gain"),
    FieldPath.receiver("sub", "operator_controls", "squelch"),
    FieldPath.receiver("sub", "operator_controls", "repeater_shift"),
)


def _ftx1_scheduler() -> AcquisitionScheduler:
    acquisition = get_radio_profile("FTX-1").state_acquisition
    assert acquisition is not None
    return AcquisitionScheduler(profile=acquisition)


def _ftx1_outstanding(
    store: StateStore, observed: tuple[FieldPath, ...] = ()
) -> tuple[FieldPath, ...]:
    scheduler = _ftx1_scheduler()
    return scheduler.unobserved_startup_paths(
        observed,
        availability=resolve_available_when(scheduler._profile, store.snapshot()),
    )


def test_ftx1_sub_paths_are_outstanding_at_startup_before_dual_receive_is_observed() -> (
    None
):
    """Every SUB path holds the gate open like any unconditional declared
    field -- the operator controls no longer wait for ``FR`` to answer."""

    outstanding = _ftx1_outstanding(StateStore())

    assert set(FTX1_SUB_PATHS).issubset(outstanding)
    # dual_watch is itself an unconditional declared field, still polled for
    # display.
    assert DUAL_WATCH in outstanding


FTX1_DRAIN_PATHS = (
    FieldPath.global_("meters", "vd"),
    FieldPath.global_("meters", "id"),
)


def test_ftx1_drain_meters_hold_the_gate_open_until_they_are_observed() -> None:
    """MOR-2425/T147: declaring VDD/IDD without ``tx_only`` enrols them.

    ``AcquisitionScheduler.unobserved_startup_paths`` drops a ``tx_only``
    path, which is why the four transmit meters never hold the gate. These
    two are not ``tx_only``, so the web start-up wait covers them -- the
    2026-09-08 bench probe is the evidence that a receiving FTX-1 answers
    both, in 8.3-14.4 ms per read.
    """
    outstanding = _ftx1_outstanding(StateStore())

    assert set(FTX1_DRAIN_PATHS).issubset(outstanding)
    # Not vacuous: the transmit meters sharing the same CAT command are
    # excluded from the same set by their ``tx_only``.
    assert FieldPath.global_("meters", "swr") not in outstanding

    observed = _ftx1_outstanding(StateStore(), FTX1_DRAIN_PATHS)

    assert set(FTX1_DRAIN_PATHS).isdisjoint(observed)


@pytest.mark.parametrize("read_only", (False, True))
async def test_application_connection_selection_follows_acquisition_before_listener(
    read_only: bool,
) -> None:
    from test_radio_poller_tx_interlock import ConnectVfoRadio, _observe_ptt
    from rigplane.web.radio_poller import RadioPoller

    radio = ConnectVfoRadio()
    config = _gated_config()
    config.read_only = read_only
    server = WebServer(radio, config)
    events: list[str] = []

    async def acquire(*args: object, **kwargs: object) -> None:
        assert radio.wire.sent_frames == []
        events.append("acquire")
        _observe_ptt(server.command_state_store, False)

    async def bind(*args: object, **kwargs: object) -> _FakeAsyncServer:
        events.append("bind")
        assert len(radio.wire.sent_frames) == (0 if read_only else 5)
        if not read_only:
            assert (
                server.command_state_store.snapshot()
                .field(FieldPath.active_slot("0"))
                .value
                == "A"
            )
        return _FakeAsyncServer()

    with (
        patch.object(RadioPoller, "start"),
        patch.object(web_startup, "_await_initial_state_acquisition", new=acquire),
        patch.object(web_startup.asyncio, "start_server", new=bind),
    ):
        await server.start()
        await server.stop()
    assert events == ["acquire", "bind"]


# ---------------------------------------------------------------------------
# IC-7610: the radio refuses MAIN's filter-shape read (MOR-2733)
# ---------------------------------------------------------------------------

MAIN_FILTER_SHAPE = FieldPath.receiver("main", "operator_controls", "filter_shape")
SUB_FILTER_SHAPE = FieldPath.receiver("sub", "operator_controls", "filter_shape")
# The MOR-2733 bench exchange: MAIN's cmd29 filter-shape read is refused with
# a bare NG that carries no 29 prefix, and SUB's is answered 00 (SHARP).
_MAIN_FILTER_SHAPE_READ = bytes.fromhex("FEFE98E029001656FD")
_SUB_FILTER_SHAPE_READ = bytes.fromhex("FEFE98E029011656FD")
_BARE_NG = bytes.fromhex("FEFEE098FAFD")
_SUB_FILTER_SHAPE_ANSWER = bytes.fromhex("FEFEE0982901165600FD")


def _read_value(request: CivFrame) -> bytes:
    """Value an unscripted read is answered with, after its echoed bytes."""

    if request.command == 0x25 or (request.command, request.sub) == (0x1C, 0x03):
        return bcd_encode(14_074_000)
    if request.command == 0x26:
        return b"\x01\x00\x01"
    if request.command in (0x14, 0x15):
        return b"\x01\x28"
    if (request.command, request.sub) == (0x21, 0x00):
        return b"\x00\x00\x00"
    return b"\x00"


class _Ic7610Wire:
    """Answers the initial state fetch through the real CI-V ingress.

    Stands in for the LAN link at the ``send_civ`` seam
    ``runtime/radio_initial_state.py: fetch_initial_state`` sends through. A
    read in ``script`` gets its scripted reply; every other read is echoed
    back from the radio with :func:`_read_value` appended. Each reply goes
    through ``parse_civ_frame`` into ``CivRuntime._route_civ_frame``.
    """

    def __init__(self, radio: IcomRadio, script: Mapping[bytes, bytes]) -> None:
        self._radio = radio
        self._script = script
        self.sent: list[bytes] = []

    async def send_civ(
        self,
        command: int,
        sub: int | None = None,
        data: bytes | None = None,
        **_kwargs: object,
    ) -> None:
        radio_addr = self._radio._radio_addr
        request = build_civ_frame(
            radio_addr, CONTROLLER_ADDR, command, sub=sub, data=data
        )
        self.sent.append(request)
        reply = self._script.get(request)
        if reply is None:
            reply = build_civ_frame(
                CONTROLLER_ADDR,
                radio_addr,
                command,
                sub=sub,
                data=(data or b"") + _read_value(parse_civ_frame(request)),
            )
        await self._radio._civ_runtime._route_civ_frame(
            parse_civ_frame(reply), generation=self._radio._civ_epoch
        )


# ---------------------------------------------------------------------------
# MOR-2749: only safety-critical fields block opening; the rest stop
# blocking 10 s after the initial fetch
# ---------------------------------------------------------------------------


def _filter_shapes_startup_required(
    scheduler: AcquisitionScheduler,
) -> AcquisitionScheduler:
    """Rebuild the IC-7610 profile with #3815's lines reverted in-test.

    Both filter-shape capabilities come back ``startup_required``, and the
    two filter-shape entries in ``field_policies`` drop their MOR-2748
    ``available_when`` clauses — the availability declaration lives on the
    ``AcquisitionPolicy``, not the ``FieldCapability``. The test pins the
    deadline rule on the pre-#3815/pre-#3828 profile shape and stays correct
    after those changes merged.
    """

    filter_shapes = {MAIN_FILTER_SHAPE, SUB_FILTER_SHAPE}
    flipped = replace(
        scheduler._profile,
        capabilities=tuple(
            replace(capability, startup_required=True)
            if capability.path in filter_shapes
            else capability
            for capability in scheduler._profile.capabilities
        ),
        field_policies={
            path: replace(policy, available_when=())
            if path in filter_shapes
            else policy
            for path, policy in scheduler._profile.field_policies.items()
        },
    )
    return AcquisitionScheduler(profile=flipped)


@pytest.mark.asyncio
async def test_ic7610_opens_while_the_radio_refuses_mains_filter_shape_read() -> None:
    """MOR-2733: every initial read is answered except MAIN's filter shape.

    The startup gate still completes, and MAIN's filter shape stays unread.
    """

    radio = IcomRadio("192.168.1.100", model="IC-7610")
    server = WebServer(radio, _gated_config())
    scheduler = _acquisition_scheduler(server)
    assert scheduler is not None
    # The generation advance runtime/_control_phase.py makes on connect.
    radio._civ_runtime.advance_generation("connect")
    wire = _Ic7610Wire(
        radio,
        {
            _MAIN_FILTER_SHAPE_READ: _BARE_NG,
            _SUB_FILTER_SHAPE_READ: _SUB_FILTER_SHAPE_ANSWER,
        },
    )
    radio.send_civ = wire.send_civ  # type: ignore[method-assign]
    radio._INITIAL_STATE_GAP_LAN = radio._INITIAL_STATE_GAP_SERIAL = 0.0

    await fetch_initial_state(radio)

    # Not vacuous: the initial fetch sent both filter-shape reads.
    assert {_MAIN_FILTER_SHAPE_READ, _SUB_FILTER_SHAPE_READ} <= set(wire.sent)
    clock = _GateClock(stop_at=10.0)
    with _fake_gate_clock(clock):
        try:
            await _await_initial_state_acquisition(server, sweep=False)
        except _GateWindowClosed:
            outstanding = scheduler.unobserved_startup_paths(
                _observed_paths(server, scheduler)
            )
            pytest.fail(
                f"startup still waiting after {clock.now:.0f}s on {outstanding}"
            )

    assert MAIN_FILTER_SHAPE not in _observed_paths(server, scheduler)
    sub_shape = FieldPath.receiver("1", "operator_controls", "filter_shape")
    assert server.command_state_store.snapshot().field(sub_shape).value == 0


@pytest.mark.asyncio
async def test_ic7610_opens_by_the_deadline_while_refusing_mains_filter_shape_read(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MOR-2749 (a): a refused non-critical field opens after 10 s.

    The profile carries #3815's ``startup_optional`` lines removed (reverted
    inside the test), the radio answers every initial read except MAIN's
    filter shape (bare NG), and the gate must complete at the 10 s deadline
    with a WARNING naming the path — with MAIN's filter shape still unread.
    """

    radio = IcomRadio("192.168.1.100", model="IC-7610")
    server = WebServer(radio, _gated_config())
    attached = _acquisition_scheduler(server)
    assert attached is not None
    scheduler = _filter_shapes_startup_required(attached)
    radio._acquisition_scheduler = scheduler
    # Not vacuous: with #3815's lines reverted, both filter shapes block.
    assert {MAIN_FILTER_SHAPE, SUB_FILTER_SHAPE} <= set(
        scheduler.unobserved_startup_paths(())
    )
    # The generation advance runtime/_control_phase.py makes on connect.
    radio._civ_runtime.advance_generation("connect")
    wire = _Ic7610Wire(
        radio,
        {
            _MAIN_FILTER_SHAPE_READ: _BARE_NG,
            _SUB_FILTER_SHAPE_READ: _SUB_FILTER_SHAPE_ANSWER,
        },
    )
    radio.send_civ = wire.send_civ  # type: ignore[method-assign]
    radio._INITIAL_STATE_GAP_LAN = radio._INITIAL_STATE_GAP_SERIAL = 0.05

    await fetch_initial_state(radio)

    # Not vacuous: the initial fetch sent both filter-shape reads.
    assert {_MAIN_FILTER_SHAPE_READ, _SUB_FILTER_SHAPE_READ} <= set(wire.sent)
    clock = _GateClock(stop_at=12.0)
    with (
        caplog.at_level(logging.WARNING, logger="rigplane.web.web_startup"),
        _fake_gate_clock(clock),
    ):
        try:
            await _await_initial_state_acquisition(server, sweep=False)
        except _GateWindowClosed:
            outstanding = scheduler.unobserved_startup_paths(
                _observed_paths(server, scheduler)
            )
            pytest.fail(
                f"startup still waiting after {clock.now:.0f}s on {outstanding}"
            )

    assert clock.now < 12.0
    assert MAIN_FILTER_SHAPE not in _observed_paths(server, scheduler)
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
        and "receiver.main.operator_controls.filter_shape" in record.getMessage()
    ]
    assert warnings, "the deadline WARNING must name the unanswered path"
    assert "after 10s" in warnings[0]


RIGS_DIR = Path(__file__).resolve().parents[1] / "rigs"


def _critical_startup_value(path: FieldPath) -> object:
    from rigplane.core.tx_target import KnownTxTarget

    if path.name == "freq_hz":
        return 14_074_000
    if path.name == "mode":
        return "LSB"
    if path.name == "tx_target":
        return KnownTxTarget(receiver="MAIN", slot=None, frequency_hz=14_074_000)
    return False  # ptt, split


def _shipped_startup_profiles() -> list[pytest.param]:
    params = []
    for model, rig in sorted(discover_rigs(RIGS_DIR).items()):
        profile = rig.to_profile()
        if profile.state_acquisition is None:
            continue
        params.append(pytest.param(model, profile.state_acquisition, id=model))
    assert params, "no shipped profile carries state acquisition"
    return params


@pytest.mark.parametrize(("model", "acquisition"), _shipped_startup_profiles())
@pytest.mark.asyncio
async def test_shipped_profile_opens_by_the_deadline_with_every_non_critical_path_unanswered(
    model: str,
    acquisition: RadioAcquisitionProfile,
) -> None:
    """MOR-2749 (b): no non-critical field of any shipped profile hangs the open.

    Every safety-critical path is answered up front and every other
    startup-required path is never answered at all; the gate must still
    complete at the 10 s deadline, on the fake clock.
    """

    scheduler = AcquisitionScheduler(profile=acquisition)
    outstanding = scheduler.unobserved_startup_paths(())
    non_critical = tuple(
        path for path in outstanding if not startup_critical_path(path)
    )
    critical = tuple(path for path in outstanding if startup_critical_path(path))
    assert non_critical, f"{model}: no startup-required non-critical path to pin"

    server = WebServer(_CivRadio(scheduler), _gated_config())
    for path in critical:
        server.command_state_store.apply(
            _observation(path, _critical_startup_value(path), at=0.0)
        )

    clock = _GateClock(stop_at=11.0)
    with _fake_gate_clock(clock):
        try:
            await _await_initial_state_acquisition(server, sweep=False)
        except _GateWindowClosed:
            still = tuple(
                path
                for path in scheduler.unobserved_startup_paths(
                    _observed_paths(server, scheduler)
                )
                if not startup_critical_path(path)
            )
            pytest.fail(
                f"{model}: non-critical paths still blocking after "
                f"{clock.now:.0f}s: {still}"
            )

    assert clock.now >= 10.0


@pytest.mark.asyncio
async def test_never_answered_critical_path_fails_startup_after_three_attempts() -> (
    None
):
    """MOR-2749 (c): three failed attempts on a critical path end startup.

    ``global.tx_state.ptt`` is cadence-owned and never answered: the initial
    fetch plus two re-reads that time out for real (the MOR-2614
    consecutive-timeout accounting) reach three attempts, and the gate
    fails through the declared-command defect record, naming the field and
    the CI-V command the radio never answered.
    """

    from rigplane.core.acquisition_scheduler import AcquisitionPriority

    PTT = FieldPath.global_("tx_state", "ptt")
    radio = IcomRadio("192.168.1.100", model="IC-7300")
    server = WebServer(radio, _gated_config())
    scheduler = AcquisitionScheduler(profile=_critical_wait_profile())
    radio._acquisition_scheduler = scheduler
    radio._INITIAL_STATE_GAP_LAN = radio._INITIAL_STATE_GAP_SERIAL = 0.05
    assert scheduler.unobserved_startup_paths(()) == (PTT,)

    def _re_read_without_answer(now: float) -> None:
        # What the drain does with a cadence group that never answers: it
        # re-reads on cadence, and each answer window that closes without a
        # reply is a real timeout (MOR-874's grace aside).
        scheduler.ensure_fresh(
            (PTT,),
            max_age=1e-9,
            priority=AcquisitionPriority.BACKGROUND,
            reason="startup-gate",
        )
        for request in scheduler.pending_requests():
            scheduler.record_acquisition_failure(
                request,
                reason="acquisition_request_timeout",
                failed_paths=request.paths,
                now=now,
                link_healthy=False,
            )

    clock = _GateClock(stop_at=6.0)
    clock.on_tick = _re_read_without_answer
    with _fake_gate_clock(clock):
        with pytest.raises(RuntimeError) as caught:
            await _await_initial_state_acquisition(server, sweep=True)

    message = str(caught.value)
    assert message.startswith("web startup aborted: ")
    assert message.endswith("Refusing to start a half-working server.")
    assert "global.tx_state.ptt" in message
    assert "1C 00" in message
    assert "still unanswered after 3 failed reads of its request group" in message
    assert scheduler.startup_defect is not None


@pytest.mark.asyncio
async def test_gate_is_unchanged_when_every_declared_path_answers() -> None:
    """MOR-2749 (d): a radio that answers everything binds as before.

    Nothing is outstanding when the gate is entered, so neither the
    deadline nor the attempt rule engages: no wait, no WARNING, no defect.
    """

    scheduler = _scheduler()
    server = WebServer(_CivRadio(scheduler), _gated_config())
    for path in scheduler.unobserved_startup_paths(()):
        server.command_state_store.apply(_observation(path, 0, at=0.0))

    clock = _GateClock(stop_at=0.05)
    with _fake_gate_clock(clock):
        await _await_initial_state_acquisition(server, sweep=True)

    assert clock.now == 0.0
    assert scheduler.startup_defect is None
    assert scheduler.unobserved_startup_paths(_observed_paths(server, scheduler)) == ()


# ---------------------------------------------------------------------------
# MOR-2841: a radio that answers NOTHING serves; a radio that answers some
# reads but not a critical one still fails (unchanged, above)
# ---------------------------------------------------------------------------


def _silent_link_radio(server: WebServer, scheduler: AcquisitionScheduler) -> IcomRadio:
    """A never-connected radio whose link-down detector has fired.

    The backend's serial watchdog (``_declare_serial_link_down``) forces
    the connection state to ``RECONNECTING`` on consecutive CI-V timeouts —
    the state a powered-off IC-7300 on an open serial port produces. The
    scheduler is attached after ``WebServer`` construction, mirroring
    ``test_never_answered_critical_path_fails_startup_after_three_attempts``.
    """

    from rigplane.runtime._connection_state import RadioConnectionState

    radio = server._radio
    assert isinstance(radio, IcomRadio)
    radio._acquisition_scheduler = scheduler
    radio._INITIAL_STATE_GAP_LAN = radio._INITIAL_STATE_GAP_SERIAL = 0.05
    radio._conn_state = RadioConnectionState.RECONNECTING
    return radio


def _re_read_without_answer(
    scheduler: AcquisitionScheduler, PTT: FieldPath
) -> Callable[[float], None]:
    from rigplane.core.acquisition_scheduler import AcquisitionPriority

    def _tick(now: float) -> None:
        scheduler.ensure_fresh(
            (PTT,),
            max_age=1e-9,
            priority=AcquisitionPriority.BACKGROUND,
            reason="startup-gate",
        )
        for request in scheduler.pending_requests():
            scheduler.record_acquisition_failure(
                request,
                reason="acquisition_request_timeout",
                failed_paths=request.paths,
                now=now,
                link_healthy=False,
            )

    return _tick


@pytest.mark.asyncio
async def test_silent_link_serves_instead_of_failing_startup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MOR-2841 (a): zero fields observed + link down = powered-off radio.

    The same three failed reads that end startup through the named defect
    (see ``test_never_answered_critical_path_fails_startup_after_three_attempts``,
    unchanged) release the gate instead when the link answers NOTHING: one
    WARNING names the radio-not-answering state, no defect is recorded, and
    the wait returns so the listener binds.
    """

    import logging

    PTT = FieldPath.global_("tx_state", "ptt")
    scheduler = AcquisitionScheduler(profile=_critical_wait_profile())
    server = WebServer(IcomRadio("192.168.1.100", model="IC-7300"), _gated_config())
    _silent_link_radio(server, scheduler)
    assert scheduler.unobserved_startup_paths(()) == (PTT,)

    clock = _GateClock(stop_at=6.0)
    clock.on_tick = _re_read_without_answer(scheduler, PTT)
    with caplog.at_level(logging.WARNING, logger="rigplane.web.web_startup"):
        with _fake_gate_clock(clock):
            # Returns instead of raising: no RuntimeError, and no
            # _GateWindowClosed either (the gate released before 6 s).
            await _await_initial_state_acquisition(server, sweep=True)

    assert scheduler.startup_defect is None
    warnings = [
        r
        for r in caplog.records
        if r.levelno >= logging.WARNING and "not answering" in r.getMessage()
    ]
    assert len(warnings) == 1
    assert "no field observed" in warnings[0].getMessage()
    # Nothing fabricated a reading: every store field is a locally
    # reconciled structural fact, not a radio observation (the
    # single-receiver topology's `active` lands at construction).
    assert all(
        field.source.source == "local_reconcile"
        for field in server.command_state_store.snapshot().fields
    )


@pytest.mark.asyncio
async def test_silent_link_serves_even_after_the_reconnect_reopens_the_port(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MOR-2841 stand recheck (2026-09-28): the raced sequence serves too.

    The stand exposed the first cut's blind spot: link-down fires at
    11:35:28, the watchdog's ``soft_reconnect`` reopens the
    present-but-silent port at 11:35:32 (connection state back to
    ``CONNECTED``), and only then does the gate's third failed critical
    read decide at 11:35:34 — against a state that no longer says
    ``RECONNECTING``, so the MOR-2749 abort won. This drives the whole
    sequence for real on the serial backend's own machinery (a fake CI-V
    link that connects fine and never answers), freezes the
    reconnect-completed instant, then runs the gate: the decision must
    rest on the link-down detector having fired, not on the
    instantaneous connection state, so the server serves in the
    not-answering state instead of aborting.
    """

    import logging

    from rigplane import IC_7610_ADDR
    from rigplane.backends.icom7610 import Icom7610SerialRadio
    from rigplane.commands import _CMD_FREQ_GET
    from rigplane.exceptions import TimeoutError as RigplaneTimeoutError
    from rigplane.runtime._connection_state import RadioConnectionState
    from test_icom7610_serial_radio import (
        _FakeSerialCivLink,
        _silence_clock_reset_gap,
        _wait_until,
    )

    # Phase A — the stand sequence through the real backend: silent link,
    # link-down fires, and the reconnect REOPENS the silent port.
    link = _FakeSerialCivLink()  # connect always succeeds; never answers
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=link,
        # Hermetic on any host: no OS enumeration, so rediscovery can never
        # adopt (or probe) a real sibling port (MOR-1453 seams).
        _enumerate_serial_ports_fn=lambda: [],
    )
    radio._civ_min_interval = 0.001
    radio._civ_get_timeout = 0.03
    radio._SERIAL_WATCHDOG_INTERVAL_S = 0.005
    await radio.connect()

    frame = build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, _CMD_FREQ_GET)
    with caplog.at_level(logging.ERROR, logger="rigplane.backends._icom_serial_base"):
        for _ in range(radio._SERIAL_LINK_DOWN_TIMEOUT_THRESHOLD):
            with pytest.raises(RigplaneTimeoutError):
                await radio._send_civ_raw(frame, wait_response=True)
            # MOR-2861 (merge of #3866): back-to-back timed-out commands run
            # the silence clock continuously across their answer windows and
            # declare link-down at 2 x window + one watchdog tick — mid-send,
            # before the consecutive-timeout threshold this phase pins. The
            # quiet gap between sends is the same idiom the MOR-2861 suite
            # uses (``_silence_clock_reset_gap``).
            await _silence_clock_reset_gap(radio)
        # ...and the reopen: connect call #2 succeeds on the silent port, so
        # the state machine is back to CONNECTED before the gate ever
        # decides. (The transient RECONNECTING window lasts one watchdog
        # tick ~5 ms — too short to poll for; the link-down ERROR record
        # below is its durable witness.)
        assert await _wait_until(
            lambda: (
                link.connect_calls >= 2
                and radio.conn_state == RadioConnectionState.CONNECTED
            ),
            timeout_s=2.0,
        )
    link_down_errors = [
        r
        for r in caplog.records
        if r.levelno >= logging.ERROR and "link-down" in r.getMessage()
    ]
    assert len(link_down_errors) == 1
    await radio._stop_civ_data_watchdog()  # freeze the raced instant

    # Phase B — the gate decides three failed critical reads in, exactly
    # as at 11:35:34 on the stand: connection state CONNECTED, zero radio
    # observations, link-down having fired earlier in the same startup.
    PTT = FieldPath.global_("tx_state", "ptt")
    scheduler = AcquisitionScheduler(profile=_critical_wait_profile())
    server = WebServer(radio, _gated_config())
    radio._acquisition_scheduler = scheduler
    radio._INITIAL_STATE_GAP_LAN = radio._INITIAL_STATE_GAP_SERIAL = 0.05
    assert scheduler.unobserved_startup_paths(()) == (PTT,)

    clock = _GateClock(stop_at=6.0)
    clock.on_tick = _re_read_without_answer(scheduler, PTT)
    with caplog.at_level(logging.WARNING, logger="rigplane.web.web_startup"):
        with _fake_gate_clock(clock):
            # Returns instead of raising: no RuntimeError, no
            # _GateWindowClosed (the gate released before 6 s).
            await _await_initial_state_acquisition(server, sweep=True)

    assert scheduler.startup_defect is None
    warnings = [
        r
        for r in caplog.records
        if r.levelno >= logging.WARNING and "not answering" in r.getMessage()
    ]
    assert len(warnings) == 1

    await radio.disconnect()


async def _served_silent_server_with_reopened_port() -> tuple[WebServer, IcomRadio]:
    """The round-3 stand shape: the gate served silently, then the watchdog
    reopened the present-but-silent port — the live state (and
    ``radio_ready``) say connected/ready again while the radio has still
    answered nothing (the 2026-09-28 pty check on mini .77)."""

    from rigplane.runtime._connection_state import RadioConnectionState

    PTT = FieldPath.global_("tx_state", "ptt")
    scheduler = AcquisitionScheduler(profile=_critical_wait_profile())
    server = WebServer(IcomRadio("192.168.1.100", model="IC-7300"), _gated_config())
    radio = _silent_link_radio(server, scheduler)

    clock = _GateClock(stop_at=6.0)
    clock.on_tick = _re_read_without_answer(scheduler, PTT)
    with _fake_gate_clock(clock):
        await _await_initial_state_acquisition(server, sweep=True)

    # The raced instant after the reopen: the transport is back, the CI-V
    # stream counts as healthy, and a fresh-enough liveness tick makes the
    # session count as ready — the exact input the live-state health read
    # misreported as ``connected``/``ready``.
    radio._conn_state = RadioConnectionState.CONNECTED
    radio._civ_transport = object()
    radio._civ_stream_ready = True
    radio._civ_recovering = False
    radio._last_civ_data_received = time.monotonic()
    return server, radio


@pytest.mark.asyncio
async def test_served_silent_link_publishes_not_answering_after_reconnect() -> None:
    """MOR-2841 (round 3): the reopened silent port must not read as healthy.

    After the gate served in the radio-not-answering state AND the link has
    reconnected (``radioLink`` 'connected', the session even counting as
    ready), the published ``radioHealth`` still reports ``stalled`` plus
    ``radio_powered_off_likely`` — the gate's silent-release decision, not
    the live link state, owns the verdict.
    """

    server, radio = await _served_silent_server_with_reopened_port()

    assert radio.radio_ready is True  # not vacuous: live evidence says ready

    health = server._build_radio_health()
    assert health["radioLink"] == "connected"
    assert health["readiness"] == "stalled"
    assert health["likelyCause"] == "radio_powered_off_likely"


@pytest.mark.asyncio
async def test_first_radio_observation_clears_the_not_answering_verdict() -> None:
    """MOR-2841 (round 3): the verdict clears at the radio's first answer.

    The served-silent mark is durable, but the health builder re-reads the
    gate's own no-radio-observation predicate on every publish: one radio
    observation in the store ends the powered-off verdict, and the healthy
    session is reported normally again.
    """

    server, radio = await _served_silent_server_with_reopened_port()
    server.command_state_store.apply(
        _observation(FieldPath.global_("tx_state", "ptt"), False, at=time.monotonic())
    )

    assert server._served_with_silent_link is True  # the mark itself stays

    health = server._build_radio_health()
    assert health["radioLink"] == "connected"
    assert health["readiness"] == "ready"
    assert health["likelyCause"] == "unknown"


@pytest.mark.asyncio
async def test_silent_link_then_radio_answers_completes_acquisition() -> None:
    """MOR-2841: Power ON mid-wait — acquisition completes normally.

    The radio answers nothing at first, then starts answering (the shape
    after Power ON): the critical observation lands, the link recovers, and
    the gate completes with its normal completion line — not the silent
    release, not the named defect.
    """

    from rigplane.runtime._connection_state import RadioConnectionState

    PTT = FieldPath.global_("tx_state", "ptt")
    scheduler = AcquisitionScheduler(profile=_critical_wait_profile())
    server = WebServer(IcomRadio("192.168.1.100", model="IC-7300"), _gated_config())
    radio = _silent_link_radio(server, scheduler)

    def _tick(now: float) -> None:
        if now < 0.1:
            # Still silent: re-reads that never answer.
            _re_read_without_answer(scheduler, PTT)(now)
            return
        # The radio starts answering (for example after Power ON): the
        # critical observation lands and the link recovers.
        radio._conn_state = RadioConnectionState.CONNECTED
        server.command_state_store.apply(_observation(PTT, False, at=now))

    clock = _GateClock(stop_at=3.0)
    clock.on_tick = _tick
    with _fake_gate_clock(clock):
        await _await_initial_state_acquisition(server, sweep=True)

    assert scheduler.startup_defect is None
    assert scheduler.unobserved_startup_paths(_observed_paths(server, scheduler)) == ()


@pytest.mark.asyncio
async def test_silent_link_tx_refused_while_provider_not_ready() -> None:
    """MOR-2841: transmit stays refused while the radio answers nothing.

    Reuses the managed TX authority's own refusal — no new refusal code.
    The arming PTT probe (``0x1C 00``, the same safety-critical read the
    gate waits on) never answers on a silent link, so the provider stays
    not-ready and every key answers ``NOT_READY``
    (``core/tx_safety.py: request_on``; the full wiring is pinned by
    ``test_a_rig_that_never_answers_ptt_stays_managed_and_refuses_tx``).
    """

    from rigplane.core.tx_safety import TxOutcome, TxOwner, TxSafetySupervisor, TxSource

    supervisor = TxSafetySupervisor()
    transition = supervisor.replace_provider(0, ready=False)
    assert transition.outcome is TxOutcome.APPLIED

    key = supervisor.request_on(TxOwner(TxSource.SDK, "session"))

    assert key.outcome is TxOutcome.NOT_READY
    assert key.snapshot.lease_id is None
    assert key.snapshot.provider_ready is False


@pytest.mark.asyncio
async def test_powerstat_on_reaches_the_backend_power_on_path() -> None:
    """MOR-2841: Power ON from the served-not-answering state is not refused.

    With the gate released and the listener bound, the web ``set_powerstat``
    command reaches the backend's power-on path (``radio.set_powerstat`` →
    the CI-V ``0x18`` power-on frame) on a profile that declares
    ``power_on`` — IC-7300 does; nothing at the poller layer refuses it.
    """

    from unittest.mock import AsyncMock

    from rigplane.rigctld.state_cache import StateCache
    from rigplane.web.radio_poller import CommandQueue, RadioPoller, SetPowerstat

    profile = resolve_radio_profile(model="IC-7300")
    assert profile.supports_command("power_on")
    radio = MagicMock()
    radio.profile = profile
    radio.model = profile.model
    radio.capabilities = set(profile.capabilities)
    radio._radio_state = RadioState()
    radio.managed_tx = None
    radio.set_powerstat = AsyncMock()
    poller = RadioPoller(radio, StateCache(), CommandQueue())

    await poller._execute(SetPowerstat(True))  # noqa: SLF001

    radio.set_powerstat.assert_awaited_once_with(True)


@pytest.mark.asyncio
@pytest.mark.usefixtures("observed_rx_dispatch_premise")
async def test_silent_startup_scan_seed_and_echo_never_count_as_radio_observation() -> (
    None
):
    """MOR-2841 (round 4): the stand flip, pinned on the verdict predicate.

    Round 3's stand check flipped the "radio probably off" verdict back on
    because the connect-time scan seed and the fire-and-forget scan echoes
    recorded ``command_response`` — a source
    :func:`runtime_helpers.store_has_radio_observation` counts as the radio
    having answered. MOR-2893 (#3875) relabelled both writers
    ``local_reconcile``; its suite pins the forbidden-source list, while
    this pins the verdict predicate itself: a silent startup that seeds
    the scan facts and then echoes a scan command still observes nothing
    from the radio, so the not-answering verdict survives both writers.
    """

    from rigplane.backends.icom7610 import Icom7610SerialRadio
    from rigplane.web.radio_poller import CommandQueue, RadioPoller, ScanStart
    from rigplane.web.runtime_helpers import store_has_radio_observation
    from test_icom7610_serial_radio import _FakeSerialCivLink

    # Leg 1 — the silent serial startup: the connect-time scan seed on a
    # link that never answers a single byte.
    link = _FakeSerialCivLink()
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=link,
        timeout=0.1,
        _enumerate_serial_ports_fn=lambda: [],
    )
    await radio.connect()
    store = radio.state_store
    poller = RadioPoller(
        radio, CommandQueue(), radio_state=radio._radio_state, state_store=store
    )

    # The startup section of RadioPoller._run(), verbatim order (the same
    # shape as test_mor2893_honest_provenance).
    await poller._fetch_nb_controls()  # noqa: SLF001
    await poller._fetch_mod_inputs()  # noqa: SLF001
    poller._seed_scan_facts_at_connect()  # noqa: SLF001

    assert store_has_radio_observation(store) is False

    # Leg 2 — a fire-and-forget scan echo on the same silent store: the
    # values land (the UI bootstrap depends on them) without ever reading
    # as a radio answer.
    await poller._execute(ScanStart(scan_type=0x01))  # noqa: SLF001

    scanning = store.snapshot().field(FieldPath.global_("slow_state", "scanning"))
    assert scanning.value is True
    assert store_has_radio_observation(store) is False

    await radio.disconnect()


# ---------------------------------------------------------------------------
# MOR-2876: a serial port that cannot be opened at startup serves a
# radio-not-connected state while the backend keeps retrying the port
# ---------------------------------------------------------------------------


def _port_missing_error(device: str) -> BaseException:
    """The error pyserial raises when the device node does not exist."""

    import serial

    return serial.SerialException(
        f"[Errno 2] could not open port {device}: "
        f"[Errno 2] No such file or directory: '{device}'"
    )


def _fast_retry_serial_radio(device: str, link: object) -> _IcomSerialRadioBase:
    from rigplane.backends.icom7610 import Icom7610SerialRadio

    radio = Icom7610SerialRadio(
        device=device,
        civ_link=link,
        # Hermetic on any host: no OS enumeration, so no real port is probed.
        _enumerate_serial_ports_fn=lambda: [],
    )
    # Short retry spacing, so a port that appears is reopened within the
    # test's bounded waits instead of after the production backoff cap.
    radio._SERIAL_WATCHDOG_INTERVAL_S = 0.005
    radio._SERIAL_WATCHDOG_RETRY_S = 0.005
    radio._SERIAL_WATCHDOG_RETRY_MAX_S = 0.01
    return radio


@asynccontextmanager
async def _served_through_cli(radio: object) -> AsyncIterator[WebServer]:
    """Run ``rigplane web`` through the real ``cli/__init__.py: _run``.

    Yields the ``WebServer`` once its ``start()`` has returned, then stops
    it and requires exit code 0. ``serve_forever`` is replaced by start /
    wait / stop so no signal handler is installed, and the listener bind is
    faked.
    """

    from rigplane.cli import _build_parser, _run
    from test_icom7610_serial_radio import _wait_until

    served: list[WebServer] = []
    release = asyncio.Event()

    async def _serve_forever(self: WebServer, *, on_started: object = None) -> None:
        await self.start()
        served.append(self)
        await release.wait()
        await self.stop()

    async def _bind(*_args: object, **_kwargs: object) -> _FakeAsyncServer:
        return _FakeAsyncServer()

    args = _build_parser().parse_args(
        ["--host", "1.2.3.4", "web", "--no-rigctld", "--no-discovery"]
    )
    args.web_bridge = None
    with (
        patch("rigplane.cli.create_radio", return_value=radio),
        patch("rigplane.cli.check_ports_available"),
        patch("rigplane.web.web_startup.asyncio.start_server", new=_bind),
        patch.object(WebServer, "serve_forever", _serve_forever),
    ):
        run = asyncio.create_task(_run(args))
        try:
            await _wait_until(lambda: bool(served) or run.done(), timeout_s=10.0)
            assert served, (
                f"rigplane web exited with {run.result()} instead of serving"
                if run.done()
                else "rigplane web did not finish starting"
            )
            yield served[0]
        finally:
            release.set()
            rc = await asyncio.wait_for(run, timeout=10.0)
    assert rc == 0


def _startup_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "rigplane.web.web_startup"
        and record.levelno >= logging.WARNING
    ]


@pytest.mark.asyncio
async def test_missing_serial_port_serves_not_connected_instead_of_exiting(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """``rigplane web`` with a missing port serves, then recovers the port.

    Before the port exists: one WARNING names the not-connected state, the
    port path and the open error; health reports ``radio_not_connected``;
    transmit is refused; nothing is fabricated. When the port appears, the
    backend's existing watchdog reopens it; while the radio stays silent
    the verdict is MOR-2841's ``radio_powered_off_likely``, and the radio's
    first answer clears it.
    """

    from rigplane.runtime.managed_tx_state import ManagedTxOutcome
    from rigplane.web.runtime_helpers import store_has_radio_observation
    from test_icom7610_serial_radio import (
        _FakeSerialCivLink,
        _freq_response_frame,
        _wait_until,
    )

    device = str(tmp_path / "cu.usbserial-1420")
    link = _FakeSerialCivLink(fail_connect=_port_missing_error(device))
    radio = _fast_retry_serial_radio(device, link)

    with caplog.at_level(logging.WARNING, logger="rigplane.web.web_startup"):
        async with _served_through_cli(radio) as server:
            composition = radio._managed_tx_composition
            assert composition is not None
            # The port keeps being retried by the backend's own watchdog.
            assert await _wait_until(lambda: link.connect_calls >= 3, timeout_s=5.0)

            warnings = _startup_warnings(caplog)
            assert len(warnings) == 1
            assert "not connected" in warnings[0]
            assert device in warnings[0]
            assert "could not open port" in warnings[0]
            assert "not answering" not in warnings[0]
            assert "Power ON" not in warnings[0]
            assert server._served_with_silent_link is False

            health = server._build_radio_health()
            assert health["likelyCause"] == "radio_not_connected"
            assert store_has_radio_observation(server.command_state_store) is False
            snapshot = await composition.authority.snapshot()
            assert snapshot.provider_generation is None
            assert await composition.authority.transmit_on() is (
                ManagedTxOutcome.REJECTED
            )

            # The port appears; the radio behind it stays silent for now.
            link._fail_connect = None
            assert await _wait_until(
                lambda: composition._active_provider is not None, timeout_s=5.0
            )
            # Stop the watchdog now that it has reopened the port: a link-down
            # it could declare on this silent link would park transmit and
            # move the state under the assertions below.
            await radio._stop_civ_data_watchdog()
            assert radio.connected is True
            health = server._build_radio_health()
            assert health["likelyCause"] == "radio_powered_off_likely"

            link.queue_response(_freq_response_frame(14_074_000))
            assert await _wait_until(
                lambda: store_has_radio_observation(server.command_state_store),
                timeout_s=5.0,
            )
            health = server._build_radio_health()
            assert health["readiness"] == "ready"
            assert health["likelyCause"] == "unknown"


@pytest.mark.asyncio
async def test_present_but_silent_port_still_serves_not_answering(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Contrast pin: an open but silent port stays MOR-2841's state.

    Same entry point as the missing-port test. The port opens and the
    radio answers nothing, so the gate's silent release decides, not the
    missing-port release: health is ``radio_powered_off_likely``, never
    ``radio_not_connected``.
    """

    from test_icom7610_serial_radio import _FakeSerialCivLink

    link = _FakeSerialCivLink()  # opens fine, answers no read
    radio = _fast_retry_serial_radio("/dev/ttyUSB0", link)
    # The watchdog must not declare link-down on its own before the startup
    # assert on a loaded runner: that would abort startup for a reason this
    # test is not about. The detector's durable record stands in for it.
    radio._SERIAL_WATCHDOG_INTERVAL_S = 3600.0
    radio._civ_link_down_ever_declared = True

    # One failed read decides a critical path, so the gate reaches the
    # MOR-2841 decision on its first pass instead of after real timeouts.
    with (
        patch.object(web_startup, "_STARTUP_GATE_CRITICAL_ATTEMPTS", 1),
        caplog.at_level(logging.WARNING, logger="rigplane.web.web_startup"),
    ):
        async with _served_through_cli(radio) as server:
            warnings = _startup_warnings(caplog)
            assert len(warnings) == 1
            assert "not answering" in warnings[0]
            assert "not connected" not in warnings[0]
            assert server._served_without_port is False
            health = server._build_radio_health()
            assert health["likelyCause"] == "radio_powered_off_likely"


def test_only_a_recorded_open_failure_makes_reconnecting_a_missing_port() -> None:
    """The missing-port decision needs the backend's recorded open failure.

    ``RECONNECTING`` alone is also MOR-2841's link-down state on an open
    port, and a failed plain ``connect()`` rests ``DISCONNECTED``: neither
    is served as a missing port.
    """

    from rigplane.runtime._connection_state import RadioConnectionState
    from test_icom7610_serial_radio import _FakeSerialCivLink

    radio = _fast_retry_serial_radio("/dev/ttyUSB0", _FakeSerialCivLink())
    server = SimpleNamespace(_radio=radio)
    radio._conn_state = RadioConnectionState.RECONNECTING  # link-down declared
    assert web_startup._serial_port_unopened(server) is False
    radio.last_error = "Failed to reconnect serial session on /dev/ttyUSB0: gone"
    assert web_startup._serial_port_unopened(server) is True
    radio._conn_state = RadioConnectionState.DISCONNECTED
    assert web_startup._serial_port_unopened(server) is False


@pytest.mark.asyncio
async def test_tx_returns_after_the_late_first_open(tmp_path: Path) -> None:
    """Transmit is refused without a port and follows the normal rules after.

    The server session serves a port that cannot be opened without marking
    any transport ready. When the port appears, the watchdog's
    ``soft_reconnect`` re-arms the mounted composition on the new transport
    (``rearm_managed_tx`` -> ``transport_ready``), the same path a runtime
    reconnect takes, and a key is accepted.
    """

    from rigplane.cli import _ManagedTxRadioSession
    from rigplane.runtime.managed_tx_composition import (
        ManagedTxComposition,
        install_managed_tx_composition,
    )
    from rigplane.runtime.managed_tx_state import ManagedTxOutcome
    from test_icom7610_serial_radio import _FakeSerialCivLink, _wait_until

    device = str(tmp_path / "cu.usbserial-1420")
    link = _FakeSerialCivLink(fail_connect=_port_missing_error(device))
    radio = _fast_retry_serial_radio(device, link)
    composition = ManagedTxComposition(radio, config_path=tmp_path / "managed-tx.json")
    install_managed_tx_composition(radio, composition)
    store = StateStore()
    store.begin_provider_generation()
    await composition.bind_state_store(store)  # as cli: _cmd_web does
    session = _ManagedTxRadioSession(radio, composition)

    assert await session.__aenter__() is radio
    try:
        assert composition._live_transport_identity is None
        assert (await composition.authority.snapshot()).provider_generation is None
        assert await composition.authority.transmit_on() is ManagedTxOutcome.REJECTED

        link._fail_connect = None
        assert await _wait_until(
            lambda: composition._active_provider is not None, timeout_s=5.0
        )
        # Stop the watchdog now that it has reopened the port: a link-down on
        # this silent link would park the provider before the key below.
        await radio._stop_civ_data_watchdog()
        assert composition._active_provider is not None
        assert composition._active_provider.transport_identity is radio._civ_transport
        keyed = await composition.authority.submit_ptt(True, "late-open-owner")
        assert keyed.outcome is ManagedTxOutcome.ACCEPTED
        await keyed.wait_settlement()
    finally:
        await session.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_only_a_port_open_failure_is_retried_on_the_server_path(
    tmp_path: Path,
) -> None:
    """``connect()`` keeps its contract; the session serves only an OSError.

    A plain ``connect()`` still raises and leaves no background task. The
    server session retries only an open failure (pyserial's
    ``SerialException`` is an ``OSError``); a missing dependency still
    fails startup.
    """

    from rigplane.cli import _ManagedTxRadioSession
    from rigplane.exceptions import ConnectionError as RigplaneConnectionError
    from rigplane.runtime._connection_state import RadioConnectionState
    from rigplane.runtime.managed_tx_composition import (
        ManagedTxComposition,
        install_managed_tx_composition,
    )
    from test_icom7610_serial_radio import _FakeSerialCivLink

    device = str(tmp_path / "cu.usbserial-1420")
    radio = _fast_retry_serial_radio(
        device, _FakeSerialCivLink(fail_connect=_port_missing_error(device))
    )
    with pytest.raises(RigplaneConnectionError, match="Failed to connect serial"):
        await radio.connect()
    assert radio.conn_state == RadioConnectionState.DISCONNECTED
    assert getattr(radio, "_civ_data_watchdog_task", None) is None

    radio = _fast_retry_serial_radio(
        device,
        _FakeSerialCivLink(fail_connect=ImportError("pyserial-asyncio is required")),
    )
    composition = ManagedTxComposition(radio, config_path=tmp_path / "managed-tx.json")
    install_managed_tx_composition(radio, composition)
    with pytest.raises(RigplaneConnectionError, match="pyserial-asyncio"):
        await _ManagedTxRadioSession(radio, composition).__aenter__()
    assert radio.conn_state == RadioConnectionState.DISCONNECTED
    assert getattr(radio, "_civ_data_watchdog_task", None) is None
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_a_never_connected_radio_opens_no_other_serial_port(
    tmp_path: Path,
) -> None:
    """No sibling-port search before the radio has ever connected.

    This host runs more than one radio: while the configured path is
    missing, the retry must not open (or even enumerate) another port.
    """

    from rigplane.backends.discovery import SerialPortCandidate
    from rigplane.backends.icom7610 import Icom7610SerialRadio
    from rigplane.exceptions import ConnectionError as RigplaneConnectionError
    from test_icom7610_serial_radio import _FakeSerialCivLink

    device = str(tmp_path / "cu.usbserial-1420")  # never created
    sibling = tmp_path / "cu.usbserial-9931"  # matches the derived glob
    sibling.write_text("")
    enumerated: list[None] = []
    probed: list[str] = []

    def _enumerate() -> list[SerialPortCandidate]:
        enumerated.append(None)
        return [SerialPortCandidate(device=str(sibling), description="", hwid=None)]

    async def _probe(port: str) -> int | None:
        probed.append(port)
        return None

    radio = Icom7610SerialRadio(
        device=device,
        civ_link=_FakeSerialCivLink(fail_connect=_port_missing_error(device)),
        _enumerate_serial_ports_fn=_enumerate,
        _civ_identity_probe=_probe,
    )
    with pytest.raises(RigplaneConnectionError):
        await radio.connect()
    with pytest.raises(RigplaneConnectionError, match="Failed to reconnect serial"):
        await radio.soft_reconnect()

    assert probed == []
    assert enumerated == []
    await radio.disconnect()


def test_every_likely_cause_the_classifier_returns_is_in_the_public_schema() -> None:
    """MOR-2919: ``likelyCause`` never leaves the published contract.

    The emitted set is read from ``classify_radio_health``'s own source:
    every ``"likelyCause"`` value in a dict literal it builds, following a
    local name to the expressions assigned to it. A value the walk cannot
    resolve fails the test instead of being skipped.
    """

    import ast
    import inspect
    import textwrap
    import typing

    from rigplane.web.runtime_helpers import classify_radio_health
    from rigplane.web.state_schema import RadioHealthPublic

    tree = ast.parse(textwrap.dedent(inspect.getsource(classify_radio_health)))
    assigned: dict[str, list[ast.expr]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned.setdefault(target.id, []).append(node.value)

    def causes(node: ast.expr) -> set[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, ast.IfExp):
            return causes(node.body) | causes(node.orelse)
        if isinstance(node, ast.Name) and node.id in assigned:
            return set().union(*(causes(value) for value in assigned[node.id]))
        raise AssertionError(f"cannot resolve a likelyCause from {ast.dump(node)}")

    emitted: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "likelyCause":
                    emitted |= causes(value)

    # Not vacuous: one value reached through each resolution rule — a
    # constant, a conditional, and a local name.
    assert {
        "server_unreachable",
        "radio_not_responding",
        "radio_remote_control_unreachable",
    } <= emitted
    annotation = RadioHealthPublic.model_fields["likelyCause"].annotation
    assert emitted <= set(typing.get_args(annotation))
