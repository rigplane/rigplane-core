"""Deterministic serial-ready test doubles for backend regression gates."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ....core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from ....exceptions import CommandError
from ....profiles import RadioProfile, resolve_radio_profile
from ....radio_state import RadioState
from ...._state_cache import StateCache
from ....types import Mode

if TYPE_CHECKING:
    from ...._poller_types import CommandQueue, CommandQueueEntry


class SerialFrameError(RuntimeError):
    """Base error for serial framing failures."""


class SerialFrameTimeoutError(SerialFrameError):
    """Raised when an incomplete frame times out."""


class SerialFrameOverflowError(SerialFrameError):
    """Raised when buffered partial frame exceeds safety limit."""


class SerialFrameCodec:
    """Simple FE FE ... FD frame codec with deterministic timeout behavior."""

    _START = b"\xfe\xfe"
    _END = 0xFD

    def __init__(
        self, *, max_frame_len: int = 1024, frame_timeout_s: float = 0.1
    ) -> None:
        if max_frame_len < 4:
            raise ValueError("max_frame_len must be >= 4")
        if frame_timeout_s <= 0:
            raise ValueError("frame_timeout_s must be > 0")
        self._max_frame_len = max_frame_len
        self._frame_timeout_s = frame_timeout_s
        self._buffer = bytearray()
        self._partial_since: float | None = None

    def encode(self, payload: bytes) -> bytes:
        """Encode payload as one CI-V frame unless already framed."""
        if payload.startswith(self._START) and payload.endswith(bytes([self._END])):
            return payload
        return self._START + payload + bytes([self._END])

    def feed(self, data: bytes) -> list[bytes]:
        """Feed raw stream bytes and return complete framed packets."""
        if not data:
            return []
        self._buffer.extend(data)
        frames: list[bytes] = []
        while True:
            start = self._buffer.find(self._START)
            if start < 0:
                keep = 1 if self._buffer and self._buffer[-1] == self._START[0] else 0
                if keep:
                    self._buffer[:] = self._buffer[-1:]
                else:
                    self._buffer.clear()
                    self._partial_since = None
                break
            if start > 0:
                del self._buffer[:start]
            end = self._buffer.find(bytes([self._END]), len(self._START))
            if end < 0:
                if len(self._buffer) > self._max_frame_len:
                    self._buffer.clear()
                    self._partial_since = None
                    raise SerialFrameOverflowError(
                        "Partial serial frame exceeded max_frame_len."
                    )
                if self._partial_since is None:
                    self._partial_since = time.monotonic()
                break
            frame = bytes(self._buffer[: end + 1])
            del self._buffer[: end + 1]
            self._partial_since = None
            frames.append(frame)
        return frames

    def expire_partial(self, *, now: float | None = None) -> bool:
        """Expire stale partial frame state; return True when expired."""
        if self._partial_since is None:
            return False
        timestamp = time.monotonic() if now is None else now
        if (timestamp - self._partial_since) <= self._frame_timeout_s:
            return False
        self._buffer.clear()
        self._partial_since = None
        return True


class DeterministicSerialCivLink:
    """In-memory async serial CI-V link for deterministic tests."""

    def __init__(self, codec: SerialFrameCodec | None = None) -> None:
        self._codec = codec or SerialFrameCodec()
        self.sent_frames: list[bytes] = []
        self._incoming_chunks: asyncio.Queue[bytes] = asyncio.Queue()
        self._decoded_frames: asyncio.Queue[bytes] = asyncio.Queue()

    async def send(self, frame: bytes) -> None:
        self.sent_frames.append(self._codec.encode(frame))

    def push_incoming_chunk(self, chunk: bytes) -> None:
        self._incoming_chunks.put_nowait(chunk)

    async def receive(self, timeout: float | None = None) -> bytes | None:
        """Return one complete frame, None on timeout, error on stale partial."""
        timeout_s = 0.1 if timeout is None else timeout
        deadline = time.monotonic() + timeout_s
        while True:
            if not self._decoded_frames.empty():
                return self._decoded_frames.get_nowait()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if self._codec.expire_partial():
                    raise SerialFrameTimeoutError(
                        "Timed out waiting for complete frame."
                    )
                return None
            try:
                chunk = await asyncio.wait_for(
                    self._incoming_chunks.get(), timeout=remaining
                )
            except asyncio.TimeoutError:
                if self._codec.expire_partial():
                    raise SerialFrameTimeoutError(
                        "Timed out waiting for complete frame."
                    )
                return None
            for frame in self._codec.feed(chunk):
                self._decoded_frames.put_nowait(frame)


@dataclass(slots=True)
class _ReceiverState:
    freq: int = 14_074_000
    mode: Mode = Mode.USB
    filter_width: int | None = 1
    data_mode: bool = False
    rf_gain: int = 200
    af_level: int = 200
    squelch: int = 0
    nb_on: bool = False
    nr_on: bool = False
    digisel_on: bool = False
    ip_plus_on: bool = False
    attenuator_db: int = 0
    preamp_level: int = 1
    # Slow-state / operator-control defaults observation-backed by the mock so
    # the v2 availability gate (MOR-429) renders these controls in the live
    # Playwright audit (MOR-437). Values mirror realistic IC-7610 readings.
    agc: int = 2  # 0=off, 1=FAST, 2=MID, 3=SLOW
    agc_time_constant: int = 2
    nr_level: int = 5
    nb_level: int = 5
    auto_notch: bool = False
    manual_notch: bool = False
    filter_width_hz: int = 2400  # SSB default passband in Hz


class SerialMockRadio:
    """Deterministic serial-ready radio stub for consumer smoke/contract tests."""

    def __init__(
        self,
        *,
        profile: RadioProfile | str | None = None,
        model: str | None = None,
    ) -> None:
        self._profile = resolve_radio_profile(profile=profile, model=model)
        self._connected = False
        self._data_mode = False
        self._ptt = False
        self._power = 100
        # Global slow-state / TX defaults, observation-backed by the mock so the
        # v2 availability gate (MOR-429) renders these controls in the live
        # Playwright audit (MOR-437). Realistic IC-7610 idle readings.
        self._mic_gain = 128
        self._compressor_level = 0
        self._monitor_gain = 128
        self._cw_pitch = 600
        self._tuner_status = 0  # 0=off, 1=on, 2=tuning
        self._split = False
        self._compressor_on = False
        self._monitor_on = False
        self._vox_on = False
        self._dual_watch = False
        self._tx_freq_monitor = False
        self._state_cache = StateCache()
        self._radio_state = RadioState()
        self._scope_callback: Any = None
        self._scope_enabled = False
        self._state_change_callback: Any = None
        self._reconnect_callback: Any = None
        self._rx: dict[int, _ReceiverState] = {
            receiver: _ReceiverState()
            for receiver in range(self._profile.receiver_count)
        }
        # Seed RadioState's MAIN/SUB receivers from the per-receiver
        # _ReceiverState defaults so the rigctld handler's per-VFO reads
        # (which go through ``radio.radio_state.{main,sub}.{freq,mode,filter}``)
        # see the same values as the bare ``radio.get_freq()`` / ``get_mode()``
        # paths. Without this, ``f VFOB`` on a freshly-connected dual-RX
        # mock returns ``0`` (the VfoSlotState default) instead of the
        # canonical 14_074_000. See ``set_freq`` / ``set_mode`` below for
        # the matching write-side mirror.
        self._sync_radio_state_from_rx(0)
        if 1 in self._rx:
            self._sync_radio_state_from_rx(1)

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def control_connected(self) -> bool:
        return self._connected

    @property
    def radio_ready(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def soft_reconnect(self) -> None:
        await self.connect()
        if self._reconnect_callback is not None:
            self._reconnect_callback()

    async def soft_disconnect(self) -> None:
        await self.disconnect()

    @property
    def state_cache(self) -> StateCache:
        return self._state_cache

    @property
    def radio_state(self) -> RadioState:
        return self._radio_state

    @property
    def profile(self) -> RadioProfile:
        return self._profile

    @property
    def model(self) -> str:
        return self._profile.model

    @property
    def capabilities(self) -> set[str]:
        return set(self._profile.capabilities)

    def _receiver_state(self, receiver: int, *, operation: str) -> _ReceiverState:
        if receiver in self._rx:
            return self._rx[receiver]
        raise CommandError(
            f"{operation} does not support receiver={receiver} for profile "
            f"{self._profile.model} (receivers={self._profile.receiver_count})"
        )

    def _sync_radio_state_from_rx(self, receiver: int) -> None:
        """Mirror per-receiver state into ``RadioState.{main,sub}``.

        rigctld's per-VFO read paths (``f VFOB``, ``m VFOB``) read from
        ``radio.radio_state.sub.{freq,mode,filter}`` directly, bypassing
        the radio coroutines. Keep that view in sync with ``self._rx``
        whenever a write touches a specific receiver.
        """
        rx = self._rx.get(receiver)
        if rx is None:
            return
        target = self._radio_state.main if receiver == 0 else self._radio_state.sub
        target.freq = rx.freq
        target.mode = rx.mode.name
        target.filter = rx.filter_width

    def set_state_change_callback(self, callback: Any | None) -> None:
        self._state_change_callback = callback

    def set_reconnect_callback(self, callback: Any | None) -> None:
        self._reconnect_callback = callback

    def on_scope_data(self, callback: Any | None) -> None:
        self._scope_callback = callback

    async def send_civ(
        self,
        command: int,
        sub: int | None = None,
        data: bytes | None = None,
        *,
        wait_response: bool = True,
    ) -> None:
        _ = (command, sub, data, wait_response)
        return None

    async def enable_scope(self, **kwargs: Any) -> None:
        _ = kwargs
        self._scope_enabled = True

    async def disable_scope(self) -> None:
        self._scope_enabled = False

    async def set_freq(self, freq: int, receiver: int = 0) -> None:
        self._receiver_state(receiver, operation="set_freq").freq = freq
        self._sync_radio_state_from_rx(receiver)
        if receiver == 0:
            self._state_cache.update_freq(freq)

    async def get_freq(self, receiver: int = 0) -> int:
        return self._receiver_state(receiver, operation="get_freq").freq

    # Backward-compat aliases
    set_frequency = set_freq
    get_frequency = get_freq

    async def set_mode(
        self, mode: Mode | str, filter_width: int | None = None, receiver: int = 0
    ) -> None:
        parsed_mode = mode if isinstance(mode, Mode) else Mode[mode.upper()]
        receiver_state = self._receiver_state(receiver, operation="set_mode")
        receiver_state.mode = parsed_mode
        if filter_width is not None:
            receiver_state.filter_width = filter_width
        self._sync_radio_state_from_rx(receiver)
        if receiver == 0:
            self._state_cache.update_mode(parsed_mode.name, receiver_state.filter_width)

    async def get_mode(self, receiver: int = 0) -> tuple[str, int | None]:
        state = self._receiver_state(receiver, operation="get_mode")
        return state.mode.name, state.filter_width

    async def get_mode_info(self, receiver: int = 0) -> tuple[Mode, int | None]:
        state = self._receiver_state(receiver, operation="get_mode_info")
        return state.mode, state.filter_width

    async def get_data_mode(self) -> bool:
        return self._data_mode

    async def set_data_mode(self, on: int | bool, receiver: int = 0) -> None:
        mode_value = int(on) if isinstance(on, bool) else int(on)
        state = self._receiver_state(receiver, operation="set_data_mode")
        state.data_mode = bool(mode_value)
        self._data_mode = bool(mode_value)
        if receiver == 0:
            self._state_cache.update_data_mode(bool(mode_value))

    async def set_ptt(self, on: bool) -> None:
        self._ptt = on
        self._state_cache.update_ptt(on)

    async def set_rf_power(self, level: int) -> None:
        self._power = level
        self._state_cache.update_rf_power(level / 255.0)

    async def get_rf_power(self) -> int:
        return self._power

    # Backward-compat aliases
    set_power = set_rf_power
    get_power = get_rf_power

    async def get_s_meter(self, receiver: int = 0) -> int:
        return 120

    async def get_swr(self) -> int:
        return 25

    async def get_swr_meter(self) -> int:
        return 25

    async def get_alc_meter(self) -> int:
        return 80

    async def get_power_meter(self) -> int:
        return 200

    async def get_filter(self, receiver: int = 0) -> int | None:
        return self._receiver_state(receiver, operation="get_filter").filter_width

    async def set_filter(self, filter_num: int, receiver: int = 0) -> None:
        self._receiver_state(receiver, operation="set_filter").filter_width = filter_num
        if receiver == 0:
            self._state_cache.update_mode(self._rx[receiver].mode.name, filter_num)

    async def get_rf_gain(self, receiver: int = 0) -> int:
        return self._receiver_state(receiver, operation="get_rf_gain").rf_gain

    async def set_rf_gain(self, level: int, receiver: int = 0) -> None:
        self._receiver_state(receiver, operation="set_rf_gain").rf_gain = level

    async def get_af_level(self, receiver: int = 0) -> int:
        return self._receiver_state(receiver, operation="get_af_level").af_level

    async def set_af_level(self, level: int, receiver: int = 0) -> None:
        self._receiver_state(receiver, operation="set_af_level").af_level = level

    async def set_squelch(self, level: int, receiver: int = 0) -> None:
        self._receiver_state(receiver, operation="set_squelch").squelch = level

    async def get_attenuator_level(self, receiver: int = 0) -> int:
        return self._receiver_state(
            receiver, operation="get_attenuator_level"
        ).attenuator_db

    async def set_attenuator_level(self, db: int, receiver: int = 0) -> None:
        self._receiver_state(
            receiver, operation="set_attenuator_level"
        ).attenuator_db = db

    async def get_preamp(self, receiver: int = 0) -> int:
        return self._receiver_state(receiver, operation="get_preamp").preamp_level

    async def set_preamp(self, level: int, receiver: int = 0) -> None:
        self._receiver_state(receiver, operation="set_preamp").preamp_level = level

    async def set_nb(self, on: bool, receiver: int = 0) -> None:
        self._receiver_state(receiver, operation="set_nb").nb_on = on

    async def set_nr(self, on: bool, receiver: int = 0) -> None:
        self._receiver_state(receiver, operation="set_nr").nr_on = on

    async def set_digisel(self, on: bool, receiver: int = 0) -> None:
        self._receiver_state(receiver, operation="set_digisel").digisel_on = on

    async def set_ipplus(self, on: bool, receiver: int = 0) -> None:
        self._receiver_state(receiver, operation="set_ipplus").ip_plus_on = on

    async def set_ip_plus(self, on: bool, receiver: int = 0) -> None:
        await self.set_ipplus(on, receiver=receiver)

    # --- Slow-state / operator-control setters (observation-backed; MOR-437) ---

    async def set_agc(self, value: int, receiver: int = 0) -> None:
        self._receiver_state(receiver, operation="set_agc").agc = value

    async def set_agc_time_constant(self, value: int, receiver: int = 0) -> None:
        self._receiver_state(
            receiver, operation="set_agc_time_constant"
        ).agc_time_constant = value

    async def set_nr_level(self, level: int, receiver: int = 0) -> None:
        self._receiver_state(receiver, operation="set_nr_level").nr_level = level

    async def set_nb_level(self, level: int, receiver: int = 0) -> None:
        self._receiver_state(receiver, operation="set_nb_level").nb_level = level

    async def set_auto_notch(self, on: bool, receiver: int = 0) -> None:
        self._receiver_state(receiver, operation="set_auto_notch").auto_notch = on

    async def set_manual_notch(self, on: bool, receiver: int = 0) -> None:
        self._receiver_state(receiver, operation="set_manual_notch").manual_notch = on

    async def set_filter_width(self, width: int, receiver: int = 0) -> None:
        self._receiver_state(
            receiver, operation="set_filter_width"
        ).filter_width_hz = width

    async def set_mic_gain(self, level: int) -> None:
        self._mic_gain = level

    async def set_compressor_level(self, level: int) -> None:
        self._compressor_level = level

    async def set_monitor_gain(self, level: int) -> None:
        self._monitor_gain = level

    async def set_cw_pitch(self, value: int) -> None:
        self._cw_pitch = value

    async def set_tuner_status(self, value: int) -> None:
        self._tuner_status = value

    async def set_split(self, on: bool) -> None:
        self._split = on

    async def set_compressor(self, on: bool) -> None:
        self._compressor_on = on

    async def set_monitor(self, on: bool) -> None:
        self._monitor_on = on

    async def set_vox(self, on: bool) -> None:
        self._vox_on = on

    async def set_dual_watch(self, on: bool) -> None:
        self._dual_watch = on

    async def set_tx_freq_monitor(self, on: bool) -> None:
        self._tx_freq_monitor = on

    def create_observation_poller(
        self,
        *,
        callback: Callable[[Sequence[Observation]], None],
        command_queue: "CommandQueue | None" = None,
    ) -> "_MockObservationPoller":
        """Construct a baseline observation poller for the serial mock.

        The mock cannot back state from a CI-V RX stream (``send_civ`` is a
        no-op), so without this the web StateStore stays empty and the v2
        availability gate (MOR-429) strips every non-observation-backed
        control from the DOM, hanging the live Playwright audit (MOR-437).
        The poller emits one honest baseline ``Observation`` per v2-rendered
        field with ``max_age=None`` (never decays) and drains the web command
        queue so the audit's ``set_*`` commands still execute, re-emitting
        the baseline afterwards so values reflect the change without snap-back.
        """
        return _MockObservationPoller(
            self,
            callback=callback,
            command_queue=command_queue,
        )

    def baseline_observations(self) -> tuple[Observation, ...]:
        """Snapshot every v2-rendered field as a confirmed Observation.

        See ``create_observation_poller``. Receivers are emitted for each
        configured receiver (MAIN, plus SUB on dual-RX profiles).
        """
        now = time.monotonic()
        meta = SourceMetadata(
            source="state_poller",
            provider="serial_mock",
            native_id="baseline",
        )
        observations: list[Observation] = []

        def _obs(path: FieldPath, value: Any) -> None:
            observations.append(
                Observation(
                    path=path,
                    value=value,
                    source=meta,
                    timestamp_monotonic=now,
                    max_age=None,
                )
            )

        for receiver in sorted(self._rx):
            rid = str(receiver)
            rx = self._rx[receiver]
            # freq_mode family (active slot)
            _obs(FieldPath.active(rid, "freq_mode", "freq_hz"), rx.freq)
            _obs(FieldPath.active(rid, "freq_mode", "mode"), rx.mode.name)
            _obs(
                FieldPath.active(rid, "freq_mode", "data_mode"),
                int(rx.data_mode),
            )
            # operator_controls family
            controls: dict[str, Any] = {
                "af_level": rx.af_level,
                "rf_gain": rx.rf_gain,
                "squelch": rx.squelch,
                "att": rx.attenuator_db,
                "preamp": rx.preamp_level,
                "agc": rx.agc,
                "agc_time_constant": rx.agc_time_constant,
                "nr_level": rx.nr_level,
                "nb_level": rx.nb_level,
                "filter_width": rx.filter_width_hz,
            }
            for name, value in controls.items():
                _obs(FieldPath.receiver(rid, "operator_controls", name), value)
            # operator_toggles family
            toggles: dict[str, bool] = {
                "nr": rx.nr_on,
                "nb": rx.nb_on,
                "auto_notch": rx.auto_notch,
                "manual_notch": rx.manual_notch,
            }
            for name, flag in toggles.items():
                _obs(FieldPath.receiver(rid, "operator_toggles", name), flag)

        # Global operator_controls
        global_controls: dict[str, Any] = {
            "mic_gain": self._mic_gain,
            "compressor_level": self._compressor_level,
            "monitor_gain": self._monitor_gain,
            "cw_pitch": self._cw_pitch,
            "tuner_status": self._tuner_status,
        }
        for name, value in global_controls.items():
            _obs(FieldPath.global_("operator_controls", name), value)
        # Global tx_state toggles
        global_toggles: dict[str, bool] = {
            "split": self._split,
            "compressor_on": self._compressor_on,
            "monitor_on": self._monitor_on,
            "vox_on": self._vox_on,
            "dual_watch": self._dual_watch,
            "tx_freq_monitor": self._tx_freq_monitor,
        }
        for name, flag in global_toggles.items():
            _obs(FieldPath.global_("tx_state", name), flag)

        return tuple(observations)

    async def set_vfo(self, vfo: str) -> None:
        return None

    # Backward-compat alias
    select_vfo = set_vfo

    async def vfo_swap(self) -> None:
        return None

    async def vfo_exchange(self) -> None:
        return None

    async def vfo_a_equals_b(self) -> None:
        return None

    async def vfo_equalize(self) -> None:
        return None

    async def set_powerstat(self, on: bool) -> None:
        self._connected = on

    async def start_audio_rx_opus(self, callback: Any) -> None:
        return None

    async def stop_audio_rx_opus(self) -> None:
        return None


class _MockObservationPoller:
    """Baseline observation poller for :class:`SerialMockRadio` (MOR-437).

    Emits the mock's honest baseline observations once at start (so the web
    StateStore observation-backs every v2-rendered field with ``max_age=None``
    — they never decay to stale/missing), then drains the web command queue so
    the live audit's ``set_*`` commands execute against the mock. After each
    drain that mutates state, the baseline is re-emitted so observed values
    track the change without snapping back to a default.
    """

    _DRAIN_INTERVAL: float = 0.05

    def __init__(
        self,
        radio: SerialMockRadio,
        *,
        callback: Callable[[Sequence[Observation]], None],
        command_queue: "CommandQueue | None" = None,
    ) -> None:
        self._radio = radio
        self._callback = callback
        self._command_queue = command_queue
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        # Seed the StateStore immediately so the first /api/v1/state read after
        # startup already observation-backs the v2 fields.
        self._callback(self._radio.baseline_observations())
        self._task = asyncio.get_running_loop().create_task(
            self._run(), name="serial-mock-observation-poller"
        )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _run(self) -> None:
        try:
            while True:
                if self._command_queue is not None and self._command_queue.has_commands:
                    changed = await self._drain_commands()
                    if changed:
                        self._callback(self._radio.baseline_observations())
                if self._command_queue is not None:
                    await self._command_queue.wait(timeout=self._DRAIN_INTERVAL)
                else:
                    await asyncio.sleep(self._DRAIN_INTERVAL)
        except asyncio.CancelledError:
            pass

    async def _drain_commands(self) -> bool:
        assert self._command_queue is not None
        changed = False
        for entry in self._command_queue.drain_entries():
            if entry.future is not None and entry.future.cancelled():
                continue
            try:
                await self._execute_command(entry.command)
                changed = True
                if entry.future is not None and not entry.future.done():
                    entry.future.set_result(None)
            except Exception as exc:  # noqa: BLE001 — surface to the waiter
                self._mark_queued_command_failed(entry, exc)
                if entry.future is not None and not entry.future.done():
                    entry.future.set_exception(exc)
        return changed

    async def _execute_command(self, cmd: Any) -> None:
        from ...._poller_types import (
            PttOff,
            PttOn,
            SetAfLevel,
            SetAgc,
            SetAgcTimeConstant,
            SetAttenuator,
            SetAutoNotch,
            SetCompressor,
            SetCompressorLevel,
            SetCwPitch,
            SetDataMode,
            SetDualWatch,
            SetFilterWidth,
            SetFreq,
            SetManualNotch,
            SetMicGain,
            SetMode,
            SetMonitor,
            SetMonitorGain,
            SetNB,
            SetNBLevel,
            SetNR,
            SetNRLevel,
            SetPreamp,
            SetRfGain,
            SetSplit,
            SetSquelch,
            SetTunerStatus,
            SetTxFreqMonitor,
            SetVox,
        )

        radio = self._radio
        match cmd:
            case SetFreq(freq=freq, receiver=rx):
                await radio.set_freq(freq, receiver=rx)
            case SetMode(mode=mode, filter_width=fw, receiver=rx):
                await radio.set_mode(mode, filter_width=fw, receiver=rx)
            case SetDataMode(mode=mode_on, receiver=rx):
                await radio.set_data_mode(mode_on, receiver=rx)
            case PttOn():
                await radio.set_ptt(True)
            case PttOff():
                await radio.set_ptt(False)
            case SetRfGain(level=level, receiver=rx):
                await radio.set_rf_gain(level, receiver=rx)
            case SetAfLevel(level=level, receiver=rx):
                await radio.set_af_level(level, receiver=rx)
            case SetSquelch(level=level, receiver=rx):
                await radio.set_squelch(level, receiver=rx)
            case SetAttenuator(db=db, receiver=rx):
                await radio.set_attenuator_level(db, receiver=rx)
            case SetPreamp(level=level, receiver=rx):
                await radio.set_preamp(level, receiver=rx)
            case SetAgc(mode=value, receiver=rx):
                await radio.set_agc(value, receiver=rx)
            case SetAgcTimeConstant(value=value, receiver=rx):
                await radio.set_agc_time_constant(value, receiver=rx)
            case SetNRLevel(level=level, receiver=rx):
                await radio.set_nr_level(level, receiver=rx)
            case SetNBLevel(level=level, receiver=rx):
                await radio.set_nb_level(level, receiver=rx)
            case SetNR(on=on, receiver=rx):
                await radio.set_nr(on, receiver=rx)
            case SetNB(on=on, receiver=rx):
                await radio.set_nb(on, receiver=rx)
            case SetAutoNotch(on=on, receiver=rx):
                await radio.set_auto_notch(on, receiver=rx)
            case SetManualNotch(on=on, receiver=rx):
                await radio.set_manual_notch(on, receiver=rx)
            case SetFilterWidth(width=width, receiver=rx):
                await radio.set_filter_width(width, receiver=rx)
            case SetMicGain(level=level):
                await radio.set_mic_gain(level)
            case SetCompressorLevel(level=level):
                await radio.set_compressor_level(level)
            case SetMonitorGain(level=level):
                await radio.set_monitor_gain(level)
            case SetCwPitch(value=value):
                await radio.set_cw_pitch(value)
            case SetTunerStatus(value=value):
                await radio.set_tuner_status(value)
            case SetSplit(on=on):
                await radio.set_split(on)
            case SetCompressor(on=on):
                await radio.set_compressor(on)
            case SetMonitor(on=on):
                await radio.set_monitor(on)
            case SetVox(on=on):
                await radio.set_vox(on)
            case SetDualWatch(on=on):
                await radio.set_dual_watch(on)
            case SetTxFreqMonitor(on=on):
                await radio.set_tx_freq_monitor(on)
            case _:
                # Unsupported commands are intentionally ignored — the mock only
                # backs the v2-rendered field surface, not the full CI-V map.
                return

    @staticmethod
    def _mark_queued_command_failed(
        entry: "CommandQueueEntry",
        exc: BaseException,
    ) -> None:
        service = entry.command_service
        if service is None or entry.command_id is None:
            return
        params: dict[str, Any] = {
            "message": str(exc) or None,
            "session_id": entry.session_id,
        }
        if entry.source is not None:
            params["source"] = entry.source
        service.fail_command(entry.command_id, **params)


__all__ = [
    "DeterministicSerialCivLink",
    "SerialFrameCodec",
    "SerialFrameError",
    "SerialFrameOverflowError",
    "SerialFrameTimeoutError",
    "SerialMockRadio",
]
