"""MOR-3999: two shutdown signals must not cancel the managed-TX release path.

A launcher that signals the whole process group delivers SIGTERM twice
within the same millisecond (once directly, once forwarded). The first
signal starts the orderly shutdown; the second used to run
``for task in asyncio.all_tasks(loop): task.cancel()``, which also killed
the Commander worker, the CI-V receive pump and the serial writer while
the managed-TX release was still in flight. Every later ``force_receive``
then failed with "Commander is not started", the release never drained,
and shutdown ended with ERROR "the rig may still be keyed" — leaving a
keyed rig keyed.

These tests drive the real shutdown path (``serve_forever``'s signal
handler plus the CLI shutdown order) on the simulator, for the serial
IC-705 profile and the LAN IC-7610 profile. The fix lives in the shared
path; there is deliberately no per-radio code here or in the fix.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rigplane import cli as cli_module
from rigplane.backends.ic705 import Ic705SerialRadio
from rigplane.backends.icom7610.drivers.serial_civ_link import SerialCivLink
from rigplane.radio import IcomRadio
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.runtime.managed_tx_config import ManagedTxTotConfigStore
from rigplane.runtime.managed_tx_state import ManagedTxIntentKind
from rigplane.web import server as server_module
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.web_startup import (
    attach_managed_tx_composition,
    prepare_managed_tx_observation_generation,
)
from integration.test_tx_under_fault_matrix import _http_json
from rigplane_testkit.icom import MockIcomRadio
from _perf_helpers import fast_connect
from testkit.ic705_fakes import ic705_usb_fake

# Same knobs as tests/integration/test_tx_under_fault_matrix.py: no real
# hardware here, but the LAN stack must behave like the production one.
_KEEPALIVE_HOLD_S = 2.0
_WATCHDOG_TIMEOUT_S = 1.5
_RECONNECT_DELAY_S = 0.2
_RECONNECT_MAX_DELAY_S = 0.5
_GATE_TIMEOUT_S = 30.0

_PATH = "/api/v1/managed-transmit/command"
_AUTHORITY_LOGGER = "rigplane.runtime.managed_tx_authority"


async def _until(predicate, seconds: float) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while not predicate():
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(0.02)
    return True


async def _stand_up_serial(tmp_path):
    """Serial IC-705 stack, mirroring test_managed_tx_force_off_wedge."""
    fake = ic705_usb_fake()
    link = SerialCivLink(
        device="civ-serial-fake",
        baudrate=115200,
        open_serial_connection=fake.open_serial_connection,
    )
    radio = Ic705SerialRadio(
        device="civ-serial-fake",
        baudrate=115200,
        civ_link=link,
        _enumerate_serial_ports_fn=lambda: [],
    )
    composition = ManagedTxComposition(radio, config_path=tmp_path / "tx.json")
    install_managed_tx_composition(radio, composition)
    web = WebServer(radio, WebConfig(host="127.0.0.1", port=0, discovery=False))
    web.command_state_store.begin_provider_generation()
    await composition.bind_state_store(web.command_state_store)
    attach_managed_tx_composition(web, composition)
    await radio.connect()
    # No web.start() here: serve_forever starts the server, like the CLI.
    return SimpleNamespace(fake=fake, radio=radio, composition=composition, web=web)


async def _stand_up_lan(tmp_path):
    """LAN IC-7610 stack, like _stand_up_stack but without starting serving.

    No rigctld (the release does not go through it) and no web.start():
    the order driver serves through the real ``serve_forever``.
    """
    config_path = tmp_path / "managed-tx.json"
    ManagedTxTotConfigStore(config_path).set_timeout_seconds(30)
    sim = MockIcomRadio(
        single_owner=True,
        keepalive_hold_s=_KEEPALIVE_HOLD_S,
        stateful_ptt=True,
        ptt_on_session_loss="hold",
        unsolicited_civ=False,
    )
    await sim.start()
    radio = IcomRadio(
        host="127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        timeout=5.0,
        model="IC-7610",
        auto_reconnect=True,
        reconnect_delay=_RECONNECT_DELAY_S,
        reconnect_max_delay=_RECONNECT_MAX_DELAY_S,
        watchdog_timeout=_WATCHDOG_TIMEOUT_S,
    )
    composition = ManagedTxComposition(radio, config_path=config_path)
    install_managed_tx_composition(radio, composition)
    with fast_connect():
        await radio.connect()
    web = WebServer(radio, WebConfig(host="127.0.0.1", port=0, discovery=False))
    prepare_managed_tx_observation_generation(web)
    await composition.bind_state_store(web.command_state_store)
    attach_managed_tx_composition(web, composition)
    return SimpleNamespace(sim=sim, radio=radio, composition=composition, web=web)


async def _tear_down_lan(stack) -> None:
    with contextlib.suppress(Exception):
        await stack.web.stop()
    with contextlib.suppress(Exception):
        with fast_connect():
            await asyncio.wait_for(stack.radio.disconnect(), 10.0)
    with contextlib.suppress(Exception):
        await asyncio.wait_for(
            stack.composition.shutdown(termination=asyncio.Event()), 5.0
        )
    with contextlib.suppress(Exception):
        await stack.sim.stop()


async def _tear_down_serial(stack) -> None:
    with contextlib.suppress(Exception):
        termination = asyncio.Event()
        shutdown = asyncio.create_task(stack.composition.shutdown(termination))
        try:
            await asyncio.wait_for(asyncio.shield(shutdown), 3.0)
        except TimeoutError:
            termination.set()
            await asyncio.wait_for(asyncio.shield(shutdown), 5.0)
    with contextlib.suppress(Exception):
        await asyncio.wait_for(stack.web.stop(), 5.0)
    with contextlib.suppress(Exception):
        await asyncio.wait_for(stack.radio.disconnect(), 5.0)


async def _key_via_http(web) -> None:
    status, body = await _http_json(
        web.port, "POST", _PATH, {"operation": "transmit_on"}
    )
    assert status == 202, body
    assert body == dict(ok=True, operation="transmit_on", result="accepted"), body


def _capture_signal_handlers(monkeypatch):
    handlers: list = []
    monkeypatch.setattr(
        server_module,
        "_install_shutdown_signal_handlers",
        lambda _loop, on_signal: handlers.append(on_signal),
    )
    return handlers


def _record_exits(monkeypatch):
    exits: list = []
    monkeypatch.setattr(os, "_exit", lambda code: exits.append(code))
    return exits


def _clean(authority) -> bool:
    state = authority.snapshot_nowait().state
    return (
        state.intent.kind is ManagedTxIntentKind.RX
        and not state.release_required
        and state.pending_effect is None
    )


def _keyed_error_records(caplog) -> list:
    return [
        record
        for record in caplog.records
        if record.levelno >= logging.ERROR
        and "may still be keyed" in record.getMessage()
    ]


def _managed_records(caplog) -> list:
    return [
        (record.levelname, record.getMessage())
        for record in caplog.records
        if record.name == _AUTHORITY_LOGGER
    ]


async def _two_signal_shutdown_order(
    *, web, session, backstop, handlers, pre_signal=None
) -> None:
    """The CLI shutdown order under two back-to-back shutdown signals.

    Mirrors ``_cmd_web``/``_run``: the order task itself is cancelled by the
    second signal too, so the serve wait absorbs ``CancelledError`` and the
    order continues (session shutdown, then radio exit) instead of dying.
    ``pre_signal`` keys the radio after serving starts, before the signals.
    """
    serve = asyncio.create_task(
        web.serve_forever(
            on_shutdown_signal=backstop.arm, on_tx_release=backstop.protect
        )
    )
    try:
        assert await _until(lambda: bool(handlers), 5.0), "signal handler installed"
        assert await _until(lambda: web._server is not None, 15.0), "server started"
        if pre_signal is not None:
            await pre_signal()
        handlers[0]()
        handlers[0]()
        try:
            await asyncio.wait_for(serve, 15.0)
        except asyncio.CancelledError:
            pass  # the hurry-up cancel; the order continues like _cmd_web
        await asyncio.wait_for(asyncio.shield(session.shutdown()), 25.0)
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            await asyncio.wait_for(serve, 15.0)
    finally:
        if not serve.done():
            serve.cancel()
            with contextlib.suppress(Exception):
                await serve


async def _run_order_with_backstop(
    *, web, radio, composition, handlers, pre_signal=None
):
    """Run the shutdown order with the production shutdown bound.

    The backstop watches the whole order task, exactly like the CLI's
    ``_run`` task: the first signal arms its clock, the release registers
    through ``protect``.
    """
    cell: dict = {}

    async def _order() -> None:
        backstop = cell["backstop"]
        session = cell["session"]
        await _two_signal_shutdown_order(
            web=web,
            session=session,
            backstop=backstop,
            handlers=handlers,
            pre_signal=pre_signal,
        )

    order = asyncio.create_task(_order())
    backstop = cli_module._ShutdownBackstop(  # noqa: SLF001
        order, cli_module._first_signal_shutdown_bound_s()
    )
    session = cli_module._ManagedTxRadioSession(radio, composition, backstop)
    cell["backstop"] = backstop
    cell["session"] = session
    try:
        # The second signal cancels the awaiting test task too; absorb it
        # and re-join: the order continues on its own, like the CLI's run.
        try:
            await asyncio.wait_for(asyncio.shield(order), 60.0)
        except asyncio.CancelledError:
            pass
        await asyncio.wait_for(asyncio.shield(order), 60.0)
    finally:
        backstop.disarm()
    return backstop


@pytest.mark.parametrize(
    "profile",
    ["serial-ic705", pytest.param("lan-ic7610", marks=pytest.mark.mock_integration)],
)
async def test_two_signals_with_clean_rx_release_drained_without_keyed_error(
    tmp_path, monkeypatch, caplog, profile
) -> None:
    """(a) RX, nothing owed: two signals still drain cleanly, server stops."""
    handlers = _capture_signal_handlers(monkeypatch)
    exits = _record_exits(monkeypatch)

    if profile == "serial-ic705":
        stack = await _stand_up_serial(tmp_path)
        web, radio, composition = stack.web, stack.radio, stack.composition
        keyed = lambda: bool(stack.fake.ptt)  # noqa: E731
        teardown = lambda: _tear_down_serial(stack)  # noqa: E731
    else:
        stack = await _stand_up_lan(tmp_path)
        web, radio, composition = stack.web, stack.radio, stack.composition
        keyed = lambda: bool(stack.sim.ptt_on)  # noqa: E731
        teardown = lambda: _tear_down_lan(stack)  # noqa: E731

    try:
        assert not keyed()
        with caplog.at_level(logging.INFO, logger=_AUTHORITY_LOGGER):
            await _run_order_with_backstop(
                web=web, radio=radio, composition=composition, handlers=handlers
            )
        assert _clean(composition.authority)
        assert not keyed()
        assert web._server is None  # noqa: SLF001  listener closed
        assert web._radio_poller is None  # noqa: SLF001  poller stopped
        assert exits == []
        assert _keyed_error_records(caplog) == []
        assert "managed TX shutdown: force_receive accepted; release drained" in [
            message for _, message in _managed_records(caplog)
        ]
    finally:
        with contextlib.suppress(Exception):
            await teardown()


@pytest.mark.parametrize(
    "profile",
    ["serial-ic705", pytest.param("lan-ic7610", marks=pytest.mark.mock_integration)],
)
async def test_two_signals_while_keyed_release_completes_and_rig_returns_to_rx(
    tmp_path, monkeypatch, caplog, profile
) -> None:
    """(b) KEYED when two signals arrive: the release completes, rig ends RX."""
    handlers = _capture_signal_handlers(monkeypatch)
    exits = _record_exits(monkeypatch)

    if profile == "serial-ic705":
        stack = await _stand_up_serial(tmp_path)
        web, radio, composition = stack.web, stack.radio, stack.composition
        keyed = lambda: bool(stack.fake.ptt)  # noqa: E731
        teardown = lambda: _tear_down_serial(stack)  # noqa: E731
    else:
        stack = await _stand_up_lan(tmp_path)
        web, radio, composition = stack.web, stack.radio, stack.composition
        keyed = lambda: bool(stack.sim.ptt_on)  # noqa: E731
        teardown = lambda: _tear_down_lan(stack)  # noqa: E731

    async def _key_and_wait() -> None:
        await _key_via_http(web)
        assert await _until(keyed, 5.0), "simulator keyed before the signals"
        assert not _clean(composition.authority)

    try:
        with caplog.at_level(logging.INFO, logger=_AUTHORITY_LOGGER):
            await _run_order_with_backstop(
                web=web,
                radio=radio,
                composition=composition,
                handlers=handlers,
                pre_signal=_key_and_wait,
            )
        assert await _until(lambda: not keyed(), 5.0), "simulator back in RX"
        assert _clean(composition.authority)
        assert exits == []
        assert _keyed_error_records(caplog) == []
        assert "managed TX shutdown: force_receive accepted; release drained" in [
            message for _, message in _managed_records(caplog)
        ]
    finally:
        with contextlib.suppress(Exception):
            await teardown()


async def test_third_signal_still_forces_exit_and_second_cancels_the_rest(
    monkeypatch,
) -> None:
    """(c) A third signal still forces the exit; the second still cancels rest."""
    handlers = _capture_signal_handlers(monkeypatch)
    exits = _record_exits(monkeypatch)
    srv = WebServer(None, WebConfig(host="127.0.0.1", port=0, discovery=False))
    with patch(
        "rigplane.web.web_startup.asyncio.start_server",
        new=AsyncMock(
            return_value=MagicMock(
                sockets=[MagicMock(getsockname=lambda: ("127.0.0.1", 0))],
                wait_closed=AsyncMock(),
            )
        ),
    ):
        serve = asyncio.create_task(srv.serve_forever())
        try:
            assert await _until(lambda: bool(handlers), 5.0), "handler installed"
            probe = asyncio.create_task(asyncio.Event().wait())
            await asyncio.sleep(0)
            handlers[0]()  # first: orderly stop begins
            await asyncio.wait_for(serve, 5.0)
            assert not probe.done()
            handlers[0]()  # second: everything else is cancelled
            # The test task is cancelled too; absorb it, let the probe's
            # cancellation land, then check it from steady state.
            with contextlib.suppress(asyncio.CancelledError):
                await _until(lambda: probe.done() or probe.cancelled(), 5.0)
            await asyncio.sleep(0)
            with contextlib.suppress(asyncio.CancelledError):
                await probe
            assert probe.cancelled()
            handlers[0]()  # third: forced exit, as before
            assert exits == [1]
        finally:
            if not serve.done():
                serve.cancel()
                with contextlib.suppress(Exception):
                    await serve
            with contextlib.suppress(Exception):
                await WebServer.stop(srv)
