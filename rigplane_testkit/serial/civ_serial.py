"""A stateful IC-7300 serial CI-V fake behind the production link seam (MOR-3218).

``CivSerialFake`` speaks the wire: bytes in, bytes out, framed
``FE FE ... FD``. It mounts behind the production ``SerialCivLink``
unmodified through its ``open_serial_connection`` seam (a zero-argument
async callable returning ``(reader, writer)``) and behind
``rigplane.core.serial_open.open_serial_port`` through the ``opener`` seam
(keyword ``url``/``baudrate``), so serial identity/reconnect tests can drive
the real link instead of mocking it.

Wire-codec ringfence (the discipline ``tests/civ_fake.py`` documents): the
frame envelope comes from the production codec — this module imports exactly
``parse_civ_frame``, ``build_civ_frame`` and ``CONTROLLER_ADDR`` from
``rigplane.commands``, plus the profile loader and ``CivFrame``. The fake
does NOT share ``SerialFrameCodec`` with the link under test: inbound stream
splitting is a deliberately dumb loop (split on ``FE FE``, take to the first
``FD`` — no resync, no abort handling, no length cap), so a codec bug in the
link cannot be masked by a shared implementation. BCD helpers are imported
from ``rigplane_testkit.icom``, never re-implemented here.

Identity is profile-derived, never hardcoded: the CI-V address comes from
``[radio].civ_addr`` and the ``19 00`` model-id answer from
``[identity].expected_ids`` of the resolved profile (``rigs/ic7300.toml``
declares both; a profile without ``expected_ids`` makes the constructor
refuse rather than guess). The personality — frequency (03/05), mode/filter
(04/06), PTT (1C 00), S-meter (15 02), identity (19 00), ACK/NAK — follows
the IC-7300 Full Manual (English) v6, §19 CONTROL COMMAND.

The USB CI-V port echoes every inbound frame by default: production serial
code observes that echo on a real IC-7300 (``backends/discovery.py`` —
"first read often returns only the echo of our" probe frame — and
``backends/_icom_serial_base.py: _read_serial_identity_payload`` — "the raw
payload bytes after the 19 00 echo"). Pass ``echo=False`` to turn it off.

A :class:`~rigplane_testkit.icom.replay.RecordedAnswerTable` mounts through
the ``recorded_answers`` keyword. With a table mounted, a request addressed
to the fake is handled in this order: a Command29-wrapped request is
answered only from the table or NAKed — never unwrapped into a native
handler; an UNWRAPPED request keeps its native handler if it has one
(frequency, mode, PTT, S-meter and identity keep their state); a
controller's own ACK/NAK gets no reply; any other request falls to the
table, then to a NAK. The echo default then comes from the table's ``echo``
fact unless ``echo=`` is passed explicitly; without a table the default
stays on.

The CI-V USB baud rate is modelled on request (MOR-3620): ``answers_at_baud=N``
hears only a connection at N baud (a radio fixed at that rate),
``locks_to_first_baud=True`` follows the first frame's baud and ignores every
other rate (``CI-V USB Baud Rate = Auto``, ``tests/fixtures/ic7300/evidence.json``).
The connection baud comes from the opener's ``baudrate`` keyword and from
``transport.serial.baudrate`` writes (``SerialCivLink.set_baudrate``); without
either option the fake hears every rate and its writer has no ``transport``.

A simulation profile (``rigplane_testkit/sim/profiles/ic7300.toml``, MOR-3597)
configures the fake through :meth:`CivSerialFake.from_profile`: recorded
answers, initial state, echo, the baud the radio is set to, the reply latency
with its per-answer-class ``[timing]`` delays (the ACK latencies, the serial
write drain and the PTT switch delays) and a :class:`ScopeSource` of recorded
wave sweeps. With a scope source mounted
the fake serves the scope switches natively (``27 10`` panel, ``27 11`` wave
output; initially panel on, wave off, as recorded) and, while wave output is
on, streams the recorded ``27 00`` sweeps at the profile's sweep rate. The
wave-output enable is refused with a NAK on a link whose baud is below
``scope_min_baud`` (recorded: NAK at 19200, ACK at 115200,
``tests/fixtures/ic7300/evidence.json``); a link that reports no baud is never
gated.

Faults are scriptable and deterministic: ``delay_identity_answer``,
``answer_identity_after_reconnect_only``, ``wrong_model_id``, ``silence``
(the MOR-2861 case: link open, nothing answers), ``usb_vanish`` /
``usb_reappear`` (reads, writes and new opens raise ``ENODEV``, the class
``core/serial_open.py: serial_open_diagnostic`` maps to
``serial_not_found``).
"""

from __future__ import annotations

import asyncio
import errno
import json
import math
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.profiles import resolve_radio_profile
from rigplane.profiles.rig_loader import load_rig
from rigplane.types import CivFrame

from rigplane_testkit.icom import (
    _bcd_decode_freq,
    _bcd_encode_freq,
    _level_bcd_encode,
)
from rigplane_testkit.icom.replay import RecordedAnswerTable
from rigplane_testkit.sim.profiles import SimProfile, load_sim_profile

_BROADCAST_ADDR = 0x00  # CI-V broadcast: every radio hears it, none NAKs it

# IC-7300 Full Manual (English) v6, §19 CONTROL COMMAND.
_CMD_FREQ_GET = 0x03  # read operating frequency
_CMD_MODE_GET = 0x04  # read operating mode (+ filter)
_CMD_FREQ_SET = 0x05  # set operating frequency
_CMD_MODE_SET = 0x06  # set operating mode (+ filter)
_CMD_METER = 0x15  # meter readings
_SUB_S_METER = 0x02
_CMD_TRANSCEIVER_ID = 0x19  # read transceiver ID
_SUB_TRANSCEIVER_ID = 0x00
_CMD_SEL_FREQ_GET = 0x25  # read the selected VFO's frequency (data 00)
_CMD_SEL_MODE_GET = 0x26  # read the selected VFO's mode, data mode and filter (data 00)
_CMD_PTT = 0x1C  # transceiver status
_SUB_PTT = 0x00
_CMD_SCOPE = 0x27  # scope: 00 wave packet, 10 panel on/off, 11 wave output on/off
_SUB_SCOPE_WAVE = 0x00
_SUB_SCOPE_PANEL = 0x10
_SUB_SCOPE_OUTPUT = 0x11
_CMD_ACK = 0xFB
_CMD_NAK = 0xFA

_PROFILE_REL = Path("rigplane_testkit/sim/profiles/ic7300.toml")
_RIG_REL = Path("rigs/ic7300.toml")

# Operating mode byte of ``04``/``06`` (Full Manual v6, §19 mode table).
_CIV_MODE_CODES = {
    "LSB": 0x00, "USB": 0x01, "AM": 0x02, "CW": 0x03,
    "RTTY": 0x04, "FM": 0x05, "CW-R": 0x07, "RTTY-R": 0x08,
}  # fmt: skip

# Errno a disappeared USB-serial device raises on the next read; mapped to
# ``serial_not_found`` by ``core/serial_open.py: serial_open_diagnostic``.
_VANISH_ERRNO = errno.ENODEV


@dataclass(frozen=True, slots=True)
class ScopeSource:
    """Recorded scope sweeps the fake replays while wave output is on.

    ``sweeps`` holds whole sweeps, each a tuple of ``(offset_s, frame)``
    packets with the offset from the sweep's first packet. Sweep ``k`` of the
    stream starts ``k / rate_hz`` seconds after wave output turned on and
    replays the recorded sweeps in a loop. ``min_baud`` is the lowest link
    baud that carries wave output (``None``: no limit).
    """

    sweeps: tuple[tuple[tuple[float, bytes], ...], ...]
    rate_hz: float
    min_baud: int | None = None

    def __post_init__(self) -> None:
        # inf would start every sweep at the same instant and spin the loop.
        if not 0.0 < self.rate_hz < math.inf:
            raise ValueError(
                f"scope rate_hz must be finite and > 0, got {self.rate_hz!r}"
            )

    @classmethod
    def from_civ_pairs(
        cls,
        rows: Iterable[dict[str, Any]],
        transport: str,
        *,
        rate_hz: float,
        min_baud: int | None = None,
    ) -> ScopeSource:
        """Group the radio's ``27 00`` packets of one ``transport`` by sweep.

        A sweep opens at a packet numbered 1 and is kept only when its packets
        run 1..max without a gap (BCD numbers, as on the wire); a partial
        sweep at either end of the recording is dropped.
        """
        whole: list[tuple[tuple[float, bytes], ...]] = []
        current: list[tuple[float, bytes]] = []
        for row in rows:
            if row["transport"] != transport or row["dir"] != "r2c":
                continue
            frame = bytes.fromhex(row["frame"])
            if frame[4:6] != bytes([_CMD_SCOPE, _SUB_SCOPE_WAVE]) or len(frame) < 10:
                continue
            seq, seq_max = _bcd_byte(frame[7]), _bcd_byte(frame[8])
            if seq == 1:
                current = []
            elif len(current) != seq - 1:
                current = []  # a packet is missing: the partial sweep is dropped
                continue
            current.append((float(row["t"]), frame))
            if seq == seq_max:
                whole.append(tuple((t - current[0][0], f) for t, f in current))
                current = []
        if not whole:
            raise ValueError(
                f"no whole scope sweep recorded for transport {transport!r}"
            )
        return cls(tuple(whole), rate_hz, min_baud)


def _bcd_byte(value: int) -> int:
    return (value >> 4) * 10 + (value & 0x0F)


def _reply_command(reply: bytes) -> int | None:
    """The command byte of a reply frame, ``None`` when it does not parse."""
    try:
        return parse_civ_frame(reply).command
    except ValueError:
        return None  # a recorded reply that is not a parseable CI-V frame


class CivSerialFake:
    """A stateful Icom radio on the far end of an in-memory serial duplex.

    State (frequency, mode, filter, PTT, S-meter) changes only through the
    wire or the explicit test-setup property setters. Every fault is
    scripted through the methods below, never hidden in an answer table.
    """

    def __init__(
        self,
        model: str = "IC-7300",
        *,
        civ_addr: int | None = None,
        model_id: bytes | None = None,
        echo: bool | None = None,
        recorded_answers: RecordedAnswerTable | None = None,
        frequency_hz: int = 14_074_000,
        mode: int = 0x01,  # USB (§19 mode table)
        filter_width: int = 1,
        s_meter: int = 120,
        answers_at_baud: int | None = None,
        locks_to_first_baud: bool = False,
        reply_latency_s: float = 0.0,
        ack_set_s: float | None = None,
        ptt_set_ack_s: float | None = None,
        readback_s: float | None = None,
        write_drain_s: float | None = None,
        tx_on_switch_s: float | None = None,
        tx_off_switch_s: float | None = None,
        scope: ScopeSource | None = None,
    ) -> None:
        if answers_at_baud is not None and locks_to_first_baud:
            raise ValueError("answers_at_baud and locks_to_first_baud are exclusive")
        if not 0.0 <= reply_latency_s < float("inf"):
            raise ValueError(
                f"reply_latency_s must be a finite number >= 0: {reply_latency_s!r}"
            )
        for name, value in (
            ("ack_set_s", ack_set_s),
            ("ptt_set_ack_s", ptt_set_ack_s),
            ("readback_s", readback_s),
            ("write_drain_s", write_drain_s),
            ("tx_on_switch_s", tx_on_switch_s),
            ("tx_off_switch_s", tx_off_switch_s),
        ):
            if value is not None and not 0.0 <= value < float("inf"):
                raise ValueError(f"{name} must be a finite number >= 0: {value!r}")
        profile = resolve_radio_profile(model=model)
        self._model = profile.model
        self._civ_addr = civ_addr if civ_addr is not None else profile.civ_addr
        if model_id is None:
            if not profile.expected_identity_ids:
                raise ValueError(
                    f"profile {profile.model!r} declares no "
                    "[identity].expected_ids; pass model_id= explicitly from a "
                    "manual-cited source"
                )
            model_id = bytes.fromhex(profile.expected_identity_ids[0])
        self._model_id = bytes(model_id)
        self._recorded_answers = recorded_answers
        if echo is not None:
            self.echo = echo
        elif recorded_answers is not None:
            self.echo = recorded_answers.echo
        else:
            self.echo = True
        self._frequency_hz = frequency_hz
        self._mode = mode
        self._filter_width = filter_width
        self._ptt = False
        self._s_meter = s_meter

        # Fault surface (all off by default).
        self._identity_delay_s = 0.0
        self._identity_after_reconnect_only = False
        self._model_id_override: bytes | None = None
        self._silent_until = 0.0
        self._vanished = False
        self._answers_at_baud = answers_at_baud
        self._locks_to_first_baud = locks_to_first_baud
        self._locked_baud: int | None = None
        self._frame_bauds: list[int | None] = []
        self._reply_latency_s = reply_latency_s
        # Per-class delays from [timing]; an unset key falls back to the
        # reply latency (ptt_set_ack to ack_set first), and an unset write
        # drain or switch delay to "no delay".
        self._ack_set_s = reply_latency_s if ack_set_s is None else ack_set_s
        self._ptt_set_ack_s = (
            self._ack_set_s if ptt_set_ack_s is None else ptt_set_ack_s
        )
        self._readback_s = reply_latency_s if readback_s is None else readback_s
        self._write_drain_s = 0.0 if write_drain_s is None else write_drain_s
        self._tx_on_switch_s = 0.0 if tx_on_switch_s is None else tx_on_switch_s
        self._tx_off_switch_s = 0.0 if tx_off_switch_s is None else tx_off_switch_s
        self._ptt_switch_handle: asyncio.TimerHandle | None = None
        # The ``time()``/``call_at()`` object delayed replies and the PTT
        # switch schedule on; tests mount a manual clock here, and the
        # running asyncio loop is resolved at use time by default.
        self._clock: Any = None
        self._scope = scope
        self._sim_profile: SimProfile | None = None
        # As the bench radio stood when it was recorded: panel on, wave output off.
        self._scope_panel = True
        self._scope_wave = False

        self._open_count = 0
        self._connections: list[_Connection] = []
        self._commands: list[CivFrame] = []

    @classmethod
    def from_profile(
        cls,
        root: Path,
        profile_path: Path | None = None,
        *,
        baud: int | None = None,
        reply_latency_s: float | None = None,
    ) -> CivSerialFake:
        """Build the fake from the simulation profile under *root* (MOR-3597).

        Loads ``rigplane_testkit/sim/profiles/ic7300.toml`` (or
        *profile_path*) with ``load_sim_profile``, mounts the recorded answer
        table and scope sweeps named by its ``[answers] recorded`` entry, and
        applies ``[initial]``, ``[transport.serial]`` (echo; the baud the
        radio answers at) and ``[timing]``. *baud* overrides the profile's
        baud (a radio set to another CI-V USB baud) and *reply_latency_s* its
        reply latency. Identity comes from ``rigs/ic7300.toml``, never from
        the profile.
        """
        root = Path(root)
        path = Path(profile_path) if profile_path is not None else root / _PROFILE_REL
        profile = load_sim_profile(path, root=root)
        if profile.transport.kind != "serial":
            raise ValueError(f"{path.name}: not a serial CI-V profile")
        if len(profile.answers.recorded) != 1:
            raise ValueError(
                f"{path.name}: exactly one [answers] recorded entry is required"
            )
        file, transport = profile.answers.recorded[0]
        rows = [
            json.loads(line)
            for line in (root / file).read_text(encoding="utf-8").splitlines()
            if line
        ]
        initial = profile.initial
        mode = _CIV_MODE_CODES.get(initial.mode) if initial.mode is not None else 0x01
        if mode is None:
            raise ValueError(f"initial.mode: no CI-V mode byte for {initial.mode!r}")
        extra: dict[str, Any] = {}
        if initial.frequency_hz is not None:
            extra["frequency_hz"] = initial.frequency_hz
        if initial.filter is not None:
            extra["filter_width"] = initial.filter
        scope = None
        if profile.scope.rate_hz is not None:
            if profile.scope.frame != "27 00":
                raise ValueError(
                    f"scope.frame: only '27 00' is served, got {profile.scope.frame!r}"
                )
            scope = ScopeSource.from_civ_pairs(
                rows,
                transport,
                rate_hz=profile.scope.rate_hz,
                min_baud=load_rig(root / _RIG_REL).default_baud,
            )
            bins = {sum(len(f) - 10 for _, f in sweep[1:]) for sweep in scope.sweeps}
            if bins != {profile.scope.bins}:
                raise ValueError(
                    f"scope.bins: profile says {profile.scope.bins}, the recording has {sorted(bins)}"
                )
        latency_ms = profile.timing.reply_latency_ms
        if reply_latency_s is None:
            reply_latency_s = 0.0 if latency_ms is None else latency_ms / 1000.0

        def _to_seconds(value: float | None) -> float | None:
            return None if value is None else value / 1000.0

        timing = profile.timing
        fake = cls(
            model=profile.sim.radio,
            echo=profile.transport.echo,
            recorded_answers=RecordedAnswerTable.from_civ_pairs(rows, transport),
            mode=mode,
            answers_at_baud=baud if baud is not None else profile.transport.baud,
            reply_latency_s=reply_latency_s,
            ack_set_s=_to_seconds(timing.ack_ms_set),
            ptt_set_ack_s=_to_seconds(timing.ptt_set_ack_ms),
            readback_s=_to_seconds(timing.readback_ms),
            write_drain_s=_to_seconds(timing.write_drain_ms),
            tx_on_switch_s=_to_seconds(timing.tx_on_switch_ms),
            tx_off_switch_s=_to_seconds(timing.tx_off_switch_ms),
            scope=scope,
            **extra,
        )
        fake._sim_profile = profile
        # The native S-meter read must say what the radio said, not the default.
        assert fake._recorded_answers is not None
        meter = fake._recorded_answers.answer(
            build_civ_frame(
                fake._civ_addr, CONTROLLER_ADDR, _CMD_METER, sub=_SUB_S_METER
            )
        )
        if meter is not None:
            fake._s_meter = int(parse_civ_frame(meter).data.hex())
        return fake

    # ------------------------------------------------------------------
    # Identity / state surface
    # ------------------------------------------------------------------

    @property
    def model(self) -> str:
        return self._model

    @property
    def sim_profile(self) -> SimProfile | None:
        """The simulation profile :meth:`from_profile` loaded, else ``None``."""
        return self._sim_profile

    @property
    def civ_addr(self) -> int:
        return self._civ_addr

    @property
    def model_id(self) -> bytes:
        """The model-id bytes the ``19 00`` identity read answers with."""
        return self._model_id_override or self._model_id

    @property
    def frequency_hz(self) -> int:
        return self._frequency_hz

    @frequency_hz.setter
    def frequency_hz(self, value: int) -> None:
        self._frequency_hz = int(value)

    @property
    def mode(self) -> int:
        return self._mode

    @property
    def filter_width(self) -> int:
        return self._filter_width

    @property
    def ptt(self) -> bool:
        return self._ptt

    @property
    def s_meter(self) -> int:
        return self._s_meter

    @s_meter.setter
    def s_meter(self, value: int) -> None:
        self._s_meter = int(value)

    @property
    def open_count(self) -> int:
        """How many times the port has been (re)opened."""
        return self._open_count

    @property
    def models_baud(self) -> bool:
        """Whether the radio hears only the baud it is fixed or locked to."""
        return self._answers_at_baud is not None or self._locks_to_first_baud

    @property
    def locked_baud(self) -> int | None:
        """The baud an Auto radio locked to, ``None`` before the first frame."""
        return self._locked_baud

    def power_cycle(self) -> None:
        """Restart the radio: an Auto radio forgets the baud it locked to."""
        self._locked_baud = None

    @property
    def frame_bauds(self) -> tuple[int | None, ...]:
        """The connection baud of every inbound frame, in order."""
        return tuple(self._frame_bauds)

    @property
    def commands(self) -> tuple[CivFrame, ...]:
        """Every well-formed inbound frame, parsed, in order of arrival."""
        return tuple(self._commands)

    # ------------------------------------------------------------------
    # Scriptable faults
    # ------------------------------------------------------------------

    def delay_identity_answer(self, seconds: float) -> None:
        """Answer the ``19 00`` identity read *seconds* late."""
        if seconds < 0:
            raise ValueError("seconds must be >= 0")
        self._identity_delay_s = seconds

    def answer_identity_after_reconnect_only(self) -> None:
        """Drop ``19 00`` until the port is REOPENED (open ordinal >= 2)."""
        self._identity_after_reconnect_only = True

    def wrong_model_id(self, model_id: bytes | None = None) -> None:
        """Answer ``19 00`` with a model id the profile does not expect.

        The default wrong id is profile-derived too: the IC-7610's declared
        native ID from ``rigs/ic7610.toml`` — a real radio that is not this
        one. (Falls back to an inverted byte if the profiles ever collide.)
        """
        if model_id is None:
            other = resolve_radio_profile(model="IC-7610")
            candidate = (
                bytes.fromhex(other.expected_identity_ids[0])
                if other.expected_identity_ids
                else b""
            )
            if not candidate or candidate == self._model_id:
                candidate = bytes([self._model_id[0] ^ 0xFF])
            model_id = candidate
        self._model_id_override = bytes(model_id)

    def silence(self, for_seconds: float) -> None:
        """MOR-2861: the link stays open and nothing answers for the window.

        Writes are still accepted (a silent device does not error the port);
        reads simply never yield — echo included.
        """
        self._silent_until = time.monotonic() + max(0.0, for_seconds)

    def usb_vanish(self, at: float = 0.0) -> None:
        """Make the device disappear *at* seconds from now.

        Every open connection fails: pending and later reads raise
        ``OSError(ENODEV)`` and writes raise it too. New opens raise the same
        error while the device is gone — the class ``serial_open.py`` maps to
        ``serial_not_found``.
        """
        asyncio.get_running_loop().call_later(max(0.0, at), self._vanish_now)

    def usb_reappear(self, after: float = 0.0) -> None:
        """Make the device openable again *after* seconds from now."""
        asyncio.get_running_loop().call_later(max(0.0, after), self._reappear_now)

    def _vanish_now(self) -> None:
        self._vanished = True
        error = OSError(_VANISH_ERRNO, "CI-V fake: USB device disappeared")
        for connection in self._connections:
            connection.fail(error)

    def _reappear_now(self) -> None:
        self._vanished = False

    # ------------------------------------------------------------------
    # Mounting seams
    # ------------------------------------------------------------------

    async def open_serial_connection(
        self, baudrate: int | None = None
    ) -> tuple[asyncio.StreamReader, Any]:
        """Opener for ``SerialCivLink(open_serial_connection=...)``; a test that
        models the baud binds it with ``functools.partial(..., baudrate=N)``."""
        return self._open(baudrate)

    async def opener(
        self,
        *,
        url: str | None = None,
        baudrate: int | None = None,
        **kwargs: Any,
    ) -> tuple[asyncio.StreamReader, Any]:
        """``open_serial_port``-style opener (keyword ``url``/``baudrate``)."""
        _ = (url, kwargs)
        return self._open(baudrate)

    def _open(
        self, baudrate: int | None = None
    ) -> tuple[asyncio.StreamReader, "_FakeSerialWriter"]:
        if self._vanished:
            raise OSError(_VANISH_ERRNO, "CI-V fake: USB device is gone")
        self._open_count += 1
        connection = _Connection(self, self._open_count, baudrate)
        self._connections.append(connection)
        if self._scope is not None and self._scope_wave:
            connection.start_scope()  # wave output is a radio setting: it outlives sessions
        return connection.reader, connection.writer

    # ------------------------------------------------------------------
    # Radio behaviour
    # ------------------------------------------------------------------

    def _hears(self, connection: "_Connection") -> bool:
        """Whether the radio's CI-V USB baud matches this connection's."""
        baud = connection.baud
        if baud is None:
            return True  # the opener did not say: nothing to mismatch
        if self._locks_to_first_baud:
            if self._locked_baud is None:
                self._locked_baud = baud
            return baud == self._locked_baud
        return self._answers_at_baud is None or baud == self._answers_at_baud

    def _on_wire_frame(self, connection: "_Connection", frame: bytes) -> None:
        self._frame_bauds.append(connection.baud)
        if not self._hears(connection):
            return  # wrong baud: line noise to the radio, no answer, no echo
        if self._vanished or time.monotonic() < self._silent_until:
            return  # silent device: writes accepted, nothing answers, no echo
        if self.echo:
            # USB CI-V echo: the port returns the inbound frame verbatim.
            connection.deliver(frame)
        try:
            parsed = parse_civ_frame(frame)
        except ValueError:
            return  # garbage on the wire: a real radio ignores it
        self._commands.append(parsed)
        if parsed.to_addr not in (self._civ_addr, _BROADCAST_ADDR):
            return
        reply = self._answer(parsed, connection, frame)
        if reply is None:
            return
        delay = self._answer_delay_s(parsed, reply)
        identity_fault = (
            parsed.command == _CMD_TRANSCEIVER_ID and self._identity_delay_s > 0
        )
        if parsed.command == _CMD_TRANSCEIVER_ID:
            delay += self._identity_delay_s
        connection.deliver_after(delay, reply, fault=identity_fault)
        if connection.scope_start_pending:
            connection.scope_start_pending = False
            connection.start_scope()  # the wave follows the ACK that enabled it

    def _scheduler(self) -> Any:
        """The ``time()``/``call_at()`` object delayed replies and the PTT
        switch schedule on: the clock mounted at ``_clock`` when present,
        else the running asyncio loop, resolved at use time."""
        if self._clock is not None:
            return self._clock
        return asyncio.get_running_loop()

    def _answer_delay_s(self, parsed: CivFrame, reply: bytes) -> float:
        """The reply latency of *reply*, chosen by the frame the fake sends.

        An ACK/NAK answer (FB/FA) takes ``ack_ms_set`` — and the ACK/NAK
        to a PTT write (``1C 00`` with a data byte) takes ``ptt_set_ack_ms``
        — while the answer to a PTT read (``1C 00`` without data) takes
        ``readback_ms``; every other answer takes the reply latency.
        """
        ptt = parsed.command == _CMD_PTT and parsed.sub == _SUB_PTT
        if _reply_command(reply) in (_CMD_ACK, _CMD_NAK):
            return self._ptt_set_ack_s if ptt and parsed.data else self._ack_set_s
        if ptt and not parsed.data:
            return self._readback_s
        return self._reply_latency_s

    def _schedule_ptt_switch(self, on: bool) -> None:
        """Flip PTT *on* after the profile's switch delay; the latest write
        wins (a still-pending switch is cancelled), and a zero delay flips
        synchronously, as an unset key behaves."""
        if self._ptt_switch_handle is not None:
            self._ptt_switch_handle.cancel()
            self._ptt_switch_handle = None
        delay = self._tx_on_switch_s if on else self._tx_off_switch_s
        if delay <= 0.0:
            self._ptt = on
            return
        loop = self._scheduler()

        def switch() -> None:
            self._ptt_switch_handle = None
            self._ptt = on

        self._ptt_switch_handle = loop.call_at(loop.time() + delay, switch)

    def _answer(
        self, parsed: CivFrame, connection: "_Connection", frame: bytes
    ) -> bytes | None:
        cmd = parsed.command
        if self._recorded_answers is not None and parsed.receiver is not None:
            # Command29-wrapped: answered from the recording or NAKed, never
            # unwrapped into a native handler.
            recorded = self._recorded_answers.answer(frame)
            return recorded if recorded is not None else self._nak()
        if cmd == _CMD_TRANSCEIVER_ID and parsed.sub == _SUB_TRANSCEIVER_ID:
            if self._identity_after_reconnect_only and connection.ordinal < 2:
                return None
            return build_civ_frame(
                CONTROLLER_ADDR,
                self._civ_addr,
                _CMD_TRANSCEIVER_ID,
                sub=_SUB_TRANSCEIVER_ID,
                data=self.model_id,
            )
        if cmd == _CMD_FREQ_GET and not parsed.data:
            return build_civ_frame(
                CONTROLLER_ADDR,
                self._civ_addr,
                _CMD_FREQ_GET,
                data=_bcd_encode_freq(self._frequency_hz),
            )
        if cmd == _CMD_SEL_FREQ_GET and parsed.data == b"\x00":
            return build_civ_frame(
                CONTROLLER_ADDR,
                self._civ_addr,
                _CMD_SEL_FREQ_GET,
                data=b"\x00" + _bcd_encode_freq(self._frequency_hz),
            )
        if cmd == _CMD_SEL_MODE_GET and parsed.data == b"\x00":
            # Data mode is not modelled: the recorded radio had it off (00).
            return build_civ_frame(
                CONTROLLER_ADDR,
                self._civ_addr,
                _CMD_SEL_MODE_GET,
                data=bytes([0x00, self._mode, 0x00, self._filter_width]),
            )
        if cmd == _CMD_FREQ_SET:
            if len(parsed.data) == 5:
                self._frequency_hz = _bcd_decode_freq(parsed.data)
                return self._ack()
            return self._nak()
        if cmd == _CMD_MODE_GET and not parsed.data:
            return build_civ_frame(
                CONTROLLER_ADDR,
                self._civ_addr,
                _CMD_MODE_GET,
                data=bytes([self._mode, self._filter_width]),
            )
        if cmd == _CMD_MODE_SET:
            if parsed.data:
                self._mode = parsed.data[0]
                if len(parsed.data) > 1:
                    self._filter_width = parsed.data[1]
                return self._ack()
            return self._nak()
        if cmd == _CMD_PTT and parsed.sub == _SUB_PTT:
            if not parsed.data:
                return build_civ_frame(
                    CONTROLLER_ADDR,
                    self._civ_addr,
                    _CMD_PTT,
                    sub=_SUB_PTT,
                    data=bytes([int(self._ptt)]),
                )
            if parsed.data[0] in (0x00, 0x01):
                self._schedule_ptt_switch(bool(parsed.data[0]))
                return self._ack()
            return self._nak()
        if cmd == _CMD_METER and parsed.sub == _SUB_S_METER and not parsed.data:
            return build_civ_frame(
                CONTROLLER_ADDR,
                self._civ_addr,
                _CMD_METER,
                sub=_SUB_S_METER,
                data=_level_bcd_encode(self._s_meter),
            )
        if (
            cmd == _CMD_SCOPE
            and self._scope is not None
            and parsed.sub in (_SUB_SCOPE_PANEL, _SUB_SCOPE_OUTPUT)
        ):
            return self._scope_switch(parsed, connection)
        if cmd in (_CMD_ACK, _CMD_NAK):
            return None  # a controller's own ACK/NAK is not answered
        if parsed.to_addr == _BROADCAST_ADDR:
            return None  # unknown broadcast: silence, as on a real bus
        if self._recorded_answers is not None:
            recorded = self._recorded_answers.answer(frame)
            if recorded is not None:
                return recorded
        return self._nak()

    def _scope_switch(self, parsed: CivFrame, connection: "_Connection") -> bytes:
        """``27 10`` (scope panel) and ``27 11`` (wave output): read or set."""
        output = parsed.sub == _SUB_SCOPE_OUTPUT
        if not parsed.data:
            state = self._scope_wave if output else self._scope_panel
            return build_civ_frame(
                CONTROLLER_ADDR,
                self._civ_addr,
                _CMD_SCOPE,
                sub=parsed.sub,
                data=bytes([int(state)]),
            )
        if len(parsed.data) != 1 or parsed.data[0] not in (0x00, 0x01):
            return self._nak()
        on = bool(parsed.data[0])
        if not output:
            self._scope_panel = on
            return self._ack()
        if on and not self._scope_carries(connection):
            return self._nak()  # recorded: the 19200 link refuses wave output
        was_on, self._scope_wave = self._scope_wave, on
        if on != was_on:  # a repeated enable leaves running sweeps alone
            for other in self._connections:
                if not on:
                    other.stop_scope()
                elif other is connection:
                    other.scope_start_pending = True
                else:
                    other.start_scope()
        return self._ack()

    def _scope_carries(self, connection: "_Connection") -> bool:
        """Whether the link's baud can carry wave output (unknown: it can)."""
        assert self._scope is not None
        minimum, baud = self._scope.min_baud, connection.baud
        return minimum is None or baud is None or baud >= minimum

    def _scope_streams_to(self, connection: "_Connection") -> bool:
        """Whether a due wave packet reaches this connection now."""
        return (
            self._scope_panel
            and self._scope_wave
            and not self._vanished
            and time.monotonic() >= self._silent_until
            and self._hears(connection)
            and self._scope_carries(connection)
        )

    def _ack(self) -> bytes:
        return build_civ_frame(CONTROLLER_ADDR, self._civ_addr, _CMD_ACK)

    def _nak(self) -> bytes:
        return build_civ_frame(CONTROLLER_ADDR, self._civ_addr, _CMD_NAK)


class _Connection:
    """One open session of the fake: an asyncio reader/writer pair.

    ``ordinal`` is the 1-based open counter value of this session, so the
    radio can tell a reconnect (ordinal >= 2) from the first open.
    """

    def __init__(
        self, fake: CivSerialFake, ordinal: int, baud: int | None = None
    ) -> None:
        self._fake = fake
        self.ordinal = ordinal
        self.baud = baud
        self.reader = asyncio.StreamReader()
        self.writer = _FakeSerialWriter(fake, self)
        self.error: OSError | None = None
        self.scope_start_pending = False
        self._last_due = 0.0  # loop time of the latest scheduled delayed reply
        self._last_due_ordered = 0.0  # ditto, [timing]-class replies only
        self._late: set[asyncio.TimerHandle] = set()  # replies still in flight
        # The [timing]-class replies still pending: what a zero-delay reply
        # must queue behind (a fault-injection reply never holds one back).
        self._ordered: set[asyncio.TimerHandle] = set()
        self._scope_handle: asyncio.TimerHandle | None = None
        self._scope_t0 = 0.0
        self._scope_sweep = 0
        self._scope_packet = 0

    def fail(self, error: OSError) -> None:
        """Kill this session: pending and later reads raise *error*."""
        self.error = error
        self.stop_scope()
        self.cancel_late()
        self.reader.set_exception(error)

    def deliver(self, data: bytes) -> None:
        if self.error is None and not self.writer.closed:
            self.reader.feed_data(data)

    def cancel_late(self) -> None:
        """Drop the replies still in flight: the port is closed or gone."""
        for handle in self._late:
            handle.cancel()
        self._late.clear()
        self._ordered.clear()

    def deliver_after(self, delay: float, data: bytes, *, fault: bool = False) -> None:
        """Deliver *data* ``delay`` seconds from now, in the order scheduled.

        A non-positive delay delivers synchronously only while no delayed
        ``[timing]``-class reply is pending; behind a pending one it is
        queued right after the pending replies, so an answer never
        overtakes an earlier request's reply. *fault* marks a reply
        delayed by the ``delay_identity_answer`` fault injection instead:
        it is not part of that order, and a non-positive-delay reply
        behind only fault replies is delivered synchronously, as before
        the ``[timing]`` keys existed. A positive delay is always
        scheduled after every earlier scheduled reply, a fault reply
        included.
        """
        if delay <= 0.0 and not self._ordered:
            self.deliver(data)
            return
        loop = self._fake._scheduler()
        if delay <= 0.0:
            # Queued behind a pending [timing]-class reply: right after
            # the latest of those, never behind a fault-injection reply.
            due = max(loop.time(), self._last_due_ordered + 1e-6)
        else:
            # The legacy rule: no scheduled reply lands before an earlier one.
            due = max(loop.time() + delay, self._last_due + 1e-6)
        self._last_due = max(self._last_due, due)
        baud = self.baud  # a reply sent at this rate is noise at another

        def deliver_late() -> None:
            self._late.discard(handle)
            self._ordered.discard(handle)
            if self.baud == baud:
                self.deliver(data)

        handle = loop.call_at(due, deliver_late)
        self._late.add(handle)
        if not fault:
            self._ordered.add(handle)
            self._last_due_ordered = due

    def start_scope(self) -> None:
        """Stream the fake's scope sweeps from the first one, after any reply due."""
        self.stop_scope()
        self.scope_start_pending = False
        if self.error is not None or self.writer.closed:
            return
        loop = asyncio.get_running_loop()
        self._scope_t0 = max(loop.time(), self._last_due) + 0.001
        self._scope_sweep = self._scope_packet = 0
        self._scope_handle = loop.call_at(self._scope_t0, self._scope_tick)

    def stop_scope(self) -> None:
        self.scope_start_pending = False
        if self._scope_handle is not None:
            self._scope_handle.cancel()
            self._scope_handle = None

    def _scope_tick(self) -> None:
        source = self._fake._scope
        assert source is not None
        self._scope_handle = None
        if self.error is not None or self.writer.closed:
            return
        sweeps = source.sweeps
        _, frame = sweeps[self._scope_sweep % len(sweeps)][self._scope_packet]
        if self._fake._scope_streams_to(self):
            self.deliver(frame)
        self._scope_packet += 1
        if self._scope_packet == len(sweeps[self._scope_sweep % len(sweeps)]):
            self._scope_packet = 0
            self._scope_sweep += 1
        offset = sweeps[self._scope_sweep % len(sweeps)][self._scope_packet][0]
        due = self._scope_t0 + self._scope_sweep / source.rate_hz + offset
        loop = asyncio.get_running_loop()
        self._scope_handle = loop.call_at(max(due, loop.time()), self._scope_tick)


class _FakeSerialPort:
    """The pyserial object behind a fake transport: baud and buffer reset."""

    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    @property
    def baudrate(self) -> int | None:
        return self._connection.baud

    @baudrate.setter
    def baudrate(self, value: int) -> None:
        self._connection.baud = int(value)

    def reset_input_buffer(self) -> None:
        pass


class _FakeTransport:
    def __init__(self, connection: _Connection) -> None:
        self.serial = _FakeSerialPort(connection)


class _FakeSerialWriter:
    """The write half of a fake serial connection.

    De-frames host bytes into the radio with a deliberately dumb splitter
    (ringfence rule 2: split on ``FE FE``, take to the first ``FD``), so a
    framing bug in the production ``SerialFrameCodec`` stays visible here.
    """

    def __init__(self, fake: CivSerialFake, connection: _Connection) -> None:
        self._fake = fake
        self._connection = connection
        self._buf = bytearray()
        self.closed = False
        # ``writer.transport.serial``: where production retunes a live port.
        self.transport: _FakeTransport | None = (
            _FakeTransport(connection) if fake.models_baud else None
        )

    def write(self, data: bytes) -> None:
        if self._connection.error is not None:
            raise self._connection.error
        if self.closed:
            raise RuntimeError("write to a closed fake serial writer")
        self._buf += bytes(data)
        while True:
            start = self._buf.find(b"\xfe\xfe")
            if start < 0:
                self._buf.clear()
                return
            end = self._buf.find(b"\xfd", start + 2)
            if end < 0:
                del self._buf[:start]
                return
            frame = bytes(self._buf[start : end + 1])
            del self._buf[: end + 1]
            self._fake._on_wire_frame(self._connection, frame)

    async def drain(self) -> None:
        if self._fake._write_drain_s:
            await asyncio.sleep(self._fake._write_drain_s)

    def close(self) -> None:
        self.closed = True
        self._connection.stop_scope()
        self._connection.cancel_late()
        if self._connection.error is None:
            self._connection.reader.feed_eof()

    async def wait_closed(self) -> None:
        pass


__all__ = ["CivSerialFake"]
