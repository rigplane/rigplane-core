"""MOR-4456: the server starts while PTT/split stay unanswered — Icom matrix.

Real startup through the production poller + fake hardware: Icom IC-7300 /
IC-7610 (serial PTY fakes) with ``1C 00`` / ``0F`` silent or NAKed while the
rest answers. Every case: the server starts, the answering criticals arrive,
HTTP/WS report TX unknown, the key is refused with the existing
unknown-RF-state reason, and one gate WARNING names the path; a late answer
clears it and the key path allows again — without ever keying. (The Yaesu
real-startup row returns with MOR-4477; Yaesu and rigplane's rigctld client
are proved in the gate-change PR.)
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


def _gated_web_config() -> WebConfig:
    """Listener config with the production startup gate engaged."""
    return WebConfig(
        host="127.0.0.1",
        port=0,
        keepalive_interval=9999.0,
        await_initial_state=True,
    )


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
