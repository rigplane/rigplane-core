"""ChannelBind 401/438: one nonce refresh inside the same attempt (MOR-4677).

Field evidence (Sentry RIGPLANE-PRO-C, phone-hotspot session 2026-11-22):
after a successful Allocate, Cloudflare TURN rejected ChannelBind with 401;
aioice's single retry (same credentials, rotated nonce) was rejected again
and ``TransactionFailed: STUN transaction failed (401)`` escaped
``request_with_retry``, failing the whole first connect attempt. A fresh
grant on the next attempt connected.

These tests drive the real aioice ``TurnClient`` (UDP) against a
small in-test STUN/TURN responder on loopback: no real network, no real
TURN server. The responder speaks just enough STUN framing (via aioice's
own ``stun`` codec) to Allocate and ChannelBind, with a scripted
401/438-then-accept sequence per test.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest

aioice_stun: Any = pytest.importorskip("aioice.stun", reason="aioice not installed")
aioice_turn: Any = pytest.importorskip("aioice.turn", reason="aioice not installed")

from rigplane.web.transport.ice_gathering import (  # noqa: E402
    install_ice_gathering_policy,
)

_REALM = "test-realm"
_PEER: tuple[str, int] = ("192.0.2.1", 9999)  # TEST-NET-1: bound, never contacted
_OP_TIMEOUT_S = 10.0


def _fresh_nonce(double: "_TurnDouble") -> bytes:
    double._nonce_seq += 1
    return f"nonce-{double._nonce_seq}".encode("ascii")


def _error(
    method: Any, transaction_id: bytes, code: int, reason: str, nonce: bytes | None
) -> Any:
    attributes: dict[str, Any] = {"ERROR-CODE": (code, reason), "REALM": _REALM}
    if nonce is not None:
        attributes["NONCE"] = nonce
    return aioice_stun.Message(
        message_method=method,
        message_class=aioice_stun.Class.ERROR,
        transaction_id=transaction_id,
        attributes=attributes,
    )


class _TurnDouble(asyncio.DatagramProtocol):
    """Scripted STUN/TURN responder: Allocate 401-once-then-accept, and a
    per-test ChannelBind error script (each entry one 401/438/403 with a
    fresh nonce, then accept). Every request is recorded as
    ``(method, username?, integrity?, nonce?)`` and every served bind-error
    nonce is kept, so tests assert the exact packet-level trace.
    """

    def __init__(self, bind_errors: list[int]) -> None:
        self._bind_errors = list(bind_errors)
        self._nonce_seq = 0
        self._allocated = False
        self._bind_error_no = 0
        self.drop_nonce_on_error: set[int] = set()
        self.requests: list[tuple[str, bool, bool, bytes | None]] = []
        self.bind_nonces: list[bytes] = []
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        request = aioice_stun.parse_message(data)
        attrs = request.attributes
        self.requests.append(
            (
                request.message_method.name,
                "USERNAME" in attrs,
                "MESSAGE-INTEGRITY" in attrs,
                attrs.get("NONCE"),
            )
        )
        assert self.transport is not None
        if request.message_method == aioice_stun.Method.ALLOCATE:
            if "USERNAME" not in attrs or self._allocated:
                response = _error(
                    request.message_method,
                    request.transaction_id,
                    401,
                    "Unauthorized",
                    _fresh_nonce(self),
                )
            else:
                self._allocated = True
                response = aioice_stun.Message(
                    message_method=request.message_method,
                    message_class=aioice_stun.Class.RESPONSE,
                    transaction_id=request.transaction_id,
                    attributes={
                        "XOR-MAPPED-ADDRESS": addr,
                        "XOR-RELAYED-ADDRESS": ("127.0.0.1", 19999),
                        "LIFETIME": 600,
                    },
                )
        elif request.message_method == aioice_stun.Method.CHANNEL_BIND:
            if self._bind_errors:
                self._bind_error_no += 1
                code = self._bind_errors.pop(0)
                reason = "Stale Nonce" if code == 438 else "Unauthorized"
                nonce = (
                    None
                    if self._bind_error_no in self.drop_nonce_on_error
                    else _fresh_nonce(self)
                )
                if nonce is not None:
                    self.bind_nonces.append(nonce)
                response = _error(
                    request.message_method,
                    request.transaction_id,
                    code,
                    reason,
                    nonce,
                )
            else:
                response = aioice_stun.Message(
                    message_method=request.message_method,
                    message_class=aioice_stun.Class.RESPONSE,
                    transaction_id=request.transaction_id,
                    attributes={},
                )
        else:  # REFRESH (allocation delete): always accept.
            response = aioice_stun.Message(
                message_method=request.message_method,
                message_class=aioice_stun.Class.RESPONSE,
                transaction_id=request.transaction_id,
                attributes={"LIFETIME": 0},
            )
        # aioice authenticates responses to authenticated TURN requests.
        if "MESSAGE-INTEGRITY" in attrs:
            response.add_message_integrity(
                aioice_turn.make_integrity_key(attrs["USERNAME"], _REALM, "pw")
            )
        self.transport.sendto(bytes(response), addr)

    def binds(self) -> list[tuple[str, bool, bool, bytes | None]]:
        """The recorded CHANNEL_BIND requests, in order."""
        return [rec for rec in self.requests if rec[0] == "CHANNEL_BIND"]


async def _double_with(bind_errors: list[int]) -> _TurnDouble:
    """A fresh double bound to an ephemeral loopback port."""
    loop = asyncio.get_running_loop()
    double = _TurnDouble(bind_errors=bind_errors)
    await loop.create_datagram_endpoint(lambda: double, local_addr=("127.0.0.1", 0))
    return double


async def _allocated_client(double: _TurnDouble) -> Any:
    """A TurnClient Allocate()d against ``double``; caller must delete()."""
    loop = asyncio.get_running_loop()
    assert double.transport is not None
    port = double.transport.get_extra_info("sockname")[1]
    _, client = await loop.create_datagram_endpoint(
        lambda: aioice_turn.TurnClientUdpProtocol(
            ("127.0.0.1", port),
            username="user",
            password="pw",
            lifetime=600,
            channel_refresh_time=500,
        ),
        remote_addr=("127.0.0.1", port),
    )
    async with asyncio.timeout(_OP_TIMEOUT_S):
        await client.connect()
    return client


async def _teardown(client: Any) -> None:
    with contextlib.suppress(Exception):
        async with asyncio.timeout(_OP_TIMEOUT_S):
            await client.delete()


async def test_channel_bind_single_401_recovered_by_aioice_itself() -> None:
    """Baseline: one 401 on ChannelBind is absorbed by aioice's own retry.

    Pins the behaviour the MOR-4677 fix builds on: the retry carries the
    fresh nonce from the 401 plus MESSAGE-INTEGRITY, and the bind succeeds.
    """
    double = await _double_with([401])
    client = await _allocated_client(double)
    try:
        async with asyncio.timeout(_OP_TIMEOUT_S):
            await client.channel_bind(0x4000, _PEER)
    finally:
        await _teardown(client)
    binds = double.binds()
    assert len(binds) == 2
    assert all(username and integrity for _, username, integrity, _ in binds)
    assert binds[1][3] == double.bind_nonces[0]


async def test_channel_bind_double_401_recovered_with_one_nonce_refresh() -> None:
    """MOR-4677: a second consecutive 401 (fresh nonce) is recovered inside
    the SAME attempt by refreshing the nonce once more and rebinding."""
    install_ice_gathering_policy()
    double = await _double_with([401, 401])
    client = await _allocated_client(double)
    try:
        async with asyncio.timeout(_OP_TIMEOUT_S):
            await client.channel_bind(0x4000, _PEER)
    finally:
        await _teardown(client)
    binds = double.binds()
    assert len(binds) == 3
    assert all(username and integrity for _, username, integrity, _ in binds)
    # The third bind carries the nonce from the SECOND 401, not the first.
    assert binds[1][3] == double.bind_nonces[0]
    assert binds[2][3] == double.bind_nonces[1]
    assert double.bind_nonces[0] != double.bind_nonces[1]


async def test_channel_bind_persistent_401_raises_after_single_refresh() -> None:
    """MOR-4677: when the server keeps 401ing, the failure still surfaces —
    after exactly ONE extra bind (no second retry loop)."""
    install_ice_gathering_policy()
    double = await _double_with([401, 401, 401, 401, 401])
    client = await _allocated_client(double)
    try:
        with pytest.raises(aioice_stun.TransactionFailed) as excinfo:
            async with asyncio.timeout(_OP_TIMEOUT_S):
                await client.channel_bind(0x4000, _PEER)
    finally:
        await _teardown(client)
    assert excinfo.value.response.attributes["ERROR-CODE"][0] == 401
    # Bounded: aioice's two attempts plus exactly one more normal bind cycle
    # (two attempts) after the single Core refresh — five scripted 401s are
    # available, only four are consumed, so there is no loop.
    assert len(double.binds()) == 4


async def test_channel_bind_double_438_recovered_with_one_nonce_refresh() -> None:
    """MOR-4677, 438 variant: two consecutive stale-nonce answers are
    recovered inside the same attempt the same way."""
    install_ice_gathering_policy()
    double = await _double_with([438, 438])
    client = await _allocated_client(double)
    try:
        async with asyncio.timeout(_OP_TIMEOUT_S):
            await client.channel_bind(0x4000, _PEER)
    finally:
        await _teardown(client)
    binds = double.binds()
    assert len(binds) == 3
    assert binds[2][3] == double.bind_nonces[1]


async def test_channel_bind_403_is_not_retried() -> None:
    """A non-recoverable error passes straight through: no refresh, no loop."""
    install_ice_gathering_policy()
    double = await _double_with([403])
    client = await _allocated_client(double)
    try:
        with pytest.raises(aioice_stun.TransactionFailed):
            async with asyncio.timeout(_OP_TIMEOUT_S):
                await client.channel_bind(0x4000, _PEER)
    finally:
        await _teardown(client)
    assert len(double.binds()) == 1


async def test_channel_bind_401_without_nonce_is_not_retried() -> None:
    """A 401 that carries no fresh NONCE cannot be refreshed: it surfaces
    instead of rebinding with a stale nonce."""
    install_ice_gathering_policy()
    double = await _double_with([401, 401])
    double.drop_nonce_on_error = {2}
    client = await _allocated_client(double)
    try:
        with pytest.raises(aioice_stun.TransactionFailed):
            async with asyncio.timeout(_OP_TIMEOUT_S):
                await client.channel_bind(0x4000, _PEER)
    finally:
        await _teardown(client)
    assert len(double.binds()) == 2
