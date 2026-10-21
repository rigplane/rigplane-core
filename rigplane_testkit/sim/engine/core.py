"""Pure deterministic simulator state engine (MOR-3599): plain-value state,
capability-validated operations, injected clock, seeded per-stream RNG,
ordered event queue, JSON-friendly trace. No sockets/asyncio/rigplane
imports; the caller derives Capabilities from a profile (no TOML here).
"""

from __future__ import annotations

import heapq
import random
from dataclasses import dataclass, field, fields
from itertools import count
from math import isfinite
from typing import Protocol


class Clock(Protocol):
    def __call__(self) -> float: ...


@dataclass
class ManualClock:
    """Deterministic test clock; ``advance(dt)`` moves and returns time."""

    start: float = 0.0

    def __post_init__(self) -> None:
        self._now = float(self.start)

    def __call__(self) -> float:
        return self._now

    def advance(self, dt: float) -> float:
        self._now += float(dt)
        return self._now


@dataclass(frozen=True)
class Capabilities:
    receiver_count: int = 1
    modes: tuple[str, ...] = ("USB", "LSB", "CW", "CWR", "AM", "FM", "RTTY", "RTTYR")
    frequency_range_hz: tuple[int, int] = (30_000, 74_800_000)
    levels: dict[str, tuple[int, int]] = field(default_factory=dict)  # (min, max)
    switches: frozenset[str] = frozenset()
    has_split: bool = True
    has_ptt: bool = True


@dataclass
class ReceiverState:
    frequency_hz: int = 14_074_000
    mode: str = "USB"
    filter: int = 1
    data_mode: bool = False
    levels: dict[str, int] = field(default_factory=dict)
    switches: dict[str, bool] = field(default_factory=dict)


@dataclass
class SimState:
    receivers: list[ReceiverState]
    selected_receiver: int = 0
    split: bool = False
    ptt: bool = False
    power_on: bool = True
    meters: dict[str, int] = field(default_factory=dict)
    extra: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class SetFrequency:
    receiver: int
    hz: int


@dataclass(frozen=True)
class SetMode:
    receiver: int
    mode: str
    filter: int | None = None  # None keeps the current filter


@dataclass(frozen=True)
class SetLevel:
    receiver: int
    name: str
    value: int


@dataclass(frozen=True)
class SetSwitch:
    receiver: int
    name: str
    on: bool


@dataclass(frozen=True)
class SetPtt:
    on: bool


@dataclass(frozen=True)
class SelectReceiver:
    receiver: int


@dataclass(frozen=True)
class SetSplit:
    on: bool


@dataclass(frozen=True)
class SetMeter:  # scenario/test input, not a wire operation
    name: str
    value: int


@dataclass(frozen=True)
class ScheduledOp:
    op: object


@dataclass(frozen=True)
class Marker:  # no-op event that appears in the trace
    name: str


@dataclass(frozen=True)
class Ok:
    value: object = None


@dataclass(frozen=True)
class Unsupported:
    what: str


@dataclass(frozen=True)
class OutOfRange:
    what: str
    value: object


@dataclass(frozen=True)
class Refused:
    reason: str


Outcome = Ok | Unsupported | OutOfRange | Refused


@dataclass(frozen=True)
class Record:
    n: int  # gapless from 0
    t: float  # injected-clock time
    kind: str
    name: str
    args: dict
    result: dict


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _jsonable(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return f"<{type(value).__name__}>"  # keep the trace JSON-serialisable


def _field_dict(obj: object) -> dict:
    if not hasattr(obj, "__dataclass_fields__"):
        return {}
    return {f.name: _jsonable(getattr(obj, f.name)) for f in fields(obj)}


def _outcome_result(outcome: Outcome) -> dict:
    tag = "".join(c if c.islower() else f"_{c.lower()}" for c in type(outcome).__name__)
    return {"outcome": tag.removeprefix("_"), **_field_dict(outcome)}


class SimEngine:
    def __init__(
        self,
        caps: Capabilities,
        *,
        clock: Clock,
        seed: int = 0,
        initial: SimState | None = None,
    ) -> None:
        self._caps = caps
        self._clock = clock
        self._seed = seed
        if initial is None:
            lo, hi = caps.frequency_range_hz
            default_hz = min(max(14_074_000, lo), hi)
            default_mode = caps.modes[0]
            initial = SimState(
                [
                    ReceiverState(frequency_hz=default_hz, mode=default_mode)
                    for _ in range(caps.receiver_count)
                ]
            )
        self._state = initial
        self._now = float(clock())
        self._queue: list[tuple[float, int, object]] = []
        self._order = count()
        self._records: list[Record] = []
        self._rngs: dict[str, random.Random] = {}

    @property
    def state(self) -> SimState:
        """Live state object; callers must treat it as read-only."""
        return self._state

    @property
    def trace(self) -> tuple[Record, ...]:
        return tuple(self._records)

    def rng(self, stream: str) -> random.Random:
        """``Random(f"{seed}:{stream}")`` per stream; same name, same object."""
        rng = self._rngs.get(stream)
        if rng is None:
            rng = random.Random(f"{self._seed}:{stream}")
            self._rngs[stream] = rng
        return rng

    def apply(self, op: object) -> Outcome:
        """Validate ``op``; mutate state only on Ok; append one record."""
        return self._apply_recorded(op)[0]

    def read(self, name: str, receiver: int | None = None) -> Outcome:
        """Query state by name; unknown names give ``Unsupported``."""
        if name in ("ptt", "split", "selected_receiver", "power_on"):
            return Ok(getattr(self._state, name))
        if name in ("frequency_hz", "mode", "filter", "data_mode"):
            target = self._target(receiver)
            if isinstance(target, OutOfRange):
                return target
            return Ok(getattr(target, name))
        prefix, _, key = name.partition(":")
        if prefix in ("level", "switch"):
            known = self._caps.levels if prefix == "level" else self._caps.switches
            if key not in known:
                return Unsupported(f"{prefix} {key!r}")
            target = self._target(receiver)
            if isinstance(target, OutOfRange):
                return target
            values = target.levels if prefix == "level" else target.switches
            return Ok(values.get(key))
        if prefix == "meter":
            return Ok(self._state.meters.get(key))
        if prefix == "extra":
            return Ok(self._state.extra.get(key))
        return Unsupported(f"read {name!r}")

    def schedule(self, at_s: float, event: object) -> None:
        """Queue at absolute engine time; equal times keep insertion order."""
        at = float(at_s)
        if not isfinite(at) or at < self._now:
            raise ValueError(f"cannot schedule {at} before engine time {self._now}")
        heapq.heappush(self._queue, (at, next(self._order), event))

    def advance_to(self, now_s: float) -> list[Record]:
        """Run every event with ``at <= now_s`` in (time, insertion) order."""
        now = float(now_s)
        if not isfinite(now) or now < self._now:
            raise ValueError(f"cannot advance to {now} before current time {self._now}")
        produced: list[Record] = []
        while self._queue and self._queue[0][0] <= now:
            _, _, event = heapq.heappop(self._queue)
            if isinstance(event, ScheduledOp):
                produced.append(self._apply_recorded(event.op)[1])
            elif isinstance(event, Marker):
                produced.append(
                    self._record("marker", event.name, {}, {"outcome": "ok"})
                )
            else:
                raise TypeError(f"unknown event: {event!r}")
        self._now = now
        return produced

    def _apply_recorded(self, op: object) -> tuple[Outcome, Record]:
        outcome = self._apply(op)
        record = self._record(
            "op", type(op).__name__, _field_dict(op), _outcome_result(outcome)
        )
        return outcome, record

    def _apply(self, op: object) -> Outcome:
        if isinstance(op, SetFrequency):
            target = self._target(op.receiver)
            if isinstance(target, OutOfRange):
                return target
            lo, hi = self._caps.frequency_range_hz
            if not _is_int(op.hz) or not lo <= op.hz <= hi:
                return OutOfRange("frequency_hz", op.hz)
            target.frequency_hz = op.hz
        elif isinstance(op, SetMode):
            target = self._target(op.receiver)
            if isinstance(target, OutOfRange):
                return target
            if op.mode not in self._caps.modes:
                return Unsupported(f"mode {op.mode!r}")
            if op.filter is not None and not _is_int(op.filter):
                return OutOfRange("filter", op.filter)
            target.mode = op.mode
            target.filter = op.filter if op.filter is not None else target.filter
        elif isinstance(op, SetLevel):
            target = self._target(op.receiver)
            if isinstance(target, OutOfRange):
                return target
            bounds = self._caps.levels.get(op.name)
            if bounds is None:
                return Unsupported(f"level {op.name!r}")
            if not _is_int(op.value) or not bounds[0] <= op.value <= bounds[1]:
                return OutOfRange(f"level {op.name}", op.value)
            target.levels[op.name] = op.value
        elif isinstance(op, SetSwitch):
            target = self._target(op.receiver)
            if isinstance(target, OutOfRange):
                return target
            if op.name not in self._caps.switches:
                return Unsupported(f"switch {op.name!r}")
            if not isinstance(op.on, bool):
                return OutOfRange(f"switch {op.name}", op.on)
            target.switches[op.name] = op.on
        elif isinstance(op, SetPtt):
            if not self._caps.has_ptt:
                return Unsupported("ptt")
            if not isinstance(op.on, bool):
                return OutOfRange("ptt", op.on)
            if op.on and not self._state.power_on:
                return Refused("power off")
            self._state.ptt = op.on
        elif isinstance(op, SetSplit):
            if not self._caps.has_split:
                return Unsupported("split")
            if not isinstance(op.on, bool):
                return OutOfRange("split", op.on)
            self._state.split = op.on
        elif isinstance(op, SelectReceiver):
            err = self._target(op.receiver)
            if isinstance(err, OutOfRange):
                return err
            self._state.selected_receiver = op.receiver
        elif isinstance(op, SetMeter):
            if not _is_int(op.value):
                return OutOfRange(f"meter {op.name}", op.value)
            self._state.meters[op.name] = op.value
        else:
            return Unsupported(f"op {type(op).__name__}")
        return Ok()

    def _target(self, receiver: int | None) -> ReceiverState | OutOfRange:
        index = self._state.selected_receiver if receiver is None else receiver
        if not _is_int(index) or not 0 <= index < self._caps.receiver_count:
            return OutOfRange("receiver", index)
        return self._state.receivers[index]

    def _record(self, kind: str, name: str, args: dict, result: dict) -> Record:
        record = Record(
            len(self._records), float(self._clock()), kind, name, args, result
        )
        self._records.append(record)
        return record
