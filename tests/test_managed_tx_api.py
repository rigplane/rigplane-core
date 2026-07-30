"""Managed PTT facade contract: one supervisor entry, one bound owner."""

from __future__ import annotations

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

_IDLE = TxSafetySupervisor().snapshot
_OWNER = TxOwner(TxSource.SDK, "session")


class FakeSupervisor:
    """Managed entry point; drives the provider's private write hook."""

    def __init__(self, radio: "FakeRadio") -> None:
        self._radio = radio
        self.calls: list[tuple[bool, TxOwner, TxReleaseReason | None]] = []

    async def request_on(self, owner: TxOwner) -> TxTransition:
        self.calls.append((True, owner, None))
        await self._radio._write_managed_ptt(0, True)
        return TxTransition(TxOutcome.ACCEPTED, _IDLE)

    async def release_owner(
        self, owner: TxOwner, *, reason: TxReleaseReason
    ) -> TxTransition:
        self.calls.append((False, owner, reason))
        await self._radio._write_managed_ptt(0, False)
        return TxTransition(TxOutcome.ACCEPTED, _IDLE)


class FakeRadio:
    """Provider whose bare ``set_ptt`` managed ingress must never reach."""

    def __init__(self, managed: bool) -> None:
        self.bare_writes: list[bool] = []
        self.managed_writes: list[bool] = []
        self.supervisor = FakeSupervisor(self)
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
