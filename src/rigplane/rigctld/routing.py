"""Rigctld vendor-specific routing strategies.

Provides routing for level/func/dump_state/info commands that differ
between radio vendors (Icom CI-V vs Yaesu CAT).

Architecture::

    RigctldHandler
        ├── _routing: RigctldRouting  (picked at __init__)
        │       ├── get_level(level) → RigctldResponse
        │       ├── set_level(level, value) → RigctldResponse
        │       ├── get_func(func) → RigctldResponse
        │       ├── set_func(func, on) → RigctldResponse
        │       ├── dump_state() → list[str]
        │       └── get_info() → str
        └── core commands: freq, mode, PTT, VFO, etc. (shared)
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..radio_protocol import Radio
    from .handler import _FallbackRigState  # noqa: TID251

from ..core.state_pipeline_contracts import FieldPath
from .contract import HamlibError, RigctldResponse  # noqa: TID251

logger = logging.getLogger(__name__)

__all__ = ["RigctldRouting", "YaesuRouting", "create_routing"]

_StateObserver = Callable[[FieldPath, object], None]


def _ok() -> RigctldResponse:
    return RigctldResponse(values=["RPRT 0"])


def _err(code: HamlibError) -> RigctldResponse:
    return RigctldResponse(error=int(code))


def _is_normalized_float(value: Any) -> bool:
    return (
        isinstance(value, float) and not isinstance(value, bool) and 0.0 <= value <= 1.0
    )


def _format_normalized_or_raw_float(value: Any, *, raw_divisor: float) -> str:
    if _is_normalized_float(value):
        return f"{value:.6f}"
    return _format_raw_scaled_float(value, raw_divisor=raw_divisor)


def _format_raw_scaled_float(value: Any, *, raw_divisor: float) -> str:
    return f"{int(value) / raw_divisor:.6f}"


def _format_strength(value: Any, *, raw_divisor: float) -> str:
    raw = int(value)
    return str(round((raw / raw_divisor) * 114.0 - 54.0))


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class RigctldRouting(Protocol):
    """Vendor-specific rigctl level/func routing.

    The optional ``vfo`` keyword on ``get_level``/``set_level``/
    ``get_func``/``set_func`` carries the Hamlib VFO label
    (``"VFOA"``/``"VFOB"``/``"currVFO"`` or ``None``) for vendors
    that need per-receiver routing under ``vfo_opt`` (see issue #1345).
    Defaults to ``None`` for backwards compatibility — implementers
    that do not care about VFO routing may safely ignore it.
    """

    async def get_level(
        self, level: str, *, vfo: str | None = None
    ) -> RigctldResponse: ...
    async def set_level(
        self, level: str, value: float, *, vfo: str | None = None
    ) -> RigctldResponse: ...
    async def get_func(
        self, func: str, *, vfo: str | None = None
    ) -> RigctldResponse: ...
    async def set_func(
        self, func: str, on: bool, *, vfo: str | None = None
    ) -> RigctldResponse: ...
    def dump_state(self) -> list[str]: ...
    def get_info(self) -> str: ...


# ---------------------------------------------------------------------------
# Yaesu CAT routing
# ---------------------------------------------------------------------------

_YAESU_DUMP_STATE: list[str] = [
    "0",  # protocol version
    "2028",  # rig model — overridden per-radio from TOML by YaesuRouting.dump_state
    "1",  # ITU region
    "30000.000000 56000000.000000 0x1ff -1 -1 0x3 0xf",
    "0 0 0 0 0 0 0",
    "1800000.000000 54000000.000000 0x1ff 5000 100000 0x3 0xf",
    "0 0 0 0 0 0 0",
    "0x1ff 1",
    "0 0",
    "0x1ff 3000",
    "0x1ff 2400",
    "0x1ff 1800",
    "0 0",
    "9999",  # max_rit
    "9999",  # max_xit
    "0",
    "0",
    "0",  # preamp
    "0",  # attenuator
    "0x00051A0E",  # has_get_func
    "0x00051A0E",  # has_set_func
    "0x540DFB3B",  # has_get_level
    "0x000DFB3B",  # has_set_level
    "0",
    "0",
]


class YaesuRouting:
    """Yaesu CAT routing for rigctl level/func commands."""

    _CW_PITCH_BASE: int = 300
    _CW_PITCH_STEP: int = 10
    _S_METER = FieldPath.receiver("main", "meters", "s_meter")
    _LEVEL_PATHS: dict[str, FieldPath] = {
        "STRENGTH": _S_METER,
        "RAWSTR": _S_METER,
        "AF": FieldPath.receiver("main", "operator_controls", "af_level"),
        "RF": FieldPath.receiver("main", "operator_controls", "rf_gain"),
        "SQL": FieldPath.receiver("main", "operator_controls", "squelch"),
        "NB": FieldPath.receiver("main", "operator_controls", "nb_level"),
        "NR": FieldPath.receiver("main", "operator_controls", "nr_level"),
        "PREAMP": FieldPath.receiver("main", "operator_controls", "preamp"),
        "ATT": FieldPath.receiver("main", "operator_controls", "att"),
    }
    _FUNC_PATHS: dict[str, FieldPath] = {
        "NB": FieldPath.receiver("main", "operator_toggles", "nb"),
        "NR": FieldPath.receiver("main", "operator_toggles", "nr"),
    }

    def __init__(
        self, radio: "Radio", cache: "_FallbackRigState", max_power_w: float
    ) -> None:
        self._radio = radio
        self._cache = cache
        self._max_power_w = max_power_w
        self._state_observer: _StateObserver | None = None

    def set_state_observer(self, observer: _StateObserver | None) -> None:
        """Install a handler-owned callback for routed backend observations."""

        self._state_observer = observer

    def state_path_for_level(
        self, level: str, *, receiver: int = 0
    ) -> FieldPath | None:
        """Return the StateStore path that can satisfy a Yaesu rigctl level."""

        del receiver
        return self._LEVEL_PATHS.get(level)

    def state_path_for_func(self, func: str, *, receiver: int = 0) -> FieldPath | None:
        """Return the StateStore path that can satisfy a Yaesu rigctl function."""

        del receiver
        return self._FUNC_PATHS.get(func)

    def format_state_level(self, level: str, value: Any) -> RigctldResponse | None:
        """Format a projected StateStore value with Yaesu rigctl scaling."""

        try:
            if level == "STRENGTH":
                return RigctldResponse(
                    values=[_format_strength(value, raw_divisor=255.0)]
                )
            if level == "RAWSTR":
                return RigctldResponse(values=[str(int(value))])
            if level in ("AF", "RF", "SQL"):
                return RigctldResponse(
                    values=[_format_normalized_or_raw_float(value, raw_divisor=255.0)]
                )
            if level == "NB":
                return RigctldResponse(
                    values=[_format_raw_scaled_float(value, raw_divisor=10.0)]
                )
            if level == "NR":
                return RigctldResponse(
                    values=[_format_raw_scaled_float(value, raw_divisor=15.0)]
                )
            if level == "PREAMP":
                return RigctldResponse(values=[str(int(value))])
            if level == "ATT":
                return RigctldResponse(values=[str(int(bool(value)))])
        except (TypeError, ValueError):
            logger.debug("rigctld: invalid Yaesu StateStore level %s=%r", level, value)
        return None

    def format_state_func(self, func: str, value: Any) -> RigctldResponse | None:
        """Format a projected StateStore value with Yaesu rigctl func semantics."""

        if func in self._FUNC_PATHS:
            return RigctldResponse(values=[str(int(bool(value)))])
        return None

    def _observe(self, path: FieldPath | None, value: object) -> None:
        observer = self._state_observer
        if observer is None or path is None:
            return
        observer(path, value)

    # -- levels --------------------------------------------------------------

    async def get_level(self, level: str, *, vfo: str | None = None) -> RigctldResponse:
        # ``vfo`` accepted for protocol conformance (issue #1345). Yaesu CAT
        # is single-receiver; routing per VFO is not meaningful, ignore.
        del vfo
        radio = self._radio

        if level in ("STRENGTH", "RAWSTR"):
            raw = await radio.get_s_meter()
            self._cache.update_s_meter(raw)
            self._observe(self.state_path_for_level(level), raw)
            if level == "STRENGTH":
                return RigctldResponse(
                    values=[str(round((raw / 255.0) * 114.0 - 54.0))]
                )
            return RigctldResponse(values=[str(raw)])

        if level == "RFPOWER":
            raw = await radio.get_rf_power()
            n = raw / self._max_power_w
            self._cache.update_rf_power(n)
            return RigctldResponse(values=[f"{n:.6f}"])

        if level == "SWR":
            swr = float(await radio.get_swr())
            self._cache.update_swr(swr)
            return RigctldResponse(values=[f"{swr:.6f}"])

        # 0–255 → 0.0–1.0
        if level == "SQL":
            raw = await radio.get_squelch()
            self._observe(self.state_path_for_level(level), raw)
            return RigctldResponse(values=[f"{raw / 255.0:.6f}"])
        if level in ("AF", "RF"):
            m = {"AF": "get_af_level", "RF": "get_rf_gain"}[level]
            raw = await getattr(radio, m)()
            self._observe(self.state_path_for_level(level), raw)
            return RigctldResponse(values=[f"{raw / 255.0:.6f}"])

        # 0–100 → 0.0–1.0
        if level in ("MICGAIN", "MONITOR_GAIN", "COMP"):
            m = {
                "MICGAIN": "get_mic_gain",
                "MONITOR_GAIN": "get_monitor_level",
                "COMP": "get_compressor_level",
            }[level]
            return RigctldResponse(values=[f"{await getattr(radio, m)() / 100.0:.6f}"])

        if level == "NB":
            raw = await radio.get_nb_level()
            self._observe(self.state_path_for_level(level), raw)
            return RigctldResponse(values=[f"{raw / 10.0:.6f}"])
        if level == "NR":
            raw = await radio.get_nr_level()
            self._observe(self.state_path_for_level(level), raw)
            return RigctldResponse(values=[f"{raw / 15.0:.6f}"])
        if level == "NOTCHF":
            _, freq_idx = await radio.get_manual_notch()
            return RigctldResponse(values=[str(freq_idx)])
        if level == "IFSHIFT":
            return RigctldResponse(values=[str(await radio.get_if_shift())])
        if level == "CWPITCH":
            # radio.get_cw_pitch returns Hz directly (Yaesu backend converts
            # idx → Hz internally per CwControlCapable contract).
            return RigctldResponse(values=[str(await radio.get_cw_pitch())])
        if level == "KEYSPD":
            return RigctldResponse(values=[str(await radio.get_key_speed())])

        # Meters
        if level in ("COMP_METER", "ID_METER", "VD_METER"):
            m = {
                "COMP_METER": "get_comp_meter",
                "ID_METER": "get_id_meter",
                "VD_METER": "get_vd_meter",
            }[level]
            return RigctldResponse(values=[f"{await getattr(radio, m)() / 255.0:.6f}"])

        if level == "PREAMP":
            value = await radio.get_preamp()
            self._observe(self.state_path_for_level(level), value)
            return RigctldResponse(values=[str(value)])
        if level == "ATT":
            value = await radio.get_attenuator()
            self._observe(self.state_path_for_level(level), bool(value))
            return RigctldResponse(values=[str(int(value))])

        return _err(HamlibError.EINVAL)

    async def set_level(
        self, level: str, value: float, *, vfo: str | None = None
    ) -> RigctldResponse:
        # ``vfo`` accepted for protocol conformance (issue #1345); ignored.
        del vfo
        radio = self._radio

        if level == "RFPOWER":
            await radio.set_power(round(value * self._max_power_w))
            return _ok()

        if level in ("AF", "RF", "SQL"):
            m = {"AF": "set_af_level", "RF": "set_rf_gain", "SQL": "set_squelch"}[level]
            await getattr(radio, m)(max(0, min(255, round(value * 255))))
            return _ok()

        if level in ("MICGAIN", "MONITOR_GAIN", "COMP"):
            m = {
                "MICGAIN": "set_mic_gain",
                "MONITOR_GAIN": "set_monitor_level",
                "COMP": "set_compressor_level",
            }[level]
            await getattr(radio, m)(max(0, min(100, round(value * 100))))
            return _ok()

        if level == "NB":
            await radio.set_nb_level(max(0, min(10, round(value * 10))))
            return _ok()
        if level == "NR":
            await radio.set_nr_level(max(0, min(15, round(value * 15))))
            return _ok()
        if level == "NOTCHF":
            await radio.set_notch_filter(round(value))
            return _ok()
        if level == "IFSHIFT":
            await radio.set_if_shift(round(value))
            return _ok()
        if level == "CWPITCH":
            # radio.set_cw_pitch accepts Hz directly and clamps to FTX-1 range
            # (300-1050) internally. Clamp here too for hamlib compatibility
            # so an out-of-range hamlib value never bubbles a ValueError.
            hz = max(
                self._CW_PITCH_BASE,
                min(
                    self._CW_PITCH_BASE + 75 * self._CW_PITCH_STEP,
                    round(value),
                ),
            )
            await radio.set_cw_pitch(hz)
            return _ok()
        if level == "KEYSPD":
            await radio.set_key_speed(round(value))
            return _ok()
        if level == "PREAMP":
            await radio.set_preamp(round(value))
            return _ok()
        if level == "ATT":
            await radio.set_attenuator(round(value))
            return _ok()

        return _err(HamlibError.EINVAL)

    # -- funcs ---------------------------------------------------------------

    async def get_func(self, func: str, *, vfo: str | None = None) -> RigctldResponse:
        # ``vfo`` accepted for protocol conformance (issue #1345); ignored.
        del vfo
        radio = self._radio

        if func == "VOX":
            return RigctldResponse(values=[str(int(await radio.get_vox()))])
        if func == "TUNER":
            return RigctldResponse(
                values=[str(int(await radio.get_tuner_status() > 0))]
            )
        if func == "COMP":
            return RigctldResponse(values=[str(int(await radio.get_processor()))])
        if func == "NB":
            value = await radio.get_nb_level() > 0
            self._observe(self.state_path_for_func(func), value)
            return RigctldResponse(values=[str(int(value))])
        if func == "NR":
            value = await radio.get_nr_level() > 0
            self._observe(self.state_path_for_func(func), value)
            return RigctldResponse(values=[str(int(value))])
        if func == "LOCK":
            return RigctldResponse(values=[str(int(await radio.get_dial_lock()))])
        if func == "SPLIT":
            return RigctldResponse(values=[str(int(await radio.get_split()))])
        if func == "AGC":
            return RigctldResponse(values=[str(int(await radio.get_agc() > 0))])
        return _err(HamlibError.EINVAL)

    async def set_func(
        self, func: str, on: bool, *, vfo: str | None = None
    ) -> RigctldResponse:
        # ``vfo`` accepted for protocol conformance (issue #1345); ignored.
        del vfo
        radio = self._radio

        if func == "VOX":
            await radio.set_vox(on)
            return _ok()
        if func == "TUNER":
            await radio.set_tuner_status(1 if on else 0)
            return _ok()
        if func == "COMP":
            await radio.set_processor(on)
            return _ok()
        if func == "NB":
            await radio.set_nb(on)
            return _ok()
        if func == "NR":
            await radio.set_nr(on)
            return _ok()
        if func == "LOCK":
            await radio.set_dial_lock(on)
            return _ok()
        if func == "SPLIT":
            await radio.set_split(on)
            return _ok()
        if func == "AGC":
            await radio.set_agc(1 if on else 0)
            return _ok()

        return _err(HamlibError.EINVAL)

    # -- dump / info ---------------------------------------------------------

    def dump_state(self) -> list[str]:
        state = list(_YAESU_DUMP_STATE)
        # Substitute the rig model from the radio's TOML config (closes #441).
        # Default 2028 (FTX-1) if the radio doesn't expose hamlib_model_id.
        model_id = getattr(self._radio, "hamlib_model_id", 2028)
        state[1] = str(int(model_id))
        return state

    def get_info(self) -> str:
        raw_model = getattr(self._radio, "model", "Yaesu")
        model = raw_model if isinstance(raw_model, str) and raw_model else "Yaesu"
        return f"Yaesu {model} (rigplane)"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_routing(
    radio: "Radio",
    cache: "_FallbackRigState",
    max_power_w: float = 100.0,
) -> RigctldRouting | None:
    """Create a vendor-specific :class:`RigctldRouting` for ``radio``.

    Dispatches via the public
    :class:`~rigplane.core.radio_protocol.RigctldRoutable` Protocol:
    radios that implement ``rigctld_routing(cache, max_power_w)`` get
    their custom strategy (Yaesu CAT today; Kenwood TS-590 or others
    in the future). Radios that do not — Icom CI-V — return ``None``
    and the handler's built-in Icom routing is used as the default
    path.
    """
    from rigplane.core.radio_protocol import RigctldRoutable

    if isinstance(radio, RigctldRoutable):
        return radio.rigctld_routing(cache, max_power_w)
    return None
