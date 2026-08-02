"""MOR-1013 slice 6: the durable OFF goes first on recovery.

``soft_reconnect`` advances the CI-V generation, which poisons the bound
managed TX port, so a release armed before the drop is re-armed against a fresh
causal boundary only once the provider is rebound. The Web recovery hook is
where that has to happen: everything else ``_on_radio_reconnect`` does — the
state refetch, the poller readiness signal it gates, the scope re-enable — is
ordinary recovered work that would otherwise reach a rig still keyed.

A real ``ManagedRadioRuntime`` over the real supervisor and the real effect
service drives these tests; a scripted double would report whatever order it
was told to, and the order is the whole claim. The provider is hand-rolled
rather than a ``MagicMock`` because a bare mock answers every ``getattr`` and
so could never show the unmanaged path staying unmanaged.

MOR-1192 hardens the same hook against the three ways it fails once MOR-1016
publishes a supervisor: a supervisor of the wrong shape read as "unmanaged", a
rebind that never returns, and two reconnects racing each other.

MOR-1196 settles the read itself: resolving the supervisor is a fallible
operation, not an attribute lookup, so it may neither be read as absence when
it fails nor escape the guard that re-opens the scope gate.

MOR-1016 PR3 gives the hook a second re-arm caller: ``soft_reconnect`` now
re-arms the radio itself, before any consumer's recovery, so this pass is very
often the *second* one. Both are routed onto ``CoreRadio.rearm_managed_tx``,
whose no-op-when-already-bound guard is what keeps the second from retiring the
port the first captured — a rebind that would take the repaired CI-V transport
down with it. Those cases run over a real ``CoreRadio`` and the real
``soft_reconnect``, because the ordering and the guard are both claims about
production wiring that a double could only assert about itself.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

import pytest

from rigplane import transport as transport_module
from rigplane.core.radio_protocol import ManagedTxSupervisor
from rigplane.core.tx_safety import (
    ProviderPttObservation,
    RadioTx,
    TxOwner,
    TxPhase,
    TxReleaseReason,
    TxSource,
    TxTransition,
)
from rigplane.runtime.managed_radio_runtime import ManagedRadioRuntime
from rigplane.runtime.managed_tx_effect_service import managed_tx_effect_service
from rigplane.runtime.radio import CoreRadio
from rigplane.web import server as web_server
from rigplane.web.server import WebServer
from test_audio_reconnect_rearm import (
    _FakeCivTransport,
    _SoftReconnectHost,
    _TestControlPhaseRuntime,
)

_OWNER = TxOwner(TxSource.WEBSOCKET, "ws-1")
_Observer = Callable[[ProviderPttObservation], None]


class _Provider:
    """Hand-rolled ``ProviderTxLifecycle`` logging every wire-facing call."""

    def __init__(self, log: list[str]) -> None:
        self.log, self._seq, self._keyed = log, 0, False
        self.write_failures = self.capture_failures = 0

    def _unbind_authoritative_ptt_observer(self) -> None:
        return None

    def _capture_managed_tx_port(self, generation: int, observer: _Observer) -> bool:
        if self.capture_failures:
            self.capture_failures -= 1
            raise ConnectionError("managed TX port capture failed")
        return True

    async def _write_managed_ptt(self, generation: int, on: bool) -> None:
        self.log.append(f"ptt({'on' if on else 'off'})")
        if self.write_failures:
            self.write_failures -= 1
            raise ConnectionError("managed PTT write failed")
        self._keyed = on

    async def _request_authoritative_ptt_read(
        self, *, provider_generation: int, observer: _Observer
    ) -> None:
        self.log.append("read_ptt")
        self._seq += 1
        observer(
            ProviderPttObservation(
                RadioTx.ON if self._keyed else RadioTx.OFF,
                provider_generation,
                self._seq,
                time.monotonic(),
            )
        )

    async def _retire_managed_tx_port(self, generation: int) -> None:
        return None


class _ShapelessSupervisor:
    """Exactly ``ManagedTxSupervisor`` and nothing more.

    The protocol declares ``request_on`` and ``release_owner`` only, so this is
    the narrowest surface a managed backend may legally publish — and recovery
    finds no ``replace_provider`` on it. Not hypothetical: the ``_Supervisor``
    in ``tests/test_web_managed_tx_owner.py`` already has exactly this shape.
    """

    async def request_on(self, owner: TxOwner) -> TxTransition:
        raise AssertionError("recovery must never key the rig")

    async def release_owner(
        self, owner: TxOwner, *, reason: TxReleaseReason
    ) -> TxTransition:
        raise AssertionError("recovery must not route a release through here")


class _Poller:
    """Only the readiness gate the hook clears and the scope enable waits on."""

    def __init__(self) -> None:
        self._initial_fetch_done = asyncio.Event()


class _Radio:
    """Duck-typed radio; a managed backend publishes its runtime here."""

    def __init__(self, log: list[str], managed_tx: object | None = None) -> None:
        self.log, self.connected, self.radio_ready = log, True, True
        self.capabilities: set[str] = set()
        if managed_tx is not None:
            self.managed_tx = managed_tx

    async def _fetch_initial_state(self) -> None:
        self.log.append("refetch")


class _ManagedTxRadio(_Radio):
    """A backend that re-arms its own managed port, as ``CoreRadio`` now does.

    Recovery stopped reaching for ``replace_provider`` on the supervisor in
    MOR-1016: only the radio can tell whether the provider is already bound to
    the port the reconnect repaired, so only the radio may decide to rebind.
    The forwarder is deliberately thin — the real method's degradation is
    pinned on the real ``CoreRadio`` further down, and a double that swallowed
    failures here would hide the rebind failures these cases were written for.
    """

    async def rearm_managed_tx(self) -> None:
        await self.managed_tx.replace_provider(ready=True)


class _RaisingSupervisorRadio(_Radio):
    """Backend whose supervisor accessor raises instead of answering.

    Neither hypothetical nor new: ``tests/test_web_managed_tx_owner.py`` models
    the same backend for the poller's unkey (MOR-1187). A property is free to
    read the transport, and a transport read is free to fail.
    """

    def __init__(self, log: list[str], error: BaseException) -> None:
        super().__init__(log)
        self._error = error

    @property
    def managed_tx(self) -> object:
        raise self._error


class _SupervisorLessRadio(_Radio):
    """Backend that publishes the member and answers ``None`` from it.

    ``ManagedTxCapable`` documents this shape in as many words — "expose no
    ``managed_tx`` attribute (or return ``None``)" — and it is the one shape
    plain ``_Radio`` cannot model, because it stores the supervisor only when
    there is one, so ``None`` there means no member at all. The static probe
    finds this property, so past it the explicit read is the only thing left
    that can settle the radio as unmanaged.
    """

    @property
    def managed_tx(self) -> None:
        return None


async def _managed(
    *, keyed: bool = False
) -> tuple[WebServer, ManagedRadioRuntime, _Provider, list[str]]:
    """A web server over a managed radio, optionally keyed and then cut off."""
    log: list[str] = []
    provider = _Provider(log)
    runtime = ManagedRadioRuntime(
        "web", service_factory=managed_tx_effect_service, provider_lifecycle=provider
    )
    await runtime.replace_provider(ready=True)
    if keyed:
        await runtime.request_fresh_ptt()  # seeds the OFF ``request_on`` demands
        await runtime.request_on(_OWNER)
        await runtime.set_provider_ready(ready=False)  # the control link drops
        assert runtime.tx_snapshot.phase is TxPhase.RELEASE_REQUIRED
    log.clear()
    return WebServer(_ManagedTxRadio(log, runtime)), runtime, provider, log  # type: ignore[arg-type]


async def _recover(server: WebServer) -> None:
    server._on_radio_reconnect()  # noqa: SLF001
    await asyncio.gather(*list(server._bg_tasks))  # noqa: SLF001


def _warned(caplog, phrase: str) -> bool:
    """Whether a managed TX warning carries ``phrase``.

    Every branch here logs, so matching the phrase and not merely "managed TX"
    is what tells the timeout apart from the generic failure.
    """
    return any(
        "managed TX" in record.getMessage() and phrase in record.getMessage()
        for record in caplog.records
    )


async def test_the_durable_off_precedes_recovered_work() -> None:
    """The release armed by the drop reaches the rig before the refetch."""
    server, runtime, _provider, log = await _managed(keyed=True)

    await _recover(server)

    assert log == ["ptt(off)", "read_ptt", "refetch"]
    assert runtime.tx_snapshot.phase is TxPhase.IDLE


async def test_a_failed_first_off_still_goes_first_on_the_retry() -> None:
    """A refused OFF keeps its place in line — it does not lose its turn."""
    server, runtime, provider, log = await _managed(keyed=True)
    provider.write_failures = 1

    await _recover(server)
    assert log == ["ptt(off)", "refetch"]
    # Recovery is not blocked by the failure, and the release outlives it.
    assert runtime.tx_snapshot.phase is TxPhase.FAULTED

    log.clear()
    await _recover(server)
    assert log == ["ptt(off)", "read_ptt", "refetch"]
    assert runtime.tx_snapshot.phase is TxPhase.IDLE


async def test_a_rebind_that_raises_neither_keys_nor_bricks_recovery() -> None:
    """No OFF reaches the rig, the refetch still runs, the release survives."""
    server, runtime, provider, log = await _managed(keyed=True)
    provider.capture_failures = 1

    await _recover(server)

    assert log == ["refetch"]
    assert runtime.tx_snapshot.phase is TxPhase.RELEASE_REQUIRED


async def test_recovery_with_nothing_pending_only_does_recovered_work() -> None:
    """No lease, no release: the hook must not invent a write of its own."""
    server, _runtime, _provider, log = await _managed()

    await _recover(server)

    assert log == ["refetch"]


async def test_an_unmanaged_radio_recovers_exactly_as_it_does_today(caplog) -> None:
    """Legacy unmanaged backends (serial/USB Icom, Yaesu CAT, rigctld-client)
    publish no supervisor at all, matching every backend's behavior before
    MOR-1016."""
    log: list[str] = []
    radio = _Radio(log)
    assert not hasattr(radio, "managed_tx")
    server = WebServer(radio)  # type: ignore[arg-type]
    # Held for the whole pass, so this says by construction what no tick count
    # can: the guard returns above the lock, and an unmanaged radio that
    # reached it would be waiting here instead of finishing.
    await server._managed_tx_rebind_lock.acquire()  # noqa: SLF001

    await _recover(server)

    assert log == ["refetch"]
    # Skipped outright, not attempted and swallowed: a swallowed failure would
    # put a warning and a traceback in every reconnect of every shipped rig.
    assert not [r for r in caplog.records if "managed TX" in r.getMessage()]


async def test_a_stalled_unmanaged_pass_does_not_hold_up_the_next_one() -> None:
    """Recovery must queue on nothing — no shipped rig publishes a supervisor."""
    log: list[str] = []
    stalled = asyncio.Event()
    radio = _Radio(log)

    async def _wedge_the_first_refetch() -> None:
        log.append("refetch")
        if len(log) == 1:
            await stalled.wait()

    radio._fetch_initial_state = _wedge_the_first_refetch  # type: ignore[method-assign]
    server = WebServer(radio)  # type: ignore[arg-type]
    server._radio_poller = poller = _Poller()  # type: ignore[assignment]
    server._on_radio_reconnect()  # noqa: SLF001
    await asyncio.sleep(0)
    server._on_radio_reconnect()  # noqa: SLF001
    await asyncio.sleep(0)

    # Serialising the pass rather than the rebind queues the second one behind
    # the stall, and ``_refetch_and_reenable`` is the only thing in ``src/``
    # that ever re-sets the gate — so the scope stays dark for good.
    assert log == ["refetch", "refetch"]
    assert poller._initial_fetch_done.is_set()
    stalled.set()


async def test_a_supervisor_without_replace_provider_is_reported_not_ignored(
    caplog,
) -> None:
    """Unmanaged is a positive determination, never a fallback from failure."""
    supervisor = _ShapelessSupervisor()
    assert isinstance(supervisor, ManagedTxSupervisor)
    assert not hasattr(supervisor, "replace_provider")
    log: list[str] = []

    with caplog.at_level(logging.WARNING):
        await _recover(WebServer(_Radio(log, supervisor)))  # type: ignore[arg-type]

    # Recovery still runs; what must not happen is silence. A managed rig whose
    # armed OFF was never even attempted looks exactly like a shipped unmanaged
    # one, and the operator is left keyed with nothing in the log to say so.
    assert log == ["refetch"]
    assert [r for r in caplog.records if "managed TX" in r.getMessage()]


async def test_a_raising_accessor_still_opens_the_scope_gate() -> None:
    """MOR-1196: the resolution belongs inside the guard that re-opens the gate.

    ``ConnectionError``, not ``AttributeError``: ``getattr(..., None)`` already
    absorbs the latter, so only a different exception shows whether the read is
    guarded at all. Resolved above the ``finally``, it escapes with the gate
    still clear, and ``EnableScope`` is deferred and re-queued for good — the
    operator's scope goes dark and stays dark. The error must still surface.
    """
    log: list[str] = []
    server = WebServer(_RaisingSupervisorRadio(log, ConnectionError("boom")))  # type: ignore[arg-type]
    server._radio_poller = poller = _Poller()  # type: ignore[assignment]

    server._on_radio_reconnect()  # noqa: SLF001
    with pytest.raises(ConnectionError, match="boom"):
        await asyncio.gather(*list(server._bg_tasks))  # noqa: SLF001

    # Nothing downstream of the failure ran, and the gate opened regardless.
    assert log == []
    assert poller._initial_fetch_done.is_set()


async def test_an_attributeerror_inside_the_accessor_is_not_absence() -> None:
    """The other half of the rule: a failed read is not a ``None``.

    ``getattr(radio, "managed_tx", None)`` cannot tell "this backend publishes
    no supervisor" from a typo on a nested attribute inside the property — the
    identical bypass MOR-1193 closed in ``ManagedTxApi.bind``. Silently
    unmanaged is the worse outcome of the two: the armed OFF is never even
    attempted, and unlike the shapeless-supervisor branch nothing says so.
    """
    log: list[str] = []
    server = WebServer(_RaisingSupervisorRadio(log, AttributeError("typo")))  # type: ignore[arg-type]
    server._radio_poller = poller = _Poller()  # type: ignore[assignment]

    server._on_radio_reconnect()  # noqa: SLF001
    with pytest.raises(AttributeError, match="typo"):
        await asyncio.gather(*list(server._bg_tasks))  # noqa: SLF001

    assert log == []
    assert poller._initial_fetch_done.is_set()


async def test_a_backend_that_answers_none_is_unmanaged_and_stays_quiet(
    caplog,
) -> None:
    """A ``None`` from the accessor is a supervisor-less rig, not a broken one.

    Past the static probe the read is the only thing that can still settle
    this, so dropping the ``None`` check hands ``NoneType`` to the shapeless-
    supervisor branch, which warns that an armed durable OFF cannot be re-armed
    — on a rig that never had a supervisor to arm one with. A warning that
    fires on a healthy backend is how the one that matters stops being read.
    """
    log: list[str] = []

    with caplog.at_level(logging.WARNING):
        await _recover(WebServer(_SupervisorLessRadio(log)))  # type: ignore[arg-type]

    assert log == ["refetch"]
    assert not [r for r in caplog.records if "managed TX" in r.getMessage()]


async def test_a_rebind_that_never_returns_does_not_wedge_recovery(
    caplog, monkeypatch
) -> None:
    """The rebind is bounded, so readiness is still signalled — and it is loud."""
    server, _runtime, provider, log = await _managed(keyed=True)
    server._radio_poller = poller = _Poller()  # type: ignore[assignment]
    never = asyncio.Event()

    async def _never_retires(generation: int) -> None:
        await never.wait()

    provider._retire_managed_tx_port = _never_retires  # type: ignore[method-assign]
    monkeypatch.setattr(web_server, "_MANAGED_TX_REBIND_TIMEOUT", 0.05)

    with caplog.at_level(logging.WARNING):
        await asyncio.wait_for(_recover(server), timeout=5.0)

    # The OFF is abandoned exactly as a refused one is — but recovery finishes,
    # and the scope gate the refetch guards is open again rather than stuck shut.
    assert log == ["refetch"]
    assert poller._initial_fetch_done.is_set()
    # Specifically the timeout, not the generic failure path: ``TimeoutError``
    # is an ``OSError`` is an ``Exception``, so deleting the timeout branch
    # outright still logs, and a laxer assertion would not notice.
    assert _warned(caplog, "still running after")

    never.set()
    await asyncio.gather(*list(server._bg_tasks), return_exceptions=True)
    # Abandoned, not cancelled. A cancel lands in ``_await_retirement``'s loop,
    # which returns it as the rebind's own failure and loses the OFF for good;
    # left alone, the write still reaches the rig when the link comes back.
    assert log == ["refetch", "ptt(off)", "read_ptt"]


async def test_a_rebind_that_fails_after_the_timeout_is_still_reported(
    caplog, monkeypatch
) -> None:
    """Nothing awaits it any more, so its failure has to find its own way out."""
    server, _runtime, provider, _log = await _managed(keyed=True)
    fail = asyncio.Event()

    async def _fails_once_nobody_is_listening(generation: int) -> None:
        await fail.wait()
        raise ConnectionError("retirement failed after the wait gave up")

    provider._retire_managed_tx_port = _fails_once_nobody_is_listening  # type: ignore[method-assign]
    monkeypatch.setattr(web_server, "_MANAGED_TX_REBIND_TIMEOUT", 0.05)

    with caplog.at_level(logging.WARNING):
        await asyncio.wait_for(_recover(server), timeout=5.0)
        fail.set()
        await asyncio.gather(*list(server._bg_tasks), return_exceptions=True)

    # Unharvested this surfaces only as asyncio's "Task exception was never
    # retrieved" at collection time, detached from the reconnect that caused it.
    assert _warned(caplog, "failed late")


async def test_overlapping_reconnects_do_not_interleave() -> None:
    """A second reconnect must not reach the provider mid-way through the first."""
    server, runtime, provider, log = await _managed(keyed=True)

    async def _logged_retire(generation: int) -> None:
        log.append(f"retire[{generation}]")

    provider._retire_managed_tx_port = _logged_retire  # type: ignore[method-assign]

    server._on_radio_reconnect()  # noqa: SLF001
    server._on_radio_reconnect()  # noqa: SLF001
    await asyncio.gather(*list(server._bg_tasks))  # noqa: SLF001

    # The first pass's OFF is written *and* confirmed before the second pass
    # touches the provider at all. Overlapped, ``retire[2]`` lands first and
    # supersedes the transition the first pass is still servicing — and note
    # the write alone is not enough to assert on: unserialised it can still
    # precede the refetch while its confirming read lands after.
    assert log.count("refetch") == 2
    assert log.index("read_ptt") < log.index("retire[2]")
    assert runtime.tx_snapshot.phase is TxPhase.IDLE


# ===========================================================================
# MOR-1016 PR3 -- one re-arm per repaired path, and it goes first
# ===========================================================================


class _CivPort:
    """A CI-V data transport stand-in; its identity is all the guard reads."""


class _ManagedRadio(CoreRadio):
    """A real ``CoreRadio`` whose managed port keeps the CI-V bookkeeping.

    ``_AssembledRadio`` in ``test_managed_tx_assembly`` retires
    unconditionally, which is right for the arming failures it pins. What the
    re-arm guard turns on is the *conditional* half of
    ``CivRuntime.retire_managed_tx_port``: the epoch advances, and the
    transport is dropped, only when the port being retired is the current one.
    Retiring a port from an older epoch, or one whose transport has since been
    replaced, leaves both alone — and telling that apart from a live binding is
    the entire job of the guard under test.
    """

    def __init__(self, provider: _Provider) -> None:
        super().__init__("127.0.0.1")
        self.provider = provider
        self._civ_transport = _CivPort()  # type: ignore[assignment]
        self._ports: dict[int, tuple[int, object]] = {}

    async def _send_civ_expect(self, civ_frame: bytes, **kwargs: object) -> object:
        return object()  # the arming probe: this rig answers 0x1C 0x00

    def _unbind_authoritative_ptt_observer(self) -> None:
        self.provider._unbind_authoritative_ptt_observer()

    def _capture_managed_tx_port(self, generation: int, observer: _Observer) -> bool:
        if self._civ_transport is None:
            # What ``CivRuntime.capture_managed_port`` answers with nothing to
            # capture — the state a destructive second rebind leaves behind.
            self._unbind_authoritative_ptt_observer()
            return False
        if not self.provider._capture_managed_tx_port(generation, observer):
            return False
        self._ports[generation] = (self._civ_epoch, self._civ_transport)
        return True

    async def _write_managed_ptt(self, generation: int, on: bool) -> None:
        await self.provider._write_managed_ptt(generation, on)

    async def _request_authoritative_ptt_read(
        self, provider_generation: int, observer: _Observer
    ) -> None:
        await self.provider._request_authoritative_ptt_read(
            provider_generation=provider_generation, observer=observer
        )

    async def _retire_managed_tx_port(self, generation: int) -> None:
        await self.provider._retire_managed_tx_port(generation)
        epoch, transport = self._ports.pop(generation, (None, None))
        if epoch == self._civ_epoch:
            self._advance_civ_generation("managed TX physical port retired")
        if transport is self._civ_transport:
            self._civ_transport = None


async def _armed() -> tuple[_ManagedRadio, ManagedRadioRuntime, list[bool]]:
    """A radio armed through the public path, counting every rebind after it."""
    radio = _ManagedRadio(_Provider([]))
    await radio.rearm_managed_tx()
    runtime = radio._managed_tx_runtime
    assert runtime is not None
    assert radio.tx_snapshot is not None and radio.tx_snapshot.provider_ready
    rebinds: list[bool] = []
    replace_provider = runtime.replace_provider

    async def _counted(*, ready: bool) -> TxTransition:
        rebinds.append(ready)
        return await replace_provider(ready=ready)

    runtime.replace_provider = _counted  # type: ignore[method-assign]
    return radio, runtime, rebinds


async def test_a_second_rearm_over_a_live_binding_touches_nothing() -> None:
    """The no-op is the feature: two recovery callers, one of them does work."""
    radio, _runtime, rebinds = await _armed()
    port = radio._civ_transport

    await radio.rearm_managed_tx()

    assert rebinds == []  # zero ``replace_provider`` calls reach the runtime
    assert radio._civ_transport is port
    assert radio.provider.log == ["read_ptt"]  # and no second seed either


async def test_web_and_the_control_phase_rebind_a_reconnect_once_between_them() -> None:
    """R2: ``soft_reconnect`` re-arms and the Web hook re-arms; one may land."""
    radio, _runtime, rebinds = await _armed()
    server = WebServer(radio)  # type: ignore[arg-type]
    # What ``soft_reconnect`` leaves behind: a fresh CI-V socket on a fresh
    # generation, with the pre-drop binding stale against both.
    radio._civ_transport = port = _CivPort()  # type: ignore[assignment]
    radio._advance_civ_generation("soft_reconnect")

    await radio.rearm_managed_tx()  # the control phase, ahead of the callbacks
    await server._service_managed_tx_release()  # the Web hook, right behind it

    # A second rebind would retire the port the first captured — current epoch,
    # current transport — which advances the generation and disconnects the
    # socket the reconnect just repaired, undoing the recovery it rode in on.
    assert rebinds == [True]
    assert radio._civ_transport is port
    snapshot = radio.tx_snapshot
    assert snapshot is not None and snapshot.provider_ready


async def test_an_arm_that_moved_the_epoch_does_not_invite_another_rebind() -> None:
    """The guard reads the binding, not ``_managed_tx_armed_epoch``.

    ``_managed_tx_armed_epoch`` survives the plain two-caller race above — it
    happens to agree there — and is wrong on both sides of this one, which is
    why that race cannot be the only case here. The marker is written before
    arming's first await and answers "have I tried on this epoch", not "is the
    provider bound". A port whose transport was rebuilt under it is dead on an
    unchanged epoch, so keying off the marker refuses the re-arm that would
    revive it; and arming a replacement retires a port of the *current* epoch,
    which advances the epoch before the capture that follows — leaving a wholly
    successful arm with its marker one epoch behind a live binding, where the
    marker now reads "never armed here" and waves the destructive rebind
    through.
    """
    radio, _runtime, rebinds = await _armed()
    # The window ``soft_reconnect`` passes through between binding the new CI-V
    # socket and advancing the generation: transport replaced, epoch not yet.
    radio._civ_transport = port = _CivPort()  # type: ignore[assignment]
    epoch_before = radio._civ_epoch

    await radio.rearm_managed_tx()

    # Re-armed, because the binding was dead — the marker would have said no.
    assert rebinds == [True]
    assert radio._civ_epoch != epoch_before  # the retirement moved it mid-call
    assert radio._managed_tx_armed_epoch == epoch_before  # the marker did not
    assert radio._civ_transport is port

    await radio.rearm_managed_tx()

    # And not re-armed, because the binding is live — the marker would have
    # said yes, retiring a current port and disconnecting a working transport.
    assert rebinds == [True]
    assert radio._civ_transport is port
    snapshot = radio.tx_snapshot
    assert snapshot is not None and snapshot.provider_ready


async def test_soft_reconnect_re_arms_before_it_calls_any_consumer(monkeypatch) -> None:
    """The durable OFF goes in front of every consumer's recovery, not one.

    The Web hook put it in front of the Web's own recovered work; nothing put
    it in front of a headless consumer's, and nothing serviced it at all on a
    rig with no ``_on_reconnect`` at all. The real ``soft_reconnect`` runs here
    because the ordering claim is about that method's body and nothing else.
    """
    monkeypatch.setattr(transport_module, "IcomTransport", _FakeCivTransport)
    order: list[str] = []
    host = _SoftReconnectHost()
    host._on_reconnect = lambda: order.append("on_reconnect")

    async def _rearm() -> None:
        order.append("rearm")

    host.rearm_managed_tx = _rearm  # type: ignore[attr-defined]

    await _TestControlPhaseRuntime(host).soft_reconnect()  # type: ignore[arg-type]

    assert order == ["rearm", "on_reconnect"]


async def test_a_re_arm_that_raises_does_not_fail_the_reconnect(
    monkeypatch, caplog
) -> None:
    """Fail-soft: arming already fails closed, so nothing here may fail hard."""
    monkeypatch.setattr(transport_module, "IcomTransport", _FakeCivTransport)
    order: list[str] = []
    host = _SoftReconnectHost()
    host._on_reconnect = lambda: order.append("on_reconnect")

    async def _raises() -> None:
        raise ConnectionError("the rig went away again")

    host.rearm_managed_tx = _raises  # type: ignore[attr-defined]

    with caplog.at_level(logging.WARNING):
        await _TestControlPhaseRuntime(host).soft_reconnect()  # type: ignore[arg-type]

    # The CI-V path is repaired and every consumer still gets its callback; a
    # rig that cannot supervise TX is still a rig that must receive.
    assert order == ["on_reconnect"]
    assert host._civ_transport is not None
    assert _warned(caplog, "re-arm failed")
