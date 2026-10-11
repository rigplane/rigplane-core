"""Silent serial link with polls outstanding through the real backend (MOR-3211).

The MOR-2861 case driven end to end: the real ``Ic7300SerialRadio`` (the
class ``rigplane web --backend serial --model IC-7300`` resolves through
``backends/factory.py``) connected to ``CivSerialFake`` through the
production ``SerialCivLink`` opener seam, a live poller driving the public
``get_freq(bypass_cache=True)`` so real CI-V ``0x03`` polls with response
waiters are continuously outstanding, and a managed-TX composition
installed the way ``cli: _cmd_web`` installs it. When the fake goes silent
with the port open, the watchdog must declare link-down inside the
documented budget (``_serial_link_down_silence_limit_s`` /
``_SERIAL_LINK_DOWN_TIMEOUT_THRESHOLD`` answer windows, whichever the
evidence crosses first) and park managed TX: the authority must refuse keys
for the gap instead of letting one land on a dead wire.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from rigplane import RadioConnectionState
from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.backends.icom7610.drivers.serial_civ_link import SerialCivLink
from rigplane.core.state_store import StateStore
from rigplane.runtime.managed_tx_authority import ShutdownResult
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.runtime.managed_tx_state import ManagedTxOutcome

from rigplane_testkit.serial import CivSerialFake

_DEVICE = "civ-serial-fake"  # synthetic node name; no real path is opened

# Shutdown budgets, scaled down from cli: _MANAGED_TX_SHUTDOWN_DRAIN_S and
# _MANAGED_TX_SHUTDOWN_SETTLE_S in _shutdown_managed_tx_composition.
_SHUTDOWN_DRAIN_S = 0.5
_SHUTDOWN_SETTLE_S = 2.0
# Park budget: composition.transport_unavailable settles its force_off at the
# authority attempt deadline (default ATTEMPT_TIMEOUT_S = 3.0 s) before the
# provider generation clears.
_PARK_SETTLE_S = 6.0
_SHUTDOWN_TERMINATED_LOG = (
    "managed TX shutdown: terminated before an accepted force_receive "
    "drained the release"
)


async def _wait_until(predicate: Callable[[], bool], *, timeout_s: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


async def _poll_loop(radio: Ic7300SerialRadio, stop: asyncio.Event) -> None:
    """A live poller: one public frequency read after another, errors ignored.

    While CONNECTED each read puts a real CI-V ``0x03`` on the wire with a
    response waiter outstanding — the "polls outstanding" half of the
    MOR-2861 evidence. A timed-out read falls back to the state cache or
    raises; both shapes are absorbed here so the loop keeps polling through
    the outage, the same absorb-and-continue stance ``web/radio_poller.py``
    takes toward failed polls.
    """
    while not stop.is_set():
        try:
            await radio.get_freq(bypass_cache=True)
        except Exception:
            pass
        await asyncio.sleep(0.02)


async def test_silent_link_declares_link_down_in_budget_and_parks_tx(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = CivSerialFake()
    link = SerialCivLink(
        device=_DEVICE, open_serial_connection=fake.open_serial_connection
    )
    radio = Ic7300SerialRadio(
        device=_DEVICE,
        civ_link=link,
        _enumerate_serial_ports_fn=lambda: [],  # no real OS port enumeration
    )
    radio._civ_get_timeout = 0.2
    radio._SERIAL_WATCHDOG_INTERVAL_S = 0.02
    radio._SERIAL_WATCHDOG_RETRY_S = 0.05
    radio._SERIAL_WATCHDOG_RETRY_MAX_S = 0.1
    composition = ManagedTxComposition(radio, config_path=tmp_path / "managed-tx.json")
    install_managed_tx_composition(radio, composition)
    store = StateStore()
    store.begin_provider_generation()
    await composition.bind_state_store(store)  # as cli: _cmd_web does

    await radio.connect()
    assert radio.connected
    assert (await composition.authority.snapshot()).provider_generation is not None

    stop = asyncio.Event()
    poller = asyncio.create_task(_poll_loop(radio, stop))
    # The documented budget: the MOR-2861 silence limit or three consecutive
    # answer-window timeouts (MOR-1440), whichever the evidence crosses
    # first, plus the tick phase against the asynchronous RX pump.
    budget_s = (
        max(
            radio._serial_link_down_silence_limit_s(),
            radio._SERIAL_LINK_DOWN_TIMEOUT_THRESHOLD * radio._civ_get_timeout,
        )
        + 2.0 * radio._SERIAL_WATCHDOG_INTERVAL_S
        + 0.3
    )
    try:
        assert await _wait_until(
            lambda: any(frame.command == 0x03 for frame in fake.commands),
            timeout_s=2.0,
        ), "the poller never reached the wire"

        with caplog.at_level(
            logging.ERROR, logger="rigplane.backends._icom_serial_base"
        ):
            fake.silence(30.0)  # monotonic window; costs no real time
            started = time.monotonic()
            assert await _wait_until(
                lambda: radio.conn_state is not RadioConnectionState.CONNECTED,
                timeout_s=budget_s + 0.5,
            ), "silent link with polls outstanding never declared link-down"
            elapsed_s = time.monotonic() - started

        assert elapsed_s <= budget_s, (
            f"link-down declared after {elapsed_s:.2f}s, over the documented "
            f"budget of {budget_s:.2f}s"
        )
        assert not radio.connected
        link_down_errors = [
            record
            for record in caplog.records
            if record.levelno >= logging.ERROR and "link-down" in record.getMessage()
        ]
        assert len(link_down_errors) == 1

        # TX is parked for the gap. Parking is not instantaneous: the
        # reconnect path parks through radio: _park_managed_tx →
        # composition.transport_unavailable, which submits a force_off and
        # waits for its settlement before retiring the provider, so on the
        # silent wire the park completes at the attempt deadline (authority
        # default ATTEMPT_TIMEOUT_S). Wait for the park, then assert both
        # observables: no ready provider, and a refused key instead of a
        # WRITE_ON landing on the dead wire.
        assert await _wait_until(
            lambda: composition.authority.snapshot_nowait().provider_generation is None,
            timeout_s=_PARK_SETTLE_S,
        ), "managed TX never parked after the silent-link link-down"
        assert await composition.authority.transmit_on() is ManagedTxOutcome.REJECTED
    finally:
        stop.set()
        await poller
        # Teardown order as production runs it (cli: _cmd_web drives the
        # command inside ``async with session``; ``_ManagedTxRadioSession.
        # __aexit__`` awaits ``_shutdown_managed_tx_composition`` before the
        # radio's own ``__aexit__`` disconnects it): stop the poller, shut
        # the composition down, then disconnect the radio.
        with caplog.at_level(
            logging.WARNING, logger="rigplane.runtime.managed_tx_authority"
        ):
            # Production shutdown shape, scaled down (cli:
            # _shutdown_managed_tx_composition): drain a short budget for an
            # accepted force_receive, then set termination and wait a settle
            # budget for the result. On the silent wire no force_receive can
            # be accepted, so the drain must time out and shutdown must
            # terminate and say so -- the MOR-3211 characterization of the
            # dead-wire unkey: shutdown cannot confirm it and must not hang
            # or pretend it drained.
            termination = asyncio.Event()
            shutdown = asyncio.create_task(composition.shutdown(termination))
            try:
                result = await asyncio.wait_for(
                    asyncio.shield(shutdown), timeout=_SHUTDOWN_DRAIN_S
                )
            except TimeoutError:
                termination.set()
                result = await asyncio.wait_for(
                    asyncio.shield(shutdown), timeout=_SHUTDOWN_SETTLE_S
                )
            assert result is ShutdownResult.TERMINATED
            assert any(
                record.levelno >= logging.WARNING
                and record.name == "rigplane.runtime.managed_tx_authority"
                and _SHUTDOWN_TERMINATED_LOG in record.getMessage()
                for record in caplog.records
            ), "shutdown must log that it terminated before draining the release"
        await radio.disconnect()
