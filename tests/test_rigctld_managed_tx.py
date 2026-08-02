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
from collections.abc import AsyncIterator, Awaitable, Callable

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

    def __init__(self, runtime: ManagedRadioRuntime, wire: list[str]) -> None:
        self.runtime, self.wire = runtime, wire
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

    async def _make(*, keyed_by: TxOwner | None = None) -> _Managed:
        wire: list[str] = []
        runtime = ManagedRadioRuntime(
            "rigctld-test",
            service_factory=managed_tx_effect_service,
            provider_lifecycle=_Provider(wire),
        )
        runtimes.append(runtime)
        await runtime.replace_provider(ready=True)
        await runtime.request_fresh_ptt()  # seeds the OFF ``request_on`` demands
        if keyed_by is not None:
            assert (await runtime.request_on(keyed_by)).outcome is TxOutcome.ACCEPTED
        wire.clear()
        return _Managed(runtime, wire)

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
