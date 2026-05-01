"""Synchronous (blocking) wrapper around :class:`~icom_lan.radio.IcomRadio`.

Provides the same API as the async version but runs an internal event loop
so callers don't need ``async/await``.

Example::

    from icom_lan.sync import IcomRadio

    with IcomRadio("192.168.1.100", username="u", password="p") as radio:
        print(radio.get_freq())
        radio.set_freq(14_074_000)
"""

import asyncio
from typing import Any, Callable, Coroutine, TypeVar

from icom_lan.audio import AudioPacket
from .ic705 import (
    prepare_ic705_data_profile as _prepare_ic705_data_profile,
    restore_ic705_data_profile as _restore_ic705_data_profile,
)
from .radio import IcomRadio as _AsyncIcomRadio, _DEFAULT_AUDIO_CODEC  # noqa: TID251
from icom_lan.core.capabilities import CAP_METERS, CAP_POWER_CONTROL
from icom_lan.core.types import AudioCodec, Mode, ScopeCompletionPolicy

T = TypeVar("T")

__all__ = ["IcomRadio"]


class IcomRadio:
    """Synchronous (blocking) wrapper for Icom radio LAN control.

    Wraps the async :class:`~icom_lan.radio.IcomRadio` with a dedicated
    event loop. All methods block until the operation completes.

    Args:
        host: Radio IP address or hostname.
        port: Control port (default 50001).
        username: Authentication username.
        password: Authentication password.
        radio_addr: CI-V address of the radio (default IC-7610 = 0x98).
        timeout: Operation timeout in seconds.
        audio_codec: Audio codec (default: stereo PCM 2ch 16-bit on dual-RX
            radios, auto-negotiated down to mono on single-RX firmware — see
            ``_DEFAULT_CODEC_PREFERENCE`` in ``types.py``).
        audio_sample_rate: Audio sample rate in Hz.
    """

    def __init__(
        self,
        host: str,
        port: int = 50001,
        username: str = "",
        password: str = "",
        radio_addr: int = 0x98,
        timeout: float = 5.0,
        audio_codec: AudioCodec | int = _DEFAULT_AUDIO_CODEC,
        audio_sample_rate: int = 48000,
    ) -> None:
        self._loop = asyncio.new_event_loop()
        self._radio = _AsyncIcomRadio(
            host,
            port=port,
            username=username,
            password=password,
            radio_addr=radio_addr,
            timeout=timeout,
            audio_codec=audio_codec,
            audio_sample_rate=audio_sample_rate,
        )

    def _run(self, coro: Coroutine[Any, Any, T]) -> T:
        """Run a coroutine on the internal event loop."""
        return self._loop.run_until_complete(coro)

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Connect to the radio (blocking)."""
        self._run(self._radio.connect())

    def disconnect(self) -> None:
        """Disconnect from the radio (blocking)."""
        self._run(self._radio.disconnect())

    @property
    def connected(self) -> bool:
        """Whether the radio is currently connected."""
        return self._radio.connected

    def __enter__(self) -> "IcomRadio":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore[no-untyped-def]
        self.disconnect()
        self._loop.close()

    # ------------------------------------------------------------------
    # Frequency
    # ------------------------------------------------------------------

    def get_freq(self) -> int:
        """Get the current operating frequency in Hz."""
        return self._run(self._radio.get_freq())

    def set_freq(self, freq_hz: int) -> None:
        """Set the operating frequency in Hz."""
        self._run(self._radio.set_freq(freq_hz))

    # Backward-compat aliases
    get_frequency = get_freq
    set_frequency = set_freq

    # ------------------------------------------------------------------
    # Mode
    # ------------------------------------------------------------------

    def get_mode(self) -> tuple[str, int | None]:
        """Get current mode as ``(name, filter)`` — Protocol-compatible."""
        return self._run(self._radio.get_mode())

    def get_mode_info(self) -> tuple[Mode, int | None]:
        """Get current mode and filter number (if reported)."""
        return self._run(self._radio.get_mode_info())

    def get_filter(self) -> int | None:
        """Get current filter number (1-3) when available."""
        return self._run(self._radio.get_filter())

    def set_filter(self, filter_width: int) -> None:
        """Set filter number (1-3) while keeping current mode."""
        self._run(self._radio.set_filter(filter_width))

    def set_mode(self, mode: str | Mode, filter_width: int | None = None) -> None:
        """Set the operating mode."""
        self._run(self._radio.set_mode(mode, filter_width))

    def get_data_mode(self) -> bool:
        """Read whether DATA mode is enabled."""
        return self._run(self._radio.get_data_mode())

    def set_data_mode(self, on: int | bool, receiver: int = 0) -> None:
        """Set DATA mode for the selected receiver."""
        self._run(self._radio.set_data_mode(on, receiver=receiver))

    # ------------------------------------------------------------------
    # Power
    # ------------------------------------------------------------------

    def get_rf_power(self) -> int:
        """Get the RF power level (0-255)."""
        if CAP_METERS not in self._radio.capabilities:
            raise AttributeError(
                "get_rf_power requires a radio that implements MetersCapable"
            )
        return self._run(self._radio.get_rf_power())

    def set_rf_power(self, level: int) -> None:
        """Set the RF power level (0-255)."""
        if CAP_POWER_CONTROL not in self._radio.capabilities:
            raise AttributeError(
                "set_rf_power requires a radio that implements PowerControlCapable"
            )
        self._run(self._radio.set_rf_power(level))

    # Backward-compat aliases
    get_power = get_rf_power
    set_power = set_rf_power

    # ------------------------------------------------------------------
    # Meters
    # ------------------------------------------------------------------

    def get_s_meter(self) -> int:
        """Read the S-meter value (0-255)."""
        if CAP_METERS not in self._radio.capabilities:
            raise AttributeError(
                "get_s_meter requires a radio that implements MetersCapable"
            )
        return self._run(self._radio.get_s_meter())

    def get_swr(self) -> float:
        """Read the SWR as a calibrated ratio (>= 1.0).

        Returns the calibrated float per
        :meth:`MetersCapable.get_swr`. For the raw 0–255 BCD reading
        program against :meth:`get_swr_meter` instead.
        """
        if CAP_METERS not in self._radio.capabilities:
            raise AttributeError(
                "get_swr requires a radio that implements MetersCapable"
            )
        return float(self._run(self._radio.get_swr()))

    def get_alc_meter(self) -> int:
        """Read the ALC meter (raw 0-255).

        Returns the unscaled meter value mirroring
        :meth:`MetersCapable.get_alc_meter`.
        """
        if CAP_METERS not in self._radio.capabilities:
            raise AttributeError(
                "get_alc_meter requires a radio that implements MetersCapable"
            )
        return self._run(self._radio.get_alc_meter())

    def get_swr_meter(self) -> int:
        """Read the SWR meter (raw 0-255).

        Returns the unscaled meter value mirroring
        :meth:`MetersCapable.get_swr_meter`. For a calibrated SWR ratio
        (>= 1.0) use :meth:`get_swr` instead.
        """
        if CAP_METERS not in self._radio.capabilities:
            raise AttributeError(
                "get_swr_meter requires a radio that implements MetersCapable"
            )
        return self._run(self._radio.get_swr_meter())

    # ------------------------------------------------------------------
    # PTT
    # ------------------------------------------------------------------

    def set_ptt(self, on: bool) -> None:
        """Enable or disable PTT."""
        self._run(self._radio.set_ptt(on))

    # ------------------------------------------------------------------
    # VFO / Split
    # ------------------------------------------------------------------

    def vfo_equalize(self) -> None:
        """Copy VFO A to VFO B (dispatches to canonical async method)."""
        # Inline the dispatch previously hidden behind the deprecated
        # ``vfo_equalize`` alias: dual-RX profiles route to
        # ``equalize_main_sub`` (MAIN→SUB), single-RX profiles route to
        # ``equalize_vfo_ab(0)`` (A→B).
        if self._radio.profile.receiver_count > 1:
            self._run(self._radio.equalize_main_sub())
        else:
            self._run(self._radio.equalize_vfo_ab(0))

    def vfo_exchange(self) -> None:
        """Swap VFO A and B (dispatches to canonical async method)."""
        # Inline the dispatch previously hidden behind the deprecated
        # ``vfo_exchange`` alias: dual-RX profiles route to ``swap_main_sub``
        # (MAIN↔SUB), single-RX profiles route to ``swap_vfo_ab(0)`` (A↔B).
        if self._radio.profile.receiver_count > 1:
            self._run(self._radio.swap_main_sub())
        else:
            self._run(self._radio.swap_vfo_ab(0))

    def set_split(self, on: bool) -> None:
        """Enable or disable split mode."""
        self._run(self._radio.set_split(on))

    def get_split(self) -> bool:
        """Read split mode state."""
        return self._run(self._radio.get_split())

    # ------------------------------------------------------------------
    # Attenuator / Preamp
    # ------------------------------------------------------------------

    def get_attenuator_level(self) -> int:
        """Read attenuator level in dB."""
        return self._run(self._radio.get_attenuator_level())

    def get_attenuator(self) -> bool:
        """Read attenuator state."""
        return self._run(self._radio.get_attenuator())

    def set_attenuator_level(self, db: int) -> None:
        """Set attenuator level in dB."""
        self._run(self._radio.set_attenuator_level(db))

    def set_attenuator(self, on: bool) -> None:
        """Enable or disable the attenuator."""
        self._run(self._radio.set_attenuator(on))

    def get_preamp(self) -> int:
        """Read preamp level (0=off, 1=PREAMP1, 2=PREAMP2)."""
        return self._run(self._radio.get_preamp())

    def set_preamp(self, level: int = 1) -> None:
        """Set preamp level (0=off, 1=PREAMP1, 2=PREAMP2)."""
        self._run(self._radio.set_preamp(level))

    def get_digisel(self) -> bool:
        """Read DIGI-SEL status."""
        return self._run(self._radio.get_digisel())

    def set_digisel(self, on: bool) -> None:
        """Set DIGI-SEL status."""
        self._run(self._radio.set_digisel(on))

    def set_squelch(self, level: int, receiver: int = 0) -> None:
        """Set squelch level (0-255, 0=open)."""
        self._run(self._radio.set_squelch(level, receiver=receiver))

    def get_data_off_mod_input(self) -> int:
        """Read the Data Off modulation input source."""
        return self._run(self._radio.get_data_off_mod_input())

    def set_data_off_mod_input(self, source: int) -> None:
        """Set the Data Off modulation input source."""
        self._run(self._radio.set_data_off_mod_input(source))

    def get_data1_mod_input(self) -> int:
        """Read the DATA1 modulation input source."""
        return self._run(self._radio.get_data1_mod_input())

    def set_data1_mod_input(self, source: int) -> None:
        """Set the DATA1 modulation input source."""
        self._run(self._radio.set_data1_mod_input(source))

    def get_vox(self) -> bool:
        """Read VOX status."""
        return self._run(self._radio.get_vox())

    def set_vox(self, on: bool) -> None:
        """Set VOX status."""
        self._run(self._radio.set_vox(on))

    # ------------------------------------------------------------------
    # State snapshot/restore
    # ------------------------------------------------------------------

    def snapshot_state(self) -> dict[str, object]:
        """Best-effort snapshot of core rig state."""
        return self._run(self._radio.snapshot_state())

    def restore_state(self, state: dict[str, object]) -> None:
        """Best-effort restore of snapshot_state()."""
        self._run(self._radio.restore_state(state))

    def prepare_ic705_data_profile(
        self,
        *,
        frequency_hz: int,
        mode: str = "FM",
        data_off_mod_input: int | None = None,
        data1_mod_input: int | None = None,
        disable_vox: bool = True,
        squelch_level: int | None = 0,
        enable_scope: bool = False,
        scope_output: bool = False,
        scope_policy: ScopeCompletionPolicy | str = ScopeCompletionPolicy.FAST,
        scope_timeout: float = 5.0,
        scope_mode: int | None = 0,
        scope_span: int | None = 7,
    ) -> dict[str, object]:
        """Prepare the radio for IC-705 data/packet workflows and return a snapshot."""
        return self._run(
            _prepare_ic705_data_profile(
                self._radio,
                frequency_hz=frequency_hz,
                mode=mode,
                data_off_mod_input=data_off_mod_input,
                data1_mod_input=data1_mod_input,
                disable_vox=disable_vox,
                squelch_level=squelch_level,
                enable_scope=enable_scope,
                scope_output=scope_output,
                scope_policy=scope_policy,
                scope_timeout=scope_timeout,
                scope_mode=scope_mode,
                scope_span=scope_span,
            )
        )

    def restore_ic705_data_profile(self, snapshot: dict[str, object]) -> None:
        """Restore a snapshot from :meth:`prepare_ic705_data_profile`."""
        self._run(_restore_ic705_data_profile(self._radio, snapshot))

    # ------------------------------------------------------------------
    # CW
    # ------------------------------------------------------------------

    def send_cw_text(self, text: str) -> None:
        """Send CW text."""
        self._run(self._radio.send_cw_text(text))

    def stop_cw_text(self) -> None:
        """Stop CW sending."""
        self._run(self._radio.stop_cw_text())

    # ------------------------------------------------------------------
    # Power control
    # ------------------------------------------------------------------

    def power_control(self, on: bool) -> None:
        """Power on/off the radio."""
        if CAP_POWER_CONTROL not in self._radio.capabilities:
            raise AttributeError(
                "power_control requires a radio that implements PowerControlCapable"
            )
        self._run(self._radio.set_powerstat(on))

    # ------------------------------------------------------------------
    # Scope
    # ------------------------------------------------------------------

    def enable_scope(
        self,
        *,
        output: bool = True,
        policy: str = "verify",
        timeout: float = 5.0,
    ) -> None:
        """Enable scope display and optional wave data output."""
        self._run(
            self._radio.enable_scope(output=output, policy=policy, timeout=timeout)
        )

    def set_scope_mode(self, mode: int) -> None:
        """Set the scope mode."""
        self._run(self._radio.set_scope_mode(mode))

    def set_scope_span(self, span: int) -> None:
        """Set the scope span preset index."""
        self._run(self._radio.set_scope_span(span))

    # ------------------------------------------------------------------
    # Audio
    # ------------------------------------------------------------------

    def start_audio_rx_opus(
        self,
        callback: Callable[[AudioPacket | None], None],
        *,
        jitter_depth: int = 5,
    ) -> None:
        """Start receiving Opus audio from the radio (blocking setup)."""
        self._run(self._radio.start_audio_rx_opus(callback, jitter_depth=jitter_depth))

    def stop_audio_rx_opus(self) -> None:
        """Stop Opus RX audio."""
        self._run(self._radio.stop_audio_rx_opus())

    def start_audio_tx_opus(self) -> None:
        """Start Opus TX audio."""
        self._run(self._radio.start_audio_tx_opus())

    def push_audio_tx_opus(self, opus_data: bytes) -> None:
        """Send an Opus audio frame to the radio."""
        self._run(self._radio.push_audio_tx_opus(opus_data))

    def stop_audio_tx_opus(self) -> None:
        """Stop Opus TX audio."""
        self._run(self._radio.stop_audio_tx_opus())

    def get_audio_stats(self) -> dict[str, bool | int | float | str]:
        """Return runtime audio stats for the active stream."""
        return self._radio.get_audio_stats()

    @property
    def audio_codec(self) -> AudioCodec:
        """Configured audio codec."""
        return self._radio.audio_codec

    @property
    def audio_sample_rate(self) -> int:
        """Configured audio sample rate."""
        return self._radio.audio_sample_rate
