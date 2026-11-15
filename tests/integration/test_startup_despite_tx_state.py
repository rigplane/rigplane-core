"""MOR-4456: the server starts while PTT/split stay unanswered.

Real startup through the production poller + fake hardware: Yaesu FTX-1
(bench CAT recording) and FT-991A (CAT simulator + served HTTP) with
``TX;`` (and FT-991A ``FT;``) silent or ``?;``; Icom IC-7300/IC-7610
(serial PTY fakes) with ``1C 00``/``0F`` silent or NAKed — while the rest
answers. Every case: the server starts, the answering criticals arrive,
HTTP/WS report TX unknown, the key is refused with the existing
unknown-RF-state reason, and one gate WARNING names the path; a late
answer clears it and the key path allows again — without ever keying.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

import pytest

from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.backends.icom7610 import Icom7610SerialRadio
from rigplane.core.exceptions import CommandError
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.runtime.tx_interlock import evaluate_tx_interlock
from rigplane.testing.timing import await_until
from rigplane.web.radio_poller import PttOn
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.web_startup import _observed_paths
from rigplane_testkit.serial.endpoints import PtyEndpoint, pty_supported
from testkit.ic7300_fakes import ic7300_usb_fake
from testkit.ic7610_fakes import ic7610_usb_fake
from support.yaesu_ft991a_http import (
    read_state as ft991a_read_state,
    serve_ft991a_over_http,
    wait_for_state_value as ft991a_wait_for_state_value,
)
from test_web_server import _close_ws, _ws_connect, _ws_recv_frame

pytestmark = [
    pytest.mark.mock_integration,
    pytest.mark.skipif(not pty_supported(), reason="requires a PTY"),
]

PTT = FieldPath.global_("tx_state", "ptt")
SPLIT = FieldPath.global_("tx_state", "split")


def _gate_warnings(caplog: pytest.LogCaptureFixture, path: FieldPath) -> list[str]:
    """Gate WARNINGs naming *path*."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
        and record.name == "rigplane.web.web_startup"
        and str(path) in record.getMessage()
    ]


def _store_shows(store: Any, path: FieldPath, value: object) -> bool:
    """Whether the store currently carries *value* for *path*."""
    try:
        return store.snapshot().field(path).value is value
    except KeyError:
        return False


async def _ws_state_data(port: int) -> dict[str, Any]:
    """First full ``state_update`` baseline a fresh WS client receives."""
    reader, writer, _ = await _ws_connect("127.0.0.1", port, "/api/v1/ws")
    try:
        async with asyncio.timeout(15):
            while True:
                _, payload = await _ws_recv_frame(reader)
                message = json.loads(payload.decode("utf-8"))
                if message.get("type") == "state_update":
                    data = message.get("data")
                    assert isinstance(data, dict), message
                    return data
    finally:
        await _close_ws(writer)


# Yaesu: FTX-1 bench recording and FT-991A simulator, real backend + gate


def _ftx1_table(fate: str | None):
    """Bench FTX-1 recording; ``fate`` drops/refuses every ``TX;`` answer.

    ``"silent"`` swallows the recorded ``TX0;`` replies so the replay never
    answers PTT; ``"refused"`` rewrites them to ``?;``; ``None`` returns the
    recording unmodified (the late-answer phase). Mirrors the ``FT;``
    surgery ``test_startup_state_gate`` pins.
    """
    import json as _json

    from rigplane_testkit.yaesu.replay import RecordedCatAnswerTable, reply_closes

    pairs = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "fixtures"
        / "ftx1"
        / "cat_pairs.jsonl"
    )
    rows = [_json.loads(line) for line in pairs.read_text().splitlines() if line]
    if fate is None:
        return RecordedCatAnswerTable.from_cat_pairs(rows)
    kept: list[dict[str, object]] = []
    pending: str | None = None
    for row in rows:
        if row["dir"] == "c2r":
            pending = str(row["line"])
            kept.append(row)
        elif pending == "TX;" and reply_closes(pending, str(row["line"])):
            pending = None
            if fate == "silent":
                continue
            kept.append({**row, "line": "?;"})
        else:
            pending = None
            kept.append(row)
    table = RecordedCatAnswerTable.from_cat_pairs(kept)
    tx_answer = table.lookup("TX;")
    assert tx_answer is not None, "bench recording must contain a TX; answer"
    if fate == "silent":
        assert tx_answer.kind == "silence"
    else:
        assert tx_answer.reply == "?;"
    return table


def _gated_web_config() -> WebConfig:
    """Listener config with the production startup gate engaged."""
    return WebConfig(
        host="127.0.0.1",
        port=0,
        keepalive_interval=9999.0,
        await_initial_state=True,
    )


async def _assert_served_unknown(host: str, port: int, leaf: str) -> dict[str, Any]:
    """The served state reports *leaf* unknown (null + unobserved)."""
    body = await ft991a_read_state(host, port)
    assert body[leaf] is None, body.get(leaf)
    assert body["fieldStatus"][leaf]["observed"] is False
    return body


async def _assert_yaesu_key_refused(station: Any) -> None:
    """The production Yaesu poller refuses a key on unknown RF state."""
    from rigplane.web.radio_poller import PttOn

    poller = station.server._state_poller  # noqa: SLF001
    decision = evaluate_tx_interlock(PttOn(), rf_state=poller._current_rf_state())
    assert not decision.allowed
    assert "RF state is unknown" in decision.reason
    with pytest.raises(CommandError, match="RF state is unknown"):
        await poller._execute_command(PttOn())  # noqa: SLF001
    assert "TX1;" not in station.wire, "refused key must not reach the wire"


@pytest.mark.skip(
    reason="MOR-4456 follow-up: gated Yaesu startup still aborts on "
    "pre-existing non-PTT declared-read defects no ptt/split demotion can "
    "lift — the FT-991A sim has no RA verb (RA0; answers ?; and records), "
    "the FTX-1 bench recording refuses RA0; and answers BP01F35; against "
    "BP01{3d}; the executable spec stays until the sim gains those verbs"
)
@pytest.mark.realtime(
    reason="real HTTP/WS socket and the production Yaesu poller/readback path"
)
@pytest.mark.parametrize("profile", ["ft991a", "ftx1"])
@pytest.mark.parametrize("fate", ["silent", "refused"])
async def test_yaesu_unanswered_ptt_still_serves_with_tx_unknown(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    profile: str,
    fate: str,
) -> None:
    """MOR-4456 (Yaesu PTT): a PTT read that never answers still serves.

    ``TX;`` stays silent (or answers ``?;``) while the rest answers, on the
    FT-991A simulator and the FTX-1 bench recording, through the real
    backend and gated ``WebServer.start()``. The server starts, PTT is
    served unknown over HTTP/WS, the key is refused for unknown RF state,
    and one gate WARNING names the path; a late answer clears it and the
    key path allows again.
    """
    from rigplane_testkit.sim.engine import Ok, Refused
    from rigplane_testkit.yaesu.replay import REFUSAL

    shadow = {"on": True}

    def hook(line: str) -> Any:
        if shadow["on"] and line == "TX;":
            if fate == "silent":
                return (None, Ok())
            return (REFUSAL, Refused("native_refusal"))
        return None

    async def stop() -> None:
        raise AssertionError("stop is assigned per profile below")

    if profile == "ft991a":
        with caplog.at_level(logging.WARNING, logger="rigplane.web.web_startup"):
            station = await serve_ft991a_over_http(
                monkeypatch, web_config=_gated_web_config(), command_hook=hook
            )
        stop = station.aclose
        host, port = station.host, station.port
    else:
        from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
        from rigplane_testkit.yaesu import FakeCatRadio
        from support.yaesu_ft991a_http import Ft991aHttpStation

        full_table = _ftx1_table(None)
        fake = FakeCatRadio(recorded_answers=_ftx1_table(fate))
        await fake.start()
        radio = YaesuCatRadio(device=fake.device_url, profile="ftx1")
        await asyncio.wait_for(radio.connect(), 30)
        config = _gated_web_config()
        config.radio_model = "FTX-1"
        server = WebServer(radio, config)
        try:
            with caplog.at_level(logging.WARNING, logger="rigplane.web.web_startup"):
                await asyncio.wait_for(server.start(), 60)
        except BaseException:
            await server.stop()
            await radio.disconnect()
            await fake.stop()
            raise
        station = Ft991aHttpStation(radio=radio)
        station.server = server
        station.port = server.port
        station.wire = fake.commands

        async def stop() -> None:  # noqa: F811
            await server.stop()
            await radio.disconnect()
            await fake.stop()

        host, port = "127.0.0.1", server.port

    try:
        # Not vacuous: the production poller asked for the PTT read.
        await await_until(
            lambda: "TX;" in station.wire,
            timeout=20.0,
            description="poller asks TX;",
        )
        # The answering criticals arrive through the real poller.
        if profile == "ft991a":
            assert await ft991a_wait_for_state_value(host, port, "mode", "USB")
        else:
            from rigplane.backends.yaesu_cat.observations import _MAIN_FREQ

            assert (
                station.server.command_state_store.snapshot().field(_MAIN_FREQ).value
                == 14_074_000
            )
        await _assert_served_unknown(host, port, "ptt")
        assert (await _ws_state_data(port))["ptt"] is None
        await _assert_yaesu_key_refused(station)
        warnings = _gate_warnings(caplog, PTT)
        assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
        assert "no declared read" in warnings[0], warnings[0]
        assert "never answered" in warnings[0], warnings[0]
        # The poller keeps asking while the gate has released.
        tx_before = station.wire.count("TX;")
        await await_until(
            lambda: station.wire.count("TX;") > tx_before,
            timeout=15.0,
            description="poller keeps asking TX; after release",
        )
        # Phase 2 — the radio answers: the served state reports PTT and
        # the key path allows again (no second WARNING names the path).
        if profile == "ft991a":
            shadow["on"] = False
        else:
            fake._recorded = full_table  # noqa: SLF001
        await await_until(
            lambda: _store_shows(station.server.command_state_store, PTT, False),
            timeout=30.0,
            description="late TX; answer observed",
        )
        body = await ft991a_read_state(host, port)
        assert body["ptt"] is False
        assert body["fieldStatus"]["ptt"]["observed"] is True

        from rigplane.web.radio_poller import PttOn

        poller = station.server._state_poller  # noqa: SL001
        await await_until(
            lambda: (
                evaluate_tx_interlock(
                    PttOn(), rf_state=poller._current_rf_state()
                ).allowed
            ),
            timeout=15.0,
            description="key path allows on fresh RX",
        )
        assert len(_gate_warnings(caplog, PTT)) == 1
    finally:
        await stop()


# Icom: IC-7610 / IC-7300 serial fakes, real backend + poller + web gate

_ICOM_RADIOS = {
    "IC-7610": (ic7610_usb_fake, Icom7610SerialRadio),
    "IC-7300": (ic7300_usb_fake, Ic7300SerialRadio),
}

_ICOM_COMMANDS = {
    "ptt": (0x1C, 0x00),
    "split": (0x0F, None),
}


async def _connected_icom(model: str):
    make_fake, radio_cls = _ICOM_RADIOS[model]
    fake = make_fake()
    endpoint = PtyEndpoint(fake)
    await endpoint.start()
    radio = radio_cls(device=endpoint.device, _enumerate_serial_ports_fn=lambda: [])
    try:
        await asyncio.wait_for(radio.connect(), 30)
    except BaseException:
        await endpoint.stop()
        raise
    return fake, endpoint, radio


def _icom_gated_web(radio, model: str) -> WebServer:
    return WebServer(
        radio,
        WebConfig(
            host="127.0.0.1",
            port=0,
            discovery=False,
            radio_model=model,
            await_initial_state=True,
        ),
    )


async def _http_state(port: int) -> dict[str, Any]:
    """GET the user-visible ``/api/v1/state`` over a real socket."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(
            f"GET /api/v1/state HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
            "Connection: close\r\n\r\n".encode()
        )
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(-1), 10.0)
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
    head, _, body = raw.partition(b"\r\n\r\n")
    assert int(head.split(b" ", 2)[1]) == 200
    return json.loads(body)


def _ptt_actuations(fake) -> list:
    """PTT key-down frames on the wire (reads carry no data bytes)."""
    return [
        frame
        for frame in fake.commands
        if frame.command == 0x1C
        and frame.sub == 0x00
        and bytes(getattr(frame, "data", b"") or b"") != b""
    ]


async def _assert_icom_key_refused_unknown_rf(web: WebServer, fake) -> None:
    """The production web poller refuses a key on unknown RF state."""
    poller = web._radio_poller  # noqa: SLF001
    decision = evaluate_tx_interlock(PttOn(), rf_state=poller._current_rf_state())  # noqa: SLF001
    assert not decision.allowed
    assert "RF state is unknown" in decision.reason
    with pytest.raises(CommandError, match="RF state is unknown"):
        poller._enforce_tx_interlock(PttOn())  # noqa: SLF001
    assert _ptt_actuations(fake) == [], "refused key must not reach the wire"


@pytest.mark.realtime(
    reason="real PTY echo and the production serial backend/poller; "
    "the gate's non-critical deadline passes in real time"
)
@pytest.mark.parametrize("model", ["IC-7610", "IC-7300"])
@pytest.mark.parametrize("path_name", ["ptt", "split"])
@pytest.mark.parametrize("fate", ["silent", "refused"])
async def test_icom_unanswered_tx_state_still_serves_with_tx_unknown(
    model: str,
    path_name: str,
    fate: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MOR-4456 (Icom): a PTT/split read that never answers still serves.

    ``1C 00``/``0F`` stays silent or NAKed while the rest answers, on the
    IC-7610/IC-7300 serial fakes through the production backend and gated
    ``WebServer.start()``. The server starts, HTTP reports TX unknown, and
    one gate record names the path (a WARNING for silence, an INFO refused
    record with no WARNING for a NAK); a late answer clears the unknown
    and — for PTT — the key path allows again.
    """
    from rigplane_testkit.serial.civ_serial import CivSerialFake

    command, sub = _ICOM_COMMANDS[path_name]
    path = FieldPath.global_("tx_state", path_name)
    other = FieldPath.global_("tx_state", "split" if path_name == "ptt" else "ptt")
    fake, endpoint, radio = await _connected_icom(model)
    real_answer = fake._answer  # noqa: SLF001

    fault = {"on": True}

    def _answer_adverse(parsed, connection, frame):
        if fault["on"] and parsed.command == command and parsed.sub == sub:
            if fate == "silent":
                return None  # the radio stays silent on this read only
            assert isinstance(fake, CivSerialFake)
            return fake._nak()  # noqa: SLF001
        return real_answer(parsed, connection, frame)

    fake._answer = _answer_adverse  # noqa: SLF001
    web = _icom_gated_web(radio, model)
    try:
        if fate == "silent":
            with caplog.at_level(logging.WARNING, logger="rigplane.web.web_startup"):
                await asyncio.wait_for(web.start(), 30)
        else:
            with caplog.at_level(logging.INFO):
                await asyncio.wait_for(web.start(), 30)

        # Not vacuous: the radio was asked for the adverse read.
        def asked() -> list:
            return [f for f in fake.commands if (f.command, f.sub) == (command, sub)]

        assert asked(), "the poller asked for the adverse read"
        # The answering paths arrive through the real poller.
        observed = _observed_paths(web, radio._acquisition_scheduler)
        assert path not in observed
        assert other in observed, observed
        assert len(observed) >= 3, observed
        port = int(web._server.sockets[0].getsockname()[1])  # noqa: SLF001
        body = await _http_state(port)
        assert body[path_name] is None, body.get(path_name)
        assert body["fieldStatus"][path_name]["observed"] is False
        if fate == "silent":
            warnings = _gate_warnings(caplog, path)
            assert len(warnings) == 1, [
                record.getMessage() for record in caplog.records
            ]
            assert ("1C 00" if path_name == "ptt" else "0F") in warnings[0]
            assert "timed out" in warnings[0], warnings[0]
            # The poller keeps asking while the gate has released.
            count_before = len(asked())
            await await_until(
                lambda: len(asked()) > count_before,
                timeout=15.0,
                description="poller keeps asking the silent read",
            )
        else:
            messages = [record.getMessage() for record in caplog.records]
            assert any(
                "refused" in message and str(path) in message
                for message in messages
                if message.startswith("acquisition:")
            ), "the refused path is named once at INFO"
            assert not [
                record
                for record in caplog.records
                if record.levelno >= logging.WARNING
                and str(path) in record.getMessage()
            ], "an unavailable path lapses no deadline WARNING"
        if path_name == "ptt":
            await _assert_icom_key_refused_unknown_rf(web, fake)
        # Phase 2 — the radio answers: the served unknown clears and, for
        # PTT, the key path allows again.
        fault["on"] = False
        await await_until(
            lambda: _tx_observed(web, path),
            timeout=30.0,
            description="late adverse-read answer observed",
        )
        body = await _http_state(port)
        assert body["fieldStatus"][path_name]["observed"] is True
        assert body[path_name] is not None
        if path_name == "ptt":
            poller = web._radio_poller  # noqa: SLF001
            await await_until(
                lambda: (
                    evaluate_tx_interlock(
                        PttOn(),
                        rf_state=poller._current_rf_state(),  # noqa: SLF001
                    ).allowed
                ),
                timeout=15.0,
                description="key path allows on fresh RX",
            )
    finally:
        with contextlib.suppress(Exception):
            await web.stop()
        await radio.disconnect()
        await endpoint.stop()


def _tx_observed(web: WebServer, path: FieldPath) -> bool:
    """Whether the store currently carries a fresh value for *path*."""
    try:
        web.command_state_store.snapshot().field(path)
    except KeyError:
        return False
    return True
