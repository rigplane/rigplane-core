"""Wire-level golden-replay tests for rigctld vfo_opt sessions.

Variant A 1/5 of epic #1341 — closes #1342. **Load-bearing assertion**
of the entire epic: every A2-A5 PR must keep the chk_vfo=0 lines green;
A5 (#1346) flipped the remaining xfailed traces to xpass.

How it works
------------
Each ``tests/golden/<client>_dual_rx_session.txt`` file documents a
faithful Hamlib 4.7.1 NET rigctl wire trace under ``chk_vfo=1`` for one
client (WSJT-X 3.1, fldigi, JS8Call). The replay runner:

1. Spins up a real :class:`RigctldServer` bound to localhost:0 with a
   :class:`SerialMockRadio` configured as IC-7610 (dual-RX, main_sub
   VFO scheme).
2. Reads the golden line-by-line. ``> <wire>`` is sent to the server;
   ``< <wire>`` is the expected next response line; ``< @dump_state``
   means "consume the multi-line dump_state response" (deferred to A5
   where the snapshot test owns its content).
3. Compares actual vs. expected, logging the first divergence point.

History
-------
- A1 (#1342) added the scaffolding with ``@pytest.mark.xfail`` — every
  trace failed at the first ``< 1`` because Variant B (PR #1340) made
  ``chk_vfo`` return ``"0"`` unconditionally.
- A2 (#1343) parser accepts the leading VFO token.
- A3 (#1344) per-VFO routing for freq/mode/PTT.
- A4 (#1345) per-VFO routing for split/RIT/level/func.
- A5 (#1346) re-enabled ``chk_vfo='1'`` for dual-RX, fixed
  ``SerialMockRadio`` to mirror per-VFO state into ``RadioState``,
  and removed the xfail markers — replays now pass end-to-end.

References
----------
- Issue #1319 — the bug the scaffolding catches.
- Epic #1341 — five-PR plan; A1 is sub-issue 1/5 (#1342).
- Hamlib spec — https://hamlib.sourceforge.net/manuals/4.5.5/rigctl.1.html
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path

import pytest

from icom_lan.backends.icom7610.drivers.serial_stub import SerialMockRadio
from icom_lan.rigctld.contract import RigctldConfig
from icom_lan.rigctld.server import RigctldServer

pytestmark = [pytest.mark.integration, pytest.mark.mock_integration]

GOLDEN_DIR = Path(__file__).parent.parent / "golden"


# ---------------------------------------------------------------------------
# Replay format
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Step:
    """One step in a golden-replay script."""

    kind: str  # "send" | "expect" | "dump_state"
    payload: str  # wire line for send/expect; "" for dump_state
    line_no: int  # source line number for diagnostics


def _parse_golden(path: Path) -> list[_Step]:
    """Parse a ``tests/golden/*_session.txt`` file into a step list.

    Format:
        ``> <wire>``        client → server (send)
        ``< <wire>``        server → client (expect single line)
        ``< @dump_state``   server → client (consume multi-line)
        ``# <text>``        comment, ignored
        blank lines         ignored
    """
    steps: list[_Step] = []
    for n, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.rstrip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("> "):
            steps.append(_Step("send", line[2:], n))
        elif line == ">":
            # bare prompt — skip
            continue
        elif line.startswith("< "):
            payload = line[2:]
            if payload == "@dump_state":
                steps.append(_Step("dump_state", "", n))
            else:
                steps.append(_Step("expect", payload, n))
        else:
            raise ValueError(f"{path}:{n}: unrecognized line {line!r}")
    return steps


# ---------------------------------------------------------------------------
# Server / client helpers
# ---------------------------------------------------------------------------


def _addr(server: RigctldServer) -> tuple[str, int]:
    assert server._server is not None
    host, port = server._server.sockets[0].getsockname()[:2]
    return str(host), int(port)


@pytest.fixture
async def rigctld_dual_rx_server() -> AsyncGenerator[RigctldServer, None]:
    """Real RigctldServer bound to localhost:0 with an IC-7610 mock radio.

    Uses SerialMockRadio(model="IC-7610") which exposes the dual-RX
    profile (receiver_count=2, vfo_scheme="main_sub"). The handler's
    chk_vfo branch reads ``radio.profile.receiver_count`` to decide
    whether to advertise vfo_opt — but Variant B currently shorts that
    to ``"0"`` unconditionally, which is why every replay xfails today.
    """
    radio = SerialMockRadio(model="IC-7610")
    await radio.connect()
    cfg = RigctldConfig(
        host="127.0.0.1",
        port=0,
        max_clients=2,
        client_timeout=5.0,
        command_timeout=2.0,
    )
    srv = RigctldServer(radio, cfg)
    await srv.start()
    try:
        yield srv
    finally:
        await srv.stop()


async def _send(writer: asyncio.StreamWriter, line: str) -> None:
    writer.write((line + "\n").encode("ascii"))
    await writer.drain()


async def _read_line(reader: asyncio.StreamReader, *, timeout: float = 1.0) -> str:
    data = await asyncio.wait_for(reader.readline(), timeout=timeout)
    return data.decode("ascii").rstrip("\n")


async def _drain_dump_state(
    reader: asyncio.StreamReader, *, timeout: float = 1.0
) -> int:
    """Consume the multi-line dump_state response.

    The IC-7610 dump_state emits 25 lines (see ``_IC7610_DUMP_STATE``
    in ``handler.py``; pinned by the snapshot test). Read exactly that
    many to keep the stream cursor aligned for the next ``> ...`` send.

    Returns the number of lines drained.
    """
    n = 0
    # _IC7610_DUMP_STATE has 25 entries today; A5 will extend with VFO
    # blocks (vfo_list, vfo_ops, status_flags, targetable_vfo). When A5
    # lands the snapshot test owns the new line count and this constant
    # becomes a derived value.
    expected_lines = 25
    while n < expected_lines:
        await _read_line(reader, timeout=timeout)
        n += 1
    return n


async def _replay(server: RigctldServer, steps: list[_Step]) -> None:
    """Drive one golden script against a live rigctld server.

    Raises AssertionError on the first divergence with file-line
    context. After Variant A 5/5 lands, all assertions hold; until
    then the wrapping @pytest.mark.xfail catches the failure.
    """
    host, port = _addr(server)
    reader, writer = await asyncio.open_connection(host, port)
    try:
        i = 0
        while i < len(steps):
            step = steps[i]
            if step.kind == "send":
                await _send(writer, step.payload)
                i += 1
            elif step.kind == "expect":
                actual = await _read_line(reader)
                assert actual == step.payload, (
                    f"line {step.line_no}: expected {step.payload!r}, got {actual!r}"
                )
                i += 1
            elif step.kind == "dump_state":
                await _drain_dump_state(reader)
                i += 1
            else:  # pragma: no cover — guarded by _parse_golden
                raise AssertionError(f"unknown step kind {step.kind!r}")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGoldenReplayDualRx:
    """Replay each canonical client's chk_vfo=1 wire trace.

    All three replays now pass end-to-end — Variant A 5/5 (#1346)
    re-enabled ``chk_vfo='1'`` and the SerialMockRadio mirrors per-VFO
    state into ``RadioState``, so the dual-RX path works without xfail.
    Earlier slices: A2 (#1343) parser support, A3 (#1344) per-VFO
    freq/mode/PTT routing, A4 (#1345) per-VFO split/RIT/level/func.
    """

    async def test_wsjtx_dual_rx_golden_replay(
        self, rigctld_dual_rx_server: RigctldServer
    ) -> None:
        steps = _parse_golden(GOLDEN_DIR / "wsjtx_dual_rx_session.txt")
        await _replay(rigctld_dual_rx_server, steps)

    async def test_fldigi_dual_rx_golden_replay(
        self, rigctld_dual_rx_server: RigctldServer
    ) -> None:
        steps = _parse_golden(GOLDEN_DIR / "fldigi_dual_rx_session.txt")
        await _replay(rigctld_dual_rx_server, steps)

    async def test_js8call_dual_rx_golden_replay(
        self, rigctld_dual_rx_server: RigctldServer
    ) -> None:
        steps = _parse_golden(GOLDEN_DIR / "js8call_dual_rx_session.txt")
        await _replay(rigctld_dual_rx_server, steps)


class TestGoldenParser:
    """Smoke tests for the golden-replay parser itself.

    Pure-Python checks — do not require a live server. These ensure
    the .txt format stays well-formed; they pass on `main` today.
    """

    def test_parses_wsjtx_golden(self) -> None:
        steps = _parse_golden(GOLDEN_DIR / "wsjtx_dual_rx_session.txt")
        # Step 1 should be the chk_vfo send.
        assert steps[0].kind == "send"
        assert steps[0].payload == "\\chk_vfo"
        # Step 2 should be the chk_vfo response expectation.
        assert steps[1].kind == "expect"
        assert steps[1].payload == "1"
        # The dump_state marker must appear exactly once.
        ds_steps = [s for s in steps if s.kind == "dump_state"]
        assert len(ds_steps) == 1

    def test_parses_fldigi_golden(self) -> None:
        steps = _parse_golden(GOLDEN_DIR / "fldigi_dual_rx_session.txt")
        assert steps[0].kind == "send"
        assert steps[0].payload == "\\chk_vfo"

    def test_parses_js8call_golden(self) -> None:
        steps = _parse_golden(GOLDEN_DIR / "js8call_dual_rx_session.txt")
        assert steps[0].kind == "send"
        assert steps[0].payload == "\\chk_vfo"
