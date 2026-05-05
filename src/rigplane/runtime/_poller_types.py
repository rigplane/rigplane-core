"""Shared command types and CommandQueue for radio pollers.

These types are used by both the web layer (``web.radio_poller``) and
backend pollers (e.g. ``backends.yaesu_cat.poller``).  Keeping them in
a neutral module avoids backend → web import cycles.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal

__all__ = [
    "Command",
    "CommandQueue",
    # -- command dataclasses (alphabetical) --
    "DisableScope",
    "EnableScope",
    "MemoryClear",
    "MemoryToVfo",
    "MemoryWrite",
    "PttOff",
    "PttOn",
    "QuickDualWatch",
    "QuickDwTrigger",
    "QuickSplit",
    "QuickSplitTrigger",
    "ScanSetDfSpan",
    "ScanSetResume",
    "ScanStart",
    "ScanStop",
    "SelectVfo",
    "SetAcc1ModLevel",
    "SetAfLevel",
    "SetAfMute",
    "SetAgc",
    "SetAgcTimeConstant",
    "SetAntenna1",
    "SetAntenna2",
    "SetApf",
    "SetAttenuator",
    "SetAudioPeakFilter",
    "SetAutoNotch",
    "SetBand",
    "SetBreakIn",
    "SetBreakInDelay",
    "SetBsr",
    "SetCivOutputAnt",
    "SetCivTransceive",
    "SetCompressor",
    "SetCompressorLevel",
    "SetCwPitch",
    "SetDashRatio",
    "SetData1ModInput",
    "SetData2ModInput",
    "SetData3ModInput",
    "SetDataMode",
    "SetDataOffModInput",
    "SetDialLock",
    "SetDigiSel",
    "SetDigiselShift",
    "SetDriveGain",
    "SetDualWatch",
    "SetFilter",
    "SetFilterShape",
    "SetFilterWidth",
    "SetFreq",
    "SetIfShift",
    "SetIpPlus",
    "SetKeySpeed",
    "SetLanModLevel",
    "SetMainSubTracking",
    "SetManualNotch",
    "SetManualNotchWidth",
    "SetMemoryContents",
    "SetMemoryMode",
    "SetMicGain",
    "SetMode",
    "SetMonitor",
    "SetMonitorGain",
    "SetNB",
    "SetNBLevel",
    "SetNR",
    "SetNRLevel",
    "SetNbDepth",
    "SetNbWidth",
    "SetNotchFilter",
    "SetPbtInner",
    "SetPbtOuter",
    "SetPower",
    "SetPowerstat",
    "SetPreamp",
    "SetRefAdjust",
    "SetRepeaterTone",
    "SetRepeaterTsql",
    "SetRfGain",
    "SetRitFrequency",
    "SetRitStatus",
    "SetRitTxStatus",
    "SetRxAntenna",
    "SetRxAntennaAnt1",
    "SetRxAntennaAnt2",
    "SetScopeCenterType",
    "SetScopeDual",
    "SetScopeDuringTx",
    "SetScopeEdge",
    "SetScopeFixedEdge",
    "SetScopeHold",
    "SetScopeMode",
    "SetScopeRbw",
    "SetScopeRef",
    "SetScopeSpeed",
    "SetScopeSpan",
    "SetScopeVbw",
    "SetSplit",
    "SetSquelch",
    "SetSsbTxBandwidth",
    "SetSystemDate",
    "SetSystemTime",
    "SetToneFreq",
    "SetTsqlFreq",
    "SetTunerStatus",
    "SetTuningStep",
    "SetTwinPeak",
    "SetTxFreqMonitor",
    "SetUsbModLevel",
    "SetUtcOffset",
    "SetVox",
    "SetVoxDelay",
    "SetVoxGain",
    "SetAntiVoxGain",
    "SetXfcStatus",
    "SwitchScopeReceiver",
    "VfoEqualize",
    "VfoSwap",
]


# ------------------------------------------------------------------
# Command types
# ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SetFreq:
    freq: int
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetMode:
    mode: str
    filter_width: int | None = None
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetFilter:
    filter_num: int
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetFilterWidth:
    width: int
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetFilterShape:
    shape: int
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetPower:
    """Set TX RF power.

    The ``unit`` tag disambiguates the level scale between backends:

    - ``"raw_255"`` (default) — Icom CI-V scale, integer 0-255.
    - ``"watts"`` — Yaesu CAT scale, integer watts (PC command, 0-999).

    Each backend's poller verifies the unit matches its expected scale and
    rejects mismatches with a clear ``ValueError``. The default keeps existing
    Icom call sites unchanged (no migration churn).
    """

    level: int
    unit: Literal["raw_255", "watts"] = "raw_255"


@dataclass(frozen=True, slots=True)
class SetRfGain:
    level: int
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetAfLevel:
    level: int
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetSquelch:
    level: int
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetNB:
    on: bool
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetNR:
    on: bool
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetDigiSel:
    on: bool
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetIpPlus:
    on: bool
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetAttenuator:
    db: int
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetPreamp:
    level: int
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetPbtInner:
    level: int
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetPbtOuter:
    level: int
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetIfShift:
    offset: int  # signed Hz
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetNRLevel:
    level: int
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetNBLevel:
    level: int
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetAutoNotch:
    on: bool
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetManualNotch:
    on: bool
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetNotchFilter:
    level: int


@dataclass(frozen=True, slots=True)
class SetAgcTimeConstant:
    value: int
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetCwPitch:
    value: int


@dataclass(frozen=True, slots=True)
class SetKeySpeed:
    speed: int


@dataclass(frozen=True, slots=True)
class SetBreakIn:
    mode: int


@dataclass(frozen=True, slots=True)
class SetApf:
    mode: int
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetTwinPeak:
    on: bool
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetDriveGain:
    level: int


@dataclass(frozen=True, slots=True)
class ScanStart:
    scan_type: int = (
        0x01  # 0x01=programmed, 0x02=P2, 0x03=ΔF, 0x12=fine, 0x22=mem, 0x23=sel_mem
    )


@dataclass(frozen=True, slots=True)
class ScanStop:
    pass


@dataclass(frozen=True, slots=True)
class ScanSetDfSpan:
    span: int  # 0xA1=±5k, 0xA2=±10k, 0xA3=±20k, 0xA4=±50k, 0xA5=±100k, 0xA6=±500k, 0xA7=±1M


@dataclass(frozen=True, slots=True)
class ScanSetResume:
    mode: int  # 0xD0=OFF, 0xD1=5s, 0xD2=10s, 0xD3=15s


@dataclass(frozen=True, slots=True)
class SetDataMode:
    mode: int
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetMicGain:
    level: int


@dataclass(frozen=True, slots=True)
class SetVox:
    on: bool


@dataclass(frozen=True, slots=True)
class SetTunerStatus:
    """0=OFF, 1=ON, 2=tune."""

    value: int


@dataclass(frozen=True, slots=True)
class SetCompressorLevel:
    level: int


@dataclass(frozen=True, slots=True)
class SetMonitor:
    on: bool


@dataclass(frozen=True, slots=True)
class SetMonitorGain:
    level: int


@dataclass(frozen=True, slots=True)
class SetDialLock:
    on: bool


@dataclass(frozen=True, slots=True)
class SetAgc:
    mode: int  # 1=FAST, 2=MID, 3=SLOW
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetRitStatus:
    on: bool


@dataclass(frozen=True, slots=True)
class SetRitTxStatus:
    on: bool


@dataclass(frozen=True, slots=True)
class SetRitFrequency:
    freq: int


@dataclass(frozen=True, slots=True)
class SetSplit:
    on: bool


@dataclass(frozen=True, slots=True)
class PttOn:
    pass


@dataclass(frozen=True, slots=True)
class PttOff:
    pass


@dataclass(frozen=True, slots=True)
class SetBand:
    band: int  # CI-V band code: 0x00=160m, 0x01=80m, ... 0x09=6m


@dataclass(frozen=True, slots=True)
class SelectVfo:
    vfo: str


@dataclass(frozen=True, slots=True)
class VfoSwap:
    pass


@dataclass(frozen=True, slots=True)
class VfoEqualize:
    pass


@dataclass(frozen=True, slots=True)
class EnableScope:
    policy: str = "fast"


@dataclass(frozen=True, slots=True)
class DisableScope:
    pass


@dataclass(frozen=True, slots=True)
class SwitchScopeReceiver:
    receiver: int  # 0=MAIN, 1=SUB


@dataclass(frozen=True, slots=True)
class SetScopeDuringTx:
    on: bool


@dataclass(frozen=True, slots=True)
class SetScopeCenterType:
    center_type: int  # 0-2


@dataclass(frozen=True, slots=True)
class SetScopeFixedEdge:
    edge: int
    start_hz: int
    end_hz: int


@dataclass(frozen=True, slots=True)
class SetScopeDual:
    dual: bool


@dataclass(frozen=True, slots=True)
class SetScopeMode:
    mode: int


@dataclass(frozen=True, slots=True)
class SetScopeSpan:
    span: int


@dataclass(frozen=True, slots=True)
class SetScopeSpeed:
    speed: int


@dataclass(frozen=True, slots=True)
class SetScopeRef:
    ref: int


@dataclass(frozen=True, slots=True)
class SetScopeHold:
    on: bool


@dataclass(frozen=True, slots=True)
class SetScopeEdge:
    """Select fixed-edge number (1-4)."""

    edge: int


@dataclass(frozen=True, slots=True)
class SetScopeVbw:
    """Set scope VBW (Video Bandwidth): narrow=True for narrow."""

    narrow: bool


@dataclass(frozen=True, slots=True)
class SetScopeRbw:
    """Set scope RBW (Resolution Bandwidth): 0=wide, 1=mid, 2=narrow."""

    rbw: int


@dataclass(frozen=True, slots=True)
class SetPowerstat:
    on: bool


@dataclass(frozen=True, slots=True)
class SetAntenna1:
    on: bool


@dataclass(frozen=True, slots=True)
class SetAntenna2:
    on: bool


@dataclass(frozen=True, slots=True)
class SetRxAntennaAnt1:
    on: bool


@dataclass(frozen=True, slots=True)
class SetRxAntennaAnt2:
    on: bool


@dataclass(frozen=True, slots=True)
class SetSystemDate:
    year: int
    month: int
    day: int


@dataclass(frozen=True, slots=True)
class SetSystemTime:
    hour: int
    minute: int


@dataclass(frozen=True, slots=True)
class SetAcc1ModLevel:
    level: int


@dataclass(frozen=True, slots=True)
class SetUsbModLevel:
    level: int


@dataclass(frozen=True, slots=True)
class SetLanModLevel:
    level: int


@dataclass(frozen=True, slots=True)
class SetDualWatch:
    on: bool


@dataclass(frozen=True, slots=True)
class SetCompressor:
    on: bool


@dataclass(frozen=True, slots=True)
class SetToneFreq:
    freq_hz: int
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetTsqlFreq:
    freq_hz: int
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetMainSubTracking:
    on: bool


@dataclass(frozen=True, slots=True)
class SetSsbTxBandwidth:
    value: int


@dataclass(frozen=True, slots=True)
class SetManualNotchWidth:
    value: int
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetBreakInDelay:
    level: int


@dataclass(frozen=True, slots=True)
class SetVoxGain:
    level: int


@dataclass(frozen=True, slots=True)
class SetAntiVoxGain:
    level: int


@dataclass(frozen=True, slots=True)
class SetVoxDelay:
    level: int


@dataclass(frozen=True, slots=True)
class SetNbDepth:
    level: int
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetNbWidth:
    level: int
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetDashRatio:
    value: int


@dataclass(frozen=True, slots=True)
class SetRepeaterTone:
    on: bool
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetRepeaterTsql:
    on: bool
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetRxAntenna:
    antenna: int  # 1 or 2
    on: bool


@dataclass(frozen=True, slots=True)
class SetMemoryMode:
    channel: int


@dataclass(frozen=True, slots=True)
class MemoryWrite:
    pass


@dataclass(frozen=True, slots=True)
class MemoryToVfo:
    channel: int


@dataclass(frozen=True, slots=True)
class MemoryClear:
    channel: int


@dataclass(frozen=True, slots=True)
class SetMemoryContents:
    mem: Any  # MemoryChannel dataclass


@dataclass(frozen=True, slots=True)
class SetBsr:
    bsr: Any  # BandStackRegister dataclass


@dataclass(frozen=True, slots=True)
class SetDataOffModInput:
    source: int


@dataclass(frozen=True, slots=True)
class SetData1ModInput:
    source: int


@dataclass(frozen=True, slots=True)
class SetData2ModInput:
    source: int


@dataclass(frozen=True, slots=True)
class SetData3ModInput:
    source: int


@dataclass(frozen=True, slots=True)
class SetAudioPeakFilter:
    on: bool
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetRefAdjust:
    value: int


@dataclass(frozen=True, slots=True)
class SetCivTransceive:
    on: bool


@dataclass(frozen=True, slots=True)
class SetCivOutputAnt:
    on: bool


@dataclass(frozen=True, slots=True)
class SetAfMute:
    on: bool
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetDigiselShift:
    level: int
    receiver: int = 0


@dataclass(frozen=True, slots=True)
class SetTuningStep:
    step: int


@dataclass(frozen=True, slots=True)
class SetXfcStatus:
    on: bool


@dataclass(frozen=True, slots=True)
class SetTxFreqMonitor:
    on: bool


@dataclass(frozen=True, slots=True)
class SetUtcOffset:
    hours: int
    minutes: int
    is_negative: bool


@dataclass(frozen=True, slots=True)
class QuickSplit:
    pass


@dataclass(frozen=True, slots=True)
class QuickDualWatch:
    pass


@dataclass(frozen=True, slots=True)
class QuickDwTrigger:
    """Emulate the physical [DUAL-W] long-press:
    equalize MAIN→SUB, then enable Dual Watch.
    Composite of `0x07 0xB1` + `0x07 0xC1`.
    """


@dataclass(frozen=True, slots=True)
class QuickSplitTrigger:
    """Emulate the physical [SPLIT] long-press:
    equalize MAIN→SUB, then enable Split.
    Composite of `0x07 0xB1` + `0x0F 0x01`.
    """


@dataclass(frozen=True, slots=True)
class Speak:
    """Trigger voice synthesizer: 0=all, 1=freq+S, 2=mode."""

    mode: int = 0


Command = (
    SetFreq
    | SetMode
    | SetFilter
    | SetFilterWidth
    | SetPower
    | SetRfGain
    | SetAfLevel
    | SetSquelch
    | SetNB
    | SetNR
    | SetDigiSel
    | SetIpPlus
    | SetAttenuator
    | SetPreamp
    | SetPbtInner
    | SetPbtOuter
    | SetIfShift
    | SetNRLevel
    | SetNBLevel
    | SetAutoNotch
    | SetManualNotch
    | SetNotchFilter
    | SetAgcTimeConstant
    | SetCwPitch
    | SetKeySpeed
    | SetBreakIn
    | SetApf
    | SetTwinPeak
    | SetDriveGain
    | ScanStart
    | ScanStop
    | ScanSetDfSpan
    | ScanSetResume
    | SetDataMode
    | SetMicGain
    | SetVox
    | SetCompressorLevel
    | SetMonitor
    | SetMonitorGain
    | SetDialLock
    | SetAgc
    | SetRitStatus
    | SetRitTxStatus
    | SetRitFrequency
    | SetSplit
    | PttOn
    | PttOff
    | SetBand
    | SelectVfo
    | VfoSwap
    | VfoEqualize
    | EnableScope
    | DisableScope
    | SwitchScopeReceiver
    | SetScopeDuringTx
    | SetScopeCenterType
    | SetScopeEdge
    | SetScopeFixedEdge
    | SetScopeDual
    | SetScopeMode
    | SetScopeSpan
    | SetScopeSpeed
    | SetScopeRef
    | SetScopeHold
    | SetScopeVbw
    | SetScopeRbw
    | SetPowerstat
    | SetAntenna1
    | SetAntenna2
    | SetRxAntennaAnt1
    | SetRxAntennaAnt2
    | SetSystemDate
    | SetSystemTime
    | SetAcc1ModLevel
    | SetUsbModLevel
    | SetLanModLevel
    | SetDualWatch
    | SetCompressor
    | SetToneFreq
    | SetTsqlFreq
    | SetMainSubTracking
    | SetSsbTxBandwidth
    | SetManualNotchWidth
    | SetBreakInDelay
    | SetVoxGain
    | SetAntiVoxGain
    | SetVoxDelay
    | SetNbDepth
    | SetNbWidth
    | SetDashRatio
    | SetRepeaterTone
    | SetRepeaterTsql
    | SetRxAntenna
    | SetMemoryMode
    | MemoryWrite
    | MemoryToVfo
    | MemoryClear
    | SetMemoryContents
    | SetBsr
    | SetDataOffModInput
    | SetData1ModInput
    | SetData2ModInput
    | SetData3ModInput
    | SetAudioPeakFilter
    | SetDigiselShift
    | SetRefAdjust
    | SetCivTransceive
    | SetCivOutputAnt
    | SetAfMute
    | SetTuningStep
    | SetXfcStatus
    | SetTxFreqMonitor
    | SetUtcOffset
    | QuickSplit
    | QuickDualWatch
    | QuickDwTrigger
    | QuickSplitTrigger
    | Speak
)


# ------------------------------------------------------------------
# CommandQueue
# ------------------------------------------------------------------


class CommandQueue:
    def __init__(self) -> None:
        self._dedup: dict[type, Command] = {}
        self._ptt: list[PttOn | PttOff] = []
        self._notify: asyncio.Event = asyncio.Event()

    def put(self, cmd: Command) -> None:
        if isinstance(cmd, (PttOn, PttOff)):
            self._ptt.append(cmd)
        else:
            self._dedup[type(cmd)] = cmd
        self._notify.set()

    def drain(self) -> list[Command]:
        self._notify.clear()
        cmds: list[Command] = []
        cmds.extend(self._ptt)
        self._ptt.clear()
        cmds.extend(self._dedup.values())
        self._dedup.clear()
        return cmds

    @property
    def has_commands(self) -> bool:
        return bool(self._ptt or self._dedup)

    async def wait(self, timeout: float | None = None) -> None:
        try:
            await asyncio.wait_for(self._notify.wait(), timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError):
            pass
