"""YaesuCatPoller — polling scheduler for YaesuCatRadio.

Three polling groups with different intervals share a single serial lock:

- **Fast  (75 ms):**  S-meter during RX; ALC/Power/COMP/SWR during TX.
- **Medium (200 ms):** Frequency, mode, PTT — changes at human speed.
- **Slow  (1000 ms):** AGC, AF/RF/squelch levels — rarely change.

Each group runs as an independent asyncio task.  The shared lock prevents
concurrent serial requests so the CAT bus is never overwhelmed.

Usage::

    poller = YaesuCatPoller(radio, callback=on_state_update)
    await poller.start()
    ...
    await poller.pause()
    await poller.resume()
    await poller.stop()
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Callable, Sequence

from rigplane.core.command_service import _is_yaesu_cat_readback, _yaesu_receiver_alias
from rigplane.core.observation_adapter import ProviderObservationAdapter
from rigplane.core.state_acquisition_policy import RadioAcquisitionProfile
from rigplane.core.state_pipeline_contracts import CommandIntent, FieldPath
from rigplane.core.tx_target import KnownTxTarget, UnknownTxTarget
from rigplane.runtime.tx_interlock import (
    DeferredTxCommandLane,
    RfState,
    TxInterlockDeferredOutcome,
    TxInterlockDisposition,
    TxInterlockDispositionOverrides,
    classify_tx_interlock,
    evaluate_tx_interlock,
)

from ...core.exceptions import TimeoutError as RigplaneTimeoutError
from ...exceptions import CommandError
from ...exceptions import ConnectionError as RadioConnectionError
from ...radio_state import YaesuStateExtension
from .transport import CatTimeoutError

if TYPE_CHECKING:
    from ..._poller_types import CommandQueue, CommandQueueEntry
    from ...radio_state import RadioState
    from ...core.state_pipeline_contracts import CommandSource, Observation
    from .radio import YaesuCatRadio

__all__ = ["YaesuCatPoller"]

logger = logging.getLogger(__name__)

_FAST_INTERVAL: float = 0.075  # 13.3 Hz
_MEDIUM_INTERVAL: float = 0.200  # 5 Hz
_SLOW_INTERVAL: float = 1.000  # 1 Hz
_EMA_ALPHA: float = 0.3
_TX_TARGET_PATH = FieldPath.global_("tx_state", "tx_target")
_ACTIVE_RECEIVER_PATH = FieldPath.global_("slow_state", "active")


def _exact_readback_value_matches(observed: Any, expected: Any) -> bool:
    if type(observed) not in (bool, int, float, str) or type(observed) is not type(
        expected
    ):
        return False
    return bool(observed == expected)


class YaesuCatPoller:
    """Polling scheduler for :class:`~.radio.YaesuCatRadio`.

    Args:
        radio:           Connected :class:`YaesuCatRadio` instance.
        callback:        Called with the current :class:`RadioState` after
                         every successful poll.
        fast_interval:   Seconds between fast (S-meter) polls.
        medium_interval: Seconds between medium (freq/mode/PTT) polls.
        slow_interval:   Seconds between slow (AGC/levels) polls.
        ema_alpha:       EMA smoothing factor for S-meter (0 = disabled,
                         0.3 = moderate smoothing, 1.0 = no smoothing).
    """

    def __init__(
        self,
        radio: "YaesuCatRadio",
        callback: Callable[["RadioState"], None] | None = None,
        *,
        observation_callback: Callable[[Sequence["Observation"]], None] | None = None,
        command_queue: "CommandQueue | None" = None,
        fast_interval: float = _FAST_INTERVAL,
        medium_interval: float = _MEDIUM_INTERVAL,
        slow_interval: float = _SLOW_INTERVAL,
        ema_alpha: float = _EMA_ALPHA,
    ) -> None:
        self._radio = radio
        self._callback = callback
        self._observation_callback = observation_callback
        self._command_queue = command_queue
        self._fast_interval = fast_interval
        self._medium_interval = medium_interval
        self._slow_interval = slow_interval
        self._ema_alpha = ema_alpha

        # Capability set from TOML — used to gate poll items.
        self._caps: set[str] = getattr(radio, "capabilities", set())

        # Shared serial access lock — one request in flight at a time.
        self._lock: asyncio.Lock = asyncio.Lock()
        # Clear = paused, set = running.
        self._paused: asyncio.Event = asyncio.Event()
        self._paused.set()
        self._reconnecting = False

        self._tasks: list[asyncio.Task[None]] = []

        # EMA state per receiver (None until first sample).
        self._ema_s_main: float | None = None
        self._ema_s_sub: float | None = None
        self._last_ptt = bool(getattr(radio.radio_state, "ptt", False))
        self._ptt_observation: Observation | None = None
        self._ptt_connection_generation: tuple[str | None, int] | None = None
        self._tx_target_generation = self._current_tx_target_generation()
        self._tx_target_invalidation: tuple[str | None, int, int | None] | None = None
        self._tx_target_known_generation: tuple[str | None, int] | None = None
        self._capture_provider_generation: Callable[[], int] | None = None
        self._advance_provider_generation: Callable[[], int] | None = None
        self._pending_receiver_select: CommandQueueEntry | None = None
        self._pending_readbacks: dict[FieldPath, tuple[Any, ...]] = {}
        self._deferred_tx_lane = DeferredTxCommandLane()
        self._deferred_tx_entry: CommandQueueEntry | None = None
        self._deferred_tx_generation: (
            tuple[tuple[str | None, int], int | None] | None
        ) = None

    def bind_provider_generation(
        self,
        *,
        capture: Callable[[], int],
        advance: Callable[[], int] | None = None,
    ) -> None:
        if self._capture_provider_generation is not None:
            self._cancel_deferred_entry("provider binding replaced")
        self._capture_provider_generation = capture
        self._advance_provider_generation = advance

    def _captured_provider_generation(self) -> int | None:
        capture = self._capture_provider_generation
        return None if capture is None else capture()

    def _provider_generation_is_current(self, generation: int | None) -> bool:
        capture = self._capture_provider_generation
        return capture is None or generation == capture()

    def _invalidate_ptt_observation(self) -> None:
        self._ptt_observation = None
        self._ptt_connection_generation = None
        self._last_ptt = False

    def _current_ptt_observation(
        self, *, now: float | None = None
    ) -> Observation | None:
        observation = self._ptt_observation
        if observation is None or type(observation.value) is not bool:
            self._invalidate_ptt_observation()
            return None
        provider_generation = self._captured_provider_generation()
        if provider_generation is not None and (
            observation.provider_generation != provider_generation
        ):
            self._invalidate_ptt_observation()
            return None
        if self._ptt_connection_generation != self._current_tx_target_generation():
            self._invalidate_ptt_observation()
            return None
        timestamp = time.monotonic() if now is None else now
        if (
            observation.max_age is None
            or timestamp < observation.timestamp_monotonic
            or timestamp >= observation.timestamp_monotonic + observation.max_age
        ):
            self._invalidate_ptt_observation()
            return None
        return observation

    def _current_rf_state(self) -> RfState:
        observation = self._current_ptt_observation()
        if observation is None:
            return RfState.UNKNOWN
        return RfState.TX if observation.value else RfState.RX

    def _tx_interlock_disposition_overrides(
        self,
    ) -> TxInterlockDispositionOverrides:
        return self._radio.profile.tx_interlock_disposition_overrides

    def _stamp_provider_generation(
        self,
        observations: Sequence["Observation"],
        generation: int | None,
    ) -> tuple["Observation", ...]:
        if generation is None:
            return tuple(observations)
        return tuple(
            replace(observation, provider_generation=generation)
            for observation in observations
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start all three polling loops."""
        if self._tasks:
            return
        self._paused.set()
        loop = asyncio.get_running_loop()
        self._tasks = [
            loop.create_task(self._fast_loop(), name="yaesu-poller-fast"),
            loop.create_task(self._medium_loop(), name="yaesu-poller-medium"),
            loop.create_task(self._slow_loop(), name="yaesu-poller-slow"),
        ]
        logger.info("YaesuCatPoller: started")

    async def stop(self) -> None:
        """Cancel all polling loops and wait for them to finish."""
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._cancel_deferred_entry("poller stopped")
        logger.info("YaesuCatPoller: stopped")

    async def pause(self) -> None:
        """Suspend polling.  In-flight requests complete; new ones wait."""
        self._paused.clear()
        logger.debug("YaesuCatPoller: paused")

    async def resume(self) -> None:
        """Resume a paused poller."""
        self._paused.set()
        logger.debug("YaesuCatPoller: resumed")

    @property
    def running(self) -> bool:
        """True if any polling task is alive."""
        return bool(self._tasks) and any(not t.done() for t in self._tasks)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_ema(self, raw: int, prev: float | None) -> float:
        """Apply exponential moving average smoothing to a meter sample."""
        if prev is None or self._ema_alpha <= 0:
            return float(raw)
        return self._ema_alpha * raw + (1.0 - self._ema_alpha) * prev

    def _emit_legacy_state(self) -> None:
        if self._callback is not None and self._observation_callback is None:
            self._callback(self._radio.radio_state)

    def _tx_target_profile(self) -> RadioAcquisitionProfile | None:
        profile = getattr(
            getattr(self._radio, "profile", None), "state_acquisition", None
        )
        if not isinstance(profile, RadioAcquisitionProfile):
            return None
        return profile if profile.capability_for(_TX_TARGET_PATH).can_poll else None

    def _current_tx_target_generation(self) -> tuple[str | None, int]:
        profile = self._tx_target_profile()
        stats = getattr(getattr(self._radio, "_transport", None), "stats", None)
        reconnects = getattr(stats, "reconnects", 0)
        if not isinstance(reconnects, int) or isinstance(reconnects, bool):
            reconnects = 0
        return (None if profile is None else profile.provider, reconnects)

    def _invalidate_tx_target(self, *, provider_generation: int | None = None) -> None:
        callback = self._observation_callback
        profile = self._tx_target_profile()
        generation = self._current_tx_target_generation()
        store_generation = provider_generation
        if store_generation is None:
            store_generation = self._captured_provider_generation()
        invalidation_generation = (*generation, store_generation)
        known = self._tx_target_known_generation == self._tx_target_generation
        if generation != self._tx_target_generation:
            self._tx_target_known_generation = None
        self._tx_target_generation = generation
        if (
            callback is None
            or profile is None
            or self._tx_target_invalidation == invalidation_generation
        ):
            return
        adapter = ProviderObservationAdapter(
            profile=profile,
            source="yaesu_poll_response",
            transport="serial",
        )
        observations: Sequence[Observation] = (
            adapter.observation(
                _TX_TARGET_PATH,
                UnknownTxTarget(reason="stale" if known else "not-observed"),
                native_id="connection_generation",
            ),
        )
        observations = self._stamp_provider_generation(observations, store_generation)
        self._tx_target_invalidation = invalidation_generation
        try:
            callback(observations)
        except (Exception, asyncio.CancelledError):
            logger.warning("YaesuCatPoller: TX target callback failed", exc_info=True)

    def _sync_tx_target_generation(self) -> tuple[str | None, int]:
        generation = self._current_tx_target_generation()
        if generation != self._tx_target_generation:
            self._cancel_deferred_entry("connection generation changed")
            self._invalidate_ptt_observation()
            self._invalidate_tx_target()
        return generation

    async def _emit_medium_observations(self) -> bool:
        if self._observation_callback is None:
            return False
        from .observations import YAESU_PTT_PATH, YaesuObservationAdapter

        provider_generation = self._captured_provider_generation()
        generation = self._sync_tx_target_generation()
        try:
            observations = await YaesuObservationAdapter.from_radio(
                self._radio
            ).poll_medium()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._invalidate_ptt_observation()
            if not self._provider_generation_is_current(provider_generation):
                return True
            self._invalidate_tx_target(provider_generation=provider_generation)
            raise
        if not self._provider_generation_is_current(provider_generation):
            self._invalidate_ptt_observation()
            return True
        if self._current_tx_target_generation() != generation:
            observations = tuple(
                item
                for item in observations
                if item.path not in (_TX_TARGET_PATH, YAESU_PTT_PATH)
            )
            self._invalidate_ptt_observation()
            self._invalidate_tx_target(provider_generation=provider_generation)
        observations = self._stamp_provider_generation(
            observations, provider_generation
        )
        ptt_observation: Observation | None = None
        for observation in observations:
            if observation.path == YAESU_PTT_PATH:
                if type(observation.value) is bool:
                    ptt_observation = observation
            elif observation.path == _TX_TARGET_PATH:
                self._tx_target_invalidation = None
                if isinstance(observation.value, KnownTxTarget):
                    self._tx_target_known_generation = generation
        if ptt_observation is None:
            self._invalidate_ptt_observation()
        else:
            self._ptt_observation = ptt_observation
            self._ptt_connection_generation = generation
            self._last_ptt = ptt_observation.value
        self._observation_callback(self._annotate_yaesu_readbacks(observations))
        return True

    async def _emit_fast_observations(self) -> bool:
        if self._observation_callback is None:
            return False
        from .observations import YaesuObservationAdapter

        adapter = YaesuObservationAdapter.from_radio(self._radio)
        provider_generation = self._captured_provider_generation()
        ema_main, ema_sub = self._ema_s_main, self._ema_s_sub

        def _smooth(receiver: int, raw: int) -> int:
            nonlocal ema_main, ema_sub
            if receiver == 0:
                ema_main = self._apply_ema(raw, ema_main)
                return round(ema_main)
            ema_sub = self._apply_ema(raw, ema_sub)
            return round(ema_sub)

        ptt_observation = self._current_ptt_observation()
        if ptt_observation is not None and ptt_observation.value:
            observations = await adapter.poll_tx_meters()
        else:
            observations = await adapter.poll_rx_meters(smooth_s_meter=_smooth)
        if not self._provider_generation_is_current(provider_generation):
            return True
        self._ema_s_main, self._ema_s_sub = ema_main, ema_sub
        self._observation_callback(
            self._stamp_provider_generation(observations, provider_generation)
        )
        return True

    async def _emit_slow_control_observations(self) -> bool:
        if self._observation_callback is None:
            return False
        from .observations import YaesuObservationAdapter

        adapter = YaesuObservationAdapter.from_radio(self._radio)
        provider_generation = self._captured_provider_generation()
        observations = (
            await adapter.poll_slow_controls() + await adapter.poll_tx_controls()
        )
        if not self._provider_generation_is_current(provider_generation):
            return True
        self._observation_callback(
            self._annotate_yaesu_readbacks(
                self._stamp_provider_generation(observations, provider_generation)
            )
        )
        return True

    def _track_receiver_select_readback(self, entry: CommandQueueEntry) -> None:
        if (
            entry.command_service is None
            or entry.command_id is None
            or entry.source is None
        ):
            return
        expectations = entry.command_service.retain_readback_expectations_for_dispatch(
            source=entry.source,
            session_id=entry.session_id,
            command_id=entry.command_id,
        )
        generation = (
            self._current_tx_target_generation(),
            self._captured_provider_generation(),
        )
        for expectation in expectations or ():
            if expectation.path == _ACTIVE_RECEIVER_PATH:
                self._pending_receiver_select = entry
            self._pending_readbacks[_yaesu_receiver_alias(expectation.path)] = (
                expectation,
                entry,
                generation,
                time.monotonic(),
            )

    def _annotate_receiver_select_readback(
        self, observations: Sequence[Observation]
    ) -> tuple[Observation, ...]:
        entry = self._pending_receiver_select
        if (
            entry is None
            or entry.command_service is None
            or entry.command_id is None
            or entry.source is None
        ):
            return tuple(observations)
        expectations = entry.command_service.readback_expectations(
            source=entry.source,
            session_id=entry.session_id,
            command_id=entry.command_id,
        )
        expectation = next(
            (item for item in expectations if item.path == _ACTIVE_RECEIVER_PATH),
            None,
        )
        if expectation is None:
            self._pending_receiver_select = None
            return tuple(observations)
        result: list[Observation] = []
        for observation in observations:
            if (
                observation.path == expectation.path
                and observation.value == expectation.value
            ):
                observation = replace(
                    observation,
                    source=replace(
                        observation.source,
                        command_source=entry.source,
                        session_id=entry.session_id,
                    ),
                    correlation_id=entry.command_id,
                )
                self._pending_receiver_select = None
            result.append(observation)
        return tuple(result)

    def _annotate_yaesu_readbacks(
        self, observations: Sequence[Observation]
    ) -> tuple[Observation, ...]:
        result = list(observations)
        for index, observation in enumerate(result):
            pending = self._pending_readbacks.get(observation.path)
            if pending is None or observation.correlation_id is not None:
                continue
            expectation, entry, generation, dispatched_at = pending
            expectations = entry.command_service.readback_expectations(
                source=entry.source,
                session_id=entry.session_id,
                command_id=entry.command_id,
            )
            matches = (
                expectation in expectations
                and _exact_readback_value_matches(observation.value, expectation.value)
                and observation.timestamp_monotonic >= dispatched_at
                and generation[0] == self._current_tx_target_generation()
                and generation[1] == self._captured_provider_generation()
                and observation.provider_generation == generation[1]
                and _is_yaesu_cat_readback(observation.source)
            )
            if matches:
                result[index] = replace(
                    observation,
                    source=replace(
                        observation.source,
                        command_source=entry.source,
                        session_id=entry.session_id,
                    ),
                    correlation_id=entry.command_id,
                )
                self._pending_readbacks.pop(observation.path, None)
            elif expectation not in expectations:
                self._pending_readbacks.pop(observation.path, None)
        return tuple(result)

    # ------------------------------------------------------------------
    # Polling loops
    # ------------------------------------------------------------------

    async def _try_reconnect(self) -> None:
        """Attempt serial reconnect if transport reports too many errors.

        Only one reconnect runs at a time.  Other loops sleep while
        reconnect is in progress.
        """
        transport = getattr(self._radio, "_transport", None)
        if transport is None:
            return
        if not getattr(transport, "_maybe_reconnect_needed", lambda: False)():
            return
        if self._reconnecting:
            return  # Another loop is already reconnecting

        self._reconnecting = True
        self._cancel_deferred_entry("serial reconnect")
        advance = self._advance_provider_generation
        provider_generation = None if advance is None else advance()
        try:
            logger.warning("YaesuCatPoller: triggering auto-reconnect")
            self._invalidate_ptt_observation()
            self._invalidate_tx_target(provider_generation=provider_generation)
            await transport.reconnect()
            logger.info("YaesuCatPoller: reconnected successfully")
        except Exception:
            logger.error("YaesuCatPoller: reconnect failed", exc_info=True)
        finally:
            generation = self._current_tx_target_generation()
            if generation != self._tx_target_generation:
                self._tx_target_known_generation = None
                self._tx_target_invalidation = None
            self._tx_target_generation = generation
            self._reconnecting = False

    async def _run_poll_cycle(
        self,
        name: str,
        coro_fn: Callable[[], Awaitable[None]],
        interval: float,
    ) -> None:
        """Generic poll loop with auto-reconnect on persistent errors."""
        _conn_backoff = 0.0
        _MAX_CONN_BACKOFF = 10.0
        while True:
            await self._paused.wait()
            if self._reconnecting:
                await asyncio.sleep(interval)
                continue
            try:
                async with self._lock:
                    self._sync_tx_target_generation()
                    await coro_fn()
                _conn_backoff = 0.0  # reset on success
            except asyncio.CancelledError:
                raise
            except (RadioConnectionError, ConnectionError, OSError):
                # Radio off or connection lost — single-line log, backoff
                _conn_backoff = min(_conn_backoff + 1.0, _MAX_CONN_BACKOFF)
                if _conn_backoff <= 1.0:
                    logger.warning(
                        "YaesuCatPoller: %s — radio not connected, retrying in %.0fs",
                        name,
                        _conn_backoff,
                    )
                await self._try_reconnect()
                await asyncio.sleep(_conn_backoff)
                continue
            except Exception:
                logger.warning("YaesuCatPoller: %s poll error", name, exc_info=True)
                await self._try_reconnect()
            await asyncio.sleep(interval)

    async def _fast_loop(self) -> None:
        await self._run_poll_cycle("fast", self._poll_fast, self._fast_interval)

    async def _medium_loop(self) -> None:
        async def _medium() -> None:
            await self._drain_commands()
            await self._poll_medium()

        await self._run_poll_cycle("medium", _medium, self._medium_interval)

    async def _slow_loop(self) -> None:
        await self._run_poll_cycle("slow", self._poll_slow, self._slow_interval)

    # ------------------------------------------------------------------
    # Command queue drain
    # ------------------------------------------------------------------

    async def _drain_commands(self) -> None:
        """Process all pending commands from the web UI command queue."""
        if self._command_queue is None:
            return

        boundary = self._deferred_generation_change()
        if boundary is not None:
            self._cancel_deferred_entry(boundary)
        now = time.monotonic()
        transition = self._deferred_tx_lane.observe(
            rf_state=self._current_rf_state(), now=now
        )
        entries: list[CommandQueueEntry] = []
        if (
            transition is not None
            and transition.outcome is not TxInterlockDeferredOutcome.HELD
        ):
            entry, self._deferred_tx_entry = self._deferred_tx_entry, None
            self._deferred_tx_generation = None
            if entry is not None:
                if transition.outcome is TxInterlockDeferredOutcome.RELEASED:
                    if self._deferred_release_is_live(entry):
                        entries.append(entry)
                else:
                    self._finish_deferred_entry(entry, superseded=False)
        if self._command_queue.has_commands:
            entries.extend(self._command_queue.drain_entries())
        for entry in entries:
            cmd = entry.command
            if entry.future is not None and entry.future.cancelled():
                logger.debug(
                    "YaesuCatPoller: skipping cancelled queued command %s",
                    type(cmd).__name__,
                )
                continue
            now = time.monotonic()
            rf_state = self._current_rf_state()
            transition = None
            try:
                overrides = self._tx_interlock_disposition_overrides()
                decision = evaluate_tx_interlock(
                    cmd,
                    rf_state=rf_state,
                    disposition_overrides=overrides,
                )
                if (
                    decision.disposition is TxInterlockDisposition.DEFER
                    and not decision.allowed
                    and rf_state is RfState.TX
                ):
                    transition = self._deferred_tx_lane.defer(
                        cmd,
                        now=now,
                        rf_state=rf_state,
                        disposition_overrides=overrides,
                    )
            except Exception as exc:
                self._mark_queued_command_failed(entry, exc)
                if entry.future is not None and not entry.future.done():
                    entry.future.set_exception(exc)
                logger.warning(
                    "YaesuCatPoller: command %s failed policy validation",
                    type(cmd).__name__,
                    exc_info=True,
                )
                continue
            if transition is not None:
                held = self._deferred_tx_lane.observe(rf_state=RfState.TX, now=now)
                if held is None:
                    raise RuntimeError("deferred command lane lost its replacement")
                previous, self._deferred_tx_entry = self._deferred_tx_entry, entry
                if (
                    previous is None
                    or transition.outcome is TxInterlockDeferredOutcome.EXPIRED
                ):
                    self._deferred_tx_generation = (
                        self._current_tx_target_generation(),
                        self._captured_provider_generation(),
                    )
                if previous is not None:
                    self._finish_deferred_entry(
                        previous,
                        superseded=(
                            transition.outcome is TxInterlockDeferredOutcome.SUPERSEDED
                        ),
                    )
                self._emit_deferred_entry_held(entry, expires_at=held.expires_at)
                continue
            try:
                if (
                    decision.disposition is TxInterlockDisposition.DEFER
                    and not decision.allowed
                ):
                    raise CommandError(decision.reason)
                await self._execute_command(cmd)
                self._track_receiver_select_readback(entry)
                if entry.future is not None and not entry.future.done():
                    entry.future.set_result(None)
            except Exception as exc:
                self._mark_queued_command_failed(entry, exc)
                if entry.future is not None and not entry.future.done():
                    entry.future.set_exception(exc)
                logger.warning(
                    "YaesuCatPoller: command %s failed",
                    type(cmd).__name__,
                    exc_info=True,
                )

    def _deferred_release_is_live(self, entry: CommandQueueEntry) -> bool:
        reason = None
        if (
            entry.source == "websocket"
            and entry.session_id is not None
            and self._command_queue is not None
            and not self._command_queue.session_is_live(entry.session_id)
        ):
            reason = f"control session {entry.session_id} is gone"
        elif (
            entry.command_service is not None
            and entry.command_id is not None
            and entry.source is not None
            and entry.command_service.retain_readback_expectations_for_dispatch(
                source=entry.source,
                session_id=entry.session_id,
                command_id=entry.command_id,
            )
            is None
        ):
            reason = "deferred command no longer active"
        if reason is None:
            return True
        error = CommandError(reason)
        if "no longer active" not in reason:
            self._mark_queued_command_failed(entry, error)
        if entry.future is not None and not entry.future.done():
            entry.future.set_exception(error)
        return False

    def _deferred_generation_change(self) -> str | None:
        generation = self._deferred_tx_generation
        if generation is None:
            return None
        connection, provider = generation
        if connection != self._current_tx_target_generation():
            return "connection generation changed"
        if provider != self._captured_provider_generation():
            return "provider generation changed"
        return None

    def _cancel_deferred_entry(self, reason: str) -> None:
        entry = self._deferred_tx_entry
        (
            self._deferred_tx_entry,
            self._deferred_tx_generation,
            self._deferred_tx_lane,
        ) = (None, None, DeferredTxCommandLane())
        if entry is None:
            return
        error = CommandError(f"deferred command cancelled: {reason}")
        self._mark_queued_command_failed(entry, error)
        if entry.future is not None and not entry.future.done():
            entry.future.set_exception(error)

    @classmethod
    def _finish_deferred_entry(cls, entry: Any, *, superseded: bool) -> None:
        message = (
            "deferred command superseded" if superseded else "deferred command expired"
        )
        error: BaseException = (
            CommandError(message) if superseded else TimeoutError(message)
        )
        if superseded and entry.command_service is not None and entry.command_id:
            entry.command_service.expire_command(
                entry.command_id,
                source=entry.source,
                session_id=entry.session_id,
            )
            entry.command_service.emit_lifecycle(
                CommandIntent(
                    id=entry.command_id,
                    name="queued_completion",
                    params={}
                    if entry.session_id is None
                    else {"session_id": entry.session_id},
                    source=entry.source or "internal_policy",
                ),
                "superseded",
                message=message,
            )
        elif not superseded:
            cls._mark_queued_command_failed(entry, error)
        if entry.future is not None and not entry.future.done():
            entry.future.set_exception(error)

    @staticmethod
    def _emit_deferred_entry_held(
        entry: CommandQueueEntry, *, expires_at: float
    ) -> None:
        if entry.command_service is None or entry.command_id is None:
            return
        source: CommandSource = entry.source or "internal_policy"
        params = {} if entry.session_id is None else {"session_id": entry.session_id}
        target = None
        events = entry.command_service.lifecycle_events()
        if isinstance(events, Sequence):
            for event in reversed(events):
                if (
                    event.command_id == entry.command_id
                    and event.source == source
                    and (event.details or {}).get("session_id") == entry.session_id
                ):
                    target = event.target
                    break
        entry.command_service.emit_lifecycle(
            CommandIntent(
                id=entry.command_id,
                name="queued_completion",
                params=params,
                source=source,
                target=target,
            ),
            "queued",
            details={
                "heldBy": "tx_interlock",
                "reason": "tx_active",
                "expiresAt": expires_at,
            },
        )

    @staticmethod
    def _mark_queued_command_failed(entry: Any, exc: BaseException) -> None:
        if entry.command_service is None or entry.command_id is None:
            return
        params: dict[str, Any] = {
            "message": str(exc) or None,
            "timed_out": isinstance(
                exc, (TimeoutError, RigplaneTimeoutError, CatTimeoutError)
            ),
            "session_id": entry.session_id,
        }
        if entry.source is not None:
            params["source"] = entry.source
        entry.command_service.fail_command(entry.command_id, **params)

    # CI-V band codes → Yaesu BS band codes
    _CIV_TO_YAESU_BAND: dict[int, int] = {
        0x00: 0,  # 160m → 1.8M
        0x01: 1,  # 80m  → 3.5M
        0x02: 2,  # 60m  → 5M
        0x03: 3,  # 40m  → 7M
        0x04: 4,  # 30m  → 10M
        0x05: 5,  # 20m  → 14M
        0x06: 6,  # 17m  → 18M
        0x07: 7,  # 15m  → 21M
        0x08: 8,  # 12m  → 24M
        0x09: 9,  # 10m  → 28M
        0x0A: 10,  # 6m   → 50M
    }

    async def _execute_command(self, cmd: Any) -> None:
        """Dispatch a single command to the radio.

        Commands come from the web UI CommandQueue.  The dispatcher handles
        all command types; unsupported commands fail truthfully.
        """
        decision = evaluate_tx_interlock(
            cmd,
            rf_state=self._current_rf_state(),
            disposition_overrides=self._tx_interlock_disposition_overrides(),
        )
        if not decision.allowed and (
            decision.disposition is TxInterlockDisposition.BLOCK
            or classify_tx_interlock(cmd) is TxInterlockDisposition.TX_SAFE
        ):
            raise CommandError(decision.reason)

        from ..._poller_types import (
            PttOff,
            PttOn,
            SelectVfo,
            SetAfLevel,
            SetAgc,
            SetApf,
            SetAttenuator,
            SetAutoNotch,
            SetBand,
            SetBreakIn,
            SetCompressor,
            SetCompressorLevel,
            SetCwPitch,
            SetDataMode,
            SetDialLock,
            SetDigiSel,
            SetDriveGain,
            SetDualWatch,
            SetFilter,
            SetFilterShape,
            SetFilterWidth,
            SetFreq,
            SetIfShift,
            SetIpPlus,
            SetKeySpeed,
            SetManualNotch,
            SetMicGain,
            SetMode,
            SetMonitor,
            SetMonitorGain,
            SetNB,
            SetNBLevel,
            SetNR,
            SetNRLevel,
            SetNotchFilter,
            SetPbtInner,
            SetPbtOuter,
            SetPower,
            SetPowerstat,
            SetPreamp,
            SetRfGain,
            SetRitFrequency,
            SetRitStatus,
            SetRitTxStatus,
            SetSplit,
            SetSquelch,
            SetTwinPeak,
            SetVox,
            SetTunerStatus,
            VfoEqualize,
            VfoSwap,
        )

        radio = self._radio
        name = type(cmd).__name__

        match cmd:
            # ── Core: Frequency / Mode / Band ──
            case SetFreq(freq=freq, receiver=rx):
                await radio.set_freq(freq, receiver=rx)
            case SetMode(mode=mode, receiver=rx):
                await radio.set_mode(mode, receiver=rx)
            case SetBand(band=band):
                yaesu_band = self._CIV_TO_YAESU_BAND.get(band, band)
                await radio.set_band(yaesu_band)
            case SelectVfo(vfo=vfo):
                normalized = vfo.strip().upper()
                receiver_count = radio.receiver_count
                if receiver_count == 1 and normalized in ("A", "VFOA", "MAIN", "0"):
                    code = 0
                elif receiver_count == 1 and normalized in ("B", "VFOB"):
                    code = 1
                elif receiver_count > 1 and normalized in ("A", "VFOA", "MAIN", "0"):
                    code = 0
                elif receiver_count > 1 and normalized in ("B", "VFOB", "SUB", "1"):
                    code = 1
                else:
                    raise CommandError(
                        f"select_vfo({vfo!r}) is unsupported for "
                        f"receiver_count={receiver_count}"
                    )
                await radio.set_vfo_select(code)
            case VfoSwap():
                profile = getattr(radio, "profile", None)
                if profile is None or profile.swap_ab_code is None:
                    model = getattr(
                        profile, "model", getattr(radio, "model", "unknown")
                    )
                    raise NotImplementedError(
                        f"VfoSwap unsupported on {model}: "
                        "profile declares no swap_ab_code"
                    )
                await radio.swap_vfo_ab(0)
            case VfoEqualize():
                profile = getattr(radio, "profile", None)
                if profile is None or profile.equal_ab_code is None:
                    model = getattr(
                        profile, "model", getattr(radio, "model", "unknown")
                    )
                    raise NotImplementedError(
                        f"VfoEqualize unsupported on {model}: "
                        "profile declares no equal_ab_code"
                    )
                await radio.equalize_vfo_ab(0)

            # ── PTT ──
            case PttOn():
                await radio.set_ptt(True)
            case PttOff():
                await radio.set_ptt(False)
            case SetPowerstat(on=on):
                await radio.set_powerstat(on)

            # ── Audio / RF Levels ──
            case SetAfLevel(level=level):
                await radio.set_af_level(level)
            case SetRfGain(level=level):
                await radio.set_rf_gain(level)
            case SetSquelch(level=level):
                await radio.set_squelch(level)
            case SetMicGain(level=level):
                await radio.set_mic_gain(level)
            case SetPower(level=level, unit=unit):
                if unit != "watts":
                    raise ValueError(
                        f"Yaesu backend expects SetPower unit='watts' "
                        f"(PC command); got unit={unit!r}"
                    )
                await radio.set_power(level)
            case SetDriveGain(level=level):
                await radio.set_drive_gain(level)

            # ── RF Front End ──
            case SetAttenuator(db=db):
                await radio.set_attenuator_level(db)
            case SetPreamp(level=level, receiver=receiver):
                await radio.set_preamp(level, receiver)

            # ── DSP / Noise ──
            case SetAgc(mode=mode):
                await radio.set_agc(mode)
            case SetNB(on=on):
                await radio.set_nb(on)
            case SetNR(on=on):
                await radio.set_nr(on)
            case SetNBLevel(level=level):
                await radio.set_nb_level(level)
            case SetNRLevel(level=level):
                await radio.set_nr_level(level)
            case SetAutoNotch(on=on):
                await radio.set_auto_notch(on)
            case SetManualNotch(on=on):
                await radio.set_manual_notch(on)
            case SetNotchFilter(level=level):
                await radio.set_manual_notch_freq(level)

            # ── Filters ──
            case SetFilter(filter_num=_num):
                raise NotImplementedError(
                    "SetFilter unsupported by Yaesu CAT dispatcher"
                )
            case SetFilterWidth(width=width):
                await radio.set_filter_width(width)
            case SetFilterShape(shape=_shape):
                raise NotImplementedError(
                    "SetFilterShape unsupported by Yaesu CAT dispatcher"
                )
            case SetPbtInner() | SetPbtOuter():
                raise NotImplementedError(f"{name} unsupported by Yaesu CAT dispatcher")

            # ── IF Shift ──
            case SetIfShift(offset=offset):
                await radio.set_if_shift(offset)

            # ── CW ──
            case SetKeySpeed(speed=speed):
                await radio.set_keyer_speed(speed)
            case SetCwPitch(value=value):
                await radio.set_key_pitch(value)
            case SetBreakIn(mode=mode):
                await radio.set_break_in(bool(mode))

            # ── TX Controls ──
            case SetCompressor(on=on):
                await radio.set_processor(on)
            case SetCompressorLevel(level=level):
                await radio.set_processor_level(level)
            case SetVox(on=on):
                await radio.set_vox(on)
            case SetTunerStatus(value=value):
                await radio.set_tuner(value)
            case SetMonitor(on=on):
                await radio.set_monitor_on(on)
            case SetMonitorGain(level=level):
                await radio.set_monitor_level(level)
            case SetSplit(on=on):
                await radio.set_split(on)

            # ── RIT / Clarifier ──
            case SetRitStatus(on=on):
                # Canonical name; read-modify-write preserves XIT bit.
                await radio.set_rit_status(on)
            case SetRitTxStatus(on=on):
                # Canonical name; read-modify-write preserves RIT bit.
                await radio.set_rit_tx_status(on)
            case SetRitFrequency(freq=freq):
                await radio.set_rit_frequency(freq)

            # ── Data Mode ──
            case SetDataMode(mode=mode):
                await radio.set_data_mode(mode)

            # ── Dial Lock ──
            case SetDialLock(on=on):
                await radio.set_lock(on)

            # ── Dual Watch ──
            case SetDualWatch(on=on):
                await radio.set_dual_watch(on)

            # ── APF (Audio Peak Filter) ──
            case SetApf(mode=mode, receiver=rx):
                await radio.set_audio_peak_filter(mode, receiver=rx)

            # ── IC-7610-specific (not applicable) ──
            case SetIpPlus() | SetTwinPeak() | SetDigiSel():
                raise NotImplementedError(f"{name} unsupported by Yaesu CAT dispatcher")

            case _:
                raise NotImplementedError(f"{name} unsupported by Yaesu CAT dispatcher")

        logger.info("CMD: %s", name)

    # ------------------------------------------------------------------
    # Poll actions
    # ------------------------------------------------------------------

    async def _poll_fast(self) -> None:
        """Fast group: S-meter (RX) or ALC/Power/COMP/SWR meters (TX)."""
        if await self._emit_fast_observations():
            return

        state = self._radio.radio_state

        if state.ptt and "meters" in self._caps:
            # TX meters — poll ALC, Power, COMP, SWR during transmit
            try:
                state.alc_meter = await self._radio.get_alc_meter()
            except Exception:
                logger.debug("YaesuCatPoller: get_alc_meter failed", exc_info=True)
            try:
                state.power_meter = await self._radio.get_power_meter()
            except Exception:
                logger.debug("YaesuCatPoller: get_power_meter failed", exc_info=True)
            try:
                state.comp_meter = await self._radio.get_comp_meter()
            except Exception:
                logger.debug("YaesuCatPoller: get_comp_meter failed", exc_info=True)
            try:
                state.swr_meter = await self._radio.get_swr_meter()
            except Exception:
                logger.debug("YaesuCatPoller: get_swr failed", exc_info=True)
        else:
            # RX meters — S-meter for main and sub receivers
            raw_main = await self._radio.get_s_meter(0)
            self._ema_s_main = self._apply_ema(raw_main, self._ema_s_main)
            state.main.s_meter = int(round(self._ema_s_main))

            if "dual_rx" in self._caps:
                try:
                    raw_sub = await self._radio.get_s_meter(1)
                    self._ema_s_sub = self._apply_ema(raw_sub, self._ema_s_sub)
                    state.sub.s_meter = int(round(self._ema_s_sub))
                except NotImplementedError:
                    pass
                except Exception:
                    logger.debug(
                        "YaesuCatPoller: sub S-meter unavailable", exc_info=True
                    )

        self._emit_legacy_state()

    async def _poll_medium(self) -> None:
        """Medium group: frequency, mode, PTT."""
        if await self._emit_medium_observations():
            return

        await self._radio.get_freq(0)
        await self._radio.get_mode(0)

        if "dual_rx" in self._caps:
            await self._radio.get_freq(1)
            await self._radio.get_mode(1)

        await self._radio.get_ptt()

        # Filter width — in medium poll for responsive knob tracking
        if "filter_width" in self._caps:
            self._radio.radio_state.main.filter_width = (
                await self._radio.get_filter_width(0)
            )

        self._emit_legacy_state()

    async def _poll_slow(self) -> None:
        """Slow group: AGC, levels, DSP, TX settings.

        Only polls parameters declared in the rig's TOML capabilities.
        Core items (AGC, mic gain) are always polled; feature-specific
        items are gated by ``self._caps`` to avoid hitting methods
        that raise ``NotImplementedError``.
        """
        if await self._emit_slow_control_observations():
            return

        state = self._radio.radio_state
        radio = self._radio
        caps = self._caps

        # -- AGC (always) --
        try:
            state.main.agc = await radio.get_agc(0)
        except NotImplementedError:
            pass
        except Exception:
            logger.debug("YaesuCatPoller: get_agc failed", exc_info=True)

        # -- AF level --
        if "af_level" in caps:
            try:
                state.main.af_level = await radio.get_af_level(0)
            except NotImplementedError:
                pass
            except Exception:
                logger.debug("YaesuCatPoller: get_af_level failed", exc_info=True)

        # -- RF gain --
        if "rf_gain" in caps:
            try:
                state.main.rf_gain = await radio.get_rf_gain(0)
            except NotImplementedError:
                pass
            except Exception:
                logger.debug("YaesuCatPoller: get_rf_gain failed", exc_info=True)

        # -- Squelch --
        if "squelch" in caps:
            try:
                state.main.squelch = await radio.get_squelch(0)
            except NotImplementedError:
                pass
            except Exception:
                logger.debug("YaesuCatPoller: get_squelch failed", exc_info=True)

        # -- SUB receiver levels --
        if "dual_rx" in caps:
            try:
                state.sub.af_level = await radio.get_af_level(1)
            except NotImplementedError:
                pass
            except Exception:
                logger.debug("YaesuCatPoller: get_af_level(sub) failed", exc_info=True)
            try:
                state.sub.rf_gain = await radio.get_rf_gain(1)
            except NotImplementedError:
                pass
            except Exception:
                logger.debug("YaesuCatPoller: get_rf_gain(sub) failed", exc_info=True)
            try:
                state.sub.squelch = await radio.get_squelch(1)
            except NotImplementedError:
                pass
            except Exception:
                logger.debug("YaesuCatPoller: get_squelch(sub) failed", exc_info=True)

        # -- DSP: NB/NR levels, auto notch --
        if "nb" in caps:
            try:
                nb_level = await radio.get_nb_level(0)
                state.main.nb_level = nb_level
                state.main.nb = nb_level > 0
            except NotImplementedError:
                pass
            except Exception:
                logger.debug("YaesuCatPoller: get_nb_level failed", exc_info=True)

        if "nr" in caps:
            try:
                nr_level = await radio.get_nr_level(0)
                state.main.nr_level = nr_level
                state.main.nr = nr_level > 0
            except NotImplementedError:
                pass
            except Exception:
                logger.debug("YaesuCatPoller: get_nr_level failed", exc_info=True)

        if "notch" in caps:
            try:
                state.main.auto_notch = await radio.get_auto_notch(0)
            except NotImplementedError:
                pass
            except Exception:
                logger.debug("YaesuCatPoller: get_auto_notch failed", exc_info=True)

        # -- TX power --
        if "tx" in caps:
            try:
                _, watts = await radio.get_power()
                state.power_level = watts
            except NotImplementedError:
                pass
            except Exception:
                logger.debug("YaesuCatPoller: get_power failed", exc_info=True)

        # -- Mic gain (always) --
        try:
            state.mic_gain = await radio.get_mic_gain()
        except NotImplementedError:
            pass
        except Exception:
            logger.debug("YaesuCatPoller: get_mic_gain failed", exc_info=True)

        # -- Split --
        if "split" in caps:
            try:
                state.split = await radio.get_split()
            except NotImplementedError:
                pass
            except Exception:
                logger.debug("YaesuCatPoller: get_split failed", exc_info=True)

        # -- VOX --
        if "vox" in caps:
            try:
                state.vox_on = await radio.get_vox()
            except NotImplementedError:
                pass
            except Exception:
                logger.debug("YaesuCatPoller: get_vox failed", exc_info=True)

        # -- Dial lock --
        if "dial_lock" in caps:
            try:
                state.dial_lock = await radio.get_lock()
            except NotImplementedError:
                pass
            except Exception:
                logger.debug("YaesuCatPoller: get_lock failed", exc_info=True)

        # -- Speech processor (COMP/PROC) --
        if "compressor" in caps:
            try:
                state.compressor_on = await radio.get_processor()
                state.compressor_level = await radio.get_processor_level()
            except NotImplementedError:
                pass
            except Exception:
                logger.debug("YaesuCatPoller: get_processor failed", exc_info=True)

        # -- ATT / Preamp --
        if "attenuator" in caps:
            try:
                state.main.att = int(await radio.get_attenuator(0))
            except NotImplementedError:
                pass
            except Exception:
                logger.debug("YaesuCatPoller: get_attenuator failed", exc_info=True)

        if "preamp" in caps:
            try:
                state.main.preamp = await radio.get_preamp(0)
            except NotImplementedError:
                pass
            except Exception:
                logger.debug("YaesuCatPoller: get_preamp failed", exc_info=True)

        # -- Antenna tuner --
        if "tuner" in caps:
            try:
                state.tuner_status = await radio.get_tuner()
            except NotImplementedError:
                pass
            except Exception:
                logger.debug("YaesuCatPoller: get_tuner failed", exc_info=True)

        # -- Contour / S-DX --
        if "contour" in caps:
            try:
                state.main.contour = await radio.get_contour(0)
            except NotImplementedError:
                pass
            except Exception:
                logger.debug("YaesuCatPoller: get_contour failed", exc_info=True)

        # -- IF Shift --
        if "if_shift" in caps:
            try:
                state.main.if_shift = await radio.get_if_shift(0)
            except NotImplementedError:
                pass
            except Exception:
                logger.debug("YaesuCatPoller: get_if_shift failed", exc_info=True)

        # -- Clarifier (RIT/XIT) --
        if "rit" in caps:
            try:
                rx_clar, tx_clar = await radio.get_clarifier()
                state.rit_on = rx_clar
                state.rit_tx = tx_clar
                state.rit_freq = await radio.get_clarifier_freq()
            except NotImplementedError:
                pass
            except Exception:
                logger.debug("YaesuCatPoller: get_clarifier failed", exc_info=True)

        # -- Manual notch state + freq --
        if "notch" in caps:
            try:
                notch_on, notch_freq = await radio.get_manual_notch()
                state.main.manual_notch = notch_on
                state.main.manual_notch_freq = notch_freq
            except NotImplementedError:
                pass
            except Exception:
                logger.debug("YaesuCatPoller: get_manual_notch failed", exc_info=True)

        # -- Narrow filter mode (always — lightweight query) --
        try:
            state.main.narrow = await radio.get_narrow()
        except NotImplementedError:
            pass
        except Exception:
            logger.debug("YaesuCatPoller: get_narrow failed", exc_info=True)

        # -- CW parameters --
        if "cw" in caps:
            try:
                state.key_speed = await radio.get_keyer_speed()
            except NotImplementedError:
                pass
            except Exception:
                logger.debug("YaesuCatPoller: get_keyer_speed failed", exc_info=True)
            try:
                # state.cw_pitch is Hz; get_cw_pitch returns Hz (300-1050)
                state.cw_pitch = await radio.get_cw_pitch()
            except NotImplementedError:
                pass
            except Exception:
                logger.debug("YaesuCatPoller: get_cw_pitch failed", exc_info=True)
            try:
                # FTX-1 CAT only has binary on/off — no semi/full distinction
                state.break_in = 1 if await radio.get_break_in() else 0
            except NotImplementedError:
                pass
            except Exception:
                logger.debug("YaesuCatPoller: get_break_in failed", exc_info=True)
            try:
                state.break_in_delay = await radio.get_break_in_delay()
            except NotImplementedError:
                pass
            except Exception:
                logger.debug("YaesuCatPoller: get_break_in_delay failed", exc_info=True)
            try:
                state.cw_spot = await radio.get_cw_spot()
            except NotImplementedError:
                pass
            except Exception:
                logger.debug("YaesuCatPoller: get_cw_spot failed", exc_info=True)

        # -- RX/TX function mode (FR/FT) — Yaesu-specific extension --
        if "dual_rx" in caps:
            if state.yaesu is None:
                state.yaesu = YaesuStateExtension()
            try:
                state.yaesu.rx_func_mode = await radio.get_rx_func()
            except NotImplementedError:
                pass
            except Exception:
                logger.debug("YaesuCatPoller: get_rx_func failed", exc_info=True)
            try:
                state.yaesu.tx_func_mode = await radio.get_tx_func()
            except NotImplementedError:
                pass
            except Exception:
                logger.debug("YaesuCatPoller: get_tx_func failed", exc_info=True)

        # -- VFO select (always) --
        try:
            state.vfo_select = await radio.get_vfo_select()
        except NotImplementedError:
            pass
        except Exception:
            logger.debug("YaesuCatPoller: get_vfo_select failed", exc_info=True)

        if self._callback is not None:
            self._callback(state)
