"""MOR-1175: ``PrivilegedTxApi`` binds a force-unkey facade, not a key one.

Binding discipline mirrors ``ManagedTxApi.bind`` exactly (MOR-1193's fix,
repeated one layer up): the ``managed_tx`` *property* read is backend code
whose failures propagate, while the ``force_unkey`` *method* probe on the
supervisor it finds is a plain shape check that runs no accessor body. Only
a supervisor that goes beyond ``ManagedTxSupervisor`` -- publishing
``force_unkey`` alongside ``request_on``/``release_owner`` -- binds here; the
narrower shape is a positive "no privileged surface" finding, not an error.

No ``MagicMock``: every double below is either a hand-rolled class shaped to
expose exactly one structural fact, or the real ``ManagedRadioRuntime`` (the
one production supervisor that actually satisfies ``PrivilegedTxSupervisor``).
The wiring test reuses the ``_Provider`` double and effect service from
``test_managed_tx_force_unkey.py`` rather than re-deriving the retry ladder
that suite already covers -- this file only proves the facade reaches the
runtime and the runtime reaches the wire, once, with the hardcoded reason.
"""

from __future__ import annotations

import inspect

import pytest

from rigplane.core.radio_protocol import (
    ManagedTxApi,
    ManagedTxSupervisor,
    PrivilegedTxApi,
    PrivilegedTxSupervisor,
)
from rigplane.core.tx_safety import (
    TxOutcome,
    TxOwner,
    TxReleaseReason,
    TxSource,
    TxTransition,
)
from rigplane.runtime.managed_radio_runtime import ManagedRadioRuntime
from rigplane.runtime.managed_tx_effect_service import managed_tx_effect_service
from test_web_recovery_durable_off import _Provider

_OWNER = TxOwner(TxSource.WEBSOCKET, "operator-1")


# --- Doubles -----------------------------------------------------------


class _ManagedOnlySupervisor:
    """Exactly ``ManagedTxSupervisor``: ``request_on``/``release_owner``, no more.

    The narrowest surface a managed backend may legally publish -- and the
    one ``PrivilegedTxApi.bind`` must refuse, because handing force semantics
    to a supervisor that never advertised it would bypass the whole point of
    splitting the two protocols.
    """

    async def request_on(self, owner: TxOwner) -> TxTransition:
        raise AssertionError("bind must not call into the supervisor")

    async def release_owner(
        self, owner: TxOwner, *, reason: TxReleaseReason
    ) -> TxTransition:
        raise AssertionError("bind must not call into the supervisor")


class _PrivilegedSupervisor:
    """Satisfies ``PrivilegedTxSupervisor``: adds ``force_unkey`` to the pair."""

    async def request_on(self, owner: TxOwner) -> TxTransition:
        raise AssertionError("bind must not call into the supervisor")

    async def release_owner(
        self, owner: TxOwner, *, reason: TxReleaseReason
    ) -> TxTransition:
        raise AssertionError("bind must not call into the supervisor")

    async def force_unkey(
        self, owner: TxOwner, *, reason: TxReleaseReason
    ) -> TxTransition:
        raise AssertionError("bind must not call into the supervisor")


class _UnmanagedRadio:
    """Publishes the ``managed_tx`` member but answers ``None`` -- unmanaged."""

    @property
    def managed_tx(self) -> object | None:
        return None


class _RaisingRadio:
    """Provider whose ``managed_tx`` accessor fails instead of answering."""

    def __init__(self, error: type[Exception]) -> None:
        self._error = error

    @property
    def managed_tx(self) -> object | None:
        raise self._error("supervisor accessor exploded")


class _NarrowRadio:
    """Managed radio publishing a supervisor of the given shape."""

    def __init__(self, supervisor: object) -> None:
        self.managed_tx = supervisor


class _CountingRadio:
    """Managed radio that counts every read of its ``managed_tx`` accessor."""

    def __init__(self, supervisor: object) -> None:
        self.reads = 0
        self._supervisor = supervisor

    @property
    def managed_tx(self) -> object:
        self.reads += 1
        return self._supervisor


class _Clock:
    """Monotonic only when the test says so (same shape as the force-unkey suite)."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class _ManagedRadio:
    """Radio publishing a real ``ManagedRadioRuntime`` as its ``managed_tx``."""

    def __init__(self, runtime: ManagedRadioRuntime) -> None:
        self.managed_tx = runtime


async def _bound_runtime() -> tuple[ManagedRadioRuntime, list[str]]:
    """A real, ready ``ManagedRadioRuntime`` over a hand-rolled provider.

    Never observed, already transmitting -- MOR-1182's exact starting point --
    so a successful force here is not resting on an observation the runtime
    happens to already have.
    """
    log: list[str] = []
    provider = _Provider(log)
    runtime = ManagedRadioRuntime(
        "privileged-test",
        service_factory=managed_tx_effect_service,
        provider_lifecycle=provider,
        clock=_Clock(),
        tick_interval_seconds=0.01,
    )
    await runtime.replace_provider(ready=True)
    provider._keyed = True  # the rig transmits; nothing here has observed it
    log.clear()
    return runtime, log


# --- bind() matrix -------------------------------------------------------


def test_a_radio_with_no_managed_tx_member_has_no_privileged_surface() -> None:
    assert PrivilegedTxApi.bind(object(), _OWNER) is None


def test_managed_tx_present_but_none_is_unmanaged() -> None:
    assert PrivilegedTxApi.bind(_UnmanagedRadio(), _OWNER) is None


@pytest.mark.parametrize("error", [AttributeError, RuntimeError, ValueError, KeyError])
def test_a_raising_managed_tx_accessor_propagates(error: type[Exception]) -> None:
    # A supervisor that cannot be resolved is not "no privileged surface":
    # answering None here would mask a broken accessor as an ordinary,
    # unmanaged radio. AttributeError is the trap this must not swallow --
    # see ManagedTxCapable's docstring and MOR-1187/1193/1196.
    with pytest.raises(error, match="exploded"):
        PrivilegedTxApi.bind(_RaisingRadio(error), _OWNER)


def test_a_managed_tx_supervisor_only_double_has_no_privileged_surface() -> None:
    radio = _NarrowRadio(_ManagedOnlySupervisor())

    assert PrivilegedTxApi.bind(radio, _OWNER) is None


def test_bind_reads_the_managed_tx_accessor_exactly_once() -> None:
    radio = _CountingRadio(_PrivilegedSupervisor())

    api = PrivilegedTxApi.bind(radio, _OWNER)

    assert api is not None
    assert radio.reads == 1


async def test_a_real_managed_radio_runtime_binds() -> None:
    runtime, _log = await _bound_runtime()
    radio = _ManagedRadio(runtime)

    api = PrivilegedTxApi.bind(radio, _OWNER)

    assert api is not None
    assert api.supervisor is runtime
    assert api.owner is _OWNER


# --- force_unkey() wiring -------------------------------------------------


async def test_force_unkey_reaches_the_runtime_with_operator_forced_unkey() -> None:
    runtime, log = await _bound_runtime()
    api = PrivilegedTxApi.bind(_ManagedRadio(runtime), _OWNER)
    assert api is not None

    forced = await api.force_unkey()

    assert forced.outcome is TxOutcome.ACCEPTED
    assert forced.snapshot.owner == _OWNER
    assert (
        forced.snapshot.terminal_release_reason is TxReleaseReason.OPERATOR_FORCED_UNKEY
    )
    # The OFF and its confirming read reached the provider, exactly once.
    assert log == ["ptt(off)", "read_ptt"]


def test_force_unkey_takes_no_parameters() -> None:
    # No ``reason`` (or anything else) may reach the caller: a parameter here
    # would let any holder of this facade launder a system-attributed release
    # through the one path meant for an operator's kill switch.
    sig = inspect.signature(PrivilegedTxApi.force_unkey)
    assert list(sig.parameters) == ["self"]


async def test_force_unkey_rejects_an_attempted_reason_override() -> None:
    api = PrivilegedTxApi(_PrivilegedSupervisor(), _OWNER)
    override = TxReleaseReason.OPERATOR_RELEASE

    with pytest.raises(TypeError):
        await api.force_unkey(reason=override)  # type: ignore[call-arg]


# --- Surface shape --------------------------------------------------------


def test_managed_tx_supervisor_protocol_attrs_pinned() -> None:
    # Guards against MOR-1175 (or anything else) widening the guaranteed
    # minimum every managed backend publishes: adding ``force_unkey`` to
    # ``ManagedTxSupervisor`` itself would hand force semantics to every
    # ingress that binds ``ManagedTxApi``, defeating the whole split.
    # 3.12+ only; see test_audio_transport_conformance.py
    protocol_attrs = getattr(ManagedTxSupervisor, "__protocol_attrs__", None)
    if protocol_attrs is not None:
        assert set(protocol_attrs) == {"request_on", "release_owner"}


def test_privileged_tx_supervisor_protocol_attrs_pinned() -> None:
    protocol_attrs = getattr(PrivilegedTxSupervisor, "__protocol_attrs__", None)
    if protocol_attrs is not None:
        assert set(protocol_attrs) == {"force_unkey"}


def test_privileged_tx_api_exposes_no_key_operation() -> None:
    api = PrivilegedTxApi(_PrivilegedSupervisor(), _OWNER)

    assert not hasattr(api, "set_ptt")


def test_managed_tx_api_has_no_force_reach() -> None:
    api = ManagedTxApi(_ManagedOnlySupervisor(), _OWNER)

    assert not hasattr(api, "force_unkey")
    assert not hasattr(ManagedTxApi, "force_unkey")
