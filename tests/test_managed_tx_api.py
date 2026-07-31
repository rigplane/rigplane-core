"""Managed PTT facade contract: one supervisor entry, one bound owner."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from rigplane.core.exceptions import CommandError
from rigplane.core.radio_protocol import (
    ManagedTxApi,
    ManagedTxCapable,
    ManagedTxSupervisor,
)
from rigplane.core.tx_safety import (
    TxOutcome,
    TxOwner,
    TxReleaseReason,
    TxSafetySupervisor,
    TxSource,
    TxTransition,
)
from rigplane.runtime.managed_radio_runtime import ManagedRadioRuntime
from rigplane.runtime.radio import IcomRadio
from rigplane.runtime.sync import IcomRadio as SyncIcomRadio

_IDLE = TxSafetySupervisor().snapshot
_OWNER = TxOwner(TxSource.SDK, "session")
# Every answer ``request_on`` can give when the caller did *not* get the rig.
_KEY_REJECTIONS = (
    TxOutcome.BUSY,
    TxOutcome.NOT_READY,
    TxOutcome.RADIO_NOT_OFF,
    TxOutcome.RELEASE_PENDING,
    TxOutcome.STALE,
)


class FakeSupervisor:
    """Managed entry point; drives the provider's private write hook."""

    def __init__(self, radio: "FakeRadio", on: TxOutcome, off: TxOutcome) -> None:
        self._radio, self._on, self._off = radio, on, off
        self.calls: list[tuple[bool, TxOwner, TxReleaseReason | None]] = []

    async def request_on(self, owner: TxOwner) -> TxTransition:
        self.calls.append((True, owner, None))
        await self._radio._write_managed_ptt(0, True)
        return TxTransition(self._on, _IDLE)

    async def release_owner(
        self, owner: TxOwner, *, reason: TxReleaseReason
    ) -> TxTransition:
        self.calls.append((False, owner, reason))
        await self._radio._write_managed_ptt(0, False)
        return TxTransition(self._off, _IDLE)


class FakeRadio:
    """Provider whose bare ``set_ptt`` managed ingress must never reach."""

    def __init__(
        self,
        managed: bool,
        on: TxOutcome = TxOutcome.ACCEPTED,
        off: TxOutcome = TxOutcome.ACCEPTED,
    ) -> None:
        self.bare_writes: list[bool] = []
        self.managed_writes: list[bool] = []
        self.supervisor = FakeSupervisor(self, on, off)
        self.managed_tx = self.supervisor if managed else None

    async def set_ptt(self, on: bool) -> None:
        self.bare_writes.append(on)

    async def _write_managed_ptt(self, provider_generation: int, on: bool) -> None:
        self.managed_writes.append(on)


def test_shipped_radio_is_dormant() -> None:
    # No backend attaches a managed runtime yet (MOR-1016), so the facade must
    # refuse to bind and leave the legacy provider path untouched.
    shipped = IcomRadio("127.0.0.1")

    assert not isinstance(shipped, ManagedTxCapable)
    assert ManagedTxApi.bind(shipped, _OWNER) is None
    assert ManagedTxApi.bind(object(), _OWNER) is None
    assert ManagedTxApi.bind(FakeRadio(False), _OWNER) is None


def test_facade_binds_the_supervisor_and_owner_once() -> None:
    radio = FakeRadio(True)

    managed = ManagedTxApi.bind(radio, _OWNER)

    assert managed is not None
    assert managed.supervisor is radio.supervisor
    assert managed.owner is _OWNER


async def test_each_request_enters_the_supervisor_exactly_once() -> None:
    radio = FakeRadio(True)
    managed = ManagedTxApi.bind(radio, _OWNER)
    assert managed is not None

    await managed.set_ptt(True)
    await managed.set_ptt(False)

    assert radio.supervisor.calls == [
        (True, _OWNER, None),
        (False, _OWNER, TxReleaseReason.OPERATOR_RELEASE),
    ]
    # No bypass and no re-entry: the effect path owns the provider write.
    assert radio.bare_writes == []
    assert radio.managed_writes == [True, False]


def test_managed_runtime_satisfies_the_supervisor_protocol() -> None:
    async def _service(supervisor: object, transition: object) -> None:
        return None

    runtime = ManagedRadioRuntime("target", service_factory=lambda _host: _service)

    assert isinstance(runtime, ManagedTxSupervisor)


# --- Supervisor resolution (MOR-1193) --------------------------------------


class RaisingRadio:
    """Provider whose supervisor accessor fails instead of answering."""

    def __init__(self, error: type[Exception]) -> None:
        self._error = error

    @property
    def managed_tx(self) -> ManagedTxSupervisor | None:
        raise self._error("supervisor accessor exploded")


class SupervisorLessRadio:
    """Managed-capable provider that publishes no supervisor of its own."""

    @property
    def managed_tx(self) -> ManagedTxSupervisor | None:
        return None


class CountingRadio(FakeRadio):
    """Managed provider that counts every read of its supervisor accessor."""

    def __init__(self) -> None:
        self.reads = 0
        super().__init__(True)

    @property
    def managed_tx(self) -> FakeSupervisor:
        self.reads += 1
        return self.supervisor

    @managed_tx.setter
    def managed_tx(self, value: FakeSupervisor | None) -> None:
        """Absorb ``FakeRadio.__init__``'s assignment; the getter is the point."""


@pytest.mark.parametrize("error", [AttributeError, RuntimeError, ValueError, KeyError])
def test_a_failing_supervisor_accessor_is_never_read_as_unmanaged(
    error: type[Exception],
) -> None:
    # A supervisor that cannot be resolved is not the same as no supervisor:
    # answering ``None`` here sends a managed rig down the legacy write with no
    # lease, no owner and no watchdog.  ``AttributeError`` is the trap — a typo
    # on a nested attribute inside the property body raises it just as a
    # missing member does — and 3.11 alone swallowed it, through the ``hasattr``
    # that ``isinstance`` probes a non-callable protocol member with.  The other
    # three always propagated: ``hasattr`` catches nothing else.
    with pytest.raises(error, match="exploded"):
        ManagedTxApi.bind(RaisingRadio(error), _OWNER)


def test_a_supervisor_less_backend_reads_unmanaged_through_its_property() -> None:
    # ``ManagedTxCapable`` declares ``managed_tx`` a property, so a backend
    # with no supervisor answers ``None`` from code rather than storing it —
    # and that answer must still mean unmanaged (MOR-1013).  Binding a ``None``
    # supervisor instead would defer the failure to the first key, with the
    # operator's finger on it.
    assert ManagedTxApi.bind(SupervisorLessRadio(), _OWNER) is None


def test_binding_reads_the_supervisor_accessor_exactly_once() -> None:
    # The only accessor call is bind's own explicit read.  A second one means
    # the protocol machinery is probing the object as well — and that probe is
    # the ``hasattr`` which turns the raise above into "unmanaged".
    radio = CountingRadio()

    managed = ManagedTxApi.bind(radio, _OWNER)

    assert managed is not None
    assert managed.supervisor is radio.supervisor
    assert radio.reads == 1


# --- SDK sync ingress (MOR-1171) -------------------------------------------


@pytest.fixture
def wrapper() -> Iterator[SyncIcomRadio]:
    """One SDK session; the sync facade owns a private event loop."""
    radio = SyncIcomRadio("127.0.0.1")
    try:
        yield radio
    finally:
        radio._loop.close()


def _attach(wrapper: SyncIcomRadio, provider: FakeRadio) -> FakeRadio:
    wrapper._radio = provider  # type: ignore[assignment]
    return provider


def test_shipped_ingress_keeps_the_legacy_direct_write(
    wrapper: SyncIcomRadio,
) -> None:
    # Nothing assembles a managed runtime yet (MOR-1016), so the backend the
    # SDK actually ships must still reach its own ``set_ptt``, unchanged.
    writes: list[bool] = []

    async def _record(on: bool) -> None:
        writes.append(on)

    assert not isinstance(wrapper._radio, ManagedTxCapable)
    assert ManagedTxApi.bind(wrapper._radio, wrapper._tx_owner) is None
    wrapper._radio.set_ptt = _record  # type: ignore[method-assign]

    wrapper.set_ptt(True)
    wrapper.set_ptt(False)

    assert writes == [True, False]


@pytest.mark.parametrize("outcome", [TxOutcome.ACCEPTED, TxOutcome.IDEMPOTENT])
def test_managed_ingress_enters_the_supervisor_exactly_once(
    wrapper: SyncIcomRadio, outcome: TxOutcome
) -> None:
    # IDEMPOTENT means this owner already holds the lease, so the request did
    # land — it is an accepting outcome, not a rejection.
    provider = _attach(wrapper, FakeRadio(True, on=outcome, off=outcome))

    wrapper.set_ptt(True)
    wrapper.set_ptt(False)

    owner = wrapper._tx_owner
    assert provider.supervisor.calls == [
        (True, owner, None),
        (False, owner, TxReleaseReason.OPERATOR_RELEASE),
    ]
    # No bypass and no re-entry: the effect path owns the provider write.
    assert provider.bare_writes == []
    assert provider.managed_writes == [True, False]


def test_owner_identity_is_stable_per_session(wrapper: SyncIcomRadio) -> None:
    provider = _attach(wrapper, FakeRadio(True))
    other = SyncIcomRadio("127.0.0.1")

    wrapper.set_ptt(True)
    wrapper.set_ptt(False)
    wrapper.set_ptt(True)

    try:
        # A per-request owner would make ``release_owner`` miss its own lease
        # and strand the rig keyed until the watchdog fires.
        assert [owner for _, owner, _ in provider.supervisor.calls] == [
            wrapper._tx_owner
        ] * 3
        assert wrapper._tx_owner.source is other._tx_owner.source is TxSource.SDK
        assert other._tx_owner != wrapper._tx_owner
    finally:
        other._loop.close()


@pytest.mark.parametrize("outcome", _KEY_REJECTIONS)
def test_a_key_the_supervisor_refused_raises(
    wrapper: SyncIcomRadio, outcome: TxOutcome
) -> None:
    # ``set_ptt`` returns nothing, so a swallowed rejection would leave the
    # caller believing it is on the air while the rig is not transmitting.
    provider = _attach(wrapper, FakeRadio(True, on=outcome))

    with pytest.raises(CommandError, match=str(outcome)):
        wrapper.set_ptt(True)

    assert provider.supervisor.calls == [(True, wrapper._tx_owner, None)]
    assert provider.bare_writes == []


def test_a_release_the_supervisor_refused_is_tolerated(
    wrapper: SyncIcomRadio,
) -> None:
    # STALE covers both "nothing was keyed" and "another owner holds it". A
    # non-owner cannot force a release by design, so an unkey is best effort
    # and must not throw out of a caller's ``finally`` block.
    provider = _attach(wrapper, FakeRadio(True, off=TxOutcome.STALE))

    wrapper.set_ptt(False)

    assert provider.supervisor.calls == [
        (False, wrapper._tx_owner, TxReleaseReason.OPERATOR_RELEASE)
    ]
    assert provider.bare_writes == []
