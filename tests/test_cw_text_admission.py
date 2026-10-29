"""MOR-3678: one core admission check for every send_cw_text.

The profile's ``cw_text`` (``supported``, ``max_length``) is the only source
of the CW text limit; there is no other constant. Every send passes the
same check inside the radio's ``send_cw_text`` (Icom and Yaesu), so the
HTTP endpoint, the WebSocket command and the CLI ``cw`` command refuse the
same requests with the same text:

* over the limit: ``CW text too long for <model>: got <n> characters,
  limit is <limit>`` -> HTTP 400 ``invalid_request``, WS
  ``command_failed``, CLI ``Error: ...`` and exit 1. Never truncated.
* disabled: ``<model> does not support sending CW text yet`` -> HTTP 409
  / WS ``unsupported_command``, CLI ``Error: ...`` and exit 1.

Transports are mocked: nothing reaches a port and nothing is keyed.
"""

from __future__ import annotations

import dataclasses
import json
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import rigplane.web.handlers.control as control
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.cli import _run
from rigplane.profiles import admit_cw_text
from rigplane.rig_loader import load_rig
from rigplane.runtime.radio import IcomRadio
from rigplane.web.protocol import decode_json
from rigplane.web.server import WebConfig, WebServer
from test_web_cw_http_control import _FakeWriter, _reader_for
from test_web_managed_tx_ingress import _handler

RIGS = Path(__file__).resolve().parents[1] / "rigs"

UNSUPPORTED = "FTX-1 does not support sending CW text yet"
TOO_LONG = "CW text too long for FTX-1: got 51 characters, limit is 50"


def _ftx1(*, supported: bool, max_length: int = 50) -> YaesuCatRadio:
    rig = load_rig(RIGS / "ftx1.toml")
    rig = dataclasses.replace(
        rig,
        cw_text=dataclasses.replace(
            rig.cw_text, supported=supported, max_length=max_length
        ),
    )
    radio = YaesuCatRadio("/dev/null", profile=rig)
    radio._transport._connected = True  # noqa: SLF001
    radio._transport.write = AsyncMock()  # noqa: SLF001
    radio._transport.query = AsyncMock(return_value="")  # noqa: SLF001
    return radio


def _writes(radio: YaesuCatRadio) -> list[str]:
    return [str(c.args[0]) for c in radio._transport.write.call_args_list]  # noqa: SLF001


# ── The limit is the profile's ───────────────────────────────────


def test_control_handler_has_no_cw_text_limit_of_its_own() -> None:
    assert not hasattr(control, "_MAX_CW_TEXT_CHARS")


@pytest.mark.asyncio
async def test_yaesu_limit_comes_from_the_profile() -> None:
    radio = _ftx1(supported=True, max_length=3)
    with pytest.raises(
        ValueError,
        match=r"^CW text too long for FTX-1: got 4 characters, limit is 3$",
    ):
        await radio.send_cw_text("CQCQ")
    assert _writes(radio) == []
    await radio.send_cw_text("CQ")
    assert _writes(radio) == ["KM5CQ;", "KY05;"]


@pytest.mark.asyncio
async def test_icom_limit_comes_from_the_profile() -> None:
    rig = load_rig(RIGS / "ic7300.toml")
    profile = dataclasses.replace(
        rig, cw_text=dataclasses.replace(rig.cw_text, max_length=4)
    ).to_profile()
    radio = IcomRadio("192.0.2.1", profile=profile)
    radio._send_civ_expect = AsyncMock()  # noqa: SLF001
    with pytest.raises(
        ValueError,
        match=r"^CW text too long for IC-7300: got 5 characters, limit is 4$",
    ):
        await radio.send_cw_text("CQ DE")
    radio._send_civ_expect.assert_not_awaited()  # noqa: SLF001


@pytest.mark.asyncio
async def test_icom_disabled_profile_refuses() -> None:
    rig = load_rig(RIGS / "ic7300.toml")
    profile = dataclasses.replace(
        rig, cw_text=dataclasses.replace(rig.cw_text, supported=False)
    ).to_profile()
    radio = IcomRadio("192.0.2.1", profile=profile)
    radio._send_civ_expect = AsyncMock()  # noqa: SLF001
    with pytest.raises(
        RuntimeError, match=r"^IC-7300 does not support sending CW text yet$"
    ):
        await radio.send_cw_text("CQ")
    radio._send_civ_expect.assert_not_awaited()  # noqa: SLF001


# ── Same refusal, same text: HTTP, WebSocket, CLI ────────────────


async def _http(radio: YaesuCatRadio, text: str) -> _FakeWriter:
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    payload = json.dumps({"text": text}).encode()
    writer = _FakeWriter()
    await srv._handle_http(  # noqa: SLF001
        writer,  # type: ignore[arg-type]
        "POST",
        "/api/v1/radio/cw/send",
        headers={"content-length": str(len(payload))},
        reader=_reader_for(payload),
    )
    return writer


async def _ws(radio: YaesuCatRadio, text: str) -> dict:
    ws = SimpleNamespace(send_text=AsyncMock())
    handler, _, _ = _handler(None, ws=ws)
    handler._radio = radio  # noqa: SLF001
    await handler._dispatch_command("cmd-cw", "send_cw_text", {"text": text})  # noqa: SLF001
    return decode_json(ws.send_text.await_args.args[0])


async def _cli(radio: YaesuCatRadio, text: str) -> int:
    radio.connect = AsyncMock()  # type: ignore[method-assign]
    radio.disconnect = AsyncMock()  # type: ignore[method-assign]
    args = Namespace(
        host="192.0.2.1",
        control_port=50001,
        user="",
        password="",
        timeout=0.1,
        command="cw",
        text=text,
        # create_radio is patched: the model only has to pass the CLI check.
        model="IC-7610",
    )
    with patch("rigplane.cli.create_radio", return_value=radio):
        return await _run(args)


CASES = [
    pytest.param(
        True,
        "A" * 51,
        400,
        "invalid_request",
        "command_failed",
        TOO_LONG,
        id="too-long",
    ),
    pytest.param(
        False,
        "CQ",
        409,
        "unsupported_command",
        "unsupported_command",
        UNSUPPORTED,
        id="unsupported",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("supported", "text", "status", "http_error", "ws_error", "message"), CASES
)
async def test_http_refuses_with_the_core_message(
    supported: bool,
    text: str,
    status: int,
    http_error: str,
    ws_error: str,
    message: str,
) -> None:
    radio = _ftx1(supported=supported)
    writer = await _http(radio, text)
    assert writer.response_status == status
    assert writer.response_body == {"error": http_error, "message": message}
    assert _writes(radio) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("supported", "text", "status", "http_error", "ws_error", "message"), CASES
)
async def test_websocket_refuses_with_the_core_message(
    supported: bool,
    text: str,
    status: int,
    http_error: str,
    ws_error: str,
    message: str,
) -> None:
    radio = _ftx1(supported=supported)
    response = await _ws(radio, text)
    assert response["ok"] is False
    assert response["error"] == ws_error
    assert response["message"] == message
    assert _writes(radio) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("supported", "text", "status", "http_error", "ws_error", "message"), CASES
)
async def test_cli_refuses_with_the_core_message(
    supported: bool,
    text: str,
    status: int,
    http_error: str,
    ws_error: str,
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    radio = _ftx1(supported=supported)
    rc = await _cli(radio, text)
    assert rc == 1
    captured = capsys.readouterr()
    assert f"Error: {message}" in captured.err
    assert "CW:" not in captured.out
    assert _writes(radio) == []


@pytest.mark.asyncio
async def test_cli_sends_text_within_the_limit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    radio = _ftx1(supported=True)
    assert await _cli(radio, "CQ") == 0
    assert _writes(radio) == ["KM5CQ;", "KY05;"]
    assert "CW: CQ" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_text_exactly_at_the_profile_limit_is_sent() -> None:
    radio = _ftx1(supported=True, max_length=3)
    await radio.send_cw_text("CQC")
    assert _writes(radio) == ["KM5CQC;", "KY05;"]


def test_icom_text_exactly_at_the_profile_limit_is_admitted() -> None:
    rig = load_rig(RIGS / "ic7300.toml")
    profile = dataclasses.replace(
        rig, cw_text=dataclasses.replace(rig.cw_text, max_length=4)
    ).to_profile()
    admit_cw_text(profile, "CQDE")
    with pytest.raises(ValueError, match="got 5 characters, limit is 4"):
        admit_cw_text(profile, "CQ DE")
