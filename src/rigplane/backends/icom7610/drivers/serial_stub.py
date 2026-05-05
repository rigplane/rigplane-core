"""Deterministic serial-ready test doubles for backend regression gates."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from ....exceptions import CommandError
from ....profiles import RadioProfile, resolve_radio_profile
from ....radio_state import RadioState
from ...._state_cache import StateCache
from ....types import Mode


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
    rf_gain: int = 255
    af_level: int = 200
    squelch: int = 0
    nb_on: bool = False
    nr_on: bool = False
    digisel_on: bool = False
    ip_plus_on: bool = False
    attenuator_db: int = 0
    preamp_level: int = 1


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


__all__ = [
    "DeterministicSerialCivLink",
    "SerialFrameCodec",
    "SerialFrameError",
    "SerialFrameOverflowError",
    "SerialFrameTimeoutError",
    "SerialMockRadio",
]
