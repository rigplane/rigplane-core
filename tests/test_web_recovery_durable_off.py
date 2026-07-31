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
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

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
from rigplane.web import server as web_server
from rigplane.web.server import WebServer

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
    return WebServer(_Radio(log, runtime)), runtime, provider, log  # type: ignore[arg-type]


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
    """Every shipped backend until MOR-1016 publishes no supervisor at all."""
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
