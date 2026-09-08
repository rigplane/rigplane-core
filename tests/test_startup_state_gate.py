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
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rigplane.core.acquisition_scheduler import AcquisitionRequest, AcquisitionScheduler
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
)
from rigplane.core.state_store import StateStore
from rigplane.core.types import bcd_encode
from rigplane.profiles import resolve_radio_profile
from rigplane.radio_state import RadioState
from rigplane.runtime._civ_rx import _profile_path_for_observation
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
    clock = _GateClock(stop_at=65.0 + _PACING_GAP)
    scheduler = AcquisitionScheduler(profile=_sweep_profile(1))
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
    fake_poller = MagicMock(drain_tx_safety_commands=AsyncMock())
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
# A declared field the backend gives up on
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

    @property
    def state_store(self) -> StateStore:
        return self._state_store

    def supports_command(self, _command: str) -> bool:
        return False

    async def read_s_meter(self, receiver: int = 0) -> int:
        from rigplane.backends.yaesu_cat.parser import CatParseError

        if receiver == 0:
            return 120
        raise CatParseError(
            "SM1{raw:03d};", "SM0000;", "Response does not match pattern"
        )

    def create_observation_poller(
        self, *, callback: Callable[[Sequence[Observation]], None], **_kwargs: object
    ) -> object:
        return _YaesuAdapterPoller(callback, self)


@pytest.mark.asyncio
async def test_gate_completes_when_the_backend_abandons_a_declared_path(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The bind happens even though one declared field never answers usably.

    ``SUB_S_METER`` is polled every cycle and every answer is unparseable, so
    no observation for it can ever reach the store. Without the backend
    telling the scheduler to release it, this gate never returns.
    """
    radio = _MalformedSubMeterRadio()
    server = WebServer(radio, _gated_config())
    scheduler = radio._acquisition_scheduler
    assert set(scheduler.unobserved_startup_paths(())) == {S_METER, SUB_S_METER}
    binds: list[str] = []

    async def _bind(*_args: object, **_kwargs: object) -> _FakeAsyncServer:
        binds.append("bind")
        return _FakeAsyncServer()

    with caplog.at_level(logging.WARNING, logger="rigplane.core.acquisition_scheduler"):
        with patch("rigplane.web.web_startup.asyncio.start_server", new=_bind):
            await asyncio.wait_for(server.start(), timeout=10.0)
            await server.stop()

    assert binds == ["bind"]
    assert scheduler.unobserved_startup_paths(_observed_paths(server, scheduler)) == ()
    abandoned = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING and str(SUB_S_METER) in record.getMessage()
    ]
    assert len(abandoned) == 1


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
