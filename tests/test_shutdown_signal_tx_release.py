"""MOR-4102: release every managed TX owner before slow web teardown."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane import cli
from rigplane.profiles import resolve_radio_profile
from rigplane.rigctld.server import RigctldServer
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.testing.timing import await_until
from rigplane.web import server as web_server
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.server import WebConfig, WebServer
from test_managed_tx_production_fake_wire import (
    _IcomActuator,
    _EofWebSocket,
    _drain_managed_positive_queue,
    _http_tx_command,
    _wait_for_settlement,
)


class _Radio(_IcomActuator):
    def __init__(self) -> None:
        super().__init__()
        self.release_on.set()
        self.profile = resolve_radio_profile(model="IC-9700")
        self.model = self.profile.model
        self.capabilities = set(self.profile.capabilities)
        self.connected = self.radio_ready = True
        self.exit_calls = 0

    async def __aenter__(self) -> _Radio:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        self.exit_calls += 1
        return False

    async def set_ptt(self, on: bool) -> None:
        self.raw_ptt.append(on)


class _Shutdown:
    def __init__(self) -> None:
        self.radio = _Radio()
        self.web: WebServer
        self.rigctld: RigctldServer
        self.signal = None
        self.blocked = asyncio.Event()
        self.resume = asyncio.Event()
        self.web_stop_completed = False
        self.arm_calls = 0

    async def assert_early_unkey(self, owner: str) -> None:
        self.signal()
        await asyncio.wait_for(self.blocked.wait(), 5)
        # The event is set inside the audio stop, before its gate is awaited.
        # Wait for the concrete wire event without opening that gate.
        try:
            await self.radio.wait_for_wire([True, False])
        except TimeoutError:
            pytest.fail(f"{owner} remains keyed while web stop is blocked")
        assert not self.resume.is_set()
        assert not self.web_stop_completed
        await _wait_for_settlement(self.composition)


@asynccontextmanager
async def _shutdown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, owner: str
) -> AsyncIterator[_Shutdown]:
    run = _Shutdown()
    run.composition = ManagedTxComposition(run.radio, config_path=tmp_path / "tx.json")
    install_managed_tx_composition(run.radio, run.composition)
    real_start = RigctldServer.start
    drain = None
    client_writer = None
    keyed = False

    async def start_rigctld(server: RigctldServer) -> None:
        run.rigctld = server
        server._state_store = run.web.command_state_store
        await real_start(server)

    async def slow_relay_stop() -> None:
        run.blocked.set()
        await run.resume.wait()
        run.web_stop_completed = True

    async def start_web(server: WebServer) -> None:
        nonlocal drain
        server.command_queue.bind_connection_generation(lambda: run.radio)
        server._server = MagicMock(wait_closed=AsyncMock())
        drain = asyncio.create_task(_drain_managed_positive_queue(server))
        server._audio_broadcaster._stop_relay = slow_relay_stop

    def make_web(radio: _Radio, _config: WebConfig) -> WebServer:
        run.web = WebServer(radio, WebConfig(host="127.0.0.1", port=0, discovery=False))
        return run.web

    def install_signal(_loop: object, handler: object) -> None:
        run.signal = handler

    monkeypatch.setattr(web_server, "WebServer", make_web)
    monkeypatch.setattr(WebServer, "start", start_web)
    monkeypatch.setattr(RigctldServer, "start", start_rigctld)
    monkeypatch.setattr(web_server, "_install_shutdown_signal_handlers", install_signal)
    monkeypatch.setattr(cli, "_print_startup_banner", lambda **_kwargs: None)
    monkeypatch.setattr(cli, "_detect_loopback_hint", lambda: None)
    args = cli._build_parser().parse_args(
        ["--host", "127.0.0.1", "web", "--rigctld-port", "0"]
    )
    args.web_bridge = None

    async def shutdown_sequence() -> None:
        async with run.session:
            assert (
                await cli._cmd_web(
                    run.radio,
                    args,
                    managed_tx_composition=run.composition,
                    shutdown_backstop=run.backstop,
                )
                == 0
            )

    task = asyncio.create_task(shutdown_sequence())
    run.backstop = cli._ShutdownBackstop(task, bound_s=60)
    arm = run.backstop.arm

    def counted_arm() -> None:
        run.arm_calls += 1
        arm()

    monkeypatch.setattr(run.backstop, "arm", counted_arm)
    run.session = cli._ManagedTxRadioSession(run.radio, run.composition, run.backstop)
    try:
        await await_until(lambda: run.signal is not None or task.done())
        if task.done():
            await task
            pytest.fail("CLI returned before installing the shutdown handler")
        if owner == "rigctld":
            port = run.rigctld._server.sockets[0].getsockname()[1]
            reader, client_writer = await asyncio.open_connection("127.0.0.1", port)
            client_writer.write(b"T 1\n")
            await client_writer.drain()
            assert await asyncio.wait_for(reader.readline(), 5) == b"RPRT 0\n"
        elif owner == "http":
            await _http_tx_command(run.web, "transmit_on")
        else:
            controller = run.web._controller
            grant = controller.acquire("browser")
            ticket = controller.attach(grant["controller_key"], "primary", "ws-1")
            handler = ControlHandler(
                ws=_EofWebSocket(),
                radio=run.radio,
                server_version="test",
                radio_model=run.radio.model,
                server=run.web,
                session_id="ws-1",
                tx_owner=ticket.tx_owner,
                managed_tx_authority=run.composition.authority,
            )
            with controller.bind(ticket):
                await handler._enqueue_command("ptt_on", {})
        await run.radio.wait_for_wire([True])
        await _wait_for_settlement(run.composition)
        keyed = True
        projection = await run.composition.authority.snapshot()
        if owner == "rigctld":
            assert projection.state.intent.owner_token == "rigctld-client-1"
        elif owner == "http":
            assert projection.state.intent.owner_token is None
        else:
            assert projection.state.intent.owner_token == ticket.tx_owner
        assert run.radio.raw_ptt == []
        yield run
    finally:
        run.resume.set()
        if run.signal is not None and not task.done() and run.arm_calls == 0:
            run.signal()
        try:
            await asyncio.wait_for(asyncio.shield(task), 10)
        finally:
            run.backstop.disarm()
            if drain is not None:
                drain.cancel()
                await asyncio.gather(drain, return_exceptions=True)
            if client_writer is not None:
                client_writer.close()
                await client_writer.wait_closed()
            await run.session.shutdown()
            await run.web._controller.settle()
        assert run.radio.exit_calls == 1
        if keyed:
            assert run.radio.wire[0] is True
            assert run.radio.wire[-1] is False
        assert run.radio.raw_ptt == []


@pytest.mark.asyncio
async def test_first_signal_releases_rigctld_owner_before_web_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async with _shutdown(monkeypatch, tmp_path, "rigctld") as run:
        await run.assert_early_unkey("rigctld owner")


@pytest.mark.asyncio
async def test_first_signal_releases_http_owner_before_web_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async with _shutdown(monkeypatch, tmp_path, "http") as run:
        await run.assert_early_unkey("HTTP owner")


@pytest.mark.asyncio
async def test_first_signal_still_releases_controller_session_owner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async with _shutdown(monkeypatch, tmp_path, "web") as run:
        await run.assert_early_unkey("controller session owner")


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["rigctld", "http", "web"])
async def test_late_handbacks_do_not_repeat_first_signal_unkey(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    owner: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with _shutdown(monkeypatch, tmp_path, owner) as run:
        await run.assert_early_unkey(f"{owner} owner awaiting idempotent handbacks")
        if owner == "rigctld":
            await run.rigctld._release_session_tx("rigctld-client-1")
        run.web._controller.revoke()
        await run.web._controller.settle()
        await run.session.shutdown()
        assert run.radio.wire == [True, False]
    assert run.radio.wire == [True, False]
    assert run.radio.exit_calls == 1
    assert not [
        record
        for record in caplog.records
        if record.levelname in {"WARNING", "ERROR", "CRITICAL"} and record.exc_info
    ]


@pytest.mark.asyncio
async def test_second_signal_cancels_tasks_without_arming_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async with _shutdown(monkeypatch, tmp_path, "web") as run:
        run.signal()
        await asyncio.wait_for(run.blocked.wait(), 5)
        victim = asyncio.create_task(asyncio.Event().wait())
        # Isolate the cancellation target; the real handler still cancels
        # every task returned by all_tasks, including a not-yet-started task.
        with monkeypatch.context() as signal_patch:
            signal_patch.setattr(
                web_server.asyncio, "all_tasks", lambda _loop: {victim}
            )
            run.signal()
        result = await asyncio.gather(victim, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        assert run.arm_calls == 1


@pytest.mark.asyncio
async def test_first_signal_release_start_failure_still_arms_clock(
    caplog: pytest.LogCaptureFixture,
) -> None:
    backstop = cli._ShutdownBackstop(asyncio.current_task(), bound_s=60)

    def fail() -> asyncio.Task[None]:
        raise RuntimeError("release could not start")

    backstop.set_first_signal_release(fail)
    try:
        backstop.arm()
        clock = backstop._clock
        assert clock is not None
        backstop.arm()
        assert backstop._clock is clock
        assert (
            sum(
                "could not start managed TX shutdown" in r.message
                for r in caplog.records
            )
            == 1
        )
    finally:
        backstop.disarm()
