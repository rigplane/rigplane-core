"""MOR-4382: real-path TX-safety order — OFF, then fetch, then web reacquire.

The eight harness-modelled ``_recover`` cases in
``tests/test_web_recovery_durable_off.py`` hold their wire order by
construction: the harness appends the radio-layer "refetch" behind the Web
pass. These tests drive the order through product code instead, on a real
``CoreRadio`` host with the real ``ControlPhaseRuntime``:

- soft recovery: the real ``_after_reconnect_parts`` (both ``soft_reconnect``
  branches funnel into it) with the radio's real ``rearm_managed_tx``, the
  real ``_fetch_initial_state`` call, and the production-wired web reacquire
  (``set_reconnect_callback(server._on_radio_reconnect)``, as
  ``web_startup`` does);
- full reconnect: the real ``CoreRadio._complete_connect`` tail after the
  real ``_shutdown_managed_tx`` session teardown — the same teardown the
  session release runs ahead of ``connect()`` — so no pending release can
  reach the fetch-first tail.

Composition reuses the established harnesses, nothing parallel: the
managed-TX side (real ``CoreRadio`` subclass, provider double on the
producer seam, public arming path) comes from
``test_web_recovery_durable_off``; the VFO/wire side (real ``CoreRadio``
subclass, real ``RadioPoller``, deterministic wire recorder) comes from
``test_radio_poller_tx_interlock``. Two radio objects because each harness
owns its seam; the SEQUENCING under test (re-arm, then fetch, then the
callback into select/scope) is product code throughout.

The fetch runs the real sweep, which degrades fast per query without a wire
(non-fatal by design); what these tests pin is the product-code order and
the exactly-once count — on the wire where a wire exists (OFF, select).

Timing: the readiness gate (``radio_ready``) reads the live clock, so these
run on the virtual clock (MOR-4075): ``scope_to`` points every module on
the driven path at it, and no wall-clock sleep survives.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from rigplane.core.tx_safety import TxPhase
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.testing.timing import VirtualClock, virtual_clock


def _scope_module(
    clock: VirtualClock, monkeypatch: pytest.MonkeyPatch, module: Any
) -> None:
    """Point one module at the virtual clock, however it imports time."""
    import types

    if hasattr(module, "time") and hasattr(module, "asyncio"):
        clock.scope_to(monkeypatch, module)
        return
    # A module with only one of the two names (e.g. the fetch sweep, which
    # sleeps but never reads the clock): build the views on a probe via the
    # public ``scope_to``, then install only the name the module has.
    probe = types.ModuleType("scope_probe")
    setattr(probe, "time", None)
    setattr(probe, "asyncio", None)
    clock.scope_to(monkeypatch, probe)
    if hasattr(module, "asyncio"):
        monkeypatch.setattr(module, "asyncio", probe.asyncio)
    if hasattr(module, "time"):
        monkeypatch.setattr(module, "time", probe.time)


def _scope_path_to_clock(clock: VirtualClock, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every module on the driven path at the virtual clock.

    Modules are resolved through ``sys.modules`` (via ``import_module``), not
    the ``import x.y.z as m`` form: the latter walks package *attributes*,
    which another test can leave pointing at a stale module object after a
    re-import, while the executing code lives in the ``sys.modules`` entry.
    """
    import importlib

    for name in (
        "rigplane.runtime._control_phase",
        "rigplane.runtime.radio_initial_state",
        "rigplane.runtime.managed_radio_runtime",
        "rigplane.runtime.radio",
        "rigplane.web.radio_poller",
        "rigplane.web.server",
    ):
        _scope_module(clock, monkeypatch, importlib.import_module(name))


async def _keyed_station() -> tuple[Any, Any, list[bool]]:
    """A real CoreRadio, armed over the public path, keyed, then cut off.

    Reuses ``_armed`` (real ``CoreRadio`` subclass + provider double +
    public arming path) and the ``_managed(keyed=True)`` keying sequence,
    so the durable OFF is armed and pending (``RELEASE_REQUIRED``) exactly
    as in the Web-hook cases. The provider log is cleared so it records
    only the recovery under test.
    """
    from test_web_recovery_durable_off import _OWNER, _armed

    radio, runtime, rebinds = await _armed()
    await runtime.request_fresh_ptt()  # seeds the OFF ``request_on`` demands
    await runtime.request_on(_OWNER)
    await runtime.set_provider_ready(ready=False)  # the control link drops
    assert runtime.tx_snapshot.phase is TxPhase.RELEASE_REQUIRED
    radio.provider.log.clear()
    return radio, runtime, rebinds


async def _web_reacquire(radio: Any) -> tuple[Any, Any, Any, Any]:
    """The production-wired web side: real poller + wire recorder, new epoch.

    Mirrors the VFO-epoch arrangement of the existing select tests
    (pre-select A, bounded-PTT-read stub, new epoch with B selected, wire
    cleared) and wires the callback exactly as ``web_startup`` does, so the
    reacquire below is the product path, not a stub.
    """
    from unittest.mock import AsyncMock

    from test_radio_poller_tx_interlock import (
        _observe_ptt,
        connect_vfo_poller,
        observe_connect_ptt_read,
    )
    from rigplane.web.server import WebServer

    poller, vfo_radio, store = connect_vfo_poller()
    server = WebServer(radio)
    server._radio_poller = poller  # noqa: SLF001
    # Production wiring (web_startup): the radio owns the callback.
    radio.set_reconnect_callback(server._on_radio_reconnect)
    _observe_ptt(store, False)
    await poller.select_vfo_a_on_connect(read_only=False)

    async def read(*args: object, **kwargs: object) -> None:
        await observe_connect_ptt_read(vfo_radio, False)

    vfo_radio.send_civ = AsyncMock(side_effect=read)
    # A new real epoch with B selected: the reacquire must re-select A once.
    vfo_radio._civ_epoch += 1
    store.begin_provider_generation()
    vfo_radio.selected = "B"
    vfo_radio.wire.sent_frames.clear()
    return server, poller, vfo_radio, store


def _record_order(radio: Any, vfo_radio: Any) -> tuple[list[str], Any]:
    """Share one order list across the TX log, the fetch and the wire.

    Observation only: the provider methods, the real fetch and the wire
    ``send`` are untouched — each appends its marker, then runs unchanged.
    """
    order: list[str] = radio.provider.log
    fetch_calls = 0
    orig_fetch = radio._fetch_initial_state

    async def _counted_fetch() -> None:
        nonlocal fetch_calls
        fetch_calls += 1
        order.append("fetch")
        await orig_fetch()

    radio._fetch_initial_state = _counted_fetch
    orig_send = vfo_radio.wire.send

    async def _recorded_send(frame: bytes) -> Any:
        order.append("wire")
        return await orig_send(frame)

    vfo_radio.wire.send = _recorded_send
    return order, lambda: fetch_calls


async def _stop_civ_side_effects(radio: Any) -> None:
    """Clean up what the real fetch starts on a wire-less host.

    The fetch dispatches for real, which lazily starts the CI-V pump and
    commander; against the stub transport the pump task fails (no wire to
    read). Retrieve it so teardown stays quiet, then stop both — the same
    hygiene ``disconnect()`` applies. Also drops the arranged CONNECTED
    mark so collection stays quiet too. Runs after every assertion.
    """
    radio._cancel_identity_read()
    rx_task = getattr(radio, "_civ_rx_task", None)
    if rx_task is not None and rx_task.done() and not rx_task.cancelled():
        rx_task.exception()
    await radio._stop_civ_rx_pump()
    await radio._stop_civ_worker()
    radio._conn_state = RadioConnectionState.DISCONNECTED


def _mark_link_recovered(
    radio: Any, clock: VirtualClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Arrange the production state a recovered link holds at the tail.

    ``soft_reconnect`` sets exactly this before ``_after_reconnect_parts``
    (CONNECTED, stream ready, fresh data); the epoch advance + fresh
    transport is what the rebuild leaves behind, staling the pre-drop
    binding so the re-arm does real work. The wide readiness window keeps
    the check deterministic however far the virtual sweep advances.
    """
    from test_web_recovery_durable_off import _CivPort

    _scope_path_to_clock(clock, monkeypatch)
    radio._civ_transport = _CivPort()
    radio._advance_civ_generation("test: repaired CI-V transport")
    radio._conn_state = RadioConnectionState.CONNECTED
    radio._civ_stream_ready = True
    radio._civ_recovering = False
    radio._civ_ready_idle_timeout = 60.0
    radio._last_civ_data_received = clock() - 0.1


@virtual_clock
async def test_soft_recovery_real_path_orders_off_before_fetch_before_reacquire(
    clock: VirtualClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Soft path, product order: durable OFF, then the one fetch, then web."""
    from rigplane.core.state_pipeline_contracts import FieldPath

    radio, runtime, rebinds = await _keyed_station()
    server, poller, vfo_radio, store = await _web_reacquire(radio)
    order, fetch_count = _record_order(radio, vfo_radio)
    _mark_link_recovered(radio, clock, monkeypatch)
    try:
        # The real tail bound to the real radio (built in __init__).
        await radio._control_phase._after_reconnect_parts(None, None)
        await asyncio.gather(*list(server._bg_tasks))  # noqa: SLF001
    finally:
        await _stop_civ_side_effects(radio)

    assert fetch_count() == 1
    assert order.count("fetch") == 1
    assert order.index("ptt(off)") < order.index("fetch")
    assert order.index("fetch") < order.index("wire")
    # The control phase did the one rebind; the Web hook found it live.
    assert rebinds == [True]
    assert len(vfo_radio.wire.sent_frames) == 6
    assert vfo_radio.selected == "A"
    assert store.snapshot().field(FieldPath.active_slot("0")).value == "A"
    assert poller._initial_fetch_done.is_set()
    assert runtime.tx_snapshot.phase is TxPhase.IDLE


@virtual_clock
async def test_overlapping_recoveries_real_path_fetch_once_each_select_once(
    clock: VirtualClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two overlapping recoveries: one OFF, one fetch each, one select."""
    radio, runtime, rebinds = await _keyed_station()
    server, poller, vfo_radio, _store = await _web_reacquire(radio)
    order, fetch_count = _record_order(radio, vfo_radio)
    _mark_link_recovered(radio, clock, monkeypatch)
    try:
        first = asyncio.ensure_future(
            radio._control_phase._after_reconnect_parts(None, None)
        )
        second = asyncio.ensure_future(
            radio._control_phase._after_reconnect_parts(None, None)
        )
        await asyncio.gather(first, second)
        await asyncio.gather(*list(server._bg_tasks))  # noqa: SLF001
    finally:
        await _stop_civ_side_effects(radio)

    # One rebind did the work (exactly one OFF); the other found it live.
    assert order.count("ptt(off)") == 1
    assert rebinds == [True]
    # One fetch per recovery completion — and each lands after the OFF the
    # re-arm lock serialised ahead of it (no interleaving with the release).
    assert fetch_count() == 2
    read_at = order.index("read_ptt")
    assert all(read_at < i for i, marker in enumerate(order) if marker == "fetch")
    # Both callbacks ran on the same epoch: still a single VFO-A select.
    assert len(vfo_radio.wire.sent_frames) == 6
    assert vfo_radio.selected == "A"
    assert poller._initial_fetch_done.is_set()
    assert runtime.tx_snapshot.phase is TxPhase.IDLE


@virtual_clock
async def test_full_reconnect_real_path_arms_clean_fetch_once_select_once(
    clock: VirtualClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full path: the session teardown owns the OFF, so the tail stays clean.

    The resident loop releases the session (which runs the managed-TX
    teardown) before ``connect()``; this test runs that same product
    teardown, then the real ``_complete_connect`` tail. Nothing pending may
    reach the fetch-first tail: no stale OFF resurfaces, the fresh session
    arms clean, fetches exactly once, and re-selects A once.
    """
    from rigplane.core.state_pipeline_contracts import FieldPath

    radio, runtime, _rebinds = await _keyed_station()
    # The teardown half, on a live link (the owner-disconnect episode): the
    # OFF goes out while the path can still carry it. On a dead link the
    # same call retires the lease silently — nothing deliverable exists —
    # and the tail below is still clean; the live link is what lets this
    # test show the OFF ahead of the fetch.
    await runtime.set_provider_ready(ready=True)
    await radio._shutdown_managed_tx()
    assert radio.provider.log.count("ptt(off)") == 1
    assert radio._managed_tx_runtime is None
    mark = len(radio.provider.log)

    server, poller, vfo_radio, store = await _web_reacquire(radio)
    order, fetch_count = _record_order(radio, vfo_radio)
    _mark_link_recovered(radio, clock, monkeypatch)

    try:
        await radio._complete_connect()
        await asyncio.gather(*list(server._bg_tasks))  # noqa: SLF001
    finally:
        await _stop_civ_side_effects(radio)

    assert fetch_count() == 1
    assert order[mark:].count("fetch") == 1
    # No stale release resurfaces on the fresh session — only the seed read.
    assert "ptt(off)" not in order[mark:]
    assert order.index("fetch") < order.index("wire")
    assert len(vfo_radio.wire.sent_frames) == 6
    assert vfo_radio.selected == "A"
    assert store.snapshot().field(FieldPath.active_slot("0")).value == "A"
    assert poller._initial_fetch_done.is_set()
    snapshot = radio.tx_snapshot
    assert snapshot is not None and snapshot.phase is TxPhase.IDLE
    assert snapshot.provider_ready


def test_path_scoping_survives_a_stale_package_attribute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_scope_path_to_clock`` must scope the executing module, always.

    The gate flake behind MOR-4382's follow-up: on an xdist worker where an
    earlier test had left ``rigplane.runtime``'s ``radio`` *attribute*
    pointing at a stale module object, the ``import x.y.z as m`` form of the
    helper scoped that stale object while ``radio_ready`` kept reading real
    ``time.monotonic`` from the executing module — so the readiness gate saw
    a million-second-old mark and skipped the fetch (``fetch_count == 0``).
    Reproduce that divergence directly: with the attribute swapped out, the
    soft-path scenario must still pass end to end.
    """
    import types

    import rigplane.runtime as runtime_pkg

    stale = types.ModuleType("stale_radio")  # no ``time``/``asyncio`` at all
    monkeypatch.setattr(runtime_pkg, "radio", stale, raising=False)
    # The divergence must really be in force, or the scenario below pins nothing.
    assert runtime_pkg.radio is stale
    test_soft_recovery_real_path_orders_off_before_fetch_before_reacquire(
        monkeypatch=monkeypatch
    )
