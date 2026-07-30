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
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from rigplane.core.tx_safety import (
    ProviderPttObservation,
    RadioTx,
    TxOwner,
    TxPhase,
    TxSource,
)
from rigplane.runtime.managed_radio_runtime import ManagedRadioRuntime
from rigplane.runtime.managed_tx_effect_service import managed_tx_effect_service
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

    await _recover(WebServer(radio))  # type: ignore[arg-type]

    assert log == ["refetch"]
    # Skipped outright, not attempted and swallowed: a swallowed failure would
    # put a warning and a traceback in every reconnect of every shipped rig.
    assert not [r for r in caplog.records if "managed TX" in r.getMessage()]
