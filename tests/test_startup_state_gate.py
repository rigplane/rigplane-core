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
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
        capabilities=(
            FieldCapability(path=path, polling=True),
        ),
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
            if path not in observed and availability.get(path, True) is True
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

    Both filter-shape capabilities come back ``startup_required``, so the
    test pins the deadline rule on the pre-#3815 profile shape and stays
    correct after that profile change merges.
    """

    flipped = replace(
        scheduler._profile,
        capabilities=tuple(
            replace(capability, startup_required=True)
            if capability.path in {MAIN_FILTER_SHAPE, SUB_FILTER_SHAPE}
            else capability
            for capability in scheduler._profile.capabilities
        ),
    )
    return AcquisitionScheduler(profile=flipped)


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
    if path.name == "freq_hz":
        return 14_074_000
    if path.name == "mode":
        return "LSB"
    if path.name == "tx_target":
        return "main"
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


@pytest.mark.parametrize(
    ("model", "acquisition"), _shipped_startup_profiles()
)
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
    assert "no answer after 3 attempts" in message
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
