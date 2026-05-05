"""Startup/shutdown orchestration for :class:`icom_lan.web.server.WebServer`.

Extracted from ``web/server.py`` to keep ``WebServer.start()`` /
``WebServer.stop()`` as thin delegators (issue #1261, Tier 3 wave 4 of #1063).

The functions here access ``WebServer`` state via the ``server`` argument
(clean dependency injection — no module-level state).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.radio_protocol import StatePollable
from ..radio_state import RadioState
from ..startup_checks import assert_radio_startup_ready
from .discovery import DiscoveryResponder, RadioInfo  # noqa: TID251
from .dx_cluster import DXClusterClient  # noqa: TID251
from .radio_poller import RadioPoller  # noqa: TID251
from .runtime_helpers import runtime_capabilities  # noqa: TID251

if TYPE_CHECKING:
    from .server import WebServer  # noqa: TID251

__all__ = ["start_web_server", "stop_web_server"]

logger = logging.getLogger(__name__)


def _supports_scope_local(server: WebServer) -> bool:
    return "scope" in runtime_capabilities(server._radio)


async def start_web_server(server: WebServer) -> None:
    """Start the HTTP/WS listener and RadioPoller (if radio is connected).

    Mirrors the original :meth:`WebServer.start` body verbatim — the method
    now delegates here so the public API is preserved.
    """
    # Load band plan TOML files
    # Try project-level band-plans/ directory first, then package fallback
    project_bp = Path(__file__).resolve().parents[3] / "band-plans"
    if project_bp.is_dir():
        server._band_plan.load(project_bp)
    else:
        logger.info("band-plan: no band-plans/ directory found")

    # Load EiBi cache if available (non-blocking)
    try:
        result = await server._eibi.load_cache()
        if result.get("status") == "ok":
            logger.info(
                "eibi: loaded %d stations from cache (season %s)",
                server._eibi.station_count,
                server._eibi.season,
            )
    except Exception:
        logger.debug("eibi: no cache to load at startup")

    ssl_ctx = None
    if server._config.tls:
        from .tls import build_ssl_context  # noqa: TID251

        ssl_ctx = build_ssl_context(
            cert_path=server._config.tls_cert or None,
            key_path=server._config.tls_key or None,
        )

    assert_radio_startup_ready(server._radio, component="web startup")

    server._server = await asyncio.start_server(
        server._accept_client,
        host=server._config.host,
        port=server._config.port,
        ssl=ssl_ctx,
        reuse_address=True,
        reuse_port=True,
    )
    addr = server._server.sockets[0].getsockname()
    scheme = "https" if ssl_ctx else "http"
    logger.info("web server listening on %s://%s:%d", scheme, addr[0], addr[1])
    if server._radio is not None:
        from ..radio_protocol import StateNotifyCapable

        # --- Request-response state poller (Yaesu CAT et al.) ---
        if isinstance(server._radio, StatePollable):

            def _state_cb(state: RadioState) -> None:
                server._radio_state = state
                server._broadcast_state_update()

            server._state_poller = server._radio.create_state_poller(
                callback=_state_cb,
                command_queue=server._command_queue,
            )
            server._spawn(server._state_poller.start())
            logger.info("state poller started")
        else:
            # --- Icom CI-V backend: fire-and-forget RadioPoller ---
            if isinstance(server._radio, StateNotifyCapable):
                # Register callback so CI-V RX stream can notify us of state changes.
                server._radio.set_state_change_callback(server._on_radio_state_change)
                # Re-enable scope after soft_reconnect (CI-V stream reset loses scope state)
                server._radio.set_reconnect_callback(server._on_radio_reconnect)
            server._radio_poller = RadioPoller(
                server._radio,
                server._command_queue,
                on_state_event=server._on_poller_state_event,
                radio_state=server._radio_state,
            )
            server._radio_poller.start()
        if _supports_scope_local(server):
            server._scope_health_task = asyncio.get_running_loop().create_task(
                server._scope_health_monitor(), name="scope-health"
            )
    server._zombie_reaper_task = asyncio.get_running_loop().create_task(
        server._zombie_reaper(), name="zombie-reaper"
    )
    if server._config.dx_cluster_host:
        server._dx_client = DXClusterClient(
            server._config.dx_cluster_host,
            server._config.dx_cluster_port,
            server._config.dx_callsign,
            on_spot=server._broadcast_dx_spot,
        )
        server._dx_client_task = asyncio.get_running_loop().create_task(
            server._dx_client.start(), name="dx-cluster"
        )
        logger.info(
            "dx-cluster: connecting to %s:%d as %s",
            server._config.dx_cluster_host,
            server._config.dx_cluster_port,
            server._config.dx_callsign,
        )

    # Start UDP discovery responder
    if server._config.discovery:
        radio = server._radio

        def _radio_provider() -> RadioInfo | None:
            if radio is None:
                return None
            return RadioInfo(
                model=getattr(radio, "model", None) or server._config.radio_model,
                connected=bool(getattr(radio, "connected", False)),
            )

        server._discovery = DiscoveryResponder(
            web_port=server._config.port,
            tls=server._config.tls,
            radio_provider=_radio_provider,
            discovery_port=server._config.discovery_port,
        )
        await server._discovery.start()


async def stop_web_server(server: WebServer) -> None:
    """Close the listener, stop RadioPoller, disconnect radio, cancel tasks.

    Mirrors the original :meth:`WebServer.stop` body verbatim — the method
    now delegates here so the public API is preserved.
    """
    # 1. Stop poller first (no more CI-V queries)
    if server._radio_poller is not None:
        server._radio_poller.stop()
        server._radio_poller = None
    if server._state_poller is not None:
        try:
            await asyncio.wait_for(server._state_poller.stop(), timeout=2.0)
        except TimeoutError:
            logger.warning("state poller stop timed out")
        server._state_poller = None

    # 2. Stop audio relay (stops AudioBus subscription → stop_audio_rx_opus)
    try:
        await asyncio.wait_for(server._audio_broadcaster._stop_relay(), timeout=2.0)
    except (TimeoutError, Exception) as exc:
        logger.warning("audio relay stop: %s", exc)

    # 2b. Stop diagnostic preview sweeper and clean up any in-flight bundles.
    try:
        await asyncio.wait_for(server._diagnostics.stop(), timeout=2.0)
    except (TimeoutError, Exception) as exc:
        logger.warning("diagnostics handler stop: %s", exc)
    if server._audio_bridge is not None:
        await server.stop_audio_bridge()

    # 3. Stop discovery responder
    if server._discovery is not None:
        await server._discovery.stop()
        server._discovery = None

    # 4. Stop DX cluster
    if server._dx_client is not None:
        await server._dx_client.stop()
        server._dx_client = None
    if server._dx_client_task is not None:
        server._dx_client_task.cancel()
        try:
            await server._dx_client_task
        except asyncio.CancelledError:
            pass
        server._dx_client_task = None

    # 5. Cancel housekeeping tasks
    for task in (
        server._zombie_reaper_task,
        server._scope_health_task,
        server._scope_reenable_task,
    ):
        if task is not None:
            task.cancel()
    server._zombie_reaper_task = None
    server._scope_health_task = None
    server._scope_reenable_task = None

    # 6. Cancel all background + client tasks first
    all_tasks = list(server._bg_tasks) + list(server._client_tasks)
    for task in all_tasks:
        task.cancel()

    # 7. Close TCP listener (now that client tasks are cancelled,
    #    wait_closed() won't block on open connections)
    if server._server is not None:
        server._server.close()
        try:
            await asyncio.wait_for(server._server.wait_closed(), timeout=2.0)
        except TimeoutError:
            logger.warning("server.wait_closed() timed out after 2s")
        server._server = None

    # 8. Wait for cancelled tasks to finish (with timeout)
    if all_tasks:
        try:
            await asyncio.wait_for(
                asyncio.gather(*all_tasks, return_exceptions=True),
                timeout=3.0,
            )
        except TimeoutError:
            logger.warning("tasks did not finish in 3s, continuing shutdown")
    server._bg_tasks.clear()

    # Radio disconnect is handled by the caller's context manager
    # (async with radio: in _run). Do NOT disconnect here.
    logger.info("web server stopped")
