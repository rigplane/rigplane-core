"""Abstract Radio Protocol — multi-backend radio control interface.

Defines :class:`Radio` (core) and optional capability Protocols
(:class:`AudioCapable`, :class:`ScopeCapable`, :class:`DualReceiverCapable`)
so that the Web UI, rigctld server, and CLI can work with **any** radio
backend — Icom LAN, Icom serial, Yaesu CAT, etc.

Quick start::

    from icom_lan.radio_protocol import Radio, AudioCapable

    async def tune(radio: Radio) -> None:
        await radio.set_freq(14_074_000)
        mode, filt = await radio.get_mode()
        print(f"Mode: {mode}, filter: {filt}")

    # Check optional capabilities at runtime:
    if isinstance(radio, AudioCapable):
        await radio.start_audio_rx_opus(callback)

Architecture::

    ┌──────────────────────────────────────────────┐
    │          Web UI  /  rigctld  /  CLI           │
    ├──────────────────────────────────────────────┤
    │          Radio Protocol (core)                │
    │  ┌──────────────┬─────────────┬────────────┐ │
    │  │ AudioCapable │ ScopeCapable│ DualRxCap. │ │
    │  └──────────────┴─────────────┴────────────┘ │
    ├────────┬──────────┬──────────┬───────────────┤
    │IcomLAN │IcomSerial│ YaesuCAT │  Future...    │
    └────────┴──────────┴──────────┴───────────────┘

Standard mode names (cross-vendor):
    USB, LSB, CW, CWR, AM, FM, RTTY, RTTYR, PSK, PSKR, DV, DD

Standard capability tags:
    audio, scope, dual_rx, meters, tx, cw,
    attenuator, preamp, rf_gain, af_level, squelch, nb, nr
"""

from __future__ import annotations

from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Literal,
    Protocol,
    runtime_checkable,
)

from .radio_state import RadioState, VfoSlotState
from .types import AudioCodec, BreakInMode, Mode

if TYPE_CHECKING:
    from ._state_cache import StateCache
    from icom_lan.audio_bus import AudioBus

    # ``_FallbackRigState`` actually lives in ``rigctld.handler`` and is
    # re-exported through ``rigctld.routing``'s TYPE_CHECKING namespace; a
    # single import edge keeps the import-linter exemption count to one
    # per contract.
    from icom_lan.rigctld.routing import (  # type: ignore[attr-defined]  # noqa: TID251
        RigctldRouting,
        _FallbackRigState,
    )
    from icom_lan.runtime._poller_types import CommandQueue
    from icom_lan.scope import ScopeFrame
    from .types import BandStackRegister, MemoryChannel, ScopeFixedEdge

__all__ = [
    "Radio",
    "VfoSlotState",
    "AudioCapable",
    "CivCommandCapable",
    "ModeInfoCapable",
    "ScopeCapable",
    "DualReceiverCapable",
    "ReceiverBankCapable",
    "TransceiverBankCapable",
    "VfoSlotCapable",
    "StateCacheCapable",
    "RecoverableConnection",
    "AdvancedControlCapable",
    "DspControlCapable",
    "AntennaControlCapable",
    "CwControlCapable",
    "VoiceControlCapable",
    "SystemControlCapable",
    "RepeaterControlCapable",
    "LevelsCapable",
    "MetersCapable",
    "PowerControlCapable",
    "RigctldRoutable",
    "SplitCapable",
    "StateNotifyCapable",
    "StatePollable",
    "StatePoller",
    "RitXitCapable",
    "TransceiverStatusCapable",
    "MemoryCapable",
]


# ---------------------------------------------------------------------------
# Core Protocol — every backend MUST implement this
# ---------------------------------------------------------------------------


@runtime_checkable
class Radio(Protocol):
    """Core interface for a controllable radio transceiver.

    Every radio backend must implement this protocol.  Optional features
    (audio streaming, spectrum scope, dual receivers) are expressed as
    separate protocols that a backend may additionally implement.
    """

    # -- Lifecycle ---------------------------------------------------------

    async def connect(self) -> None:
        """Establish connection to the radio."""
        ...

    async def disconnect(self) -> None:
        """Disconnect from the radio, releasing all resources."""
        ...

    async def __aenter__(self) -> "Radio": ...
    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None: ...

    @property
    def connected(self) -> bool:
        """Whether the radio is currently connected and healthy."""
        ...

    @property
    def radio_ready(self) -> bool:
        """Whether the backend considers the CI-V stream ready for clients."""
        ...

    # -- Frequency ---------------------------------------------------------

    async def get_freq(self, receiver: int = 0) -> int:
        """Get the current frequency in Hz.

        Args:
            receiver: 0 = main (default), 1 = sub.
        """
        ...

    async def set_freq(self, freq: int, receiver: int = 0) -> None:
        """Set the frequency in Hz.

        Args:
            freq: Frequency in Hz (e.g. 14_074_000).
            receiver: 0 = main (default), 1 = sub.
        """
        ...

    # -- Mode --------------------------------------------------------------

    async def get_mode(self, receiver: int = 0) -> tuple[str, int | None]:
        """Get the current operating mode.

        Returns:
            Tuple of (mode_name, filter_number_or_None).
            Mode names are standardised strings: USB, LSB, CW, AM, FM, etc.
        """
        ...

    async def set_mode(
        self,
        mode: str,
        filter_width: int | None = None,
        receiver: int = 0,
    ) -> None:
        """Set the operating mode.

        Args:
            mode: Mode name string (e.g. "USB", "CW", "FT8").
            filter_width: Optional filter number (1-3 on Icom, varies by vendor).
            receiver: 0 = main, 1 = sub.
        """
        ...

    async def get_data_mode(self) -> bool:
        """Whether DATA mode is active (e.g. USB-D for FT8/FT4)."""
        ...

    async def set_data_mode(self, on: int | bool, receiver: int = 0) -> None:
        """Set receiver DATA mode.

        Args:
            on: False/0 to disable, True/1 to enable DATA1, or an explicit
                DATA mode value 0-3 when the radio supports multiple DATA
                sub-modes.
            receiver: 0 = main, 1 = sub.
        """
        ...

    # -- TX ----------------------------------------------------------------

    async def set_ptt(self, on: bool) -> None:
        """Key or unkey the transmitter."""
        ...

    # -- State -------------------------------------------------------------

    @property
    def radio_state(self) -> RadioState:
        """Live radio state (freq, mode, meters for all receivers)."""
        ...

    @property
    def model(self) -> str:
        """Human-readable radio model name (e.g. 'IC-7610', 'FTX-1')."""
        ...

    @property
    def backend_id(self) -> str:
        """Stable string identifier for the backend family.

        Consumers (web server, rigctld) use this to route backend-specific
        logic without importing concrete backend classes.

        Known values (family-level, not per-model):
          - ``"icom_lan"``    — Icom LAN/CI-V-over-Ethernet (IC-7610, IC-9700, …)
          - ``"icom_serial"`` — Icom serial CI-V (IC-7300, IC-705, …)
          - ``"yaesu_cat"``   — Yaesu CAT (FTX-1, …)
        """
        ...

    @property
    def capabilities(self) -> set[str]:
        """Set of capability tags this radio supports.

        Standard tags: audio, scope, dual_rx, meters, tx, cw,
        attenuator, preamp, rf_gain, af_level, squelch, nb, nr.
        """
        ...

    def supports_command(self, command: str) -> bool:
        """Check if this radio supports a specific command.

        For TOML-profile-driven backends (Yaesu), checks the command map.
        For hardcoded backends (Icom LAN), returns True for known commands.
        """
        ...


# ---------------------------------------------------------------------------
# Optional capability Protocols
# ---------------------------------------------------------------------------

# --- Capabilities split from Radio (P1 slim) -------------------------------


@runtime_checkable
class LevelsCapable(Protocol):
    """Radio supports setting receiver levels: AF, RF gain, squelch."""

    async def get_af_level(self, receiver: int = 0) -> int:
        """Get AF (audio) output level (0-255).

        Args:
            receiver: 0 = main (default), 1 = sub.
        """
        ...

    async def set_af_level(self, level: int, receiver: int = 0) -> None:
        """Set AF (audio) output level (0-255).

        Args:
            level: Level in 0-255 scale.
            receiver: 0 = main (default), 1 = sub.
        """
        ...

    async def get_rf_gain(self, receiver: int = 0) -> int:
        """Get RF gain level (0-255).

        Args:
            receiver: 0 = main (default), 1 = sub.
        """
        ...

    async def set_rf_gain(self, level: int, receiver: int = 0) -> None:
        """Set RF gain level (0-255).

        Args:
            level: Level in 0-255 scale.
            receiver: 0 = main (default), 1 = sub.
        """
        ...

    async def set_squelch(self, level: int, receiver: int = 0) -> None:
        """Set squelch level (0-255).

        Args:
            level: Level in 0-255 scale.
            receiver: 0 = main (default), 1 = sub.
        """
        ...

    async def get_squelch(self, receiver: int = 0) -> int:
        """Get squelch level (0-255).

        Args:
            receiver: 0 = main (default), 1 = sub.

        Returns:
            Current squelch level in 0-255 scale.
        """
        ...

    async def get_nr_level(self, receiver: int = 0) -> int:
        """Get noise reduction level (0-255)."""
        ...

    async def set_nr_level(self, level: int, receiver: int = 0) -> None:
        """Set noise reduction level (0-255)."""
        ...

    async def get_nb_level(self, receiver: int = 0) -> int:
        """Get noise blanker level (0-255)."""
        ...

    async def set_nb_level(self, level: int, receiver: int = 0) -> None:
        """Set noise blanker level (0-255)."""
        ...

    async def get_mic_gain(self) -> int:
        """Get microphone gain level (0-255)."""
        ...

    async def set_mic_gain(self, level: int) -> None:
        """Set microphone gain level (0-255)."""
        ...

    async def get_drive_gain(self) -> int:
        """Get drive gain / TX power adjust level (0-255)."""
        ...

    async def set_drive_gain(self, level: int) -> None:
        """Set drive gain / TX power adjust level (0-255)."""
        ...

    async def get_compressor_level(self) -> int:
        """Get speech compressor level (0-255)."""
        ...

    async def set_compressor_level(self, level: int) -> None:
        """Set speech compressor level (0-255)."""
        ...


@runtime_checkable
class MetersCapable(Protocol):
    """Radio supports read-only meters: S-meter, SWR, TX power."""

    async def get_s_meter(self, receiver: int = 0) -> int:
        """Get S-meter reading (raw value, vendor-specific scale).

        Args:
            receiver: 0 = main (default), 1 = sub.
        """
        ...

    async def get_swr(self) -> float:
        """Get SWR reading (1.0 = perfect match)."""
        ...

    async def get_rf_power(self) -> int:
        """Get TX power level (0-255 normalised scale).

        .. deprecated::
            Redundant with :meth:`PowerControlCapable.get_rf_power`. Kept here
            to preserve ``isinstance(radio, MetersCapable)`` checks. New code
            should program against :class:`PowerControlCapable`, which exposes
            both ``get_rf_power`` and ``set_rf_power`` together.
        """
        ...

    async def get_comp_meter(self) -> int:
        """Get compression meter reading (0-255)."""
        ...

    async def get_id_meter(self) -> int:
        """Get drive (Id) meter reading (0-255)."""
        ...

    async def get_vd_meter(self) -> int:
        """Get Vd meter reading (0-255)."""
        ...

    async def get_power_meter(self) -> int:
        """Get TX power meter reading (raw 0-255).

        Distinct from :meth:`get_rf_power` (configured/set TX power level)
        — this returns the live TX output meter.
        """
        ...

    async def get_alc_meter(self) -> int:
        """Get ALC meter reading (raw 0-255)."""
        ...

    async def get_swr_meter(self) -> int:
        """Get SWR meter reading (raw 0-255).

        Returns the unscaled meter value. For a calibrated SWR ratio
        (>= 1.0) use :meth:`get_swr`.
        """
        ...


@runtime_checkable
class PowerControlCapable(Protocol):
    """Radio supports power on/off and TX power level control."""

    async def get_powerstat(self) -> bool:
        """Get the current radio power state.

        Returns:
            True if radio is powered on, False if powered off.
        """
        ...

    async def set_powerstat(self, on: bool) -> None:
        """Power the radio on or off.

        Args:
            on: True to power on, False to power off.
        """
        ...

    async def get_rf_power(self) -> int:
        """Get TX power level (0-255 normalised scale).

        Paired with :meth:`set_rf_power`. Also declared on
        :class:`MetersCapable` for backwards compatibility with existing
        ``isinstance`` checks.
        """
        ...

    async def set_rf_power(self, level: int) -> None:
        """Set TX power level (0-255 normalised scale).

        Args:
            level: Power level in 0-255 scale.
        """
        ...

    @property
    def native_power_unit(self) -> Literal["raw_255", "watts"]:
        """The wire-level unit used by this radio's power commands.

        Icom CI-V radios use a raw 0-255 scale (``"raw_255"``); Yaesu
        CAT ``PC`` commands use watts (0-999, three-digit padded).
        Higher-level layers (web UI, rigctld) inspect this to decide
        whether to translate from a user-friendly unit before queueing
        a :class:`SetPower` command.

        Implementations typically declare this as a class attribute
        (``native_power_unit: Literal["raw_255", "watts"] = "raw_255"``)
        — a class attribute structurally satisfies the property
        requirement under :func:`runtime_checkable`.
        """
        ...


@runtime_checkable
class SplitCapable(Protocol):
    """Radio supports split-frequency operation (RX on one VFO, TX on the other).

    Universal across the supported HF/VHF matrix: Icom rigs use CI-V ``0x0F``
    (read with no payload, set with ``0x00``/``0x01``), Yaesu CAT uses ``ST;``.
    Split is rig-global on every supported model — there is intentionally no
    ``receiver`` parameter; split applies to the whole transceiver, not a
    per-receiver toggle.
    """

    async def get_split(self) -> bool:
        """Return ``True`` if split mode is enabled."""
        ...

    async def set_split(self, on: bool) -> None:
        """Enable (``True``) or disable (``False``) split mode."""
        ...


@runtime_checkable
class StateNotifyCapable(Protocol):
    """Radio supports server integration callbacks for state and reconnect."""

    def set_state_change_callback(
        self,
        callback: Callable[..., Any] | None,
    ) -> None:
        """Register a callback for radio state change notifications.

        Called by the web server to receive real-time updates.
        Pass ``None`` to unregister.
        """
        ...

    def set_reconnect_callback(
        self,
        callback: Callable[..., Any] | None,
    ) -> None:
        """Register a callback invoked after successful reconnection.

        Used by the web server to re-enable scope/audio after transport
        recovery.  Pass ``None`` to unregister.
        """
        ...


@runtime_checkable
class StatePoller(Protocol):
    """Coroutine-driven request-response state poller.

    Periodically issues read-state commands and invokes the registered
    callback with the freshest :class:`RadioState`. Used by the web and
    rigctld layers for radio backends that lack push-style state events
    (Yaesu CAT and similar).
    """

    async def start(self) -> None:
        """Start the polling loops."""
        ...

    async def stop(self) -> None:
        """Stop the polling loops and wait for them to finish."""
        ...


@runtime_checkable
class StatePollable(Protocol):
    """Radio that needs an explicit request-response state poller.

    Yaesu CAT radios implement this; Icom CI-V radios do not (they push
    state changes via the CI-V RX stream and let the web layer drive a
    fire-and-forget :class:`~icom_lan.web.radio_poller.RadioPoller` for
    cache prewarming instead).

    Consumers (web, rigctld) check this with ``isinstance`` and route
    state-driving logic without importing concrete backend classes::

        if isinstance(radio, StatePollable):
            poller = radio.create_state_poller(callback=on_state)
            await poller.start()
    """

    def create_state_poller(
        self,
        *,
        callback: Callable[[RadioState], None],
        command_queue: "CommandQueue | None" = None,
    ) -> StatePoller:
        """Construct a :class:`StatePoller` bound to this radio.

        Args:
            callback: Invoked with the current :class:`RadioState`
                after every successful poll.
            command_queue: Optional outbound command queue drained on
                each poll cycle.

        Returns:
            A :class:`StatePoller` ready for ``await poller.start()``.
        """
        ...


@runtime_checkable
class RigctldRoutable(Protocol):
    """Radio that provides a custom rigctld command-routing strategy.

    The rigctld TCP server's handler ships with a built-in default
    routing that works for Icom CI-V radios. Backends with significantly
    different command semantics (Yaesu CAT today; future Kenwood TS-590
    or other vendors) expose their own
    :class:`~icom_lan.rigctld.routing.RigctldRouting` implementation via
    this method.

    Consumers (rigctld handler) check this with ``isinstance`` and pick
    up the vendor-specific routing without importing concrete backend
    classes::

        if isinstance(radio, RigctldRoutable):
            routing = radio.rigctld_routing(cache, max_power_w)
        else:
            routing = None  # fall through to the built-in Icom path
    """

    def rigctld_routing(
        self,
        cache: "_FallbackRigState",
        max_power_w: float = 100.0,
    ) -> "RigctldRouting":
        """Construct a :class:`RigctldRouting` bound to this radio.

        Args:
            cache: Shared :class:`_FallbackRigState` cache used by the
                rigctld handler to remember last-known meter/level
                values when the radio cannot answer.
            max_power_w: Rated maximum TX power in watts; used to scale
                normalised RFPOWER readings (defaults to 100 W).

        Returns:
            A :class:`RigctldRouting` ready to serve get/set level,
            get/set func, ``dump_state``, and ``get_info`` calls.
        """
        ...


# ---------------------------------------------------------------------------
# Other optional capability Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class AudioCapable(Protocol):
    """Radio supports real-time audio streaming (LAN, USB, or virtual).

    Audio format is Opus-encoded.  For USB audio devices, the backend
    handles capture/playback internally and exposes the same interface.

    Preferred consumer pattern: use :attr:`audio_bus` for pub/sub access
    so multiple consumers can receive audio simultaneously::

        async with radio.audio_bus.subscribe(name="recorder") as sub:
            async for packet in sub:
                save(packet)
    """

    @property
    def audio_bus(self) -> "AudioBus":
        """AudioBus instance for pub/sub audio distribution.

        Returns an :class:`~icom_lan.audio_bus.AudioBus` that manages
        subscriptions and automatically starts/stops the radio's audio
        stream based on subscriber count.
        """
        ...

    @property
    def audio_codec(self) -> AudioCodec:
        """Configured audio codec for the radio's audio stream.

        Backends fix this once at construction time (e.g. Yaesu USB-audio
        always reports :attr:`~icom_lan.types.AudioCodec.PCM_1CH_16BIT`);
        Icom LAN backends honour the value supplied to the constructor.
        """
        ...

    @property
    def audio_sample_rate(self) -> int:
        """Configured audio sample rate in Hz.

        Used by the web audio broadcaster to clock the relay correctly
        (see ``web/handlers/audio.py``); a missing property would silently
        fall back to a default rate and mis-clock playback.
        """
        ...

    async def start_audio_rx_opus(
        self,
        callback: Callable[..., Awaitable[None]],
    ) -> None:
        """Start receiving audio.  Decoded frames are passed to *callback*.

        Note: prefer :attr:`audio_bus` for multi-consumer scenarios.
        Direct callback usage is single-consumer only.
        """
        ...

    async def stop_audio_rx_opus(self) -> None:
        """Stop receiving audio."""
        ...

    async def push_audio_tx_opus(self, data: bytes) -> None:
        """Send Opus-encoded audio data for transmission."""
        ...

    async def start_audio_rx_pcm(
        self,
        callback: Callable[..., Any],
        *,
        sample_rate: int | None = None,
        channels: int | None = None,
        frame_ms: int | None = None,
    ) -> None: ...

    async def stop_audio_rx_pcm(self) -> None: ...

    async def start_audio_tx_pcm(
        self,
        *,
        sample_rate: int | None = None,
        channels: int | None = None,
        frame_ms: int | None = None,
    ) -> None: ...

    async def push_audio_tx_pcm(self, data: bytes) -> None: ...
    async def stop_audio_tx_pcm(self) -> None: ...
    async def get_audio_stats(self) -> dict[str, Any]: ...
    async def start_audio_tx_opus(self) -> None: ...
    async def stop_audio_tx_opus(self) -> None: ...


@runtime_checkable
class CivCommandCapable(Protocol):
    """Radio exposes low-level CI-V command injection for background pollers."""

    async def send_civ(
        self,
        command: int,
        sub: int | None = None,
        data: bytes | None = None,
        *,
        wait_response: bool = True,
    ) -> Any:
        """Send a CI-V command through the active backend transport."""
        ...


@runtime_checkable
class ModeInfoCapable(Protocol):
    """Radio exposes backend-native mode metadata for rigctld consumers."""

    async def get_mode_info(self, receiver: int = 0) -> tuple[Mode, int | None]:
        """Return backend-native mode enum and filter selector."""
        ...


@runtime_checkable
class ScopeCapable(Protocol):
    """Radio supports a spectrum/panadapter scope data stream."""

    async def enable_scope(self, **kwargs: Any) -> None:
        """Enable the spectrum scope.

        Keyword arguments are backend-specific (e.g. span, center_freq).
        """
        ...

    async def disable_scope(self) -> None:
        """Disable the spectrum scope."""
        ...

    def on_scope_data(self, callback: Callable[..., Any] | None) -> None:
        """Register scope-frame callback; pass ``None`` to unregister."""
        ...

    def scope_stream(self) -> AsyncIterator["ScopeFrame"]:
        """Async iterator yielding scope frames as they are assembled."""
        ...

    async def get_scope_during_tx(self) -> bool: ...
    async def set_scope_during_tx(self, on: bool) -> None: ...
    async def get_scope_center_type(self) -> int: ...
    async def set_scope_center_type(self, center_type: int) -> None: ...
    async def get_scope_fixed_edge(self) -> "ScopeFixedEdge": ...
    async def set_scope_fixed_edge(
        self, *, edge: int, start_hz: int, end_hz: int
    ) -> None: ...
    async def get_scope_edge(self) -> int: ...
    async def set_scope_edge(self, edge: int) -> None: ...
    async def get_scope_rbw(self) -> int: ...
    async def set_scope_rbw(self, rbw: int) -> None: ...
    async def get_scope_vbw(self) -> bool: ...
    async def set_scope_vbw(self, narrow: bool) -> None: ...
    async def capture_scope_frame(self, *, timeout: float = 10.0) -> "ScopeFrame": ...
    async def capture_scope_frames(
        self, *, count: int, timeout: float = 15.0
    ) -> list[Any]: ...

    # Scope control settings (Command 0x27 sub-commands)
    async def get_scope_receiver(self) -> int: ...
    async def set_scope_receiver(self, receiver: int) -> None: ...
    async def get_scope_dual(self) -> bool: ...
    async def set_scope_dual(self, dual: bool) -> None: ...
    async def get_scope_mode(self) -> int: ...
    async def set_scope_mode(self, mode: int) -> None: ...
    async def get_scope_span(self) -> int: ...
    async def set_scope_span(self, span: int) -> None: ...
    async def get_scope_speed(self) -> int: ...
    async def set_scope_speed(self, speed: int) -> None: ...
    async def get_scope_ref(self) -> float: ...
    async def set_scope_ref(self, ref: float) -> None: ...
    async def get_scope_hold(self) -> bool: ...
    async def set_scope_hold(self, on: bool) -> None: ...


@runtime_checkable
class DualReceiverCapable(Protocol):
    """Radio has two independent receivers (e.g. IC-7610 Main/Sub)."""

    async def swap_main_sub(self) -> None:
        """Swap Main and Sub VFO frequencies."""
        ...

    async def equalize_main_sub(self) -> None:
        """Set Sub VFO frequency equal to Main."""
        ...

    async def set_main_sub_tracking(self, on: bool) -> None:
        """Enable or disable Main/Sub frequency tracking."""
        ...

    async def get_main_sub_tracking(self) -> bool:
        """Get Main/Sub frequency tracking on/off state."""
        ...


@runtime_checkable
class ReceiverBankCapable(Protocol):
    """Radio exposes a bank of independent receivers.

    Surfaces the ``Transceiver → Receiver → VFO`` hierarchy without breaking
    the existing ``receiver: int = 0`` parameter convention used across the
    codebase.  A *receiver* is a full signal chain (RF front-end, IF, demod)
    that can be tuned independently; each receiver in turn owns a pair of
    VFO slots (A/B) accessed via :class:`VfoSlotCapable`.

    On a single-receiver radio ``receiver_count == 1`` and
    :meth:`select_receiver` is a no-op for ``which == 0``.  On a dual-receiver
    rig (e.g. IC-7610 Main/Sub) ``receiver_count == 2`` and the ``which``
    argument accepts either the integer index (``0`` for Main, ``1`` for Sub)
    or the case-insensitive name (``"main"`` / ``"sub"``).
    """

    @property
    def receiver_count(self) -> int:
        """Number of independent receivers exposed by this transceiver."""
        ...

    async def select_receiver(self, which: int | str) -> None:
        """Make ``which`` the active receiver for subsequent commands.

        Accepts an integer index (``0``-based) or a case-insensitive name
        (``"main"`` / ``"sub"``).  Implementations may reject out-of-range
        values with :class:`ValueError`.
        """
        ...

    async def get_active_receiver(self) -> int:
        """Return the index of the currently active receiver."""
        ...


@runtime_checkable
class TransceiverBankCapable(Protocol):
    """Radio exposes a bank of independent transceivers.

    Sits at the top of the ``Transceiver → Receiver → VFO`` hierarchy: a
    *transceiver* is a fully independent RF stage (its own TX PA, antenna
    port, and receiver chain) addressable as a distinct unit.  Examples are
    the Yaesu FTX-1 family, where HF+50 MHz and 144+430 MHz are wired as two
    separate transceivers sharing a single control head.

    On a single-transceiver radio ``transceiver_count == 1`` and
    :meth:`set_tx_source` is a no-op for ``xcvr == 0``.  On a multi-
    transceiver rig the ``xcvr`` argument is a 0-based integer index
    identifying which transceiver the operator is driving on TX; the radio
    may expose additional receivers per transceiver via
    :class:`ReceiverBankCapable`.
    """

    @property
    def transceiver_count(self) -> int:
        """Number of independent transceivers exposed by this rig."""
        ...

    async def get_tx_source(self) -> int:
        """Return the 0-based index of the currently active TX transceiver."""
        ...

    async def set_tx_source(self, xcvr: int) -> None:
        """Make ``xcvr`` the active TX transceiver.

        Implementations may reject out-of-range values with
        :class:`ValueError`.
        """
        ...


@runtime_checkable
class VfoSlotCapable(Protocol):
    """Radio exposes VFO A/B slots per receiver.

    Completes the ``Transceiver → Receiver → VFO`` hierarchy: each receiver
    owns a pair of VFO slots (A and B) with independent state tracked by
    :class:`~icom_lan.radio_state.VfoSlotState` (frequency, mode, filter,
    etc.).  Operations take an explicit ``receiver`` index matching the
    existing ``receiver: int = 0`` convention.

    The slot parameter is always a single-character string (``"A"`` or
    ``"B"``), case-insensitive on input and upper-case on output.
    """

    async def get_vfo_slot(self, receiver: int = 0) -> str:
        """Return the active VFO slot (``"A"`` or ``"B"``) for ``receiver``."""
        ...

    async def set_vfo_slot(self, slot: str, receiver: int = 0) -> None:
        """Make ``slot`` (``"A"`` or ``"B"``) the active VFO on ``receiver``."""
        ...

    async def swap_vfo_ab(self, receiver: int = 0) -> None:
        """Swap VFO A and VFO B state on ``receiver`` (frequency, mode, …)."""
        ...

    async def equalize_vfo_ab(self, receiver: int = 0) -> None:
        """Copy the active VFO's state to the inactive VFO on ``receiver``."""
        ...


@runtime_checkable
class StateCacheCapable(Protocol):
    """Radio exposes a shared state cache for server-side snapshots."""

    @property
    def state_cache(self) -> "StateCache":
        """Shared state cache object."""
        ...


@runtime_checkable
class RecoverableConnection(Protocol):
    """Radio supports soft reconnect/disconnect operations."""

    async def soft_reconnect(self) -> None:
        """Attempt in-place reconnect without full teardown."""
        ...

    async def soft_disconnect(self) -> None:
        """Gracefully disconnect from the radio."""
        ...


@runtime_checkable
class DspControlCapable(Protocol):
    """DSP controls: NB, NR, DIGISEL, AGC, filters, notch, PBT, APF/TPF."""

    async def set_filter(self, filter_num: int, receiver: int = 0) -> None: ...
    async def get_filter(self, receiver: int = 0) -> int | None:
        """Get current mode filter number (1-3) when reported, else None."""
        ...

    async def set_filter_shape(self, shape: int, receiver: int = 0) -> None: ...

    async def set_filter_width(self, width_hz: int, receiver: int = 0) -> None:
        """Set DSP IF filter width in Hz (Hz↔index translation handled by backend)."""
        ...

    async def get_filter_width(self, receiver: int = 0) -> int:
        """Get DSP IF filter width in Hz (Hz↔index translation handled by backend)."""
        ...

    async def set_nb(self, on: bool, receiver: int = 0) -> None: ...
    async def set_nr(self, on: bool, receiver: int = 0) -> None: ...
    async def set_digisel(self, on: bool, receiver: int = 0) -> None: ...
    async def set_ip_plus(self, on: bool, receiver: int = 0) -> None: ...
    async def set_agc(self, mode: int, receiver: int = 0) -> None: ...
    async def get_agc(self, receiver: int = 0) -> int:
        """Get AGC mode (numeric — backend-specific encoding)."""
        ...

    async def get_auto_notch(self, receiver: int = 0) -> bool: ...
    async def set_auto_notch(self, on: bool, receiver: int = 0) -> None: ...
    async def get_manual_notch(self, receiver: int = 0) -> bool: ...
    async def set_manual_notch(self, on: bool, receiver: int = 0) -> None: ...

    async def set_manual_notch_width(self, value: int, receiver: int = 0) -> None:
        """Set manual notch filter width (0-255)."""
        ...

    async def get_manual_notch_width(self, receiver: int = 0) -> int:
        """Get manual notch filter width (0-255)."""
        ...

    async def set_notch_filter(self, level: int, receiver: int = 0) -> None:
        """Set notch filter level/position (0-255).

        Maps to Icom CI-V ``0x14 0x0D`` and to the Yaesu BP01
        ``set_manual_notch_freq`` alias on Yaesu CAT backends.
        """
        ...

    async def get_notch_filter(self, receiver: int = 0) -> int:
        """Get notch filter level/position (0-255)."""
        ...

    async def get_pbt_inner(self, receiver: int = 0) -> int: ...
    async def set_pbt_inner(self, level: int, receiver: int = 0) -> None: ...
    async def get_pbt_outer(self, receiver: int = 0) -> int: ...
    async def set_pbt_outer(self, level: int, receiver: int = 0) -> None: ...

    async def set_audio_peak_filter(self, mode: int, receiver: int = 0) -> None:
        """Set Audio Peak Filter (APF) mode (0=off, 1=soft, 2=sharp)."""
        ...

    async def get_audio_peak_filter(self, receiver: int = 0) -> int:
        """Get Audio Peak Filter (APF) mode."""
        ...

    async def set_twin_peak_filter(self, on: bool, receiver: int = 0) -> None:
        """Enable or disable Twin Peak Filter (TPF)."""
        ...

    async def get_twin_peak_filter(self, receiver: int = 0) -> bool:
        """Get Twin Peak Filter (TPF) on/off state."""
        ...

    async def set_nb_depth(self, level: int, receiver: int = 0) -> None:
        """Set NB depth (0-255)."""
        ...

    async def get_nb_depth(self, receiver: int = 0) -> int:
        """Get NB depth (0-255)."""
        ...

    async def set_nb_width(self, level: int, receiver: int = 0) -> None:
        """Set NB width (0-255)."""
        ...

    async def get_nb_width(self, receiver: int = 0) -> int:
        """Get NB width (0-255)."""
        ...


@runtime_checkable
class AntennaControlCapable(Protocol):
    """Attenuator, preamp, and antenna selection (ANT1/2, RX ANT)."""

    async def set_attenuator_level(self, db: int, receiver: int = 0) -> None: ...
    async def set_attenuator(self, on: bool, receiver: int = 0) -> None: ...
    async def get_attenuator_level(self, receiver: int = 0) -> int: ...
    async def get_attenuator(self, receiver: int = 0) -> bool: ...
    async def set_preamp(self, level: int, receiver: int = 0) -> None: ...
    async def get_preamp(self, receiver: int = 0) -> int: ...
    async def set_antenna_1(self, on: bool) -> None: ...
    async def set_antenna_2(self, on: bool) -> None: ...
    async def set_rx_antenna_ant1(self, on: bool) -> None: ...
    async def set_rx_antenna_ant2(self, on: bool) -> None: ...
    async def get_antenna_1(self) -> bool: ...
    async def get_antenna_2(self) -> bool: ...
    async def get_rx_antenna_ant1(self) -> bool: ...
    async def get_rx_antenna_ant2(self) -> bool: ...


@runtime_checkable
class CwControlCapable(Protocol):
    """CW text, key speed, pitch, dash ratio, break-in mode/delay."""

    async def send_cw_text(self, text: str) -> None: ...
    async def stop_cw_text(self) -> None: ...

    async def get_cw_pitch(self) -> int: ...
    async def set_cw_pitch(self, freq: int) -> None: ...

    async def get_break_in(self) -> BreakInMode:
        """Get CW break-in mode (OFF/SEMI/FULL).

        Backends that only expose binary on/off (e.g. Yaesu CAT) map
        ``False`` to :attr:`BreakInMode.OFF` and ``True`` to
        :attr:`BreakInMode.SEMI`. :class:`BreakInMode` is an :class:`IntEnum`
        and remains bool-compatible at runtime.
        """
        ...

    async def set_break_in(self, mode: BreakInMode | int) -> None:
        """Set CW break-in mode (accepts :class:`BreakInMode` or int).

        Backends that only expose binary on/off (e.g. Yaesu CAT) treat
        :attr:`BreakInMode.OFF` as off and any other value as on.
        """
        ...

    async def set_break_in_delay(self, level: int) -> None:
        """Set CW break-in delay (0-255)."""
        ...

    async def get_break_in_delay(self) -> int:
        """Get CW break-in delay (0-255)."""
        ...

    async def set_dash_ratio(self, value: int) -> None:
        """Set CW dash-to-dot ratio (0-255)."""
        ...

    async def get_dash_ratio(self) -> int:
        """Get CW dash-to-dot ratio (0-255)."""
        ...

    async def set_key_speed(self, speed: int) -> None:
        """Set CW keyer speed in WPM."""
        ...

    async def get_key_speed(self) -> int:
        """Get CW keyer speed in WPM."""
        ...


@runtime_checkable
class VoiceControlCapable(Protocol):
    """VOX, compressor, monitor, modulation levels, SSB TX bandwidth."""

    async def get_vox(self) -> bool: ...
    async def set_vox(self, on: bool) -> None: ...

    async def get_vox_gain(self) -> int: ...
    async def set_vox_gain(self, level: int) -> None: ...
    async def get_anti_vox_gain(self) -> int: ...
    async def set_anti_vox_gain(self, level: int) -> None: ...

    async def set_vox_delay(self, level: int) -> None:
        """Set VOX hang delay (0-255)."""
        ...

    async def get_vox_delay(self) -> int:
        """Get VOX hang delay (0-255)."""
        ...

    async def get_compressor(self) -> bool: ...
    async def set_compressor(self, on: bool) -> None: ...

    async def get_monitor(self) -> bool: ...
    async def set_monitor(self, on: bool) -> None: ...
    async def get_monitor_gain(self) -> int: ...
    async def set_monitor_gain(self, level: int) -> None: ...

    async def set_acc1_mod_level(self, level: int) -> None: ...
    async def set_usb_mod_level(self, level: int) -> None: ...
    async def set_lan_mod_level(self, level: int) -> None: ...

    async def set_ssb_tx_bandwidth(self, value: int) -> None:
        """Set SSB TX bandwidth (0-2 or vendor-specific index)."""
        ...

    async def get_ssb_tx_bandwidth(self) -> int:
        """Get SSB TX bandwidth index."""
        ...


@runtime_checkable
class SystemControlCapable(Protocol):
    """System date/time, dual watch, tuner, dial lock, band select, scan."""

    async def set_system_date(self, year: int, month: int, day: int) -> None: ...
    async def get_system_date(self) -> tuple[int, int, int]: ...
    async def set_system_time(self, hour: int, minute: int) -> None: ...
    async def get_system_time(self) -> tuple[int, int]: ...
    async def set_dual_watch(self, on: bool) -> None: ...
    async def get_dual_watch(self) -> bool: ...
    async def set_tuner_status(self, value: int) -> None: ...
    async def get_tuner_status(self) -> int: ...
    async def get_dial_lock(self) -> bool: ...
    async def set_dial_lock(self, on: bool) -> None: ...

    async def set_band(self, band_code: int) -> None:
        """Select a band by vendor band code."""
        ...

    async def scan_start(self, mode: int = 0) -> None:
        """Start scanning in the given mode (0 = programmed scan)."""
        ...

    async def scan_stop(self) -> None:
        """Stop any active scan."""
        ...

    async def scan_set_df_span(self, span: int) -> None:
        """Set ΔF scan span (0xA1=±5kHz .. 0xA7=±1MHz)."""
        ...

    async def scan_set_resume(self, mode: int) -> None:
        """Set scan resume mode (0xD0=OFF, 0xD1=5s, 0xD2=10s, 0xD3=15s)."""
        ...


@runtime_checkable
class RepeaterControlCapable(Protocol):
    """Repeater tone (CTCSS) and tone squelch (TSQL) controls."""

    async def set_repeater_tone(self, on: bool, receiver: int = 0) -> None:
        """Enable or disable repeater tone (CTCSS) on TX."""
        ...

    async def get_repeater_tone(self, receiver: int = 0) -> bool:
        """Get repeater tone (CTCSS) TX on/off state."""
        ...

    async def set_repeater_tsql(self, on: bool, receiver: int = 0) -> None:
        """Enable or disable tone squelch (TSQL) on RX."""
        ...

    async def get_repeater_tsql(self, receiver: int = 0) -> bool:
        """Get tone squelch (TSQL) RX on/off state."""
        ...

    async def set_tone_freq(self, freq_hz: int, receiver: int = 0) -> None:
        """Set CTCSS tone TX frequency in hundredths of Hz (e.g. 8800 = 88.0 Hz)."""
        ...

    async def get_tone_freq(self, receiver: int = 0) -> int:
        """Get CTCSS tone TX frequency in hundredths of Hz."""
        ...

    async def set_tsql_freq(self, freq_hz: int, receiver: int = 0) -> None:
        """Set TSQL (tone squelch) RX frequency in hundredths of Hz."""
        ...

    async def get_tsql_freq(self, receiver: int = 0) -> int:
        """Get TSQL (tone squelch) RX frequency in hundredths of Hz."""
        ...


@runtime_checkable
class AdvancedControlCapable(
    DspControlCapable,
    AntennaControlCapable,
    CwControlCapable,
    VoiceControlCapable,
    SystemControlCapable,
    RepeaterControlCapable,
    Protocol,
):
    """Composed protocol — radio supports full advanced control surface."""

    ...


@runtime_checkable
class RitXitCapable(Protocol):
    """Radio supports RIT/XIT (a.k.a. clarifier) frequency offset and on/off control.

    Cross-vendor surface: Icom calls this RIT/XIT, Yaesu calls it the
    "clarifier" — semantically identical six-method contract on every HF
    rig in the project (IC-7610, IC-7300, IC-705, IC-9700, FTX-1).
    """

    async def get_rit_frequency(self) -> int:
        """Get RIT frequency offset in Hz."""
        ...

    async def set_rit_frequency(self, freq_hz: int) -> None:
        """Set RIT frequency offset in Hz."""
        ...

    async def get_rit_status(self) -> bool:
        """Get RIT on/off status."""
        ...

    async def set_rit_status(self, on: bool) -> None:
        """Set RIT on/off status."""
        ...

    async def get_rit_tx_status(self) -> bool:
        """Get RIT TX (XIT) on/off status."""
        ...

    async def set_rit_tx_status(self, on: bool) -> None:
        """Set RIT TX (XIT) on/off status."""
        ...


@runtime_checkable
class TransceiverStatusCapable(Protocol):
    """Radio supports TX frequency monitor (M4 transceiver_status family).

    RIT/XIT lives in :class:`RitXitCapable` — the two were previously bundled
    here but have unrelated semantics.
    """

    async def get_tx_freq_monitor(self) -> bool:
        """Get TX frequency monitor on/off status."""
        ...

    async def set_tx_freq_monitor(self, on: bool) -> None:
        """Set TX frequency monitor on/off status."""
        ...


@runtime_checkable
class MemoryCapable(Protocol):
    """Radio supports memory channel operations.

    Covers memory mode selection, VFO-to-memory write, memory-to-VFO recall,
    memory clear, full memory channel programming, and band stacking register
    write.  Read-back methods are omitted because the IC-7610 (and many Icom
    radios) do not support GET variants for these commands.
    """

    async def set_memory_mode(self, channel: int) -> None:
        """Select a memory channel (1-101)."""
        ...

    async def memory_write(self) -> None:
        """Write the current VFO state to the selected memory channel."""
        ...

    async def memory_to_vfo(self, channel: int) -> None:
        """Load a memory channel into the VFO (1-101)."""
        ...

    async def memory_clear(self, channel: int) -> None:
        """Clear a memory channel (1-101)."""
        ...

    async def set_memory_contents(self, mem: "MemoryChannel") -> None:
        """Write full channel data to a memory channel."""
        ...

    async def set_bsr(self, bsr: "BandStackRegister") -> None:
        """Write a band stacking register entry."""
        ...
