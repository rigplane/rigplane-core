"""TCI adapter handler: the Core side of the leaf ``TciHandler`` (MOR-3821 C1).

One instance per accepted socket; the owner id (``tci-client-N``) is minted
once by the server factory, as ``rigctld-client-N`` is. C1 implements the
init burst, the read-only commands and the teardown release; sets of Tier
A/B commands degrade to reads here and become real writes in C2.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from ..core.state_store import StateStore
from ..runtime.managed_tx_authority import ATTEMPT_TIMEOUT_S, RETRY_DELAY_S
from ..runtime.managed_tx_submission import release_managed_session_tx
from ..tci.handler import CommandOutcome
from .commands import (
    SPOT_COMMANDS,
    START_STOP_COMMANDS,
    TIER_AB_COMMANDS,
)
from .notifier import TciNotifier, drive_percent, project_state, tx_frequency

logger = logging.getLogger(__name__)

__all__ = [
    "SESSION_TX_HANDBACK_TIMEOUT_S",
    "TciAdapterConfig",
    "TciAdapterHandler",
]

#: Bound on each wait for a departing session's managed-TX handback: one
#: authority attempt plus its retry delay, the same sum ``rigctld`` uses
#: (``SESSION_TX_HANDBACK_TIMEOUT_S`` there; recomputed from the shared
#: authority constants because the adapter may not import the sibling seat).
SESSION_TX_HANDBACK_TIMEOUT_S: float = ATTEMPT_TIMEOUT_S + RETRY_DELAY_S

#: Commands whose first argument is the ``trx`` index (always 0, spec 5.3).
_TRX_SCOPED = frozenset(
    {
        "vfo",
        "modulation",
        "split_enable",
        "trx",
        "drive",
        "tune",
        "tune_drive",
        "rit_enable",
        "xit_enable",
        "rit_offset",
        "xit_offset",
        "dds",
        "if",
        "tx_enable",
        "tx_frequency",
    }
)


class TciAdapterConfig:
    """Adapter knobs (mirrors the rigctld seat's timeout/cache pair)."""

    def __init__(self, *, command_timeout: float = 2.0, cache_ttl: float = 0.2) -> None:
        self.command_timeout = command_timeout
        self.cache_ttl = cache_ttl


class TciAdapterHandler:
    """Per-connection TCI command handler (C1: reads, burst, teardown)."""

    def __init__(
        self,
        *,
        radio: Any,
        state_store: StateStore,
        owner: str,
        managed_tx_authority: Any = None,
        command_queue: Any = None,
        command_service: Any = None,
        state_model_service: Any = None,
        config: TciAdapterConfig | None = None,
        clock: Callable[[], float] = time.monotonic,
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
        if not owner:
            raise ValueError("owner must be a non-empty session id")
        self._radio = radio
        self._store = state_store
        self._owner = owner
        self._authority = managed_tx_authority
        self._queue = command_queue
        self._service = command_service
        self._state_model_service = state_model_service
        self._config = config or TciAdapterConfig()
        self._clock = clock
        self._notifier = TciNotifier(
            radio=radio,
            store=state_store,
            managed_present=all(supplied),
        )
        self._ignored_logged: set[str] = set()
        self._state_model_service_checked = False

    @property
    def owner(self) -> str:
        """The lease-bearing owner id minted for this socket."""
        return self._owner

    @property
    def notifier(self) -> TciNotifier:
        """The shared projection object (C2 writes re-project through it)."""
        return self._notifier

    # -- leaf contract ---------------------------------------------------

    async def on_connect(self) -> Sequence[str]:
        """Return the init burst (spec 5.4)."""
        return await self._notifier.init_burst()

    async def on_binary(self, payload: bytes) -> None:
        """Binary frames are phase audio: ignored until it ships (spec 6.2)."""

    async def on_disconnect(self) -> None:
        """Release the session's managed TX lease, exactly once (spec 7.4)."""
        await release_managed_session_tx(
            self.release_session_tx if self._authority is not None else None,
            self._owner,
            handback_timeout_s=SESSION_TX_HANDBACK_TIMEOUT_S,
        )

    async def release_session_tx(self, session_id: str) -> None:
        """Hand back any managed TX lease ``session_id`` still holds."""
        try:
            if self._authority is not None:
                await self._authority.owner_disconnect(session_id)
        except Exception:
            logger.warning(
                "tci: managed TX release for session %s failed",
                session_id,
                exc_info=True,
            )

    async def on_command(self, name: str, args: tuple[str, ...]) -> CommandOutcome:
        """Handle one parsed command; never raises into the server."""
        try:
            return await self._dispatch(name, args)
        except Exception:
            logger.debug("tci %s: command %s failed", self._owner, name, exc_info=True)
            return CommandOutcome()

    # -- dispatch --------------------------------------------------------

    async def _dispatch(self, name: str, args: tuple[str, ...]) -> CommandOutcome:
        if name not in TIER_AB_COMMANDS:
            # Tier C or unknown (spec 6.1): ignored without a reply, logged
            # once per connection with the name only (C0 needs the names).
            if name not in self._ignored_logged:
                self._ignored_logged.add(name)
                logger.info(
                    "tci %s: ignoring Tier C/unknown command %s", self._owner, name
                )
            return CommandOutcome()
        if name in _TRX_SCOPED and not self._trx_ok(args):
            return CommandOutcome()
        if name in START_STOP_COMMANDS:
            echo = f"{name};"
            return CommandOutcome(to_sender=(echo,), to_others=(echo,))
        if name in SPOT_COMMANDS:
            # Accepted and dropped: no panorama exists (spec 5.5).
            return CommandOutcome()
        if name == "ready":
            return CommandOutcome()
        if name in ("vfo",):
            return await self._read_vfo(args)
        if name == "modulation":
            return await self._read_modulation()
        if name == "split_enable":
            return await self._read_split()
        if name == "trx":
            return await self._read_trx()
        if name in ("drive", "tune_drive"):
            return await self._read_drive(name)
        if name == "tune":
            line = "tune:0,false;"
            return CommandOutcome(to_sender=(line,), to_others=(line,))
        if name in ("rit_enable", "xit_enable"):
            return await self._read_rit_enable(name)
        if name in ("rit_offset", "xit_offset"):
            return await self._read_rit_offset(name)
        if name == "tx_enable":
            return await self._read_tx_enable()
        if name == "tx_frequency":
            return await self._read_tx_frequency()
        if name in ("dds", "if"):
            return await self._read_dds_if()
        if name in ("cw_keyer_speed", "cw_macros_speed"):
            return await self._read_key_speed(name)
        if name in (
            "vfo_limits",
            "modulations_list",
            "trx_count",
            "channels_count",
            "device",
            "protocol",
            "receive_only",
        ):
            return await self._read_identity(name)
        # Known Tier A/B write-only set (C2 writes): degrade to the current
        # state here so a write is still answered (spec 5.2).
        return await self._read_current(name)

    @staticmethod
    def _trx_ok(args: tuple[str, ...]) -> bool:
        """The trx selector, when present, must address receiver 0 (5.3)."""
        if not args:
            return True
        try:
            return int(args[0]) == 0
        except ValueError:
            return False

    # -- reads -----------------------------------------------------------

    def _ensure_fresh(self, reason: str) -> None:
        """Ask acquisition to refresh, the way the rigctld seat does.

        The state-model contract is synchronous (enqueue and return), so a
        coroutine ``ensure_fresh`` is skipped, never awaited; the caller
        always re-projects the store afterwards.
        """
        service = self._state_model_service
        if service is None or self._config.cache_ttl <= 0:
            return
        ensure = getattr(service, "ensure_fresh", None)
        if ensure is None or not callable(ensure):
            return
        import inspect

        if inspect.iscoroutinefunction(ensure):
            return
        try:
            ensure((), max_age=self._config.cache_ttl, priority="user", reason=reason)
        except Exception:
            logger.debug("tci %s: ensure_fresh failed", self._owner, exc_info=True)

    async def _state(self) -> Any:
        self._ensure_fresh("tci.read")
        return await project_state(
            self._radio,
            self._store,
            managed_keyed=self._managed_keyed(),
            fallback_modulation=self._notifier.modulation,
        )

    def _managed_keyed(self) -> bool:
        if self._authority is None:
            return False
        try:
            snapshot = self._authority.snapshot_nowait()
        except Exception:
            return False
        return bool(getattr(getattr(snapshot, "state", None), "intent", None))

    @staticmethod
    def _reply(line: str) -> CommandOutcome:
        return CommandOutcome(to_sender=(line,), to_others=(line,))

    async def _read_vfo(self, args: tuple[str, ...]) -> CommandOutcome:
        channel = 0
        if len(args) >= 2:
            try:
                channel = int(args[1])
            except ValueError:
                channel = 0
            if channel not in (0, 1):
                return CommandOutcome()
        state = await self._state()
        freq = state.freq_a if channel == 0 else state.freq_b
        return self._reply(f"vfo:0,{channel},{freq};")

    async def _read_modulation(self) -> CommandOutcome:
        state = await self._state()
        return self._reply(f"modulation:0,{state.modulation};")

    async def _read_split(self) -> CommandOutcome:
        state = await self._state()
        return self._reply(f"split_enable:0,{_tf(state.split)};")

    async def _read_trx(self) -> CommandOutcome:
        state = await self._state()
        return self._reply(f"trx:0,{_tf(state.ptt)};")

    async def _read_drive(self, name: str = "drive") -> CommandOutcome:
        state = await self._state()
        drive = drive_percent(state.power_native, self._radio)
        return self._reply(f"{name}:0,{drive};")

    async def _read_rit_enable(self, name: str) -> CommandOutcome:
        state = await self._state()
        on = state.rit_on if name == "rit_enable" else state.rit_tx
        key = "rit_enable" if name == "rit_enable" else "xit_enable"
        return self._reply(f"{key}:0,{_tf(on)};")

    async def _read_rit_offset(self, name: str) -> CommandOutcome:
        state = await self._state()
        key = "rit_offset" if name == "rit_offset" else "xit_offset"
        return self._reply(f"{key}:0,{state.rit_freq};")

    async def _read_tx_enable(self) -> CommandOutcome:
        return self._reply(f"tx_enable:0,{_tf(self._notifier.managed_present)};")

    async def _read_tx_frequency(self) -> CommandOutcome:
        state = await self._state()
        # No trx prefix: eesdr-tci declares TX_FREQUENCY without rx/sub_rx.
        return self._reply(f"tx_frequency:{tx_frequency(state)};")

    async def _read_dds_if(self) -> CommandOutcome:
        state = await self._state()
        lines = (
            f"dds:0,{state.freq_a};",
            "if:0,0,0;",
            f"if:0,1,{state.freq_b - state.freq_a};",
        )
        return CommandOutcome(to_sender=lines, to_others=lines)

    async def _read_key_speed(self, name: str) -> CommandOutcome:
        speed = 20
        get_speed = getattr(self._radio, "get_key_speed", None)
        if callable(get_speed):
            try:
                speed = int(await get_speed())
            except Exception:
                pass
        return self._reply(f"{name}:{speed};")

    async def _read_identity(self, name: str) -> CommandOutcome:
        lines = await self._notifier.current_lines()
        picked = tuple(sorted({line for line in lines if line.startswith(f"{name}:")}))
        if not picked:
            return CommandOutcome()
        return CommandOutcome(to_sender=picked, to_others=picked)

    async def _read_current(self, name: str) -> CommandOutcome:
        """Degrade a C1 set to the current state (real writes land in C2)."""
        readers: Mapping[str, Callable[[], Awaitable[CommandOutcome]]] = {
            "vfo": lambda: self._read_vfo(("0", "0")),
            "modulation": self._read_modulation,
            "split_enable": self._read_split,
            "trx": self._read_trx,
            "drive": lambda: self._read_drive("drive"),
            "tune_drive": lambda: self._read_drive("tune_drive"),
            "rit_enable": lambda: self._read_rit_enable("rit_enable"),
            "xit_enable": lambda: self._read_rit_enable("xit_enable"),
            "rit_offset": lambda: self._read_rit_offset("rit_offset"),
            "xit_offset": lambda: self._read_rit_offset("xit_offset"),
            "cw_keyer_speed": lambda: self._read_key_speed("cw_keyer_speed"),
            "cw_macros_speed": lambda: self._read_key_speed("cw_macros_speed"),
        }
        reader = readers.get(name)
        if reader is None:
            return CommandOutcome()
        return await reader()


def _tf(value: bool) -> str:
    return "true" if value else "false"
