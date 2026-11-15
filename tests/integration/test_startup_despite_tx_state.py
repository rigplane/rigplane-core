"""MOR-4476: the server starts while PTT/split stay unanswered — Icom matrix.

Real startup through the production poller on the IC-7610 / IC-7300
serial PTY fakes: ``1C 00`` (ptt) or ``0F`` (split) stays silent or NAKed
while the rest answers. Every row: the server starts (silent rows at a
patched-down non-critical deadline, NAK rows at once), HTTP reports TX
unknown, silent warns once / NAK names INFO once, and a late answer clears
the unknown. The poller's local TX interlock still refuses a key while RF
state is unknown — without ever keying.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any
from unittest.mock import patch

import pytest

from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.backends.icom7610 import Icom7610SerialRadio
from rigplane.core.exceptions import CommandError
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.runtime.tx_interlock import evaluate_tx_interlock
from rigplane.testing.timing import await_until
from rigplane.web import web_startup
from rigplane.web.radio_poller import PttOn
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.web_startup import _observed_paths
from rigplane_testkit.serial.endpoints import PtyEndpoint, pty_supported
from testkit.ic7300_fakes import ic7300_usb_fake
from testkit.ic7610_fakes import ic7610_usb_fake

pytestmark = [
    pytest.mark.mock_integration,
    pytest.mark.skipif(not pty_supported(), reason="requires a PTY"),
]

PTT = FieldPath.global_("tx_state", "ptt")
SPLIT = FieldPath.global_("tx_state", "split")

_ICOM_RADIOS = {
    "IC-7610": (ic7610_usb_fake, Icom7610SerialRadio),
    "IC-7300": (ic7300_usb_fake, Ic7300SerialRadio),
}

_ICOM_COMMANDS = {
    "ptt": (0x1C, 0x00),
    "split": (0x0F, None),
}

# The production non-critical deadline is 10 s; the gate reads this module
# name every iteration, so a patched-down value keeps the same WARNING shape
# with no real 10 s wait. The other gate tests cover the literal 10 s.
_SHORT_DEADLINE = 3.0


async def _http_state(port: int) -> dict[str, Any]:
    """The user-visible ``/api/v1/state`` over a real socket."""
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
    assert int(head.split(b" ", 2)[1]) == 200, head
    return json.loads(body)


def _ptt_actuations(fake: Any) -> list[Any]:
    """PTT key-down frames on the wire (reads carry no data bytes)."""
    return [
        frame
        for frame in fake.commands
        if frame.command == 0x1C
        and frame.sub == 0x00
        and bytes(getattr(frame, "data", b"") or b"") != b""
    ]


@pytest.mark.realtime(
    reason="real PTY echo and the production serial backend/poller; "
    "the gate's non-critical deadline is patched down, no real 10 s wait"
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
    """MOR-4476 (Icom): a PTT/split read that never answers still serves."""
    make_fake, radio_cls = _ICOM_RADIOS[model]
    command, sub = _ICOM_COMMANDS[path_name]
    path = PTT if path_name == "ptt" else SPLIT
    other = SPLIT if path_name == "ptt" else PTT
    fake = make_fake()
    endpoint = PtyEndpoint(fake)
    await endpoint.start()
    radio = radio_cls(device=endpoint.device, _enumerate_serial_ports_fn=lambda: [])
    try:
        await asyncio.wait_for(radio.connect(), 30)
    except BaseException:
        await endpoint.stop()
        raise
    real_answer = fake._answer  # noqa: SLF001
    fault = {"on": True}

    def _answer_adverse(parsed: Any, connection: Any, frame: bytes) -> Any:
        if fault["on"] and parsed.command == command and parsed.sub == sub:
            if fate == "silent":
                return None  # the radio stays silent on this read only
            return fake._nak()  # noqa: SLF001
        return real_answer(parsed, connection, frame)

    fake._answer = _answer_adverse  # noqa: SLF001
    web = WebServer(
        radio,
        WebConfig(
            host="127.0.0.1",
            port=0,
            discovery=False,
            radio_model=model,
            await_initial_state=True,
        ),
    )
    scheduler = radio._acquisition_scheduler  # noqa: SLF001
    deadline = (
        patch.object(
            web_startup,
            "_STARTUP_GATE_NON_CRITICAL_DEADLINE_SECONDS",
            _SHORT_DEADLINE,
        )
        if fate == "silent"
        else contextlib.nullcontext()
    )
    try:
        with deadline, caplog.at_level(logging.INFO):
            await asyncio.wait_for(web.start(), 60)

        # Not vacuous: the radio was asked for the adverse read.
        assert [f for f in fake.commands if (f.command, f.sub) == (command, sub)]

        def _rest_arrived() -> bool:
            observed = _observed_paths(web, scheduler)
            return path not in observed and other in observed and len(observed) >= 3

        await await_until(
            _rest_arrived,
            timeout=30.0,
            description="answering reads observed, adverse still out",
        )
        port = int(web._server.sockets[0].getsockname()[1])  # noqa: SLF001
        body = await _http_state(port)
        assert body[path_name] is None, body.get(path_name)
        assert body["fieldStatus"][path_name]["observed"] is False
        if fate == "silent":
            warnings = [
                record.getMessage()
                for record in caplog.records
                if record.levelno == logging.WARNING
                and record.name == "rigplane.web.web_startup"
                and "no answer for" in record.getMessage()
                and str(path) in record.getMessage()
            ]
            assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
            assert "serving without them" in warnings[0]
            count_before = len(
                [f for f in fake.commands if (f.command, f.sub) == (command, sub)]
            )
            await await_until(
                lambda: (
                    len(
                        [
                            f
                            for f in fake.commands
                            if (f.command, f.sub) == (command, sub)
                        ]
                    )
                    > count_before
                ),
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
            ], "a refused path lapses no deadline WARNING"
            assert scheduler.consecutive_refusals(path) > 0
            assert scheduler.startup_defect is None
        if path_name == "ptt":
            poller = web._radio_poller  # noqa: SLF001
            decision = evaluate_tx_interlock(
                PttOn(),
                rf_state=poller._current_rf_state(),  # noqa: SLF001
            )
            assert not decision.allowed
            assert "RF state is unknown" in decision.reason
            with pytest.raises(CommandError, match="RF state is unknown"):
                poller._enforce_tx_interlock(PttOn())  # noqa: SLF001
            assert _ptt_actuations(fake) == [], "refused key must not reach the wire"
        # Phase 2 — the radio answers: the served unknown clears and, for
        # PTT, the key path allows again.
        fault["on"] = False
        await await_until(
            lambda: path in _observed_paths(web, scheduler),
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
