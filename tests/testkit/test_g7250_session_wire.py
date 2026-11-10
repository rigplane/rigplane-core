"""The G7250 from the operator's seat: discovery, connect, served state.

MOR-4352 review round 1, blocker 3: the product seam, not the units. The
serial hardware alone is faked -- the testkit ``CivSerialFake`` behind the
production ``serial_open._open_with_idle_lines`` seam (the harness of
``tests/testkit/test_memory_recall_wire.py``). Discovery runs the real
``discover_serial_radios``, the session is the radio Core builds from the
CLI options (``--model G7250 --serial-port ...``), the web app is the real
``WebServer.start()`` startup (poller, initial-state gate, bind), and the
assertions read the public HTTP state output.

Every reply the fake gives is a literal answer of the read-only bench
audit of a real G7250 (LIVE G7250 2026-11-09, 115200 baud): the radio
answers ANY CI-V address and echoes the address it was asked as its own
(probed 00/A4/88/70/72/E0), ``0x1D 0x19`` answers ``72 50``, ``03``
answers 7.138000 MHz, ``04`` answers LSB FIL1, ``0x1A 0x03`` answers
0x33 (2900 Hz through the profile's segmented-BCD table), ``15 02``
answers ``00 63``, and ``0x1A 0x01`` / ``0x1C 0x01`` NAK. The audit never
keyed the radio; no SET path is exercised here.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from rigplane.backends.factory import create_radio
from rigplane.cli import _build_backend_config, _build_parser
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame
from rigplane.core import serial_open
from rigplane_testkit.icom.replay import RecordedAnswerTable
from rigplane_testkit.serial import CivSerialFake
from test_discovery import _make_port
from test_web_server import _addr, _http_get, _read_http_response

_DEVICE = "civ-serial-fake"  # synthetic node name; no real path is opened
_ANSWER_WINDOW_S = 0.2  # compressed CI-V answer window (production: 2.0 s)

# The literal bench rows (LIVE G7250 2026-11-09, MOR-4352), as civ_pairs:
# request -> reply at the address the audit asked. The answer table keys on
# the CI-V body (addresses excluded), so one table serves the session at
# the profile address 0x88, a probe at any other address, and the X6200
# coverage row below.
_BENCH_ROWS = [
    # 0x1D 0x19 asked at the echoed broadcast address 0x00 -> `72 50`.
    {"dir": "c2r", "frame": "fefe00e01d19fd"},
    {"dir": "r2c", "frame": "fefee0001d197250fd"},
    # Filter width at the session address 0x88: 0x1A 0x03 -> 0x33 (=2900 Hz
    # through the profile's segmented_bcd_index table).
    {"dir": "c2r", "frame": "fefe88e01a03fd"},
    {"dir": "r2c", "frame": "fefee0881a0333fd"},
    # NAKed on the bench: the 0x1A 0x01 register (band spectrum / BSR) and
    # the 0x1C 0x01 tuner read (the G7250 has no ATU).
    {"dir": "c2r", "frame": "fefe88e01a01fd"},
    {"dir": "r2c", "frame": "fefee088fafd"},
    {"dir": "c2r", "frame": "fefe88e01c01fd"},
    {"dir": "r2c", "frame": "fefee088fafd"},
    # NB / NR toggles read 00 (off) -- the round-1 review's blocker 2 pair.
    {"dir": "c2r", "frame": "fefe88e01622fd"},
    {"dir": "r2c", "frame": "fefee088162200fd"},
    {"dir": "c2r", "frame": "fefe88e01640fd"},
    {"dir": "r2c", "frame": "fefee088164000fd"},
]


def _bench_answer_table(
    extra_rows: list[dict[str, str]] | None = None,
) -> RecordedAnswerTable:
    rows = [{"transport": "usb", **row} for row in [*_BENCH_ROWS, *(extra_rows or [])]]
    return RecordedAnswerTable.from_civ_pairs(rows, "usb")


class _G7250BenchFake(CivSerialFake):
    """The G7250 bench personality on the testkit serial fake.

    The shared fake answers identity (``19 00``) from its fixed
    ``civ_addr`` -- enough for the session at the profile address 0x88,
    where the real radio echoes the asked address (payload 0x88, LIVE
    G7250 2026-11-09). The broadcast asks are the one shape the shared
    fake cannot answer: the real radio replies to them as the 0x00 radio
    (identity echoes the broadcast address back with payload 0x00, and
    the Xiegu model-id probe answers ``72 50`` from 0x00 -- the literal
    frames discovery sees), so those two cases are overridden here.
    Every other reply comes from the native handlers
    (frequency/mode/PTT/S-meter through the production codecs) and the
    recorded bench table above.
    """

    def __init__(self) -> None:
        super().__init__(
            model="G7250",
            civ_addr=0x88,
            # The constructor refuses a profile without expected_ids; the
            # bench echo at the session address 0x88 is the cited source.
            # (The broadcast ask below never reaches this value.)
            model_id=b"\x88",
            recorded_answers=_bench_answer_table(),
            # LIVE G7250 2026-11-09: 03 -> 7.138000 MHz, 04 -> LSB FIL1,
            # 15 02 -> 00 63 (BCD of 63), 1C 00 -> 00 (idle).
            frequency_hz=7_138_000,
            mode=0x00,
            filter_width=1,
            s_meter=63,
        )

    def _answer(self, parsed: Any, connection: Any, frame: bytes) -> bytes | None:
        # The shared fake serves the recorded table to UNICAST frames only
        # (an unknown broadcast gets bus silence), but this radio answers
        # the broadcast-addressed discovery asks too (LIVE G7250
        # 2026-11-09). Both broadcast replies below are the literal bench
        # frames: identity echoes the broadcast address back, and the Xiegu
        # model-id probe answers `72 50` from 0x00.
        if parsed.to_addr == 0x00:
            if parsed.command == 0x19 and parsed.sub == 0x00:
                return build_civ_frame(
                    CONTROLLER_ADDR, 0x00, 0x19, sub=0x00, data=b"\x00"
                )
            if parsed.command == 0x1D and parsed.data == b"\x19":
                return bytes.fromhex("fefee0001d197250fd")
        return super()._answer(parsed, connection, frame)


def _mount(
    monkeypatch: pytest.MonkeyPatch, fake: CivSerialFake, opens: list[tuple[str, int]]
) -> None:
    """Route every production serial open to *fake*, recording (url, baud)."""

    async def opener(*, url: str, baudrate: int, **kw: Any) -> Any:
        opens.append((url, baudrate))
        return await fake.opener(url=url, baudrate=baudrate, **kw)

    monkeypatch.setattr(serial_open, "_open_with_idle_lines", opener)


async def _http_state(host: str, port: int) -> dict:
    status, _, body = await _http_get(host, port, "/api/v1/state")
    assert status == 200, f"GET /api/v1/state: HTTP {status}"
    return json.loads(body)


async def _http_post_json(
    host: str, port: int, path: str, payload: dict
) -> tuple[int, dict[str, str], bytes]:
    """Minimal POST with a JSON body over a real socket (mirrors _http_get)."""
    body = json.dumps(payload).encode()
    reader, writer = await asyncio.open_connection(host, port)
    try:
        writer.write(
            f"POST {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        return await asyncio.wait_for(_read_http_response(reader), timeout=5.0)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# The broadcast-address discovery path names it G7250
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discovery_names_the_g7250_through_the_broadcast_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real ``discover_serial_radios`` against the bench personality.

    The broadcast probe (``19 00`` at 0x00) is answered with the echoed
    broadcast address -- the address table alone says "Unknown (0x00)" --
    and the follow-up ``0x1D 0x19`` probe at 0x00 answers ``72 50``, which
    the profiles' ``xiegu_model_id`` data resolves to the G7250. The bench
    bridge is a generic WCH CH340, deliberately not the X6200 CH342
    fingerprint.
    """
    from rigplane.backends.discovery import discover_serial_radios

    fake = _G7250BenchFake()
    opens: list[tuple[str, int]] = []
    _mount(monkeypatch, fake, opens)
    port = _make_port(
        "/dev/cu.usbserial-2130",
        "USB Serial",
        "USB VID:PID=1A86:7523",
        vid=0x1A86,
        pid=0x7523,
        manufacturer="wch",
        product="USB Serial",
    )
    with monkeypatch.context() as m:
        m.setattr("serial.tools.list_ports.comports", lambda: [port])
        results = await discover_serial_radios()

    assert len(results) == 1
    result = results[0]
    assert result.protocol == "civ"
    assert result.address == 0x00
    assert result.model == "G7250"
    assert result.profile_id == "xiegu_g7250"
    # The probe hits the profile baud first (MOR-4207) and the radio
    # answers there: LIVE -- 115200, silent at 19200.
    assert result.baudrate == 115_200
    assert all(baud == 115_200 for _url, baud in opens)


# ---------------------------------------------------------------------------
# The session through the real entry point, state served over HTTP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_session_serves_the_bench_state_over_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI options -> backend config -> radio -> ``WebServer.start()`` -> state.

    ``WebServer.start()`` runs the production startup (poller, the
    initial-state-acquisition gate, then the bind), so reaching the HTTP
    assertions means the startup gate completed. No ``--baud`` is given:
    the profile's own 115200 must be what the session opens at.
    """
    from rigplane.web.server import WebConfig, WebServer

    fake = _G7250BenchFake()
    opens: list[tuple[str, int]] = []
    _mount(monkeypatch, fake, opens)

    args = _build_parser().parse_args(
        ["--model", "G7250", "--serial-port", _DEVICE, "status"]
    )
    assert args.serial_baud is None  # no explicit baud on the command line
    config = await _build_backend_config(args)
    assert config.baudrate == 115_200  # the profile's default_baud

    radio = create_radio(config)
    radio._civ_get_timeout = _ANSWER_WINDOW_S
    radio._enumerate_serial_ports_fn = lambda: []  # no OS port enumeration
    # await_initial_state is what the CLI web command sets (cli/__init__):
    # the startup gate holds the listener until initial state acquisition
    # completes -- the production behaviour this test pins.
    web = WebServer(
        radio,
        WebConfig(host="127.0.0.1", port=0, await_initial_state=True),
    )
    try:
        await asyncio.wait_for(radio.connect(), 30)
        # The startup gate (initial state acquisition) completes before the
        # listener binds -- this await IS the "acquisition complete" check.
        await asyncio.wait_for(web.start(), 90)

        host, port = _addr(web)
        state = await _http_state(host, port)

        # The bench reads, served through the public output.
        assert state["main"]["freqHz"] == 7_138_000
        assert state["main"]["mode"] == "LSB"
        assert state["main"]["filterWidth"] == 2900
        for key in ("main.freqHz", "main.mode", "main.filterWidth"):
            assert state["fieldStatus"][key]["observed"] is True, key

        # Every open of the session device used the profile's baud.
        assert opens, "the session never opened the serial device"
        assert all(url == _DEVICE and baud == 115_200 for url, baud in opens)
    finally:
        await web.stop()
        await radio.disconnect()


# ---------------------------------------------------------------------------
# NB / NR: a 00 reply reads False through the real API (review blocker 2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "civ_addr", "model_id"),
    [
        # LIVE G7250 2026-11-09: 16 22 (NB) and 16 40 (NR) read 00 = off.
        ("G7250", 0x88, b"\x88"),
        # The X6200 carries the same toggle pair at its family default
        # address (its 19 00 emulates the IC-705 id, b"\x01\x05") -- the
        # same check on the sibling radio for shared-API coverage.
        ("X6200", 0xA4, b"\x01\x05"),
    ],
)
@pytest.mark.asyncio
async def test_nb_and_nr_wire_zero_reads_false(
    model: str,
    civ_addr: int,
    model_id: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``16 22``/``16 40`` answering ``00`` reads False, never True.

    ``CoreRadio.get_nb``/``get_nr`` compare the first data byte to 0x01;
    a radio that answers 00 must surface as off on both Xiegu profiles
    (the ingress side decodes ``bool(data[0])`` -- the pair is consistent
    exactly when the reply is a real 00/01 toggle value).
    """
    fake = CivSerialFake(
        model=model,
        civ_addr=civ_addr,
        model_id=model_id,
        recorded_answers=_bench_answer_table(),
    )
    opens: list[tuple[str, int]] = []
    _mount(monkeypatch, fake, opens)

    radio = create_radio(
        await _build_backend_config(
            _build_parser().parse_args(
                ["--model", model, "--serial-port", _DEVICE, "status"]
            )
        )
    )
    radio._civ_get_timeout = _ANSWER_WINDOW_S
    radio._enumerate_serial_ports_fn = lambda: []
    try:
        await asyncio.wait_for(radio.connect(), 30)
        assert await radio.get_nb() is False
        assert await radio.get_nr() is False
        # The reads really went to the wire for both opcodes.
        asked = {(frame.command, frame.sub) for frame in fake.commands}
        assert (0x16, 0x22) in asked
        assert (0x16, 0x40) in asked
    finally:
        await radio.disconnect()


# ---------------------------------------------------------------------------
# RF power: the Pro TX smoke tool's pre-key round trip (MOR-4352 follow-up)
# ---------------------------------------------------------------------------

# The 14 0A register rows. The GET pair is the literal bench minimum read
# (LIVE G7250 2026-11-10: 14 0A answered 00 12; owner-read display 5 W, the
# menu goes no lower). The SET pair teaches the table that 14 0A writes are
# ACKed -- inherited from the X6200 family protocol, not probed on the
# G7250 (the audit never keyed the radio). With it the fake holds the
# written value like a register, so the poll after a SET reads raw 12 back.
_RF_POWER_ROWS = [
    {"dir": "c2r", "frame": "fefe88e0140afd"},
    {"dir": "r2c", "frame": "fefee088140a0012fd"},
    {"dir": "c2r", "frame": "fefe88e0140a0012fd"},
    {"dir": "r2c", "frame": "fefee088fbfd"},
]


@pytest.mark.asyncio
async def test_set_rf_power_normalized_minimum_reaches_wire_and_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The minimum-power round trip the TX smoke tool gates its key-up on.

    The tool reads ``[controls.rf_power]`` from the profile, requires
    ``raw_max == 255``, sets the radio to ``raw_min / 255`` through
    ``POST /api/v1/commands`` with ``level_unit = "normalized"`` and
    requires the ``powerLevel`` readback to reach that minimum before it
    keys. The same round trip against the bench personality, with only the
    serial port faked: the wire SET is ``FE FE 88 E0 14 0A 00 12 FD`` and
    the served state reports the profile minimum.
    """
    from rigplane.profiles import get_radio_profile
    from rigplane.web.server import WebConfig, WebServer

    profile = get_radio_profile("G7250")
    assert profile.controls is not None
    rf_power = profile.controls["rf_power"]
    raw_min, raw_max = rf_power["raw_min"], rf_power["raw_max"]
    assert raw_max == 255  # the tool's precondition

    fake = CivSerialFake(
        model="G7250",
        civ_addr=0x88,
        # The constructor refuses a profile without expected_ids; the
        # bench echo at the session address 0x88 is the cited source.
        model_id=b"\x88",
        recorded_answers=_bench_answer_table(_RF_POWER_ROWS),
        # LIVE G7250 2026-11-09: 03 -> 7.138000 MHz, 04 -> LSB FIL1,
        # 15 02 -> 00 63, 1C 00 -> 00 (idle).
        frequency_hz=7_138_000,
        mode=0x00,
        filter_width=1,
        s_meter=63,
    )
    opens: list[tuple[str, int]] = []
    _mount(monkeypatch, fake, opens)

    args = _build_parser().parse_args(
        ["--model", "G7250", "--serial-port", _DEVICE, "status"]
    )
    config = await _build_backend_config(args)

    radio = create_radio(config)
    radio._civ_get_timeout = _ANSWER_WINDOW_S
    radio._enumerate_serial_ports_fn = lambda: []  # no OS port enumeration
    web = WebServer(
        radio,
        WebConfig(host="127.0.0.1", port=0, await_initial_state=True),
    )
    try:
        await asyncio.wait_for(radio.connect(), 30)
        await asyncio.wait_for(web.start(), 90)

        host, port = _addr(web)
        status, _, body = await _http_post_json(
            host,
            port,
            "/api/v1/commands",
            {
                "name": "set_rf_power",
                "params": {
                    "level": raw_min / 255,
                    "level_unit": "normalized",
                },
            },
        )
        assert status == 200, (status, body)
        assert json.loads(body)["ok"] is True

        # The wire SET carries exactly the profile minimum, BCD-encoded.
        assert any(
            frame.command == 0x14 and frame.sub == 0x0A and frame.data == b"\x00\x12"
            for frame in fake.commands
        )

        state = await _http_state(host, port)
        assert state["powerLevel"] == pytest.approx(raw_min / 255)
    finally:
        await web.stop()
        await radio.disconnect()
