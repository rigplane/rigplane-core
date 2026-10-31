"""MOR-3534: multi-session controller mode, one TX holder and many clients.

Local mode opens one controller session per ``POST /api/v1/controller``. The
session key correlates a client's channels; it authenticates nobody. Managed
TX ownership follows the session, and the one hard rule is no double TX:
whoever keys holds the transmitter until release, everyone else is refused,
and anyone may still force TX off.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_managed_tx_authority import (
    FakeClock,
    FakeConfigStore,
    FakeFence,
    FakeLane,
    FakeWakeup,
)
from test_managed_tx_authority import authority as managed_authority
from test_managed_tx_production_composition import LegacyRadio, RecordingActuator
from test_web_audio_tx_session import _pcm_tx_frame, _SessionLanRadio, _start_tx
from test_web_controller import Writer, audio_handler, request, station  # noqa: F401

from rigplane.core.exceptions import CommandRejectedError
from rigplane.core.state_pipeline_contracts import (
    CommandIntent,
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.runtime.controller_authority import (
    ControllerAuthority,
    ControllerError,
    ControllerTicket,
)
from rigplane.runtime.managed_tx_authority import ManagedTxAuthority
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.runtime.managed_tx_state import (
    ActuationOperation,
    ManagedTxIntentKind,
    ManagedTxOutcome,
)
from rigplane.web import server as server_module
from rigplane.web.handlers.control import ControlHandler

ACCEPTED = ManagedTxOutcome.ACCEPTED
REJECTED = ManagedTxOutcome.REJECTED


class Clock:
    def __init__(self) -> None:
        self.now = 10.0

    def __call__(self) -> float:
        return self.now


def stack(
    **limits: int,
) -> tuple[Clock, ControllerAuthority, ManagedTxAuthority, object]:
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
        **limits,
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
    # MOR-3537: break-in arms CW keying.
    ("set_break_in", {"mode": 1}),
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


# ---------------------------------------------------------------------------
# H2: TX audio pinned to the holder session; H4: per-session WebSocket caps
# ---------------------------------------------------------------------------


async def test_session_tx_audio_is_pinned_to_the_holder(station) -> None:  # noqa: F811
    server, managed = station
    assert server._managed_tx_authority() is managed
    controller = server._controller
    key_a, a = open_session(controller, "a")
    key_b, _ = open_session(controller, "b")
    radio = _SessionLanRadio()
    holder, other, sessionless = (
        audio_handler(server, radio, ticket)
        for ticket in (
            controller.attach(key_a, "auxiliary", "a-audio"),
            controller.attach(key_b, "auxiliary", "b-audio"),
            controller.capture(),
        )
    )
    for handler in (holder, other, sessionless):
        await _start_tx(handler)
    with controller.bind(a):
        assert await managed.ptt_down(a.tx_owner) is ACCEPTED
    await other._handle_tx_audio(_pcm_tx_frame(b"other"))
    await sessionless._handle_tx_audio(_pcm_tx_frame(b"sessionless"))
    await holder._handle_tx_audio(_pcm_tx_frame(b"holder"))
    assert radio.pushed == [b"holder"]
    assert other._tx_warn_counts["tx_gate_closed"] == 1
    assert sessionless._tx_warn_counts["tx_gate_closed"] == 1
    assert await managed.ptt_up(a.tx_owner) is ACCEPTED
    # A sessionless socket holds: sessionless audio is not pinned, but a
    # session's audio never feeds someone else's transmission.
    assert await managed.ptt_down("websocket-1") is ACCEPTED
    await other._handle_tx_audio(_pcm_tx_frame(b"other-2"))
    await sessionless._handle_tx_audio(_pcm_tx_frame(b"sessionless-2"))
    assert radio.pushed == [b"holder", b"sessionless-2"]
    assert await managed.ptt_up("websocket-1") is ACCEPTED
    for handler in (holder, other, sessionless):
        await handler._stop_tx(reason="test")


async def test_ws_caps_count_per_session_and_never_evict_the_holder(
    station,  # noqa: F811
    monkeypatch,
) -> None:
    server, managed = station
    assert server._managed_tx_authority() is managed
    controller = server._controller
    release = asyncio.Event()
    started: list[object] = []

    class Handler:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def run(self) -> None:
            started.append(self)
            await release.wait()

    monkeypatch.setattr(server_module, "ControlHandler", Handler)
    keys = {name: await _grant(server) for name in ("holder", "other")}
    channels: list[asyncio.Task[None]] = []

    async def connect(key: str | None, role: str = "auxiliary") -> None:
        headers = {"sec-websocket-key": "dGhlIHNhbXBsZSBub25jZQ=="}
        if key is not None:
            headers["sec-websocket-protocol"] = f"rigplane-controller-v1.{key}"
        before = len(started)
        channels.append(
            asyncio.create_task(
                server._handle_websocket(
                    asyncio.StreamReader(),
                    Writer(),
                    "/api/v1/ws",
                    headers,
                    {"controller_role": [role]},
                )
            )
        )
        for _ in range(100):
            await asyncio.sleep(0)
            if len(started) != before:
                return
        raise AssertionError("channel never registered")

    try:
        for name, key in keys.items():
            await connect(key, "primary")
        owners = {
            name: ticket.tx_owner
            for name, ticket in (
                (name, controller.credential(key)) for name, key in keys.items()
            )
        }
        with controller.bind(controller.credential(keys["holder"])):
            assert await managed.ptt_down(owners["holder"]) is ACCEPTED
        for _ in range(9):  # 10 channels in the holder's own session
            await connect(keys["holder"])
        for _ in range(8):  # 9 channels in another session
            await connect(keys["other"])
        for _ in range(10):  # 10 sessionless channels from the same IP
            await connect(None)
        counts = {
            scope: len(conns)
            for (scope, _path), conns in server._conn_manager._connections.items()
        }
        assert counts == {owners["holder"]: 10, owners["other"]: 9, "127.0.0.1": 9}
        controller.validate(controller.credential(keys["other"]))
        assert managed.tx_holder_nowait() == owners["holder"]
    finally:
        release.set()
        await asyncio.gather(*channels, return_exceptions=True)
        assert await managed.force_off() is ACCEPTED


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


def test_ws_caps_keep_the_control_channel_of_a_sessionless_ptt_holder() -> None:
    manager = server_module.ConnectionManager()
    holder = object()
    others = [object() for _ in range(9)]
    manager.register("127.0.0.1", "/api/v1/ws", holder, owner="websocket-1")  # type: ignore[arg-type]
    evicted = [
        manager.register("127.0.0.1", "/api/v1/ws", ws, holder="websocket-1")  # type: ignore[arg-type]
        for ws in others
    ]
    assert evicted[-1] == [others[0]]
    assert holder in manager._connections[("127.0.0.1", "/api/v1/ws")]


# Behaviour pins moved here from part 1 to keep it under the size ceiling.


async def test_any_client_may_force_tx_off() -> None:
    _, controller, managed, _ = stack()
    try:
        _, a = open_session(controller, "a")
        _, b = open_session(controller, "b")
        with controller.bind(a):
            assert await managed.transmit_on() is ACCEPTED
        with controller.bind(b):
            assert await managed.force_off() is ACCEPTED
        assert managed.tx_holder_nowait() is None
        assert (await managed.snapshot()).state.intent.kind is ManagedTxIntentKind.RX
    finally:
        await close(controller, managed)


async def test_sessionless_socket_keys_as_before_but_never_over_a_holder() -> None:
    _, controller, managed, _ = stack()
    try:
        _, a = open_session(controller, "a")
        with controller.bind(a):
            assert await managed.ptt_down(a.tx_owner) is ACCEPTED
        assert await managed.ptt_down("websocket-1") is REJECTED
        assert await managed.ptt_up(a.tx_owner) is ACCEPTED
        assert await managed.ptt_down("websocket-1") is ACCEPTED
        assert managed.tx_holder_nowait() == "websocket-1"
        assert await managed.ptt_up("websocket-1") is ACCEPTED
    finally:
        await close(controller, managed)


async def test_session_owner_tokens_are_not_sequential() -> None:
    _, controller, managed, _ = stack()
    try:
        owners = {open_session(controller, name)[1].tx_owner for name in "abc"}
        assert len(owners) == 3
        assert not {"controller-session-1", "controller-session-2"} & owners
    finally:
        await close(controller, managed)


# ---------------------------------------------------------------------------
# MOR-3537: a TX no owner keyed; the execution-time TX-start re-check
# ---------------------------------------------------------------------------


class FakeRadioTx:
    """The radio's PTT readback: TX observed at ``tx_at`` (``None`` is RX)."""

    def __init__(self) -> None:
        self.tx_at: float | None = None

    def radio_tx_since(self, since: float) -> bool:
        return self.tx_at is not None and self.tx_at > since


def radio_tx_authority() -> tuple[ManagedTxAuthority, FakeClock, FakeLane, FakeRadioTx]:
    """A managed authority over a fake radio PTT readback."""
    clock, lane, radio = FakeClock(), FakeLane(), FakeRadioTx()
    managed = ManagedTxAuthority(
        lane,
        FakeConfigStore(),
        FakeFence(),
        provider_generation=7,
        clock=clock,
        wakeup=FakeWakeup(),
        attempt_timeout_seconds=1.0,
        retry_delay_seconds=2.0,
        radio_tx=radio,
    )
    return managed, clock, lane, radio


async def test_a_tx_no_owner_keyed_refuses_keying_but_force_off_still_unkeys() -> None:
    managed, clock, lane, radio = radio_tx_authority()
    try:
        radio.tx_at = clock.now  # the operator keys at the radio
        with pytest.raises(ControllerError, match="radio_tx_unowned"):
            await managed.ptt_down("websocket-1")
        with pytest.raises(ControllerError, match="radio_tx_unowned"):
            await managed.transmit_on()
        assert await managed.ptt_up("websocket-1") is REJECTED
        assert lane.effects == []
        # The emergency release is never gated: it unkeys whoever keyed.
        assert await managed.force_off() is ACCEPTED
        assert [effect.operation for effect in lane.effects] == [
            ActuationOperation.FORCE_RECEIVE
        ]

        radio.tx_at = None  # the radio reports RX
        assert await managed.ptt_down("websocket-1") is ACCEPTED
        assert await managed.ptt_up("websocket-1") is ACCEPTED
        assert lane.effects[-2].operation is ActuationOperation.PTT_ON
    finally:
        await managed.close()


async def test_core_tx_the_radio_did_not_release_refuses_starts_not_force_off() -> None:
    """MOR-3921: the same single refusal code as any other TX the radio reports."""
    managed, clock, lane, radio = radio_tx_authority()
    try:
        assert await managed.ptt_down("websocket-1") is ACCEPTED
        assert await managed.ptt_up("websocket-1") is ACCEPTED  # the unkey is ACKed
        clock.now += 0.5
        # The radio still reports TX after Core's unkey.
        radio.tx_at = clock.now
        assert managed.radio_tx_refusal_nowait() == "radio_tx_unowned"
        with pytest.raises(ControllerError, match="radio_tx_unowned"):
            await managed.ptt_down("websocket-2")
        assert await managed.force_off() is ACCEPTED
        assert lane.effects[-1].operation is ActuationOperation.FORCE_RECEIVE
    finally:
        await managed.close()


async def test_a_tx_report_older_than_cores_release_does_not_refuse_keying() -> None:
    managed, clock, lane, radio = radio_tx_authority()
    try:
        assert await managed.ptt_down("websocket-1") is ACCEPTED
        clock.now += 0.5
        radio.tx_at = clock.now  # the radio reports the TX Core keyed
        clock.now += 0.5
        assert await managed.force_off() is ACCEPTED  # Core's own TX: released
        assert lane.effects[-1].operation is ActuationOperation.FORCE_RECEIVE
        # The radio's last report predates the release: no fresh TX report,
        # so keying is not refused.
        assert await managed.ptt_down("websocket-2") is ACCEPTED
        assert await managed.ptt_up("websocket-2") is ACCEPTED
    finally:
        await managed.close()


@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("send_cw_text", {"text": "test"}),
        ("set_vox", {"on": True}),
        ("set_break_in", {"mode": 1}),
    ],
)
async def test_a_tx_start_admitted_before_another_owner_keys_is_refused_when_run(
    station,  # noqa: F811
    monkeypatch,
    name: str,
    params: dict[str, object],
) -> None:
    """H3: the owner check runs again when the queued intent executes."""
    server, managed = station
    assert server._managed_tx_authority() is managed
    controller = server._controller
    _, a = open_session(controller, "a")
    _, b = open_session(controller, "b")
    handler = _handler(server, managed, b)
    admitted: list[CommandIntent] = []

    async def hold(intent: CommandIntent, *, executor: object = None) -> object:
        admitted.append(intent)
        return SimpleNamespace(executor_result=SimpleNamespace(details={}))

    monkeypatch.setattr(handler, "_command_service", SimpleNamespace(execute=hold))
    # Nobody holds TX: b's TX start is admitted. A client-sent owner is dropped.
    with controller.bind(b):
        await handler._enqueue_command(name, {**params, "_tx_owner": a.tx_owner})
    (intent,) = admitted
    assert intent.params["_tx_owner"] == b.tx_owner
    # a keys before b's intent runs.
    with controller.bind(a):
        assert await managed.ptt_down(a.tx_owner) is ACCEPTED
    # The shared executor runs it on a fresh handler with no identity of its own.
    with pytest.raises(ControllerError, match="controller_tx_unsupported"):
        await _handler(server, managed)._execute_intent(intent)
    assert server.command_queue.drain() == []
    assert managed.tx_holder_nowait() == a.tx_owner
    assert await managed.ptt_up(a.tx_owner) is ACCEPTED


@pytest.mark.parametrize(("name", "params"), _TX_STARTS)
async def test_a_tx_start_is_refused_while_the_radio_sends_a_tx_no_owner_keyed(
    station,  # noqa: F811
    name: str,
    params: dict[str, object],
) -> None:
    """CW text, VOX, TUNE and break-in never key over the operator's TX."""
    server, _ = station
    managed, clock, _lane, radio = radio_tx_authority()
    try:
        handler = _handler(server, managed)
        handler._validate_controller_command(name, params)  # radio RX: admitted
        radio.tx_at = clock.now  # the operator keys at the radio
        with pytest.raises(ControllerError, match="radio_tx_unowned"):
            await handler._enqueue_command(name, params)
        # An intent admitted before the operator keyed is refused when it runs.
        intent = CommandIntent("queued", name, dict(params), "websocket")
        with pytest.raises(ControllerError, match="radio_tx_unowned"):
            await _handler(server, managed)._execute_intent(intent)
        assert server.command_queue.drain() == []
        radio.tx_at = None  # the operator's TX ends
        handler._validate_controller_command(name, params)
    finally:
        await managed.close()


def _radio_reports(store: StateStore, transmitting: bool) -> None:
    """One PTT readback from the radio, observed now."""
    store.apply_current(
        Observation(
            path=FieldPath.global_("tx_state", "ptt"),
            value=transmitting,
            source=SourceMetadata(source="poll_response", provider="test"),
            timestamp_monotonic=time.monotonic(),
            max_age=60.0,
        )
    )


async def test_any_tx_the_radio_reports_after_cores_release_refuses_with_one_code(
    tmp_path: Path,
) -> None:
    """MOR-3921: no origin classification; force_off always unkeys (MOR-3561)."""
    actuator, radio = RecordingActuator(), LegacyRadio()
    composition = ManagedTxComposition(actuator, config_path=tmp_path / "tx.json")
    install_managed_tx_composition(radio, composition)
    store = StateStore()
    store.begin_provider_generation()
    await composition.transport_ready(radio)
    await composition.bind_state_store(store)
    authority = composition.authority

    def unkeys() -> int:
        return actuator.operations.count(ActuationOperation.FORCE_RECEIVE)

    try:
        _radio_reports(store, False)
        assert await authority.ptt_down("websocket-1") is ACCEPTED
        _radio_reports(store, True)  # the radio reports Core's TX
        assert await authority.ptt_up("websocket-1") is ACCEPTED  # unkey ACKed
        # A poll after the unkey still reads TX, with no RX in between (a
        # stale poll, or the radio kept Core's TX): no new key, same code.
        _radio_reports(store, True)
        assert authority.radio_tx_refusal_nowait() == "radio_tx_unowned"
        with pytest.raises(ControllerError, match="radio_tx_unowned"):
            await authority.ptt_down("websocket-2")
        sent = unkeys()
        assert await authority.force_off() is ACCEPTED
        assert unkeys() == sent + 1  # the emergency release still unkeys

        _radio_reports(store, False)  # the radio reports RX
        assert authority.radio_tx_refusal_nowait() is None
        _radio_reports(store, True)  # then the operator keys at the radio
        assert authority.radio_tx_refusal_nowait() == "radio_tx_unowned"
        with pytest.raises(ControllerError, match="radio_tx_unowned"):
            await authority.ptt_down("websocket-2")
        sent = unkeys()
        assert await authority.force_off() is ACCEPTED
        assert unkeys() == sent + 1  # force_off unkeys even the operator
    finally:
        await composition.shutdown(asyncio.Event())


# MOR-3557: per-IP cap on live controller sessions.


async def test_new_session_at_the_per_ip_cap_is_refused_with_its_code() -> None:
    _, controller, managed, _ = stack(max_sessions_per_client=3)
    try:
        for _ in range(3):
            controller.acquire("pro", "10.0.0.1")
        with pytest.raises(ControllerError) as refused:
            controller.acquire("pro", "10.0.0.1")
        assert (refused.value.code, refused.value.status) == (
            "controller_session_limit",
            429,
        )
        assert len(controller._sessions) == 3  # refused, nothing evicted
    finally:
        await close(controller, managed)


async def test_the_cap_never_ends_the_tx_holders_session() -> None:
    _, controller, managed, _ = stack(max_sessions_per_client=2)
    try:
        key = controller.acquire("pro", "10.0.0.1")["controller_key"]
        holder = controller.attach(key, "primary", "holder")
        controller.acquire("pro", "10.0.0.1")
        with controller.bind(holder):
            assert await managed.ptt_down(holder.tx_owner) is ACCEPTED
        for _ in range(5):
            with pytest.raises(ControllerError, match="controller_session_limit"):
                controller.acquire("pro", "10.0.0.1")
        controller.validate(holder)
        assert managed.tx_holder_nowait() == holder.tx_owner
        assert controller.is_session_owner(holder.tx_owner)
    finally:
        await close(controller, managed)


async def test_a_released_session_frees_capacity_for_its_ip() -> None:
    _, controller, managed, _ = stack(max_sessions_per_client=2)
    try:
        first = controller.acquire("pro", "10.0.0.1")["controller_key"]
        controller.acquire("pro", "10.0.0.1")
        with pytest.raises(ControllerError, match="controller_session_limit"):
            controller.acquire("pro", "10.0.0.1")
        await controller.release(first)
        controller.acquire("pro", "10.0.0.1")
    finally:
        await close(controller, managed)


async def test_an_expired_session_frees_capacity_for_its_ip() -> None:
    clock, controller, managed, _ = stack(max_sessions_per_client=1)
    try:
        controller.acquire("pro", "10.0.0.1")
        clock.now += ControllerAuthority.TTL_MS / 1000 + 1
        grant = controller.acquire("pro", "10.0.0.1")
        assert len(controller._sessions) == 1
        assert isinstance(grant["controller_key"], str)
    finally:
        await close(controller, managed)


async def test_the_cap_is_counted_per_ip() -> None:
    _, controller, managed, _ = stack(max_sessions_per_client=1)
    try:
        controller.acquire("pro", "10.0.0.1")
        controller.acquire("pro", "10.0.0.2")
        with pytest.raises(ControllerError, match="controller_session_limit"):
            controller.acquire("pro", "10.0.0.2")
        controller.acquire("pro")  # unknown peer is not counted
        controller.acquire("pro")
        # an unknown-peer session does not use up a known IP's allowance
        controller.acquire("pro", "10.0.0.3")
    finally:
        await close(controller, managed)


async def test_http_post_refuses_a_session_over_the_ip_cap(station) -> None:  # noqa: F811
    server, _ = station
    cap = server._config.max_controller_sessions_per_ip
    assert cap == 32
    server._controller._max_sessions_per_client = 2
    for _ in range(2):
        assert (
            await request(server, "POST", "/api/v1/controller", {"kind": "pro"})
        ).status == 201
    refused = await request(server, "POST", "/api/v1/controller", {"kind": "pro"})
    assert (refused.status, refused.payload) == (
        429,
        {"error": "controller_session_limit"},
    )
    other = await request(
        server, "POST", "/api/v1/controller", {"kind": "pro"}, peer="192.168.1.9"
    )
    assert other.status == 201


async def test_a_cap_below_one_is_rejected() -> None:
    with pytest.raises(ValueError):
        stack(max_sessions_per_client=0)
    with pytest.raises(ValueError):
        server_module.WebConfig(max_controller_sessions_per_ip=0)
