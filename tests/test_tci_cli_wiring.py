"""C4 RED: end-to-end CLI wiring for the TCI listener (MOR-3821, design 9/10.3).

The real ``_cmd_web`` wiring with ``--tci --tci-port 0`` against a
simulator-backed radio: a real ``eesdr-tci`` Listener reads the init
burst, sets a frequency and reads it back, programs DIGU, keys and
unkeys with the managed composition present (simulator, no real TX),
then stops. Start/stop twice; a busy TCI port aborts startup.
"""

from __future__ import annotations

import asyncio
import errno
import importlib
import socket
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.core.state_store import StateStore
from rigplane.runtime.managed_tx_authority import ManagedTxAuthority
from rigplane.runtime.managed_tx_fence import TxAbortFence
from test_managed_tx_authority import FakeConfigStore, FakeLane

from tci_adapter_stub import StubCommandQueue, StubCommandService, StubIcomRadio

pytest.importorskip("eesdr_tci", reason="mini-only dev dependency")
listener_mod = importlib.import_module("eesdr_tci.listener")

Listener = listener_mod.Listener

from rigplane.tci_adapter.server import TciAdapterServer  # noqa: E402


def _loaded_store() -> StateStore:
    from tci_adapter_stub import build_store

    return build_store(
        {
            FieldPath.active("main", "freq_mode", "freq_hz"): 14_074_000,
            FieldPath.active("main", "freq_mode", "mode"): "USB",
            FieldPath.active("main", "freq_mode", "data_mode"): 0,
            FieldPath.global_("tx_state", "split"): False,
            FieldPath.global_("operator_controls", "rit_freq"): 0,
            FieldPath.global_("tx_state", "rit_on"): False,
            FieldPath.global_("tx_state", "rit_tx"): False,
            FieldPath.global_("operator_controls", "power_level"): 128,
        }
    )


def _data2_radio(store: StateStore) -> StubIcomRadio:
    from tci_adapter_stub import StubProfile

    radio = StubIcomRadio(profile=StubProfile(data_mode_count=3))
    radio.mod_inputs = {2: 5}
    radio.observe_store = store
    return radio


class _E2E:
    """One wired ``_cmd_web`` run with a real TCI server underneath."""

    def __init__(self) -> None:
        self.lane = FakeLane()
        self.authority = ManagedTxAuthority(
            self.lane,
            FakeConfigStore(10.0),
            TxAbortFence(),
            provider_generation=7,
            attempt_timeout_seconds=1.0,
            retry_delay_seconds=2.0,
        )
        self.queue = StubCommandQueue()
        self.store = _loaded_store()
        self.radio = _data2_radio(self.store)
        self.service = StubCommandService()
        self.composition = SimpleNamespace(
            authority=self.authority,
            bind_state_store=AsyncMock(),
        )
        self.servers: list[TciAdapterServer] = []
        outer = self

        class RecordingTciServer(TciAdapterServer):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                outer.servers.append(self)

        self._recording = RecordingTciServer
        self.stop_event = asyncio.Event()

    def patches(self):  # type: ignore[no-untyped-def]
        from rigplane.cli import _cmd_web  # noqa: F401

        outer = self

        class FakeWebServer:
            def __init__(self, _radio: Any, _cfg: Any) -> None:
                self.command_queue = outer.queue
                self.command_service = outer.service
                self.command_state_store = outer.store

            def attach_rigctl_channel(self, _open_channel: Any) -> None:
                pass

            async def serve_forever(self, *, on_started=None):  # type: ignore[no-untyped-def]
                if on_started is not None:
                    on_started()
                await outer.stop_event.wait()

        return (
            patch("rigplane.web.server.WebServer", FakeWebServer),
            patch(
                "rigplane.tci_adapter.server.TciAdapterServer", self._recording
            ),
            patch(
                "rigplane.web.web_startup.prepare_managed_tx_observation_generation",
                return_value=3,
            ),
            patch("rigplane.web.web_startup.attach_managed_tx_composition"),
            patch("rigplane.cli._detect_loopback_hint", return_value=None),
        )

    async def run_web(self) -> int:
        from rigplane.cli import _build_parser, _cmd_web

        args = _build_parser().parse_args(
            ["--host", "1.2.3.4", "web", "--no-rigctld", "--tci", "--tci-port", "0"]
        )
        args.web_bridge = None
        patches = self.patches()
        for entered in patches:
            entered.__enter__()
        try:
            return await _cmd_web(
                self.radio, args, managed_tx_composition=self.composition
            )
        finally:
            for entered in reversed(patches):
                entered.__exit__(None, None, None)

    async def stop_web(self, web_task: "asyncio.Task[int]") -> int:
        self.stop_event.set()
        async with asyncio.timeout(10.0):
            return await web_task


@pytest.fixture()
def tcp_available() -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
    except OSError as exc:
        pytest.skip(f"loopback bind unavailable: {exc}")
    finally:
        probe.close()


async def _wait_until(predicate, timeout: float = 5.0) -> None:  # type: ignore[no-untyped-def]
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


async def _drive_session(e2e: _E2E) -> None:
    async with asyncio.timeout(10.0):
        await _wait_until(lambda: len(e2e.servers) == 1)
    server = e2e.servers[0]
    assert server._authority is e2e.authority  # noqa: SLF001
    observed: list[tuple[object, object, object, object]] = []

    async def on_param(name: object, rx: object, sub_rx: object, params: object) -> None:
        observed.append((name, rx, sub_rx, params))

    listener = Listener(f"ws://127.0.0.1:{server.bound_port}")
    listener.add_param_listener("*", on_param)
    await listener.start()
    try:
        await listener.ready()
        await _wait_until(lambda: ("READY", None, None, None) in observed)
        assert ("VFO", 0, 0, 14074000) in observed
        assert ("RECEIVE_ONLY", None, None, False) in observed
        assert ("TX_ENABLE", 0, None, True) in observed

        await listener.send("vfo:0,0,14074001;")
        await _wait_until(lambda: ("VFO", 0, 0, 14074001) in observed)

        await listener.send("modulation:0,DIGU;")
        await _wait_until(lambda: ("MODULATION", 0, None, "DIGU") in observed)

        await listener.send("trx:0,true;")
        await _wait_until(lambda: ("TRX", 0, None, True) in observed)
        assert [e.operation.name for e in e2e.lane.effects] == ["PTT_ON"]
        snapshot = await e2e.authority.snapshot()
        from rigplane.runtime.managed_tx_state import ManagedTxIntentKind

        assert snapshot.state.intent.kind is ManagedTxIntentKind.PTT

        await listener.send("trx:0,false;")
        await _wait_until(lambda: ("TRX", 0, None, False) in observed)
    finally:
        listener.shutdown()
        try:
            await listener.wait()
        except (asyncio.CancelledError, Exception):
            pass
    snapshot = await e2e.authority.snapshot()
    from rigplane.runtime.managed_tx_state import ManagedTxIntentKind

    assert snapshot.state.intent.kind is ManagedTxIntentKind.RX


async def test_tci_wiring_end_to_end(tcp_available: None) -> None:
    e2e = _E2E()
    web_task = asyncio.ensure_future(e2e.run_web())
    try:
        await _drive_session(e2e)
    finally:
        assert await e2e.stop_web(web_task) == 0
    await e2e.authority.close()


async def test_tci_wiring_start_stop_twice(tcp_available: None) -> None:
    for _ in range(2):
        e2e = _E2E()
        web_task = asyncio.ensure_future(e2e.run_web())
        try:
            await _drive_session(e2e)
        finally:
            assert await e2e.stop_web(web_task) == 0
        await e2e.authority.close()


async def test_tci_busy_port_aborts_startup(tcp_available: None) -> None:
    from rigplane.cli import _build_parser, _cmd_web

    class BusyTciServer:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def start(self) -> None:
            raise OSError(errno.EADDRINUSE, "Address already in use")

        async def stop(self) -> None:  # pragma: no cover - never called
            pass

    class FakeWebServer:
        def __init__(self, _radio: Any, _cfg: Any) -> None:
            pass

        async def serve_forever(self, *, on_started=None):  # type: ignore[no-untyped-def]
            raise AssertionError("web must not start when TCI fails hard")

    store = _loaded_store()
    args = _build_parser().parse_args(
        ["--host", "1.2.3.4", "web", "--no-rigctld", "--tci"]
    )
    args.web_bridge = None
    with (
        patch("rigplane.web.server.WebServer", FakeWebServer),
        patch("rigplane.tci_adapter.server.TciAdapterServer", BusyTciServer),
        patch("rigplane.cli._detect_loopback_hint", return_value=None),
    ):
        with pytest.raises(OSError) as exc_info:
            await _cmd_web(_data2_radio(store), args)
    assert exc_info.value.errno == errno.EADDRINUSE
