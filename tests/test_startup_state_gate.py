"""The web UI is served only after the initial state acquisition completes.

Covers the three pieces of that gate:

* ``AcquisitionScheduler.unobserved_startup_paths`` /
  ``initial_acquisition_complete`` — the completion predicate over the
  declared, non-``tx_only`` paths;
* ``web/web_startup.py: _start_web_server`` — poller and freshness task
  first, then the gate, then the bind;
* ``cli/__init__.py: _cmd_web`` — the ``Web UI:`` banner line prints only
  once the server reports it started.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rigplane.core.acquisition_scheduler import AcquisitionScheduler
from rigplane.core.state_acquisition_policy import (
    AcquisitionPolicy,
    FieldCapability,
    RadioAcquisitionProfile,
)
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.radio_state import RadioState
from rigplane.web.server import WebConfig, WebServer

FREQ = FieldPath.active("main", "freq_mode", "freq_hz")
MODE = FieldPath.active("main", "freq_mode", "mode")
S_METER = FieldPath.receiver("main", "meters", "s_meter")
POWER = FieldPath.global_("meters", "power")


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


def test_uncapped_prime_queues_every_unobserved_policy_field_in_one_pass() -> None:
    scheduler = AcquisitionScheduler(profile=_sweep_profile(8))

    queued = scheduler.prime_unobserved(observed_paths=(), limit=None)

    assert len({path for request in queued for path in request.paths}) == 8


def test_default_prime_burst_cap_is_unchanged() -> None:
    scheduler = AcquisitionScheduler(profile=_sweep_profile(8))

    queued = scheduler.prime_unobserved(observed_paths=())

    assert len({path for request in queued for path in request.paths}) == 5


# ---------------------------------------------------------------------------
# Web startup ordering
# ---------------------------------------------------------------------------


class _RecordingScheduler:
    """Stands in for :class:`AcquisitionScheduler` at the gate's call sites."""

    def __init__(self, required: Sequence[FieldPath]) -> None:
        self._required = tuple(required)
        self.prime_limits: list[int | None] = []

    def unobserved_startup_paths(
        self, observed_paths: Iterable[FieldPath]
    ) -> tuple[FieldPath, ...]:
        observed = frozenset(observed_paths)
        return tuple(path for path in self._required if path not in observed)

    def prime_unobserved(
        self,
        observed_paths: Iterable[FieldPath],
        *,
        reason: str = "prime-unobserved",
        limit: int | None = 5,
    ) -> tuple[object, ...]:
        self.prime_limits.append(limit)
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

    async def _bind(*_args: object, **_kwargs: object) -> _FakeAsyncServer:
        binds.append(
            {
                "poller": server._radio_poller,
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
    assert binds[0]["poller"] is fake_poller, "poller must start before the bind"
    assert binds[0]["freshness"] is not None, "freshness task must start before bind"
    # Uncapped while the gate is open (spec: the startup sweep is not
    # subject to _PRIME_UNOBSERVED_BURST_LIMIT).
    assert set(scheduler.prime_limits) == {None}


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
