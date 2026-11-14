"""MOR-4382: kept Icom LAN identity behaviours whose only tests were deleted.

PR #4556 deleted the identity-gate and hold-stream test modules. The LAN
``19 00`` read stays
information-only (``CoreRadio.probe_connection_identity`` publishes
``connection.identity`` and never gates readiness), and these four
behaviours the PR keeps lost their coverage. Each test below drives the
current single-owner path: the public ``connect()`` for the read, the
real ``_on_token_reply`` classifier, and the real ``_connect_once``
setup/teardown of the token callback.
"""

from __future__ import annotations

import asyncio
import struct
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest

from rigplane.core.radio_protocol import RadioIdentityStatus
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.runtime._control_phase import ControlPhaseRuntime
from rigplane.runtime.radio import IcomRadio
from rigplane_testkit.leaked_radio_guard import fake_connected

from test_radio import MockTransport
from test_radio_connect import (
    ConnectMockTransport,
    _build_conninfo,
    _ConnectOnceStubs,
)

_TOKEN = 0x5EC0DE01


def _kept_radio(*, expected_ids: tuple[str, ...]) -> tuple[IcomRadio, MockTransport]:
    """Real ``IcomRadio`` over a mock transport pair with a live-looking session.

    The tail of ``connect()`` (initial fetch, TX arming) is stubbed the way
    the work it follows needs; the identity read itself stays real except
    for the network reply, which each test scripts.
    """
    radio = IcomRadio("192.168.1.100", model="IC-7610", timeout=0.5)
    radio._profile = replace(radio._profile, expected_identity_ids=expected_ids)
    transport = MockTransport()
    transport._udp_transport = object()  # control session looks open
    radio._civ_transport = transport
    radio._ctrl_transport = transport
    radio._fetch_initial_state = AsyncMock()
    radio._arm_managed_tx = AsyncMock()
    return radio, transport


def _owner_lifecycle_double(radio: IcomRadio, *, connect: object) -> AsyncMock:
    """A session lifecycle whose teardown just parks the link as idle.

    The connect half is the test's own gated fake; the disconnect half
    mirrors it so the radio ends DISCONNECTED and the leak guard stays
    quiet, without dragging the control-phase teardown into a mock link.
    """

    async def _idle_disconnect() -> None:
        radio._conn_state = RadioConnectionState.DISCONNECTED

    lifecycle = AsyncMock()
    lifecycle.connect.side_effect = connect
    lifecycle.disconnect.side_effect = _idle_disconnect
    radio._session_lifecycle = lifecycle
    return lifecycle


def _token_answer(
    token: int, result: int, *, request_type: int = 0x05, reply_flag: int = 0x02
) -> bytes:
    """A 0x40 token packet with the fields ``_on_token_reply`` reads.

    The reply flag at 0x14, the request type at 0x15, our token at 0x1C
    and the verdict at ``_RENEWAL_RESPONSE_OFFSET`` decide; anything else
    in the packet is filler the classifier never looks at.
    """
    pkt = bytearray(0x40)
    pkt[0x14] = reply_flag
    pkt[0x15] = request_type
    struct.pack_into("<I", pkt, 0x1C, token)
    struct.pack_into("<I", pkt, ControlPhaseRuntime._RENEWAL_RESPONSE_OFFSET, result)
    return bytes(pkt)


async def test_two_connect_callers_share_one_identity_read() -> None:
    """Two concurrent ``connect()`` callers run ONE ``19 00`` read.

    ``CoreRadio.connect`` holds ``_connect_lock`` (radio.py): the second
    caller waits, then returns through the ``connected`` early-exit while
    the first caller's single probe is still in flight.
    """
    radio, _transport = _kept_radio(expected_ids=("98",))
    opened, answer = asyncio.Event(), asyncio.Event()
    entered = asyncio.Event()

    async def _gated_connect() -> None:
        await opened.wait()
        fake_connected(radio)
        radio._conn_state = RadioConnectionState.CONNECTED
        await radio.probe_connection_identity()

    async def _gated_read() -> bytes:
        entered.set()
        await answer.wait()
        return b"\x98"

    _owner_lifecycle_double(radio, connect=_gated_connect)
    radio._read_lan_identity_payload = AsyncMock(side_effect=_gated_read)
    calls = [asyncio.create_task(radio.connect()) for _ in range(2)]
    await asyncio.sleep(0)
    opened.set()
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert radio._read_lan_identity_payload.await_count == 1
    finally:
        answer.set()
        await asyncio.gather(*calls)
    assert radio._read_lan_identity_payload.await_count == 1
    identity = radio.connection_identity
    assert identity is not None
    assert identity.status is RadioIdentityStatus.VERIFIED
    assert radio.connected is True
    await radio.disconnect()


async def test_raw_answer_without_expected_metadata_is_unverified() -> None:
    """A ``19 00`` answer with no expected ids is UNVERIFIED, not a hold.

    ``_classify_lan_identity_answer`` (radio.py) publishes UNVERIFIED when
    the profile declares nothing to compare against; the link stays
    connected and the command gate (``_check_connected``, no identity
    fence) keeps passing.
    """
    radio, _transport = _kept_radio(expected_ids=())

    async def _owner_connect() -> None:
        fake_connected(radio)
        radio._conn_state = RadioConnectionState.CONNECTED
        await radio.probe_connection_identity()

    _owner_lifecycle_double(radio, connect=_owner_connect)
    radio._read_lan_identity_payload = AsyncMock(return_value=b"\x0b\x90")
    await radio.connect()

    identity = radio.connection_identity
    assert identity is not None
    assert identity.status is RadioIdentityStatus.UNVERIFIED
    assert identity.answered_id == "0B90"
    assert identity.answered_model is None
    assert radio.connected is True
    radio._check_connected()
    await radio.disconnect()


@pytest.mark.parametrize(
    ("answer", "holds"),
    [
        (_token_answer(_TOKEN, 0x00000000), True),
        (_token_answer(_TOKEN, 0xFFFFFFFF), False),
        (_token_answer(_TOKEN ^ 1, 0x00000000), None),
        (_token_answer(_TOKEN ^ 1, 0xFFFFFFFF), None),
        (_token_answer(_TOKEN, 0x00000000, request_type=0x01), None),
        (_token_answer(_TOKEN, 0x00000001), None),
        (_token_answer(_TOKEN, 0x00000000, reply_flag=0x01), None),
    ],
    ids=[
        "ok",
        "reject",
        "stale-token-ok",
        "stale-token-reject",
        "token-remove-answer",
        "unknown-result",
        "request-not-reply",
    ],
)
async def test_token_answer_classifier(answer: bytes, holds: bool | None) -> None:
    """The token-reply matrix: ok holds, reject drops, anything foreign is ignored.

    ``ControlPhaseRuntime._on_token_reply`` settles ``_renewal_waiter``
    only for a renewal reply carrying our token; ok also stamps
    ``_renewal_answered_at``. Five foreign shapes — stale token either
    way, a token-remove answer, an unknown result, a request echoed back —
    leave the waiter pending.
    """
    radio = IcomRadio("192.168.1.100", model="IC-7610")
    radio._token = _TOKEN
    phase = radio._control_phase
    waiter: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
    phase._renewal_waiter = waiter
    phase._on_token_reply(answer)
    if holds is None:
        assert not waiter.done()
        assert phase._renewal_answered_at is None
    else:
        assert waiter.result() is holds
        assert (phase._renewal_answered_at is not None) is holds


async def test_connect_routes_token_answers_to_the_probe_only_after_setup() -> None:
    """``_token_reply_callback`` is None during setup, set only after it.

    ``_connect_once`` clears the callback up front so the GUID/status
    exchange owns the control queue, then points it at ``_on_token_reply``
    once the session is live (``_control_phase.py``).
    """
    radio = IcomRadio("192.168.1.100", username="u", password="p", model="IC-7610")
    mt = ConnectMockTransport()
    radio._ctrl_transport = mt
    # Left set by an earlier session on the same transport object.
    mt._token_reply_callback = radio._control_phase._on_token_reply
    mt.queue_response(_build_conninfo())
    during_setup: list[object] = []
    real_receive_guid = radio._control_phase._receive_guid

    async def spy_receive_guid() -> bytes | None:
        during_setup.append(mt._token_reply_callback)
        return await real_receive_guid()

    with (
        _ConnectOnceStubs(radio),
        patch.object(
            radio._control_phase, "_receive_guid", side_effect=spy_receive_guid
        ),
    ):
        await radio._control_phase._connect_once()
    assert during_setup == [None]
    assert mt._token_reply_callback == radio._control_phase._on_token_reply
    await radio.disconnect()
