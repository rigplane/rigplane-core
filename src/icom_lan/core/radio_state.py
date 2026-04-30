"""RadioState — dual-receiver radio state model.

Holds the complete state for both MAIN and SUB receivers of the IC-7610,
plus global parameters (PTT, split, TX power).  Populated by
:class:`~icom_lan._civ_rx.CivRuntime` from incoming CI-V frames; read
by the HTTP ``GET /api/v1/state`` endpoint.

This is intentionally additive: it runs *alongside* the existing
:class:`~icom_lan.rigctld.state_cache.StateCache` without replacing it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any

from .types import ScopeFixedEdge

__all__ = [
    "ReceiverState",
    "ScopeControlsState",
    "TxBandEdge",
    "RadioState",
    "VfoSlotState",
    "YaesuStateExtension",
]


@dataclass(frozen=True, slots=True)
class VfoSlotState:
    """Immutable per-VFO-slot (A or B) state within a receiver."""

    freq_hz: int = 0
    mode: str = "USB"
    filter_num: int | None = None
    data_mode: int = 0


@dataclass(slots=True)
class TxBandEdge:
    """A TX-permitted frequency range reported by the radio (0x1E)."""

    start_hz: int = 0
    end_hz: int = 0


@dataclass(slots=True)
class ReceiverState:
    """Per-receiver (MAIN or SUB) state.

    Frequency, mode, filter and data_mode are stored per VFO slot
    (``vfo_a`` / ``vfo_b``); the legacy ``freq`` / ``mode`` / ``filter`` /
    ``data_mode`` attributes are derived properties reading and writing
    the slot selected by :attr:`active_slot` (``"A"`` or ``"B"``).
    """

    vfo_a: VfoSlotState = field(default_factory=VfoSlotState)
    vfo_b: VfoSlotState = field(default_factory=VfoSlotState)
    active_slot: str = "A"  # "A" or "B"
    filter_width: int | None = None
    att: int = 0  # dB: 0, 3, 6, …, 45
    preamp: int = 0  # 0=off, 1=P1, 2=P2
    nb: bool = False
    nr: bool = False
    digisel: bool = False
    ipplus: bool = False
    s_meter_sql_open: bool = False
    agc: int = 0
    audio_peak_filter: int = 0
    auto_notch: bool = False
    manual_notch: bool = False
    twin_peak_filter: bool = False
    filter_shape: int = 0
    agc_time_constant: int = 0
    af_level: int = 0  # 0-255
    rf_gain: int = 0  # 0-255
    squelch: int = 0  # 0-255
    s_meter: int = 0  # raw 0-241
    apf_type_level: int = 0  # 0-255
    apf_on: bool = False  # Audio Peak Filter on/off (Yaesu CO02)
    apf_freq: int = 0  # APF frequency (Yaesu CO03)
    nr_level: int = 0  # 0-255
    pbt_inner: int = 128  # 0-255, 128=center
    pbt_outer: int = 128  # 0-255, 128=center
    nb_level: int = 0  # 0-255
    digisel_shift: int = 0  # 0-255
    af_mute: bool = False
    contour: int = 0  # 0=off, >0=on (S-DX / contour DSP)
    if_shift: int = 0  # signed Hz, e.g. -1200..+1200
    narrow: bool = False
    manual_notch_freq: int = 0  # 0-255
    manual_notch_width: int = 0  # 0-255
    repeater_tone: bool = False
    repeater_tsql: bool = False
    tone_freq: int = 0  # centihz, e.g. 8850 = 88.50 Hz
    tsql_freq: int = 0  # centihz, e.g. 8850 = 88.50 Hz

    # --- VFO-slot-derived properties (legacy compat) ---------------------

    @property
    def _active(self) -> VfoSlotState:
        return self.vfo_b if self.active_slot == "B" else self.vfo_a

    def _replace_active(self, **kw: Any) -> None:
        new_slot = replace(self._active, **kw)
        if self.active_slot == "B":
            self.vfo_b = new_slot
        else:
            self.vfo_a = new_slot

    @property
    def freq(self) -> int:
        return self._active.freq_hz

    @freq.setter
    def freq(self, value: int) -> None:
        self._replace_active(freq_hz=value)

    @property
    def mode(self) -> str:
        return self._active.mode

    @mode.setter
    def mode(self, value: str) -> None:
        self._replace_active(mode=value)

    @property
    def filter(self) -> int | None:
        return self._active.filter_num

    @filter.setter
    def filter(self, value: int | None) -> None:
        self._replace_active(filter_num=value)

    @property
    def data_mode(self) -> int:
        return self._active.data_mode

    @data_mode.setter
    def data_mode(self, value: int) -> None:
        self._replace_active(data_mode=value)


# Wrap ReceiverState.__init__ to accept legacy kwargs (freq/mode/filter/
# data_mode) and route them into vfo_a.  The dataclass-generated __init__
# doesn't know about the derived properties, so we intercept the kwargs
# before delegating.
_ReceiverState_orig_init = ReceiverState.__init__


def _receiver_state_init(
    self: ReceiverState,
    *args: Any,
    freq: int | None = None,
    mode: str | None = None,
    filter: int | None = None,  # noqa: A002  (match legacy kwarg name)
    data_mode: int | None = None,
    **kwargs: Any,
) -> None:
    _ReceiverState_orig_init(self, *args, **kwargs)
    legacy: dict[str, Any] = {}
    if freq is not None:
        legacy["freq_hz"] = freq
    if mode is not None:
        legacy["mode"] = mode
    if filter is not None:
        legacy["filter_num"] = filter
    if data_mode is not None:
        legacy["data_mode"] = data_mode
    if legacy:
        # Legacy kwargs always populate vfo_a (the default slot).
        self.vfo_a = replace(self.vfo_a, **legacy)


ReceiverState.__init__ = _receiver_state_init  # type: ignore[method-assign]


def _receiver_to_dict(rx: ReceiverState) -> dict[str, Any]:
    """Serialise a ReceiverState to a dict that keeps both the slot-shaped
    (``vfo_a``/``vfo_b``/``active_slot``) view and the legacy top-level
    ``freq``/``mode``/``filter``/``data_mode`` keys for backward-compat
    with existing JSON consumers.
    """
    d = asdict(rx)
    d["vfo_a"] = asdict(rx.vfo_a)
    d["vfo_b"] = asdict(rx.vfo_b)
    d["active_slot"] = rx.active_slot
    d["freq"] = rx.freq
    d["mode"] = rx.mode
    d["filter"] = rx.filter
    d["data_mode"] = rx.data_mode
    return d


@dataclass(slots=True)
class ScopeControlsState:
    """Readable IC-7610 scope-control state."""

    receiver: int = 0
    dual: bool = False
    mode: int = 0
    span: int = 0
    edge: int = 0
    hold: bool = False
    ref_db: float = 0.0
    speed: int = 0
    during_tx: bool = False
    center_type: int = 0
    vbw_narrow: bool = False
    rbw: int = 0
    fixed_edge: ScopeFixedEdge = field(
        default_factory=lambda: ScopeFixedEdge(
            range_index=0,
            edge=0,
            start_hz=0,
            end_hz=0,
        )
    )


@dataclass(slots=True)
class YaesuStateExtension:
    """Yaesu-CAT-specific state that has no clean Icom analog.

    Populated by ``YaesuCatPoller``; left as ``None`` on
    :class:`RadioState` for non-Yaesu backends. Keeping these fields in a
    dedicated namespace keeps the generic :class:`RadioState` semantically
    backend-neutral while still making Yaesu-only flags observable to UI
    consumers (e.g. a Yaesu-specific panel).

    Fields:
        rx_func_mode: ``FR`` command — 0=dual RX off, 1=single RX.
        tx_func_mode: ``FT`` command — 0=MAIN TX, 1=SUB TX.

    ``None`` for any field means "not yet polled / unknown".
    """

    rx_func_mode: int | None = None
    tx_func_mode: int | None = None


@dataclass(slots=True)
class RadioState:
    """Full radio state: two receivers + global parameters."""

    main: ReceiverState = field(default_factory=ReceiverState)
    sub: ReceiverState = field(default_factory=ReceiverState)
    active: str = "MAIN"  # "MAIN" | "SUB"
    power_on: bool = True  # Radio power status (on/off)
    ptt: bool = False
    power_level: int = 0  # TX power 0-255
    split: bool = False
    dual_watch: bool = False
    scanning: bool = False
    scan_type: int = (
        0  # 0=none, 0x01=prog, 0x02=P2, 0x03=ΔF, 0x12=fine, 0x22=mem, 0x23=sel
    )
    scan_resume_mode: int = 0  # 0=OFF, 1=5s, 2=10s, 3=15s (low nibble of 0xD0-0xD3)
    tuning_step: int = 0
    overflow: bool = False
    tuner_status: int = 0  # 0=off, 1=on, 2=tuning
    tx_freq_monitor: bool = False
    rit_freq: int = 0  # signed Hz (±9999)
    rit_on: bool = False
    rit_tx: bool = False
    comp_meter: int = 0  # raw 0-255
    vd_meter: int = 0  # raw 0-255 (supply voltage)
    id_meter: int = 0  # raw 0-255 (drain current)
    power_meter: int = 0  # raw 0-255 (CI-V 0x15/0x11)
    swr_meter: int = 0  # raw 0-255 (CI-V 0x15/0x12)
    alc_meter: int = 0  # raw 0-255 (CI-V 0x15/0x13)
    cw_pitch: int = 0  # Hz
    mic_gain: int = 0  # 0-255
    key_speed: int = 0  # WPM
    notch_filter: int = 0  # 0-255
    main_sub_tracking: bool = False
    compressor_on: bool = False
    compressor_level: int = 0  # 0-255
    monitor_on: bool = False
    break_in_delay: int = 0  # 0-255
    # cw_spot tri-state:
    #   None = not populated by this backend (Icom backends leave it unset)
    #   bool = explicit value reported by the radio (Yaesu backends populate it)
    cw_spot: bool | None = None
    break_in: int = 0  # 0=off, 1=semi, 2=full
    dial_lock: bool = False
    drive_gain: int = 0  # 0-255
    monitor_gain: int = 0  # 0-255
    vfo_select: int = 0  # 0=VFO-A/MAIN, 1=VFO-B/SUB
    # Yaesu-specific extension; None on non-Yaesu backends, populated on Yaesu.
    yaesu: YaesuStateExtension | None = None
    vox_on: bool = False
    vox_gain: int = 0  # 0-255
    anti_vox_gain: int = 0  # 0-255
    vox_delay: int = 0  # 0-20 (0.0-2.0 sec in 0.1s steps)
    ssb_tx_bandwidth: int = 0  # 0=wide, 1=mid, 2=nar
    ref_adjust: int = 0  # 0-511
    dash_ratio: int = 0  # 28-45
    nb_depth: int = 0  # 0-9
    nb_width: int = 0  # 0-255
    tx_antenna: int = 1  # 1 or 2
    rx_antenna_1: bool = False
    rx_antenna_2: bool = False
    tx_band_edges: list[TxBandEdge] = field(default_factory=list)
    scope_controls: ScopeControlsState = field(default_factory=ScopeControlsState)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable dict of the current radio state."""
        return {
            "active": self.active,
            "power_on": self.power_on,
            "ptt": self.ptt,
            "power_level": self.power_level,
            "split": self.split,
            "dual_watch": self.dual_watch,
            "scanning": self.scanning,
            "scan_type": self.scan_type,
            "scan_resume_mode": self.scan_resume_mode,
            "tuning_step": self.tuning_step,
            "overflow": self.overflow,
            "tuner_status": self.tuner_status,
            "tx_freq_monitor": self.tx_freq_monitor,
            "rit_freq": self.rit_freq,
            "rit_on": self.rit_on,
            "rit_tx": self.rit_tx,
            "comp_meter": self.comp_meter,
            "vd_meter": self.vd_meter,
            "id_meter": self.id_meter,
            "power_meter": self.power_meter,
            "swr_meter": self.swr_meter,
            "alc_meter": self.alc_meter,
            "cw_pitch": self.cw_pitch,
            "mic_gain": self.mic_gain,
            "key_speed": self.key_speed,
            "notch_filter": self.notch_filter,
            "main_sub_tracking": self.main_sub_tracking,
            "compressor_on": self.compressor_on,
            "compressor_level": self.compressor_level,
            "monitor_on": self.monitor_on,
            "break_in_delay": self.break_in_delay,
            "cw_spot": self.cw_spot,
            "break_in": self.break_in,
            "dial_lock": self.dial_lock,
            "drive_gain": self.drive_gain,
            "monitor_gain": self.monitor_gain,
            "vfo_select": self.vfo_select,
            "yaesu": (
                {
                    "rx_func_mode": self.yaesu.rx_func_mode,
                    "tx_func_mode": self.yaesu.tx_func_mode,
                }
                if self.yaesu is not None
                else None
            ),
            "vox_on": self.vox_on,
            "vox_gain": self.vox_gain,
            "anti_vox_gain": self.anti_vox_gain,
            "vox_delay": self.vox_delay,
            "ssb_tx_bandwidth": self.ssb_tx_bandwidth,
            "ref_adjust": self.ref_adjust,
            "dash_ratio": self.dash_ratio,
            "nb_depth": self.nb_depth,
            "nb_width": self.nb_width,
            "tx_antenna": self.tx_antenna,
            "rx_antenna_1": self.rx_antenna_1,
            "rx_antenna_2": self.rx_antenna_2,
            "tx_band_edges": [
                {"start_hz": e.start_hz, "end_hz": e.end_hz} for e in self.tx_band_edges
            ],
            "scope_controls": asdict(self.scope_controls),
            "main": _receiver_to_dict(self.main),
            "sub": _receiver_to_dict(self.sub),
        }

    @staticmethod
    def _receiver_from_dict(d: dict[str, Any]) -> ReceiverState:
        """Construct a :class:`ReceiverState` from a ``to_dict()`` payload.

        Accepts both the slot-shaped view (``vfo_a``/``vfo_b``/``active_slot``)
        and the legacy top-level ``freq``/``mode``/``filter``/``data_mode``
        fallback.
        """
        slot_keys = {"freq_hz", "mode", "filter_num", "data_mode"}
        plain: dict[str, Any] = {
            k: v
            for k, v in d.items()
            if k
            not in {
                "vfo_a",
                "vfo_b",
                "active_slot",
                "freq",
                "mode",
                "filter",
                "data_mode",
            }
        }
        rx = ReceiverState(**plain)
        if "vfo_a" in d and isinstance(d["vfo_a"], dict):
            rx.vfo_a = VfoSlotState(
                **{k: v for k, v in d["vfo_a"].items() if k in slot_keys}
            )
        if "vfo_b" in d and isinstance(d["vfo_b"], dict):
            rx.vfo_b = VfoSlotState(
                **{k: v for k, v in d["vfo_b"].items() if k in slot_keys}
            )
        if "active_slot" in d:
            rx.active_slot = str(d["active_slot"])
        if "vfo_a" not in d:
            # Legacy fallback: top-level freq/mode/filter/data_mode → vfo_a.
            rx.vfo_a = VfoSlotState(
                freq_hz=int(d.get("freq", 0)),
                mode=str(d.get("mode", "USB")),
                filter_num=d.get("filter"),
                data_mode=int(d.get("data_mode", 0)),
            )
        return rx

    def receiver(self, which: str) -> ReceiverState:
        """Return the :class:`ReceiverState` for *which* (``"MAIN"`` or ``"SUB"``)."""
        return self.main if which == "MAIN" else self.sub
