"""TCI adapter server: the leaf listener bound to Core (MOR-3821 C1, 4/7.4/9).

:func:`tci_allow_predicate` builds the Origin/Host predicate from
``core.host_guard`` the way the web seat does (native clients send no
``Origin`` and are admitted; a browser ``Origin`` that is not
same-origin is refused, as is a non-local ``Host``).
:class:`TciAdapterServer` owns one leaf :class:`TciServer`, mints the
per-socket ``tci-client-N`` owner ids, and runs the notifier poll that
broadcasts ``StateStore`` deltas to every session.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from ..core.host_guard import (
    ORIGIN_FORBIDDEN_BODY,
    MISDIRECTED_BODY,
    host_header_allowed,
    same_origin_allowed,
)
from ..core.state_store import StateStore
from ..tci.handler import TciHandler
from ..tci.server import TciServer
from ..ws import AllowHeaders, UpgradeDecline
from .commands import TCI_DEFAULT_HOST, TCI_DEFAULT_PORT
from .handler import TciAdapterConfig, TciAdapterHandler
from .notifier import TciNotifier

logger = logging.getLogger(__name__)

__all__ = [
    "TciAdapterServer",
    "build_tci_server",
    "tci_allow_predicate",
]


def tci_allow_predicate(
    *,
    allowed_hosts: tuple[str, ...] = (),
) -> AllowHeaders:
    """Upgrade predicate: local ``Host`` required, foreign ``Origin`` refused.

    Mirrors ``WebServer._handle_websocket`` (MOR-2880/2881): a missing
    ``Host`` or ``Origin`` is admitted (non-browser clients send neither),
    anything else must pass the shared ``core.host_guard`` checks.
    """

    def _allow(request_headers: Mapping[str, str]) -> UpgradeDecline | None:
        raw_host = request_headers.get("host")
        if not host_header_allowed(raw_host, allowed_hosts):
            return UpgradeDecline(421, "Misdirected Request", MISDIRECTED_BODY)
        origin = request_headers.get("origin")
        if origin is not None and not same_origin_allowed(
            origin, raw_host, "http", allowed_hosts, ()
        ):
            return UpgradeDecline(403, "Forbidden", ORIGIN_FORBIDDEN_BODY)
        return None

    return _allow


class TciAdapterServer:
    """One TCI listener: leaf server plus the shared notifier poll."""

    def __init__(
        self,
        *,
        radio: Any,
        state_store: StateStore,
        host: str = TCI_DEFAULT_HOST,
        port: int = TCI_DEFAULT_PORT,
        handler_factory: Callable[[str], TciHandler] | None = None,
        managed_tx_authority: Any = None,
        command_queue: Any = None,
        command_service: Any = None,
        state_model_service: Any = None,
        config: TciAdapterConfig | None = None,
        allow: AllowHeaders | None = None,
        clock: Callable[[], float] = time.monotonic,
        poll_interval: float = 0.2,
        coalesce_window: float | None = None,
    ) -> None:
        supplied = (
            managed_tx_authority is not None,
            command_queue is not None,
            command_service is not None,
        )
        if any(supplied) and not all(supplied):
            raise ValueError(
                "managed authority, command queue, and service must be supplied together"
            )
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        self._radio = radio
        self._store = state_store
        self._authority = managed_tx_authority
        self._queue = command_queue
        self._service = command_service
        self._state_model_service = state_model_service
        self._config = config or TciAdapterConfig()
        self._clock = clock
        self._poll_interval = poll_interval
        self._notifier = TciNotifier(
            radio=radio, store=state_store, managed_present=all(supplied)
        )
        self._ids = itertools.count(1)
        self._external_factory = handler_factory
        leaf_kwargs: dict[str, Any] = {}
        if coalesce_window is not None:
            leaf_kwargs["coalesce_window"] = coalesce_window
        self._server = TciServer(
            self._make_handler,
            host=host,
            port=port,
            allow=allow if allow is not None else tci_allow_predicate(),
            clock=clock,
            **leaf_kwargs,
        )
        self._poll_task: asyncio.Task[None] | None = None

    def _make_handler(self) -> TciHandler:
        if self._external_factory is not None:
            return self._external_factory(f"tci-client-{next(self._ids)}")
        return TciAdapterHandler(
            radio=self._radio,
            state_store=self._store,
            owner=f"tci-client-{next(self._ids)}",
            managed_tx_authority=self._authority,
            command_queue=self._queue,
            command_service=self._service,
            state_model_service=self._state_model_service,
            config=self._config,
            clock=self._clock,
        )

    @property
    def bound_port(self) -> int:
        """Actual bound port (``port=0`` picks an ephemeral one)."""
        return self._server.bound_port

    @property
    def sessions(self) -> Sequence[Any]:
        """Live sessions, oldest first."""
        return self._server.sessions

    @property
    def notifier(self) -> TciNotifier:
        """The shared projection object (tests drive ``refresh`` directly)."""
        return self._notifier

    async def start(self) -> None:
        """Bind the listener and start the notification poll."""
        await self._server.start()
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.ensure_future(self._poll())
        if self._host_exposed(self._server):
            logger.warning(
                "tci listening on a non-loopback address; remote PTT has rigctld parity"
            )

    @staticmethod
    def _host_exposed(server: TciServer) -> bool:
        listener = server._listener  # noqa: SLF001
        if listener is None:
            return False
        try:
            sockets = listener.sockets or ()
            names = {str(sock.getsockname()[0]) for sock in sockets}
        except Exception:
            return False
        return bool(names - {"127.0.0.1", "::1"})

    async def stop(self) -> None:
        """Stop the poll, then every session, then the listener (spec 7.4)."""
        task, self._poll_task = self._poll_task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug("tci poll stop failed", exc_info=True)
        await self._server.stop()

    async def _poll(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._poll_interval)
                try:
                    lines = await self._notifier.refresh()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.debug("tci poll refresh failed", exc_info=True)
                    continue
                if lines:
                    self._server.broadcast(lines)
        except asyncio.CancelledError:
            pass


def build_tci_server(
    *,
    radio: Any,
    state_store: StateStore,
    host: str = TCI_DEFAULT_HOST,
    port: int = TCI_DEFAULT_PORT,
    **kwargs: Any,
) -> TciAdapterServer:
    """Build the adapter server over an already-composed store.

    The adapter starts no acquisition of its own: the store is fed by the
    seat that owns the radio (design 5.1).
    """
    return TciAdapterServer(
        radio=radio, state_store=state_store, host=host, port=port, **kwargs
    )
