"""MOR-1014 (MOR-1016 PR 6): the rigctld PTT route through the supervisor.

rigctld is the second ingress onto a managed rig and the one an operator never
watches: WSJT-X keys and unkeys on its own schedule and reads a bare ``RPRT``
back. So the two things this route may not do are report success for a key that
never reached the wire, and take another owner's transmission down behind the
supervisor's back.

The owner identity is the per-connection ``rigctld-client-N`` id. It qualifies
for a lease under the same rule the Web session does (``managed_tx_ingress``):
the TCP server mints it once per accepted socket and releases it on that
socket's teardown, so the lease is always releasable. A per-request id would
not be, and neither is anything the read paths mint.

A real ``ManagedRadioRuntime`` over the real supervisor and the real effect
service drives every case, and the provider is the hand-rolled one from
``test_web_recovery_durable_off`` — the wire log is the whole claim, and a
scripted double would report whatever outcome it was told to while a
``MagicMock`` answers every ``getattr`` and so could never show the unmanaged
path staying unmanaged (nor a ``runtime_checkable`` probe failing on 3.12+,
gh-102433).

MOR-1175 is what makes the declined-unkey case a decision rather than a gap:
rigctld gets no privileged force-unkey, so a ``STALE`` release stays a no-op at
the supervisor and the rig-left-keyed corner is covered by the other owner's
teardown and the max key-down watchdog instead.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import pytest

from rigplane.core.tx_safety import (
    TxOutcome,
    TxOwner,
    TxPhase,
    TxSource,
)
from rigplane.profiles import resolve_radio_profile
from rigplane.rigctld import protocol as rigctld_protocol
from rigplane.rigctld.contract import HamlibError, RigctldCommand, RigctldConfig
from rigplane.rigctld.handler import RigctldHandler
from rigplane.rigctld.server import RigctldServer
from rigplane.runtime.managed_radio_runtime import ManagedRadioRuntime
from rigplane.runtime.managed_tx_effect_service import managed_tx_effect_service
from test_web_recovery_durable_off import _Provider

_SESSION = "rigctld-client-1"
_MINE = TxOwner(TxSource.RIGCTLD, _SESSION)
_OTHER = TxOwner(TxSource.WEBSOCKET, "ws-1")
_KEY_WIRE = ["ptt(on)", "read_ptt"]
_UNKEY_WIRE = ["ptt(off)", "read_ptt"]


class _Radio:
    """Duck-typed radio recording every *legacy* (unsupervised) PTT write.

    Deliberately not a ``MagicMock``: one satisfies a ``runtime_checkable``
    protocol on 3.11 but not on 3.12+ (gh-102433), and it would answer the
    ``managed_tx`` probe on both, so the unmanaged case could never be shown.
    The member is assigned only when there is a supervisor, which is what makes
    the unmanaged instance genuinely memberless.
    """

    def __init__(self, managed_tx: object | None = None) -> None:
        self.profile = resolve_radio_profile(model="IC-7610")
        self.capabilities: set[str] = set()
        self.legacy_writes: list[bool] = []
        if managed_tx is not None:
            self.managed_tx = managed_tx

    async def set_ptt(self, on: bool) -> None:
        self.legacy_writes.append(on)


class _Managed:
    """A managed rig plus the two views the assertions need: wire and policy."""

    def __init__(
        self, runtime: ManagedRadioRuntime, wire: list[str], provider: _Provider
    ) -> None:
        self.runtime, self.wire, self.provider = runtime, wire, provider
        self.radio = _Radio(runtime)
        self.handler = RigctldHandler(self.radio, RigctldConfig())  # type: ignore[arg-type]

    @property
    def owner(self) -> TxOwner | None:
        return self.runtime.tx_snapshot.owner


_MakeManaged = Callable[..., Awaitable[_Managed]]


@pytest.fixture
async def managed() -> AsyncIterator[_MakeManaged]:
    """Build managed rigs and retire their watchdog tickers afterwards.

    ``request_on`` arms a real 0.25 s ticker task; shutting the runtime down is
    what keeps it from outliving the test's event loop. Nothing here waits on
    that clock — every case settles inside the awaits it makes.
    """
    runtimes: list[ManagedRadioRuntime] = []

    async def _make(
        *,
        keyed_by: TxOwner | None = None,
        provider_factory: Callable[[list[str]], _Provider] = _Provider,
    ) -> _Managed:
        wire: list[str] = []
        provider = provider_factory(wire)
        runtime = ManagedRadioRuntime(
            "rigctld-test",
            service_factory=managed_tx_effect_service,
            provider_lifecycle=provider,
        )
        runtimes.append(runtime)
        await runtime.replace_provider(ready=True)
        await runtime.request_fresh_ptt()  # seeds the OFF ``request_on`` demands
        if keyed_by is not None:
            assert (await runtime.request_on(keyed_by)).outcome is TxOutcome.ACCEPTED
        wire.clear()
        return _Managed(runtime, wire, provider)

    yield _make

    async def _noop() -> None:
        return None

    for runtime in runtimes:
        await runtime.shutdown(release_provider=_noop)


def _ptt(value: str) -> RigctldCommand:
    return RigctldCommand(short_cmd="T", long_cmd="set_ptt", args=(value,), is_set=True)


def _warned(caplog: pytest.LogCaptureFixture, phrase: str) -> bool:
    """Whether a warning carries ``phrase``.

    Every refusal branch logs, so the phrase — not the word "managed" — is what
    tells a declined unkey apart from a rejected key apart from an ownerless one.
    """
    return any(
        record.levelno >= logging.WARNING and phrase in record.getMessage()
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# The key
# ---------------------------------------------------------------------------


async def test_a_key_takes_a_lease_under_this_connection_id(
    managed: _MakeManaged,
) -> None:
    """The lease is held by the rigctld connection, not by a request."""
    rig = await managed()

    resp = await rig.handler.execute(_ptt("1"), session_id=_SESSION)

    assert resp.error == HamlibError.OK
    assert rig.wire == _KEY_WIRE
    assert rig.owner == _MINE
    assert rig.runtime.tx_snapshot.phase is TxPhase.KEYED
    # The whole point: a managed rig is never keyed by the raw write.
    assert rig.radio.legacy_writes == []


@pytest.mark.parametrize("declined_by", ["busy", "not_ready"])
async def test_a_refused_key_answers_rprt_and_never_reaches_the_rig(
    managed: _MakeManaged, caplog: pytest.LogCaptureFixture, declined_by: str
) -> None:
    """A key the supervisor declines must not come back as ``RPRT 0``.

    Nothing reached the wire, so reporting success would leave WSJT-X sending
    audio into a rig it never keyed — and, on the next unkey, releasing a lease
    it never held.
    """
    rig = await managed(keyed_by=_OTHER if declined_by == "busy" else None)
    if declined_by == "not_ready":
        await rig.runtime.set_provider_ready(ready=False)

    with caplog.at_level(logging.WARNING):
        resp = await rig.handler.execute(_ptt("1"), session_id=_SESSION)

    assert resp.error == HamlibError.ERJCTED
    assert rig.wire == []
    assert rig.radio.legacy_writes == []
    assert rig.owner != _MINE
    assert _warned(caplog, "rejected PTT ON")


# ---------------------------------------------------------------------------
# The unkey
# ---------------------------------------------------------------------------


async def test_an_unkey_gives_this_session_lease_back_and_reaches_the_rig(
    managed: _MakeManaged,
) -> None:
    """The ordinary case: our lease, released through the facade."""
    rig = await managed()
    await rig.handler.execute(_ptt("1"), session_id=_SESSION)
    rig.wire.clear()

    resp = await rig.handler.execute(_ptt("0"), session_id=_SESSION)

    assert resp.error == HamlibError.OK
    assert rig.wire == _UNKEY_WIRE
    assert rig.owner is None
    assert rig.runtime.tx_snapshot.phase is TxPhase.IDLE
    assert rig.radio.legacy_writes == []


async def test_a_declined_unkey_reports_ok_and_writes_nothing(
    managed: _MakeManaged, caplog: pytest.LogCaptureFixture
) -> None:
    """Someone else's lease is not rigctld's to break (MOR-1175).

    Two claims in one, and both are the ratified policy rather than an
    accident. No write of any kind: rigctld has no privileged force, so
    escalating a ``STALE`` release into a raw ``set_ptt(False)`` would end
    another owner's over behind the supervisor's back. And ``RPRT 0``: the
    command was processed and declined by policy, while WSJT-X reads a nonzero
    RPRT mid-sequence as a hard rig-control failure.
    """
    rig = await managed(keyed_by=_OTHER)

    with caplog.at_level(logging.WARNING):
        resp = await rig.handler.execute(_ptt("0"), session_id=_SESSION)

    assert resp.error == HamlibError.OK
    assert rig.wire == []
    assert rig.radio.legacy_writes == []
    assert rig.owner == _OTHER  # the foreign lease is untouched
    assert _warned(caplog, "declined the unkey")


# ---------------------------------------------------------------------------
# The two fall-throughs
# ---------------------------------------------------------------------------


async def test_an_unmanaged_radio_keeps_the_legacy_write_both_ways() -> None:
    """Legacy unmanaged backends (serial/USB Icom, Yaesu CAT, rigctld-client):
    byte-identical to what rigctld always sent."""
    radio = _Radio()
    handler = RigctldHandler(radio, RigctldConfig())  # type: ignore[arg-type]

    key = await handler.execute(_ptt("1"), session_id=_SESSION)
    unkey = await handler.execute(_ptt("0"), session_id=_SESSION)

    assert (key.error, unkey.error) == (HamlibError.OK, HamlibError.OK)
    assert radio.legacy_writes == [True, False]


async def test_an_ownerless_key_is_refused_and_an_ownerless_unkey_is_not(
    managed: _MakeManaged, caplog: pytest.LogCaptureFixture
) -> None:
    """The asymmetry, on the one path that can reach a managed rig ownerless.

    No session id means no identity a lease could be released against, so the
    key would be unsupervised — refused. The unkey is never refused for the
    same reason: an unkey turned away for lacking an owner strands a keyed
    transmitter, which is strictly worse than the unsupervised de-key it would
    be preventing.
    """
    rig = await managed()

    with caplog.at_level(logging.WARNING):
        key = await rig.handler.execute(_ptt("1"))
        unkey = await rig.handler.execute(_ptt("0"))

    assert key.error == HamlibError.EACCESS
    assert rig.wire == []  # refused before anything was written
    assert rig.owner is None
    assert _warned(caplog, "no releasable owner")

    assert unkey.error == HamlibError.OK
    assert rig.radio.legacy_writes == [False]


# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------


async def _settle(predicate: Callable[[], bool], *, what: str) -> None:
    """Await a real socket teardown, bounded. Not a clock: no case waits on one."""
    for _ in range(400):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"{what} never happened")


async def test_a_dropped_connection_hands_the_lease_to_the_next_client(
    managed: _MakeManaged,
) -> None:
    """A WSJT-X that dies mid-over must not park the rig on the watchdog.

    Driven over the real ``_handle_client`` loop, the real protocol module, the
    real handler and a real socket, because the claim is about what the *server*
    does when a connection ends — the one thing a handler-level call could only
    assert about itself.
    """
    rig = await managed()
    server = RigctldServer(
        rig.radio,  # type: ignore[arg-type]
        RigctldConfig(),
        _protocol=rigctld_protocol,
        _handler=rig.handler,
    )
    tcp = await asyncio.start_server(
        server._handle_client,  # noqa: SLF001
        host="127.0.0.1",
        port=0,
    )
    port = int(tcp.sockets[0].getsockname()[1])
    live: list[asyncio.StreamWriter] = []

    async def _key() -> tuple[bytes, asyncio.StreamWriter]:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        live.append(writer)
        writer.write(b"T 1\n")
        await writer.drain()
        return await reader.readline(), writer

    async def _drop(writer: asyncio.StreamWriter) -> None:
        live.remove(writer)
        writer.close()
        await writer.wait_closed()

    try:
        answer, first = await _key()
        assert answer == b"RPRT 0\n"
        assert rig.owner == TxOwner(TxSource.RIGCTLD, "rigctld-client-1")

        await _drop(first)  # the operator's laptop sleeps mid-transmission
        await _settle(lambda: rig.owner is None, what="the lease handback")
        assert rig.wire[-2:] == _UNKEY_WIRE

        # ``RPRT -9`` here would mean the dead client's lease is still standing.
        answer, second = await _key()
        assert answer == b"RPRT 0\n"
        assert rig.owner == TxOwner(TxSource.RIGCTLD, "rigctld-client-2")
        await _drop(second)
        await _settle(lambda: rig.owner is None, what="the second handback")
    finally:
        # Every accepted socket must be shut before ``wait_closed``: since 3.12
        # it waits on the handlers too, so a client left open by a failing
        # assertion would hang the teardown rather than report the failure.
        for writer in live:
            writer.close()
        for writer in live:
            await writer.wait_closed()
        tcp.close()
        await tcp.wait_closed()


# ---------------------------------------------------------------------------
# Session loss: every way a connection can end
# ---------------------------------------------------------------------------


class _Gated(_Provider):
    """A provider whose first write in one direction parks until released.

    The only way to hold a rigctld session inside an in-flight PTT write long
    enough to cancel it. One direction at a time: gating the key asks what a
    cancelled key may still do afterwards, gating the unkey asks whether the
    teardown handback survives being cancelled, and each leaves the other
    direction free to reach the wire so the answer is readable there.
    """

    def __init__(self, log: list[str], *, direction: bool) -> None:
        super().__init__(log)
        self.direction = direction
        self.reached = asyncio.Event()
        self.gate = asyncio.Event()

    async def _write_managed_ptt(self, generation: int, on: bool) -> None:
        if on is self.direction and not self.reached.is_set():
            self.reached.set()
            await self.gate.wait()
        await super()._write_managed_ptt(generation, on)


def _count_releases(handler: RigctldHandler) -> list[str]:
    """Record every teardown release the server drives, in order.

    The count is half the claim — ``[]`` is a stranded lease and two entries is
    a second release path nobody audited — and the session id in it is the
    other half: a per-request id here could never match the lease that was
    taken, so the rig would stay keyed with the count still reading 1.
    """
    log: list[str] = []
    real = handler.release_session_tx

    async def _counting(session_id: str) -> None:
        log.append(session_id)
        await real(session_id)

    handler.release_session_tx = _counting  # type: ignore[method-assign]
    return log


@asynccontextmanager
async def _serving(
    rig: _Managed, **overrides: object
) -> AsyncIterator[tuple[RigctldServer, int]]:
    """A real listener over the real accept path, on an ephemeral port.

    ``_accept_client`` rather than ``_handle_client`` because the cancellation
    and shutdown cases are about the task registry it fills: ``stop()`` has
    nothing to cancel if the connection was never registered there.
    """
    server = RigctldServer(
        rig.radio,  # type: ignore[arg-type]
        RigctldConfig(**overrides),  # type: ignore[arg-type]
        _protocol=rigctld_protocol,
        _handler=rig.handler,
    )
    tcp = await asyncio.start_server(
        server._accept_client,  # noqa: SLF001
        host="127.0.0.1",
        port=0,
    )
    # Handing the listener over is what puts ``stop()``'s own shutdown ordering
    # under test rather than a listener the test closes itself.
    server._server = tcp  # noqa: SLF001
    try:
        yield server, int(tcp.sockets[0].getsockname()[1])
    finally:
        await asyncio.wait_for(server.stop(), timeout=_SHUTDOWN_BUDGET)


async def _keyed_client(port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Connect, key, and prove the lease is this connection's before losing it."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"T 1\n")
    await writer.drain()
    assert await reader.readline() == b"RPRT 0\n"
    return reader, writer


# Well clear of a real teardown, nowhere near ``client_timeout``: the point of
# the bound is that shutdown must never be paid for out of the idle clock.
_SHUTDOWN_BUDGET = 10.0
_IDLE_CLOCK = 0.25


@pytest.mark.parametrize(
    "loss", ["eof", "quit", "reset", "idle_timeout", "cancellation", "shutdown"]
)
async def test_every_way_a_session_ends_hands_the_lease_back_exactly_once(
    managed: _MakeManaged, loss: str
) -> None:
    """One release per lost session, on every path out of the session loop.

    Six ways a WSJT-X connection ends and one guarantee: the lease goes back
    once, under the id it was taken with. The paths are not variations on a
    theme — EOF and ``q`` leave the loop, a reset and a cancellation unwind it,
    the idle clock breaks it, and shutdown reaches in from outside — and only
    the ``finally`` covers all six. Anything narrower strands a transmitter on
    the paths it misses; anything that fires twice is a release path nobody
    audited, and the second one carries a spent lease.
    """
    rig = await managed()
    releases = _count_releases(rig.handler)

    async with _serving(
        rig,
        client_timeout=_IDLE_CLOCK if loss == "idle_timeout" else 300.0,
    ) as (server, port):
        reader, writer = await _keyed_client(port)
        assert rig.owner == _MINE
        rig.wire.clear()

        try:
            if loss == "eof":
                writer.close()
                await writer.wait_closed()
            elif loss == "quit":
                writer.write(b"q\n")
                await writer.drain()
            elif loss == "reset":
                # SO_LINGER 0 makes close() send an RST, so the server unwinds
                # through ``ConnectionResetError`` instead of a clean EOF.
                writer.get_extra_info("socket").setsockopt(
                    socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
                )
                writer.close()
            elif loss == "idle_timeout":
                pass  # the server's own idle clock ends it
            elif loss == "cancellation":
                for task in list(server._client_tasks):  # noqa: SLF001
                    task.cancel()
            else:
                # Bounded because the failure mode is a hang, not a wrong
                # value: a stop() that waits the listener out before retiring
                # its sessions blocks for the whole idle clock, with the rig
                # still keyed for every second of it.
                await asyncio.wait_for(server.stop(), timeout=_SHUTDOWN_BUDGET)

            await _settle(lambda: rig.owner is None, what=f"the {loss} handback")
        finally:
            writer.close()

    assert releases == [_SESSION]
    assert rig.wire == _UNKEY_WIRE
    assert rig.radio.legacy_writes == []


async def test_a_cancelled_key_cannot_reach_the_rig_once_the_session_is_gone(
    managed: _MakeManaged,
) -> None:
    """A key cancelled mid-flight must not land behind its own release.

    The ordering is the whole claim. The session's ``finally`` releases the
    lease, so any part of the cancelled ``PTT ON`` still able to run afterwards
    keys a rig that has no owner left to unkey it — the lease is spent, the
    watchdog retired with it, and nothing in the product is watching. Detaching
    the command from the connection that raised it (spawning it, shielding it
    so the socket loop stays responsive) is exactly how that happens, and it is
    what this rules out: the last thing on the wire has to be the unkey.
    """
    rig = await managed(provider_factory=lambda wire: _Gated(wire, direction=True))
    gate = rig.provider
    assert isinstance(gate, _Gated)

    async with _serving(rig) as (server, port):
        _reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(b"T 1\n")
            await writer.drain()
            await _settle(gate.reached.is_set, what="the key reaching the wire")

            for task in list(server._client_tasks):  # noqa: SLF001
                task.cancel()
            gate.gate.set()  # anything still holding the key may proceed now

            await _settle(lambda: rig.owner is None, what="the handback")
            # Give a detached key every chance to land before reading the wire.
            for _ in range(20):
                await asyncio.sleep(0.005)
        finally:
            writer.close()

    assert rig.wire[-2:] == _UNKEY_WIRE
    assert "ptt(on)" not in rig.wire[rig.wire.index("ptt(off)") :]
    assert rig.runtime.tx_snapshot.phase is not TxPhase.KEYED
    assert rig.radio.legacy_writes == []


async def test_shutdown_cannot_walk_away_from_a_handback_it_interrupted(
    managed: _MakeManaged,
) -> None:
    """``stop()`` may cancel a session; it may not cancel the lease coming back.

    The window is small and entirely real — the intermittent one the parametrised
    case above hits by luck: a session is already inside its teardown release
    when shutdown cancels it. Every other step of a teardown may be abandoned
    there; this one may not. Dropping it leaves the lease standing with the
    server gone and no client left able to unkey, and nothing behind it: the
    release obligation stays unsettled at ``RELEASE_REQUIRED`` and no watchdog
    re-arms, because beginning a release clears the max key-down deadline. It
    also leaves the socket unclosed, which since 3.12 is on its own enough to
    make ``stop()`` wait for a connection that will never come back.
    """
    rig = await managed(provider_factory=lambda wire: _Gated(wire, direction=False))
    gate = rig.provider
    assert isinstance(gate, _Gated)
    releases = _count_releases(rig.handler)

    async with _serving(rig) as (server, port):
        _reader, writer = await _keyed_client(port)
        assert rig.owner == _MINE
        rig.wire.clear()

        writer.close()  # EOF: the session drops into its teardown release
        await _settle(gate.reached.is_set, what="the handback reaching the wire")

        stopping = asyncio.ensure_future(server.stop())
        await asyncio.sleep(0)  # far enough for stop() to cancel the session
        gate.gate.set()  # whatever survived the cancellation may finish now
        await asyncio.wait_for(stopping, timeout=_SHUTDOWN_BUDGET)

    assert releases == [_SESSION]
    assert rig.owner is None
    assert rig.wire == _UNKEY_WIRE
    assert rig.radio.legacy_writes == []


async def test_a_teardown_cancelled_twice_still_lets_go_of_the_connection(
    managed: _MakeManaged,
) -> None:
    """The handback survives one cancellation by design, not two — the socket must.

    A hard shutdown does not cancel politely once, and the second cancellation
    does end the handback: awaiting the shielded task makes it this session's
    ``_fut_waiter`` again, so the lease that was on its way back stays held —
    stuck at ``RELEASE_REQUIRED`` with an unfinished WRITE_OFF and no watchdog
    to fall back on, since beginning a release clears the max key-down deadline
    and ``tick`` re-arms it only while no release is pending. That is the
    honest boundary, it belongs to the uncertain-shutdown surface (MOR-1015),
    and it is not what this pins. What the second cancellation may not do is
    take the accepted
    connection down with it: since 3.12 an unclosed one keeps the listener's
    ``wait_closed()`` waiting for a client that is already gone, so the cost of
    skipping that close is not a logged warning but a ``stop()`` that never
    returns — and a rigctld that will not stop is a rigctld nobody can take off
    the air.
    """
    rig = await managed(provider_factory=lambda wire: _Gated(wire, direction=False))
    gate = rig.provider
    assert isinstance(gate, _Gated)
    releases = _count_releases(rig.handler)

    async with _serving(rig) as (server, port):
        _reader, writer = await _keyed_client(port)
        assert rig.owner == _MINE
        rig.wire.clear()

        writer.close()  # EOF: the session drops into its teardown release
        await _settle(gate.reached.is_set, what="the handback reaching the wire")

        sessions = list(server._client_tasks)  # noqa: SLF001
        for _ in range(200):
            if all(task.done() for task in sessions):
                break
            for task in sessions:
                task.cancel()
            await asyncio.sleep(0.001)
        assert all(task.done() for task in sessions), "the session never ended"

        gate.gate.set()  # nothing is parked on it now; belt and braces
        await asyncio.wait_for(server.stop(), timeout=_SHUTDOWN_BUDGET)

    assert releases == [_SESSION]  # attempted once, from the one path there is
    assert rig.radio.legacy_writes == []


async def test_teardown_shuts_the_door_before_it_starts_retiring_sessions(
    managed: _MakeManaged,
) -> None:
    """A server that has begun shutting down may not accept another client.

    The other half of ``stop()``'s ordering, and the half that is invisible if
    you only watch the sessions that already exist: the listener has to close
    *before* the cancellations go out, not after. Cancelling first leaves the
    door open for the whole cancel-and-gather window — which is exactly as long
    as the slowest handback — and a WSJT-X arriving in it is accepted into a
    server that is going away. Its session was never in the set that got
    cancelled, so it can key a rig nothing will ever unkey, and its socket then
    holds ``wait_closed()`` open behind it. Refusing the connection is the only
    answer that leaves no such session.
    """
    rig = await managed(provider_factory=lambda wire: _Gated(wire, direction=False))
    gate = rig.provider
    assert isinstance(gate, _Gated)

    async with _serving(rig) as (server, port):
        _reader, writer = await _keyed_client(port)
        assert rig.owner == _MINE

        # Park the departing session inside its handback: that is what holds
        # the cancel-and-gather window open long enough for a client to race in.
        writer.close()
        await _settle(gate.reached.is_set, what="the handback reaching the wire")

        stopping = asyncio.ensure_future(server.stop())
        await asyncio.sleep(0)  # stop() runs to its first await, sessions parked

        with pytest.raises(OSError):  # ConnectionRefusedError, if it is refused
            latecomer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port), timeout=2.0
            )
            latecomer[1].close()  # only reached when the door was left open

        gate.gate.set()
        await asyncio.wait_for(stopping, timeout=_SHUTDOWN_BUDGET)

    assert rig.owner is None
    assert rig.radio.legacy_writes == []


# ---------------------------------------------------------------------------
# Lease qualification and the shared target
# ---------------------------------------------------------------------------


async def test_a_spent_sessions_unkey_cannot_de_key_the_newer_owner(
    managed: _MakeManaged,
) -> None:
    """An OFF carrying a spent lease is not a licence over the current one.

    The dangerous shape is same-source, not foreign-source: two rigctld
    sessions on one server look alike everywhere except the connection id, so
    anything that qualifies an unkey by *source* rather than by the whole
    identity would let a departing WSJT-X take the live one's transmission
    down. Both of the spent session's routes are tried — a late ``T 0`` on the
    wire and the teardown release its socket drives — and the same session
    unkeying its own lease still works, so this is qualification and not a
    blanket refusal.
    """
    older, newer = "rigctld-client-1", "rigctld-client-2"
    rig = await managed()
    await rig.handler.execute(_ptt("1"), session_id=older)
    await rig.handler.execute(_ptt("0"), session_id=older)  # lease spent
    assert (await rig.handler.execute(_ptt("1"), session_id=newer)).error is (
        HamlibError.OK
    )
    assert rig.owner == TxOwner(TxSource.RIGCTLD, newer)
    rig.wire.clear()

    late = await rig.handler.execute(_ptt("0"), session_id=older)
    await rig.handler.release_session_tx(older)

    # ``RPRT 0`` because WSJT-X reads a nonzero RPRT mid-sequence as a hard
    # rig-control failure; nothing written because the lease is not this one's.
    assert late.error is HamlibError.OK
    assert rig.owner == TxOwner(TxSource.RIGCTLD, newer)
    assert rig.runtime.tx_snapshot.phase is TxPhase.KEYED
    assert rig.wire == []
    assert rig.radio.legacy_writes == []

    assert (await rig.handler.execute(_ptt("0"), session_id=newer)).error is (
        HamlibError.OK
    )
    assert rig.owner is None
    assert rig.wire == _UNKEY_WIRE


async def test_a_wsjtx_session_contends_for_the_same_target_as_the_web(
    managed: _MakeManaged,
) -> None:
    """One rig, one target, one lease — rigctld included.

    A private supervisor for rigctld would pass every test that only ever looks
    at rigctld: keys accepted, leases released, teardown clean. It would also
    let WSJT-X and the web hold the rig at once. So the claim has to be made
    from the other side of the boundary — while rigctld holds the lease the web
    ingress must be refused by the very supervisor rigctld keyed through, and
    must become able to key the moment rigctld gives it back.
    """
    rig = await managed()
    assert (await rig.handler.execute(_ptt("1"), session_id=_SESSION)).error is (
        HamlibError.OK
    )

    contended = await rig.runtime.request_on(_OTHER)

    assert contended.outcome is not TxOutcome.ACCEPTED
    assert rig.owner == _MINE

    assert (await rig.handler.execute(_ptt("0"), session_id=_SESSION)).error is (
        HamlibError.OK
    )
    assert (await rig.runtime.request_on(_OTHER)).outcome is TxOutcome.ACCEPTED
    assert rig.owner == _OTHER
