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
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..radio_protocol import Radio
    from .handler import _FallbackRigState  # noqa: TID251

from .contract import HamlibError, RigctldResponse  # noqa: TID251

logger = logging.getLogger(__name__)

__all__ = ["RigctldRouting", "YaesuRouting", "create_routing"]


def _ok() -> RigctldResponse:
    return RigctldResponse(values=["RPRT 0"])


def _err(code: HamlibError) -> RigctldResponse:
    return RigctldResponse(error=int(code))


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class RigctldRouting(Protocol):
    """Vendor-specific rigctl level/func routing."""

    async def get_level(self, level: str) -> RigctldResponse: ...
    async def set_level(self, level: str, value: float) -> RigctldResponse: ...
    async def get_func(self, func: str) -> RigctldResponse: ...
    async def set_func(self, func: str, on: bool) -> RigctldResponse: ...
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

    def __init__(
        self, radio: "Radio", cache: "_FallbackRigState", max_power_w: float
    ) -> None:
        self._radio = radio
        self._cache = cache
        self._max_power_w = max_power_w

    # -- levels --------------------------------------------------------------

    async def get_level(self, level: str) -> RigctldResponse:
        radio = self._radio

        if level in ("STRENGTH", "RAWSTR"):
            raw = await radio.get_s_meter()
            self._cache.update_s_meter(raw)
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
            return RigctldResponse(values=[f"{await radio.get_squelch() / 255.0:.6f}"])
        if level in ("AF", "RF"):
            m = {"AF": "get_af_level", "RF": "get_rf_gain"}[level]
            return RigctldResponse(values=[f"{await getattr(radio, m)() / 255.0:.6f}"])

        # 0–100 → 0.0–1.0
        if level in ("MICGAIN", "MONITOR_GAIN", "COMP"):
            m = {
                "MICGAIN": "get_mic_gain",
                "MONITOR_GAIN": "get_monitor_level",
                "COMP": "get_compressor_level",
            }[level]
            return RigctldResponse(values=[f"{await getattr(radio, m)() / 100.0:.6f}"])

        if level == "NB":
            return RigctldResponse(values=[f"{await radio.get_nb_level() / 10.0:.6f}"])
        if level == "NR":
            return RigctldResponse(values=[f"{await radio.get_nr_level() / 15.0:.6f}"])
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
            return RigctldResponse(values=[str(await radio.get_preamp())])
        if level == "ATT":
            return RigctldResponse(values=[str(int(await radio.get_attenuator()))])

        return _err(HamlibError.EINVAL)

    async def set_level(self, level: str, value: float) -> RigctldResponse:
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

    async def get_func(self, func: str) -> RigctldResponse:
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
            return RigctldResponse(values=[str(int(await radio.get_nb_level() > 0))])
        if func == "NR":
            return RigctldResponse(values=[str(int(await radio.get_nr_level() > 0))])
        if func == "LOCK":
            return RigctldResponse(values=[str(int(await radio.get_dial_lock()))])
        if func == "SPLIT":
            return RigctldResponse(values=[str(int(await radio.get_split()))])
        if func == "AGC":
            return RigctldResponse(values=[str(int(await radio.get_agc() > 0))])
        return _err(HamlibError.EINVAL)

    async def set_func(self, func: str, on: bool) -> RigctldResponse:
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
        return f"Yaesu {model} (icom-lan)"


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
    :class:`~icom_lan.core.radio_protocol.RigctldRoutable` Protocol:
    radios that implement ``rigctld_routing(cache, max_power_w)`` get
    their custom strategy (Yaesu CAT today; Kenwood TS-590 or others
    in the future). Radios that do not — Icom CI-V — return ``None``
    and the handler's built-in Icom routing is used as the default
    path.
    """
    from icom_lan.core.radio_protocol import RigctldRoutable

    if isinstance(radio, RigctldRoutable):
        return radio.rigctld_routing(cache, max_power_w)
    return None
