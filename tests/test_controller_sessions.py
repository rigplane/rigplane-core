"""MOR-3534: multi-session controller mode, one TX holder and many clients.

Local mode opens one controller session per ``POST /api/v1/controller``. The
session key correlates a client's channels; it authenticates nobody. Managed
TX ownership follows the session, and the one hard rule is no double TX:
whoever keys holds the transmitter until release, everyone else is refused,
and anyone may still force TX off.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from test_managed_tx_authority import authority as managed_authority
from test_web_controller import request, station  # noqa: F401

from rigplane.core.exceptions import CommandRejectedError
from rigplane.core.state_pipeline_contracts import CommandIntent
from rigplane.runtime.controller_authority import (
    ControllerAuthority,
    ControllerError,
    ControllerTicket,
)
from rigplane.runtime.managed_tx_authority import ManagedTxAuthority
from rigplane.runtime.managed_tx_state import (
    ActuationOperation,
    ManagedTxIntentKind,
    ManagedTxOutcome,
)
from rigplane.web.handlers.control import ControlHandler

ACCEPTED = ManagedTxOutcome.ACCEPTED
REJECTED = ManagedTxOutcome.REJECTED


class Clock:
    def __init__(self) -> None:
        self.now = 10.0

    def __call__(self) -> float:
        return self.now


def stack() -> tuple[Clock, ControllerAuthority, ManagedTxAuthority, object]:
    """A controller over a managed authority on the fake effect lane."""
    clock = Clock()
    managed, *_, lane = managed_authority()

    async def cleanup() -> None:
        await managed.force_off()

    controller = ControllerAuthority(
        ready=lambda: True,
        cleanup=cleanup,
        clock=clock,
        release_session=managed.owner_disconnect,
    )
    managed.bind_controller(controller)
    return clock, controller, managed, lane


def open_session(
    controller: ControllerAuthority, name: str
) -> tuple[str, ControllerTicket]:
    key = controller.acquire("pro")["controller_key"]
    assert isinstance(key, str)
    return key, controller.attach(key, "primary", name)


async def close(controller: ControllerAuthority, managed: ManagedTxAuthority) -> None:
    controller.revoke()
    await controller.settle()
    off = await managed.submit_force_off()
    await off.wait_settlement()
    await managed.close()


async def test_local_acquire_opens_independent_sessions() -> None:
    _, controller, managed, _ = stack()
    try:
        first = controller.acquire("browser")
        second = controller.acquire("pro")
        assert set(first) == {"controller_key", "generation", "ttl_ms", "heartbeat_ms"}
        assert first["ttl_ms"] == 6000 and first["heartbeat_ms"] == 2000
        assert first["controller_key"] != second["controller_key"]
        a = controller.attach(first["controller_key"], "primary", "a")
        b = controller.attach(second["controller_key"], "primary", "b")
        assert not a.remote and not b.remote
        assert a.tx_owner and b.tx_owner and a.tx_owner != b.tx_owner
        assert first["controller_key"] not in a.tx_owner
        controller.validate(a)
        controller.validate(b)
        with pytest.raises(ControllerError, match="controller_busy"):
            controller.attach(first["controller_key"], "primary", "a-again")
        assert controller.status()["mode"] == "local"
    finally:
        await close(controller, managed)


async def test_two_sessions_keying_at_once_admit_exactly_one() -> None:
    _, controller, managed, lane = stack()
    try:
        _, a = open_session(controller, "a")
        _, b = open_session(controller, "b")
        ready = asyncio.get_running_loop().create_future()
        with controller.bind(a):
            first = managed.start_ptt_submission(True, a.tx_owner, ready=ready)
        with controller.bind(b):
            second = managed.start_ptt_submission(True, b.tx_owner, ready=ready)
        ready.set_result(None)
        results = await asyncio.gather(first, second)
        for result in results:
            await result.wait_settlement()
        assert sorted(result.outcome for result in results) == [ACCEPTED, REJECTED]
        assert [effect.operation for effect in lane.effects] == [
            ActuationOperation.PTT_ON
        ]
        winner = a if results[0].outcome is ACCEPTED else b
        assert managed.tx_holder_nowait() == winner.tx_owner
    finally:
        await close(controller, managed)


async def test_only_the_owner_session_unkeys_and_keys_as_itself() -> None:
    _, controller, managed, _ = stack()
    try:
        _, a = open_session(controller, "a")
        _, b = open_session(controller, "b")
        with controller.bind(a):
            assert await managed.ptt_down(a.tx_owner) is ACCEPTED
        with (
            controller.bind(b),
            pytest.raises(ControllerError, match="controller_tx_unsupported"),
        ):
            managed.start_ptt_submission(True, a.tx_owner)
        assert await managed.ptt_up(b.tx_owner) is REJECTED
        assert managed.tx_holder_nowait() == a.tx_owner
        assert await managed.ptt_up(a.tx_owner) is ACCEPTED
        assert managed.tx_holder_nowait() is None
    finally:
        await close(controller, managed)


async def test_owned_transmit_on_refuses_every_other_owner() -> None:
    _, controller, managed, lane = stack()
    try:
        _, a = open_session(controller, "a")
        _, b = open_session(controller, "b")
        with controller.bind(a):
            assert await managed.transmit_on() is ACCEPTED
        assert managed.tx_holder_nowait() == a.tx_owner
        with controller.bind(b):
            assert await managed.transmit_on() is REJECTED
        assert await managed.transmit_on() is REJECTED  # sessionless
        with controller.bind(a):
            assert await managed.transmit_on() is ACCEPTED  # the owner, idempotent
        assert [effect.operation for effect in lane.effects] == [
            ActuationOperation.TRANSMIT_ON
        ]
        assert managed.tx_holder_nowait() == a.tx_owner
    finally:
        await close(controller, managed)


@pytest.mark.parametrize("held", ["ptt", "transmit"])
async def test_session_expiry_releases_only_its_own_tx(held: str) -> None:
    clock, controller, managed, lane = stack()
    try:
        _, a = open_session(controller, "a")
        _, b = open_session(controller, "b")
        closed: list[str] = []
        controller.add_channel(a, lambda: closed.append("a"))
        controller.add_channel(b, lambda: closed.append("b"))
        with controller.bind(a):
            keyed = (
                managed.ptt_down(a.tx_owner) if held == "ptt" else managed.transmit_on()
            )
            assert await keyed is ACCEPTED
        assert managed.tx_holder_nowait() == a.tx_owner
        clock.now += 3
        controller.heartbeat(b)
        clock.now += 3.5  # past a's deadline, inside b's renewed one
        with pytest.raises(ControllerError, match="controller_invalid"):
            controller.validate(a)
        controller.validate(b)
        assert closed == ["a"]
        await controller.settle()
        assert managed.tx_holder_nowait() is None
        assert (await managed.snapshot()).state.intent.kind is ManagedTxIntentKind.RX
        assert lane.effects[-1].operation is ActuationOperation.FORCE_RECEIVE
    finally:
        await close(controller, managed)


async def test_primary_loss_fences_only_that_session() -> None:
    _, controller, managed, _ = stack()
    try:
        key_a, a = open_session(controller, "a")
        _, b = open_session(controller, "b")
        auxiliary = controller.attach(key_a, "auxiliary", "a-audio")
        assert auxiliary.tx_owner == a.tx_owner
        closed: list[str] = []
        controller.add_channel(auxiliary, lambda: closed.append("a-audio"))
        controller.add_channel(b, lambda: closed.append("b"))
        with controller.bind(a):
            assert await managed.ptt_down(a.tx_owner) is ACCEPTED
        controller.detach(a)
        assert closed == ["a-audio"]
        for ticket in (a, auxiliary):
            with pytest.raises(ControllerError):
                controller.validate(ticket)
        controller.validate(b)
        with pytest.raises(ControllerError, match="controller_invalid"):
            controller.attach(key_a, "primary", "late")
        await controller.settle()
        assert managed.tx_holder_nowait() is None
    finally:
        await close(controller, managed)


# ---------------------------------------------------------------------------
# Web surface: the HTTP grant, session-owned transmit_on, CW/VOX/TUNE refusal
# ---------------------------------------------------------------------------


async def _grant(server) -> str:
    response = await request(server, "POST", "/api/v1/controller", {"kind": "pro"})
    assert response.status == 201, response.payload
    return response.payload["controller_key"]


async def test_http_controller_post_always_succeeds_in_local_mode(station) -> None:  # noqa: F811
    server, _ = station
    grants = await asyncio.gather(
        *(
            request(server, "POST", "/api/v1/controller", {"kind": kind})
            for kind in ("browser", "pro", "browser")
        )
    )
    assert [grant.status for grant in grants] == [201, 201, 201]
    keys = {grant.payload["controller_key"] for grant in grants}
    assert len(keys) == 3
    status = await request(server, "GET", "/api/v1/controller")
    assert status.payload["mode"] == "local"
    assert not any(key in repr(status.payload) for key in keys)


async def test_http_transmit_on_is_owned_by_the_session(station, monkeypatch) -> None:  # noqa: F811
    server, managed = station

    async def admit(*, ready, submission, **_kwargs):
        ready.set_result(None)
        return await submission

    monkeypatch.setattr(server, "enqueue_managed_positive_tx", admit)
    server.command_queue.bind_connection_generation(lambda: 1)
    a, b = await _grant(server), await _grant(server)
    path = "/api/v1/managed-transmit/command"
    on = {"operation": "transmit_on"}
    assert (await request(server, "POST", path, on, key=a)).status == 202
    assert (await request(server, "POST", path, on, key=b)).status == 409
    assert (await request(server, "POST", path, on)).status == 409
    assert (await request(server, "POST", path, on, key=a)).status == 202
    stale = await request(server, "POST", path, on, key="0" * 64)
    assert (stale.status, stale.payload) == (403, {"error": "controller_invalid"})
    assert managed.tx_holder_nowait() is not None
    released = await request(server, "DELETE", "/api/v1/controller", key=a)
    assert released.status == 200
    assert (await managed.snapshot()).state.intent.kind is ManagedTxIntentKind.RX
    assert (await request(server, "POST", path, on, key=b)).status == 202
    assert await managed.force_off() is ACCEPTED


def _handler(server, managed, ticket: ControllerTicket | None = None) -> ControlHandler:
    radio = SimpleNamespace(_controller_authority=server._controller)
    return ControlHandler(
        SimpleNamespace(),
        radio,
        "test",
        "IC-7610",
        server=server,
        managed_tx_authority=managed,
        tx_owner=None if ticket is None else ticket.tx_owner,
    )


_TX_STARTS = [
    ("send_cw_text", {"text": "test"}),
    ("set_vox", {"on": True}),
    ("set_tuner_status", {"value": 2}),
]


@pytest.mark.parametrize(("name", "params"), _TX_STARTS)
async def test_non_holder_cw_vox_tune_are_refused_while_a_session_holds_tx(
    station,  # noqa: F811
    name: str,
    params: dict[str, object],
) -> None:
    server, managed = station
    assert server._managed_tx_authority() is managed
    controller = server._controller
    _, a = open_session(controller, "a")
    _, b = open_session(controller, "b")
    with controller.bind(a):
        assert await managed.ptt_down(a.tx_owner) is ACCEPTED
    with (
        controller.bind(b),
        pytest.raises(ControllerError, match="controller_tx_unsupported"),
    ):
        await _handler(server, managed, b)._enqueue_command(name, params)
    with pytest.raises(ControllerError, match="controller_tx_unsupported"):
        await _handler(server, managed)._enqueue_command(name, params)
    assert server.command_queue.drain() == []
    with controller.bind(a):
        _handler(server, managed, a)._validate_controller_command(name, params)
    assert await managed.ptt_up(a.tx_owner) is ACCEPTED
    with controller.bind(b):
        _handler(server, managed, b)._validate_controller_command(name, params)


@pytest.mark.parametrize(("name", "params"), _TX_STARTS)
async def test_session_cw_vox_tune_are_refused_while_a_socket_holds_ptt(
    station,  # noqa: F811
    name: str,
    params: dict[str, object],
) -> None:
    server, managed = station
    assert server._managed_tx_authority() is managed
    controller = server._controller
    _, b = open_session(controller, "b")
    socket = _handler(server, managed)
    assert await managed.ptt_down(socket._tx_owner) is ACCEPTED
    with (
        controller.bind(b),
        pytest.raises(ControllerError, match="controller_tx_unsupported"),
    ):
        await _handler(server, managed, b)._enqueue_command(name, params)
    socket._validate_controller_command(name, params)
    assert await managed.ptt_up(socket._tx_owner) is ACCEPTED


@pytest.mark.parametrize(("name", "params"), _TX_STARTS)
async def test_naming_the_holder_in_a_request_never_claims_its_tx(
    station,  # noqa: F811
    name: str,
    params: dict[str, object],
) -> None:
    """Ownership comes from server-known identity only, never a request field."""
    server, managed = station
    assert server._managed_tx_authority() is managed
    controller = server._controller
    _, a = open_session(controller, "a")
    _, b = open_session(controller, "b")
    with controller.bind(a):
        assert await managed.ptt_down(a.tx_owner) is ACCEPTED
    spoofed = {**params, "session_id": a.tx_owner}
    with (
        controller.bind(b),
        pytest.raises(ControllerError, match="controller_tx_unsupported"),
    ):
        await _handler(server, managed, b)._enqueue_command(name, spoofed)
    with pytest.raises(ControllerError, match="controller_tx_unsupported"):
        await _handler(server, managed)._enqueue_command(name, spoofed)
    # HTTP /api/v1/commands validates its intent on a fresh handler.
    intent = CommandIntent("spoof", name, spoofed, "http")
    with pytest.raises(ControllerError, match="controller_tx_unsupported"):
        await _handler(server, managed)._execute_intent(intent)
    assert server.command_queue.drain() == []
    # Nor can a request field release the holder's PTT.
    for ticket in (b, None):
        with pytest.raises(CommandRejectedError):
            await _handler(server, managed, ticket)._enqueue_managed_ptt(
                "ptt_off", {"session_id": a.tx_owner}, source="websocket"
            )
    assert managed.tx_holder_nowait() == a.tx_owner
    assert await managed.ptt_up(a.tx_owner) is ACCEPTED


async def test_http_transmit_on_for_a_session_ended_mid_request_is_forbidden(
    station,  # noqa: F811
    monkeypatch,
) -> None:
    server, _ = station

    async def ended(*, submission, **_kwargs):
        submission.cancel()
        raise ControllerError("controller_invalid")

    monkeypatch.setattr(server, "enqueue_managed_positive_tx", ended)
    server.command_queue.bind_connection_generation(lambda: 1)
    key = await _grant(server)
    response = await request(
        server,
        "POST",
        "/api/v1/managed-transmit/command",
        {"operation": "transmit_on"},
        key=key,
    )
    assert (response.status, response.payload) == (403, {"error": "controller_invalid"})
