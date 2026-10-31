"""Silent serial link with polls outstanding through the real backend (MOR-3211).

The MOR-2861 case driven end to end: the real ``Ic7300SerialRadio`` (the
class ``rigplane web --backend serial --model IC-7300`` resolves through
``backends/factory.py``) connected to ``CivSerialFake`` through the
production ``SerialCivLink`` opener seam, a live poller sending
fire-and-forget CI-V ``0x03`` reads in the shape ``web/radio_poller.py``
sends them (``send_civ(..., wait_response=False, wait_dispatch=False)``),
and a managed-TX composition installed the way ``cli: _cmd_web`` installs
it. Those reads leave keyed response sinks outstanding but never produce a
counted request timeout, so only the silence path
(``_serial_link_down_silence_limit_s``) can declare link-down here; the
test asserts that path by its log text and its budget.
``_declare_serial_link_down`` must park managed TX before it tears down
audio: the authority must have retired its provider by then, and must
refuse keys afterwards.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from pathlib import Path

import pytest

from rigplane import RadioConnectionState
from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.backends.icom7610.drivers.serial_civ_link import SerialCivLink
from rigplane.commands.commander import Priority
from rigplane.core.state_store import StateStore
from rigplane.runtime.managed_tx_authority import ShutdownResult
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.runtime.managed_tx_state import ManagedTxOutcome

from rigplane_testkit.serial import CivSerialFake
from _timing import await_until

_DEVICE = "civ-serial-fake"  # synthetic node name; no real path is opened

# Cadence knobs the test sets on the radio, and the silence limit the
# production formula (_serial_link_down_silence_limit_s: two answer windows
# plus one watchdog tick) must yield for them. Pinned here so a change to
# that formula fails this test instead of silently widening its budget.
_ANSWER_WINDOW_S = 0.2
_WATCHDOG_TICK_S = 0.02
_SILENCE_LIMIT_S = 2 * _ANSWER_WINDOW_S + _WATCHDOG_TICK_S
# The timeout path's threshold (_SERIAL_LINK_DOWN_TIMEOUT_THRESHOLD); pinned
# for the same reason, although these reads never feed it.
_TIMEOUT_THRESHOLD = 3
# Budget: the silence limit, one tick until the first watchdog tick that
# starts the silence clock, one tick until the tick that sees the limit
# crossed, and slack for event loop scheduling on a loaded test host.
_SCHEDULING_SLACK_S = 0.2
_LINK_DOWN_BUDGET_S = _SILENCE_LIMIT_S + 2 * _WATCHDOG_TICK_S + _SCHEDULING_SLACK_S
_SILENCE_EVIDENCE_LOG = "polls outstanding and no CI-V frame parsed"

# Shutdown budgets, scaled down from cli: _MANAGED_TX_SHUTDOWN_DRAIN_S and
# _MANAGED_TX_SHUTDOWN_SETTLE_S in _shutdown_managed_tx_composition.
_SHUTDOWN_DRAIN_S = 0.5
_SHUTDOWN_SETTLE_S = 2.0
# Park budget: composition.transport_unavailable settles its force_off at the
# authority attempt deadline before the provider generation clears.
_PARK_SETTLE_S = 6.0
_SHUTDOWN_TERMINATED_LOG = (
    "managed TX shutdown: terminated before an accepted force_receive "
    "drained the release"
)


async def _poll_loop(radio: Ic7300SerialRadio, stop: asyncio.Event) -> None:
    """A live poller: fire-and-forget frequency reads, errors ignored.

    Each read registers a keyed response sink and enqueues a real CI-V
    ``0x03`` for the wire, the call ``web/radio_poller.py: RadioPoller._civ``
    makes for background polls. After link-down a read waits in the CI-V recovery
    path, so teardown cancels this task rather than awaiting it.
    """
    while not stop.is_set():
        try:
            await radio.send_civ(
                0x03,
                wait_response=False,
                priority=Priority.BACKGROUND,
                wait_dispatch=False,
            )
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
    radio._civ_get_timeout = _ANSWER_WINDOW_S
    radio._SERIAL_WATCHDOG_INTERVAL_S = _WATCHDOG_TICK_S
    radio._SERIAL_WATCHDOG_RETRY_S = 0.05
    radio._SERIAL_WATCHDOG_RETRY_MAX_S = 0.1
    composition = ManagedTxComposition(radio, config_path=tmp_path / "managed-tx.json")
    install_managed_tx_composition(radio, composition)
    store = StateStore()
    store.begin_provider_generation()
    await composition.bind_state_store(store)  # as cli: _cmd_web does

    assert radio._serial_link_down_silence_limit_s() == pytest.approx(_SILENCE_LIMIT_S)
    assert radio._SERIAL_LINK_DOWN_TIMEOUT_THRESHOLD == _TIMEOUT_THRESHOLD

    await radio.connect()
    assert radio.connected
    assert (await composition.authority.snapshot()).provider_generation is not None

    # Record the authority's provider generation at the moment link-down
    # moves on to audio teardown, which _declare_serial_link_down does only
    # after its park has returned.
    generation_at_audio_teardown: list[int | None] = []
    stop_audio = radio._stop_serial_audio_driver

    async def _recording_stop_audio() -> None:
        generation_at_audio_teardown.append(
            composition.authority.snapshot_nowait().provider_generation
        )
        await stop_audio()

    radio._stop_serial_audio_driver = _recording_stop_audio  # type: ignore[method-assign]

    stop = asyncio.Event()
    poller = asyncio.create_task(_poll_loop(radio, stop))
    budget_s = _LINK_DOWN_BUDGET_S
    try:
        assert await await_until(
            lambda: any(frame.command == 0x03 for frame in fake.commands), timeout=2.0
        ), "the poller never reached the wire"

        with caplog.at_level(
            logging.ERROR, logger="rigplane.backends._icom_serial_base"
        ):
            fake.silence(30.0)  # monotonic window; costs no real time
            started = time.monotonic()
            assert await await_until(
                lambda: radio.conn_state is not RadioConnectionState.CONNECTED,
                timeout=budget_s + 0.5,
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
        assert _SILENCE_EVIDENCE_LOG in link_down_errors[0].getMessage(), (
            "link-down was not declared by the silence path: "
            f"{link_down_errors[0].getMessage()!r}"
        )

        # Link-down parks TX itself: by the time it tears down audio the
        # provider is retired.
        assert await await_until(
            lambda: bool(generation_at_audio_teardown), timeout=_PARK_SETTLE_S
        ), "link-down never reached audio teardown"
        assert generation_at_audio_teardown[0] is None, (
            "link-down tore down audio with managed TX still provided "
            f"(generation {generation_at_audio_teardown[0]})"
        )
        assert await composition.authority.transmit_on() is ManagedTxOutcome.REJECTED
    finally:
        stop.set()
        poller.cancel()
        with contextlib.suppress(asyncio.CancelledError):
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
