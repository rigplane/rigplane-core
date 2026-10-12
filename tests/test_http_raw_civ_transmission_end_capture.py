"""HTTP ``raw_civ_transaction`` capture parity with the typed unkey.

The typed unkey (``ptt_off``) never captures a controlling seat, so a
client that is not the current controller — or that holds a remote
ticket — can still end a transmission.  A raw CI-V transaction whose
built frame ends a transmission — PTT off (``1C 00 00``) or the voice
TX memory stop (``28 00 00``), bare or 0x29-wrapped, the set
``is_exact_raw_transmission_end_frame`` accepts — gets the same
exemption on the HTTP raw path: ``_HttpCommandExecutor`` skips
``controller.capture()`` for exactly those frames, and the remote-mode
HTTP route gate lets exactly those frames through to the handler.  The
raw tuner off (``1C 01 00``) stays captured, matching the typed
``set_tuner_status`` 0, and the batch credential gate still refuses
requests without a controller credential.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rigplane.core.command_service import command_intent_from_request
from rigplane.runtime.controller_authority import ControllerError, ControllerTicket
from rigplane.web.server import WebConfig, WebServer, _HttpCommandExecutor
from test_controller_authority import Safety
from test_web_command_batch_http import _managed_server, _post_json, _radio


def _remote_server() -> tuple[WebServer, Safety]:
    """Unmanaged server with a fake radio and a real remote-mode authority."""
    radio = _radio()
    radio.send_civ_transaction = AsyncMock(
        return_value=SimpleNamespace(status="sent", frame=None, frame_bytes=None)
    )
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    safety = Safety()
    srv._controller = safety.authority()
    return srv, safety


async def _arm_remote(srv: WebServer) -> tuple[str, ControllerTicket]:
    """Switch the server controller to remote mode; return key and ticket.

    Attaches a primary, mirroring a live remote station: without one,
    even a valid credential stops at ``controller_primary_required``
    before the executor's capture gate is reached.
    """
    await srv._controller.set_mode("remote")
    grant = srv._controller.acquire("pro")
    key = grant["controller_key"]
    assert isinstance(key, str)
    primary = srv._controller.attach(key, "primary", "native")
    return key, primary


async def _release(srv: WebServer) -> None:
    srv._controller.revoke()
    await srv._controller.settle()


def _raw_intent(command: int, sub: int | None, data: bytes):
    return command_intent_from_request(
        "raw_civ_transaction",
        {
            "command": command,
            "sub": sub,
            "data": data,
            "expect": "none",
            "timeout": None,
        },
        source="http",
    )


_TRANSMISSION_END_ROWS: tuple[tuple[tuple[int, int | None, bytes], str], ...] = (
    ((0x1C, 0x00, b"\x00"), "ptt-off"),
    ((0x29, 0x00, bytes.fromhex("1C0000")), "cmd29-wrapped-ptt-off"),
    ((0x28, 0x00, b"\x00"), "voice-memory-stop"),
    ((0x29, 0x00, bytes.fromhex("280000")), "cmd29-voice-memory-stop"),
)


@pytest.mark.parametrize(
    ("frame", "label"),
    [(row[0], row[1]) for row in _TRANSMISSION_END_ROWS],
    ids=[row[1] for row in _TRANSMISSION_END_ROWS],
)
async def test_executor_transmission_end_from_non_controller_reaches_radio(
    frame: tuple[int, int | None, bytes], label: str
) -> None:
    del label
    srv, _ = _remote_server()
    try:
        await _arm_remote(srv)
        command, sub, data = frame
        await _HttpCommandExecutor(srv).execute(_raw_intent(command, sub, data))
        srv._radio.send_civ_transaction.assert_awaited_once_with(
            command, sub=sub, data=data, expect="none", timeout=None
        )
    finally:
        await _release(srv)


@pytest.mark.parametrize(
    ("frame", "label"),
    [(row[0], row[1]) for row in _TRANSMISSION_END_ROWS],
    ids=[row[1] for row in _TRANSMISSION_END_ROWS],
)
async def test_executor_transmission_end_with_remote_ticket_reaches_radio(
    frame: tuple[int, int | None, bytes], label: str
) -> None:
    del label
    srv, _ = _remote_server()
    try:
        _, ticket = await _arm_remote(srv)
        command, sub, data = frame
        with srv._controller.bind(ticket):
            await _HttpCommandExecutor(srv).execute(_raw_intent(command, sub, data))
        srv._radio.send_civ_transaction.assert_awaited_once_with(
            command, sub=sub, data=data, expect="none", timeout=None
        )
    finally:
        await _release(srv)


async def test_executor_tuner_off_from_non_controller_is_captured() -> None:
    srv, _ = _remote_server()
    try:
        await _arm_remote(srv)
        with pytest.raises(ControllerError, match="controller_invalid"):
            await _HttpCommandExecutor(srv).execute(_raw_intent(0x1C, 0x01, b"\x00"))
        srv._radio.send_civ_transaction.assert_not_awaited()
    finally:
        await _release(srv)


async def test_executor_tuner_off_with_remote_ticket_is_captured() -> None:
    srv, _ = _remote_server()
    try:
        _, ticket = await _arm_remote(srv)
        with (
            srv._controller.bind(ticket),
            pytest.raises(ControllerError, match="controller_tx_unsupported"),
        ):
            await _HttpCommandExecutor(srv).execute(_raw_intent(0x1C, 0x01, b"\x00"))
        srv._radio.send_civ_transaction.assert_not_awaited()
    finally:
        await _release(srv)


async def test_executor_ptt_on_from_non_controller_is_captured() -> None:
    srv, _ = _remote_server()
    try:
        await _arm_remote(srv)
        with pytest.raises(ControllerError, match="controller_invalid"):
            await _HttpCommandExecutor(srv).execute(_raw_intent(0x1C, 0x00, b"\x01"))
        srv._radio.send_civ_transaction.assert_not_awaited()
    finally:
        await _release(srv)


async def test_executor_ptt_on_with_remote_ticket_is_captured() -> None:
    srv, _ = _remote_server()
    try:
        _, ticket = await _arm_remote(srv)
        with (
            srv._controller.bind(ticket),
            pytest.raises(ControllerError, match="controller_tx_unsupported"),
        ):
            await _HttpCommandExecutor(srv).execute(_raw_intent(0x1C, 0x00, b"\x01"))
        srv._radio.send_civ_transaction.assert_not_awaited()
    finally:
        await _release(srv)


async def test_executor_frequency_read_from_non_controller_is_captured() -> None:
    srv, _ = _remote_server()
    try:
        await _arm_remote(srv)
        with pytest.raises(ControllerError, match="controller_invalid"):
            await _HttpCommandExecutor(srv).execute(_raw_intent(0x03, None, b""))
        srv._radio.send_civ_transaction.assert_not_awaited()
    finally:
        await _release(srv)


async def test_executor_frequency_read_with_remote_ticket_is_captured() -> None:
    srv, _ = _remote_server()
    try:
        _, ticket = await _arm_remote(srv)
        with (
            srv._controller.bind(ticket),
            pytest.raises(ControllerError, match="controller_tx_unsupported"),
        ):
            await _HttpCommandExecutor(srv).execute(_raw_intent(0x03, None, b""))
        srv._radio.send_civ_transaction.assert_not_awaited()
    finally:
        await _release(srv)


async def test_executor_set_powerstat_from_non_controller_is_captured() -> None:
    srv, _ = _remote_server()
    srv._radio.set_powerstat = AsyncMock()
    try:
        await _arm_remote(srv)
        with pytest.raises(ControllerError, match="controller_invalid"):
            await _HttpCommandExecutor(srv).execute(
                command_intent_from_request(
                    "set_powerstat", {"power_on": True}, source="http"
                )
            )
        srv._radio.set_powerstat.assert_not_awaited()
    finally:
        await _release(srv)


async def test_executor_set_powerstat_with_remote_ticket_is_captured() -> None:
    srv, _ = _remote_server()
    srv._radio.set_powerstat = AsyncMock()
    try:
        _, ticket = await _arm_remote(srv)
        with (
            srv._controller.bind(ticket),
            pytest.raises(ControllerError, match="controller_tx_unsupported"),
        ):
            await _HttpCommandExecutor(srv).execute(
                command_intent_from_request(
                    "set_powerstat", {"power_on": True}, source="http"
                )
            )
        srv._radio.set_powerstat.assert_not_awaited()
    finally:
        await _release(srv)


async def test_executor_malformed_raw_params_from_non_controller_is_captured() -> None:
    srv, _ = _remote_server()
    try:
        await _arm_remote(srv)
        with pytest.raises(ControllerError, match="controller_invalid"):
            await _HttpCommandExecutor(srv).execute(
                command_intent_from_request("raw_civ_transaction", {}, source="http")
            )
        srv._radio.send_civ_transaction.assert_not_awaited()
    finally:
        await _release(srv)


async def test_executor_malformed_raw_params_with_remote_ticket_is_captured() -> None:
    srv, _ = _remote_server()
    try:
        _, ticket = await _arm_remote(srv)
        with (
            srv._controller.bind(ticket),
            pytest.raises(ControllerError, match="controller_tx_unsupported"),
        ):
            await _HttpCommandExecutor(srv).execute(
                command_intent_from_request("raw_civ_transaction", {}, source="http")
            )
        srv._radio.send_civ_transaction.assert_not_awaited()
    finally:
        await _release(srv)


_SINGLE_PASS_ROWS: tuple[tuple[dict, str, bool], ...] = (
    ({"command": 0x1C, "sub": 0x00, "data": "00", "expect": "none"}, "ptt-off", False),
    ({"command": 0x1C, "sub": 0x00, "data": "00", "expect": "none"}, "ptt-off", True),
    ({"command": 0x29, "sub": 0x00, "data": "1C0000", "expect": "none"}, "cmd29", True),
    ({"command": 0x28, "sub": 0x00, "data": "00", "expect": "none"}, "voice", False),
)


@pytest.mark.parametrize(
    ("payload", "label", "with_key"),
    _SINGLE_PASS_ROWS,
    ids=[f"{row[1]}-{'keyed' if row[2] else 'anonymous'}" for row in _SINGLE_PASS_ROWS],
)
async def test_single_endpoint_transmission_end_reaches_radio(
    payload: dict, label: str, with_key: bool
) -> None:
    del label
    srv, _ = _remote_server()
    try:
        key, _ticket = await _arm_remote(srv)
        headers = {"x-rigplane-controller": key} if with_key else None
        writer = await _post_json(
            srv, "/api/v1/civ/transaction", dict(payload), headers=headers
        )
        assert writer.response_status == 200
        assert writer.response_body["ok"] is True
        srv._radio.send_civ_transaction.assert_awaited_once_with(
            payload["command"],
            sub=payload.get("sub"),
            data=bytes.fromhex(payload.get("data", "")),
            expect="none",
            timeout=None,
        )
    finally:
        await _release(srv)


@pytest.mark.parametrize(
    ("payload", "with_key"),
    [
        ({"command": 0x1C, "sub": 0x01, "data": "00", "expect": "none"}, False),
        ({"command": 0x1C, "sub": 0x01, "data": "00", "expect": "none"}, True),
        ({"command": 0x1C, "sub": 0x00, "data": "01", "expect": "none"}, False),
        ({"command": 0x03, "expect": "none"}, True),
        ({"command": "0xZZ", "expect": "none"}, False),
    ],
    ids=[
        "tuner-off-anonymous",
        "tuner-off-keyed",
        "ptt-on-anonymous",
        "freq-read-keyed",
        "malformed-anonymous",
    ],
)
async def test_single_endpoint_other_frames_keep_route_refusal(
    payload: dict, with_key: bool
) -> None:
    srv, _ = _remote_server()
    try:
        key, _ticket = await _arm_remote(srv)
        headers = {"x-rigplane-controller": key} if with_key else None
        writer = await _post_json(
            srv, "/api/v1/civ/transaction", dict(payload), headers=headers
        )
        assert writer.response_status == 403
        assert writer.response_body["error"] == "controller_local_required"
        srv._radio.send_civ_transaction.assert_not_awaited()
    finally:
        await _release(srv)


async def test_single_endpoint_managed_transmission_end_still_refused() -> None:
    radio = _radio()
    radio.send_civ_transaction = AsyncMock(
        return_value=SimpleNamespace(status="sent", frame=None, frame_bytes=None)
    )
    srv, probe = _managed_server(radio)
    srv._controller = Safety().authority()
    try:
        await _arm_remote(srv)
        writer = await _post_json(
            srv,
            "/api/v1/civ/transaction",
            {"command": 0x1C, "sub": 0x00, "data": "00", "expect": "none"},
        )
        assert writer.response_status == 403
        assert writer.response_body["error"] == "managed_tx_refused"
        radio.send_civ_transaction.assert_not_awaited()
        assert probe.touches == []
    finally:
        await _release(srv)


async def test_batch_transmission_end_with_remote_key_reaches_radio() -> None:
    srv, _ = _remote_server()
    try:
        key, _ticket = await _arm_remote(srv)
        writer = await _post_json(
            srv,
            "/api/v1/commands/batch",
            {
                "steps": [
                    {
                        "type": "raw_civ_transaction",
                        "command": 0x1C,
                        "sub": 0x00,
                        "data": "00",
                        "expect": "none",
                    }
                ]
            },
            headers={"x-rigplane-controller": key},
        )
        assert writer.response_status == 200
        [step] = writer.response_body["results"]
        assert step["ok"] is True
        srv._radio.send_civ_transaction.assert_awaited_once_with(
            0x1C, sub=0x00, data=b"\x00", expect="none", timeout=10.0
        )
    finally:
        await _release(srv)


async def test_batch_tuner_off_with_remote_key_is_captured() -> None:
    srv, _ = _remote_server()
    try:
        key, _ticket = await _arm_remote(srv)
        writer = await _post_json(
            srv,
            "/api/v1/commands/batch",
            {
                "steps": [
                    {
                        "type": "raw_civ_transaction",
                        "command": 0x1C,
                        "sub": 0x01,
                        "data": "00",
                        "expect": "none",
                    }
                ]
            },
            headers={"x-rigplane-controller": key},
        )
        assert writer.response_status == 403
        assert writer.response_body["error"] == "controller_tx_unsupported"
        srv._radio.send_civ_transaction.assert_not_awaited()
    finally:
        await _release(srv)


async def test_batch_transmission_end_without_key_keeps_credential_refusal() -> None:
    srv, _ = _remote_server()
    try:
        await _arm_remote(srv)
        writer = await _post_json(
            srv,
            "/api/v1/commands/batch",
            {
                "steps": [
                    {
                        "type": "raw_civ_transaction",
                        "command": 0x1C,
                        "sub": 0x00,
                        "data": "00",
                        "expect": "none",
                    }
                ]
            },
        )
        assert writer.response_status == 403
        assert writer.response_body["error"] == "controller_invalid"
        srv._radio.send_civ_transaction.assert_not_awaited()
    finally:
        await _release(srv)
