"""HTTP typed transmission-end passes the remote route gate like the raw one.

In remote mode an anonymous client can already END a transmission through
``POST /api/v1/civ/transaction`` with a transmission-end frame (PTT off
``1C 00 00``, voice TX memory stop ``28 00 00``): the route peeks the body
and skips the credential gate for exactly those frames.  A typed
transmission end — ``ptt_off``, ``ptt`` with ``state`` false, or a
``send_civ`` whose frame ends a transmission — gets the same exemption on
``POST /api/v1/commands`` (single command) and on
``POST /api/v1/commands/batch`` only when EVERY step ends a transmission
(typed, or a raw transaction step with a transmission-end frame).  Anything
that can key — ``ptt`` true or missing ``state``, ``ptt_on``,
``transmit_on``, reads, ``set_freq``, a non-transmission-end ``send_civ`` —
still needs the controller credential, as does a malformed, oversize or
unreadable body.  The peek classifies with the same parse the handler
executes, so no body reads as a transmission end to the peek and as keying
to the handler.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from rigplane.runtime.controller_authority import ControllerError
from rigplane.web.server import WebServer
from test_controller_authority import Safety
from test_http_raw_civ_transmission_end_capture import (
    _arm_remote,
    _release,
    _remote_server,
)
from test_web_command_batch_http import (
    _complete_ordered_commands,
    _FakeWriter,
    _post_json,
    _reader_for,
)


async def _post_raw(
    srv: WebServer,
    path: str,
    body: bytes,
    *,
    headers: dict[str, str] | None = None,
) -> _FakeWriter:
    writer = _FakeWriter()
    request_headers = {"content-length": str(len(body))}
    if headers:
        request_headers.update(headers)
    await srv._handle_http(  # noqa: SLF001
        writer,  # type: ignore[arg-type]
        "POST",
        path,
        headers=request_headers,
        reader=_reader_for(body),
    )
    return writer


async def _anonymous_and_keyed(
    path: str, payload: dict[str, Any]
) -> tuple[_FakeWriter, _FakeWriter]:
    """Post *payload* without and with a credential; return both writers."""
    srv, _ = _remote_server()
    try:
        key, _ticket = await _arm_remote(srv)
        anonymous = await _post_json(srv, path, dict(payload))
        keyed = await _post_json(
            srv, path, dict(payload), headers={"x-rigplane-controller": key}
        )
        return anonymous, keyed
    finally:
        await _release(srv)


_DISPATCHED_SINGLE_ROWS: tuple[tuple[dict[str, Any], str], ...] = (
    ({"name": "ptt_off", "params": {}}, "ptt-off"),
    ({"name": "ptt", "params": {"state": False}}, "ptt-state-false"),
    (
        {"name": "send_civ", "params": {"command": 0x1C, "sub": 0x00, "data": "00"}},
        "send-civ-ptt-off",
    ),
    (
        {"name": "send_civ", "params": {"command": 0x28, "sub": 0x00, "data": "00"}},
        "send-civ-voice-stop",
    ),
)


@pytest.mark.parametrize(
    ("payload", "label"),
    [(row[0], row[1]) for row in _DISPATCHED_SINGLE_ROWS],
    ids=[row[1] for row in _DISPATCHED_SINGLE_ROWS],
)
async def test_single_typed_transmission_end_passes_without_credential(
    payload: dict[str, Any], label: str
) -> None:
    del label
    anonymous, keyed = await _anonymous_and_keyed("/api/v1/commands", payload)
    assert anonymous.response_status != 403
    assert anonymous.response_status == keyed.response_status
    assert anonymous.response_body == keyed.response_body


_GATED_SINGLE_ROWS: tuple[tuple[dict[str, Any], str], ...] = (
    ({"name": "ptt", "params": {"state": True}}, "ptt-state-true"),
    ({"name": "ptt", "params": {}}, "ptt-missing-state"),
    ({"name": "ptt_on", "params": {}}, "ptt-on"),
    ({"name": "transmit_on", "params": {}}, "transmit-on"),
    ({"name": "set_freq", "params": {"freq": 14_074_000}}, "set-freq"),
    ({"name": "get_freq", "params": {}}, "get-freq"),
    (
        {"name": "send_civ", "params": {"command": 0x1C, "sub": 0x01, "data": "00"}},
        "send-civ-tuner-off",
    ),
    (
        {"name": "send_civ", "params": {"command": 0x1C, "sub": 0x00, "data": "01"}},
        "send-civ-ptt-on",
    ),
)


@pytest.mark.parametrize(
    ("payload", "label"),
    [(row[0], row[1]) for row in _GATED_SINGLE_ROWS],
    ids=[row[1] for row in _GATED_SINGLE_ROWS],
)
async def test_single_keying_command_keeps_credential_refusal(
    payload: dict[str, Any], label: str
) -> None:
    del label
    srv, _ = _remote_server()
    try:
        await _arm_remote(srv)
        writer = await _post_json(srv, "/api/v1/commands", dict(payload))
        assert writer.response_status == 403
        assert writer.response_body["error"] == "controller_invalid"
        assert srv.command_queue.drain() == []
        srv._radio.send_civ_transaction.assert_not_awaited()
    finally:
        await _release(srv)


async def test_batch_all_transmission_end_passes_without_credential() -> None:
    payload = {
        "steps": [
            {"name": "ptt_off", "params": {}},
            {
                "type": "raw_civ_transaction",
                "command": 0x28,
                "sub": 0x00,
                "data": "00",
                "expect": "none",
            },
        ]
    }
    anonymous, keyed = await _anonymous_and_keyed("/api/v1/commands/batch", payload)
    assert anonymous.response_status != 403
    assert anonymous.response_status == keyed.response_status
    assert anonymous.response_body == keyed.response_body


async def test_batch_single_raw_transmission_end_executes_without_credential() -> None:
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
                        "command": 0x28,
                        "sub": 0x00,
                        "data": "00",
                        "expect": "none",
                    }
                ]
            },
        )
        assert writer.response_status == 200
        [step] = writer.response_body["results"]
        assert step["ok"] is True
        srv._radio.send_civ_transaction.assert_awaited_once_with(
            0x28, sub=0x00, data=b"\x00", expect="none", timeout=10.0
        )
    finally:
        await _release(srv)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "steps": [
                {"name": "ptt_off", "params": {}},
                {"name": "set_freq", "params": {"freq": 14_074_000}},
            ]
        },
        {"steps": []},
        {"steps": "ptt_off"},
        {"nope": True},
    ],
    ids=["mixed", "empty-steps", "non-list-steps", "no-steps"],
)
async def test_batch_without_full_transmission_end_keeps_refusal(
    payload: dict[str, Any],
) -> None:
    srv, _ = _remote_server()
    try:
        await _arm_remote(srv)
        writer = await _post_json(srv, "/api/v1/commands/batch", dict(payload))
        assert writer.response_status == 403
        assert writer.response_body["error"] == "controller_invalid"
        assert srv.command_queue.drain() == []
        srv._radio.send_civ_transaction.assert_not_awaited()
    finally:
        await _release(srv)


async def test_batch_case_variant_step_names_keep_refusal() -> None:
    srv, _ = _remote_server()
    try:
        await _arm_remote(srv)
        for name in ("PTT_OFF", " ptt_off", "Ptt_Off"):
            writer = await _post_json(
                srv,
                "/api/v1/commands/batch",
                {"steps": [{"name": name, "params": {}}]},
            )
            assert writer.response_status == 403
            assert writer.response_body["error"] == "controller_invalid"
        writer = await _post_raw(
            srv,
            "/api/v1/commands/batch",
            b'{"steps": [{"name": "ptt_off", "name": "set_freq", '
            b'"params": {"freq": 14074000}}]}',
        )
        assert writer.response_status == 403
        assert writer.response_body["error"] == "controller_invalid"
        assert srv.command_queue.drain() == []
    finally:
        await _release(srv)


async def test_single_duplicate_keys_keep_refusal() -> None:
    srv, _ = _remote_server()
    try:
        await _arm_remote(srv)
        for raw in (
            b'{"name":"ptt_off","name":"ptt_on"}',
            b'{"name":"ptt","params":{"state":false,"state":true}}',
        ):
            writer = await _post_raw(srv, "/api/v1/commands", raw)
            assert writer.response_status == 403
            assert writer.response_body["error"] == "controller_invalid"
        assert srv.command_queue.drain() == []
    finally:
        await _release(srv)


async def test_single_extra_top_level_state_still_only_dekeys() -> None:
    payload = {"name": "ptt", "params": {"state": False}, "state": True}
    anonymous, keyed = await _anonymous_and_keyed("/api/v1/commands", payload)
    assert anonymous.response_status != 403
    assert anonymous.response_status == keyed.response_status
    assert anonymous.response_body == keyed.response_body


async def test_single_malformed_body_keeps_todays_refusal() -> None:
    srv, _ = _remote_server()
    try:
        key, _ticket = await _arm_remote(srv)
        anonymous = await _post_raw(srv, "/api/v1/commands", b'{"name":')
        assert anonymous.response_status == 403
        assert anonymous.response_body["error"] == "controller_invalid"
        keyed = await _post_raw(
            srv,
            "/api/v1/commands",
            b'{"name":',
            headers={"x-rigplane-controller": key},
        )
        assert keyed.response_status == 400
        assert keyed.response_body["error"] == "invalid_json"
    finally:
        await _release(srv)


async def test_single_oversize_body_keeps_todays_refusal() -> None:
    from rigplane.web import server as web_server_mod

    srv, _ = _remote_server()
    try:
        key, _ticket = await _arm_remote(srv)
        big = json.dumps(
            {
                "name": "ptt_off",
                "params": {},
                "pad": "x" * (web_server_mod._MAX_POST_BODY + 1),  # noqa: SLF001
            }
        ).encode()
        anonymous = await _post_raw(srv, "/api/v1/commands", big)
        assert anonymous.response_status == 403
        assert anonymous.response_body["error"] == "controller_invalid"
        keyed = await _post_raw(
            srv,
            "/api/v1/commands",
            big,
            headers={"x-rigplane-controller": key},
        )
        assert keyed.response_status == 413
        assert keyed.response_body["error"] == "request_too_large"
    finally:
        await _release(srv)


async def test_keyed_set_freq_still_enqueues() -> None:
    srv, _ = _remote_server()
    captured: list[object] = []
    consumer = asyncio.create_task(
        _complete_ordered_commands(srv.command_queue, 1, captured)
    )
    try:
        key, _ticket = await _arm_remote(srv)
        writer = await _post_json(
            srv,
            "/api/v1/commands",
            {"name": "set_freq", "params": {"freq": 14_074_000}},
            headers={"x-rigplane-controller": key},
        )
    finally:
        await asyncio.wait_for(consumer, timeout=5.0)
        await _release(srv)
    assert writer.response_status == 200
    assert writer.response_body["ok"] is True
    assert len(captured) == 1


_PREDICATE_ROWS: tuple[tuple[str, dict[str, Any], bool, str], ...] = (
    ("ptt_off", {}, True, "ptt-off"),
    ("ptt", {"state": False}, True, "ptt-state-false"),
    (
        "send_civ",
        {"command": 0x1C, "sub": 0x00, "data": "00"},
        True,
        "send-civ-ptt-off",
    ),
    ("ptt", {"state": True}, False, "ptt-state-true"),
    ("ptt", {}, False, "ptt-missing-state"),
    ("ptt_on", {}, False, "ptt-on"),
    ("get_freq", {}, False, "get-freq"),
    (
        "send_civ",
        {"command": 0x1C, "sub": 0x01, "data": "00"},
        False,
        "send-civ-tuner-off",
    ),
)


@pytest.mark.parametrize(
    ("name", "params", "expected", "label"),
    [(row[0], row[1], row[2], row[3]) for row in _PREDICATE_ROWS],
    ids=[row[3] for row in _PREDICATE_ROWS],
)
def test_typed_transmission_end_predicate(
    name: str, params: dict[str, Any], expected: bool, label: str
) -> None:
    del label
    from rigplane.web.handlers.control import is_typed_transmission_end_command

    assert is_typed_transmission_end_command(name, params) is expected


async def _remote_authority() -> tuple[Safety, Any]:
    safety = Safety()
    authority = safety.authority()
    await authority.set_mode("remote")
    grant = authority.acquire("pro")
    authority.attach(grant["controller_key"], "primary", "native")
    return safety, authority


@pytest.mark.parametrize(
    ("name", "params", "label"),
    [
        ("ptt_off", {}, "ptt-off"),
        ("ptt", {"state": False}, "ptt-false"),
        ("send_civ", {"command": 0x1C, "sub": 0x00, "data": "00"}, "send-civ"),
        ("get_freq", {}, "get-freq-stays-exempt"),
    ],
    ids=["ptt-off", "ptt-false", "send-civ", "get-freq-stays-exempt"],
)
async def test_handler_exempt_names_skip_capture(
    name: str, params: dict[str, Any], label: str
) -> None:
    del label
    srv, _ = _remote_server()
    _safety, authority = await _remote_authority()
    try:
        await _arm_remote(srv)
        handler = srv._control_handler_for()  # noqa: SLF001
        handler._radio = SimpleNamespace(_controller_authority=authority)  # type: ignore[method-assign]
        handler._validate_controller_command(name, dict(params))  # noqa: SLF001
    finally:
        authority.revoke()
        await authority.settle()
        await _release(srv)


@pytest.mark.parametrize(
    ("name", "params", "label"),
    [
        ("ptt", {"state": True}, "ptt-true"),
        ("ptt", {}, "ptt-missing-state"),
        ("ptt_on", {}, "ptt-on"),
        ("set_freq", {"freq": 14_074_000}, "set-freq"),
    ],
    ids=["ptt-true", "ptt-missing-state", "ptt-on", "set-freq"],
)
async def test_handler_capturing_names_still_capture(
    name: str, params: dict[str, Any], label: str
) -> None:
    del label
    srv, _ = _remote_server()
    _safety, authority = await _remote_authority()
    try:
        await _arm_remote(srv)
        handler = srv._control_handler_for()  # noqa: SLF001
        handler._radio = SimpleNamespace(_controller_authority=authority)  # type: ignore[method-assign]
        with pytest.raises(ControllerError):
            handler._validate_controller_command(name, dict(params))  # noqa: SLF001
    finally:
        authority.revoke()
        await authority.settle()
        await _release(srv)
