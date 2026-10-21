"""Yaesu CAT protocol model (MOR-3602): one wire line in, one reply line out.

Pure and socket-free; no wall clock decides a reply. Replies come from
this module's own codec tables, never from the production
parser/formatter, so the same code cannot pass both sides of a test.
Precedence for one request line (docs/internals/sim-architecture.md
section 5): a native engine-backed handler's data reply or accepted SET
wins immediately; a native ``?;`` refusal falls through to the recorded
answer table, where recorded data, refusal or silence wins; with no
recorded entry the refusal stands. Only a well-formed CAT command (two
uppercase letters, then only digits, letters, ``+`` or ``-``) can reach
that precedence chain; anything else (unterminated, non-ASCII, over 64
bytes, more than one ``;``, a body outside that shape) is malformed: it
answers ``?;`` instead of raising, is not counted, and never consults
the recorded table. Declared but unimplemented forms use a private sentinel,
distinct from a native refusal. Like requests with no native handler, they
consult the recorded table first; without an entry they are the
``Unsupported("not_modelled")`` coverage gap: each
answers ``?;``, is counted per request line in ``not_modelled``, and is
reported to the ``on_not_modelled`` observer callback when one was
given. The callback is best-effort instrumentation: an ordinary
``Exception`` from it is logged and swallowed and never changes the
reply (a ``BaseException`` such as ``KeyboardInterrupt`` propagates),
and the model never touches the engine for it (no trace record is written and no scheduled
operation is drained). ``handle_with_outcome`` returns the reply
together with its explicit engine ``Outcome``.

Engine state keys are the capability profile's exact control names
(rigs/ftx1.toml), no aliases: the levels ``af_level``/``rf_gain``/
``squelch``/``nb_level`` and the selectors ``agc``/``preamp``.

The engine stores whatever a scenario sets. The one wire-side guard is the
S-meter: ``SetMeter`` is only checked to be an int, so the reply reports the
reading clamped to the 000-255 wire domain, and seeding from a recording
ignores a reading outside it. The model-owned ``state.extra`` keys are
validated once, when :func:`build_ftx1_engine` builds the engine (a bad
value raises ``ValueError``), and every SET or seeding write to them stays
in-domain, so the fixed-width replies need no fallback.

FTX-1 facts fixed by tests/fixtures/ftx1/cat_pairs.jsonl: ``SM1;`` reads
the SUB meter (``s_meter_sub``; MAIN's is ``s_meter_main``) but echoes
the side digit as 0; a receiver's S-meter reads 0 while its AGC is OFF
(the AGC link is the likely cause, not proven); every ``FA`` SET is
refused while MAIN is in MEMORY mode (observed on the MAIN side only, so
``FB`` stays accepted). Reply formats are byte-identical to the
recording for the state seeded from it.

Keyer memory (MOR-3678), from the FTX-1 CAT Operation Reference Manual
FTX-1_CAT_OM_ENG_2508-C, not from a recording: ``KM`` KEYING MEMORY
(printed p.17, PDF p.18) writes ``KM P1 P2...;`` with P1 the channel 1-5
and P2 up to 50 message characters, and reads ``KM P1;``; ``KY`` CW
KEYING MEMORY PLAY (printed p.18, PDF p.19) sets ``KY P1 P2;`` with P1
0 TEXT / 1 MESSAGE memory and P2 0 STOP / 1-5 playback, and has no read
form (command list, printed p.5: Read X, Answer X). Where the manual is
silent the model stays conservative: KM text is limited to the
well-formed body charset above (the manual names no KM character set;
a space or CW punctuation is malformed here), a read of a channel the
sim never saw written is the ``not_modelled`` gap (no invented empty or
padded answer), and KY only records the request: the manual does not
say whether KM writes the TEXT or the MESSAGE memory, so playback is not
linked to KM's text, and playback keys nothing (the engine PTT is
untouched). The keyer memory lives in the model, not the engine.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from rigplane_testkit.sim.engine import (
    Capabilities,
    Clock,
    Ok,
    Outcome,
    ReceiverState,
    Refused,
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
    Unsupported,
)
from rigplane_testkit.sim.profiles import SimProfile
from rigplane_testkit.yaesu.replay import REFUSAL, RecordedCatAnswerTable

__all__ = [
    "NATIVE_READS",
    "NATIVE_SET_VERBS",
    "YaesuCatModel",
    "build_ftx1_engine",
    "seed_from_recording",
]

_MAX_LINE = 64  # bytes; a longer request answers ?;

_LOG = logging.getLogger("rigplane_testkit.sim.model.yaesu_cat")


class _NotModelled:
    """A declared command form without native implementation, not a refusal."""


_NOT_MODELLED = _NotModelled()

#: Well-formed request body: two uppercase letters, then only digits,
#: letters, ``+`` or ``-`` (the FTX-1 CAT OM frame shape). Anything else
#: is malformed: refused, never counted, never looked up in the table.
_BODY = re.compile(r"[A-Z]{2}[0-9A-Za-z+\-]*")

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
NATIVE_SET_VERBS = frozenset("FA FB MD TX AI ST VS AG RG SQ NL GT PA KM KY".split())

# FTX-1 CAT OM 2508-C: KM P1 "1 - 5", P2 "up to 50 characters" (printed
# p.17); KY P1 "0"/"1", P2 "0" STOP or "1"-"5" (printed p.18).
_KEYER_CHANNELS = "12345"
_KEYER_TEXT_MAX = 50

# Static codec data the engine cannot express, handed over through
# ``SimState.extra`` by ``build_ftx1_engine`` (scalar values only).
_EXTRA_MODE_NAMES = "cat_mode_names"
_EXTRA_MODE_CODES = "cat_mode_codes"

# IF reply layout (rigs/ftx1.toml get_if_status): chan(5) freq(9) sign
# offset(4) clarifier rx/tx mode memory tone tail(3). The tail is seeded
# and echoed verbatim; its meaning is not decoded.
_IF_REPLY = re.compile(r"IF(.{5})(\d{9})([+-])(\d{4}).{2}(.)(.)(.)(.{3});")

#: Domains of the model-owned ``state.extra`` int keys, enforced once by
#: ``build_ftx1_engine`` (a bad value raises ``ValueError``) and kept by
#: every SET/seeding write, so no reply re-checks them.
_EXTRA_INT_DOMAINS = {
    "memory_channel": (0, 99_999),  # 5-digit IF field
    "if_offset_hz": (-9_999, 9_999),  # sign plus 4-digit IF field
    "if_tone_mode": (0, 9),  # 1-digit IF field
    "auto_information": (0, 1),  # 1-digit AI field
}


def _is_valid_if_tail(value: object) -> bool:
    """The IF tail is exactly 3 printable-ASCII bytes; ``;`` would end the
    reply early and anything non-ASCII would break the 3-byte field."""
    return (
        type(value) is str
        and len(value) == 3
        and all(" " <= ch <= "~" and ch != ";" for ch in value)
    )


def _validate_extra(extra: Mapping[str, object]) -> None:
    """Reject an out-of-domain model-owned ``state.extra`` value."""
    for key, (lo, hi) in _EXTRA_INT_DOMAINS.items():
        value = extra.get(key)
        if type(value) is not int or not lo <= value <= hi:
            raise ValueError(
                f"state.extra.{key}: expected an int in {lo}..{hi}, got {value!r}"
            )
    memory_mode = extra.get("main_memory_mode")
    if type(memory_mode) is not bool:
        raise ValueError(
            f"state.extra.main_memory_mode: expected a bool, got {memory_mode!r}"
        )
    tail = extra.get("if_tail")
    if not _is_valid_if_tail(tail):
        raise ValueError(
            "state.extra.if_tail: expected exactly 3 printable-ASCII chars "
            f"other than ';', got {tail!r}"
        )


class _RigData(Protocol):
    """The resolved rig data :func:`build_ftx1_engine` reads, structurally.

    Profile resolution belongs to the loader/factory boundary
    (docs/internals/sim-architecture.md section 3), so the model imports
    nothing from ``rigplane``; a real ``RigConfig`` satisfies this protocol
    without being named here.
    """

    model: str
    receiver_count: int
    modes: tuple[str, ...]
    mode_codes: tuple[str, ...] | None
    agc_readback_modes: tuple[int, ...] | None
    pre_values: tuple[int, ...] | None
    freq_ranges: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class _LevelCodec:
    """One receiver-routed 3-digit level: wire verb, engine field, top value."""

    verb: str
    name: str
    maximum: int


# Ranges from tests/golden/wire_oracle/ftx1.toml (NL: "000 OFF, 001-010").
# The engine state key is the capability profile's exact control name
# (docs/internals/sim-architecture.md section 4): rigs/ftx1.toml declares
# [controls.af_level] / [controls.rf_gain] / [controls.squelch] /
# [controls.nb_level].
_LEVELS = (
    _LevelCodec("AG", "af_level", 255),
    _LevelCodec("RG", "rf_gain", 255),
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
    _SelectorCodec("PA", "preamp", (0,), 2),  # HF/50 MHz only; 0 IPO, 1/2 AMP1/2
)
_SELECTOR_BY_VERB = {codec.verb: codec for codec in _SELECTORS}


class YaesuCatModel:
    """Yaesu CAT request/reply personality on top of a :class:`SimEngine`.

    ``handle`` takes one request line with its ``;`` and returns the reply
    line with its ``;``, or ``None`` for "no reply" (a served SET, or
    recorded silence); ``handle_with_outcome`` also returns the explicit
    engine ``Outcome`` behind that reply. The engine must come from
    ``build_ftx1_engine``: the mode name/code mapping travels in its
    ``state.extra``. *on_not_modelled* is an observer called with the
    request line each time a well-formed request hits the coverage gap;
    it is best-effort instrumentation: an ordinary ``Exception`` from it
    never changes the reply, a ``BaseException`` propagates.
    """

    def __init__(
        self,
        engine: SimEngine,
        *,
        identity: str,
        recorded: RecordedCatAnswerTable | None = None,
        on_not_modelled: Callable[[str], None] | None = None,
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
        self._on_not_modelled = on_not_modelled
        self._not_modelled: dict[str, int] = {}
        self._name_to_code = dict(zip(name_list, codes))
        self._code_to_name = {code: name for name, code in self._name_to_code.items()}
        self._keying_memory: dict[int, str] = {}
        self._keyer_play_requests: list[tuple[int, int]] = []
        self._handlers: dict[str, Callable[[str], str | None | _NotModelled]] = {
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
            "KM": self._keying_memory_command,
            "KY": self._keyer_play,
        }
        for codec in _LEVELS:
            self._handlers[codec.verb] = lambda body, c=codec: self._level(body, c)
        for codec in _SELECTORS:
            self._handlers[codec.verb] = lambda body, c=codec: self._selector(body, c)

    @property
    def engine(self) -> SimEngine:
        return self._engine

    @property
    def not_modelled(self) -> Mapping[str, int]:
        """``Unsupported("not_modelled")`` replies per request line (a copy)."""
        return dict(self._not_modelled)

    @property
    def keying_memory(self) -> Mapping[int, str]:
        """KM channel -> text the sim saw written (a copy)."""
        return dict(self._keying_memory)

    @property
    def keyer_play_requests(self) -> tuple[tuple[int, int], ...]:
        """Accepted ``KY P1 P2;`` requests as ``(P1, P2)``, in order."""
        return tuple(self._keyer_play_requests)

    def handle(self, line: str) -> str | None:
        """Answer one request line (``;`` included); ``None`` means no reply."""
        return self.handle_with_outcome(line)[0]

    def handle_with_outcome(self, line: str) -> tuple[str | None, Outcome]:
        """Answer one request line and name the explicit outcome.

        The reply is exactly what :meth:`handle` returns; the outcome is
        ``Ok()`` for a native data reply, an accepted SET or a recorded
        answer, ``Refused("malformed")`` for malformed input,
        ``Refused("native_refusal")`` for a native refusal that stood,
        and ``Unsupported("not_modelled")`` for the coverage gap.
        """
        if not isinstance(line, str):
            return REFUSAL, Refused("malformed")
        if (
            not line.isascii()
            or len(line) > _MAX_LINE
            or not line.endswith(";")
            or ";" in line[:-1]
            or _BODY.fullmatch(line[:-1]) is None
        ):
            # Malformed input is its own class: refused, never counted,
            # and the recorded table is never consulted for it.
            return REFUSAL, Refused("malformed")
        body = line[:-1]
        handler = self._handlers.get(body[:2])
        reply = handler(body) if handler is not None else _NOT_MODELLED
        if not isinstance(reply, _NotModelled) and reply != REFUSAL:
            return reply, Ok()  # native data or accepted SET (None) wins
        # Both native refusals and unimplemented forms defer to recorded
        # data, refusal or silence before choosing their distinct outcomes.
        if self._recorded is not None:
            answer = self._recorded.lookup(line)
            if answer is not None:
                # Data line, refusal, or None: recorded silence.
                return answer.reply, Ok()
        if reply == REFUSAL:
            return REFUSAL, Refused("native_refusal")
        self._note_not_modelled(line)
        return REFUSAL, Unsupported("not_modelled")

    def _note_not_modelled(self, line: str) -> None:
        """Count the coverage gap, then report it to the observer callback.

        The callback is best-effort instrumentation: a failure in it is
        logged and swallowed (``Exception`` only — a ``BaseException``
        such as a cancellation propagates); an ``Exception`` never changes
        the reply. The engine is not involved at all.
        """
        self._not_modelled[line] = self._not_modelled.get(line, 0) + 1
        if self._on_not_modelled is None:
            return
        try:
            self._on_not_modelled(line)
        except Exception:
            _LOG.exception("on_not_modelled callback failed for %r", line)

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

    def _power(self, body: str) -> str | _NotModelled:
        if body == "PS":
            return "PS1;" if self._read_bool("power_on", True) else "PS0;"
        if body in ("PS0", "PS1"):
            return _NOT_MODELLED  # declared SET; the engine has no power op
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

    def _selector(self, body: str, codec: _SelectorCodec) -> str | None | _NotModelled:
        rest = body[2:]
        # rigs/ftx1.toml set_preamp selects a band, not a receiver. Only
        # band 0 (HF/50 MHz) is backed by the engine's preamp selector.
        if (
            codec.verb == "PA"
            and len(rest) == 2
            and rest[0] in "12"
            and rest[1] in "012"
        ):
            return _NOT_MODELLED
        if len(rest) == 1 and rest.isdigit() and int(rest) in codec.sides:
            # The engine enforces the capability domain on every write, so
            # the one-digit readback field cannot widen.
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

    def _keying_memory_command(self, body: str) -> str | None | _NotModelled:
        # KM KEYING MEMORY, CAT OM 2508-C printed p.17.
        if len(body) < 3 or body[2] not in _KEYER_CHANNELS:
            return REFUSAL
        channel = int(body[2])
        text = body[3:]
        if not text:
            stored = self._keying_memory.get(channel)
            # An unwritten channel has no sourced answer: coverage gap.
            return _NOT_MODELLED if stored is None else f"KM{channel}{stored};"
        if len(text) > _KEYER_TEXT_MAX:
            return REFUSAL
        self._keying_memory[channel] = text
        return None

    def _keyer_play(self, body: str) -> str | None:
        # KY CW KEYING MEMORY PLAY, CAT OM 2508-C printed p.18: set-only.
        if (
            len(body) != 4
            or body[2] not in "01"
            or body[3] not in "0" + _KEYER_CHANNELS
        ):
            return REFUSAL
        self._keyer_play_requests.append((int(body[2]), int(body[3])))
        return None

    def _s_meter(self, body: str) -> str:
        if body not in ("SM0", "SM1"):
            return REFUSAL
        # Each receiver reads its own meter (s_meter_main / s_meter_sub);
        # AGC OFF pins that receiver's reading at 0 (recorded; cause
        # likely, not proven). Both sides echo the side digit as 0
        # (recorded quirk).
        side = int(body[2])
        agc = self._read_int("selector:agc", side)
        meter = "s_meter_main" if side == 0 else "s_meter_sub"
        value = 0 if agc == 0 else self._read_int(f"meter:{meter}")
        # The engine stores whatever the scenario sets (SetMeter is only
        # checked to be an int); the CAT wire domain is 000-255, so the
        # reply reports the clamped value.
        return f"SM0{min(max(value, 0), 255):03d};"

    def _information(self, body: str) -> str:
        if body != "IF":
            return REFUSAL  # SET on IF is refused
        code = self._mode_code(0)
        if code is None:
            return REFUSAL  # unreachable: engine modes all have codes
        # The extra fields are validated when the engine is built and every
        # write to them stays in-domain, so each already fits its exact wire
        # width (5/9/sign+4 digits, 1 digit, 3 bytes).
        offset = self._extra_int("if_offset_hz")
        sign = "+" if offset >= 0 else "-"
        memory = "1" if self._extra_bool("main_memory_mode") else "0"
        return (
            f"IF{self._extra_int('memory_channel'):05d}"
            f"{self._read_int('frequency_hz', 0):09d}"
            f"{sign}{abs(offset):04d}00{code}{memory}"
            f"{self._extra_int('if_tone_mode', 2)}"
            f"{self._engine.state.extra.get('if_tail', '003')};"
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
            if request in ("SM0;", "SM1;"):  # each side seeds its own meter
                digits = reply[3:-1]
                # A reading outside the 000-255 wire domain is not seeded.
                if len(digits) == 3 and digits.isdigit() and int(digits) <= 255:
                    meter = "s_meter_main" if request == "SM0;" else "s_meter_sub"
                    self._applied(SetMeter(meter, int(digits)))
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
        # A recorded tail that would break the 3-byte wire field is ignored.
        if _is_valid_if_tail(tail):
            extra["if_tail"] = tail


def build_ftx1_engine(
    profile: SimProfile, rig: _RigData, *, clock: Clock, seed: int = 0
) -> SimEngine:
    """Build the FTX-1 engine: capabilities from *rig* (the loaded
    ``rigs/ftx1.toml``), receiver-0 initial state from *profile*.

    The profile's ``[state.extra]`` is overlaid on the model's own extra
    defaults (profile values win, unknown keys pass through untouched) and
    the model-owned keys are then validated: a bad value raises
    ``ValueError``.
    """
    if rig.receiver_count != 2:
        raise ValueError(f"{rig.model!r}: the model expects 2 receivers")
    if not rig.mode_codes or len(rig.mode_codes) != len(rig.modes):
        raise ValueError(f"{rig.model!r}: [modes] list/codes must be parallel")
    if any("," in mode for mode in rig.modes):
        raise ValueError(f"{rig.model!r}: mode names must not contain commas")
    if not rig.agc_readback_modes or not rig.pre_values:
        raise ValueError(f"{rig.model!r}: [agc]/[preamp] domains are required")
    # FA/FB accept the whole CAT domain, which is wider than any single
    # [freq_ranges] entry: the wire oracle (tests/golden/wire_oracle/
    # ftx1.toml, rows vfo_a_freq/vfo_b_freq, FTX-1 CAT OM 2508-C p. 16)
    # declares 000030000-470000000 Hz, so the lower bound is the oracle's
    # 30 kHz, not the lowest profile range start (100 kHz).
    lo = 30_000
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
    main_filter = profile.initial.filter
    main_data_mode = profile.initial.data_mode
    extra = {
        "auto_information": 0,
        "main_memory_mode": False,
        "memory_channel": 0,
        "if_offset_hz": 0,
        "if_tone_mode": 2,
        "if_tail": "003",
        _EXTRA_MODE_NAMES: ",".join(rig.modes),
        _EXTRA_MODE_CODES: "".join(rig.mode_codes),
    }
    extra.update(dict(profile.state_extra))  # profile values win
    _validate_extra(extra)
    initial = SimState(
        receivers=[
            ReceiverState(
                frequency_hz=main_hz,
                mode=main_mode,
                filter=1 if main_filter is None else main_filter,
                data_mode=False if main_data_mode is None else main_data_mode,
            ),
            ReceiverState(frequency_hz=default_hz, mode=caps.modes[0]),
        ],
        meters={"s_meter_main": 0, "s_meter_sub": 0},
        extra=extra,
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
