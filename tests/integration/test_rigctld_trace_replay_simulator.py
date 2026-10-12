"""Replay Hamlib client traces through RigctldServer into the recorded fake.

MOR-3262. The same ``tests/golden/<client>_dual_rx_session.txt`` traces
that ``tests/integration/test_rigctld_golden_replay.py`` replays against a
``SerialMockRadio`` are replayed here through the production stack down to
the wire: ``rigctld/server.py: RigctldServer`` → the real IC-7610
``runtime/radio.py: CoreRadio`` (``IcomRadio`` over loopback UDP, profile
``rigs/ic7610.toml``) → ``tests/testkit/ic7610_fakes.py: ic7610_lan_fake``
(``rigplane_testkit/icom/simulator.py: MockIcomRadio`` with the recorded
IC-7610 LAN answer table and opt-in stateful PTT). The trace parser
(``_parse_golden``), the reply readers
(``_read_line``, ``_drain_dump_state``) and the PTT re-read wait
(``wait_for_known_rf_state``) are imported from the existing replay test
and ``_ptt_reread_fixtures`` — no parallel harness. The server fixture
mirrors the golden-replay one (localhost server, one keepalive connection,
RF-state wait) with only the radio swapped, plus settle polls on a
throwaway session: ``CoreRadio`` starts with an empty state mirror and
store projection, so the fixture waits for the initial ``f``/``m`` reads
on both VFOs, and every ``M`` SET waits for the canonical store (not just
the session overlay) to report the set mode — like a polling client, each
bounded by a deadline.

Per trace two tests share one checked replay:

* ``test_<client>_prefix`` replays every step before the spec's prefix
  cutoff with all checks green.
* ``test_<client>_full`` replays the whole trace: the wsjtx row
  strict-xfails with the first divergent line in its reason
  (``pytest.mark.xfail``, ``strict=True``,
  ``raises=TraceDivergence``); the fldigi and js8call rows pass
  outright. Execution stops at the divergence, like the golden replay.

Checked replay rules, per step kind:

* every ``<`` reply must equal the server answer (same ``@dump_state``
  consumption: exactly 25 lines);
* after each ``>`` SET line (``F``, ``M``, ``S``, ``T`` with a VFO token)
  the radio side is asserted WITHOUT the production command builders:
  (i) for ``F``/``M`` the simulator's decoded state (selected/unselected
  frequency, mode byte, filter) settles to the set value, waited on with
  a deadline — ``CoreRadio`` frequency/mode writes are fire-and-forget;
  (ii) where the trace value matches a row of the manual-transcribed
  oracle ``tests/golden/wire_oracle/ic7610.toml`` (same control,
  receiver, value), a frame captured by the simulator's opt-in
  ``capture_civ_frames`` equals that row's frame from the command byte
  on. ``S`` gets (ii) only: the simulator keeps no decoded split state.
  ``T`` gets (ii) first, then check (i) compares a real decoded
  readback: the data byte of the simulator's own ``1C 00`` GET reply
  must equal the value the ``T`` line set. The simulator has no PTT
  state accessor, so the GET reply — built by its own
  ``simulator.py: MockIcomRadio._dispatch_civ`` PTT branch — is the
  readback. PTT writes are fire-and-forget (``radio.py: set_ptt``), so
  check (i) polls the readback with a deadline; with the stateful
  build the SET flips the simulated state and the poll converges
  (``T … 1`` reads back ``0x01`` until ``T … 0`` flips it back);
* after each ``>`` READ line (``f``, ``m``, ``t``) the reply equals the
  simulator's state for that control (frequency per receiver; mode name
  and passband from the simulator's mode byte and filter; PTT readback,
  which the simulator constantly reports OFF).

Error contract (so a strict xfail can never hide a harness problem):
harness waits (``_wait_until``, ``_poll_until_reply``) raise
``TimeoutError`` and fixture/parse/oracle setup mistakes raise
``RuntimeError`` — never ``AssertionError``. A SET's landed check
(``F`` as for ``T``) polls the simulated radio with a deadline but
never raises ``TimeoutError`` itself; its final compare is the
divergence assertion. The ONLY ``AssertionError``
a full-trace replay raises at its divergent line is re-raised by the
test as ``TraceDivergence`` (matched on the trace line number and the
``>`` line text); any other ``AssertionError`` becomes ``RuntimeError``
and FAILS instead of xfailing.

Remaining divergence (traces never edited):

* wsjtx line 62 ``F VFOB 14076000`` answers ``RPRT 0`` but never
  lands on the SUB receiver: the recorded table ACKs the CI-V ``0x07``
  select (so line 60 passes and the SET is accepted), but
  ``simulator.py: MockIcomRadio`` keeps no per-receiver frequency
  behind a ``0x07`` select while its native ``0x05`` handler writes
  MAIN, so the landed check diverges. The remainder of the trace
  (from line 62 on) is the strict xfail (MOR-3310).
* fldigi and js8call pass whole: their single PTT cycle (``T … 1`` /
  ``T … 0``) converges through the stateful PTT hook, and every ``t``
  read in all three traces happens while PTT is off.

All waits carry explicit deadlines; no bare sleeps. The simulator's
unselected receiver is preset to 14074000 Hz: the wsjtx trace documents
both receivers starting there, while the simulator's factory default is
7074000 Hz.
"""

from __future__ import annotations

import asyncio
import time
import tomllib
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from rigplane.radio import IcomRadio
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer
from rigplane_testkit.icom import MockIcomRadio

from _perf_helpers import fast_connect
from _ptt_reread_fixtures import wait_for_known_rf_state
from testkit.ic7610_fakes import ic7610_lan_fake
from test_rigctld_golden_replay import (
    _addr,
    _drain_dump_state,
    _parse_golden,
    _read_line,
    _send,
    _Step,
)

pytestmark = [pytest.mark.integration, pytest.mark.mock_integration]

GOLDEN_DIR = Path(__file__).parent.parent / "golden"
ORACLE_PATH = GOLDEN_DIR / "wire_oracle" / "ic7610.toml"

_CONNECT_TIMEOUT = 60.0
_RF_KNOWN_TIMEOUT = 15.0
_READ_TIMEOUT = 5.0
_SETTLE_TIMEOUT = 5.0
_FRAME_TIMEOUT = 5.0
_INITIAL_READ_TIMEOUT = 20.0
_POST_SET_READ_TIMEOUT = 15.0

_VFO_RECEIVER = {"VFOA": 0, "VFOB": 1}

# Test-local transcriptions of values the traces use. The filter map is
# the inverse of ``handler.py: _FILTER_TO_PASSBAND`` ({1: 3000, 2: 2400,
# 3: 1800}); the mode byte is what the simulator stores for USB, which the
# passing golden replay round-trips. Anything else fails loudly instead of
# silently skipping.
_MODE_BYTE = {"USB": 0x01}
_FILTER_OF_PASSBAND = {3000: 1, 2400: 2}

# The ``t`` READ check below expects OFF: every trace reads ``t`` only
# while PTT is off, and the stateful simulator answers the ``1C 00`` PTT
# GET with its current state (``simulator.py:
# MockIcomRadio._dispatch_civ``, PTT branch).
_SIM_PTT_READBACK = "0"


class TraceDivergence(AssertionError):
    """The replay diverged exactly at the trace's known-unsatisfiable line.

    A full-trace test strict-xfails only on this type, matched on the
    trace line number and the ``>`` line text. Every other failure —
    harness ``TimeoutError``/``RuntimeError`` or an ``AssertionError``
    from any other line — propagates as a different type and FAILS.
    """


@dataclass(frozen=True, slots=True)
class _TraceSpec:
    """One golden trace, its prefix cutoff and its xfail anchor line.

    ``diverge_line`` / ``diverge_send`` locate the full-trace test's
    xfail: the first unsatisfiable source line while the trace still
    diverges, the formerly-divergent line once the whole trace passes.
    The prefix test covers a fixed cutoff (``prefix_before_line``) that
    never widens when the divergence moves.
    """

    name: str
    filename: str
    diverge_line: int
    diverge_send: str  # the `>` line text at diverge_line, for xfail matching
    prefix_before_line: int  # the prefix test replays steps below this line
    prefix_steps: int  # steps with line_no < prefix_before_line (parser-verified)


@dataclass(frozen=True, slots=True)
class _SetOp:
    """A parsed ``>`` SET line with a VFO token."""

    cmd: str  # "F" | "M" | "S" | "T"
    receiver: int  # 0 = VFOA/MAIN, 1 = VFOB/SUB
    args: tuple[str, ...]
    line_no: int


@dataclass(frozen=True, slots=True)
class _ReadOp:
    """A parsed ``>`` READ line with a VFO token."""

    cmd: str  # "f" | "m" | "t"
    receiver: int
    line_no: int


def _parse_send(payload: str, line_no: int) -> _SetOp | _ReadOp | None:
    """Parse a client ``>`` line into a SET, READ, or meta (``\\...``) op."""
    if payload.startswith("\\"):
        return None
    tokens = payload.split()
    cmd, vfo = tokens[0], tokens[1]
    if vfo not in _VFO_RECEIVER:
        raise RuntimeError(f"line {line_no}: unknown VFO token {vfo!r}")
    receiver = _VFO_RECEIVER[vfo]
    if cmd in ("F", "M", "S", "T"):
        return _SetOp(cmd, receiver, tuple(tokens[2:]), line_no)
    if cmd in ("f", "m", "t"):
        return _ReadOp(cmd, receiver, line_no)
    raise RuntimeError(f"line {line_no}: unsupported client line {payload!r}")


# ---------------------------------------------------------------------------
# Stack fixture (mirrors the golden-replay fixture; radio is CoreRadio)
# ---------------------------------------------------------------------------


async def _stack() -> AsyncGenerator[tuple[RigctldServer, MockIcomRadio], None]:
    """Live RigctldServer on a CoreRadio connected to the LAN simulator."""
    sim = ic7610_lan_fake(capture_civ_frames=True, stateful_ptt=True)
    await sim.start()
    # Trace precondition (wsjtx §4): both receivers start at 14074000 Hz.
    sim._unselected_frequency = 14_074_000  # noqa: SLF001
    radio = IcomRadio(
        host="127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        model="IC-7610",
    )
    with fast_connect():
        await asyncio.wait_for(radio.connect(), timeout=_CONNECT_TIMEOUT)
    if not radio.connected:
        raise RuntimeError("CoreRadio failed to connect to the LAN simulator")
    cfg = RigctldConfig(
        host="127.0.0.1",
        port=0,
        # 3, not the golden fixture's 2: keepalive + replay + one
        # throwaway settle connection (post-M convergence polls).
        max_clients=3,
        client_timeout=5.0,
        command_timeout=2.0,
    )
    server = RigctldServer(radio, cfg)
    await server.start()
    host, port = _addr(server)
    _keepalive_reader, keepalive_writer = await asyncio.open_connection(host, port)
    # `wait_for_known_rf_state` raises AssertionError (via `_poll_rf_state`)
    # when the RF state never leaves UNKNOWN; a setup failure must not be
    # absorbable by a full-trace `raises=TraceDivergence` xfail, so wrap it.
    # `asyncio.wait_for` already raises builtin TimeoutError past its own
    # deadline, which propagates untouched.
    try:
        await asyncio.wait_for(
            wait_for_known_rf_state(server), timeout=_RF_KNOWN_TIMEOUT
        )
    except AssertionError as exc:
        raise RuntimeError(f"RF state never left UNKNOWN: {exc}") from exc
    # The trace preconditions both receivers at 14074000 Hz USB/FIL1.
    # ``CoreRadio`` starts with an empty state mirror and store projection
    # (the fixture skips the initial fetch like the other simulator tests),
    # and ``handler.py: RigctldHandler._cmd_get_mode`` answers from the
    # store projection only once background acquisition populates
    # mode/filter_num/data_mode — before that the first ``m`` reads
    # passband 0 (``handler.py: _filter_to_passband`` maps unknown to 0),
    # while ``f VFOB`` reads the SUB mirror default 0. Poll like a real
    # client until the stack reports the precondition.
    for send_line, want in (
        ("f VFOA", ["14074000"]),
        ("m VFOA", ["USB", "3000"]),
        ("f VFOB", ["14074000"]),
        ("m VFOB", ["USB", "3000"]),
    ):
        await _poll_until_reply(
            host, port, send_line, want, timeout=_INITIAL_READ_TIMEOUT
        )
    try:
        yield server, sim
    finally:
        keepalive_writer.close()
        try:
            await keepalive_writer.wait_closed()
        except Exception:
            pass
        await server.stop()
        await radio.disconnect()
        await sim.stop()


async def _poll_until_reply(
    host: str, port: int, send_line: str, want: list[str], *, timeout: float
) -> None:
    """Poll one rigctld READ on a throwaway connection until it answers want.

    The throwaway connection is a different session, so session-scoped
    pending overlays never apply: convergence here proves the canonical
    store/mirror reports the value, the same condition a polling client
    waits for. Bounded by ``timeout``.
    """
    reader, writer = await asyncio.open_connection(host, port)
    try:
        deadline = time.monotonic() + timeout
        while True:
            writer.write((send_line + "\n").encode("ascii"))
            await writer.drain()
            got = [await _read_line(reader, timeout=_READ_TIMEOUT) for _ in want]
            if got == want:
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"`{send_line}` never settled to {want!r} within "
                    f"{timeout}s (got {got!r})"
                )
            await asyncio.sleep(0.2)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


@pytest.fixture
async def stack() -> AsyncGenerator[tuple[RigctldServer, MockIcomRadio], None]:
    """Fresh simulator + CoreRadio + rigctld server per test."""
    async for item in _stack():
        yield item


# ---------------------------------------------------------------------------
# Oracle rows (manual-transcribed; never generated from code)
# ---------------------------------------------------------------------------


def _oracle_rows() -> list[dict]:
    with open(ORACLE_PATH, "rb") as handle:
        return list(tomllib.load(handle)["row"])


def _match_oracle_row(op: _SetOp, rows: list[dict]) -> dict | None:
    """Return the oracle row with the same control, receiver and value."""
    control = {"F": "operating_freq", "M": "operating_mode", "S": "split", "T": "ptt"}[
        op.cmd
    ]
    want_receiver = "main" if op.receiver == 0 else "sub"
    for row in rows:
        if row["control"] != control or row.get("direction") != "set":
            continue
        if row.get("receiver", "none") not in ("none", want_receiver):
            continue
        if _row_value_matches(row["value"], op):
            return row
    return None


def _row_value_matches(row_value: str, op: _SetOp) -> bool:
    """Compare an oracle row value with the trace SET value."""
    if op.cmd == "F":
        # Row values read like "14.074 MHz".
        number, unit = row_value.split()
        if unit != "MHz":
            raise RuntimeError(f"unexpected oracle frequency unit {unit!r}")
        return int(op.args[0]) == int(float(number) * 1_000_000)
    if op.cmd == "M":
        # Row values read like "USB FIL1".
        mode, fil = row_value.split()
        try:
            want_filter = _FILTER_OF_PASSBAND[int(op.args[1])]
        except (KeyError, ValueError):
            raise RuntimeError(
                f"line {op.line_no}: no filter transcription for "
                f"passband {op.args[1]!r}"
            ) from None
        return op.args[0] == mode and want_filter == int(fil[3:])
    if op.cmd in ("S", "T"):
        try:
            return {"1": "on", "0": "off"}[op.args[0]] == row_value
        except KeyError:
            raise RuntimeError(
                f"line {op.line_no}: unexpected on/off value {op.args[0]!r}"
            ) from None
    raise RuntimeError(
        f"line {op.line_no}: no value rule for {op.cmd}"
    )  # pragma: no cover


# ---------------------------------------------------------------------------
# Radio-side checks (no production command builders used)
# ---------------------------------------------------------------------------


async def _wait_until(
    description: str, line_no: int, predicate: Callable[[], bool], *, timeout: float
) -> None:
    """Poll ``predicate`` until true, else raise TimeoutError with context.

    Deliberately NOT AssertionError: a stalled wait is a harness/timing
    problem, and a full-trace strict xfail must never absorb it.
    """
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"line {line_no}: {description} never landed within {timeout}s"
            )
        await asyncio.sleep(0.05)


def _sim_freq(sim: MockIcomRadio, receiver: int) -> int:
    if receiver == 0:
        return sim._frequency  # noqa: SLF001
    return sim._unselected_frequency  # noqa: SLF001


def _sim_mode_filter(sim: MockIcomRadio, receiver: int) -> tuple[int, int]:
    if receiver == 0:
        return sim._mode, sim._filter  # noqa: SLF001
    return sim._unselected_mode, sim._unselected_filter  # noqa: SLF001


def _sim_ptt_readback(sim: MockIcomRadio) -> int:
    """The data byte of the simulator's own ``1C 00`` GET reply.

    The simulator keeps no decoded PTT state attribute, so the GET reply
    built by its own ``_dispatch_civ`` PTT branch is the readback: with
    the stateful build it answers the current simulated state (``0x01``
    after ``T … 1``, back to ``0x00`` after ``T … 0``). Uses no
    production command builder.
    """
    reply = sim._dispatch_civ(0x1C, bytes((0x00,)), 0xE0)  # noqa: SLF001
    if reply is None or len(reply) != 8 or reply[4] != 0x1C or reply[5] != 0x00:
        raise RuntimeError(f"simulator 1C 00 GET gave no decodable reply: {reply!r}")
    return reply[6]


async def _check_set_landed(
    sim: MockIcomRadio,
    op: _SetOp,
    rows: list[dict],
    mark: int,
    host: str,
    port: int,
) -> None:
    """Check (i) simulator state and (ii) captured frame for one SET line."""
    if op.cmd == "F":
        want_hz = int(op.args[0])
        vfo = "VFOA" if op.receiver == 0 else "VFOB"
        # Frequency writes are fire-and-forget, so poll the simulator's
        # decoded frequency with a deadline; the final compare is the
        # divergence assertion — a SET that never lands is a divergence
        # of the simulated radio, not a harness stall. This poll never
        # raises TimeoutError itself, so a harness timeout elsewhere
        # still FAILS instead of xfailing.
        deadline = time.monotonic() + _SETTLE_TIMEOUT
        while _sim_freq(sim, op.receiver) != want_hz:
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.05)
        got_hz = _sim_freq(sim, op.receiver)
        assert got_hz == want_hz, (
            f"line {op.line_no}: `F {vfo} {op.args[0]}` set {want_hz} but "
            f"the simulator's {'MAIN' if op.receiver == 0 else 'SUB'} "
            f"frequency is {got_hz}"
        )
    elif op.cmd == "M":
        try:
            want_mode = _MODE_BYTE[op.args[0]]
            want_filter = _FILTER_OF_PASSBAND[int(op.args[1])]
        except (KeyError, ValueError):
            raise RuntimeError(
                f"line {op.line_no}: no transcription for mode {op.args!r}"
            ) from None
        await _wait_until(
            f"simulator mode {op.args[0]}/{op.args[1]}",
            op.line_no,
            lambda: _sim_mode_filter(sim, op.receiver) == (want_mode, want_filter),
            timeout=_SETTLE_TIMEOUT,
        )
        # The trace's next `m` read must report the set value, but
        # ``_cmd_get_mode`` answers from the store projection only when
        # mode/filter_num/data_mode are ALL observed; before that it falls
        # back to the stale state mirror. The session overlay masks this on
        # the replay connection, so poll a throwaway session (no overlays)
        # until the canonical store reports the set value — like a polling
        # client. Throws TimeoutError past the deadline.
        vfo = "VFOA" if op.receiver == 0 else "VFOB"
        await _poll_until_reply(
            host,
            port,
            f"m {vfo}",
            [op.args[0], op.args[1]],
            timeout=_POST_SET_READ_TIMEOUT,
        )
    # ``T`` check (i) runs after the frame wait below; ``S`` gets check
    # (ii) only (the simulator keeps no decoded split state).

    row = _match_oracle_row(op, rows)
    if row is not None:
        want = bytes.fromhex(row["frame"])[4:]

        def _frame_seen() -> bool:
            return any(frame[4:] == want for frame in sim.civ_frames_received[mark:])

        await _wait_until(
            f"captured frame {want.hex().upper()}",
            op.line_no,
            _frame_seen,
            timeout=_FRAME_TIMEOUT,
        )

    if op.cmd == "T":
        vfo = "VFOA" if op.receiver == 0 else "VFOB"
        try:
            want_ptt = {"1": 0x01, "0": 0x00}[op.args[0]]
        except KeyError:
            raise RuntimeError(
                f"line {op.line_no}: unexpected on/off value {op.args[0]!r}"
            ) from None
        # PTT writes are fire-and-forget, so poll the simulator's own
        # ``1C 00`` GET readback with a deadline; the final compare is the
        # divergence assertion — with the stateful build it converges once
        # the SET lands. This poll never raises TimeoutError itself, so a
        # harness timeout elsewhere still FAILS instead of xfailing.
        deadline = time.monotonic() + _SETTLE_TIMEOUT
        while _sim_ptt_readback(sim) != want_ptt:
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.05)
        got_ptt = _sim_ptt_readback(sim)
        assert got_ptt == want_ptt, (
            f"line {op.line_no}: `T {vfo} {op.args[0]}` set "
            f"{'ON' if want_ptt else 'OFF'} ({want_ptt:#04x}) but the "
            f"simulator's own 1C 00 GET readback is "
            f"{'ON' if got_ptt else 'OFF'} ({got_ptt:#04x})"
        )


def _check_read_matches_sim(
    sim: MockIcomRadio, op: _ReadOp, replies: list[str]
) -> None:
    """The reply to a READ line equals the simulator's state."""
    if op.cmd == "f":
        assert len(replies) == 1, f"line {op.line_no}: {replies!r}"
        assert int(replies[0]) == _sim_freq(sim, op.receiver), (
            f"line {op.line_no}: reply {replies[0]!r} != "
            f"simulator {_sim_freq(sim, op.receiver)}"
        )
    elif op.cmd == "m":
        assert len(replies) == 2, f"line {op.line_no}: {replies!r}"
        mode_byte, filt = _sim_mode_filter(sim, op.receiver)
        names = {byte: name for name, byte in _MODE_BYTE.items()}
        passbands = {n: pb for pb, n in _FILTER_OF_PASSBAND.items()}
        if mode_byte not in names or filt not in passbands:
            raise AssertionError(
                f"line {op.line_no}: no transcription for simulator mode "
                f"{mode_byte:#04x}/filter {filt}"
            )
        assert replies[0] == names[mode_byte], (
            f"line {op.line_no}: reply {replies[0]!r} != simulator mode "
            f"{mode_byte:#04x}"
        )
        assert replies[1] == str(passbands[filt]), (
            f"line {op.line_no}: reply {replies[1]!r} != simulator filter {filt}"
        )
    elif op.cmd == "t":
        assert len(replies) == 1, f"line {op.line_no}: {replies!r}"
        assert replies[0] == _SIM_PTT_READBACK, (
            f"line {op.line_no}: reply {replies[0]!r} != simulator PTT readback OFF"
        )


# ---------------------------------------------------------------------------
# Checked replay
# ---------------------------------------------------------------------------


async def _replay_checked(
    server: RigctldServer,
    sim: MockIcomRadio,
    steps: list[_Step],
    rows: list[dict],
) -> tuple[int, int]:
    """Drive steps against the server, asserting replies and radio side.

    Returns the number of steps replayed and the last step's source
    line number, so callers can assert the whole script was consumed.
    """
    host, port = _addr(server)
    reader, writer = await asyncio.open_connection(host, port)
    replayed = 0
    try:
        i = 0
        while i < len(steps):
            step = steps[i]
            if step.kind == "send":
                op = _parse_send(step.payload, step.line_no)
                mark = len(sim.civ_frames_received)
                await _send(writer, step.payload)
                replies: list[str] = []
                j = i + 1
                while j < len(steps) and steps[j].kind == "expect":
                    actual = await _read_line(reader, timeout=_READ_TIMEOUT)
                    # Names the `>` line number and text: a divergence
                    # here is the line-tied xfail signal for e.g. wsjtx
                    # line 62 `F VFOB 14076000` (landed check on SUB).
                    assert actual == steps[j].payload, (
                        f"line {step.line_no}: `{step.payload}` diverged "
                        f"at reply line {steps[j].line_no}: expected "
                        f"{steps[j].payload!r}, got {actual!r}"
                    )
                    replies.append(actual)
                    j += 1
                if isinstance(op, _SetOp):
                    await _check_set_landed(sim, op, rows, mark, host, port)
                elif isinstance(op, _ReadOp):
                    _check_read_matches_sim(sim, op, replies)
                replayed += j - i
                i = j
            elif step.kind == "dump_state":
                await _drain_dump_state(reader, timeout=_READ_TIMEOUT)
                replayed += 1
                i += 1
            else:  # pragma: no cover — guarded by _parse_golden
                raise RuntimeError(f"unknown step kind {step.kind!r}")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
    last_line = steps[-1].line_no if steps else 0
    return replayed, last_line


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


_WSJTX_XFAIL_REASON = (
    "MOR-3310: line 62 `F VFOB 14076000` never lands on the SUB "
    "receiver: the recorded table ACKs the 07 D1 select, but "
    "MockIcomRadio keeps no per-receiver frequency behind it and the "
    "native 05 handler writes MAIN"
)

_SPECS = [
    _TraceSpec(
        name="wsjtx",
        filename="wsjtx_dual_rx_session.txt",
        diverge_line=62,
        diverge_send="F VFOB 14076000",
        prefix_before_line=60,
        prefix_steps=20,
    ),
    _TraceSpec(
        name="fldigi",
        filename="fldigi_dual_rx_session.txt",
        diverge_line=46,
        diverge_send="T VFOA 1",
        prefix_before_line=46,
        prefix_steps=18,
    ),
    _TraceSpec(
        name="js8call",
        filename="js8call_dual_rx_session.txt",
        diverge_line=37,
        diverge_send="T VFOA 1",
        prefix_before_line=37,
        prefix_steps=15,
    ),
]


def _is_intended_divergence(spec: _TraceSpec, message: str) -> bool:
    """True when a replay failure names exactly the spec's divergent line."""
    return f"line {spec.diverge_line}:" in message and spec.diverge_send in message


@pytest.mark.parametrize("spec", _SPECS, ids=[s.name for s in _SPECS])
async def test_prefix_passes(
    stack: tuple[RigctldServer, MockIcomRadio], spec: _TraceSpec
) -> None:
    """Steps before the spec's prefix cutoff pass with all checks."""
    server, sim = stack
    steps = [
        s
        for s in _parse_golden(GOLDEN_DIR / spec.filename)
        if s.line_no < spec.prefix_before_line
    ]
    if not steps:
        raise RuntimeError(f"{spec.filename}: empty prefix")
    replayed, last_line = await _replay_checked(server, sim, steps, _oracle_rows())
    assert replayed == spec.prefix_steps == len(steps)
    assert last_line == steps[-1].line_no < spec.prefix_before_line


@pytest.mark.parametrize(
    "spec",
    [
        pytest.param(
            _SPECS[0],
            marks=pytest.mark.xfail(
                strict=True, raises=TraceDivergence, reason=_WSJTX_XFAIL_REASON
            ),
        ),
        _SPECS[1],
        _SPECS[2],
    ],
    ids=[s.name for s in _SPECS],
)
async def test_full_trace(
    stack: tuple[RigctldServer, MockIcomRadio], spec: _TraceSpec
) -> None:
    """Whole-trace replay; the wsjtx row xfails exactly at the known
    divergent line.

    Only the divergent line maps to ``TraceDivergence``. A harness
    ``TimeoutError``/``RuntimeError``, or an ``AssertionError`` from any
    other line, becomes ``RuntimeError`` and FAILS. (``raises=`` with a
    message match via ``pytest.RaisesExc`` is not used: the repo's
    minimum pytest is 8.0 per pyproject, which predates ``RaisesExc``.)
    """
    server, sim = stack
    steps = _parse_golden(GOLDEN_DIR / spec.filename)
    divergent = [s for s in steps if s.line_no == spec.diverge_line]
    assert len(divergent) == 1 and divergent[0].payload == spec.diverge_send
    try:
        await _replay_checked(server, sim, steps, _oracle_rows())
    except TraceDivergence:
        raise
    except AssertionError as exc:
        message = str(exc)
        if _is_intended_divergence(spec, message):
            raise TraceDivergence(
                f"diverged at trace line {spec.diverge_line} "
                f"`{spec.diverge_send}`: {exc}"
            ) from exc
        raise RuntimeError(
            f"full-trace replay failed off the divergent line: {exc}"
        ) from exc
