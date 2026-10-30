"""TCI adapter handler: the Core side of the leaf ``TciHandler`` (MOR-3821).

One instance per accepted socket; the owner id (``tci-client-N``) is minted
once by the server factory, as ``rigctld-client-N`` is. Reads project the
shared store; writes build a ``CommandIntent`` (``source="tci"``) and take
the same queue and executor arms as a ``rigctld`` write. A write is always
answered: success replies to every session — a VFO write with the
confirmed frequency from Core's state, any other with an echo — and a
no-op or failure answers the actual state to the requester only.
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from ..audio.route import (
    apply_wsjtx_data_mod_input,
    profile_data_mode_count,
    resolve_audio_route,
    rigctld_wsjtx_policy,
)
from ..core.command_service import (
    CommandExecutionInvalidatedError,
    CommandExecutionResult,
    command_intent_from_request,
    resolve_power_level_target,
)
from ..core.exceptions import CommandError, CommandRejectedError
from ..core.radio_protocol import (
    RelativeVfoFrequencyWritable,
    RelativeVfoModeWritable,
    RitXitCapable,
    SplitCapable,
)
from ..core.state_store import StateStore
from ..runtime.controller_authority import ControllerError
from ..runtime.managed_tx_authority import ATTEMPT_TIMEOUT_S, RETRY_DELAY_S
from ..runtime.managed_tx_submission import (
    ManagedPttTracker,
    execute_managed_ptt,
    release_managed_session_tx,
)
from ..tci.frames import HEADER_SIZE, StreamType, unpack_header
from ..tci.handler import CommandOutcome
from ..tci.protocol import parse_bool, parse_int
from .audio import (
    TciAudioSettings,
    TciRxPump,
    TciTxSession,
    rx_audio_available,
    tx_route_ready,
)
from .commands import (
    SPOT_COMMANDS,
    START_STOP_COMMANDS,
    TCI_TO_CORE_MODE,
    TIER_AB_COMMANDS,
)
from .notifier import (
    READ_PATHS,
    TciNotifier,
    announced_modulations,
    authority_keyed,
    drive_percent,
    project_state,
    supports_data_mode,
    tx_frequency,
    tx_role_tci,
)

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
        "audio_samplerate",
        "audio_stream_sample_type",
        "audio_stream_channels",
        "audio_stream_samples",
        "audio_start",
        "audio_stop",
        "tx_stream_audio_buffering",
    }
)


class TciAdapterConfig:
    """Adapter knobs (mirrors the rigctld seat's timeout/cache pair)."""

    def __init__(self, *, command_timeout: float = 2.0, cache_ttl: float = 0.2) -> None:
        self.command_timeout = command_timeout
        self.cache_ttl = cache_ttl


class _TciWriteError(Exception):
    """A write that reached no radio effect (no-op or refused input)."""


class _TciCommandExecutor:
    """Execute TCI write intents against the radio (rigctld parity).

    The ticket is captured at the start of every intent and re-validated
    immediately before each radio effect; when another seat holds the
    controller the write is refused with no radio effect. Relative-VFO
    writes carry the validation as ``before_set`` so the transport's
    final currency check enforces it as well.
    """

    def __init__(self, handler: TciAdapterHandler) -> None:
        self._handler = handler

    async def execute(self, intent: Any) -> CommandExecutionResult:
        handler = self._handler
        try:
            ticket = handler._capture_ticket()
        except ControllerError as exc:
            raise CommandRejectedError(str(exc)) from exc
        name = intent.name
        params = intent.params
        if name == "set_freq":
            handler._validate_ticket(ticket)
            await handler._radio.set_freq(
                int(params["freq_hz"]), receiver=int(params.get("receiver", 0))
            )
        elif name == "set_split_freq":
            handler._validate_ticket(ticket)
            await handler._queued_split_freq(intent, ticket)
        elif name == "set_mode":
            receiver = int(params.get("receiver", 0))
            mode = str(params["mode"])
            handler._validate_ticket(ticket)
            if receiver == 0:
                await handler._radio.set_mode(mode, filter_width=None)
            else:
                await handler._radio.set_mode(
                    mode, filter_width=None, receiver=receiver
                )
        elif name == "set_split":
            handler._validate_ticket(ticket)
            await handler._radio.set_split(bool(params["on"]))
        elif name == "set_rit":
            handler._validate_ticket(ticket)
            await handler._radio.set_rit_frequency(int(params["hz"]))
        elif name == "set_xit":
            handler._validate_ticket(ticket)
            await handler._radio.set_rit_frequency(int(params["hz"]))
        elif name == "set_rit_status":
            handler._validate_ticket(ticket)
            await handler._radio.set_rit_status(bool(params["on"]))
        elif name == "set_rit_tx_status":
            handler._validate_ticket(ticket)
            await handler._radio.set_rit_tx_status(bool(params["on"]))
        elif name == "set_rf_power":
            handler._validate_ticket(ticket)
            await handler._radio.set_rf_power(int(params["native"]))
        elif name == "set_key_speed":
            handler._validate_ticket(ticket)
            await handler._radio.set_key_speed(int(params["speed"]))
        else:
            raise ValueError(f"unsupported TCI command intent: {name!r}")
        return CommandExecutionResult()


class TciAdapterHandler:
    """Per-connection TCI command handler (reads, writes, teardown)."""

    def __init__(
        self,
        *,
        radio: Any,
        state_store: StateStore,
        owner: str,
        managed_tx_authority: Any = None,
        command_queue: Any = None,
        command_service: Any = None,
        controller_authority: Any = None,
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
        if state_model_service is None:
            # The web seat attaches the service to the radio during start,
            # after listeners are built: handlers are minted per connection,
            # so read the radio-carried service here (the rigctld seat does
            # the same through its capability protocol). Duck-typed: the
            # freshness call below re-validates the shape before using it.
            state_model_service = getattr(radio, "state_model_service", None)
        self._radio = radio
        self._store = state_store
        self._owner = owner
        self._authority = managed_tx_authority
        self._queue = command_queue
        self._service = command_service
        self._controller_authority = controller_authority
        self._state_model_service = state_model_service
        self._tracker = ManagedPttTracker()
        # Last adapter-programmed TX tuple (TCI name, numeric DATA, MOD
        # input): the key-down check (spec 5.7) requires the readback to
        # match it. Set on every modulation write and split enable.
        self._tx_required: tuple[str, int, int | None] | None = None
        self._config = config or TciAdapterConfig()
        self._clock = clock
        self._notifier = TciNotifier(
            radio=radio,
            store=state_store,
            managed_present=all(supplied),
            managed_tx_authority=managed_tx_authority,
        )
        self._executor = _TciCommandExecutor(self)
        self._ignored_logged: set[str] = set()
        self._state_model_service_checked = False
        self._audio_settings: TciAudioSettings | None = None
        self._rx_pump: TciRxPump | None = None
        self._tx_session: TciTxSession | None = None
        self._tx_iq = False
        self._tx_outbound: deque[bytes] = deque(maxlen=8)
        self.dropped_tx = 0

    @property
    def owner(self) -> str:
        """The lease-bearing owner id minted for this socket."""
        return self._owner

    @property
    def audio_active(self) -> bool:
        """Whether this client's RX pump is serving audio."""
        return self._rx_pump is not None and self._rx_pump.running

    @property
    def audio_pump(self) -> TciRxPump | None:
        """The live RX pump, if this client started one."""
        return self._rx_pump

    @property
    def tx_session(self) -> TciTxSession | None:
        """The live TX session, if this client holds a ``source=tci`` over."""
        return self._tx_session

    def drain_audio(self) -> list[bytes]:
        """Take every queued RX frame and TX_CHRONO for server delivery."""
        frames: list[bytes] = []
        if self._rx_pump is not None:
            frames.extend(self._rx_pump.drain())
        if self._tx_outbound:
            frames.extend(self._tx_outbound)
            self._tx_outbound.clear()
        return frames

    def _emit_tx_chrono(self, frame: bytes) -> None:
        """Queue one TX_CHRONO marker for the server delivery pump."""
        self._tx_outbound.append(bytes(frame))

    @property
    def notifier(self) -> TciNotifier:
        """The shared projection object (C2 writes re-project through it)."""
        return self._notifier

    # -- leaf contract ---------------------------------------------------

    async def on_connect(self) -> Sequence[str]:
        """Register the session, then return the init burst (spec 5.4).

        Step 6 appends the audio stream settings when this client can
        serve audio (spec 8.1).
        """
        if self._queue is not None:
            self._queue.register_session(self._owner)
        burst = list(await self._notifier.init_burst())
        lines = self._audio_burst_lines()
        if lines:
            ready = burst.pop()
            assert ready == "ready;"
            burst.extend(lines)
            burst.append(ready)
        return burst

    def _audio_burst_lines(self) -> list[str]:
        """Init-burst step 6: stream settings, only with an audio route (8.1)."""
        if not rx_audio_available(self._radio):
            return []
        settings = self._audio_settings_or_seed()
        return [
            f"audio_samplerate:{settings.samplerate};",
            f"audio_stream_sample_type:{settings.sample_type};",
            f"audio_stream_channels:{settings.channels};",
            f"audio_stream_samples:{settings.samples};",
        ]

    def _audio_settings_or_seed(self) -> TciAudioSettings:
        """Per-client settings, seeded from the radio rate on first use."""
        if self._audio_settings is None:
            self._audio_settings = TciAudioSettings.from_radio(self._radio)
        return self._audio_settings

    async def on_binary(self, payload: bytes) -> None:
        """One client TX frame: convert, buffer, play out (spec 8.3).

        With no keyed TX session the frame is dropped and counted: a
        frame never keys the radio.
        """
        session = self._tx_session
        if session is None:
            self.dropped_tx += 1
            return
        if len(payload) < HEADER_SIZE:
            session.dropped_malformed += 1
            return
        try:
            header = unpack_header(bytes(payload[:HEADER_SIZE]))
        except (ValueError, struct.error):
            session.dropped_malformed += 1
            return
        if header.stream_type is not StreamType.TX_AUDIO:
            return
        if header.channels not in (1, 2):
            session.dropped_malformed += 1
            return
        body = bytes(payload[HEADER_SIZE:])
        if not body:
            return
        session.feed_client_frame(
            body,
            channels=header.channels,
            sample_rate=header.sample_rate,
            sample_type=header.sample_type,
        )

    async def on_disconnect(self) -> None:
        """Unregister the session, then release its managed TX lease (7.4).

        Unregistration runs before the awaited disconnect cleanup so no
        queued key can outlive the socket; the managed release is preserved
        on every path. The RX pump stops here too (spec 8.2).
        """
        try:
            if self._queue is not None:
                self._queue.unregister_session(self._owner)
        finally:
            await self._stop_audio()
            await release_managed_session_tx(
                self.release_session_tx if self._authority is not None else None,
                self._owner,
                handback_timeout_s=SESSION_TX_HANDBACK_TIMEOUT_S,
            )

    async def _stop_audio(self) -> None:
        """Close the RX pump and forget it (stop/disconnect, spec 8.2)."""
        pump, self._rx_pump = self._rx_pump, None
        if pump is not None:
            await pump.stop()
        await self._stop_tx_session()

    async def _stop_tx_session(self) -> None:
        """Stop the TX session and release its lease (unkey/disconnect, 8.3)."""
        session, self._tx_session = self._tx_session, None
        if session is None:
            return
        try:
            await session.stop()
        except Exception:
            logger.debug("tci %s: tx session stop failed", self._owner, exc_info=True)
        lease = session.lease
        if lease is not None:
            try:
                await lease.release()
            except Exception:
                logger.debug(
                    "tci %s: tx lease release failed", self._owner, exc_info=True
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
        if name == "vfo":
            if len(args) >= 3:
                return await self._write_vfo(args)
            return await self._read_vfo(args)
        if name == "modulation":
            if len(args) >= 2:
                return await self._write_modulation(args)
            return await self._read_modulation()
        if name == "split_enable":
            if len(args) >= 2:
                return await self._write_split(args)
            return await self._read_split()
        if name == "trx":
            if len(args) >= 2:
                return await self._write_trx(args)
            return await self._read_trx()
        if name in ("drive", "tune_drive"):
            if name == "drive" and len(args) >= 2:
                return await self._write_drive(args)
            if len(args) >= 2:
                # ``tune_drive`` sets are ignored (spec 6.4).
                return await self._sender_current(self._read_drive(name))
            return await self._read_drive(name)
        if name == "tune":
            # Not honoured (spec 6.4): always "not tuning", to the requester.
            return CommandOutcome(to_sender=("tune:0,false;",))
        if name == "audio_samplerate":
            return await self._audio_samplerate(args)
        if name == "audio_stream_sample_type":
            return await self._audio_sample_type(args)
        if name == "audio_stream_channels":
            return await self._audio_channels(args)
        if name == "audio_stream_samples":
            return await self._audio_samples(args)
        if name == "tx_stream_audio_buffering":
            return await self._audio_buffering(args)
        if name == "audio_start":
            return await self._audio_start()
        if name == "audio_stop":
            return await self._audio_stop()
        if name in ("rit_enable", "xit_enable"):
            if len(args) >= 2:
                return await self._write_rit_enable(name, args)
            return await self._read_rit_enable(name)
        if name in ("rit_offset", "xit_offset"):
            if len(args) >= 2:
                return await self._write_rit_offset(name, args)
            return await self._read_rit_offset(name)
        if name == "tx_enable":
            # Server only (spec 5.5): a client set is answered with current.
            if len(args) >= 2:
                return await self._sender_current(self._read_tx_enable())
            return await self._read_tx_enable()
        if name == "tx_frequency":
            if len(args) >= 2:
                return await self._sender_current(self._read_tx_frequency())
            return await self._read_tx_frequency()
        if name in ("dds", "if"):
            return await self._read_dds_if()
        if name in ("cw_keyer_speed", "cw_macros_speed"):
            if len(args) >= 1:
                return await self._write_key_speed(name, args)
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
        return CommandOutcome()

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
            ensure(
                READ_PATHS,
                max_age=self._config.cache_ttl,
                priority="user",
                reason=reason,
            )
        except Exception:
            logger.debug("tci %s: ensure_fresh failed", self._owner, exc_info=True)

    async def _state(self) -> Any:
        self._ensure_fresh("tci.read")
        state = await project_state(
            self._radio,
            self._store,
            managed_keyed=authority_keyed(self._authority),
            fallback_modulation=self._notifier.modulation,
        )
        # Reads share the projection path: retain what was announced so a
        # later unmapped mode falls back to it (never to a stale value).
        self._notifier.note_modulation(state.modulation)
        return state

    def _capture_ticket(self) -> Any:
        """Capture the controller ticket for one write (rigctld parity).

        Raises :class:`ControllerError` when the seat is held elsewhere:
        the caller refuses the write with the actual state and performs
        no radio effect. The explicit remote-mode check mirrors the key
        path, so doubles without a ``capture`` method are gated exactly
        like the real authority (whose ``capture`` refuses there too).
        """
        controller = self._controller_authority
        if controller is None:
            return None
        if getattr(controller, "mode", "local") == "remote":
            raise ControllerError("controller holds the remote seat")
        capture = getattr(controller, "capture", None)
        if not callable(capture):
            return None
        return capture()

    @staticmethod
    def _validate_ticket(ticket: Any) -> None:
        """Re-validate a captured ticket immediately before the effect."""
        if ticket is not None:
            ticket.authority.validate(ticket)

    async def _execute_write(self, intent: Any) -> None:
        """Run one write intent through the shared command pipeline.

        With the composed service present every intent goes through
        :meth:`CommandService.execute` with the adapter executor (pending
        overlays, lifecycle, invalidation — exactly like the rigctld
        seat); in seat-less tests the same executor runs directly, so the
        controller gate holds on every path.
        """
        service = self._service
        execute = getattr(service, "execute", None)
        if callable(execute):
            try:
                await execute(intent, executor=self._executor)
            except CommandExecutionInvalidatedError as exc:
                raise _TciWriteError(f"write invalidated: {exc}") from exc
            return
        await self._executor.execute(intent)

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
        return self._reply(f"{name}:0,{state.drive};")

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

    # -- writes ----------------------------------------------------------

    async def _sender_current(self, read: Any) -> CommandOutcome:
        """Answer the actual state to the requester only (no-op/failure)."""
        outcome = await read
        return CommandOutcome(to_sender=outcome.to_sender)

    # -- audio phase (spec 8.1/8.2) --------------------------------------

    def _audio_ignored(self) -> CommandOutcome | None:
        """``None`` when audio serves, else the ignored no-reply outcome."""
        if rx_audio_available(self._radio):
            return None
        return CommandOutcome()

    @staticmethod
    def _sender_only(line: str) -> CommandOutcome:
        """Audio settings are per-client: answered to the requester only."""
        return CommandOutcome(to_sender=(line,))

    async def _audio_samplerate(self, args: tuple[str, ...]) -> CommandOutcome:
        if self._audio_ignored() is not None:
            return CommandOutcome()
        settings = self._audio_settings_or_seed()
        if len(args) >= 2:
            rate = parse_int(args[1])
            if rate is None:
                return self._sender_only(f"audio_samplerate:{settings.samplerate};")
            settings.set_samplerate(rate)
            self._apply_audio_settings()
        return self._sender_only(f"audio_samplerate:{settings.samplerate};")

    async def _audio_sample_type(self, args: tuple[str, ...]) -> CommandOutcome:
        if self._audio_ignored() is not None:
            return CommandOutcome()
        settings = self._audio_settings_or_seed()
        if len(args) >= 2:
            settings.set_sample_type(args[1])
            self._apply_audio_settings()
        return self._sender_only(f"audio_stream_sample_type:{settings.sample_type};")

    async def _audio_channels(self, args: tuple[str, ...]) -> CommandOutcome:
        if self._audio_ignored() is not None:
            return CommandOutcome()
        settings = self._audio_settings_or_seed()
        if len(args) >= 2:
            try:
                settings.set_channels(int(args[1]))
            except ValueError:
                pass
            self._apply_audio_settings()
        return self._sender_only(f"audio_stream_channels:{settings.channels};")

    async def _audio_samples(self, args: tuple[str, ...]) -> CommandOutcome:
        if self._audio_ignored() is not None:
            return CommandOutcome()
        settings = self._audio_settings_or_seed()
        if len(args) >= 2:
            count = parse_int(args[1])
            if count is None:
                return self._sender_only(f"audio_stream_samples:{settings.samples};")
            settings.set_samples(count)
            self._apply_audio_settings()
        return self._sender_only(f"audio_stream_samples:{settings.samples};")

    async def _audio_buffering(self, args: tuple[str, ...]) -> CommandOutcome:
        if self._audio_ignored() is not None:
            return CommandOutcome()
        settings = self._audio_settings_or_seed()
        if len(args) >= 2:
            millis = parse_int(args[1])
            if millis is not None:
                settings.set_buffering(millis)
        return self._sender_only(f"tx_stream_audio_buffering:{settings.buffering_ms};")

    async def _audio_start(self) -> CommandOutcome:
        if self._audio_ignored() is not None:
            return CommandOutcome()
        settings = self._audio_settings_or_seed()
        if self._rx_pump is None:
            pump = TciRxPump(self._radio, self._owner, settings=settings)
            try:
                await pump.start()
            except Exception:
                logger.debug("tci %s: audio start failed", self._owner, exc_info=True)
                return CommandOutcome()
            self._rx_pump = pump
        return self._sender_only("audio_start:0;")

    async def _audio_stop(self) -> CommandOutcome:
        if self._audio_ignored() is not None:
            return CommandOutcome()
        await self._stop_audio()
        return self._sender_only("audio_stop:0;")

    def _apply_audio_settings(self) -> None:
        """Push changed settings into the live pump, if one serves."""
        if self._rx_pump is not None:
            self._rx_pump.reconfigure(self._audio_settings_or_seed())

    def _refuse_key(self) -> CommandOutcome:
        """Refuse a key: ``trx:0,false;`` to the requester only (spec 5.5).

        A refused PTT is never broadcast: other clients keep their own
        state, and the notifier (not the refusal) reports real changes.
        """
        return CommandOutcome(to_sender=("trx:0,false;",))

    async def _write_trx(self, args: tuple[str, ...]) -> CommandOutcome:
        """Key or unkey through the shared managed ingress (spec 7.3)."""
        want = parse_bool(args[1])
        if want is None:
            # Unparsable set degrades to a read (spec 6.1 rule 2).
            return await self._sender_current(self._read_trx())
        if not want:
            return await self._unkey()
        return await self._key(args[2] if len(args) >= 3 else None)

    async def _key(self, source: str | None) -> CommandOutcome:
        """Key the current input via the managed authority (spec 7.3)."""
        if source == "tci":
            return await self._key_tci()
        if source is not None:
            # Any other explicit source is refused. Accepting it would
            # key whatever input the data route selected, which is not
            # what the client asked for.
            logger.info(
                "tci %s: refusing key with explicit source %s",
                self._owner,
                source,
            )
            return self._refuse_key()
        if self._authority is None or self._queue is None or self._service is None:
            # No managed composition: legacy ``set_ptt`` fallback does
            # not exist for TCI (spec 7.2).
            return self._refuse_key()
        controller = self._controller_authority
        if controller is not None and getattr(controller, "mode", "local") == "remote":
            return self._refuse_key()
        try:
            if self._authority.held_by_other(self._owner):
                return self._refuse_key()
            if self._authority.radio_tx_refusal_nowait() is not None:
                return self._refuse_key()
        except Exception:
            logger.debug(
                "tci %s: TX admission probe failed", self._owner, exc_info=True
            )
            return self._refuse_key()
        loop = asyncio.get_running_loop()
        try:
            # The tuple check runs INSIDE the managed admission (as
            # ``before_on``): after the intent commits to PTT(owner) and
            # before PTT_ON dispatches. A mode/DATA change queued between
            # an early look and admission is therefore refused with no
            # PTT write, instead of keying a stale tuple.
            submission, ready = self._tracker.begin_key(
                self._authority,
                owner=self._owner,
                on=True,
                expires_at_monotonic=loop.time() + self._config.command_timeout,
                connection_generation=self._queue.capture_connection_generation(),
                before_on=self._check_tx_tuple,
            )
            await execute_managed_ptt(
                authority=self._authority,
                queue=self._queue,
                owner=self._owner,
                on=True,
                submission=submission,
                ready=ready,
                source="tci",
                provider_generation=self._store.provider_generation,
            )
        except Exception:
            # Rejected, failed setup, radio refusal, timeout: all are
            # ``trx:0,false;`` to the requester (spec 7.3 step 3). Only an
            # external cancellation propagates (never swallowed: it is a
            # ``BaseException``, and the disconnect path releases the
            # lease explicitly).
            logger.debug("tci %s: managed key failed", self._owner, exc_info=True)
            return self._refuse_key()
        return self._reply("trx:0,true;")

    async def _key_tci(self) -> CommandOutcome:
        """Key with TX audio over TCI (spec 7.3/8.3, fail-closed).

        The key is accepted only with a PCM TX route; the TX input is
        established and verified inside the authority's admitted key
        (``before_on``), never before it. After the key is accepted the
        TX lease is acquired and the chrono/playout session starts; it
        is released at unkey/disconnect.
        """
        if tx_route_ready(self._radio) is None:
            logger.info("tci %s: refusing tci key with no PCM TX route", self._owner)
            return self._refuse_key()
        if self._authority is None or self._queue is None or self._service is None:
            return self._refuse_key()
        controller = self._controller_authority
        if controller is not None and getattr(controller, "mode", "local") == "remote":
            return self._refuse_key()
        try:
            if self._authority.held_by_other(self._owner):
                return self._refuse_key()
            if self._authority.radio_tx_refusal_nowait() is not None:
                return self._refuse_key()
        except Exception:
            logger.debug(
                "tci %s: TX admission probe failed", self._owner, exc_info=True
            )
            return self._refuse_key()
        loop = asyncio.get_running_loop()
        try:
            submission, ready = self._tracker.begin_key(
                self._authority,
                owner=self._owner,
                on=True,
                expires_at_monotonic=loop.time() + self._config.command_timeout,
                connection_generation=self._queue.capture_connection_generation(),
                before_on=self._run_before_on,
            )
            await execute_managed_ptt(
                authority=self._authority,
                queue=self._queue,
                owner=self._owner,
                on=True,
                submission=submission,
                ready=ready,
                source="tci",
                provider_generation=self._store.provider_generation,
            )
        except Exception:
            logger.debug("tci %s: managed tci key failed", self._owner, exc_info=True)
            return self._refuse_key()
        try:
            lease = await self._radio.audio_session.acquire_tx(self._owner)
        except Exception:
            logger.debug("tci %s: tx lease acquire failed", self._owner, exc_info=True)
            await self._best_effort_unkey()
            return self._refuse_key()
        session = TciTxSession(
            self._radio,
            self._owner,
            self._authority,
            lease,
            settings=self._audio_settings_or_seed(),
            iq=self._tx_iq,
            emit=self._emit_tx_chrono,
        )
        try:
            await session.start()
        except Exception:
            logger.debug("tci %s: tx session start failed", self._owner, exc_info=True)
            try:
                await lease.release()
            except Exception:
                logger.debug(
                    "tci %s: tx lease release failed", self._owner, exc_info=True
                )
            await self._best_effort_unkey()
            return self._refuse_key()
        self._tx_session = session
        return self._reply("trx:0,true;")

    async def _best_effort_unkey(self) -> None:
        """Release our key when TX setup failed after the PTT went out."""
        try:
            await self._authority.submit_ptt(False, self._owner)
        except Exception:
            logger.debug("tci %s: best-effort unkey failed", self._owner, exc_info=True)

    async def _run_before_on(self) -> bool:
        """Authority ``before_on`` hook: route prep inside the admitted key."""
        try:
            return await self._prepare_tci_route()
        except Exception:
            logger.debug(
                "tci %s: tx route preparation failed", self._owner, exc_info=True
            )
            return False

    async def _prepare_tci_route(self) -> bool:
        """Establish and verify the TX data route (spec 8.3 step 1/7.3a).

        Applies the WSJT-X data policy for the current mode (required
        DATA state and MOD input source) and reads both back; the key is
        refused when the mode has no data route, the radio cannot take
        DATA, or the readback differs. Direct radio calls, like the
        modulation path they share the glue with.
        """
        state = await self._state()
        if state.modulation not in ("USB", "LSB", "AM", "FM", "DIGU", "DIGL"):
            return False
        if not supports_data_mode(self._radio):
            return False
        route = resolve_audio_route(self._radio)
        data_mode, mod_input = rigctld_wsjtx_policy(route)
        count = int(profile_data_mode_count(self._radio))
        if (
            isinstance(data_mode, int)
            and not isinstance(data_mode, bool)
            and data_mode <= count
        ):
            value: int | bool = data_mode
        else:
            value = True
        await apply_wsjtx_data_mod_input(self._radio, data_mode=value, source=mod_input)
        setter = getattr(self._radio, "set_data_mode", None)
        if not callable(setter):
            return False
        await setter(value)
        reader = getattr(self._radio, "get_data_mode", None)
        if not callable(reader):
            return False
        try:
            if int(await reader()) != int(value):
                return False
        except (TypeError, ValueError):
            return False
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and value > 0
            and mod_input is not None
        ):
            getter = getattr(self._radio, f"get_data{int(value)}_mod_input", None)
            if callable(getter):
                try:
                    actual_input = await getter()
                except Exception:
                    return False
                try:
                    if int(actual_input) != int(mod_input):
                        return False
                except (TypeError, ValueError):
                    return False
        self._tx_iq = True
        return True

    async def _unkey(self) -> CommandOutcome:
        """Release our lease, if any; always answered (spec 7.3)."""
        if self._authority is None or self._queue is None:
            return await self._sender_current(self._read_trx())
        loop = asyncio.get_running_loop()
        try:
            # Only a key-down captures the connection generation: an
            # unkey never touches the queue's ordering gate, so teardown
            # keeps working after the consumer unbinds (MOR-3821).
            submission, ready = self._tracker.begin_key(
                self._authority,
                owner=self._owner,
                on=False,
                expires_at_monotonic=loop.time() + self._config.command_timeout,
                connection_generation=None,
            )
            await execute_managed_ptt(
                authority=self._authority,
                queue=self._queue,
                owner=self._owner,
                on=False,
                submission=submission,
                ready=ready,
                source="tci",
                provider_generation=self._store.provider_generation,
            )
        except Exception:
            logger.debug("tci %s: managed unkey failed", self._owner, exc_info=True)
            return await self._sender_current(self._read_trx())
        await self._stop_tx_session()
        return self._reply("trx:0,false;")

    async def _check_tx_tuple(self) -> bool:
        """Admission-time check (spec 5.7): observed TX tuple matches.

        Runs inside the managed admission (as ``before_on``), on a fresh
        read-back of the RAW TX-role tuple (native mode, numeric DATA,
        MOD input). Missing evidence (no published mode/DATA), an
        unmapped raw mode (e.g. RTTY), a moved tuple or MOD input, or a
        failed readback refuses the key with no PTT write. With no prior
        programming (bare ``trx:0,true``) the current input keys when the
        radio is correctly configured and the authority admits it: there
        is no per-socket programming prerequisite.
        """
        required = self._tx_required
        if required is None:
            return True
        name, data, mod_input = required
        try:
            state = await self._state()
            actual_name, actual_data = tx_role_tci(self._store, split=state.split)
        except Exception:
            logger.debug("tci %s: TX tuple readback failed", self._owner, exc_info=True)
            return False
        if actual_name is None or actual_data is None:
            return False
        if actual_name != name or _data_num(actual_data) != _data_num(data):
            return False
        if (
            isinstance(data, int)
            and not isinstance(data, bool)
            and data > 0
            and mod_input is not None
        ):
            getter = getattr(self._radio, f"get_data{int(data)}_mod_input", None)
            if not callable(getter):
                return False
            try:
                actual_input = await getter()
            except Exception:
                return False
            try:
                if int(actual_input) != int(mod_input):
                    return False
            except (TypeError, ValueError):
                return False
        return True

    def _intent(self, name: str, params: Mapping[str, Any], **kwargs: Any) -> Any:
        """Build the ``source="tci"`` intent (validation + normalization)."""
        return command_intent_from_request(
            name,
            dict(params),
            source="tci",
            command_id=f"tci-{name}-{time.monotonic_ns()}",
            session_id=self._owner,
            timeout=self._config.command_timeout,
            **kwargs,
        )

    async def _write_vfo(self, args: tuple[str, ...]) -> CommandOutcome:
        try:
            channel = int(args[1])
        except ValueError:
            return await self._sender_current(self._read_vfo(args))
        hz = parse_int(args[2])
        if channel not in (0, 1) or hz is None or hz <= 0:
            return await self._sender_current(self._read_vfo(args[:2]))
        if channel == 0:
            try:
                intent = self._intent("set_freq", {"freq": hz, "receiver": 0})
                await self._execute_write(intent)
            except Exception:
                logger.debug("tci %s: set_freq failed", self._owner, exc_info=True)
                return await self._sender_current(self._read_vfo(args[:2]))
        else:
            try:
                intent = self._intent("set_split_freq", {"freq_hz": hz})
                await self._execute_write(intent)
            except Exception:
                logger.debug(
                    "tci %s: set_split_freq failed", self._owner, exc_info=True
                )
                return await self._sender_current(self._read_vfo(args[:2]))
        actual = await self._confirmed_vfo_freq(channel, hz)
        return self._reply(f"vfo:0,{channel},{actual};")

    async def _confirmed_vfo_freq(self, channel: int, hz: int) -> int:
        """Confirm a VFO write from Core's state, never the request (MOR-3821).

        The store lands the radio's report after the executor returns (the
        write confirmation is queued, never synchronous), so the first read
        can still carry the pre-write value. Re-project through the read
        path (``_state``, the same projection the reads use) until it shows
        the requested frequency or ``command_timeout`` expires, then answer
        the actual frequency either way: when the radio kept another
        frequency the client re-syncs to the shared truth.
        """
        deadline = self._clock() + self._config.command_timeout
        while True:
            state = await self._state()
            actual: int = state.freq_a if channel == 0 else state.freq_b
            if actual == hz or self._clock() >= deadline:
                return actual
            # Pacing only: the deadline above decides the verdict, not this
            # pause (the rigctld seat paces its DATA readback the same way).
            await asyncio.sleep(0.05)

    async def _queued_split_freq(self, intent: Any, ticket: Any) -> None:
        """Channel-1 write: queued when managed, direct otherwise (rigctld)."""
        if self._authority is not None and self._queue is not None:
            loop = asyncio.get_running_loop()
            completion: asyncio.Future[None] = loop.create_future()
            timeout = self._config.command_timeout
            try:
                self._queue.put_ordered(
                    intent,
                    future=completion,
                    command_id=intent.id,
                    source=intent.source,
                    session_id=self._owner,
                    expires_at_monotonic=loop.time() + timeout,
                    provider_generation=self._store.provider_generation,
                    connection_generation=(self._queue.capture_connection_generation()),
                )
                await asyncio.wait_for(completion, timeout=timeout)
            except (TimeoutError, asyncio.TimeoutError) as exc:
                raise _TciWriteError("split write timed out") from exc
            except (CommandRejectedError, ControllerError, CommandError) as exc:
                raise _TciWriteError(f"split write refused: {exc}") from exc
            finally:
                if not completion.done():
                    completion.cancel()
                await asyncio.gather(completion, return_exceptions=True)
            return
        if not isinstance(self._radio, RelativeVfoFrequencyWritable):
            raise _TciWriteError("no relative-VFO surface")
        try:
            await self._radio.write_relative_vfo_frequency(
                int(intent.params["freq_hz"]),
                selected=False,
                before_set=lambda: self._validate_ticket(ticket),
            )
        except (CommandRejectedError, ControllerError, CommandError) as exc:
            raise _TciWriteError(f"split write refused: {exc}") from exc

    async def _write_modulation(self, args: tuple[str, ...]) -> CommandOutcome:
        name = args[1].strip().upper()
        # Outside the announced list: ignored per 6.1 (never half-applied).
        if name not in announced_modulations(self._radio):
            return await self._sender_current(self._read_modulation())
        mode, data_on = TCI_TO_CORE_MODE[name]
        try:
            intent = self._intent("set_mode", {"mode": mode.name, "receiver": 0})
            await self._execute_write(intent)
            ticket = self._capture_ticket()
            if data_on:
                data_value = await self._write_data_on(ticket)
            else:
                data_value = False
                if mode.name in ("USB", "LSB"):
                    await self._write_data_off(ticket)
            await self._sync_split_mode(int(mode.value), data_value, ticket)
            _, policy_input = rigctld_wsjtx_policy(resolve_audio_route(self._radio))
            self._tx_required = (name, data_value, policy_input)
            self._notifier.note_modulation(name)
        except Exception:
            logger.debug("tci %s: set_mode failed", self._owner, exc_info=True)
            return await self._sender_current(self._read_modulation())
        return self._reply(f"modulation:0,{name};")

    async def _write_data_on(self, ticket: Any = None) -> int | bool:
        """Apply the WSJT-X DATA route shared with the rigctld seat (P0d)."""
        route = resolve_audio_route(self._radio)
        data_mode, mod_input = rigctld_wsjtx_policy(route)
        count = int(profile_data_mode_count(self._radio))
        if (
            isinstance(data_mode, int)
            and not isinstance(data_mode, bool)
            and data_mode <= count
        ):
            value: int | bool = data_mode
        else:
            value = True
        if not supports_data_mode(self._radio):
            raise _TciWriteError("no DATA-mode command")
        self._validate_ticket(ticket)
        await apply_wsjtx_data_mod_input(self._radio, data_mode=value, source=mod_input)
        setter = getattr(self._radio, "set_data_mode", None)
        if not callable(setter):
            raise _TciWriteError("no DATA-mode command")
        self._validate_ticket(ticket)
        await setter(value)
        return value

    async def _write_data_off(self, ticket: Any = None) -> None:
        """Clear DATA for a plain USB/LSB set, verified like rigctld's."""
        if not supports_data_mode(self._radio):
            return
        setter = getattr(self._radio, "set_data_mode", None)
        if not callable(setter):
            return
        self._validate_ticket(ticket)
        await setter(False)
        reader = getattr(self._radio, "get_data_mode", None)
        if callable(reader) and await reader():
            raise _TciWriteError("DATA still on after clear")

    async def _sync_split_mode(
        self, mode_civ: int, data: int | bool, ticket: Any = None
    ) -> None:
        """Keep B's mode tuple equal to A's (spec 5.7).

        Direct radio call, never queued: the ``set_split_mode`` intent
        binding rejects numeric DATA (``ValueError: data must be a boolean
        or None``), so the queued intent cannot carry the sub-mode P0e
        added. The method itself is that widened surface. The controller
        ticket travels as ``before_set`` so the transport's final currency
        check enforces it too.
        """
        if not isinstance(self._radio, RelativeVfoModeWritable):
            return
        await self._radio.write_relative_vfo_mode(
            mode_civ,
            selected=False,
            data=data,
            before_set=lambda: self._validate_ticket(ticket),
        )

    async def _write_split(self, args: tuple[str, ...]) -> CommandOutcome:
        on = parse_bool(args[1])
        if on is None:
            return await self._sender_current(self._read_split())
        if not isinstance(self._radio, SplitCapable):
            return await self._sender_current(self._read_split())
        try:
            intent = self._intent("set_split", {"on": on})
            await self._execute_write(intent)
            if on:
                ticket = self._capture_ticket()
                await self._sync_split_on(ticket)
                split_state = await self._state()
                _, split_input = rigctld_wsjtx_policy(resolve_audio_route(self._radio))
                self._tx_required = (
                    split_state.modulation,
                    split_state.data_a,
                    split_input,
                )
        except Exception:
            logger.debug("tci %s: set_split failed", self._owner, exc_info=True)
            return await self._sender_current(self._read_split())
        return self._reply(f"split_enable:0,{_tf(on)};")

    async def _sync_split_on(self, ticket: Any = None) -> None:
        """On ``split_enable:0,true``, program B with A's tuple (spec 5.7)."""
        state = await self._state()
        entry = TCI_TO_CORE_MODE.get(state.modulation)
        if entry is None:
            return
        mode, data_on = entry
        await self._sync_split_mode(
            int(mode.value), state.data_a if data_on else False, ticket
        )

    async def _write_rit_enable(
        self, name: str, args: tuple[str, ...]
    ) -> CommandOutcome:
        on = parse_bool(args[1])
        if on is None:
            return await self._sender_current(self._read_rit_enable(name))
        key = "rit_enable" if name == "rit_enable" else "xit_enable"
        if not isinstance(self._radio, RitXitCapable):
            return await self._sender_current(self._read_rit_enable(name))
        try:
            if name == "rit_enable":
                intent = self._intent("set_rit_status", {"on": on})
            else:
                intent = self._intent("set_rit_tx_status", {"on": on})
            await self._execute_write(intent)
        except Exception:
            logger.debug("tci %s: rit status failed", self._owner, exc_info=True)
            return await self._sender_current(self._read_rit_enable(name))
        return self._reply(f"{key}:0,{_tf(on)};")

    async def _write_rit_offset(
        self, name: str, args: tuple[str, ...]
    ) -> CommandOutcome:
        hz = parse_int(args[1])
        if hz is None:
            return await self._sender_current(self._read_rit_offset(name))
        key = "rit_offset" if name == "rit_offset" else "xit_offset"
        if not isinstance(self._radio, RitXitCapable):
            return await self._sender_current(self._read_rit_offset(name))
        try:
            # Offset writes change ONLY the offset (spec 5.5): TCI exposes
            # separate enable commands, so no enable state is touched here.
            if name == "rit_offset":
                intent = self._intent("set_rit", {"hz": hz})
            else:
                intent = self._intent("set_xit", {"hz": hz})
            await self._execute_write(intent)
        except Exception:
            logger.debug("tci %s: rit offset failed", self._owner, exc_info=True)
            return await self._sender_current(self._read_rit_offset(name))
        return self._reply(f"{key}:0,{hz};")

    async def _write_drive(self, args: tuple[str, ...]) -> CommandOutcome:
        pct = parse_int(args[1])
        if pct is None or not 0 <= pct <= 100:
            # Out-of-domain input degrades to a read (spec 6.1 rule 2).
            return await self._sender_current(self._read_drive("drive"))
        setter = getattr(self._radio, "set_rf_power", None)
        if not callable(setter):
            return await self._sender_current(self._read_drive("drive"))
        try:
            radio_profile = getattr(self._radio, "profile", None)
            native, _normalized = resolve_power_level_target(
                pct / 100.0,
                power_native_unit=getattr(self._radio, "native_power_unit", "raw_255"),
                power_max_watts=getattr(radio_profile, "max_watts", None),
                power_min_watts=getattr(radio_profile, "min_watts", None),
            )
            intent = self._intent(
                "set_rf_power",
                {"value": pct / 100.0, "native": int(native)},
                power_native_unit=getattr(self._radio, "native_power_unit", "raw_255"),
                power_max_watts=getattr(radio_profile, "max_watts", None),
                power_min_watts=getattr(radio_profile, "min_watts", None),
            )
            await self._execute_write(intent)
        except Exception:
            logger.debug("tci %s: set_rf_power failed", self._owner, exc_info=True)
            return await self._sender_current(self._read_drive("drive"))
        actual = drive_percent(int(native), self._radio)
        return self._reply(f"drive:0,{actual};")

    async def _write_key_speed(
        self, name: str, args: tuple[str, ...]
    ) -> CommandOutcome:
        speed = parse_int(args[0])
        if speed is None or speed <= 0:
            return await self._sender_current(self._read_key_speed(name))
        setter = getattr(self._radio, "set_key_speed", None)
        if not callable(setter):
            return await self._sender_current(self._read_key_speed(name))
        try:
            intent = self._intent("set_key_speed", {"speed": speed})
            await self._execute_write(intent)
        except Exception:
            logger.debug("tci %s: set_key_speed failed", self._owner, exc_info=True)
            return await self._sender_current(self._read_key_speed(name))
        return self._reply(f"{name}:{speed};")


def _tf(value: bool) -> str:
    return "true" if value else "false"


def _data_num(value: Any) -> int:
    """Normalize a DATA sub-mode to an int (plain on/off count as 1/0)."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
