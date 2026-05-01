"""Integration tests: WSJT-X / fldigi / JS8Call flows through rigctld.

Part of issue #723 — exercise real-world rigctld command sequences against
the in-process rigctld server + a recording mock radio, and assert the
sequence of Radio-level calls the server issues as a result.

Why not raw CI-V bytes?
    The handler's job is to translate rigctld commands to Radio protocol
    calls (``set_freq``, ``set_mode``, ``set_split``, ``set_vfo``,
    ``set_ptt``). CI-V wire encoding of those calls is already covered by
    ``tests/test_commands.py`` and contract tests. Duplicating the full
    LAN UDP stack here would add auth/keep-alive timing noise without
    testing anything new.

What we assert
    * Exact order and arguments of recorded Radio-level calls.
    * Split enable/disable routing for the IC-7610 dual-RX profile.
    * fldigi-style mode/freq change on both IC-7610 (dual-RX) and
      IC-7300 (single-RX) profiles.
    * JS8Call heartbeat GETs returning the current state.
    * Backwards-compatible fallback for unknown VFO names.

Tests are marked ``integration`` + ``mock_integration`` so they skip in
default CI runs but execute via the integration marker and do not require
real hardware.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest

from icom_lan.backends.icom7610.drivers.serial_stub import SerialMockRadio
from icom_lan.rigctld.contract import RigctldConfig
from icom_lan.rigctld.server import RigctldServer

pytestmark = [pytest.mark.integration, pytest.mark.mock_integration]

# ---------------------------------------------------------------------------
# Recording mock radio
# ---------------------------------------------------------------------------


class RecordingMockRadio(SerialMockRadio):
    """SerialMockRadio subclass that records every Radio-level call.

    Each recorded entry is ``(method_name, args_tuple)``. The tests assert
    the exact sequence of calls emitted by the rigctld handler.

    Adds ``set_split`` which ``SerialMockRadio`` does not implement;
    the rigctld handler calls it via ``getattr(radio, 'set_split', None)``
    so without this override the split-VFO paths would silently no-op.
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self._split_on = False

    def reset_calls(self) -> None:
        self.calls.clear()

    # ---- Frequency / mode ------------------------------------------------

    async def set_freq(self, freq: int, receiver: int = 0) -> None:
        self.calls.append(("set_freq", (freq, receiver)))
        await super().set_freq(freq, receiver=receiver)

    async def set_mode(
        self,
        mode: object,
        filter_width: int | None = None,
        receiver: int = 0,
    ) -> None:
        self.calls.append(("set_mode", (mode, filter_width, receiver)))
        await super().set_mode(mode, filter_width=filter_width, receiver=receiver)  # type: ignore[arg-type]

    async def set_data_mode(self, on: int | bool, receiver: int = 0) -> None:
        self.calls.append(("set_data_mode", (bool(on), receiver)))
        await super().set_data_mode(on, receiver=receiver)

    # ---- PTT -------------------------------------------------------------

    async def set_ptt(self, on: bool) -> None:
        self.calls.append(("set_ptt", (bool(on),)))
        await super().set_ptt(on)

    # ---- Split / VFO -----------------------------------------------------

    async def set_split(self, on: bool) -> None:
        """Handler uses getattr fallback — must exist here to be observable."""
        self.calls.append(("set_split", (bool(on),)))
        self._split_on = bool(on)

    async def set_vfo(self, vfo: str) -> None:
        self.calls.append(("set_vfo", (vfo,)))
        await super().set_vfo(vfo)


# ---------------------------------------------------------------------------
# Test client — minimal rigctld ASCII client
# ---------------------------------------------------------------------------


class RigctldClient:
    """Minimal rigctld ASCII client used by the WSJT-X/fldigi/JS8Call tests."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._reader = reader
        self._writer = writer

    async def send(self, line: str, *, read_timeout: float = 1.0) -> str:
        """Send one command, return the decoded response (no trailing newline).

        Responses are either ``"RPRT <code>"`` for SET or a value block
        terminated by ``\\n`` for GET. We read whatever arrives within
        ``read_timeout`` and strip the trailing ``\\n``.
        """
        self._writer.write((line + "\n").encode("ascii"))
        await self._writer.drain()
        data = await asyncio.wait_for(self._reader.read(4096), timeout=read_timeout)
        return data.decode("ascii").rstrip("\n")

    async def close(self) -> None:
        try:
            self._writer.close()
            await self._writer.wait_closed()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _addr(server: RigctldServer) -> tuple[str, int]:
    assert server._server is not None
    host, port = server._server.sockets[0].getsockname()[:2]
    return str(host), int(port)


async def _make_server(
    radio: RecordingMockRadio,
) -> RigctldServer:
    cfg = RigctldConfig(
        host="127.0.0.1",
        port=0,
        max_clients=2,
        client_timeout=5.0,
        command_timeout=2.0,
    )
    srv = RigctldServer(radio, cfg)
    await srv.start()
    return srv


async def _make_client(server: RigctldServer) -> RigctldClient:
    host, port = _addr(server)
    reader, writer = await asyncio.open_connection(host, port)
    return RigctldClient(reader, writer)


@pytest.fixture
async def ic7610_setup() -> AsyncGenerator[
    tuple[RecordingMockRadio, RigctldServer], None
]:
    radio = RecordingMockRadio(model="IC-7610")
    await radio.connect()
    server = await _make_server(radio)
    try:
        yield radio, server
    finally:
        await server.stop()


@pytest.fixture
async def ic7300_setup() -> AsyncGenerator[
    tuple[RecordingMockRadio, RigctldServer], None
]:
    radio = RecordingMockRadio(model="IC-7300")
    await radio.connect()
    server = await _make_server(radio)
    try:
        yield radio, server
    finally:
        await server.stop()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWsjtxSplitOnIc7610:
    """WSJT-X split-on-TX-freq sequence against IC-7610 (dual-RX)."""

    async def test_split_enable_routes_tx_to_sub_receiver(
        self, ic7610_setup: tuple[RecordingMockRadio, RigctldServer]
    ) -> None:
        """``set_split_vfo 1 VFOB`` must enable split and route TX to SUB.

        Expected Radio-level call order (IC-7610, receiver_count=2):
            1. set_split(True)
            2. set_vfo("SUB")   # VFOB → SUB on main_sub scheme
        """
        radio, server = ic7610_setup
        client = await _make_client(server)
        try:
            # WSJT-X-typical preflight GETs (we don't assert on their
            # recorded-call side-effects — they go to getters only).
            await client.send("f")  # get_freq
            radio.reset_calls()

            # The split enable sequence.
            resp = await client.send("S 1 VFOB")
            assert resp == "RPRT 0"

            # PTT on → PTT off
            resp = await client.send("T 1")
            assert resp == "RPRT 0"
            resp = await client.send("T 0")
            assert resp == "RPRT 0"

            assert radio.calls == [
                ("set_split", (True,)),
                ("set_vfo", ("SUB",)),
                ("set_ptt", (True,)),
                ("set_ptt", (False,)),
            ]
        finally:
            await client.close()

    async def test_split_disable_does_not_switch_vfo(
        self, ic7610_setup: tuple[RecordingMockRadio, RigctldServer]
    ) -> None:
        """``set_split_vfo 0 VFOA`` only disables split — no set_vfo call.

        The handler only issues set_vfo when ``on=True`` (split enable).
        """
        radio, server = ic7610_setup
        client = await _make_client(server)
        try:
            radio.reset_calls()
            resp = await client.send("S 0 VFOA")
            assert resp == "RPRT 0"

            assert radio.calls == [("set_split", (False,))]
        finally:
            await client.close()


class TestFldigiModeFreqSequence:
    """fldigi mode/freq change sequence against IC-7610 and IC-7300."""

    async def test_fldigi_sequence_ic7610(
        self, ic7610_setup: tuple[RecordingMockRadio, RigctldServer]
    ) -> None:
        """fldigi: get_mode → set_mode USB → set_freq 7074000 → get_freq."""
        radio, server = ic7610_setup
        client = await _make_client(server)
        try:
            # 1. get_mode — should return the current (USB, passband) pair
            resp = await client.send("m")
            lines = resp.splitlines()
            assert lines[0] == "USB"
            assert lines[1].isdigit()

            radio.reset_calls()

            # 2. set_mode USB (no passband)
            resp = await client.send("M USB")
            assert resp == "RPRT 0"

            # 3. set_freq 7074000
            resp = await client.send("F 7074000")
            assert resp == "RPRT 0"

            # 4. get_freq — served from the handler's optimistic cache after set.
            resp = await client.send("f")
            assert resp == "7074000"

            # Active receiver is MAIN → receiver=0.
            assert radio.calls == [
                ("set_mode", ("USB", None, 0)),
                ("set_freq", (7074000, 0)),
            ]
        finally:
            await client.close()

    async def test_fldigi_sequence_ic7300(
        self, ic7300_setup: tuple[RecordingMockRadio, RigctldServer]
    ) -> None:
        """Same fldigi flow on IC-7300 (single-RX) — no regression."""
        radio, server = ic7300_setup
        client = await _make_client(server)
        try:
            resp = await client.send("m")
            assert resp.splitlines()[0] == "USB"

            radio.reset_calls()

            resp = await client.send("M USB")
            assert resp == "RPRT 0"
            resp = await client.send("F 7074000")
            assert resp == "RPRT 0"
            resp = await client.send("f")
            assert resp == "7074000"

            # IC-7300 has one receiver; handler still uses receiver=0.
            assert radio.calls == [
                ("set_mode", ("USB", None, 0)),
                ("set_freq", (7074000, 0)),
            ]
        finally:
            await client.close()


class TestJs8CallHeartbeat:
    """JS8Call heartbeat GETs against IC-7610."""

    async def test_heartbeat_get_freq_mode_roundtrip(
        self, ic7610_setup: tuple[RecordingMockRadio, RigctldServer]
    ) -> None:
        """JS8Call periodically issues get_freq and get_mode.

        The responses must be well-formed and should NOT produce any
        SET-style Radio calls.
        """
        radio, server = ic7610_setup
        client = await _make_client(server)
        try:
            radio.reset_calls()

            resp = await client.send("f")
            assert resp.isdigit()
            assert int(resp) == 14_074_000  # SerialMockRadio default

            resp = await client.send("m")
            mode_line, pb_line = resp.splitlines()
            assert mode_line == "USB"
            assert pb_line.isdigit()

            # A third heartbeat round — still GETs only.
            resp = await client.send("f")
            assert resp.isdigit()

            # No SET Radio calls produced by heartbeat GETs.
            set_calls = [name for name, _ in radio.calls if name.startswith("set_")]
            assert set_calls == []
        finally:
            await client.close()


class TestUnknownVfoBackwardsCompat:
    """Unknown VFO names must not raise; handler returns ok() fallback."""

    async def test_unknown_vfo_name_returns_ok(
        self, ic7610_setup: tuple[RecordingMockRadio, RigctldServer]
    ) -> None:
        """``set_vfo VFO-B`` (hyphenated) is not a known VFO — ok() fallback.

        Handler code (set_vfo):
            if vfo not in ("VFOA", "VFOB") or info is None:
                return _ok()

        The radio's set_vfo must NOT be called for unknown names.
        """
        radio, server = ic7610_setup
        client = await _make_client(server)
        try:
            radio.reset_calls()
            resp = await client.send("V VFO-B")
            assert resp == "RPRT 0"
            # No Radio-level set_vfo was issued.
            assert ("set_vfo", ("SUB",)) not in radio.calls
            assert ("set_vfo", ("MAIN",)) not in radio.calls
            assert all(name != "set_vfo" for name, _ in radio.calls)
        finally:
            await client.close()


# ---------------------------------------------------------------------------
# Real Hamlib chk_vfo=1 path — Variant A 1/5 (#1342)
# ---------------------------------------------------------------------------
#
# All test classes ABOVE this line exercise the bare-form path used by
# Hamlib clients under chk_vfo=0 (single-RX behaviour, or post-Variant-B
# rollback). They predate epic #1341.
#
# THIS class delegates to the golden-replay test in
# tests/integration/test_rigctld_golden_replay.py. The reason: research
# finding A5 of issue #1319 — the existing fake bare-form tests are why
# regression #1319 escaped CI in #722. We don't duplicate the replay
# here; we make the dependency explicit so a reviewer reading this file
# knows the chk_vfo=1 contract is enforced (just elsewhere).


class TestRigctldWsjtxRealDualRx:
    """The chk_vfo=1 wire trace — see test_rigctld_golden_replay.py.

    Variant A 1/5 (#1342) introduced ``tests/golden/wsjtx_dual_rx_session.txt``
    and ``tests/integration/test_rigctld_golden_replay.py::TestGoldenReplayDualRx``
    as the load-bearing assertion that prevents another #1319.

    This class documents that, on the WSJT-X side, the contract is:
        1. Open TCP connection.
        2. Send ``\\chk_vfo``; expect ``1``.
        3. Send ``\\dump_state``; receive 26+ lines.
        4. Every freq/mode/PTT/level/func/split command thereafter is
           prefixed with ``VFOA``/``VFOB``/``currVFO``.

    The test below is a "marker" test — it asserts the golden file
    exists and contains the chk_vfo=1 handshake. The actual wire-level
    replay lives in the dedicated test file so the wsjtx file stays
    focused on call-sequence assertions for the bare-form path.
    """

    def test_golden_replay_fixture_exists(self) -> None:
        """The wsjtx_dual_rx_session.txt fixture must exist and start
        with the chk_vfo=1 handshake. Replay assertions live in
        test_rigctld_golden_replay.py.
        """
        from pathlib import Path

        golden = Path(__file__).parent.parent / "golden" / "wsjtx_dual_rx_session.txt"
        assert golden.exists(), (
            "Variant A 1/5 (#1342) golden replay fixture missing — "
            "was it deleted or moved?"
        )
        text = golden.read_text()
        assert "> \\chk_vfo" in text
        assert "< 1" in text  # vfo_opt enabled response
        assert "> f VFOA" in text  # canonical VFO-prefixed command
