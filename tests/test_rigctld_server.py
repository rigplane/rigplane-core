# mypy: disable-error-code=untyped-decorator
"""Tests for src/rigplane/rigctld/server.py.

Strategy
--------
- Inject mock protocol and handler via the private _protocol / _handler kwargs
  on RigctldServer so these tests never need a real radio or real protocol impl.
- Use asyncio.open_connection as the test client.
- Port 0 → OS assigns a free ephemeral port; read it from server._server.sockets.
- asyncio_mode = "auto" (pyproject.toml) — no @pytest.mark.asyncio needed.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, cast
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from rigplane.backends.icom7610.drivers.serial_stub import SerialMockRadio
from rigplane.core.acquisition_scheduler import (
    AcquisitionExecutionResult,
    AcquisitionPriority,
    AcquisitionRequest,
    AcquisitionScheduler,
    RadioStateModelService,
    StateFreshnessService,
)
from rigplane.core.state_acquisition_policy import (
    AcquisitionPolicy,
    FieldAvailability,
    FieldCapability,
    RadioAcquisitionProfile,
)
from rigplane.core.state_diagnostics import StateDiagnosticsRecorder
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.rigctld.contract import (
    ClientSession,
    HamlibError,
    RigctldCommand,
    RigctldConfig,
    RigctldResponse,
)
from rigplane.rigctld.server import RigctldServer, run_rigctld_server
from rigplane.types import Mode

# ---------------------------------------------------------------------------
# Canned objects shared across tests
# ---------------------------------------------------------------------------

_FREQ_CMD = RigctldCommand(short_cmd="f", long_cmd="get_freq", is_set=False)
_FREQ_RESP = RigctldResponse(values=["14074000"], error=0)
_RESPONSE_BYTES = b"14074000\n"
_ERROR_BYTES = b"RPRT -8\n"  # EPROTO
_TIMEOUT_BYTES = b"RPRT -5\n"  # ETIMEOUT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _addr(server: RigctldServer) -> tuple[str, int]:
    """Return (host, port) for a started server."""
    assert server._server is not None
    sockname = server._server.sockets[0].getsockname()
    assert isinstance(sockname, tuple)
    host, port = sockname[:2]
    assert isinstance(host, str)
    assert isinstance(port, int)
    return host, port


async def _connect(
    server: RigctldServer,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    host, port = _addr(server)
    return await asyncio.open_connection(host, port)


async def _close(writer: asyncio.StreamWriter) -> None:
    try:
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass


async def _read_all(reader: asyncio.StreamReader, *, timeout: float = 1.0) -> bytes:
    """Read until EOF or timeout."""
    try:
        return await asyncio.wait_for(reader.read(4096), timeout=timeout)
    except asyncio.TimeoutError:
        return b""


class _ContractPrewarmRadio:
    def __init__(self, mode: str, data_mode: bool = False) -> None:
        self.mode = mode
        self.data_mode = data_mode
        self.set_data_mode = AsyncMock(side_effect=self._set_data_mode)

    async def get_mode(self, receiver: int = 0) -> tuple[str, int | None]:
        assert receiver == 0
        return self.mode, 2

    async def get_data_mode(self) -> bool:
        return self.data_mode

    async def _set_data_mode(self, on: bool) -> None:
        self.data_mode = on


class _CompatHandler:
    def __init__(self) -> None:
        self.calls: list[RigctldCommand] = []

    async def execute(self, cmd: RigctldCommand) -> RigctldResponse:
        self.calls.append(cmd)
        return _FREQ_RESP


class _RecordingStateModelService:
    def ensure_fresh(self, *args: object, **kwargs: object) -> object:
        return object()


class _FakeSocket:
    def getsockname(self) -> tuple[str, int]:
        return ("127.0.0.1", 0)


class _FakeAsyncServer:
    def __init__(self) -> None:
        self.sockets = [_FakeSocket()]
        self.closed = False

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class _ProfiledStandaloneRadio:
    def __init__(self, *, profile: object) -> None:
        self.profile = profile
        self.connected = True
        self.radio_ready = True
        self.control_connected = True
        self.get_freq = AsyncMock(return_value=14_090_000)


class _CivProfiledStandaloneRadio(_ProfiledStandaloneRadio):
    def __init__(self, *, profile: object, observed_freq: int) -> None:
        super().__init__(profile=profile)
        self.observed_freq = observed_freq
        self.send_civ_calls: list[dict[str, object]] = []

    async def send_civ(
        self,
        command: int,
        sub: int | None = None,
        data: bytes | None = None,
        *,
        wait_response: bool = True,
    ) -> None:
        self.send_civ_calls.append(
            {
                "command": command,
                "sub": sub,
                "data": data,
                "wait_response": wait_response,
            }
        )


class _ApplyingAcquisitionExecutor:
    def __init__(self, radio: object, *, value: int) -> None:
        self._radio = radio
        self._value = value
        self.calls: list[tuple[AcquisitionRequest, frozenset[FieldPath]]] = []

    async def execute(
        self,
        request: AcquisitionRequest,
        *,
        already_sent_paths: frozenset[FieldPath],
    ) -> AcquisitionExecutionResult:
        self.calls.append((request, already_sent_paths))
        store = getattr(self._radio, "_state_store")
        scheduler = getattr(self._radio, "_acquisition_scheduler")
        change_set = store.apply(
            Observation(
                path=request.paths[0],
                value=self._value,
                source=SourceMetadata(
                    source="poll_response",
                    provider=request.provider,
                    native_id="fake-provider-read",
                ),
                timestamp_monotonic=3.0,
                max_age=request.policy.freshness_ttl_seconds,
            )
        )
        scheduler.record_acquisition_result(request, change_set)
        return AcquisitionExecutionResult(sent_paths=request.paths)


class _BlockingAcquisitionExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[AcquisitionRequest, frozenset[FieldPath]]] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(
        self,
        request: AcquisitionRequest,
        *,
        already_sent_paths: frozenset[FieldPath],
    ) -> AcquisitionExecutionResult:
        self.calls.append((request, already_sent_paths))
        self.started.set()
        await self.release.wait()
        return AcquisitionExecutionResult(sent_paths=request.paths)


class _FailingAcquisitionExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[AcquisitionRequest, frozenset[FieldPath]]] = []

    async def execute(
        self,
        request: AcquisitionRequest,
        *,
        already_sent_paths: frozenset[FieldPath],
    ) -> AcquisitionExecutionResult:
        self.calls.append((request, already_sent_paths))
        raise RuntimeError("executor transport failed")


def _acquisition_profile(
    *paths: FieldPath,
    provider: str = "test_provider",
) -> RadioAcquisitionProfile:
    return RadioAcquisitionProfile(
        provider=provider,
        capabilities=tuple(
            FieldCapability(
                path=path,
                polling=True,
                command_response_observable=True,
            )
            for path in paths
        ),
        default_policy=AcquisitionPolicy(),
    )


def _unavailable_profile(path: FieldPath) -> RadioAcquisitionProfile:
    return RadioAcquisitionProfile(
        provider="test_provider",
        capabilities=(
            FieldCapability(
                path=path,
                availability=FieldAvailability.UNSUPPORTED,
                diagnostic=f"{path}: field unavailable in fake profile",
            ),
        ),
        default_policy=AcquisitionPolicy(),
    )


def _apply_store_value(
    store: StateStore,
    path: FieldPath,
    value: object,
    *,
    max_age: float | None = None,
) -> None:
    store.apply(
        Observation(
            path=path,
            value=value,
            source=SourceMetadata(
                source="test",
                provider="tests",
                command_source="rigctld",
            ),
            timestamp_monotonic=1.0,
            max_age=max_age,
        )
    )


async def _wait_for(predicate: object, *, timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if cast(Any, predicate)():
            return
        await asyncio.sleep(0.01)
    assert cast(Any, predicate)()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_radio() -> MagicMock:
    radio = MagicMock(name="radio")
    radio.connected = True
    radio.radio_ready = True
    radio.control_connected = True
    return radio


@pytest.fixture
def cfg() -> RigctldConfig:
    return RigctldConfig(
        host="127.0.0.1",
        port=0,  # OS assigns a free port
        max_clients=3,
        client_timeout=0.5,
        command_timeout=0.3,
    )


@pytest.fixture
def proto() -> MagicMock:
    """Mock protocol module with canned responses."""
    m = MagicMock(name="protocol")
    m.parse_line.return_value = _FREQ_CMD
    m.format_response.return_value = _RESPONSE_BYTES
    m.format_error.return_value = _ERROR_BYTES
    return m


@pytest.fixture
def handler() -> MagicMock:
    """Mock handler *instance* (not the class) with async execute."""
    m = MagicMock(name="handler")
    m.execute = AsyncMock(return_value=_FREQ_RESP)
    return m


@pytest.fixture
async def server(
    mock_radio: MagicMock, cfg: RigctldConfig, proto: MagicMock, handler: MagicMock
) -> AsyncIterator[RigctldServer]:
    srv = RigctldServer(mock_radio, cfg, _protocol=proto, _handler=handler)
    await srv.start()
    yield srv
    await srv.stop()


@pytest.fixture
async def server_serial_radio(
    cfg: RigctldConfig,
) -> AsyncIterator[tuple[RigctldServer, SerialMockRadio]]:
    """RigctldServer running on top of a real SerialMockRadio core."""
    radio = SerialMockRadio()
    await radio.connect()
    srv = RigctldServer(radio, cfg)
    await srv.start()
    try:
        yield srv, radio
    finally:
        await srv.stop()


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestLifecycle:
    async def test_start_creates_server(self, server: RigctldServer) -> None:
        assert server._server is not None

    async def test_stop_closes_server(
        self,
        mock_radio: MagicMock,
        cfg: RigctldConfig,
        proto: MagicMock,
        handler: MagicMock,
    ) -> None:
        srv = RigctldServer(mock_radio, cfg, _protocol=proto, _handler=handler)
        await srv.start()
        await srv.stop()
        assert srv._server is None

    async def test_context_manager(
        self,
        mock_radio: MagicMock,
        cfg: RigctldConfig,
        proto: MagicMock,
        handler: MagicMock,
    ) -> None:
        async with RigctldServer(
            mock_radio, cfg, _protocol=proto, _handler=handler
        ) as srv:
            host, port = _addr(srv)
            assert port > 0
        assert srv._server is None

    async def test_double_stop_is_safe(
        self,
        mock_radio: MagicMock,
        cfg: RigctldConfig,
        proto: MagicMock,
        handler: MagicMock,
    ) -> None:
        srv = RigctldServer(mock_radio, cfg, _protocol=proto, _handler=handler)
        await srv.start()
        await srv.stop()
        await srv.stop()  # second call must not raise

    async def test_start_does_not_bind_backend_state_cache_by_default(
        self, cfg: RigctldConfig
    ) -> None:
        radio = SerialMockRadio()
        await radio.connect()
        srv = RigctldServer(radio, cfg)
        await srv.start()
        try:
            assert srv._rig_handler is not None
            assert srv._poller is None
            assert srv._rig_handler._cache is not radio.state_cache
        finally:
            await srv.stop()

    async def test_start_passes_state_model_capability_to_default_handler(
        self, mock_radio: MagicMock, cfg: RigctldConfig
    ) -> None:
        store = StateStore()
        model_service = _RecordingStateModelService()
        mock_radio.state_store = store
        mock_radio.state_model_service = model_service
        fake_server = _FakeAsyncServer()

        with (
            patch(
                "rigplane.rigctld.server.asyncio.start_server",
                new=AsyncMock(return_value=fake_server),
            ),
            patch("rigplane.rigctld.handler.RigctldHandler") as handler_cls,
        ):
            srv = RigctldServer(mock_radio, cfg)
            await srv.start()
            await srv.stop()

        handler_cls.assert_called_once_with(
            mock_radio,
            cfg,
            state_store=store,
            state_model_service=model_service,
        )

    async def test_start_bootstraps_profiled_standalone_state_acquisition(
        self, cfg: RigctldConfig
    ) -> None:
        freq = FieldPath.active("main", "freq_mode", "freq_hz")
        radio = _ProfiledStandaloneRadio(
            profile=type(
                "Profile",
                (),
                {"state_acquisition": _acquisition_profile(freq)},
            )()
        )
        fake_server = _FakeAsyncServer()

        with (
            patch(
                "rigplane.rigctld.server.asyncio.start_server",
                new=AsyncMock(return_value=fake_server),
            ),
            patch("rigplane.rigctld.handler.RigctldHandler") as handler_cls,
        ):
            srv = RigctldServer(radio, cfg)
            await srv.start()
            try:
                assert isinstance(srv._state_store, StateStore)
                assert isinstance(srv._acquisition_scheduler, AcquisitionScheduler)
                assert isinstance(srv._state_model_service, RadioStateModelService)
                assert isinstance(srv._state_freshness_service, StateFreshnessService)
                assert srv._state_store_freshness_task is not None
                assert not srv._state_store_freshness_task.done()
            finally:
                await srv.stop()

        handler_cls.assert_called_once_with(
            radio,
            cfg,
            state_store=srv._state_store,
            state_model_service=srv._state_model_service,
        )
        assert srv._state_store_freshness_task is None

    async def test_start_reuses_runtime_services_without_owning_freshness_task(
        self, cfg: RigctldConfig
    ) -> None:
        store = StateStore()
        freq = FieldPath.active("main", "freq_mode", "freq_hz")
        scheduler = AcquisitionScheduler(profile=_acquisition_profile(freq))
        model_service = RadioStateModelService(store=store, scheduler=scheduler)
        freshness_service = StateFreshnessService(store=store, scheduler=scheduler)
        radio = _ProfiledStandaloneRadio(
            profile=type(
                "Profile",
                (),
                {"state_acquisition": _acquisition_profile(freq)},
            )()
        )
        radio.state_store = store
        radio.state_model_service = model_service
        radio._state_freshness_service = freshness_service
        fake_server = _FakeAsyncServer()

        with (
            patch(
                "rigplane.rigctld.server.asyncio.start_server",
                new=AsyncMock(return_value=fake_server),
            ),
            patch("rigplane.rigctld.handler.RigctldHandler") as handler_cls,
        ):
            srv = RigctldServer(radio, cfg)
            await srv.start()
            await srv.stop()

        assert srv._state_store is store
        assert srv._state_model_service is model_service
        assert srv._state_freshness_service is freshness_service
        assert srv._state_store_freshness_task is None
        handler_cls.assert_called_once_with(
            radio,
            cfg,
            state_store=store,
            state_model_service=model_service,
        )

    async def test_start_without_acquisition_profile_keeps_handler_local_fallback(
        self, cfg: RigctldConfig
    ) -> None:
        radio = _ProfiledStandaloneRadio(
            profile=type("Profile", (), {"state_acquisition": None})()
        )
        fake_server = _FakeAsyncServer()

        with (
            patch(
                "rigplane.rigctld.server.asyncio.start_server",
                new=AsyncMock(return_value=fake_server),
            ),
            patch("rigplane.rigctld.handler.RigctldHandler") as handler_cls,
        ):
            srv = RigctldServer(radio, cfg)
            await srv.start()
            await srv.stop()

        assert srv._state_store is None
        assert srv._acquisition_scheduler is None
        assert srv._state_model_service is None
        assert srv._state_freshness_service is None
        assert srv._state_store_freshness_task is None
        handler_cls.assert_called_once_with(
            radio,
            cfg,
            state_store=None,
            state_model_service=None,
        )

    async def test_stale_standalone_state_store_queues_acquisition_via_server_service(
        self, cfg: RigctldConfig
    ) -> None:
        freq = FieldPath.active("main", "freq_mode", "freq_hz")
        radio = _ProfiledStandaloneRadio(
            profile=type(
                "Profile",
                (),
                {"state_acquisition": _acquisition_profile(freq)},
            )()
        )
        executor = _BlockingAcquisitionExecutor()
        radio._acquisition_executor = executor
        fake_server = _FakeAsyncServer()

        with patch(
            "rigplane.rigctld.server.asyncio.start_server",
            new=AsyncMock(return_value=fake_server),
        ):
            srv = RigctldServer(radio, cfg)
            await srv.start()
            try:
                assert isinstance(srv._state_store, StateStore)
                assert isinstance(srv._state_freshness_service, StateFreshnessService)
                assert isinstance(srv._acquisition_scheduler, AcquisitionScheduler)
                _apply_store_value(
                    srv._state_store,
                    freq,
                    14_074_000,
                    max_age=0.25,
                )
                srv._state_freshness_service.tick(now=2.0)

                resp = await srv._rig_handler.execute(_FREQ_CMD)

                assert resp.values == ["14090000"]
                await asyncio.wait_for(executor.started.wait(), timeout=1.0)
                requests = srv._acquisition_scheduler.pending_requests()
                assert len(requests) == 1
                assert requests[0].paths == (freq,)
                assert requests[0].priority is AcquisitionPriority.USER
                assert requests[0].reasons == ("stale", "rigctld.get_freq")
            finally:
                executor.release.set()
                await srv.stop()

    async def test_stale_standalone_get_drains_acquisition_through_provider_observation(
        self, cfg: RigctldConfig
    ) -> None:
        freq = FieldPath.active("main", "freq_mode", "freq_hz")
        radio = _ProfiledStandaloneRadio(
            profile=type(
                "Profile",
                (),
                {"state_acquisition": _acquisition_profile(freq)},
            )()
        )
        executor = _ApplyingAcquisitionExecutor(radio, value=14_110_000)
        radio._acquisition_executor = executor
        fake_server = _FakeAsyncServer()

        with patch(
            "rigplane.rigctld.server.asyncio.start_server",
            new=AsyncMock(return_value=fake_server),
        ):
            srv = RigctldServer(radio, cfg)
            await srv.start()
            try:
                assert isinstance(srv._state_store, StateStore)
                assert isinstance(srv._acquisition_scheduler, AcquisitionScheduler)
                _apply_store_value(
                    srv._state_store,
                    freq,
                    14_074_000,
                    max_age=0.25,
                )
                srv._state_freshness_service.tick(now=2.0)

                resp = await srv._rig_handler.execute(_FREQ_CMD)

                assert resp.values == ["14090000"]
                await _wait_for(
                    lambda: srv._acquisition_scheduler.pending_requests() == ()
                )
                assert executor.calls
                assert srv._state_store.snapshot().field(freq).value == 14_110_000
                assert srv._acquisition_scheduler.diagnostics()[
                    "queuedRequestCount"
                ] == 0
            finally:
                await srv.stop()

    async def test_standalone_drain_records_executor_missing_failure(
        self, cfg: RigctldConfig
    ) -> None:
        freq = FieldPath.active("main", "freq_mode", "freq_hz")
        diagnostics = StateDiagnosticsRecorder(enabled=True)
        radio = _ProfiledStandaloneRadio(
            profile=type(
                "Profile",
                (),
                {"state_acquisition": _acquisition_profile(freq)},
            )()
        )
        radio._state_diagnostics = diagnostics
        fake_server = _FakeAsyncServer()

        with patch(
            "rigplane.rigctld.server.asyncio.start_server",
            new=AsyncMock(return_value=fake_server),
        ):
            srv = RigctldServer(radio, cfg)
            await srv.start()
            try:
                assert isinstance(srv._state_store, StateStore)
                assert isinstance(srv._acquisition_scheduler, AcquisitionScheduler)
                _apply_store_value(
                    srv._state_store,
                    freq,
                    14_074_000,
                    max_age=0.25,
                )
                srv._state_freshness_service.tick(now=2.0)

                await srv._rig_handler.execute(_FREQ_CMD)

                await _wait_for(
                    lambda: srv._acquisition_scheduler.pending_requests() == ()
                )
                scheduler_diagnostics = srv._acquisition_scheduler.diagnostics()
                assert scheduler_diagnostics["failureCountByReason"][
                    "acquisition_executor_missing"
                ] == 1
                assert any(
                    event.kind == "acquisition_executor_missing"
                    and event.source == "rigctld.server"
                    and event.details["provider"] == "test_provider"
                    for event in diagnostics.events()
                )
            finally:
                await srv.stop()

    async def test_standalone_drain_records_executor_exception_and_keeps_running(
        self, cfg: RigctldConfig
    ) -> None:
        freq = FieldPath.active("main", "freq_mode", "freq_hz")
        diagnostics = StateDiagnosticsRecorder(enabled=True)
        radio = _ProfiledStandaloneRadio(
            profile=type(
                "Profile",
                (),
                {"state_acquisition": _acquisition_profile(freq)},
            )()
        )
        executor = _FailingAcquisitionExecutor()
        radio._acquisition_executor = executor
        radio._state_diagnostics = diagnostics
        fake_server = _FakeAsyncServer()

        with patch(
            "rigplane.rigctld.server.asyncio.start_server",
            new=AsyncMock(return_value=fake_server),
        ):
            srv = RigctldServer(radio, cfg)
            await srv.start()
            try:
                assert isinstance(srv._state_store, StateStore)
                assert isinstance(srv._acquisition_scheduler, AcquisitionScheduler)
                _apply_store_value(
                    srv._state_store,
                    freq,
                    14_074_000,
                    max_age=0.25,
                )
                srv._state_freshness_service.tick(now=2.0)

                await srv._rig_handler.execute(_FREQ_CMD)

                await _wait_for(
                    lambda: srv._acquisition_scheduler.pending_requests() == ()
                )
                assert executor.calls
                assert srv._state_acquisition_drain_task is not None
                assert not srv._state_acquisition_drain_task.done()
                scheduler_diagnostics = srv._acquisition_scheduler.diagnostics()
                assert scheduler_diagnostics["failureCountByReason"][
                    "acquisition_executor_error"
                ] == 1
                assert any(
                    event.kind == "acquisition_request_failed"
                    and event.source == "rigctld.server"
                    and event.details["reason"] == "acquisition_executor_error"
                    and "executor transport failed" in event.details["error"]
                    for event in diagnostics.events()
                )
            finally:
                await srv.stop()

    async def test_civ_profile_without_send_capability_fails_before_timeout(
        self, cfg: RigctldConfig
    ) -> None:
        freq = FieldPath.active("main", "freq_mode", "freq_hz")
        diagnostics = StateDiagnosticsRecorder(enabled=True)
        radio = _ProfiledStandaloneRadio(
            profile=type(
                "Profile",
                (),
                {
                    "state_acquisition": _acquisition_profile(
                        freq,
                        provider="icom_civ",
                    )
                },
            )()
        )
        radio._state_diagnostics = diagnostics
        fake_server = _FakeAsyncServer()

        with patch(
            "rigplane.rigctld.server.asyncio.start_server",
            new=AsyncMock(return_value=fake_server),
        ):
            srv = RigctldServer(radio, cfg)
            await srv.start()
            try:
                assert isinstance(srv._state_store, StateStore)
                assert isinstance(srv._acquisition_scheduler, AcquisitionScheduler)
                _apply_store_value(
                    srv._state_store,
                    freq,
                    14_074_000,
                    max_age=0.25,
                )
                srv._state_freshness_service.tick(now=2.0)

                await srv._rig_handler.execute(_FREQ_CMD)

                await _wait_for(
                    lambda: srv._acquisition_scheduler.pending_requests() == ()
                )
                scheduler_diagnostics = srv._acquisition_scheduler.diagnostics()
                assert scheduler_diagnostics["failureCountByReason"][
                    "acquisition_executor_unavailable"
                ] == 1
                assert any(
                    event.kind == "acquisition_executor_unavailable"
                    and event.source == "rigctld.server"
                    and event.details["provider"] == "icom_civ"
                    for event in diagnostics.events()
                )
                assert not any(
                    event.kind == "acquisition_request_failed"
                    and event.details.get("reason") == "acquisition_request_timeout"
                    for event in diagnostics.events()
                )
            finally:
                await srv.stop()

    async def test_civ_standalone_drain_sends_read_and_applies_observation(
        self, cfg: RigctldConfig
    ) -> None:
        freq = FieldPath.active("main", "freq_mode", "freq_hz")
        radio = _CivProfiledStandaloneRadio(
            profile=type(
                "Profile",
                (),
                {
                    "state_acquisition": _acquisition_profile(
                        freq,
                        provider="icom_civ",
                    )
                },
            )(),
            observed_freq=14_110_000,
        )
        fake_server = _FakeAsyncServer()

        with patch(
            "rigplane.rigctld.server.asyncio.start_server",
            new=AsyncMock(return_value=fake_server),
        ):
            srv = RigctldServer(radio, cfg)
            await srv.start()
            try:
                assert isinstance(srv._state_store, StateStore)
                assert isinstance(srv._acquisition_scheduler, AcquisitionScheduler)
                _apply_store_value(
                    srv._state_store,
                    freq,
                    14_074_000,
                    max_age=0.25,
                )
                srv._state_freshness_service.tick(now=2.0)

                resp = await srv._rig_handler.execute(_FREQ_CMD)

                assert resp.values == ["14090000"]
                await _wait_for(lambda: bool(radio.send_civ_calls))
                assert radio.send_civ_calls == [
                    {
                        "command": 0x25,
                        "sub": None,
                        "data": b"\x00",
                        "wait_response": False,
                    }
                ]

                request = srv._acquisition_scheduler.pending_requests()[0]
                change_set = srv._state_store.apply(
                    Observation(
                        path=freq,
                        value=radio.observed_freq,
                        source=SourceMetadata(
                            source="command_response",
                            provider=request.provider,
                            native_id="0x25",
                        ),
                        timestamp_monotonic=3.0,
                        max_age=request.policy.freshness_ttl_seconds,
                    )
                )
                srv._acquisition_scheduler.record_acquisition_result(
                    request,
                    change_set,
                )

                assert srv._state_store.snapshot().field(freq).value == 14_110_000
                assert srv._acquisition_scheduler.pending_requests() == ()
            finally:
                await srv.stop()

    async def test_unavailable_field_records_distinct_acquisition_diagnostic(
        self, cfg: RigctldConfig
    ) -> None:
        freq = FieldPath.active("main", "freq_mode", "freq_hz")
        diagnostics = StateDiagnosticsRecorder(enabled=True)
        radio = _ProfiledStandaloneRadio(
            profile=type(
                "Profile",
                (),
                {"state_acquisition": _unavailable_profile(freq)},
            )()
        )
        radio._state_diagnostics = diagnostics
        fake_server = _FakeAsyncServer()

        with patch(
            "rigplane.rigctld.server.asyncio.start_server",
            new=AsyncMock(return_value=fake_server),
        ):
            srv = RigctldServer(radio, cfg)
            await srv.start()
            try:
                await srv._rig_handler.execute(_FREQ_CMD)

                assert isinstance(srv._acquisition_scheduler, AcquisitionScheduler)
                assert srv._acquisition_scheduler.pending_requests() == ()
                assert not any(
                    event.kind == "acquisition_executor_missing"
                    for event in diagnostics.events()
                )
                assert any(
                    event.kind == "acquisition_unavailable"
                    and event.source == "rigctld.handler"
                    and event.details["reason"] == "rigctld.get_freq"
                    for event in diagnostics.events()
                )
            finally:
                await srv.stop()


# ---------------------------------------------------------------------------
# Accept / response cycle
# ---------------------------------------------------------------------------


class TestAcceptResponse:
    async def test_single_command_response(
        self, server: RigctldServer, proto: MagicMock
    ) -> None:
        r, w = await _connect(server)
        w.write(b"f\n")
        await w.drain()

        data = await asyncio.wait_for(r.read(4096), timeout=1.0)
        assert data == _RESPONSE_BYTES
        proto.parse_line.assert_called_once_with(b"f", ANY)

        await _close(w)

    async def test_multiple_commands_same_connection(
        self, server: RigctldServer, proto: MagicMock
    ) -> None:
        r, w = await _connect(server)

        for _ in range(3):
            w.write(b"f\n")
            await w.drain()
            data = await asyncio.wait_for(r.read(4096), timeout=1.0)
            assert data == _RESPONSE_BYTES

        assert proto.parse_line.call_count == 3
        await _close(w)

    async def test_set_command_calls_execute(
        self, server: RigctldServer, proto: MagicMock, handler: MagicMock
    ) -> None:
        set_cmd = RigctldCommand("F", "set_freq", args=("14074000",), is_set=True)
        proto.parse_line.return_value = set_cmd
        proto.format_response.return_value = b"RPRT 0\n"

        r, w = await _connect(server)
        w.write(b"F 14074000\n")
        await w.drain()

        data = await asyncio.wait_for(r.read(4096), timeout=1.0)
        assert data == b"RPRT 0\n"
        handler.execute.assert_called_once()
        call = handler.execute.call_args
        assert call is not None
        assert call.args == (set_cmd,)
        assert call.kwargs["session_id"] == "rigctld-client-1"
        await _close(w)

    async def test_legacy_handler_execute_without_session_id_still_runs(
        self, mock_radio: MagicMock, cfg: RigctldConfig, proto: MagicMock
    ) -> None:
        compat_handler = _CompatHandler()
        srv = RigctldServer(
            mock_radio,
            cfg,
            _protocol=proto,
            _handler=compat_handler,
        )
        await srv.start()
        try:
            r, w = await _connect(srv)
            w.write(b"f\n")
            await w.drain()

            data = await asyncio.wait_for(r.read(4096), timeout=1.0)
            assert data == _RESPONSE_BYTES
            assert compat_handler.calls == [_FREQ_CMD]
            await _close(w)
        finally:
            await srv.stop()

    async def test_format_response_receives_session(
        self, server: RigctldServer, proto: MagicMock
    ) -> None:
        r, w = await _connect(server)
        w.write(b"f\n")
        await w.drain()
        await asyncio.wait_for(r.read(4096), timeout=1.0)

        # format_response must be called with (cmd, resp, ClientSession)
        call_args = proto.format_response.call_args
        assert call_args is not None
        _cmd, _resp, session = call_args.args
        assert isinstance(session, ClientSession)
        assert session.client_id > 0

        await _close(w)

    async def test_blank_lines_are_skipped(
        self, server: RigctldServer, proto: MagicMock
    ) -> None:
        r, w = await _connect(server)
        w.write(b"\n\n\nf\n")
        await w.drain()
        data = await asyncio.wait_for(r.read(4096), timeout=1.0)
        assert data == _RESPONSE_BYTES
        proto.parse_line.assert_called_once_with(b"f", ANY)
        await _close(w)

    async def test_crlf_line_ending_accepted(
        self, server: RigctldServer, proto: MagicMock
    ) -> None:
        r, w = await _connect(server)
        w.write(b"f\r\n")
        await w.drain()
        data = await asyncio.wait_for(r.read(4096), timeout=1.0)
        assert data == _RESPONSE_BYTES
        proto.parse_line.assert_called_once_with(b"f", ANY)
        await _close(w)


class TestSemiIntegrationSerialMockRadio:
    async def test_get_and_set_frequency_flows_through_core(
        self, server_serial_radio: tuple[RigctldServer, SerialMockRadio]
    ) -> None:
        """f/F commands go through real RigctldHandler into SerialMockRadio."""
        server, radio = server_serial_radio
        reader, writer = await _connect(server)
        try:
            # Initial frequency from SerialMockRadio default state.
            writer.write(b"f\n")
            await writer.drain()
            data = await asyncio.wait_for(reader.read(4096), timeout=1.0)
            assert b"14074000" in data

            # Change frequency and verify both protocol response and core state.
            writer.write(b"F 7050000\n")
            await writer.drain()
            data_set = await asyncio.wait_for(reader.read(4096), timeout=1.0)
            assert data_set == b"RPRT 0\n"

            writer.write(b"f\n")
            await writer.drain()
            data_after = await asyncio.wait_for(reader.read(4096), timeout=1.0)
            assert data_after == b"7050000\n"

            freq_core = await radio.get_freq()
            assert freq_core == 7_050_000
        finally:
            await _close(writer)

    async def test_get_and_set_mode_flows_through_core(
        self, server_serial_radio: tuple[RigctldServer, SerialMockRadio]
    ) -> None:
        """m/M commands go through real RigctldHandler into SerialMockRadio."""
        server, radio = server_serial_radio
        reader, writer = await _connect(server)
        try:
            # Initial mode from SerialMockRadio default state.
            writer.write(b"m\n")
            await writer.drain()
            data = await asyncio.wait_for(reader.read(4096), timeout=1.0)
            lines = data.decode().splitlines()
            assert lines[0] == "USB"

            # Change mode to LSB with passband and verify both layers.
            writer.write(b"M LSB 2400\n")
            await writer.drain()
            data_set = await asyncio.wait_for(reader.read(4096), timeout=1.0)
            assert data_set == b"RPRT 0\n"

            writer.write(b"m\n")
            await writer.drain()
            data_after = await asyncio.wait_for(reader.read(4096), timeout=1.0)
            after_lines = data_after.decode().splitlines()
            assert after_lines[0] == "LSB"

            mode_core, filt_core = await radio.get_mode()
            assert mode_core == "LSB"
            assert filt_core == 2
        finally:
            await _close(writer)


# ---------------------------------------------------------------------------
# Quit command
# ---------------------------------------------------------------------------


class TestQuit:
    async def test_quit_closes_connection(
        self, server: RigctldServer, proto: MagicMock
    ) -> None:
        proto.parse_line.return_value = RigctldCommand("q", "quit")

        r, w = await _connect(server)
        w.write(b"q\n")
        await w.drain()

        data = await _read_all(r)
        assert data == b""  # server closed the connection
        await _close(w)

    async def test_quit_decrements_client_count(
        self, server: RigctldServer, proto: MagicMock
    ) -> None:
        proto.parse_line.return_value = RigctldCommand("q", "quit")

        r, w = await _connect(server)
        w.write(b"q\n")
        await w.drain()
        await _read_all(r)

        # Give event loop a beat to run the done callback.
        await asyncio.sleep(0.05)
        assert server._client_count == 0
        await _close(w)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    async def test_parse_error_sends_enimpl(
        self, server: RigctldServer, proto: MagicMock
    ) -> None:
        """Unknown commands (ValueError) return ENIMPL, not EPROTO."""
        proto.parse_line.side_effect = ValueError("unknown command")
        proto.format_error.return_value = b"RPRT -4\n"

        r, w = await _connect(server)
        w.write(b"garbage\n")
        await w.drain()

        data = await asyncio.wait_for(r.read(4096), timeout=1.0)
        assert data == b"RPRT -4\n"
        proto.format_error.assert_called_with(HamlibError.ENIMPL)
        await _close(w)

    async def test_parse_error_connection_stays_open(
        self, server: RigctldServer, proto: MagicMock
    ) -> None:
        """After a parse error the connection should remain open."""
        # First command: parse error
        proto.parse_line.side_effect = [ValueError("bad"), _FREQ_CMD]
        proto.format_error.return_value = b"RPRT -8\n"

        r, w = await _connect(server)
        w.write(b"garbage\n")
        await w.drain()
        await asyncio.wait_for(r.read(4096), timeout=1.0)  # consume error

        # Second command: succeeds
        proto.parse_line.side_effect = None
        proto.parse_line.return_value = _FREQ_CMD
        w.write(b"f\n")
        await w.drain()
        data = await asyncio.wait_for(r.read(4096), timeout=1.0)
        assert data == _RESPONSE_BYTES

        await _close(w)

    async def test_handler_exception_sends_eio(
        self, server: RigctldServer, handler: MagicMock, proto: MagicMock
    ) -> None:
        handler.execute.side_effect = RuntimeError("radio exploded")
        proto.format_error.return_value = b"RPRT -6\n"

        r, w = await _connect(server)
        w.write(b"f\n")
        await w.drain()

        data = await asyncio.wait_for(r.read(4096), timeout=1.0)
        assert data == b"RPRT -6\n"
        proto.format_error.assert_called_with(HamlibError.EIO)
        await _close(w)

    async def test_line_too_long_closes_connection(self, server: RigctldServer) -> None:
        r, w = await _connect(server)
        # max_line_length default is 1024; send > 1024 bytes without \n first
        oversized = b"x" * 1025 + b"\n"
        w.write(oversized)
        await w.drain()

        data = await _read_all(r)
        assert data == b""  # connection closed

        await _close(w)


# ---------------------------------------------------------------------------
# Command timeout
# ---------------------------------------------------------------------------


class TestCommandTimeout:
    async def test_slow_handler_gets_etimeout(
        self, server: RigctldServer, handler: MagicMock, proto: MagicMock
    ) -> None:
        async def slow(cmd: RigctldCommand) -> RigctldResponse:
            await asyncio.sleep(10)  # > command_timeout=0.3
            return _FREQ_RESP  # pragma: no cover

        handler.execute = slow
        proto.format_error.return_value = _TIMEOUT_BYTES

        r, w = await _connect(server)
        w.write(b"f\n")
        await w.drain()

        # Should receive timeout error within 1s (command_timeout=0.3)
        data = await asyncio.wait_for(r.read(4096), timeout=1.0)
        assert data == _TIMEOUT_BYTES
        proto.format_error.assert_called_with(HamlibError.ETIMEOUT)
        await _close(w)

    async def test_connection_still_usable_after_timeout(
        self, server: RigctldServer, handler: MagicMock, proto: MagicMock
    ) -> None:
        """After a command timeout the client can send another command."""
        call_count = 0

        async def sometimes_slow(cmd: RigctldCommand) -> RigctldResponse:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                await asyncio.sleep(10)  # first: timeout
            return _FREQ_RESP

        handler.execute = sometimes_slow
        proto.format_error.return_value = _TIMEOUT_BYTES

        r, w = await _connect(server)

        # First command times out
        w.write(b"f\n")
        await w.drain()
        err_data = await asyncio.wait_for(r.read(4096), timeout=1.0)
        assert err_data == _TIMEOUT_BYTES

        # Second command succeeds
        w.write(b"f\n")
        await w.drain()
        ok_data = await asyncio.wait_for(r.read(4096), timeout=1.0)
        assert ok_data == _RESPONSE_BYTES

        await _close(w)


# ---------------------------------------------------------------------------
# Idle timeout
# ---------------------------------------------------------------------------


class TestIdleTimeout:
    async def test_idle_client_gets_disconnected(self, server: RigctldServer) -> None:
        """client_timeout=0.5; sending nothing should close the connection."""
        r, w = await _connect(server)

        # Read should return EOF after idle timeout fires.
        data = await asyncio.wait_for(r.read(4096), timeout=2.0)
        assert data == b""

        await _close(w)

    async def test_active_client_resets_timeout(
        self, server: RigctldServer, proto: MagicMock
    ) -> None:
        """Each command resets the idle clock."""
        r, w = await _connect(server)

        # Send two commands 0.3s apart (< client_timeout=0.5) — should work.
        for _ in range(2):
            w.write(b"f\n")
            await w.drain()
            await asyncio.wait_for(r.read(4096), timeout=1.0)
            await asyncio.sleep(0.2)

        await _close(w)


# ---------------------------------------------------------------------------
# Max clients
# ---------------------------------------------------------------------------


class TestMaxClients:
    async def test_max_clients_enforced(
        self, server: RigctldServer, cfg: RigctldConfig
    ) -> None:
        """Connecting max_clients+1 should get an immediate EOF on the last."""
        connections: list[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = []

        for _ in range(cfg.max_clients):
            r, w = await _connect(server)
            connections.append((r, w))

        # Give event loop a beat so all connections are registered.
        await asyncio.sleep(0.05)

        # One extra — should be rejected.
        r_extra, w_extra = await _connect(server)
        data = await _read_all(r_extra)
        assert data == b""  # immediate EOF

        for r, w in connections:
            await _close(w)
        await _close(w_extra)

    async def test_client_count_decreases_on_disconnect(
        self, server: RigctldServer, proto: MagicMock
    ) -> None:
        proto.parse_line.return_value = RigctldCommand("q", "quit")

        r, w = await _connect(server)
        await asyncio.sleep(0.05)
        assert server._client_count == 1

        w.write(b"q\n")
        await w.drain()
        await _read_all(r)
        await asyncio.sleep(0.05)

        assert server._client_count == 0
        await _close(w)


# ---------------------------------------------------------------------------
# Concurrent clients
# ---------------------------------------------------------------------------


class TestConcurrentClients:
    async def test_three_concurrent_clients(self, server: RigctldServer) -> None:
        """All three clients should receive independent responses."""
        conns = [await _connect(server) for _ in range(3)]

        for _, w in conns:
            w.write(b"f\n")
            await w.drain()

        results = []
        for r, _ in conns:
            data = await asyncio.wait_for(r.read(4096), timeout=1.0)
            results.append(data)

        assert all(d == _RESPONSE_BYTES for d in results)

        for r, w in conns:
            await _close(w)

    async def test_each_client_has_unique_id(
        self, server: RigctldServer, proto: MagicMock
    ) -> None:
        conns = [await _connect(server) for _ in range(3)]

        for _, w in conns:
            w.write(b"f\n")
            await w.drain()

        for r, _ in conns:
            await asyncio.wait_for(r.read(4096), timeout=1.0)

        # Collect the sessions passed to format_response
        sessions = [call.args[2] for call in proto.format_response.call_args_list]
        ids = {s.client_id for s in sessions}
        assert len(ids) == 3, "each client should have a unique client_id"

        for r, w in conns:
            await _close(w)


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------


class TestGracefulShutdown:
    async def test_stop_closes_active_clients(
        self,
        mock_radio: MagicMock,
        cfg: RigctldConfig,
        proto: MagicMock,
        handler: MagicMock,
    ) -> None:
        srv = RigctldServer(mock_radio, cfg, _protocol=proto, _handler=handler)
        await srv.start()

        r, w = await _connect(srv)
        await asyncio.sleep(0.05)  # ensure task is running

        await srv.stop()

        # Client should receive EOF after server stops.
        data = await _read_all(r)
        assert data == b""
        await _close(w)

    async def test_stop_cancels_all_tasks(
        self,
        mock_radio: MagicMock,
        cfg: RigctldConfig,
        proto: MagicMock,
        handler: MagicMock,
    ) -> None:
        srv = RigctldServer(mock_radio, cfg, _protocol=proto, _handler=handler)
        await srv.start()

        readers_writers = [await _connect(srv) for _ in range(2)]
        await asyncio.sleep(0.05)

        assert srv._client_count == 2
        await srv.stop()
        # Allow done callbacks to fire after task cancellation
        await asyncio.sleep(0.05)
        assert srv._client_count == 0

        for r, w in readers_writers:
            await _close(w)

    async def test_serve_forever_stops_on_cancel(
        self,
        mock_radio: MagicMock,
        cfg: RigctldConfig,
        proto: MagicMock,
        handler: MagicMock,
    ) -> None:
        srv = RigctldServer(mock_radio, cfg, _protocol=proto, _handler=handler)
        task = asyncio.get_event_loop().create_task(srv.serve_forever())
        await asyncio.sleep(0.05)

        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

        assert srv._server is None


# ---------------------------------------------------------------------------
# Abrupt disconnect
# ---------------------------------------------------------------------------


class TestAbruptDisconnect:
    async def test_abrupt_disconnect_does_not_crash_server(
        self, server: RigctldServer
    ) -> None:
        r, w = await _connect(server)

        # Close without sending anything (abrupt).
        w.close()
        await asyncio.sleep(0.1)

        # Server must still be running and accept new clients.
        assert server._server is not None
        r2, w2 = await _connect(server)
        w2.write(b"f\n")
        await w2.drain()
        data = await asyncio.wait_for(r2.read(4096), timeout=1.0)
        assert data == _RESPONSE_BYTES
        await _close(w2)

    async def test_disconnect_mid_session_handled(
        self, server: RigctldServer, handler: MagicMock
    ) -> None:
        """Handler may be awaiting execute when client disconnects."""
        evt = asyncio.Event()

        async def blocking(cmd: RigctldCommand) -> RigctldResponse:
            evt.set()
            await asyncio.sleep(10)  # blocking
            return _FREQ_RESP  # pragma: no cover

        handler.execute = blocking

        r, w = await _connect(server)
        w.write(b"f\n")
        await w.drain()

        # Wait until handler is entered, then yank the connection.
        await asyncio.wait_for(evt.wait(), timeout=1.0)
        w.close()
        await asyncio.sleep(0.2)

        # Server should still be alive.
        assert server._server is not None


# ---------------------------------------------------------------------------
# run_rigctld_server convenience helper
# ---------------------------------------------------------------------------


class TestRunRigctldServer:
    async def test_run_stops_on_cancel(self, mock_radio: MagicMock) -> None:
        """run_rigctld_server should exit cleanly when cancelled."""
        # Use a no-op handler/protocol so start() doesn't fail on stubs.
        proto = MagicMock()
        proto.parse_line.return_value = _FREQ_CMD
        proto.format_response.return_value = _RESPONSE_BYTES
        proto.format_error.return_value = _ERROR_BYTES

        hdl = MagicMock()
        hdl.execute = AsyncMock(return_value=_FREQ_RESP)

        # Patch the module-level imports that run_rigctld_server triggers.
        import rigplane.rigctld.server as server_mod

        orig_cls = server_mod.RigctldServer
        server_mod_any = cast(Any, server_mod)

        def _patched_cls(radio: MagicMock, config: RigctldConfig) -> RigctldServer:
            return orig_cls(radio, config, _protocol=proto, _handler=hdl)

        server_mod_any.RigctldServer = _patched_cls
        try:
            task = asyncio.get_event_loop().create_task(
                run_rigctld_server(mock_radio, host="127.0.0.1", port=0)
            )
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        finally:
            server_mod_any.RigctldServer = orig_cls


# ---------------------------------------------------------------------------
# WSJT-X compatibility prewarm
# ---------------------------------------------------------------------------


class TestWsjtxCompatPrewarm:
    async def test_prewarm_falls_back_to_core_radio_contract(self) -> None:
        radio = _ContractPrewarmRadio("USB", data_mode=False)
        cfg = RigctldConfig(wsjtx_compat=True)
        srv = RigctldServer(radio, cfg, _protocol=MagicMock(), _handler=MagicMock())

        await srv._wsjtx_compat_prewarm()

        radio.set_data_mode.assert_awaited_once_with(True)

    async def test_prewarm_enables_data_when_usb_and_data_off(
        self, mock_radio: MagicMock
    ) -> None:
        cfg = RigctldConfig(wsjtx_compat=True)
        srv = RigctldServer(
            mock_radio, cfg, _protocol=MagicMock(), _handler=MagicMock()
        )

        mock_radio.get_mode_info = AsyncMock(return_value=(Mode.USB, 2))
        mock_radio.get_data_mode = AsyncMock(return_value=False)
        mock_radio.set_data_mode = AsyncMock(return_value=None)

        await srv._wsjtx_compat_prewarm()

        mock_radio.set_data_mode.assert_awaited_once_with(True)

    async def test_prewarm_skips_when_data_already_on(
        self, mock_radio: MagicMock
    ) -> None:
        cfg = RigctldConfig(wsjtx_compat=True)
        srv = RigctldServer(
            mock_radio, cfg, _protocol=MagicMock(), _handler=MagicMock()
        )

        mock_radio.get_mode_info = AsyncMock(return_value=(Mode.USB, 2))
        mock_radio.get_data_mode = AsyncMock(return_value=True)
        mock_radio.set_data_mode = AsyncMock(return_value=None)

        await srv._wsjtx_compat_prewarm()

        mock_radio.set_data_mode.assert_not_called()

    async def test_prewarm_configured_data2_falls_back_on_single_data_profile(
        self, mock_radio: MagicMock
    ) -> None:
        cfg = RigctldConfig(
            wsjtx_compat=True,
            wsjtx_data_mode=2,
            wsjtx_data_mod_input=5,
        )
        srv = RigctldServer(
            mock_radio, cfg, _protocol=MagicMock(), _handler=MagicMock()
        )

        mock_radio.profile = MagicMock(data_mode_count=1)
        mock_radio.get_mode_info = AsyncMock(return_value=(Mode.USB, 2))
        mock_radio.get_data_mode = AsyncMock(return_value=True)
        mock_radio.set_data_mode = AsyncMock(return_value=None)
        mock_radio.set_data2_mod_input = AsyncMock(return_value=None)

        await srv._wsjtx_compat_prewarm()

        mock_radio.set_data2_mod_input.assert_not_called()
        mock_radio.set_data_mode.assert_awaited_once_with(True)

    async def test_prewarm_skips_for_non_ssb_modes(self, mock_radio: MagicMock) -> None:
        cfg = RigctldConfig(wsjtx_compat=True)
        srv = RigctldServer(
            mock_radio, cfg, _protocol=MagicMock(), _handler=MagicMock()
        )

        mock_radio.get_mode_info = AsyncMock(return_value=(Mode.CW, None))
        mock_radio.get_data_mode = AsyncMock(return_value=False)
        mock_radio.set_data_mode = AsyncMock(return_value=None)

        await srv._wsjtx_compat_prewarm()

        mock_radio.set_data_mode.assert_not_called()
