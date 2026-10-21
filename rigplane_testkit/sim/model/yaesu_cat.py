"""Yaesu CAT protocol model (MOR-3602): one wire line in, one reply line out.

Pure, socket-free and clock-free. Replies come from this module's own
codec tables, never from the production parser/formatter, so the same
code cannot pass both sides of a test. Precedence for one request line:
a native engine-backed handler, then the recorded answer table, then the
``?;`` refusal. Malformed lines (unterminated, non-ASCII, over 64 bytes,
more than one ``;``) answer ``?;`` instead of raising.

FTX-1 facts fixed by tests/fixtures/ftx1/cat_pairs.jsonl: ``SM1;`` is
answered with the side digit echoed as 0; a receiver's S-meter reads 0
while its AGC is OFF (the AGC link is the likely cause, not proven);
every ``FA`` SET is refused while MAIN is in MEMORY mode (observed on the
MAIN side only, so ``FB`` stays accepted). Reply formats are
byte-identical to the recording for the state seeded from it.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from rigplane_testkit.sim.engine import (
    Capabilities,
    Clock,
    Ok,
    ReceiverState,
    SelectReceiver,
    SetFrequency,
    SetLevel,
    SetMeter,
    SetMode,
    SetPtt,
    SetSelector,
    SetSplit,
    SimEngine,
    SimState,
)
from rigplane_testkit.sim.profiles import SimProfile
from rigplane_testkit.yaesu.replay import REFUSAL, RecordedCatAnswerTable

if TYPE_CHECKING:
    from rigplane.profiles.rig_loader import RigConfig

__all__ = [
    "NATIVE_READS",
    "NATIVE_SET_VERBS",
    "YaesuCatModel",
    "build_ftx1_engine",
    "seed_from_recording",
]

_MAX_LINE = 64  # bytes; a longer request answers ?;

#: Read lines the model serves from engine state (seed order).
NATIVE_READS = (
    "ID;",
    "IF;",
    "FA;",
    "FB;",
    "MD0;",
    "MD1;",
    "TX;",
    "PS;",
    "AI;",
    "ST;",
    "VS;",
    "SM0;",
    "SM1;",
    "AG0;",
    "AG1;",
    "RG0;",
    "RG1;",
    "SQ0;",
    "SQ1;",
    "NL0;",
    "NL1;",
    "GT0;",
    "GT1;",
    "PA0;",
)

#: Two-letter verbs whose SET lines the model serves natively.
NATIVE_SET_VERBS = frozenset("FA FB MD TX AI ST VS AG RG SQ NL GT PA".split())

# Static codec data the engine cannot express, handed over through
# ``SimState.extra`` by ``build_ftx1_engine`` (scalar values only).
_EXTRA_MODE_NAMES = "cat_mode_names"
_EXTRA_MODE_CODES = "cat_mode_codes"

# IF reply layout (rigs/ftx1.toml get_if_status): chan(5) freq(9) sign
# offset(4) clarifier rx/tx mode memory tone tail(3). The tail is seeded
# and echoed verbatim; its meaning is not decoded.
_IF_REPLY = re.compile(r"IF(.{5})(\d{9})([+-])(\d{4}).{2}(.)(.)(.)(.{3});")


@dataclass(frozen=True, slots=True)
class _LevelCodec:
    """One receiver-routed 3-digit level: wire verb, engine field, top value."""

    verb: str
    name: str
    maximum: int


# Ranges from tests/golden/wire_oracle/ftx1.toml (NL: "000 OFF, 001-010").
_LEVELS = (
    _LevelCodec("AG", "af", 255),
    _LevelCodec("RG", "rf", 255),
    _LevelCodec("SQ", "squelch", 255),
    _LevelCodec("NL", "nb_level", 10),
)
_LEVEL_BY_VERB = {codec.verb: codec for codec in _LEVELS}


@dataclass(frozen=True, slots=True)
class _SelectorCodec:
    """One receiver-routed 1-digit selector; a SET accepts 0..set_max."""

    verb: str
    name: str
    sides: tuple[int, ...]
    set_max: int


_SELECTORS = (
    # rigs/ftx1.toml [agc]: 0-4 settable; 5/6 are AUTO-speed readbacks the
    # radio only answers with, so a SET of 5/6 is refused.
    _SelectorCodec("GT", "agc", (0, 1), 4),
    _SelectorCodec("PA", "preamp", (0,), 2),  # 0 IPO, 1 AMP1, 2 AMP2; MAIN only
)
_SELECTOR_BY_VERB = {codec.verb: codec for codec in _SELECTORS}


class YaesuCatModel:
    """Yaesu CAT request/reply personality on top of a :class:`SimEngine`.

    ``handle`` takes one request line with its ``;`` and returns the reply
    line with its ``;``, or ``None`` for "no reply" (a served SET, or
    recorded silence). The engine must come from ``build_ftx1_engine``:
    the mode name/code mapping travels in its ``state.extra``.
    """

    def __init__(
        self,
        engine: SimEngine,
        *,
        identity: str,
        recorded: RecordedCatAnswerTable | None = None,
    ) -> None:
        if type(identity) is not str or not identity:
            raise ValueError(f"identity must be a non-empty string, got {identity!r}")
        names = engine.state.extra.get(_EXTRA_MODE_NAMES)
        codes = engine.state.extra.get(_EXTRA_MODE_CODES)
        if type(names) is not str or type(codes) is not str:
            raise ValueError(
                "engine extra lacks cat_mode_names/cat_mode_codes; "
                "build the engine with build_ftx1_engine"
            )
        name_list = names.split(",")
        if len(name_list) != len(codes) or not all(name_list):
            raise ValueError("cat_mode_names and cat_mode_codes are not parallel")
        self._engine = engine
        self._identity = identity
        self._recorded = recorded
        self._name_to_code = dict(zip(name_list, codes))
        self._code_to_name = {code: name for name, code in self._name_to_code.items()}
        self._handlers: dict[str, Callable[[str], str | None]] = {
            "ID": self._identity_read,
            "IF": self._information,
            "FA": lambda body: self._frequency(body, "FA", 0),
            "FB": lambda body: self._frequency(body, "FB", 1),
            "MD": self._mode,
            "TX": self._ptt,
            "PS": self._power,
            "AI": self._auto_information,
            "ST": self._split,
            "VS": self._vfo_select,
            "SM": self._s_meter,
        }
        for codec in _LEVELS:
            self._handlers[codec.verb] = lambda body, c=codec: self._level(body, c)
        for codec in _SELECTORS:
            self._handlers[codec.verb] = lambda body, c=codec: self._selector(body, c)

    @property
    def engine(self) -> SimEngine:
        return self._engine

    def handle(self, line: str) -> str | None:
        """Answer one request line (``;`` included); ``None`` means no reply."""
        if not isinstance(line, str):
            return REFUSAL
        if (
            not line.isascii()
            or len(line) > _MAX_LINE
            or not line.endswith(";")
            or ";" in line[:-1]
        ):
            return REFUSAL
        body = line[:-1]
        handler = self._handlers.get(body[:2])
        if handler is not None:
            return handler(body)
        if self._recorded is not None:
            answer = self._recorded.lookup(line)
            if answer is not None:
                return answer.reply  # data line, refusal, or None: recorded silence
        return REFUSAL

    # ------------------------------------------------------------------
    # engine helpers
    # ------------------------------------------------------------------

    def _applied(self, op: object) -> str | None:
        """Apply a SET op: no reply on Ok, ``?;`` on any other outcome."""
        return None if isinstance(self._engine.apply(op), Ok) else REFUSAL

    def _read_int(
        self, name: str, receiver: int | None = None, default: int = 0
    ) -> int:
        outcome = self._engine.read(name, receiver)
        if isinstance(outcome, Ok) and type(outcome.value) is int:
            return outcome.value
        return default

    def _read_bool(self, name: str, default: bool = False) -> bool:
        outcome = self._engine.read(name)
        if isinstance(outcome, Ok) and type(outcome.value) is bool:
            return outcome.value
        return default

    def _read_str(self, name: str, receiver: int, default: str = "") -> str:
        outcome = self._engine.read(name, receiver)
        if isinstance(outcome, Ok) and type(outcome.value) is str:
            return outcome.value
        return default

    def _extra_int(self, name: str, default: int = 0) -> int:
        value = self._engine.state.extra.get(name)
        return value if type(value) is int else default

    def _extra_bool(self, name: str, default: bool = False) -> bool:
        value = self._engine.state.extra.get(name)
        return value if type(value) is bool else default

    def _mode_code(self, receiver: int) -> str | None:
        return self._name_to_code.get(self._read_str("mode", receiver))

    # ------------------------------------------------------------------
    # native verb handlers (body = request line without its ';')
    # ------------------------------------------------------------------

    def _identity_read(self, body: str) -> str:
        if body == "ID":
            return f"ID{self._identity};"
        return REFUSAL

    def _frequency(self, body: str, verb: str, receiver: int) -> str | None:
        if body == verb:
            return f"{verb}{self._read_int('frequency_hz', receiver):09d};"
        digits = body[2:]
        if len(digits) != 9 or not digits.isdigit():
            return REFUSAL
        if verb == "FA" and self._extra_bool("main_memory_mode"):
            # Recorded: MAIN in MEMORY mode refuses every FA SET. FB in
            # MEMORY mode is not observed and stays accepted.
            return REFUSAL
        return self._applied(SetFrequency(receiver, int(digits)))

    def _mode(self, body: str) -> str | None:
        if len(body) == 3 and body[2] in "01":
            code = self._mode_code(int(body[2]))
            return f"MD{body[2]}{code};" if code is not None else REFUSAL
        if len(body) == 4 and body[2] in "01":
            name = self._code_to_name.get(body[3])
            if name is None:
                return REFUSAL
            return self._applied(SetMode(int(body[2]), name))
        return REFUSAL

    def _ptt(self, body: str) -> str | None:
        if body == "TX":
            return "TX1;" if self._read_bool("ptt") else "TX0;"
        if body in ("TX0", "TX1"):
            return self._applied(SetPtt(body == "TX1"))
        return REFUSAL  # TX2 (self-keyed readback) is not settable

    def _power(self, body: str) -> str:
        if body == "PS":
            return "PS1;" if self._read_bool("power_on", True) else "PS0;"
        # The engine has no power op: PS SET is refused, state unchanged.
        return REFUSAL

    def _auto_information(self, body: str) -> str | None:
        if body == "AI":
            return f"AI{self._extra_int('auto_information')};"
        if body in ("AI0", "AI1"):
            # No engine op covers this flag; extra is its documented home.
            # No unsolicited pushes are sent in this PR either way.
            self._engine.state.extra["auto_information"] = int(body[2])
            return None
        return REFUSAL

    def _split(self, body: str) -> str | None:
        if body == "ST":
            return "ST1;" if self._read_bool("split") else "ST0;"
        if body in ("ST0", "ST1"):
            return self._applied(SetSplit(body == "ST1"))
        return REFUSAL

    def _vfo_select(self, body: str) -> str | None:
        if body == "VS":
            return f"VS{self._read_int('selected_receiver')};"
        if body in ("VS0", "VS1"):
            return self._applied(SelectReceiver(int(body[2])))
        return REFUSAL

    def _level(self, body: str, codec: _LevelCodec) -> str | None:
        rest = body[2:]
        if len(rest) == 1 and rest in "01":
            value = self._read_int(f"level:{codec.name}", int(rest))
            return f"{codec.verb}{rest}{value:03d};"
        if len(rest) == 4 and rest[0] in "01" and rest[1:].isdigit():
            value = int(rest[1:])
            if value > codec.maximum:
                return REFUSAL
            return self._applied(SetLevel(int(rest[0]), codec.name, value))
        return REFUSAL

    def _selector(self, body: str, codec: _SelectorCodec) -> str | None:
        rest = body[2:]
        if len(rest) == 1 and rest.isdigit() and int(rest) in codec.sides:
            value = self._read_int(f"selector:{codec.name}", int(rest))
            return f"{codec.verb}{rest}{value};"
        if (
            len(rest) == 2
            and rest[0].isdigit()
            and int(rest[0]) in codec.sides
            and rest[1].isdigit()
        ):
            value = int(rest[1])
            if value > codec.set_max:
                return REFUSAL
            return self._applied(SetSelector(int(rest[0]), codec.name, value))
        return REFUSAL

    def _s_meter(self, body: str) -> str:
        if body not in ("SM0", "SM1"):
            return REFUSAL
        # AGC OFF pins the reading at 0 (recorded; cause likely, not
        # proven). SM1; echoes the side digit as 0 (recorded quirk).
        agc = self._read_int("selector:agc", int(body[2]))
        value = 0 if agc == 0 else self._read_int("meter:s")
        return f"SM0{value:03d};"

    def _information(self, body: str) -> str:
        if body != "IF":
            return REFUSAL  # SET on IF is refused
        code = self._mode_code(0)
        if code is None:
            return REFUSAL  # unreachable: engine modes all have codes
        offset = self._extra_int("if_offset_hz")
        sign = "+" if offset >= 0 else "-"
        tone = min(max(self._extra_int("if_tone_mode", 2), 0), 9)
        tail = self._engine.state.extra.get("if_tail", "003")
        if type(tail) is not str or len(tail) != 3:
            tail = "003"
        memory = "1" if self._extra_bool("main_memory_mode") else "0"
        return (
            f"IF{self._extra_int('memory_channel'):05d}"
            f"{self._read_int('frequency_hz', 0):09d}"
            f"{sign}{abs(offset):04d}00{code}{memory}{tone}{tail};"
        )

    # ------------------------------------------------------------------
    # seeding (own decoders, the inverse of the encoders above)
    # ------------------------------------------------------------------

    def _seed(self, request: str, reply: str) -> None:
        verb = request[:2]
        if verb == "ID":
            return  # identity is constructor data, not state
        if verb == "IF":
            self._seed_information(reply)
            return
        if verb in ("FA", "FB"):
            digits = reply[2:-1]
            if len(digits) == 9 and digits.isdigit():
                self._applied(SetFrequency(0 if verb == "FA" else 1, int(digits)))
            return
        if verb == "MD":
            name = self._code_to_name.get(reply[3:-1])
            if name is not None:
                self._applied(SetMode(int(request[2]), name))
            return
        if verb == "TX":
            if reply in ("TX0;", "TX1;"):
                self._applied(SetPtt(reply == "TX1;"))
            return
        if verb == "PS":
            if reply in ("PS0;", "PS1;"):
                # No engine op: power_on is seeded by direct assignment.
                self._engine.state.power_on = reply == "PS1;"
            return
        if verb == "AI":
            if reply in ("AI0;", "AI1;"):
                self._engine.state.extra["auto_information"] = int(reply[2])
            return
        if verb == "ST":
            if reply in ("ST0;", "ST1;"):
                self._applied(SetSplit(reply == "ST1;"))
            return
        if verb == "VS":
            if reply in ("VS0;", "VS1;"):
                self._applied(SelectReceiver(int(reply[2])))
            return
        if verb == "SM":
            if request == "SM0;":  # SM1 reads the same meter through the quirk
                digits = reply[3:-1]
                if len(digits) == 3 and digits.isdigit():
                    self._applied(SetMeter("s", int(digits)))
            return
        level = _LEVEL_BY_VERB.get(verb)
        if level is not None:
            digits = reply[3:-1]
            if len(digits) == 3 and digits.isdigit():
                self._applied(SetLevel(int(request[2]), level.name, int(digits)))
            return
        selector = _SELECTOR_BY_VERB.get(verb)
        if selector is not None:
            value = reply[3:-1]
            if len(value) == 1 and value.isdigit():
                self._applied(SetSelector(int(request[2]), selector.name, int(value)))

    def _seed_information(self, reply: str) -> None:
        match = _IF_REPLY.fullmatch(reply)
        if match is None:
            return
        chan, freq, sign, offset, mode, memory, tone, tail = match.groups()
        self._applied(SetFrequency(0, int(freq)))
        name = self._code_to_name.get(mode)
        if name is not None:
            self._applied(SetMode(0, name))
        extra = self._engine.state.extra
        if chan.isdigit():
            extra["memory_channel"] = int(chan)
        extra["if_offset_hz"] = int(offset) if sign == "+" else -int(offset)
        extra["main_memory_mode"] = memory == "1"
        if tone.isdigit():
            extra["if_tone_mode"] = int(tone)
        extra["if_tail"] = tail


def build_ftx1_engine(
    profile: SimProfile, rig: RigConfig, *, clock: Clock, seed: int = 0
) -> SimEngine:
    """Build the FTX-1 engine: capabilities from *rig* (the loaded
    ``rigs/ftx1.toml``), receiver-0 initial state from *profile*."""
    if rig.receiver_count != 2:
        raise ValueError(f"{rig.model!r}: the model expects 2 receivers")
    if not rig.mode_codes or len(rig.mode_codes) != len(rig.modes):
        raise ValueError(f"{rig.model!r}: [modes] list/codes must be parallel")
    if any("," in mode for mode in rig.modes):
        raise ValueError(f"{rig.model!r}: mode names must not contain commas")
    if not rig.agc_readback_modes or not rig.pre_values:
        raise ValueError(f"{rig.model!r}: [agc]/[preamp] domains are required")
    lo = min(entry["start_hz"] for entry in rig.freq_ranges)
    hi = max(entry["end_hz"] for entry in rig.freq_ranges)
    caps = Capabilities(
        receiver_count=rig.receiver_count,
        modes=tuple(rig.modes),
        frequency_range_hz=(lo, hi),
        levels={codec.name: (0, codec.maximum) for codec in _LEVELS},
        selectors={
            "agc": tuple(rig.agc_readback_modes),
            "preamp": tuple(rig.pre_values),
        },
    )
    if profile.initial.mode is not None and profile.initial.mode not in caps.modes:
        raise ValueError(f"initial mode {profile.initial.mode!r} not in {caps.modes}")
    default_hz = min(max(14_074_000, lo), hi)
    main_hz = profile.initial.frequency_hz
    if main_hz is None:
        main_hz = default_hz
    main_mode = profile.initial.mode
    if main_mode is None:
        main_mode = caps.modes[0]
    initial = SimState(
        receivers=[
            ReceiverState(frequency_hz=main_hz, mode=main_mode),
            ReceiverState(frequency_hz=default_hz, mode=caps.modes[0]),
        ],
        meters={"s": 0},
        extra={
            "auto_information": 0,
            "main_memory_mode": False,
            "memory_channel": 0,
            "if_offset_hz": 0,
            "if_tone_mode": 2,
            "if_tail": "003",
            _EXTRA_MODE_NAMES: ",".join(rig.modes),
            _EXTRA_MODE_CODES: "".join(rig.mode_codes),
        },
    )
    return SimEngine(caps, clock=clock, seed=seed, initial=initial)


def seed_from_recording(model: YaesuCatModel, recorded: RecordedCatAnswerTable) -> None:
    """Seed engine/extra from the recorded replies to the native reads.

    Replies are parsed with the model's own decoders (the inverse of its
    encoders), so seeding and replying share one table. A missing or
    silent recorded read keeps the default; the conformance test then
    fails loudly instead of the gap being hidden.
    """
    for request in NATIVE_READS:
        answer = recorded.lookup(request)
        if answer is None or answer.reply is None:
            continue
        model._seed(request, answer.reply)
