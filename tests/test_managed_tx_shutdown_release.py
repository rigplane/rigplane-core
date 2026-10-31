"""MOR-3999: a SIGTERM-style shutdown must release TX through the live Commander.

Each time the ``station`` server was stopped by SIGTERM shortly after a
keyed sequence on real hardware (IC-705 USB), the log ended with
``ERROR managed TX shutdown: unkey not confirmed ... the rig may still be
keyed`` with ``(Commander is not started)``, while the radio was verifiably
in RX. A quiet stop (SIGTERM seconds after the last unkey) logged
``managed TX shutdown: force_receive accepted; release drained``.

These tests stand up the production stack on the simulator/testkit (FAKE
RADIO ONLY — nothing here can key a real radio), key through the managed
authority, and then run the CLI's exact shutdown order
(``web.stop()`` → ``rigctld.stop()`` → ``_shutdown_managed_tx_composition``
→ ``radio.disconnect()``):

* (a) radio in RX with nothing owed, shutdown right after the unkey settles
  → no ERROR containing ``may still be keyed``, and the drained line logs;
* (b) radio still KEYED at shutdown (no unkey sent) → the release goes out
  through the live Commander before it stops, the simulated radio ends in
  RX, and the shutdown reports drained.

Both cases run on two radio profiles (IC-705 serial, IC-7610 LAN): the fix
belongs to the shared shutdown path, never to a per-radio mechanism.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from rigplane.backends.ic705 import Ic705SerialRadio  # noqa: E402
from rigplane.backends.icom7610.drivers.serial_civ_link import SerialCivLink  # noqa: E402
from rigplane.cli import _shutdown_managed_tx_composition  # noqa: E402
from rigplane.radio import IcomRadio  # noqa: E402
from rigplane.rigctld.contract import RigctldConfig  # noqa: E402
from rigplane.rigctld.server import RigctldServer  # noqa: E402
from rigplane.runtime.managed_tx_composition import (  # noqa: E402
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.runtime.managed_tx_state import ManagedTxIntentKind  # noqa: E402
from rigplane.web.server import WebConfig, WebServer  # noqa: E402
from rigplane.web.web_startup import (  # noqa: E402
    attach_managed_tx_composition,
    prepare_managed_tx_observation_generation,
)

from _perf_helpers import fast_connect  # noqa: E402
from rigplane_testkit.icom import MockIcomRadio  # noqa: E402
from testkit.ic705_fakes import ic705_usb_fake  # noqa: E402


def _clean(authority: Any) -> bool:
    state = authority.snapshot_nowait().state
    return (
        state.intent.kind is ManagedTxIntentKind.RX
        and not state.release_required
        and state.pending_effect is None
    )


async def _until(predicate: Callable[[], bool], seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(0.02)
    return True


class _Stack:
    def __init__(self, sim: Any, radio: Any, web: Any, rigctld: Any) -> None:
        self.sim = sim
        self.radio = radio
        self.web = web
        self.rigctld = rigctld
        self.composition = radio._managed_tx_composition

    @property
    def authority(self) -> Any:
        return self.composition.authority

    def sim_keyed(self) -> bool:
        ptt = getattr(self.sim, "ptt_on", None)
        if ptt is None:
            ptt = getattr(self.sim, "ptt", False)
        return bool(ptt)


async def _stand_up_serial(tmp_path: Any) -> _Stack:
    from rigplane.runtime.managed_tx_config import ManagedTxTotConfigStore

    config_path = tmp_path / "managed-tx.json"
    ManagedTxTotConfigStore(config_path).set_timeout_seconds(30)
    fake = ic705_usb_fake()
    port = "civ-serial-fake"
    link = SerialCivLink(
        device=port, baudrate=115200, open_serial_connection=fake.open_serial_connection
    )
    radio = Ic705SerialRadio(
        device=port,
        baudrate=115200,
        civ_link=link,
        _enumerate_serial_ports_fn=lambda: [],
    )
    composition = ManagedTxComposition(radio, config_path=config_path)
    install_managed_tx_composition(radio, composition)
    web = WebServer(radio, WebConfig(host="127.0.0.1", port=0, discovery=False))
    web.command_state_store.begin_provider_generation()
    await composition.bind_state_store(web.command_state_store)
    attach_managed_tx_composition(web, composition)
    rigctld = RigctldServer(
        radio,
        RigctldConfig(host="127.0.0.1", port=0),
        allow_not_ready=True,
        managed_tx_authority=composition.authority,
        command_queue=web.command_queue,
        command_service=web.command_service,
    )
    await radio.connect()
    await web.start()
    await rigctld.start()
    return _Stack(fake, radio, web, rigctld)


async def _stand_up_lan(tmp_path: Any) -> _Stack:
    from rigplane.runtime.managed_tx_config import ManagedTxTotConfigStore

    config_path = tmp_path / "managed-tx.json"
    ManagedTxTotConfigStore(config_path).set_timeout_seconds(30)
    sim = MockIcomRadio(
        single_owner=True,
        keepalive_hold_s=2.0,
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
        reconnect_delay=0.2,
        reconnect_max_delay=0.5,
        watchdog_timeout=1.5,
    )
    composition = ManagedTxComposition(radio, config_path=config_path)
    install_managed_tx_composition(radio, composition)
    with fast_connect():
        await radio.connect()
    web = WebServer(radio, WebConfig(host="127.0.0.1", port=0, discovery=False))
    prepare_managed_tx_observation_generation(web)
    await composition.bind_state_store(web.command_state_store)
    attach_managed_tx_composition(web, composition)
    rigctld = RigctldServer(
        radio,
        RigctldConfig(host="127.0.0.1", port=0),
        allow_not_ready=True,
        managed_tx_authority=composition.authority,
        command_queue=web.command_queue,
        command_service=web.command_service,
    )
    await rigctld.start()
    web.attach_rigctl_channel(rigctld.open_channel)
    await web.start()
    return _Stack(sim, radio, web, rigctld)


_BUILDERS = {
    "serial-ic705": _stand_up_serial,
    "lan-ic7610": _stand_up_lan,
}


@contextlib.asynccontextmanager
async def _use_stack(name: str, tmp_path: Any) -> AsyncIterator[_Stack]:
    built = await _BUILDERS[name](tmp_path)
    try:
        yield built
    finally:
        with contextlib.suppress(Exception):
            await built.rigctld.stop()
        with contextlib.suppress(Exception):
            await built.web.stop()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(built.radio.disconnect(), 10.0)
        sim_stop = getattr(built.sim, "stop", None)
        if callable(sim_stop):
            with contextlib.suppress(Exception):
                await sim_stop()


async def _cli_order_shutdown(stack: _Stack) -> None:
    """The CLI's exact teardown order (``_run``/``_cmd_web``/``__aexit__``)."""
    await stack.web.stop()
    await stack.rigctld.stop()
    await _shutdown_managed_tx_composition(stack.composition)
    await stack.radio.disconnect()


def _managed_tx_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if "managed TX" in record.getMessage()]


def _keyed_errors(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelname == "ERROR" and "may still be keyed" in record.getMessage()
    ]


@pytest.mark.parametrize("profile", sorted(_BUILDERS))
async def test_quiet_shutdown_after_unkey_drains_release(
    profile: str, tmp_path: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """Case (a): RX with nothing owed, shutdown right after the unkey settles."""
    caplog.set_level(logging.INFO)
    async with _use_stack(profile, tmp_path) as stack:
        assert await stack.authority.transmit_on()
        assert await _until(stack.sim_keyed, 5.0), "simulator did not key"
        assert await stack.authority.force_off()
        assert await _until(
            lambda: _clean(stack.authority) and not stack.sim_keyed(), 5.0
        ), "release did not settle before shutdown"

        await _cli_order_shutdown(stack)

        errors = [
            record.getMessage()
            for record in caplog.records
            if record.levelname == "ERROR"
            and "may still be keyed" in record.getMessage()
        ]
        assert errors == [], errors
        assert _clean(stack.authority)
        assert not stack.sim_keyed()
        assert any(
            "force_receive accepted; release drained" in record.getMessage()
            for record in _managed_tx_records(caplog)
        ), [record.getMessage() for record in _managed_tx_records(caplog)]


@pytest.mark.parametrize("profile", sorted(_BUILDERS))
async def test_shutdown_with_unkey_still_in_flight(
    profile: str, tmp_path: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """SIGTERM between the unkey admission and its settlement still drains."""
    caplog.set_level(logging.INFO)
    async with _use_stack(profile, tmp_path) as stack:
        assert await stack.authority.transmit_on()
        assert await _until(stack.sim_keyed, 5.0), "simulator did not key"
        submission = await stack.authority.submit_force_off()
        assert submission.outcome is not None
        # No settlement wait: SIGTERM lands while the release is in flight.
        await _cli_order_shutdown(stack)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(submission.wait_settlement(), 5.0)

        errors = _keyed_errors(caplog)
        assert errors == [], errors
        assert not stack.sim_keyed(), "simulator still keyed after shutdown"
        assert any(
            "force_receive accepted; release drained" in record.getMessage()
            for record in _managed_tx_records(caplog)
        ), [record.getMessage() for record in _managed_tx_records(caplog)]


@pytest.mark.parametrize("profile", sorted(_BUILDERS))
async def test_clean_shutdown_with_stopped_commander_still_drains(
    profile: str, tmp_path: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """Nothing owed + Commander already stopped (MOR-3999) still drains.

    Models the real-hardware state: after the keyed sequence disturbed the
    link, recovery stopped the CI-V Commander without completing a re-arm
    (provider generation still 1), so the shutdown's release attempt runs
    against a stopped Commander. Nothing is owed, so the shutdown must not
    manufacture a release attempt — and must not report "may still be keyed".
    """
    caplog.set_level(logging.INFO)
    async with _use_stack(profile, tmp_path) as stack:
        assert await stack.authority.transmit_on()
        assert await _until(stack.sim_keyed, 5.0), "simulator did not key"
        assert await stack.authority.force_off()
        assert await _until(
            lambda: _clean(stack.authority) and not stack.sim_keyed(), 5.0
        ), "release did not settle before shutdown"
        # The production seam link recovery and disconnect use to stop the
        # Commander; staged here to model the stopped Commander at generation 1.
        await stack.radio._stop_civ_worker()

        await _cli_order_shutdown(stack)

        assert _keyed_errors(caplog) == [], [
            record.getMessage() for record in caplog.records if record.levelname == "ERROR"
        ]
        assert not any(
            "force_receive not confirmed" in record.getMessage()
            for record in caplog.records
            if record.levelname == "WARNING"
        ), [record.getMessage() for record in caplog.records]
        assert _clean(stack.authority)
        assert not stack.sim_keyed()
        assert any(
            "force_receive accepted; release drained" in record.getMessage()
            for record in _managed_tx_records(caplog)
        ), [record.getMessage() for record in _managed_tx_records(caplog)]


@pytest.mark.parametrize("profile", sorted(_BUILDERS))
async def test_shutdown_while_keyed_releases_before_commander_stops(
    profile: str, tmp_path: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """Case (b): still KEYED at shutdown — the OFF must beat the Commander stop."""
    caplog.set_level(logging.INFO)
    async with _use_stack(profile, tmp_path) as stack:
        assert await stack.authority.transmit_on()
        assert await _until(stack.sim_keyed, 5.0), "simulator did not key"
        assert stack.sim_keyed()

        await _cli_order_shutdown(stack)

        errors = [
            record.getMessage()
            for record in caplog.records
            if record.levelname == "ERROR"
            and "may still be keyed" in record.getMessage()
        ]
        assert errors == [], errors
        assert not stack.sim_keyed(), "simulator still keyed after shutdown"
        assert any(
            "force_receive accepted; release drained" in record.getMessage()
            for record in _managed_tx_records(caplog)
        ), [record.getMessage() for record in _managed_tx_records(caplog)]
