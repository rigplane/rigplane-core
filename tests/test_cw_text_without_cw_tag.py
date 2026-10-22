"""MOR-3678 (#4317 gate review): the profile's cwText alone decides CW text.

The ``cw`` capability tag drives the decoder, console and keyer controls;
it is not a second gate on ``send_cw_text``. A CI-V profile with a
``send_cw`` command but no ``cw`` tag publishes ``cwText.supported: true``
and sends over HTTP, the WebSocket and the CLI alike; the same profile with
``[cw_text] supported = false`` is refused by all three with the same text.
Transports are mocked: nothing reaches a port and nothing is keyed.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rigplane.core.capabilities import CAP_CW
from rigplane.rig_loader import load_rig
from rigplane.runtime.callable_support import supports_callable
from rigplane.runtime.radio import IcomRadio
from rigplane.web.server import WebConfig, WebServer, _serialize_cw_text
from test_cw_text_admission import _cli, _http, _ws
from test_cw_text_capability import _MINIMAL_TOML
from test_web_cw_http_control import _FakeWriter, _reader_for

UNSUPPORTED = "CW Text Test does not support sending CW text yet"


def _radio(tmp_path: Path, cw_text: str = "") -> IcomRadio:
    text = _MINIMAL_TOML.format(
        commands_extra="send_cw = [0x17]\nstop_cw = [0x17]", cw_text=cw_text
    )
    assert 'features = ["tx", "cw"]' in text
    text = text.replace('features = ["tx", "cw"]', 'features = ["tx"]')
    path = tmp_path / "cw_text_test.toml"
    path.write_text(text, encoding="utf-8")
    profile = load_rig(path).to_profile()
    assert CAP_CW not in profile.capabilities
    radio = IcomRadio("192.0.2.1", profile=profile)
    radio._check_connected = lambda: None  # type: ignore[method-assign]  # noqa: SLF001
    radio._send_civ_expect = AsyncMock(  # type: ignore[method-assign]  # noqa: SLF001
        return_value=SimpleNamespace(command=0xFB)
    )
    return radio


def _sent(radio: IcomRadio) -> int:
    return radio._send_civ_expect.await_count  # type: ignore[attr-defined]  # noqa: SLF001


def test_profile_without_cw_tag_publishes_cw_text_support(tmp_path: Path) -> None:
    radio = _radio(tmp_path)
    assert _serialize_cw_text(radio.profile)["supported"] is True
    assert supports_callable(radio.profile, "send_cw_text") is True


@pytest.mark.asyncio
async def test_http_sends_without_cw_tag(tmp_path: Path) -> None:
    radio = _radio(tmp_path)
    writer = await _http(radio, "CQ")  # type: ignore[arg-type]
    assert (writer.response_status, writer.response_body) == (200, {"text": "CQ"})
    assert _sent(radio) == 1


@pytest.mark.asyncio
async def test_websocket_sends_without_cw_tag(tmp_path: Path) -> None:
    radio = _radio(tmp_path)
    response = await _ws(radio, "CQ")  # type: ignore[arg-type]
    assert response["ok"] is True, response
    assert _sent(radio) == 1


@pytest.mark.asyncio
async def test_cli_sends_without_cw_tag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    radio = _radio(tmp_path)
    assert await _cli(radio, "CQ") == 0  # type: ignore[arg-type]
    assert "CW: CQ" in capsys.readouterr().out
    assert _sent(radio) == 1


_DISABLED = "[cw_text]\nsupported = false"


@pytest.mark.asyncio
async def test_disabled_profile_refused_alike_over_http(tmp_path: Path) -> None:
    radio = _radio(tmp_path, _DISABLED)
    writer = await _http(radio, "CQ")  # type: ignore[arg-type]
    assert (writer.response_status, writer.response_body) == (
        409,
        {"error": "unsupported_command", "message": UNSUPPORTED},
    )
    assert _sent(radio) == 0


@pytest.mark.asyncio
async def test_disabled_profile_refused_alike_over_websocket(tmp_path: Path) -> None:
    radio = _radio(tmp_path, _DISABLED)
    response = await _ws(radio, "CQ")  # type: ignore[arg-type]
    assert (response["ok"], response["error"], response["message"]) == (
        False,
        "unsupported_command",
        UNSUPPORTED,
    )
    assert _sent(radio) == 0


@pytest.mark.asyncio
async def test_disabled_profile_refused_alike_over_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    radio = _radio(tmp_path, _DISABLED)
    assert await _cli(radio, "CQ") == 1  # type: ignore[arg-type]
    captured = capsys.readouterr()
    assert f"Error: {UNSUPPORTED}" in captured.err
    assert "CW:" not in captured.out
    assert _sent(radio) == 0


@pytest.mark.asyncio
async def test_http_stop_works_where_cw_text_can_be_sent(tmp_path: Path) -> None:
    # A radio that sends CW text must also stop it over the same endpoint
    # set, cw tag or not (#4317 gate review).
    radio = _radio(tmp_path)
    radio._send_civ_raw = AsyncMock(  # type: ignore[method-assign]  # noqa: SLF001
        return_value=SimpleNamespace(command=0xFB)
    )
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))  # type: ignore[arg-type]
    writer = _FakeWriter()
    await srv._handle_http(  # noqa: SLF001
        writer,  # type: ignore[arg-type]
        "POST",
        "/api/v1/radio/cw/stop",
        headers={"content-length": "0"},
        reader=_reader_for(b""),
    )
    assert writer.response_status == 200, writer.response_body
    radio._send_civ_raw.assert_awaited_once()  # type: ignore[attr-defined]  # noqa: SLF001
