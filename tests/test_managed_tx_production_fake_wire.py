import asyncio
import json
from collections.abc import Callable
from typing import Any

import pytest

from rigplane.backends.rigctld_client.radio import RigctldClientRadio
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.core.civ import CivRequestTracker
from rigplane.core.state_store import StateStore
from rigplane.core.tx_observation import TxStateReading
from rigplane.core.types import CivFrame
from rigplane.profiles import resolve_radio_profile
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer
from rigplane.runtime._poller_types import (
    execute_command_queue_entry,
    execute_positive_tx_queue_entry,
    validate_command_queue_entry_currency,
)
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.runtime.managed_tx_state import (
    AbortOperation,
    ActuationOperation,
    ActuationResult,
    EffectToken,
    ManagedTxIntentKind,
)
from rigplane.runtime.radio import CoreRadio
from rigplane.testing.timing import VirtualClock
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.web_startup import attach_managed_tx_composition


class _DelayedWire:
    def __init__(self) -> None:
        self.on_started = asyncio.Event()
        self.release_on = asyncio.Event()
        self.wire: list[bool] = []
        self.wire_changed = asyncio.Event()
        self.raw_ptt: list[bool] = []

    async def finish(self, on: bool, is_current: Callable[[], bool]) -> None:
        if on:
            self.on_started.set()
            try:
                await self.release_on.wait()
            except asyncio.CancelledError:
                await self.release_on.wait()
        if is_current():
            self.wire.append(on)
            self.wire_changed.set()

    async def wait_for_wire(self, expected: list[bool]) -> None:
        async def wait() -> None:
            while self.wire != expected:
                self.wire_changed.clear()
                if self.wire != expected:
                    await self.wire_changed.wait()

        await asyncio.wait_for(wait(), 1)


class _IcomActuator(_DelayedWire):
    actuate = CoreRadio.actuate
    ICOM_PTT_ON_ACK_TIMEOUT_S = CoreRadio.ICOM_PTT_ON_ACK_TIMEOUT_S

    def __init__(self) -> None:
        super().__init__()
        self._commands = type(
            "Commands",
            (),
            {
                "ptt_on": staticmethod(lambda **_kw: b"ON"),
                "ptt_off": staticmethod(lambda **_kw: b"OFF"),
                "stop_cw": staticmethod(lambda **_kw: b"CW-OFF"),
                "set_tuner_status": staticmethod(lambda *_a, **_kw: b"TUNE-OFF"),
            },
        )()
        self._radio_addr = 0x94
        # ``CoreRadio.actuate`` distrusts the unkey's answer while the tracker
        # holds another write's unclaimed answer (MOR-2860); none here does.
        self._civ_request_tracker = CivRequestTracker()
        self._civ_get_timeout = 2.0

    async def _send_civ_raw(
        self,
        frame: bytes,
        *,
        is_current: Callable[[], bool],
        wait_response: bool,
        **_kw: Any,
    ) -> CivFrame | None:
        if frame in (b"ON", b"OFF"):
            await self.finish(frame == b"ON", is_current)
        # The production actuator awaits FB/FA for both key and unkey writes.
        if wait_response:
            return CivFrame(to_addr=0xE0, from_addr=self._radio_addr, command=0xFB)
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation", [ActuationOperation.PTT_ON, ActuationOperation.TRANSMIT_ON]
)
async def test_shared_icom_fake_keys_and_releases_with_production_actuator(
    operation: ActuationOperation,
) -> None:
    actuator = _IcomActuator()
    actuator.release_on.set()
    token = EffectToken(1, 1, "shared-icom-fake")

    keyed = await actuator.actuate(token, operation, is_current=lambda: True)

    assert keyed is ActuationResult.ACCEPTED
    assert actuator.on_started.is_set()
    assert actuator.wire == [True]
    released = await actuator.actuate(
        token, ActuationOperation.FORCE_RECEIVE, is_current=lambda: True
    )
    assert released is ActuationResult.ACCEPTED
    assert actuator.wire == [True, False]


class _YaesuActuator(_DelayedWire):
    actuate = YaesuCatRadio.actuate

    async def _write(
        self,
        command: str,
        *,
        is_current: Callable[[], bool],
        **params: Any,
    ) -> None:
        if command == "set_ptt":
            await self.finish(params["state"] == "1", is_current)

    async def read_ptt(
        self,
        *,
        is_current: Callable[[], bool] | None = None,
        tier: Any = None,
    ) -> bool:
        # MOR-2862: answer the in-actuation confirming read the way a
        # radio would — the PTT state this fake last wrote.
        del is_current, tier
        return self.wire[-1] if self.wire else False


class _RigctldActuator(_DelayedWire):
    actuate = RigctldClientRadio.actuate
    # ``actuate`` skips the unkey read-back while the transmit-state read is
    # not answerable (MOR-4456); borrow that one concept and its two facts
    # from the producer, starting answerable like a fresh client.
    _ptt_read_answerable = RigctldClientRadio._ptt_read_answerable

    def __init__(self) -> None:
        super().__init__()
        self._ptt_read_demoted = False
        self._ptt_read_refused = False

    async def _set_ptt(
        self,
        on: bool,
        *,
        is_current: Callable[[], bool],
        urgent: bool,
    ) -> None:
        del urgent
        await self.finish(on, is_current)

    async def _read_ptt(
        self,
        *,
        is_current: Callable[[], bool] | None = None,
        urgent: bool = False,
    ) -> bool:
        # MOR-2862: answer the in-actuation confirming read the way the
        # daemon would — the PTT state this fake last wrote.
        del is_current, urgent
        return self.wire[-1] if self.wire else False


class _HttpWriter:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None

    def get_extra_info(self, name: str) -> object:
        return ("127.0.0.1", 1234) if name == "peername" else None

    @property
    def status(self) -> int:
        return int(self.buffer.split(b" ", 2)[1])


class _EofWebSocket:
    outbound_progress: int = 0

    async def send_text(self, _text: str) -> None:
        return None

    async def recv(self) -> tuple[int, bytes]:
        raise EOFError


async def _http_tx_command(server: WebServer, operation: str) -> None:
    body = json.dumps({"operation": operation}).encode()
    reader = asyncio.StreamReader()
    reader.feed_data(body)
    reader.feed_eof()
    writer = _HttpWriter()
    await server._handle_http(  # noqa: SLF001
        writer,  # type: ignore[arg-type]
        "POST",
        "/api/v1/managed-transmit/command",
        headers={"content-length": str(len(body))},
        reader=reader,
    )
    assert writer.status == 202


async def _wait_for_settlement(composition: ManagedTxComposition) -> None:
    async def wait() -> None:
        while (await composition.authority.snapshot()).state.pending_effect is not None:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), 1)


def _control_handler(
    server: WebServer,
    actuator: _DelayedWire,
    composition: ManagedTxComposition,
    session_id: str,
) -> ControlHandler:
    return ControlHandler(
        ws=_EofWebSocket(),  # type: ignore[arg-type]
        radio=actuator,  # type: ignore[arg-type]
        server_version="test",
        radio_model="IC-9700",
        server=server,
        session_id=session_id,
        managed_tx_authority=composition.authority,
    )


async def _joined_web_runtime(
    tmp_path: Any, actuator_type: type[_DelayedWire], **web_options: Any
) -> tuple[_DelayedWire, ManagedTxComposition, WebServer]:
    actuator = actuator_type()
    actuator.release_on.set()
    profile = resolve_radio_profile(model="IC-9700")
    actuator.profile = profile  # type: ignore[attr-defined]
    actuator.model = profile.model  # type: ignore[attr-defined]
    actuator.capabilities = set(profile.capabilities)  # type: ignore[attr-defined]
    actuator.connected = True  # type: ignore[attr-defined]
    actuator.radio_ready = True  # type: ignore[attr-defined]

    async def raw_set_ptt(on: bool) -> None:
        actuator.raw_ptt.append(on)

    actuator.set_ptt = raw_set_ptt  # type: ignore[attr-defined]
    composition = ManagedTxComposition(
        actuator, config_path=tmp_path / f"{actuator_type.__name__}-web.json"
    )
    install_managed_tx_composition(actuator, composition)
    actuator.set_ptt = raw_set_ptt  # type: ignore[attr-defined]
    server = WebServer(
        actuator,  # type: ignore[arg-type]
        WebConfig(host="127.0.0.1", port=0, **web_options),
    )
    server.command_queue.bind_connection_generation(lambda: actuator)
    await composition.transport_ready(actuator)
    await composition.bind_state_store(server.command_state_store)
    attach_managed_tx_composition(server, composition)
    return actuator, composition, server


async def _drain_managed_positive_queue(server: WebServer) -> None:
    while True:
        await server.command_queue.wait(0.01)
        while (entry := server.command_queue.take_entry()) is not None:
            validate_command_queue_entry_currency(
                entry,
                now=asyncio.get_running_loop().time(),
                provider_generation=server.command_state_store.provider_generation,
                connection_generation=server.command_queue.capture_connection_generation(),
                session_is_live=server.command_queue.session_is_live,
                require_connection_generation=True,
            )
            await execute_command_queue_entry(entry, execute_positive_tx_queue_entry)


class _PrepareStopWire(_DelayedWire):
    def __init__(self) -> None:
        super().__init__()
        self.force_started = asyncio.Event()
        self.allow_force = asyncio.Event()
        self.allow_force.set()
        self.reads: list[bool] = []
        self.answers = True

    async def actuate(
        self,
        _token: EffectToken,
        operation: ActuationOperation | AbortOperation,
        *,
        is_current: Callable[[], bool],
    ) -> ActuationResult:
        if operation is ActuationOperation.FORCE_RECEIVE:
            self.force_started.set()
            await self.allow_force.wait()
            await self.finish(False, is_current)
        elif isinstance(operation, ActuationOperation):
            await self.finish(True, is_current)
        return ActuationResult.ACCEPTED

    async def read_transmit_state(self) -> TxStateReading:
        if not self.answers:
            return TxStateReading(None, failure="no_response")
        keyed = bool(self.wire and self.wire[-1])
        self.reads.append(keyed)
        return TxStateReading(keyed, verified_readback=True)


async def _post_managed_tx(
    server: WebServer,
    suffix: str,
    payload: dict[str, object],
    *,
    token: str | None = "existing-station-token",
    writer: _HttpWriter | None = None,
) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload).encode()
    reader = asyncio.StreamReader()
    reader.feed_data(body)
    reader.feed_eof()
    headers = {"content-length": str(len(body))}
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    writer = writer if writer is not None else _HttpWriter()
    await server._handle_http(  # noqa: SLF001
        writer,  # type: ignore[arg-type]
        "POST",
        f"/api/{'v1' if suffix == 'command' else 'local/v1'}/managed-transmit/{suffix}",
        headers=headers,
        reader=reader,
    )
    return writer.status, json.loads(writer.buffer.split(b"\r\n\r\n", 1)[1])


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_prepare_stop_reports_current_binding_evidence(virtual_clock, tmp_path):
    actuator, composition, server = await _joined_web_runtime(
        tmp_path, _PrepareStopWire, runtime_auth_token="existing-station-token"
    )
    assert isinstance(actuator, _PrepareStopWire)
    drain = asyncio.create_task(_drain_managed_positive_queue(server))
    try:
        status, reply = await _post_managed_tx(server, "prepare-stop", {})
        assert status == 200
        assert reply["bindings"] == [
            {"providerGeneration": 1, "outcome": "never_keyed", "reason": "no_keying"}
        ]
        assert actuator.wire == []
        assert (await _post_managed_tx(server, "resume", {}))[0] == 200

        await _http_tx_command(server, "transmit_on")
        await virtual_clock.elapse(0)
        assert actuator.wire == [True]
        actuator.answers = False
        status, reply = await _post_managed_tx(server, "prepare-stop", {})
        assert status == 200
        assert reply["bindings"][0]["outcome"] == "unconfirmed"
        assert reply["bindings"][0]["reason"] == "no_response"
        assert actuator.wire == [True, False]

        actuator.answers = True
        status, reply = await _post_managed_tx(server, "prepare-stop", {})
        assert status == 200
        assert reply["bindings"] == [
            {
                "providerGeneration": 1,
                "outcome": "released",
                "reason": "receive_confirmed",
            }
        ]
        assert actuator.wire[-1] is False
        assert actuator.reads == [False, False, False]  # startup, idle, fresh RX
        # Legacy emergency OFF remains available during the lease.
        await _http_tx_command(server, "force_off")
    finally:
        drain.cancel()
        await asyncio.gather(drain, return_exceptions=True)
        await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
@pytest.mark.timeout(10)
@pytest.mark.parametrize("reopen", ["resume", "expiry"])
async def test_prepare_stop_fences_cat_web_and_provider_admission(
    reopen, virtual_clock: VirtualClock, monkeypatch, tmp_path
):
    from rigplane.runtime import managed_tx_authority

    virtual_clock.scope_to(monkeypatch, managed_tx_authority)
    actuator, composition, server = await _joined_web_runtime(
        tmp_path, _PrepareStopWire, runtime_auth_token="existing-station-token"
    )
    assert isinstance(actuator, _PrepareStopWire)
    drain = asyncio.create_task(_drain_managed_positive_queue(server))
    cat = RigctldServer(
        actuator,  # type: ignore[arg-type]
        RigctldConfig(host="127.0.0.1", port=0),
        allow_not_ready=True,
        managed_tx_authority=composition.authority,
        command_queue=server.command_queue,
        command_service=server.command_service,
    )
    await cat.start()
    assert cat._server is not None  # noqa: SLF001
    cat_reader, cat_writer = await asyncio.open_connection(
        "127.0.0.1",
        cat._server.sockets[0].getsockname()[1],  # noqa: SLF001
    )
    try:
        actuator.release_on.clear()
        await _http_tx_command(server, "transmit_on")
        await actuator.on_started.wait()
        actuator.allow_force.clear()
        prepare = asyncio.create_task(_post_managed_tx(server, "prepare-stop", {}))
        await actuator.force_started.wait()

        cat_writer.write(b"T 1\n")
        await cat_writer.drain()
        assert await cat_reader.readline() not in (b"RPRT 0\n", b"")
        status, _ = await _post_managed_tx(
            server, "command", {"operation": "transmit_on"}
        )
        assert status >= 400
        actuator.release_on.set()
        actuator.allow_force.set()
        assert (await prepare)[1]["bindings"][0]["outcome"] == "released"
        assert actuator.wire and not any(actuator.wire)

        await composition.transport_unavailable(actuator)
        await composition.transport_ready(object())
        assert (await composition.authority.snapshot()).provider_generation is None

        if reopen == "resume":
            assert (await _post_managed_tx(server, "resume", {}))[0] == 200
        else:
            lease = managed_tx_authority.PREPARE_STOP_LEASE_SECONDS
            await virtual_clock.elapse(lease / 2)
            assert (await _post_managed_tx(server, "prepare-stop", {}))[0] == 200
            await virtual_clock.elapse(lease / 2)
            assert (await composition.authority.snapshot()).provider_generation is None
            await virtual_clock.elapse(lease / 2)
        await virtual_clock.elapse(0)
        assert (await composition.authority.snapshot()).provider_generation == 2
        await _http_tx_command(server, "transmit_on")
        await virtual_clock.elapse(0)
        assert actuator.wire[-1] is True
        await _http_tx_command(server, "force_off")
        await virtual_clock.elapse(0)
        cat_writer.write(b"T 1\n")
        await cat_writer.drain()
        assert await cat_reader.readline() == b"RPRT 0\n"
        await virtual_clock.elapse(0)
        assert actuator.wire[-1] is True
        cat_writer.write(b"T 0\n")
        await cat_writer.drain()
        assert await cat_reader.readline() == b"RPRT 0\n"
        await virtual_clock.elapse(0)
        assert actuator.wire[-1] is False
    finally:
        actuator.release_on.set()
        actuator.allow_force.set()
        cat_writer.close()
        await cat_writer.wait_closed()
        await cat.stop()
        drain.cancel()
        await asyncio.gather(drain, return_exceptions=True)
        await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_prepare_stop_requires_runtime_bearer_and_a_real_loopback_peer(tmp_path):
    class RemoteWriter(_HttpWriter):
        def get_extra_info(self, name: str) -> object:
            if name == "peername":
                return ("192.0.2.1", 1234)
            return None

    class ChannelWriter(_HttpWriter):
        def get_extra_info(self, name: str) -> object:
            return name == "rigplane_api_channel" or super().get_extra_info(name)

    actuator, composition, server = await _joined_web_runtime(
        tmp_path, _PrepareStopWire, runtime_auth_token="existing-station-token"
    )
    assert isinstance(actuator, _PrepareStopWire)
    try:
        for suffix in ("prepare-stop", "resume"):
            assert (await _post_managed_tx(server, suffix, {}, token=None))[0] == 401
            assert (await _post_managed_tx(server, suffix, {}, token="wrong"))[0] == 401
            for writer in (RemoteWriter(), ChannelWriter()):
                assert (await _post_managed_tx(server, suffix, {}, writer=writer))[
                    0
                ] == 403
            assert (await _post_managed_tx(server, suffix, {"extra": True}))[0] == 400
        assert actuator.wire == []
        assert actuator.reads == [False]  # startup only
    finally:
        await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actuator_type", [_IcomActuator, _YaesuActuator, _RigctldActuator]
)
async def test_real_provider_actuator_suppresses_stale_on_at_final_wire(
    tmp_path, actuator_type: type[_DelayedWire]
) -> None:
    actuator = actuator_type()
    composition = ManagedTxComposition(
        actuator, config_path=tmp_path / f"{actuator_type.__name__}.json"
    )
    store = StateStore()
    store.begin_provider_generation()
    await composition.transport_ready(actuator)
    await composition.bind_state_store(store)

    submission = await composition.authority.submit_ptt(True, "provider-owner")
    await asyncio.wait_for(actuator.on_started.wait(), 0.2)
    store.begin_provider_generation()
    actuator.release_on.set()
    await submission.wait_settlement()
    await composition.transport_ready(object())

    assert True not in actuator.wire
    assert actuator.wire[-1:] == [False]
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actuator_type", [_IcomActuator, _YaesuActuator, _RigctldActuator]
)
async def test_web_sessions_and_latched_commands_share_one_authority_to_final_wire(
    tmp_path, actuator_type: type[_DelayedWire]
) -> None:
    actuator, composition, server = await _joined_web_runtime(tmp_path, actuator_type)
    drain_task = asyncio.create_task(_drain_managed_positive_queue(server))
    session_a = _control_handler(server, actuator, composition, "session-a")
    session_b = _control_handler(server, actuator, composition, "session-b")
    try:
        assert session_a._managed_tx_authority is composition.authority  # noqa: SLF001
        assert session_b._managed_tx_authority is composition.authority  # noqa: SLF001
        assert server._managed_tx_authority() is composition.authority  # noqa: SLF001

        await session_a._enqueue_command("ptt_on", {})  # noqa: SLF001
        await actuator.wait_for_wire([True])
        await _wait_for_settlement(composition)

        await session_b.run()
        assert actuator.wire == [True]
        assert (
            await composition.authority.snapshot()
        ).state.intent.owner_token == "session-a"

        await _http_tx_command(server, "force_off")
        await actuator.wait_for_wire([True, False])
        await _wait_for_settlement(composition)
        await _http_tx_command(server, "transmit_on")
        await actuator.wait_for_wire([True, False, True])
        await _wait_for_settlement(composition)

        await session_a.run()
        projection = await composition.authority.snapshot()
        assert projection.state.intent.kind is ManagedTxIntentKind.TRANSMIT
        assert actuator.wire == [True, False, True]

        await _http_tx_command(server, "force_off")
        await actuator.wait_for_wire([True, False, True, False])
        await _wait_for_settlement(composition)
        assert not server.command_queue.drain()
        assert actuator.raw_ptt == []
    finally:
        drain_task.cancel()
        await asyncio.gather(drain_task, return_exceptions=True)
        await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actuator_type", [_IcomActuator, _YaesuActuator, _RigctldActuator]
)
async def test_replacement_and_shutdown_drain_off_debt_at_final_wire_without_on_replay(
    tmp_path, actuator_type: type[_DelayedWire]
) -> None:
    actuator = actuator_type()
    actuator.release_on.set()
    retired: list[tuple[int, tuple[bool, ...]]] = []

    async def retire(event: Any) -> None:
        retired.append((event.provider_generation, tuple(actuator.wire)))

    composition = ManagedTxComposition(
        actuator,
        config_path=tmp_path / f"{actuator_type.__name__}-lifecycle.json",
        retire_provider=retire,
    )
    authority = composition.authority
    fence = composition._abort_fence  # noqa: SLF001
    lane = composition._effect_lane  # noqa: SLF001
    store = StateStore()
    store.begin_provider_generation()
    first, replacement = object(), object()
    await composition.transport_ready(first)
    await composition.bind_state_store(store)

    first_on = await authority.submit_transmit_on()
    await first_on.wait_settlement()
    await actuator.wait_for_wire([True])

    store.begin_provider_generation()
    await composition.transport_ready(replacement)
    await actuator.wait_for_wire([True, False])
    assert [entry[0] for entry in retired] == [1]
    assert composition.authority is authority
    assert composition._abort_fence is fence  # noqa: SLF001
    assert composition._effect_lane is lane  # noqa: SLF001
    assert (await authority.snapshot()).provider_generation == 2

    replacement_on = await authority.submit_transmit_on()
    await replacement_on.wait_settlement()
    await actuator.wait_for_wire([True, False, True])

    await composition.shutdown(asyncio.Event())

    assert actuator.wire == [True, False, True, False]
    assert retired == [(1, (True,)), (2, (True, False, True, False))]
    assert composition.authority is authority
    assert composition._abort_fence is fence  # noqa: SLF001
    assert composition._effect_lane is lane  # noqa: SLF001


@pytest.mark.asyncio
@pytest.mark.parametrize("actuator_type", [_IcomActuator, _YaesuActuator])
@pytest.mark.parametrize("operation", ["ptt_on", "transmit_on"])
async def test_first_signal_fences_admitted_on_at_final_wire(
    tmp_path, monkeypatch, actuator_type: type[_DelayedWire], operation: str
) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from rigplane import cli
    from rigplane.runtime.managed_tx_authority import ShutdownResult
    from rigplane.web import server as web_server

    actuator, composition, server = await _joined_web_runtime(tmp_path, actuator_type)
    actuator.release_on.clear()
    signals = []

    async def start() -> None:
        server._server = MagicMock(wait_closed=AsyncMock())

    monkeypatch.setattr(server, "start", start)
    monkeypatch.setattr(server, "stop", AsyncMock())
    monkeypatch.setattr(
        web_server,
        "_install_shutdown_signal_handlers",
        lambda _loop, cb: signals.append(cb),
    )
    backstop = cli._ShutdownBackstop(asyncio.current_task(), bound_s=60)
    session = cli._ManagedTxRadioSession(actuator, composition, backstop)
    serving = asyncio.create_task(server.serve_forever(on_shutdown_signal=backstop.arm))
    try:
        if operation == "ptt_on":
            submission = await composition.authority.submit_ptt(True, "owner")
        else:
            submission = await composition.authority.submit_transmit_on()
        await actuator.on_started.wait()
        assert signals
        signals[0]()
        # Resume the final write before the newly scheduled shutdown tree runs.
        actuator.release_on.set()
        await submission.wait_settlement()
        await session.shutdown()
        await serving
        assert True not in actuator.wire
        assert actuator.wire[-1:] == [False]
        assert await composition.shutdown(asyncio.Event()) is ShutdownResult.DRAINED
    finally:
        actuator.release_on.set()
        backstop.disarm()
        await session.shutdown()
        serving.cancel()
        await asyncio.gather(serving, return_exceptions=True)


class _UnansweredReleaseActuator:
    """A radio that keys and stops CW but never confirms the unkey.

    Every release attempt fails, so the authority keeps the release owed and
    its scheduler retries it; ``retry_entered`` marks the scheduler's retry
    in flight. ``answer_release`` lets the test's cleanup end the retries.
    """

    def __init__(self) -> None:
        self.release_attempts = 0
        self.retry_entered = asyncio.Event()
        self.answer_release = False

    async def actuate(
        self,
        token: EffectToken,
        operation: ActuationOperation | AbortOperation,
        *,
        is_current: Callable[[], bool],
    ) -> ActuationResult:
        del token, is_current
        if operation is not ActuationOperation.FORCE_RECEIVE or self.answer_release:
            return ActuationResult.ACCEPTED
        self.release_attempts += 1
        if self.release_attempts >= 2:
            self.retry_entered.set()
            await asyncio.Event().wait()
        raise RuntimeError("radio did not answer the unkey")


@pytest.mark.asyncio
async def test_cancelling_every_task_ends_a_release_retry_in_flight(tmp_path) -> None:
    """MOR-4518: the release-retry scheduler honours cancellation.

    ``asyncio.run`` (and pytest's loop teardown) cancel every task once and
    wait for them. A retry in flight settles even when its waiter is
    cancelled, so a scheduler that swallowed that cancel re-armed the retry
    every second and the loop never closed: the process could not exit
    while a radio did not confirm its unkey.
    """
    actuator = _UnansweredReleaseActuator()

    async def retire(event: Any) -> None:
        del event

    composition = ManagedTxComposition(
        actuator, config_path=tmp_path / "unanswered.json", retire_provider=retire
    )
    store = StateStore()
    store.begin_provider_generation()
    await composition.transport_ready(object())
    await composition.bind_state_store(store)
    current = asyncio.current_task()
    try:
        on = await composition.authority.submit_transmit_on()
        await on.wait_settlement()
        off = await composition.authority.submit_force_off()
        await off.wait_settlement()
        await asyncio.wait_for(actuator.retry_entered.wait(), 5)

        others = [task for task in asyncio.all_tasks() if task is not current]
        for task in others:
            task.cancel()
        _, pending = await asyncio.wait(others, timeout=5)
        assert not pending, f"tasks ignored cancellation: {pending}"
    finally:
        actuator.answer_release = True
