"""Rigctld TCP server — asyncio.start_server transport layer.

Responsibilities:
- TCP listener on configurable host:port
- Per-client asyncio.Task with isolated StreamReader/StreamWriter
- Connection lifecycle (accept, timeout, graceful close)
- Max client limit
- Structured logging (client connect/disconnect/errors)
- CLI entry point integration

This module handles only I/O. It delegates parsing to protocol.py
and command execution to handler.py.
"""

from __future__ import annotations

import asyncio
import datetime
import inspect
import logging
import time
from collections.abc import Coroutine
from typing import TYPE_CHECKING, Any, cast

from ..core.acquisition_scheduler import (
    AcquisitionExecutionResult,
    AcquisitionExecutor,
    AcquisitionRequest,
    AcquisitionScheduler,
    RadioStateModelService,
    StateFreshnessService,
    civ_acquisition_executor_for_provider,
)
from ..core.state_diagnostics import StateDiagnosticsRecorder
from ..core.state_pipeline_contracts import FieldPath
from ..core.state_acquisition_policy import RadioAcquisitionProfile
from ..core.state_store import StateStore
from ..radio_protocol import (
    CivCommandCapable,
    StateModelCapable,
    StateModelService,
    StateStoreCapable,
)
from ..startup_checks import assert_radio_startup_ready
from . import audit as _audit  # noqa: TID251
from .circuit_breaker import CircuitBreaker, CircuitState  # noqa: TID251
from .contract import ClientSession, HamlibError, RigctldConfig  # noqa: TID251
from .utils import get_mode_reader  # noqa: TID251

if TYPE_CHECKING:
    from ..radio_protocol import Radio

logger = logging.getLogger(__name__)

__all__ = ["RigctldServer", "run_rigctld_server"]


class _AcquisitionExecutorUnavailable(RuntimeError):
    """Raised when a configured acquisition executor cannot run on this radio."""


def _profile_data_mode_count(radio: "Radio") -> int:
    profile = getattr(radio, "profile", None)
    count = getattr(profile, "data_mode_count", 1)
    return count if isinstance(count, int) and count > 0 else 1


def _wsjtx_data_mode_value(radio: "Radio", config: RigctldConfig) -> int | bool:
    if config.wsjtx_data_mode is not None:
        return int(config.wsjtx_data_mode)
    return True


async def _apply_wsjtx_data_mod_input(
    radio: "Radio",
    *,
    data_mode: int | bool,
    source: int | None,
) -> None:
    if source is None or type(data_mode) is not int:
        return
    setter_name = f"set_data{data_mode}_mod_input"
    setter = getattr(radio, setter_name, None)
    if setter is None:
        logger.debug(
            "WSJT-X compat: radio has no %s, skipping DATA mod input",
            setter_name,
        )
        return
    await setter(source)


def _mode_to_name(mode: object) -> str:
    """Normalize backend mode values to an uppercase mode name."""
    name = getattr(mode, "name", None)
    if isinstance(name, str):
        return name.upper()
    if isinstance(mode, str):
        return mode.upper()
    value = getattr(mode, "value", None)
    if isinstance(value, int):
        return getattr(mode, "name", "USB").upper()
    return str(mode).upper()


def _is_packet_mode_set(cmd: Any) -> bool:
    """Return True for set_mode PKT* commands.

    Used to hold poller writes a bit longer while radio applies DATA-mode
    transitions (USB/LSB/RTTY -> PKT*).
    """
    try:
        return (
            getattr(cmd, "long_cmd", "") == "set_mode"
            and bool(getattr(cmd, "args", ()))
            and str(cmd.args[0]).upper().startswith("PKT")
        )
    except Exception:
        return False


class RigctldServer:
    """Asyncio TCP server implementing the hamlib NET rigctld protocol.

    Args:
        radio: Connected IcomRadio instance.
        config: Server configuration; defaults to RigctldConfig().
        _protocol: Override the protocol module (for testing).
        _handler: Override the handler instance (for testing).
        _poller: Override the poller instance (for testing).
        _circuit_breaker: Override the circuit breaker (for testing).
    """

    def __init__(
        self,
        radio: "Radio",
        config: RigctldConfig | None = None,
        *,
        _protocol: Any = None,
        _handler: Any = None,
        _poller: Any = None,
        _circuit_breaker: Any = None,
    ) -> None:
        self._radio = radio
        self._config = config or RigctldConfig()
        self._server: asyncio.Server | None = None
        self._client_tasks: set[asyncio.Task[None]] = set()
        self._client_count = 0
        self._next_client_id = 0
        # Injected for testing; populated lazily in start() if None.
        self._protocol: Any = _protocol
        self._rig_handler: Any = _handler
        self._poller: Any = _poller
        self._circuit_breaker: CircuitBreaker | None = _circuit_breaker
        self._server_was_running: bool = False
        self._state_store: StateStore | None = None
        self._acquisition_scheduler: AcquisitionScheduler | None = None
        self._state_model_service: StateModelService | None = None
        self._state_freshness_service: StateFreshnessService | None = None
        self._state_store_freshness_task: asyncio.Task[None] | None = None
        self._acquisition_executor: AcquisitionExecutor | None = None
        self._state_acquisition_drain_task: asyncio.Task[None] | None = None
        self._acquisition_in_flight: dict[str, tuple[frozenset[FieldPath], float]] = {}
        self._state_diagnostics = getattr(radio, "_state_diagnostics", None)
        # Per-client sliding window for rate limiting: client_id → timestamps
        self._rate_windows: dict[int, list[float]] = {}
        # prevent GC of fire-and-forget tasks
        self._bg_tasks: set[asyncio.Task[Any]] = set()

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        """Create a background task and prevent GC from collecting it."""
        task = asyncio.get_running_loop().create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task

    def _handler_execute_call(self, cmd: Any, *, session_id: str) -> Coroutine[Any, Any, Any]:
        execute = getattr(self._rig_handler, "execute")
        try:
            signature = inspect.signature(execute)
        except (TypeError, ValueError):
            signature = None

        if signature is not None:
            for parameter in signature.parameters.values():
                if parameter.kind is inspect.Parameter.VAR_KEYWORD:
                    return cast(Coroutine[Any, Any, Any], execute(cmd, session_id=session_id))
                if parameter.name == "session_id":
                    return cast(Coroutine[Any, Any, Any], execute(cmd, session_id=session_id))
        return cast(Coroutine[Any, Any, Any], execute(cmd))

    def __del__(self) -> None:
        """Emit WARN if instance is collected while TCP server/poller is still active."""
        try:
            still_running = self._server is not None or self._server_was_running
            if still_running:
                logger.warning(
                    "RigctldServer collected while still running; "
                    "ensure stop() or async context manager is used."
                )
        except Exception:
            pass  # avoid raising in destructor

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def circuit_breaker_state(self) -> CircuitState | None:
        """Current circuit breaker state, or ``None`` if not yet initialised."""
        if self._circuit_breaker is None:
            return None
        return self._circuit_breaker.state

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _profile_state_acquisition(self) -> RadioAcquisitionProfile | None:
        """Return profile acquisition metadata for the attached radio, if any."""
        raw_profile = getattr(self._radio, "profile", None)
        acquisition_profile = getattr(raw_profile, "state_acquisition", None)
        if acquisition_profile is not None:
            return cast(RadioAcquisitionProfile, acquisition_profile)

        model = getattr(self._radio, "model", None)
        if not isinstance(model, str):
            return None

        try:
            from ..profiles import resolve_radio_profile  # noqa: PLC0415

            profile = resolve_radio_profile(model=model)
        except Exception:
            logger.debug("rigctld state acquisition: failed to resolve profile", exc_info=True)
            return None
        return cast(RadioAcquisitionProfile | None, profile.state_acquisition)

    def _radio_state_store(self) -> StateStore | None:
        if not isinstance(self._radio, StateStoreCapable):
            return None
        store = self._radio.state_store
        return store if isinstance(store, StateStore) else None

    def _radio_state_model_service(self) -> StateModelService | None:
        if not isinstance(self._radio, StateModelCapable):
            return None
        service = self._radio.state_model_service
        return service if isinstance(service, StateModelService) else None

    def _radio_state_freshness_service(self) -> StateFreshnessService | None:
        service = getattr(self._radio, "_state_freshness_service", None)
        return service if isinstance(service, StateFreshnessService) else None

    def _provided_acquisition_executor(self) -> AcquisitionExecutor | None:
        raw_executor = getattr(self._radio, "__dict__", {}).get("_acquisition_executor")
        execute = getattr(raw_executor, "execute", None)
        if callable(execute):
            return cast(AcquisitionExecutor, raw_executor)
        return None

    def _provider_uses_civ_executor(self, provider: str) -> bool:
        return (
            civ_acquisition_executor_for_provider(
                provider,
                self._send_one_state_query,
            )
            is not None
        )

    def _default_acquisition_executor_for_scheduler(
        self,
        scheduler: AcquisitionScheduler,
    ) -> AcquisitionExecutor | None:
        if not self._provider_uses_civ_executor(scheduler.provider):
            return None
        if not isinstance(self._radio, CivCommandCapable):
            return None
        return civ_acquisition_executor_for_provider(
            scheduler.provider,
            self._send_one_state_query,
        )

    def _attach_state_acquisition_services(
        self,
        *,
        store: StateStore,
        scheduler: AcquisitionScheduler,
        model_service: StateModelService,
        freshness_service: StateFreshnessService,
    ) -> None:
        for name, value in (
            ("_state_store", store),
            ("_acquisition_scheduler", scheduler),
            ("state_model_service", model_service),
            ("_state_freshness_service", freshness_service),
        ):
            try:
                setattr(self._radio, name, value)
            except Exception:
                logger.debug(
                    "rigctld state acquisition: failed to attach %s",
                    name,
                    exc_info=True,
                )

    def _bootstrap_state_acquisition(self) -> None:
        """Prepare StateStore-backed acquisition services for standalone rigctld."""
        self._state_store = self._radio_state_store()
        self._state_model_service = self._radio_state_model_service()
        self._state_freshness_service = self._radio_state_freshness_service()
        self._acquisition_executor = self._provided_acquisition_executor()

        acquisition_profile = self._profile_state_acquisition()
        if acquisition_profile is None:
            return
        if self._state_store is None:
            self._state_store = StateStore()
        if self._state_model_service is not None:
            scheduler = getattr(self._radio, "_acquisition_scheduler", None)
            if isinstance(scheduler, AcquisitionScheduler):
                self._acquisition_scheduler = scheduler
                if self._acquisition_executor is None:
                    self._acquisition_executor = (
                        self._default_acquisition_executor_for_scheduler(scheduler)
                    )
            return

        scheduler = AcquisitionScheduler(profile=acquisition_profile)
        model_service = RadioStateModelService(
            store=self._state_store,
            scheduler=scheduler,
        )
        freshness_service = StateFreshnessService(
            store=self._state_store,
            scheduler=scheduler,
        )
        self._acquisition_scheduler = scheduler
        self._state_model_service = model_service
        self._state_freshness_service = freshness_service
        if self._acquisition_executor is None:
            self._acquisition_executor = self._default_acquisition_executor_for_scheduler(
                scheduler
            )
        self._attach_state_acquisition_services(
            store=self._state_store,
            scheduler=scheduler,
            model_service=model_service,
            freshness_service=freshness_service,
        )

    def _start_state_freshness_task(self) -> None:
        if (
            self._state_freshness_service is None
            or self._acquisition_scheduler is None
            or self._state_store_freshness_task is not None
        ):
            return
        self._state_store_freshness_task = asyncio.get_running_loop().create_task(
            self._state_freshness_service.run(),
            name="rigctld-state-freshness",
        )

    def _start_state_acquisition_drain_task(self) -> None:
        if (
            self._acquisition_scheduler is None
            or self._state_acquisition_drain_task is not None
        ):
            return
        self._state_acquisition_drain_task = asyncio.get_running_loop().create_task(
            self._run_state_acquisition_drain(),
            name="rigctld-state-acquisition",
        )

    async def _send_one_state_query(
        self,
        cmd_byte: int,
        sub_byte: int | None,
        receiver: int | None,
    ) -> None:
        radio = self._radio
        if not isinstance(radio, CivCommandCapable):
            raise _AcquisitionExecutorUnavailable(
                "radio does not support CI-V state acquisition sends"
            )
        if receiver is not None:
            if cmd_byte in (0x25, 0x26):
                await radio.send_civ(
                    cmd_byte,
                    data=bytes([receiver]),
                    wait_response=False,
                )
                return
            inner = bytes([receiver, cmd_byte])
            if sub_byte is not None:
                inner += bytes([sub_byte])
            await radio.send_civ(0x29, data=inner, wait_response=False)
            return
        await radio.send_civ(
            cmd_byte,
            sub=sub_byte,
            data=b"",
            wait_response=False,
        )

    def _record_state_diagnostic(
        self,
        kind: str,
        source: str,
        **details: Any,
    ) -> None:
        if isinstance(self._state_diagnostics, StateDiagnosticsRecorder):
            self._state_diagnostics.record(kind, source, **details)

    @staticmethod
    def _acquisition_request_expired(
        request: AcquisitionRequest,
        *,
        sent_at: float,
        now: float,
    ) -> bool:
        deadlines = [request.deadline_monotonic]
        if request.timeout is not None:
            deadlines.append(sent_at + request.timeout)
        deadline = min(float(value) for value in deadlines)
        return now >= deadline

    async def _run_state_acquisition_drain(self) -> None:
        while True:
            try:
                await self._drain_state_acquisition_once()
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(
                    "rigctld state acquisition drain failed: %s",
                    exc,
                    exc_info=True,
                )
                self._record_state_diagnostic(
                    "acquisition_drain_error",
                    "rigctld.server",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                await asyncio.sleep(0.05)

    def _record_acquisition_failure(
        self,
        scheduler: AcquisitionScheduler,
        request: AcquisitionRequest,
        *,
        reason: str,
        failed_paths: tuple[FieldPath, ...] | frozenset[FieldPath],
        now: float,
        kind: str = "acquisition_request_failed",
        error: str | None = None,
        error_type: str | None = None,
    ) -> None:
        details: dict[str, Any] = {
            "request_id": request.id,
            "paths": [str(path) for path in failed_paths],
            "reason": reason,
            "provider": request.provider,
        }
        if error is not None:
            details["error"] = error
        if error_type is not None:
            details["error_type"] = error_type
        self._record_state_diagnostic(kind, "rigctld.server", **details)
        scheduler.record_acquisition_failure(
            request,
            reason=reason,
            failed_paths=failed_paths,
            now=now,
        )

    async def _drain_state_acquisition_once(self) -> None:
        scheduler = self._acquisition_scheduler
        if scheduler is None:
            return
        now = time.monotonic()
        pending = scheduler.pending_requests()
        pending_ids = {request.id for request in pending}
        for request_id in tuple(self._acquisition_in_flight):
            if request_id not in pending_ids:
                del self._acquisition_in_flight[request_id]

        for request in pending:
            sent_paths: frozenset[FieldPath] = frozenset()
            sent_at = 0.0
            existing = self._acquisition_in_flight.get(request.id)
            if existing is not None:
                sent_paths, sent_at = existing
                if self._acquisition_request_expired(
                    request,
                    sent_at=sent_at,
                    now=now,
                ):
                    self._record_acquisition_failure(
                        scheduler,
                        request,
                        reason="acquisition_request_timeout",
                        failed_paths=sent_paths or frozenset(request.paths),
                        now=now,
                    )
                    self._acquisition_in_flight.pop(request.id, None)
                    continue
                sent_paths = sent_paths.intersection(request.paths)

            if all(path in sent_paths for path in request.paths):
                continue

            executor = self._acquisition_executor
            if executor is None:
                if (
                    self._provider_uses_civ_executor(request.provider)
                    and not isinstance(self._radio, CivCommandCapable)
                ):
                    reason = "acquisition_executor_unavailable"
                    kind = reason
                else:
                    reason = "acquisition_executor_missing"
                    kind = reason
                self._record_acquisition_failure(
                    scheduler,
                    request,
                    reason=reason,
                    failed_paths=request.paths,
                    now=now,
                    kind=kind,
                )
                continue

            try:
                result: AcquisitionExecutionResult = await executor.execute(
                    request,
                    already_sent_paths=sent_paths,
                )
            except asyncio.CancelledError:
                raise
            except _AcquisitionExecutorUnavailable as exc:
                failed_paths = tuple(
                    path for path in request.paths if path not in sent_paths
                )
                self._record_acquisition_failure(
                    scheduler,
                    request,
                    reason="acquisition_executor_unavailable",
                    failed_paths=failed_paths or request.paths,
                    now=now,
                    kind="acquisition_executor_unavailable",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                self._acquisition_in_flight.pop(request.id, None)
                continue
            except Exception as exc:
                failed_paths = tuple(
                    path for path in request.paths if path not in sent_paths
                )
                self._record_acquisition_failure(
                    scheduler,
                    request,
                    reason="acquisition_executor_error",
                    failed_paths=failed_paths or request.paths,
                    now=now,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                self._acquisition_in_flight.pop(request.id, None)
                continue
            failed_paths = tuple(result.failed_paths)
            if failed_paths:
                reason = result.failure_reason or "acquisition_request_failed"
                self._record_acquisition_failure(
                    scheduler,
                    request,
                    reason=reason,
                    failed_paths=failed_paths,
                    now=now,
                )

            newly_sent = tuple(result.sent_paths)
            if newly_sent:
                self._acquisition_in_flight[request.id] = (
                    sent_paths.union(newly_sent),
                    now,
                )
                self._record_state_diagnostic(
                    "acquisition_request_sent",
                    "rigctld.server",
                    request_id=request.id,
                    paths=[str(path) for path in newly_sent],
                    pending_request_count=len(scheduler.pending_requests()),
                )

    async def start(self) -> None:
        """Start the TCP listener and initialise the command handler."""
        assert_radio_startup_ready(self._radio, component="rigctld startup")

        if self._protocol is None:
            from . import protocol as _proto_mod  # noqa: PLC0415, TID251

            self._protocol = _proto_mod

        if self._rig_handler is None:
            from . import handler as _handler_mod  # noqa: PLC0415, TID251

            self._bootstrap_state_acquisition()
            self._rig_handler = _handler_mod.RigctldHandler(
                self._radio,
                self._config,
                state_store=self._state_store,
                state_model_service=self._state_model_service,
            )

        self._server = await asyncio.start_server(
            self._accept_client,
            host=self._config.host,
            port=self._config.port,
        )
        self._server_was_running = True
        addr = self._server.sockets[0].getsockname()
        logger.info("rigctld listening on %s:%d", addr[0], addr[1])
        self._start_state_freshness_task()
        self._start_state_acquisition_drain_task()

        # Poller starts lazily on first client connection to avoid idle
        # CI-V traffic/noise when no CAT clients are connected.

    async def stop(self) -> None:
        """Close the listener and cancel all active client tasks."""
        if self._state_acquisition_drain_task is not None:
            self._state_acquisition_drain_task.cancel()
            try:
                await self._state_acquisition_drain_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug(
                    "rigctld state acquisition drain task failed before stop",
                    exc_info=True,
                )
            self._state_acquisition_drain_task = None

        if self._state_store_freshness_task is not None:
            self._state_store_freshness_task.cancel()
            try:
                await self._state_store_freshness_task
            except asyncio.CancelledError:
                pass
            self._state_store_freshness_task = None

        if self._poller is not None:
            await self._poller.stop()
            self._poller = None

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self._server_was_running = False

        tasks = list(self._client_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        logger.info("rigctld stopped")

    async def serve_forever(self) -> None:
        """Start the server and block until cancelled or stop() is called."""
        await self.start()
        assert self._server is not None
        try:
            await self._server.serve_forever()
        finally:
            await self.stop()

    async def __aenter__(self) -> RigctldServer:
        await self.start()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.stop()

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    def _check_rate_limit(self, client_id: int) -> bool:
        """Return True if the command is allowed, False if the rate limit is exceeded.

        Uses a 1-second sliding window per client.  Only allowed commands
        consume a slot; rejected commands are not counted.
        """
        limit = self._config.command_rate_limit
        if limit is None:
            return True
        now = time.monotonic()
        window = self._rate_windows.setdefault(client_id, [])
        cutoff = now - 1.0
        while window and window[0] <= cutoff:
            window.pop(0)
        if len(window) >= limit:
            return False
        window.append(now)
        return True

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _accept_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Callback from asyncio.start_server — spawns a per-client task.

        Called synchronously within the event loop; must not block.
        """
        if self._client_count >= self._config.max_clients:
            peer = writer.get_extra_info("peername", ("?", 0))
            logger.warning(
                "max_clients (%d) reached, rejecting %s:%s",
                self._config.max_clients,
                peer[0],
                peer[1],
            )
            writer.close()
            return

        loop = asyncio.get_running_loop()
        task = loop.create_task(self._handle_client(reader, writer))
        self._client_tasks.add(task)
        self._client_count += 1

        # Start poller when the first client connects.
        if self._poller is not None and self._client_count == 1:
            self._spawn(self._poller.start())

        # Optional WSJT-X compatibility pre-warm for first client:
        # if radio is in USB/LSB/RTTY with DATA off, enable DATA mode upfront
        # to avoid long CAT/PTT latency on first TX sequence.
        if self._client_count == 1 and self._config.wsjtx_compat:
            self._spawn(self._wsjtx_compat_prewarm())

        task.add_done_callback(self._on_client_done)

    def _on_client_done(self, task: asyncio.Task[None]) -> None:
        self._client_tasks.discard(task)
        self._client_count -= 1

        # Stop poller when no clients remain.
        if self._poller is not None and self._client_count <= 0:
            self._client_count = 0
            try:
                self._spawn(self._poller.stop())
            except RuntimeError:
                # Loop already closed during shutdown.
                pass

    async def _wsjtx_compat_prewarm(self) -> None:
        """Best-effort DATA-mode prewarm for WSJT-X compatibility mode."""
        get_mode = get_mode_reader(self._radio, _mode_to_name)
        if get_mode is None:
            return
        poller = self._poller
        if poller is not None:
            poller.write_busy = True
        try:
            mode_name, _ = await get_mode()
            data_on = await self._radio.get_data_mode()
            # A plain DATA flag does not tell us whether the rig is in DATA1,
            # DATA2, or DATA3.  When the embedded LAN bridge requests an
            # explicit WSJT-X DATA sub-mode, apply it even if DATA is already
            # on; otherwise keep the legacy prewarm behavior and only enable
            # DATA1 when the rig is still in plain USB/LSB/RTTY.
            explicit_data_mode = self._config.wsjtx_data_mode is not None
            if mode_name in {"USB", "LSB", "RTTY"} and (
                explicit_data_mode or not data_on
            ):
                data_mode = _wsjtx_data_mode_value(self._radio, self._config)
                if type(data_mode) is int and data_mode > _profile_data_mode_count(
                    self._radio
                ):
                    data_mode = True
                await _apply_wsjtx_data_mod_input(
                    self._radio,
                    data_mode=data_mode,
                    source=self._config.wsjtx_data_mod_input,
                )
                await self._radio.set_data_mode(data_mode)
                if poller is not None:
                    poller.hold_for(1.5)
                logger.info(
                    "WSJT-X compat prewarm: DATA%s enabled (base mode=%s)",
                    data_mode,
                    mode_name,
                )
        except Exception as exc:
            logger.debug("WSJT-X compat prewarm skipped/failed: %s", exc)
        finally:
            if poller is not None:
                poller.write_busy = False

    # ------------------------------------------------------------------
    # Per-client coroutine
    # ------------------------------------------------------------------

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Manage a single TCP client for its full lifetime."""
        proto = self._protocol

        peer = writer.get_extra_info("peername", ("?", 0))
        self._next_client_id += 1
        client_id = self._next_client_id
        session = ClientSession(
            client_id=client_id,
            peername=f"{peer[0]}:{peer[1]}",
        )
        logger.info("client #%d connected from %s", client_id, session.peername)

        try:
            while True:
                # ── read with idle timeout ───────────────────────────
                try:
                    raw = await asyncio.wait_for(
                        self._readline(reader),
                        timeout=self._config.client_timeout,
                    )
                except asyncio.TimeoutError:
                    logger.info(
                        "client #%d idle timeout (%.1fs)",
                        client_id,
                        self._config.client_timeout,
                    )
                    break
                except ValueError as exc:
                    # Line too long or malformed framing — close connection.
                    logger.warning("client #%d framing error: %s", client_id, exc)
                    break

                if raw is None:
                    logger.info("client #%d EOF", client_id)
                    break

                if not raw:
                    continue  # skip blank lines

                logger.debug("client #%d → %r", client_id, raw)

                # ── parse ────────────────────────────────────────────
                try:
                    cmd = proto.parse_line(raw, session)
                except ValueError as exc:
                    # Unknown command or bad args → ENIMPL, not EPROTO
                    # (WSJT-X sends commands we don't support yet)
                    logger.debug("client #%d unknown/bad command: %s", client_id, exc)
                    writer.write(proto.format_error(HamlibError.ENIMPL))
                    await writer.drain()
                    continue
                except Exception as exc:
                    logger.warning("client #%d parse error: %s", client_id, exc)
                    writer.write(proto.format_error(HamlibError.EPROTO))
                    await writer.drain()
                    continue

                # ── quit ─────────────────────────────────────────────
                if cmd.short_cmd == "q":
                    logger.info("client #%d quit", client_id)
                    break

                # ── rate limit ───────────────────────────────────────
                if not self._check_rate_limit(client_id):
                    logger.warning(
                        "client #%d rate limited (limit=%.1f cmds/sec)",
                        client_id,
                        self._config.command_rate_limit,
                    )
                    writer.write(proto.format_error(HamlibError.EIO))
                    await writer.drain()
                    continue

                # ── execute with command timeout ─────────────────────
                poller_hold = bool(cmd.is_set and self._poller is not None)
                if poller_hold:
                    self._poller.write_busy = True
                t_start = time.monotonic()
                try:
                    resp = await asyncio.wait_for(
                        self._handler_execute_call(
                            cmd,
                            session_id=f"rigctld-client-{client_id}",
                        ),
                        timeout=self._config.command_timeout,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "client #%d command %r timed out",
                        client_id,
                        cmd.short_cmd,
                    )
                    writer.write(proto.format_error(HamlibError.ETIMEOUT))
                    await writer.drain()
                    continue
                except Exception as exc:
                    logger.error("client #%d handler error: %s", client_id, exc)
                    writer.write(proto.format_error(HamlibError.EIO))
                    await writer.drain()
                    continue
                finally:
                    if poller_hold:
                        # Packet mode transitions can make CI-V briefly unresponsive.
                        # Keep poller paused for a settle window to avoid
                        # immediate get_frequency storms.
                        if _is_packet_mode_set(cmd):
                            try:
                                self._poller.hold_for(3.0)
                            except Exception:
                                logger.debug("hold_for failed", exc_info=True)
                        self._poller.write_busy = False

                # ── session-state wiring (vfo_opt handshake) ─────────
                # When the handler advertised vfo_opt via ``\chk_vfo`` →
                # ``"1"``, Hamlib will start prefixing every command
                # with ``VFOA``/``VFOB``/``currVFO``. Flip the session
                # bit so the parser knows it's expected and so the
                # diagnostic in protocol.parse_line stays quiet. Wired
                # here (rather than in handler.py) to keep the leaf
                # handlers free of session arguments. Dormant on `main`
                # because Variant B (#1340) keeps chk_vfo at ``"0"``;
                # #1346 (A5) flips it back to ``"1"`` for dual-RX.
                if cmd.long_cmd == "chk_vfo" and resp.values == ["1"]:
                    session.vfo_mode = True

                # ── send response ────────────────────────────────────
                out = proto.format_response(cmd, resp, session)
                duration_ms = round((time.monotonic() - t_start) * 1000, 3)
                logger.debug("client #%d ← %r", client_id, out)
                writer.write(out)
                await writer.drain()

                # ── audit ────────────────────────────────────────────
                _audit.log_command(
                    _audit.AuditRecord(
                        timestamp=datetime.datetime.now(
                            datetime.timezone.utc
                        ).isoformat(),
                        client_id=client_id,
                        peername=session.peername,
                        cmd=cmd.short_cmd,
                        long_cmd=cmd.long_cmd,
                        args=cmd.args,
                        vfo=cmd.vfo_arg,
                        duration_ms=duration_ms,
                        rprt=resp.error,
                        is_set=cmd.is_set,
                    )
                )

        except asyncio.CancelledError:
            logger.info("client #%d cancelled (server shutdown)", client_id)
        except ConnectionResetError:
            logger.info("client #%d connection reset by peer", client_id)
        except Exception as exc:
            logger.error(
                "client #%d unexpected error: %s", client_id, exc, exc_info=True
            )
        finally:
            self._rate_windows.pop(client_id, None)
            try:
                writer.close()
                await writer.wait_closed()
            except OSError:
                pass
            logger.info("client #%d disconnected", client_id)

    # ------------------------------------------------------------------
    # Line reader
    # ------------------------------------------------------------------

    async def _readline(self, reader: asyncio.StreamReader) -> bytes | None:
        """Read one newline-terminated line, stripping the terminator.

        Returns:
            Stripped bytes, or None on EOF.

        Raises:
            ValueError: If the line exceeds max_line_length.
        """
        try:
            line = await reader.readline()
        except asyncio.IncompleteReadError:
            # EOF arrived before a newline (abrupt disconnect).
            return None
        except asyncio.LimitOverrunError:
            raise ValueError("command line exceeds StreamReader buffer limit")

        if not line:
            return None  # clean EOF

        stripped = line.rstrip(b"\r\n")
        if len(stripped) > self._config.max_line_length:
            raise ValueError(
                f"command line too long: {len(stripped)} bytes "
                f"(max {self._config.max_line_length})"
            )
        return stripped


# ---------------------------------------------------------------------------
# Convenience entry point
# ---------------------------------------------------------------------------


async def run_rigctld_server(radio: "Radio", **kwargs: Any) -> None:
    """Create a :class:`RigctldServer` from *kwargs* and run it forever.

    Keyword arguments are forwarded to :class:`RigctldConfig`.

    Example::

        await run_rigctld_server(radio, host="0.0.0.0", port=4532)
    """
    config = RigctldConfig(**kwargs)
    server = RigctldServer(radio, config)
    await server.serve_forever()
