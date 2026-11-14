"""G7250 operator controls through the real local API (MOR-4436 part A round 2).

Codex P1 on the round-1 profile-data commit (``f1d793c``): the corrected
G7250 values — ATT ``[0, 20]``, preamp ``[0, 1, 2]``, AGC OFF/FAST/SLOW =
``0/1/3``, CW pitch quantum 10, key speed 5-50 WPM — were pinned only at the
declaration level (``att_values == (0, 20)`` and friends). Owner rule: UI and
profile control changes need a seam-crossing test, so every case here drives
the real HTTP command handler against the real CLI-built G7250 radio over
the shared serial fake, and asserts the emitted CI-V bytes recorded by the
fake plus the served readback — never profile dict fields.

Harness (reused, not reinvented):

* ``tests/test_band_4m.py`` ``_station`` — a real ``WebServer`` on a real
  socket, the radio Core builds from the CLI options over the testkit
  ``CivSerialFake``, and a queue consumer that executes each submitted
  command through the poller (the role the running poller loop plays in
  production). Copied shape, G7250 build, no band plans.
* ``tests/test_web_split_writes.py`` ``_request`` — ``POST
  /api/v1/commands`` over a real socket.
* ``testkit/test_g7250_session_wire.py`` — ``_DEVICE``,
  ``_ANSWER_WINDOW_S`` and the bench ``RecordedAnswerTable``. No parallel
  fake: the shared ``CivSerialFake`` natively answers identity, frequency,
  mode, PTT and S-meter, and the bench table covers the recorded rows; the
  only addition is a small register overlay for the five operator-control
  bytes this profile drives (same subclass pattern as that module's
  ``_G7250QuantizedPowerFake``): a SET with data stores the bytes, the bare
  GET answers them, everything else falls through to ``super()``. The
  shared fake does not natively model ``0x11`` / ``0x16`` function /
  ``0x14`` level registers (unknown unicast reads answer NAK), so without
  the overlay the drain's readback GETs could not be answered; with it
  every case below is expressible on the shared fake and no case is
  dropped.

Readback path: the poller queues a post-write readback as a genuine
consequence of executing the command; the test dispatches it with one
production drain pass (``poller._send_query()``) and waits for the served
state the same way ``tests/test_ic9700_preamp.py`` does (bounded
``asyncio.timeout`` + ``asyncio.sleep(0)`` yields). No ``time.sleep``, no
wall-clock reads in test code (MOR-4075); the loop-time seeding copied from
``_station`` is the neighbouring pattern.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from rigplane.backends.factory import create_radio
from rigplane.cli import _build_backend_config, _build_parser
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame
from rigplane.core import serial_open
from rigplane.core.types import bcd_decode, bcd_encode
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.web.radio_poller import RadioPoller
from rigplane.web.server import WebConfig, WebServer
from rigplane_testkit.serial import CivSerialFake
from test_web_server import _addr, _http_get
from test_web_split_writes import _request
from testkit.test_g7250_session_wire import (
    _ANSWER_WINDOW_S,
    _DEVICE,
    _bench_answer_table,
)

_CIV_ADDR = 0xA0  # G7250 CI-V address (rigs/g7250.toml, MOR-4436)


class _G7250OperatorFake(CivSerialFake):
    """The G7250 operator-control registers on the shared serial fake.

    The radio stores the last written byte(s) per control and answers the
    bare GET with them — the echo-consistent minimal model (the same class
    as the whole-watt register in ``_G7250QuantizedPowerFake``): whatever
    the backend encoder wrote is what the readback decodes, so the served
    value can only match when the profile encodes the request correctly.
    Initial values mirror the bench audit (LIVE G7250 2026-11-09: ATT,
    preamp and NR/NB read ``00``); AGC starts at ``00`` (OFF) rather than
    the bench's MID shadow (``02``), which the new profile deliberately
    leaves uncommandable.
    """

    def __init__(self) -> None:
        super().__init__(
            model="G7250",
            civ_addr=_CIV_ADDR,
            model_id=b"\xa0",
            recorded_answers=_bench_answer_table(),
            frequency_hz=7_138_000,
            mode=0x00,
            filter_width=1,
            s_meter=63,
        )
        self._att = b"\x00"
        self._preamp = b"\x00"
        self._agc = b"\x00"
        self._pitch = b"\x00\x00"
        self._speed = b"\x00\x00"

    def _answer(self, parsed: Any, connection: Any, frame: bytes) -> bytes | None:
        if parsed.receiver is None:
            if parsed.command == 0x11:
                if parsed.data:
                    self._att = bytes(parsed.data)
                    return self._ack()
                return build_civ_frame(
                    CONTROLLER_ADDR, self._civ_addr, 0x11, data=self._att
                )
            if parsed.command == 0x16 and parsed.sub in (0x02, 0x12):
                register = "_preamp" if parsed.sub == 0x02 else "_agc"
                if parsed.data:
                    setattr(self, register, bytes(parsed.data))
                    return self._ack()
                return build_civ_frame(
                    CONTROLLER_ADDR,
                    self._civ_addr,
                    0x16,
                    sub=parsed.sub,
                    data=getattr(self, register),
                )
            if parsed.command == 0x14 and parsed.sub in (0x09, 0x0C):
                register = "_pitch" if parsed.sub == 0x09 else "_speed"
                if parsed.data:
                    setattr(self, register, bytes(parsed.data))
                    return self._ack()
                return build_civ_frame(
                    CONTROLLER_ADDR,
                    self._civ_addr,
                    0x14,
                    sub=parsed.sub,
                    data=getattr(self, register),
                )
            # Frequency SET the profile now uses: `25 00` + 5 BCD bytes
            # stores the frequency (the radio applies it and answers FB,
            # like the real radio on serial — reference Table 1-1 — and
            # over LAN, where bare `05` is only echoed, never applied).
            # 0x25 carries no sub-command byte, so the 00 selector rides in
            # the payload: the SET has 6 payload bytes, the bare `25 00`
            # GET has 1 and falls through to the shared fake, which answers
            # it from frequency_hz — as does `03`. `05` is deliberately
            # unhandled here: whatever the shared fake does with it stands.
            if (
                parsed.command == 0x25
                and parsed.sub is None
                and len(parsed.data) == 6
                and parsed.data[:1] == b"\x00"
            ):
                self.frequency_hz = bcd_decode(parsed.data[1:])
                return self._ack()
        return super()._answer(parsed, connection, frame)


@contextlib.asynccontextmanager
async def _station(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[
    tuple[WebServer, Any, CivSerialFake, list[Any], asyncio.Event, RadioPoller]
]:
    """The band-4m harness over a G7250: real server socket, real radio on
    the register fake, real poller consumer. Yields the poller as well so
    tests can dispatch the production post-write readback drain."""
    fake = _G7250OperatorFake()

    async def opener(*, url: str, baudrate: int, **kw: Any) -> Any:
        return await fake.opener(url=url, baudrate=baudrate, **kw)

    monkeypatch.setattr(serial_open, "_open_with_idle_lines", opener)
    args = _build_parser().parse_args(
        ["--model", "G7250", "--serial-port", _DEVICE, "status"]
    )
    radio = create_radio(await _build_backend_config(args))
    radio._civ_get_timeout = _ANSWER_WINDOW_S
    radio._civ_min_interval = 0.0  # no hardware pacing sleep (MOR-4075: no real waits)
    radio._enumerate_serial_ports_fn = lambda: []  # no OS port enumeration
    await asyncio.wait_for(radio.connect(), 30)
    web = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    poller = RadioPoller(radio, web.command_queue, state_store=web.command_state_store)
    web.command_state_store.apply_current(
        Observation(
            path=FieldPath.global_("tx_state", "ptt"),
            value=False,
            source=SourceMetadata(source="test", provider="tests"),
            timestamp_monotonic=asyncio.get_running_loop().time(),
            max_age=60,
        )
    )
    web._server = await asyncio.start_server(web._accept_client, "127.0.0.1", 0)
    entries: list[Any] = []
    executed = asyncio.Event()

    async def consume() -> None:
        while True:
            await web.command_queue.wait(2)
            entry = web.command_queue.take_entry()
            assert entry is not None
            entries.append(entry)
            await poller._execute_queued_entry(entry)
            executed.set()

    consumer = asyncio.create_task(consume())
    try:
        yield web, radio, fake, entries, executed, poller
    finally:
        web._server.close()
        await web._server.wait_closed()
        web._server = None
        tasks = (consumer, *tuple(web._client_tasks))
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await radio.disconnect()


async def _http_state(host: str, port: int) -> dict:
    status, _, body = await _http_get(host, port, "/api/v1/state")
    assert status == 200, f"GET /api/v1/state: HTTP {status}"
    return json.loads(body)


async def _http_capabilities(host: str, port: int) -> dict:
    status, _, body = await _http_get(host, port, "/api/v1/capabilities")
    assert status == 200, f"GET /api/v1/capabilities: HTTP {status}"
    return json.loads(body)


async def _wait_served(
    host: str, port: int, keys: tuple[str, ...], expected: object
) -> dict:
    """Poll the served state until *keys* resolve to *expected*.

    Same bounded yield-loop shape as
    ``tests/test_ic9700_preamp.py`` (``asyncio.timeout`` +
    ``asyncio.sleep(0)``): the readback reply arrives through the radio's
    own RX pump after the drain pass sends it, so the test yields until
    the store credits it.
    """
    async with asyncio.timeout(5.0):
        while True:
            state = await _http_state(host, port)
            node: object = state
            for key in keys:
                if not isinstance(node, dict) or key not in node:
                    node = None
                    break
                node = node[key]
            if node == expected:
                return state
            await asyncio.sleep(0)


def _single_set(fake: CivSerialFake, command: int, sub: int | None) -> Any:
    """The one SET frame with data the fake recorded for a command."""
    writes = [
        frame
        for frame in fake.commands
        if frame.command == command and frame.sub == sub and frame.data
    ]
    assert len(writes) == 1, (
        f"expected exactly one SET for {command:#04x}/{sub}, "
        f"got {len(writes)}: {[(f.command, f.sub, bytes(f.data)) for f in fake.commands]}"
    )
    return writes[0]


@pytest.mark.asyncio
async def test_att_on_writes_20_and_serves_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ATT on is a write of 20 dB, not 1.

    Previous profile (``attenuator.values = [0, 1]``): level 20 is refused
    (``ValueError`` → HTTP 400) and the on-value wrote ``01`` — so the
    ``11 20`` frame assertion, the served ``att == 20`` and the served
    ``attValues == [0, 20]`` all fail there.
    """
    async with _station(monkeypatch) as (web, _radio, fake, entries, executed, poller):
        host, port = _addr(web)
        status, result = await _request(web, "http", "set_att", {"level": 20})
        assert status == 200, result
        assert result["ok"] is True
        await asyncio.wait_for(executed.wait(), 5)
        assert len(entries) == 1

        # FE FE 88 E0 11 20 FD on the wire.
        written = _single_set(fake, 0x11, None)
        assert written.to_addr == _CIV_ADDR
        assert bytes(written.data) == b"\x20"

        await poller._send_query()
        state = await _wait_served(host, port, ("main", "att"), 20)
        assert state["main"]["att"] == 20

        assert (await _http_capabilities(host, port))["attValues"] == [0, 20]


@pytest.mark.asyncio
async def test_preamp_pre2_writes_02_and_serves_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PRE2 is a write of ``16 02 02``.

    Previous profile (``preamp.values = [0, 1]``): level 2 is refused
    (``ValueError`` → HTTP 400), so the ``16 02 02`` frame assertion and
    the served ``preamp == 2`` both fail there.
    """
    async with _station(monkeypatch) as (web, _radio, fake, entries, executed, poller):
        host, port = _addr(web)
        status, result = await _request(web, "http", "set_preamp", {"level": 2})
        assert status == 200, result
        assert result["ok"] is True
        await asyncio.wait_for(executed.wait(), 5)
        assert len(entries) == 1

        # FE FE 88 E0 16 02 02 FD on the wire.
        written = _single_set(fake, 0x16, 0x02)
        assert written.to_addr == _CIV_ADDR
        assert bytes(written.data) == b"\x02"

        await poller._send_query()
        state = await _wait_served(host, port, ("main", "preamp"), 2)
        assert state["main"]["preamp"] == 2

        assert (await _http_capabilities(host, port))["preValues"] == [0, 1, 2]


@pytest.mark.asyncio
async def test_agc_slow_writes_03_and_capabilities_serve_slow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SLOW is resolved from the served capabilities and writes ``16 12 03``.

    Previous profile (``agc.modes = [0, 1, 2, 3]`` with SLOW = 2): the
    served labels resolve SLOW to 2, so the ``16 12 03`` frame assertion
    fails there — and the capabilities assertions (``[0, 1, 3]`` /
    OFF/FAST/SLOW, no AUTO) fail with it.
    """
    async with _station(monkeypatch) as (web, _radio, fake, entries, executed, poller):
        host, port = _addr(web)
        capabilities = await _http_capabilities(host, port)
        assert capabilities["agcModes"] == [0, 1, 3]
        assert capabilities["agcLabels"] == {"0": "OFF", "1": "FAST", "3": "SLOW"}
        slow = next(
            mode for mode, label in capabilities["agcLabels"].items() if label == "SLOW"
        )
        assert slow == "3"

        status, result = await _request(web, "http", "set_agc", {"mode": int(slow)})
        assert status == 200, result
        assert result["ok"] is True
        await asyncio.wait_for(executed.wait(), 5)
        assert len(entries) == 1

        # FE FE 88 E0 16 12 03 FD on the wire.
        written = _single_set(fake, 0x16, 0x12)
        assert written.to_addr == _CIV_ADDR
        assert bytes(written.data) == b"\x03"

        await poller._send_query()
        state = await _wait_served(host, port, ("main", "agc"), 3)
        assert state["main"]["agc"] == 3


@pytest.mark.asyncio
async def test_cw_pitch_410_encodes_raw_3_and_serves_410(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """410 Hz encodes to raw 3 (``14 09 00 03``) and reads back as 410 Hz.

    Previous profile (``encode_rounding = "ceil"``, quantum 1): 410 Hz
    encoded to raw 4 (``14 09 00 04``) and decoded back as 413 Hz — so the
    frame assertion and the served ``cwPitch == 410`` both fail there.
    """
    async with _station(monkeypatch) as (web, _radio, fake, entries, executed, poller):
        host, port = _addr(web)
        status, result = await _request(web, "http", "set_cw_pitch", {"value": 410})
        assert status == 200, result
        assert result["ok"] is True
        await asyncio.wait_for(executed.wait(), 5)
        assert len(entries) == 1

        # FE FE 88 E0 14 09 00 03 FD on the wire.
        written = _single_set(fake, 0x14, 0x09)
        assert written.to_addr == _CIV_ADDR
        assert bytes(written.data) == b"\x00\x03"

        await poller._send_query()
        state = await _wait_served(host, port, ("cwPitch",), 410)
        assert state["cwPitch"] == 410


@pytest.mark.asyncio
async def test_key_speed_5_wpm_encodes_raw_0_and_serves_5(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 5 WPM floor encodes to raw 0 and reads back as 5 WPM.

    Previous profile (key speed band 6-48 WPM): 5 is outside the domain
    and refused (``ValueError`` → HTTP 400), so nothing is written and
    nothing is served.
    """
    async with _station(monkeypatch) as (web, _radio, fake, entries, executed, poller):
        host, port = _addr(web)
        status, result = await _request(web, "http", "set_key_speed", {"speed": 5})
        assert status == 200, result
        assert result["ok"] is True
        await asyncio.wait_for(executed.wait(), 5)
        assert len(entries) == 1

        written = _single_set(fake, 0x14, 0x0C)
        assert written.to_addr == _CIV_ADDR
        assert bytes(written.data) == b"\x00\x00"

        await poller._send_query()
        state = await _wait_served(host, port, ("keySpeed",), 5)
        assert state["keySpeed"] == 5


@pytest.mark.asyncio
async def test_key_speed_50_wpm_encodes_raw_255_and_serves_50(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 50 WPM ceiling encodes to raw 255 and reads back as 50 WPM.

    Previous profile (key speed band 6-48 WPM): 50 is outside the domain
    and refused (``ValueError`` → HTTP 400), so nothing is written and
    nothing is served.
    """
    async with _station(monkeypatch) as (web, _radio, fake, entries, executed, poller):
        host, port = _addr(web)
        status, result = await _request(web, "http", "set_key_speed", {"speed": 50})
        assert status == 200, result
        assert result["ok"] is True
        await asyncio.wait_for(executed.wait(), 5)
        assert len(entries) == 1

        written = _single_set(fake, 0x14, 0x0C)
        assert written.to_addr == _CIV_ADDR
        assert bytes(written.data) == b"\x02\x55"

        await poller._send_query()
        state = await _wait_served(host, port, ("keySpeed",), 50)
        assert state["keySpeed"] == 50


@pytest.mark.asyncio
async def test_set_freq_writes_25_00_to_a0_and_serves_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Frequency is written as ``25 00`` + BCD, never bare ``05``.

    Previous profile (``set_freq = [0x05]``): the write goes out as
    ``05 <bcd>`` — which the live G7250 over LAN echoes without applying
    (LIVE G7250 LAN 2026-11-13), and which the reference (Table 1-1)
    does not class as the serial write either — so the ``25 00`` frame
    assertion and the no-``05`` assertion below both fail there, while
    the served readback alone would stay green.
    """
    async with _station(monkeypatch) as (web, _radio, fake, entries, executed, poller):
        host, port = _addr(web)
        status, result = await _request(
            web, "http", "set_freq", {"freq": 7_280_000, "receiver": 0}
        )
        assert status == 200, result
        assert result["ok"] is True
        await asyncio.wait_for(executed.wait(), 5)
        assert len(entries) == 1

        # FE FE A0 E0 25 00 00 00 28 07 00 FD on the wire. 0x25 carries no
        # sub-command byte, so the frame parses with sub None and the 00
        # selector rides in the payload ahead of the 5 BCD bytes.
        written = _single_set(fake, 0x25, None)
        assert written.to_addr == _CIV_ADDR
        assert written.sub is None
        assert bytes(written.data) == b"\x00" + bcd_encode(7_280_000)
        assert bcd_encode(7_280_000) == bytes.fromhex("00 00 28 07 00")
        assert not any(frame.command == 0x05 for frame in fake.commands)

        await poller._send_query()
        state = await _wait_served(host, port, ("main", "freqHz"), 7_280_000)
        assert state["main"]["freqHz"] == 7_280_000
