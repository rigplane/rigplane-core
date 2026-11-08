"""Web adapters for existing scope producer and ordered control contracts."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from rigplane.audio.scope_source import AudioPassband
from rigplane.audio.bus import STAGE_RX_POST_DSP
from rigplane.dsp.tap_registry import TapHandle
from rigplane.core.state_pipeline_contracts import FieldPath


if TYPE_CHECKING:
    from .server import WebServer  # noqa: TID251

_AUDIO_MODE_ALIASES = {
    "DATA-U": "USB",
    "DATA-L": "LSB",
    "AM-N": "AM",
    "FM-N": "FM",
    "DATA-FM": "FM",
    "DATA-FM-N": "FM",
}


class WebRadioScopeWriter:
    def __init__(self, server: WebServer) -> None:
        self._server = server

    def tuned_frequency(self, receiver: int) -> float:
        state = self._server._radio_state
        return float(state.main.freq if receiver == 0 else state.sub.freq)

    async def set_output(self, enabled: bool) -> None:
        await self._server._set_radio_scope_output(enabled)

    async def set_center(self, center_hz: float, span_hz: float, receiver: int) -> None:
        from .radio_poller import SetScopeMode, SetScopeSpan, SwitchScopeReceiver  # noqa: TID251

        span = self._server._get_profile().scope_span_presets_hz.index(int(span_hz))
        for command in (
            SwitchScopeReceiver(receiver),
            SetScopeMode(0),
            SetScopeSpan(span),
        ):
            if not await self._server._execute_scope_command(command):
                raise RuntimeError(f"radio scope {type(command).__name__} failed")


class WebScopePcmTap:
    def __init__(self, server: WebServer, *, sample_rate: int | None = None) -> None:
        if sample_rate is not None and (
            type(sample_rate) is not int or sample_rate <= 0
        ):
            raise ValueError("sample rate must be a positive integer")
        self._server = server
        self._sample_rate = sample_rate
        self._handle: TapHandle | None = None

    def facts(self) -> AudioPassband:
        snapshot = self._server.command_state_store.snapshot()
        read = self._server._active_primary_freq_mode_value
        frequency = read(snapshot, "freq_hz")
        width = read(snapshot, "filter_width")
        mode = read(snapshot, "mode")
        if (
            not isinstance(frequency, int)
            or frequency <= 0
            or not isinstance(width, int)
            or width <= 0
            or not isinstance(mode, str)
        ):
            raise ValueError(
                "audio FFT requires observed frequency, mode and filter width"
            )
        mode = _AUDIO_MODE_ALIASES.get(mode.upper(), mode.upper())
        if mode in ("CW-U", "CW-L"):
            try:
                pitch = snapshot.field(
                    FieldPath.global_("operator_controls", "cw_pitch")
                ).value
            except KeyError:
                raise ValueError("audio FFT requires observed CW pitch") from None
            if type(pitch) is not int or pitch <= 0:
                raise ValueError("audio FFT requires observed CW pitch")
            lower = mode == "CW-L"
            return AudioPassband(
                frequency,
                -width / 2,
                width / 2,
                "lower" if lower else "upper",
                pitch if lower else -pitch,
            )
        if mode in ("CW", "CW-R", "RTTY", "RTTY-R", "DV"):
            # Main behaviour (AudioFftScope): a symmetric VFO-centered crop
            # by the mode filter rule, full spectrum when the profile
            # declares no rule. Correct-side selection is MOR-4288.
            profile = self._server._get_profile_unless_provider_owned()
            data_mode = self._server._active_primary_freq_mode_value(
                snapshot, "data_mode"
            )
            if not isinstance(data_mode, int):
                data_mode = 0
            rule = (
                profile.resolve_filter_rule(mode, data_mode=data_mode)
                if profile is not None
                else None
            )
            max_hz = rule.max_hz if rule is not None else None
            if max_hz is None:
                if self._sample_rate is None:
                    raise ValueError(
                        f"audio FFT requires observed sample rate for mode {mode}: "
                        "no filter rule declares its bandwidth"
                    )
                half = self._sample_rate / 2
            else:
                half = max_hz / 2
            return AudioPassband(frequency, -half, half, "both")
        if mode not in ("USB", "LSB", "AM", "FM", "WFM"):
            raise ValueError(
                f"audio FFT RF mapping unavailable for mode {mode}: carrier offset is not observed"
            )
        lower = mode == "LSB"
        if mode in ("AM", "FM", "WFM"):
            return AudioPassband(frequency, -width / 2, width / 2, "both")
        low, high = (-width, 0) if lower else (0, width)
        return AudioPassband(frequency, low, high, "lower" if lower else "upper")

    async def acquire(self, callback: Callable[[bytes], None]) -> None:
        self.facts()
        broadcaster = self._server._audio_broadcaster
        self._handle = broadcaster.taps(STAGE_RX_POST_DSP).register(
            "scope-audio-fft", callback
        )
        await broadcaster.ensure_relay()

    async def release(self) -> None:
        broadcaster = self._server._audio_broadcaster
        if self._handle is not None:
            broadcaster.taps(STAGE_RX_POST_DSP).unregister(self._handle)
            self._handle = None
        await broadcaster.release_relay_if_idle()
