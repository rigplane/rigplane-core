"""StateStore projection for the TCI adapter (MOR-3821 C1, spec 5.4/5.5).

One shared object per server: :meth:`TciNotifier.init_burst` renders the
init burst (spec 5.4), :meth:`TciNotifier.refresh` maps a
``StateStore`` delta to the notification lines the leaf broadcasts.
Every read is store-first with a radio fallback and a documented default,
so a burst never fails before the first acquisition lands.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..audio.route import profile_data_mode_count
from ..core.state_pipeline_contracts import FieldPath
from ..core.state_store import SnapshotDelta, StateStore
from ..core.tx_observation import OBSERVED_PTT_PATH, ObservedPtt, project_observed_ptt
from .commands import (
    TCI_CHANNELS_COUNT,
    TCI_PROTOCOL_NAME,
    TCI_PROTOCOL_VERSION,
    TCI_TRX_COUNT,
    modulations_list,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AdapterState",
    "TciNotifier",
    "drive_percent",
    "project_state",
]

#: Store paths the burst and notifications project (mirrors the rigctld
#: seat's ``_freq_path``/``_mode_path``/``_data_mode_path``/``_level_path``
#: and the intent targets in ``core.command_service``).
_FREQ_A_PATH = FieldPath.active("main", "freq_mode", "freq_hz")
_FREQ_B_PATH = FieldPath.active("sub", "freq_mode", "freq_hz")
_MODE_A_PATH = FieldPath.active("main", "freq_mode", "mode")
_DATA_A_PATH = FieldPath.active("main", "freq_mode", "data_mode")
_MODE_B_PATH = FieldPath.active("sub", "freq_mode", "mode")
_DATA_B_PATH = FieldPath.active("sub", "freq_mode", "data_mode")
_SPLIT_PATH = FieldPath.global_("tx_state", "split")
_RIT_FREQ_PATH = FieldPath.global_("operator_controls", "rit_freq")
_RIT_ON_PATH = FieldPath.global_("tx_state", "rit_on")
_RIT_TX_PATH = FieldPath.global_("tx_state", "rit_tx")
_POWER_PATH = FieldPath.global_("operator_controls", "power_level")


@dataclass(frozen=True)
class AdapterState:
    """Everything one burst or notification round renders."""

    freq_a: int
    freq_b: int
    modulation: str
    split: bool
    rit_freq: int
    rit_on: bool
    rit_tx: bool
    power_native: int
    ptt: bool


def _store_value(store: StateStore, path: FieldPath) -> Any:
    """The store value, or a miss marker that is never a real reading."""
    try:
        return store.snapshot().field(path).value
    except KeyError:
        return None


def _mode_name(value: Any) -> str:
    """Normalize a store/radio mode to an uppercase name (never empty)."""
    name = getattr(value, "name", None)
    if isinstance(name, str) and name:
        return name.upper()
    if isinstance(value, str) and value:
        return value.upper()
    return "USB"


def _supports_data_mode(radio: Any) -> bool:
    """Whether the radio implements the DATA-mode command.

    Same probe ``rigctld`` uses (``RigctldHandler._supports_data_mode``),
    kept local because the adapter may not import the sibling ``rigctld``
    seat (``independence-top``): a name-only command query first, the
    method's presence for radios predating ``supports_command``.
    """
    supports = getattr(radio, "supports_command", None)
    if callable(supports):
        try:
            return bool(supports("set_data_mode"))
        except Exception:
            return False
    return callable(getattr(radio, "set_data_mode", None))


def _power_scale(radio: Any) -> float:
    """Native units per 100 drive points: max watts, else raw 255."""
    unit = getattr(radio, "native_power_unit", "raw_255")
    profile = getattr(radio, "profile", None)
    max_watts_raw = getattr(profile, "max_watts", None)
    max_watts: float | None = None
    if (
        isinstance(max_watts_raw, (int, float))
        and not isinstance(max_watts_raw, bool)
        and max_watts_raw > 0
    ):
        max_watts = float(max_watts_raw)
    watts_native = unit == "watts" or (unit is None and max_watts is not None)
    if watts_native and max_watts is not None:
        return max_watts
    return 255.0


def drive_percent(power_native: Any, radio: Any) -> int:
    """Map a native power level to the 0-100 ``drive`` domain."""
    try:
        native = float(power_native)
    except (TypeError, ValueError):
        return 0
    scale = _power_scale(radio)
    if not scale > 0:
        return 0
    return max(0, min(100, round(native / scale * 100)))


def _profile_modes(radio: Any) -> tuple[str, ...]:
    profile = getattr(radio, "profile", None)
    modes = getattr(profile, "modes", None)
    if not isinstance(modes, (tuple, list)) or not modes:
        return ("AM", "LSB", "USB", "CW", "FM")
    return tuple(str(mode).upper() for mode in modes)


def _freq_ranges(radio: Any) -> tuple[tuple[int, int], ...]:
    """Profile frequency ranges as (lo, hi); empty when unpublished."""
    profile = getattr(radio, "profile", None)
    ranges = getattr(profile, "freq_ranges", None)
    if not isinstance(ranges, (tuple, list)):
        return ()
    parsed = []
    for entry in ranges:
        try:
            parsed.append((int(entry.start), int(entry.end)))
        except (AttributeError, TypeError, ValueError):
            continue
    return tuple(parsed)


def _project_modulation(
    store: StateStore, fallback: str, *, channel_b: bool = False
) -> str:
    """Project a channel's TCI modulation name (spec 5.6)."""
    from .commands import TCI_TO_CORE_MODE

    mode_path = _MODE_B_PATH if channel_b else _MODE_A_PATH
    data_path = _DATA_B_PATH if channel_b else _DATA_A_PATH
    mode_value = _store_value(store, mode_path)
    if mode_value is None:
        return fallback
    data_value = _store_value(store, data_path)
    try:
        data_on = bool(int(data_value)) if data_value is not None else False
    except (TypeError, ValueError):
        data_on = False
    name = _mode_name(mode_value)
    if data_on and name == "LSB":
        return "DIGL"
    if data_on and name == "USB":
        return "DIGU"
    if not data_on and name in TCI_TO_CORE_MODE:
        return name
    # A Core mode with no TCI name, or DATA on a non-sideband mode
    # (spec 5.6): keep the last announced value.
    logger.debug("tci: unmapped mode %s data=%s; keeping %s", name, data_on, fallback)
    return fallback


async def project_state(
    radio: Any,
    store: StateStore,
    *,
    managed_keyed: bool = False,
    fallback_modulation: str = "USB",
) -> AdapterState:
    """Project the notifier state: store first, radio second, default last."""
    freq_a = _store_value(store, _FREQ_A_PATH)
    if freq_a is None:
        get_freq = getattr(radio, "get_freq", None)
        if callable(get_freq):
            try:
                freq_a = int(await get_freq())
            except Exception:
                freq_a = 0
        else:
            freq_a = 0
    try:
        freq_a = int(freq_a)
    except (TypeError, ValueError):
        freq_a = 0

    freq_b = _store_value(store, _FREQ_B_PATH)
    if freq_b is None:
        read_relative = getattr(radio, "read_relative_vfo", None)
        if callable(read_relative):
            try:
                freq_b = int((await read_relative(selected=False)).freq_hz)
            except Exception:
                freq_b = freq_a
        else:
            freq_b = freq_a
    try:
        freq_b = int(freq_b)
    except (TypeError, ValueError):
        freq_b = freq_a

    modulation = _project_modulation(store, fallback_modulation)

    split = _store_value(store, _SPLIT_PATH)
    if split is None:
        get_split = getattr(radio, "get_split", None)
        if callable(get_split):
            try:
                split = bool(await get_split())
            except Exception:
                split = False
        else:
            split = False
    split = bool(split)

    rit_freq = _store_value(store, _RIT_FREQ_PATH)
    if rit_freq is None:
        get_rit = getattr(radio, "get_rit_frequency", None)
        if callable(get_rit):
            try:
                rit_freq = int(await get_rit())
            except Exception:
                rit_freq = 0
        else:
            rit_freq = 0
    try:
        rit_freq = int(rit_freq)
    except (TypeError, ValueError):
        rit_freq = 0

    rit_on = _store_value(store, _RIT_ON_PATH)
    if rit_on is None:
        get_rit_status = getattr(radio, "get_rit_status", None)
        if callable(get_rit_status):
            try:
                rit_on = bool(await get_rit_status())
            except Exception:
                rit_on = False
        else:
            rit_on = False
    rit_tx = _store_value(store, _RIT_TX_PATH)
    if rit_tx is None:
        get_rit_tx = getattr(radio, "get_rit_tx_status", None)
        if callable(get_rit_tx):
            try:
                rit_tx = bool(await get_rit_tx())
            except Exception:
                rit_tx = False
        else:
            rit_tx = False

    power = _store_value(store, _POWER_PATH)
    if power is None:
        get_power = getattr(radio, "get_rf_power", None)
        if callable(get_power):
            try:
                power = int(await get_power())
            except Exception:
                power = 0
        else:
            power = 0
    try:
        power = int(power)
    except (TypeError, ValueError):
        power = 0

    try:
        observed = project_observed_ptt(store.snapshot())
    except Exception:
        observed = ObservedPtt.UNKNOWN
    ptt = observed is ObservedPtt.ON or (
        observed is ObservedPtt.UNKNOWN and managed_keyed
    )

    return AdapterState(
        freq_a=freq_a,
        freq_b=freq_b,
        modulation=modulation,
        split=bool(split),
        rit_freq=rit_freq,
        rit_on=bool(rit_on),
        rit_tx=bool(rit_tx),
        power_native=power,
        ptt=ptt,
    )


def tx_frequency(state: AdapterState) -> int:
    """Effective TX frequency: B when split is on, else A, plus XIT (5.5)."""
    base = state.freq_b if state.split else state.freq_a
    if state.rit_tx:
        base += state.rit_freq
    return base


def _bool(value: bool) -> str:
    return "true" if value else "false"


class TciNotifier:
    """Init burst plus delta-to-notification mapping, shared per server."""

    def __init__(self, *, radio: Any, store: StateStore, managed_present: bool) -> None:
        self._radio = radio
        self._store = store
        self._managed_present = managed_present
        self._baseline = store.snapshot()
        self._modulation = "USB"
        self._last_sent: dict[str, str] = {}

    @property
    def managed_present(self) -> bool:
        """Whether the managed TX composition backs this server (spec 7.2)."""
        return self._managed_present

    @property
    def modulation(self) -> str:
        """Last announced TCI modulation (unmapped modes keep it, 5.6)."""
        return self._modulation

    def identity_lines(self) -> list[str]:
        """Init-burst identity block, without side effects (spec 5.4)."""
        model = getattr(self._radio, "model", None)
        device = str(model) if isinstance(model, str) and model else "unknown"
        ranges = _freq_ranges(self._radio)
        lines = [
            f"protocol:{TCI_PROTOCOL_NAME},{TCI_PROTOCOL_VERSION};",
            f"device:{device};",
            f"receive_only:{_bool(not self._managed_present)};",
            f"trx_count:{TCI_TRX_COUNT};",
            f"channels_count:{TCI_CHANNELS_COUNT};",
        ]
        if ranges:
            lo = min(start for start, _end in ranges)
            hi = max(end for _start, end in ranges)
            lines.append(f"vfo_limits:{lo},{hi};")
        names = modulations_list(
            _profile_modes(self._radio), data_capable=_supports_data_mode(self._radio)
        )
        lines.append(f"modulations_list:{','.join(names)};")
        return lines

    def _state_lines(self, state: AdapterState) -> list[str]:
        drive = drive_percent(state.power_native, self._radio)
        tx = tx_frequency(state)
        return [
            f"dds:0,{state.freq_a};",
            "if:0,0,0;",
            f"if:0,1,{state.freq_b - state.freq_a};",
            f"vfo:0,0,{state.freq_a};",
            f"vfo:0,1,{state.freq_b};",
            f"modulation:0,{state.modulation};",
            f"trx:0,{_bool(state.ptt)};",
            "tune:0,false;",
            f"drive:0,{drive};",
            f"tune_drive:0,{drive};",
            f"rit_enable:0,{_bool(state.rit_on)};",
            f"xit_enable:0,{_bool(state.rit_tx)};",
            f"split_enable:0,{_bool(state.split)};",
            f"rit_offset:0,{state.rit_freq};",
            f"xit_offset:0,{state.rit_freq};",
            f"tx_enable:0,{_bool(self._managed_present)};",
            f"tx_frequency:{tx};",
        ]

    async def _current_state(self) -> AdapterState:
        state = await project_state(
            self._radio,
            self._store,
            fallback_modulation=self._modulation,
        )
        self._modulation = state.modulation
        return state

    async def current_lines(self) -> list[str]:
        """Identity plus state lines, without touching the baseline."""
        state = await self._current_state()
        return self.identity_lines() + self._state_lines(state)

    async def init_burst(self) -> list[str]:
        """Render the init burst (spec 5.4) and seed the notification baseline."""
        lines = await self.current_lines() + ["ready;"]
        self._last_sent = {line.split(";")[0]: line for line in lines}
        self._baseline = self._store.snapshot()
        return lines

    async def refresh(self) -> list[str]:
        """Map the store delta since the last baseline to new lines.

        Lines already sent are suppressed: only changed state reaches the
        broadcast. A store that dropped its history re-emits the full state.
        """
        try:
            delta: SnapshotDelta = self._store.delta_since(self._baseline)
        except Exception:
            delta = self._store.delta_since(StateStore.empty())
        self._baseline = self._store.snapshot()
        watched = (
            _FREQ_A_PATH,
            _FREQ_B_PATH,
            _MODE_A_PATH,
            _DATA_A_PATH,
            _MODE_B_PATH,
            _DATA_B_PATH,
            _SPLIT_PATH,
            _RIT_FREQ_PATH,
            _RIT_ON_PATH,
            _RIT_TX_PATH,
            _POWER_PATH,
            OBSERVED_PTT_PATH,
        )
        if not delta.requires_full_snapshot and not any(
            change.path in watched for change in delta.changes
        ):
            return []
        state = await self._current_state()
        fresh = {line.split(";")[0]: line for line in self._state_lines(state)}
        emitted = [
            line for key, line in fresh.items() if self._last_sent.get(key) != line
        ]
        self._last_sent.update(fresh)
        return emitted

    def data_mode_count(self) -> int:
        """Profile DATA sub-mode count for the mode/DATA glue (spec 5.7)."""
        return int(profile_data_mode_count(self._radio))
