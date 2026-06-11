"""Synchronous (blocking) wrapper around :class:`~rigplane.radio.IcomRadio`.

Provides the same API as the async version but runs an internal event loop
so callers don't need ``async/await``.

Example::

    from rigplane.sync import IcomRadio

    with IcomRadio("192.168.1.100", username="u", password="p") as radio:
        print(radio.get_freq())
        radio.set_freq(14_074_000)
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, TypeVar

from rigplane.audio import AudioPacket
from rigplane.core.command_service import (
    CommandExecutionResult,
    CommandService,
    command_intent_from_request,
    command_response_observation,
)
from rigplane.core.state_pipeline_contracts import CommandIntent, Observation
from rigplane.core.state_store import StateStore
from .ic705 import (
    prepare_ic705_data_profile as _prepare_ic705_data_profile,
    restore_ic705_data_profile as _restore_ic705_data_profile,
)
from .radio import IcomRadio as _AsyncIcomRadio, _DEFAULT_AUDIO_CODEC  # noqa: TID251
from rigplane.core.capabilities import CAP_METERS, CAP_POWER_CONTROL
from rigplane.core.types import AudioCodec, Mode, ScopeCompletionPolicy

T = TypeVar("T")

__all__ = ["IcomRadio"]


@dataclass(slots=True)
class _SyncCommandExecutor:
    wrapper: "IcomRadio"

    async def execute(self, intent: CommandIntent) -> CommandExecutionResult:
        radio = self.wrapper._radio
        params = intent.params
        if intent.name == "set_freq":
            await radio.set_freq(int(params["freq_hz"]))
        elif intent.name == "set_filter":
            await radio.set_filter(int(params["filter_num"]))
        elif intent.name == "set_mode":
            await radio.set_mode(params["mode"], params.get("filter_width"))
        elif intent.name == "set_data_mode":
            await radio.set_data_mode(
                params["value"],
                receiver=int(params.get("receiver", 0)),
            )
        elif intent.name == "set_rf_power":
            await radio.set_rf_power(int(params["value"]))
        elif intent.name == "set_ptt":
            await radio.set_ptt(bool(params["ptt"]))
        elif intent.name == "set_split":
            await radio.set_split(bool(params["split"]))
        elif intent.name == "set_attenuator_level":
            await radio.set_attenuator_level(
                int(params["att"]),
                receiver=int(params.get("receiver", 0)),
            )
        elif intent.name == "set_preamp":
            await radio.set_preamp(
                int(params["preamp"]),
                receiver=int(params.get("receiver", 0)),
            )
        elif intent.name == "set_squelch":
            await radio.set_squelch(
                int(params["squelch"]),
                receiver=int(params.get("receiver", 0)),
            )
        elif intent.name == "set_powerstat":
            await radio.set_powerstat(bool(params["power_on"]))
        else:
            raise ValueError(f"unsupported public API command intent: {intent.name!r}")

        observations: tuple[Observation, ...] = ()
        if intent.target is not None:
            observations = (
                command_response_observation(
                    intent,
                    timestamp_monotonic=time.monotonic(),
                    provider="public_api",
                ),
            )
        return CommandExecutionResult(observations=observations)


class IcomRadio:
    """Synchronous (blocking) wrapper for Icom radio LAN control.

    Wraps the async :class:`~rigplane.radio.IcomRadio` with a dedicated
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
        audio_sample_rate: Audio sample rate in Hz. Omit to use the radio
            profile's LAN audio policy.
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
        audio_sample_rate: int | None = None,
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
        # Non-canonical, non-decaying store (MOR-432): the synchronous client
        # facade has no async event loop running a StateFreshnessService, so
        # this store never ages fields to STALE. It backs the local
        # CommandService only and is not a production state-delivery store
        # (the web/rigctld servers wire and drive freshness over their own
        # canonical stores).
        self._state_store = StateStore()
        self._command_service = CommandService(
            executor=_SyncCommandExecutor(self),
            state_store=self._state_store,
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
        self._run(
            self._command_service.execute(
                command_intent_from_request(
                    "set_freq",
                    {"freq": freq_hz, "receiver": 0},
                    source="public_api",
                )
            )
        )

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
        self._run(
            self._command_service.execute(
                command_intent_from_request(
                    "set_filter",
                    {"filter_num": filter_width, "receiver": 0},
                    source="public_api",
                )
            )
        )

    def set_mode(self, mode: str | Mode, filter_width: int | None = None) -> None:
        """Set the operating mode."""
        mode_name = mode.name if isinstance(mode, Mode) else str(mode)
        self._run(
            self._command_service.execute(
                command_intent_from_request(
                    "set_mode",
                    {"mode": mode_name, "filter_width": filter_width, "receiver": 0},
                    source="public_api",
                )
            )
        )

    def get_data_mode(self) -> bool:
        """Read whether DATA mode is enabled."""
        return self._run(self._radio.get_data_mode())

    def set_data_mode(self, on: int | bool, receiver: int = 0) -> None:
        """Set DATA mode for the selected receiver."""
        self._run(
            self._command_service.execute(
                command_intent_from_request(
                    "set_data_mode",
                    {"value": on, "receiver": receiver},
                    source="public_api",
                )
            )
        )

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
        self._run(
            self._command_service.execute(
                command_intent_from_request(
                    "set_rf_power",
                    {"value": level},
                    source="public_api",
                )
            )
        )

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
        self._run(
            self._command_service.execute(
                command_intent_from_request(
                    "set_ptt",
                    {"on": on},
                    source="public_api",
                )
            )
        )

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
        self._run(
            self._command_service.execute(
                command_intent_from_request(
                    "set_split",
                    {"on": on},
                    source="public_api",
                )
            )
        )

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
        self._run(
            self._command_service.execute(
                command_intent_from_request(
                    "set_attenuator_level",
                    {"db": db, "receiver": 0},
                    source="public_api",
                )
            )
        )

    def set_attenuator(self, on: bool) -> None:
        """Enable or disable the attenuator."""
        self._run(self._radio.set_attenuator(on))

    def get_preamp(self) -> int:
        """Read preamp level (0=off, 1=PREAMP1, 2=PREAMP2)."""
        return self._run(self._radio.get_preamp())

    def set_preamp(self, level: int = 1) -> None:
        """Set preamp level (0=off, 1=PREAMP1, 2=PREAMP2)."""
        self._run(
            self._command_service.execute(
                command_intent_from_request(
                    "set_preamp",
                    {"level": level, "receiver": 0},
                    source="public_api",
                )
            )
        )

    def get_digisel(self) -> bool:
        """Read DIGI-SEL status."""
        return self._run(self._radio.get_digisel())

    def set_digisel(self, on: bool) -> None:
        """Set DIGI-SEL status."""
        self._run(self._radio.set_digisel(on))

    def set_squelch(self, level: int, receiver: int = 0) -> None:
        """Set squelch level (0-255, 0=open)."""
        self._run(
            self._command_service.execute(
                command_intent_from_request(
                    "set_squelch",
                    {"level": level, "receiver": receiver},
                    source="public_api",
                )
            )
        )

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
        self._run(
            self._command_service.execute(
                command_intent_from_request(
                    "set_powerstat",
                    {"on": on},
                    source="public_api",
                )
            )
        )

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
