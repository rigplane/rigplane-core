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
    TCI_TO_CORE_MODE,
    TCI_TRX_COUNT,
    modulations_list,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AdapterState",
    "READ_PATHS",
    "TciNotifier",
    "announced_modulations",
    "authority_keyed",
    "drive_percent",
    "project_state",
    "store_power_to_percent",
    "supports_data_mode",
    "tx_role_raw",
    "tx_role_tci",
]

#: Candidate store paths the burst and notifications project, in lookup
#: order. Production publishes two vocabularies: the Yaesu acquisition
#: path uses ``active("main"/"sub")``, the Icom CI-V path uses numeric
#: receiver ids (``active("0"/"1")``), and the single-receiver /
#: ``main_unselected`` topologies publish the unselected TX tuple under
#: ``unselected("0", ...)`` (``runtime/_civ_rx.py``). The projection tries
#: each candidate in order (mirroring the rigctld seat's alias handling),
#: so one layer serves every backend with no model branches.
_FREQ_A_PATHS: tuple[FieldPath, ...] = (
    FieldPath.active("main", "freq_mode", "freq_hz"),
    FieldPath.active("0", "freq_mode", "freq_hz"),
)
_FREQ_B_PATHS: tuple[FieldPath, ...] = (
    FieldPath.unselected("0", "freq_mode", "freq_hz"),
    FieldPath.active("1", "freq_mode", "freq_hz"),
    FieldPath.active("sub", "freq_mode", "freq_hz"),
)
_MODE_A_PATHS: tuple[FieldPath, ...] = (
    FieldPath.active("main", "freq_mode", "mode"),
    FieldPath.active("0", "freq_mode", "mode"),
)
_DATA_A_PATHS: tuple[FieldPath, ...] = (
    FieldPath.active("main", "freq_mode", "data_mode"),
    FieldPath.active("0", "freq_mode", "data_mode"),
)
_MODE_B_PATHS: tuple[FieldPath, ...] = (
    FieldPath.unselected("0", "freq_mode", "mode"),
    FieldPath.active("1", "freq_mode", "mode"),
    FieldPath.active("sub", "freq_mode", "mode"),
)
_DATA_B_PATHS: tuple[FieldPath, ...] = (
    FieldPath.unselected("0", "freq_mode", "data_mode"),
    FieldPath.active("1", "freq_mode", "data_mode"),
    FieldPath.active("sub", "freq_mode", "data_mode"),
)
_SPLIT_PATH = FieldPath.global_("tx_state", "split")
_RIT_FREQ_PATH = FieldPath.global_("operator_controls", "rit_freq")
_RIT_ON_PATH = FieldPath.global_("tx_state", "rit_on")
_RIT_TX_PATH = FieldPath.global_("tx_state", "rit_tx")
_POWER_PATH = FieldPath.global_("operator_controls", "power_level")

#: Every store path a TCI read can project: the adapter handler asks
#: acquisition to refresh exactly these (instead of an empty request,
#: which the scheduler rejects), because :func:`project_state` reads
#: exactly these on every read.
READ_PATHS: tuple[FieldPath, ...] = (
    *_FREQ_A_PATHS,
    *_FREQ_B_PATHS,
    *_MODE_A_PATHS,
    *_DATA_A_PATHS,
    *_MODE_B_PATHS,
    *_DATA_B_PATHS,
    _SPLIT_PATH,
    _RIT_FREQ_PATH,
    _RIT_ON_PATH,
    _RIT_TX_PATH,
    _POWER_PATH,
    OBSERVED_PTT_PATH,
)


@dataclass(frozen=True)
class AdapterState:
    """Everything one burst or notification round renders."""

    freq_a: int
    freq_b: int
    modulation: str
    data_a: int
    modulation_b: str
    data_b: int
    split: bool
    rit_freq: int
    rit_on: bool
    rit_tx: bool
    drive: int
    ptt: bool


def _store_value(store: StateStore, path: FieldPath) -> Any:
    """The store value, or a miss marker that is never a real reading."""
    try:
        return store.snapshot().field(path).value
    except KeyError:
        return None


def _store_first(store: StateStore, paths: tuple[FieldPath, ...]) -> Any:
    """The first published candidate value, or None when all miss."""
    snapshot = store.snapshot()
    for path in paths:
        try:
            return snapshot.field(path).value
        except KeyError:
            continue
    return None


#: Native DATA-partner names published by backends that encode DATA in the
#: mode (Yaesu CAT ``DATA-U``/``DATA-L``/``DATA-FM``): (Core mode, data on).
_NATIVE_DATA_PARTNERS: dict[str, tuple[str, bool]] = {
    "DATA-U": ("USB", True),
    "DATA-L": ("LSB", True),
    "DATA-FM": ("FM", True),
    "DATA-FM-N": ("FM", True),
    "DATA": ("USB", True),
}

#: Native narrow/reverse aliases published as profile or observed mode
#: names (Yaesu ``CW-U``/``RTTY-L``/..., Icom ``CW_R``): Core mode name.
_NATIVE_BASE_ALIASES: dict[str, str] = {
    "CW-U": "CW",
    "CW-L": "CW_R",
    "RTTY-L": "RTTY",
    "RTTY-U": "RTTY_R",
    "FM-N": "FM",
    "AM-N": "AM",
    "CW_R": "CW_R",
}


def _canonical_native_mode(name: str) -> tuple[str, bool]:
    """Map a native/profile mode name to (Core mode name, data flag).

    No model branches: the table covers the vocabularies the backends and
    profiles publish (Icom Core names, Yaesu ``DATA-*`` partners and
    ``CW-U``-style aliases). A trailing ``-D`` or an embedded ``DATA``
    marks DATA for the matching sideband; anything else keeps its name
    with DATA off, and the caller falls back when it has no TCI name.
    """
    upper = name.strip().upper()
    if upper in _NATIVE_DATA_PARTNERS:
        return _NATIVE_DATA_PARTNERS[upper]
    if upper in _NATIVE_BASE_ALIASES:
        return (_NATIVE_BASE_ALIASES[upper], False)
    data = upper.endswith("-D") or "DATA" in upper
    base = upper.replace("DATA", "").strip("- _")
    if data and base in ("U", "USB"):
        return ("USB", True)
    if data and base in ("L", "LSB"):
        return ("LSB", True)
    if data and base in ("", "FM"):
        return ("FM", True)
    if base:
        return (base, bool(data))
    return (upper, bool(data))


def _mode_name(value: Any) -> str:
    """Normalize a store/radio mode to an uppercase name (never empty)."""
    name = getattr(value, "name", None)
    if isinstance(name, str) and name:
        return name.upper()
    if isinstance(value, str) and value:
        return value.upper()
    return "USB"


def supports_data_mode(radio: Any) -> bool:
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


def store_power_to_percent(power_value: Any) -> int:
    """Map a store ``power_level`` (normalized 0..1) to ``drive`` percent.

    The store contract publishes ``power_level`` with ``unit="normalized"``
    (``core/state_pipeline_contracts.py``); native scaling applies only to
    direct radio readbacks (see :func:`drive_percent`).
    """
    try:
        normalized = float(power_value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, round(normalized * 100)))


def _profile_modes(radio: Any) -> tuple[str, ...]:
    profile = getattr(radio, "profile", None)
    modes = getattr(profile, "modes", None)
    if not isinstance(modes, (tuple, list)) or not modes:
        return ("AM", "LSB", "USB", "CW", "FM")
    names = []
    for mode in modes:
        base, _data = _canonical_native_mode(str(mode))
        names.append(base.upper())
    return tuple(names)


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


def _project_data(store: StateStore, *, channel_b: bool = False) -> int:
    """Numeric DATA sub-mode of a channel (0 when unpublished)."""
    data_paths = _DATA_B_PATHS if channel_b else _DATA_A_PATHS
    data_value = _store_first(store, data_paths)
    if data_value is None:
        return 0
    try:
        return max(0, int(data_value))
    except (TypeError, ValueError):
        return 0


def tx_role_raw(store: StateStore, *, split: bool) -> tuple[str | None, int | None]:
    """Raw TX-role tuple: native mode name and numeric DATA.

    Channel B while split is on, else channel A, through the same
    capability-correct candidate paths the display projection uses. Either
    element is None when no candidate path published it (missing evidence);
    the caller refuses key-down on missing evidence rather than projecting
    a display default.
    """
    mode_paths = _MODE_B_PATHS if split else _MODE_A_PATHS
    data_paths = _DATA_B_PATHS if split else _DATA_A_PATHS
    raw_mode = _store_first(store, mode_paths)
    raw_data = _store_first(store, data_paths)
    mode_name: str | None = None
    if raw_mode is not None:
        try:
            mode_name = _mode_name(raw_mode)
        except Exception:
            mode_name = None
    data_num: int | None = None
    if raw_data is not None:
        try:
            data_num = max(0, int(raw_data))
        except (TypeError, ValueError):
            data_num = None
    return (mode_name, data_num)


def tx_role_tci(store: StateStore, *, split: bool) -> tuple[str | None, int | None]:
    """Strict TX-role tuple: TCI name and numeric DATA.

    Same evidence as :func:`tx_role_raw`, canonicalized to the TCI
    vocabulary. Either element is None when the evidence is missing or the
    raw mode has no TCI name (e.g. RTTY): the admission check refuses
    key-down on None rather than projecting a display default.
    """
    raw_mode, raw_data = tx_role_raw(store, split=split)
    if raw_mode is None or raw_data is None:
        return (None, None)
    base, name_data = _canonical_native_mode(raw_mode)
    data = max(0, int(raw_data))
    data_on = bool(data) or name_data
    if data_on and base == "LSB":
        return ("DIGL", data)
    if data_on and base == "USB":
        return ("DIGU", data)
    if not data_on and base in TCI_TO_CORE_MODE:
        return (base, data)
    return (None, None)


def authority_keyed(authority: Any) -> bool:
    """Whether the managed authority currently holds TX (any non-RX kind).

    The projection reads the intent KIND, not the intent's truthiness: the
    real idle composition carries an RX intent object, which is truthy.
    Doubles without a real intent kind keep the legacy truthiness read.
    """
    if authority is None:
        return False
    try:
        snapshot = authority.snapshot_nowait()
    except Exception:
        return False
    intent = getattr(getattr(snapshot, "state", None), "intent", None)
    kind = getattr(intent, "kind", None)
    if kind is None:
        return bool(intent)
    try:
        from ..runtime.managed_tx_state import ManagedTxIntentKind

        return kind is not ManagedTxIntentKind.RX
    except Exception:
        return bool(intent)


def announced_modulations(radio: Any) -> tuple[str, ...]:
    """The TCI names this radio supports (profile modes plus DATA surface)."""
    return modulations_list(
        _profile_modes(radio), data_capable=supports_data_mode(radio)
    )


def _project_modulation(
    store: StateStore, fallback: str, *, channel_b: bool = False
) -> str:
    """Project a channel's TCI modulation name (spec 5.6).

    The native observed name is canonicalized first (Yaesu ``DATA-U`` and
    ``CW-U``-style aliases map without model branches), and the numeric
    DATA sub-mode ORs with DATA encoded in the name: a successful DIGU
    write reads back as DIGU even when the name already says so.
    """
    mode_paths = _MODE_B_PATHS if channel_b else _MODE_A_PATHS
    mode_value = _store_first(store, mode_paths)
    if mode_value is None:
        return fallback
    stored_data = _project_data(store, channel_b=channel_b)
    base, name_data = _canonical_native_mode(_mode_name(mode_value))
    data_on = bool(stored_data) or name_data
    if data_on and base == "LSB":
        return "DIGL"
    if data_on and base == "USB":
        return "DIGU"
    if not data_on and base in TCI_TO_CORE_MODE:
        return base
    # A Core mode with no TCI name, or DATA on a non-sideband mode
    # (spec 5.6): keep the last announced value.
    logger.debug("tci: unmapped mode %s data=%s; keeping %s", base, data_on, fallback)
    return fallback


async def project_state(
    radio: Any,
    store: StateStore,
    *,
    managed_keyed: bool = False,
    fallback_modulation: str = "USB",
) -> AdapterState:
    """Project the notifier state: store first, radio second, default last."""
    freq_a = _store_first(store, _FREQ_A_PATHS)
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

    freq_b = _store_first(store, _FREQ_B_PATHS)
    relative_b = None
    if freq_b is None:
        read_relative = getattr(radio, "read_relative_vfo", None)
        if callable(read_relative):
            try:
                relative_b = await read_relative(selected=False)
                freq_b = int(relative_b.freq_hz)
            except Exception:
                relative_b = None
                freq_b = freq_a
        else:
            freq_b = freq_a
    try:
        freq_b = int(freq_b)
    except (TypeError, ValueError):
        freq_b = freq_a

    modulation = _project_modulation(store, fallback_modulation)
    data_a = _project_data(store)
    if relative_b is not None and _store_first(store, _MODE_B_PATHS) is None:
        # No published B mode: complete the tuple from the relative-VFO
        # readback instead of defaulting to A's modulation with DATA zero
        # (single-receiver profiles would otherwise refuse split DIGU keys
        # while accepting plain USB after a B change).
        try:
            base, name_data = _canonical_native_mode(_mode_name(relative_b.mode))
            stored_b = _store_first(store, _DATA_B_PATHS)
            data_b = stored_b if stored_b is not None else relative_b.data_mode
            try:
                data_b = max(0, int(data_b))
            except (TypeError, ValueError):
                data_b = 0
            if (bool(data_b) or name_data) and base == "LSB":
                modulation_b = "DIGL"
            elif (bool(data_b) or name_data) and base == "USB":
                modulation_b = "DIGU"
            else:
                modulation_b = base if base in TCI_TO_CORE_MODE else modulation
        except Exception:
            modulation_b = _project_modulation(store, modulation, channel_b=True)
            data_b = _project_data(store, channel_b=True)
    else:
        modulation_b = _project_modulation(store, modulation, channel_b=True)
        data_b = _project_data(store, channel_b=True)

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

    power = _store_first(store, (_POWER_PATH,))
    if power is None:
        get_power = getattr(radio, "get_rf_power", None)
        if callable(get_power):
            try:
                drive = drive_percent(int(await get_power()), radio)
            except Exception:
                drive = 0
        else:
            drive = 0
    else:
        # The store publishes normalized 0..1; native scaling is only for
        # the direct radio readback above.
        drive = store_power_to_percent(power)

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
        data_a=data_a,
        modulation_b=modulation_b,
        data_b=data_b,
        split=bool(split),
        rit_freq=rit_freq,
        rit_on=bool(rit_on),
        rit_tx=bool(rit_tx),
        drive=int(drive),
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


def _cache_key(line: str) -> str:
    """Stable notification key: command plus selectors, never the value.

    The key drops the last comma segment (the value) so a return to a
    previous value is a key hit with a different line and is re-emitted.
    ``tx_frequency`` carries no selector commas and keys on the whole head.
    """
    head = line.split(";")[0]
    if "," in head:
        return head.rsplit(",", 1)[0]
    return head


#: State lines that always travel together (spec 5.5: all three re-sent).
_DDS_IF_KEYS = ("dds:0", "if:0,0", "if:0,1")


class TciNotifier:
    """Init burst plus delta-to-notification mapping, shared per server."""

    def __init__(
        self,
        *,
        radio: Any,
        store: StateStore,
        managed_present: bool,
        managed_tx_authority: Any = None,
    ) -> None:
        self._radio = radio
        self._store = store
        self._managed_present = managed_present
        self._authority = managed_tx_authority
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

    def note_modulation(self, modulation: str) -> None:
        """Retain the last announced modulation through the shared path."""
        if modulation:
            self._modulation = modulation

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
        names = announced_modulations(self._radio)
        lines.append(f"modulations_list:{','.join(names)};")
        return lines

    def _state_lines(self, state: AdapterState) -> list[str]:
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
            f"drive:0,{state.drive};",
            f"tune_drive:0,{state.drive};",
            f"rit_enable:0,{_bool(state.rit_on)};",
            f"xit_enable:0,{_bool(state.rit_tx)};",
            f"split_enable:0,{_bool(state.split)};",
            f"rit_offset:0,{state.rit_freq};",
            f"xit_offset:0,{state.rit_freq};",
            f"tx_enable:0,{_bool(self._managed_present)};",
            f"tx_frequency:{tx};",
        ]

    async def _current_state(
        self, *, managed_keyed: bool | None = None
    ) -> AdapterState:
        if managed_keyed is None:
            managed_keyed = authority_keyed(self._authority)
        state = await project_state(
            self._radio,
            self._store,
            managed_keyed=managed_keyed,
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
        self._last_sent = {_cache_key(line): line for line in lines}
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
        watched = set(READ_PATHS)
        if not delta.requires_full_snapshot and not any(
            change.path in watched for change in delta.changes
        ):
            return []
        state = await self._current_state()
        fresh = {_cache_key(line): line for line in self._state_lines(state)}
        changed = {
            key for key, line in fresh.items() if self._last_sent.get(key) != line
        }
        if changed & set(_DDS_IF_KEYS):
            # The DDS/IF triple always travels together (spec 5.5).
            changed |= set(_DDS_IF_KEYS)
        emitted = [fresh[key] for key in fresh if key in changed]
        self._last_sent.update(fresh)
        return emitted

    def data_mode_count(self) -> int:
        """Profile DATA sub-mode count for the mode/DATA glue (spec 5.7)."""
        return int(profile_data_mode_count(self._radio))
