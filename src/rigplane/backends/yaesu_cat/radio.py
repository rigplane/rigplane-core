"""Yaesu CAT radio backend — FTX-1 and compatible transceivers.

Implements the core :class:`~rigplane.radio_protocol.Radio` protocol
using :class:`YaesuCatTransport` for serial I/O and
:class:`CatCommandParser` / :func:`format_command` for encoding.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal, cast

from ...audio import AudioPacket
from ...command_spec import CatCommandSpec
from ...commands import hz_to_table_index, table_index_to_hz
from ...types import AudioCodec, BreakInMode
from ...exceptions import AudioFormatError, CommandError
from ...exceptions import ConnectionError as RadioConnectionError
from ...radio_state import RadioState
from .parser import CatCommandParser, format_command
from .transport import YaesuCatTransport

if TYPE_CHECKING:
    from ..._poller_types import CommandQueue
    from ...audio.usb_driver import UsbAudioDriver
    from ...audio_bus import AudioBus
    from ...profiles import RadioProfile
    from ...profiles.rig_loader import RigConfig
    from ...types import BandStackRegister, MemoryChannel
    from .poller import YaesuCatPoller

__all__ = ["YaesuCatRadio"]

logger = logging.getLogger(__name__)

# Path to rigs/ directory: src/rigplane/backends/yaesu_cat/radio.py → 4 levels up
_RIGS_DIR = Path(__file__).parents[4] / "rigs"


def _load_config(profile: Any) -> "RigConfig":
    """Load RigConfig from a profile name or return an existing RigConfig."""
    from ...rig_loader import RigConfig, load_rig

    if isinstance(profile, str):
        path = _RIGS_DIR / f"{profile}.toml"
        return load_rig(path)
    if isinstance(profile, RigConfig):
        return profile
    raise TypeError(f"profile must be str or RigConfig, got {type(profile).__name__}")


# Backwards-compat alias — historical name kept for the FTX-1 backend.
# The shared implementation now lives in ``rigplane.meter_cal`` so that
# both the Yaesu and Icom backends apply the same piecewise-linear curve
# to ``[[meters.swr.calibration]]`` tables (issue #1173).
from ...meter_cal import MeterType, interpolate_swr as _interpolate_swr  # noqa: E402


class YaesuCatRadio:
    """Radio backend for Yaesu FTX-1 (and compatible) transceivers.

    Communicates via Yaesu CAT protocol over serial.  Supports the four
    core operations needed for the FTX-1 smoke test: frequency, mode,
    PTT, and S-meter.

    Usage::

        async with YaesuCatRadio("/dev/cu.usbserial-...") as radio:
            freq = await radio.get_freq()
            await radio.set_freq(14_074_000)
            mode, _ = await radio.get_mode()
            await radio.set_ptt(True)
            s = await radio.get_s_meter()
    """

    # Yaesu CAT ``PC`` command takes a watt value (0-999, three-digit
    # padded), not a raw 0-255 scale. Inspected by upper layers to
    # avoid translating user-facing watt values into the Icom raw
    # scale before queueing SetPower. See
    # :class:`rigplane.core.radio_protocol.PowerControlCapable`.
    native_power_unit: Literal["raw_255", "watts"] = "watts"

    # Yaesu CAT radios connect over USB and expose audio as a separate
    # USB Audio Class device handled by the OS, not through in-band
    # CI-V/CAT framing. The web layer's ``runtime_capabilities`` helper
    # uses this marker (via :class:`UsbAudioCapable`) to keep the
    # ``"audio"`` UI capability advertised even for backends that don't
    # implement the Radio Protocol's :class:`AudioCapable` surface.
    # See :class:`rigplane.core.radio_protocol.UsbAudioCapable`.
    has_usb_audio: bool = True

    def __init__(
        self,
        device: str,
        baudrate: int = 38400,
        profile: str | Any = "ftx1",
        rx_device: str | None = None,
        tx_device: str | None = None,
        audio_sample_rate: int = 48000,
        audio_driver: UsbAudioDriver | None = None,
    ) -> None:
        """Create a YaesuCatRadio instance.

        Args:
            device: Serial port path (e.g. ``"/dev/cu.usbserial-01AE340D0"``).
            baudrate: Serial baud rate (default 38400 for FTX-1).
            profile: Rig profile name (``"ftx1"``) or a loaded ``RigConfig``.
            rx_device: USB audio input device name for RX audio capture.
            tx_device: USB audio output device name for TX audio playback.
            audio_sample_rate: Audio sample rate in Hz (default 48000).
            audio_driver: Optional pre-constructed UsbAudioDriver (for testing).
        """
        self._config: RigConfig = _load_config(profile)
        self._profile_cache: RadioProfile | None = None
        self._transport = YaesuCatTransport(device=device, baudrate=baudrate)
        self._state = RadioState()
        self._audio_bus: AudioBus | None = None
        self._audio_seq = 0
        self._opus_rx_user_callback: Callable[[AudioPacket | None], None] | None = None
        self._pcm_rx_user_callback: Callable[[bytes | None], None] | None = None
        self._audio_sample_rate = audio_sample_rate
        if audio_driver is None:
            # Lazy import: avoids pulling rigplane.audio.backend (PortAudio,
            # numpy DSP) into top-level package import. PR #1200 / #1194.
            from ...audio.usb_driver import UsbAudioDriver as _UsbAudioDriver

            self._audio_driver: UsbAudioDriver = _UsbAudioDriver(
                serial_port=device,
                rx_device=rx_device,
                tx_device=tx_device,
                sample_rate=audio_sample_rate,
                channels=1,
                backend=None,  # default PortAudioBackend
            )
        else:
            self._audio_driver = audio_driver

        # Build bidirectional mode code ↔ name maps.
        # FTX-1 CAT codes are 1-based: index 0 in modes list → code "1".
        self._code_to_mode: dict[str, str] = {}
        self._mode_to_code: dict[str, str] = {}
        for i, name in enumerate(self._config.modes, start=1):
            code = str(i)
            self._code_to_mode[code] = name
            self._mode_to_code[name] = code

        # Compile response parsers once at init time (keyed by command name).
        # Commands with unsupported placeholders (e.g. {vfo}, {band}) are skipped.
        self._parsers: dict[str, CatCommandParser] = {}
        for cmd_name, spec in self._config.commands.items():
            if isinstance(spec, CatCommandSpec) and spec.parse:
                try:
                    self._parsers[cmd_name] = CatCommandParser(spec.parse)
                except ValueError:
                    logger.debug(
                        "Skipping parser for %r (unsupported placeholder)", cmd_name
                    )

    # -- Lifecycle ----------------------------------------------------------

    async def connect(self) -> None:
        """Open the serial port and seed state from IF bulk query."""
        await self._transport.connect()
        try:
            await self.get_if_status()
        except (CommandError, Exception):
            logger.debug("IF bulk query at connect failed (non-fatal)")

    async def disconnect(self) -> None:
        """Close the serial port."""
        await self._audio_driver.stop_rx()
        await self._audio_driver.stop_tx()
        await self._transport.close()

    async def __aenter__(self) -> "YaesuCatRadio":
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.disconnect()

    @property
    def connected(self) -> bool:
        """Whether the serial transport is connected."""
        return self._transport.connected

    @property
    def radio_ready(self) -> bool:
        """Whether the backend is ready for commands."""
        return self._transport.connected

    @property
    def backend_id(self) -> str:
        """Stable backend family identifier — ``"yaesu_cat"`` for Yaesu CAT."""
        return "yaesu_cat"

    @property
    def model(self) -> str:
        """Human-readable radio model name (e.g. ``'FTX-1'``)."""
        return str(self._config.model)

    @property
    def hamlib_model_id(self) -> int:
        """Hamlib rig_model integer (e.g. ``2028`` for RIG_MODEL_FTX1).

        Read from the rig's TOML ``[radio].hamlib_model_id`` field; used by
        the rigctld Yaesu ``dump_state`` response so external clients see the
        correct model (closes #441).
        """
        return int(self._config.hamlib_model_id)

    @property
    def capabilities(self) -> set[str]:
        """Set of capability tags from the rig profile."""
        return set(self._config.capabilities)

    @property
    def profile(self) -> "RadioProfile":
        """RadioProfile for this rig (from TOML config, cached)."""
        if self._profile_cache is None:
            self._profile_cache = self._config.to_profile()
        return self._profile_cache

    @property
    def radio_state(self) -> RadioState:
        """Live radio state snapshot (updated by get_* calls)."""
        return self._state

    @property
    def audio_codec(self) -> AudioCodec:
        """Audio codec used by USB audio (always PCM 16-bit mono)."""
        return AudioCodec.PCM_1CH_16BIT

    @property
    def audio_sample_rate(self) -> int:
        """Configured audio sample rate in Hz (default 48000)."""
        return self._audio_sample_rate

    @property
    def usb_audio_contract(self) -> object | None:
        """Effective OS audio contract reported by the USB audio driver."""

        return getattr(self._audio_driver, "usb_audio_contract", None)

    @property
    def audio_bus(self) -> "AudioBus":
        """AudioBus instance for pub/sub audio distribution."""
        if self._audio_bus is None:
            from ...audio_bus import AudioBus

            self._audio_bus = AudioBus(self)
        return self._audio_bus

    # -- AudioCapable methods -----------------------------------------------

    async def start_audio_rx_opus(
        self,
        callback: Callable[[AudioPacket | None], None],
        *,
        jitter_depth: int = 5,
    ) -> None:
        if not callable(callback):
            raise TypeError("callback must be callable and accept AudioPacket | None.")
        if isinstance(jitter_depth, bool) or not isinstance(jitter_depth, int):
            raise TypeError(
                f"jitter_depth must be an int, got {type(jitter_depth).__name__}."
            )
        if jitter_depth < 0:
            raise ValueError(f"jitter_depth must be >= 0, got {jitter_depth}.")
        self._require_connected()

        self._opus_rx_user_callback = callback

        def _on_pcm_frame(pcm_frame: bytes) -> None:
            packet = AudioPacket(
                ident=0x9781,
                send_seq=self._audio_seq,
                data=pcm_frame,
            )
            self._audio_seq = (self._audio_seq + 1) & 0xFFFF
            callback(packet)

        await self._audio_driver.start_rx(_on_pcm_frame)

    async def stop_audio_rx_opus(self) -> None:
        self._opus_rx_user_callback = None
        await self._audio_driver.stop_rx()

    async def start_audio_rx_pcm(
        self,
        callback: Callable[[bytes | None], None],
        *,
        sample_rate: int = 48000,
        channels: int = 1,
        frame_ms: int = 20,
        jitter_depth: int = 5,
    ) -> None:
        if not callable(callback):
            raise TypeError("callback must be callable and accept bytes | None.")
        for name, value in (
            ("sample_rate", sample_rate),
            ("channels", channels),
            ("frame_ms", frame_ms),
            ("jitter_depth", jitter_depth),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an int, got {type(value).__name__}.")
        if jitter_depth < 0:
            raise ValueError(f"jitter_depth must be >= 0, got {jitter_depth}.")
        if (sample_rate * frame_ms) % 1000 != 0:
            raise AudioFormatError(
                "sample_rate * frame_ms must produce an integer frame size."
            )

        self._require_connected()
        self._pcm_rx_user_callback = callback

        await self._audio_driver.start_rx(
            callback,
            sample_rate=sample_rate,
            channels=channels,
            frame_ms=frame_ms,
        )

    async def stop_audio_rx_pcm(self) -> None:
        self._pcm_rx_user_callback = None
        await self._audio_driver.stop_rx()

    async def start_audio_tx_pcm(
        self,
        *,
        sample_rate: int = 48000,
        channels: int = 1,
        frame_ms: int = 20,
    ) -> None:
        for name, value in (
            ("sample_rate", sample_rate),
            ("channels", channels),
            ("frame_ms", frame_ms),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an int, got {type(value).__name__}.")
        if (sample_rate * frame_ms) % 1000 != 0:
            raise AudioFormatError(
                "sample_rate * frame_ms must produce an integer frame size."
            )

        self._require_connected()
        await self._audio_driver.start_tx(
            sample_rate=sample_rate,
            channels=channels,
            frame_ms=frame_ms,
        )

    async def stop_audio_tx_pcm(self) -> None:
        await self._audio_driver.stop_tx()

    async def _push_pcm_tx(self, frame: bytes) -> None:
        if not isinstance(frame, bytes):
            raise TypeError(f"frame must be bytes, got {type(frame).__name__}.")
        if len(frame) == 0:
            raise ValueError("frame must not be empty.")

        self._require_connected()
        await self._audio_driver._push_tx_pcm(frame)

    # -- AudioCapable TX methods --------------------------------------------

    async def push_audio_tx_opus(self, data: bytes) -> None:
        """Forward Opus TX data as PCM (USB audio is always PCM)."""
        # Browser sends Opus; AudioBroadcaster transcodes to PCM before calling.
        # If raw Opus arrives here, just push as-is (driver handles it).
        await self._push_pcm_tx(data)

    async def push_audio_tx_pcm(self, data: bytes) -> None:
        """Push raw PCM TX data."""
        await self._push_pcm_tx(data)

    async def start_audio_tx_opus(self) -> None:
        """No-op: USB audio TX is started via start_audio_tx_pcm."""
        pass

    async def stop_audio_tx_opus(self) -> None:
        """Stop TX audio."""
        await self.stop_audio_tx_pcm()

    async def get_audio_stats(self) -> dict[str, Any]:
        """Return basic audio stats."""
        return {
            "rx_active": self._audio_driver.rx_running,
            "tx_active": self._audio_driver.tx_running,
            "sample_rate": self._audio_sample_rate,
        }

    # -- LevelsCapable: unsupported TX controls -----------------------------

    async def get_drive_gain(self) -> int:
        """Drive gain not available via CAT on FTX-1."""
        return 0

    async def set_drive_gain(self, level: int) -> None:
        """Drive gain not available via CAT on FTX-1."""
        logger.debug("set_drive_gain: not supported on this radio")

    async def get_compressor_level(self) -> int:
        """Alias for LevelsCapable compatibility — delegates to processor level."""
        return await self.get_processor_level()

    async def set_compressor_level(self, level: int) -> None:
        """Alias for LevelsCapable compatibility — delegates to processor level."""
        await self.set_processor_level(level)

    # -- Optional commands (profile-dependent) ------------------------------

    async def set_nb(self, on: bool, receiver: int = 0) -> None:
        """Enable or disable the noise blanker.

        For radios with ``level_is_toggle`` (e.g. FTX-1), translates to
        ``set_nb_level(0)`` for off and ``set_nb_level(default)`` for on.
        No-op if neither ``set_nb`` nor ``set_nb_level`` is defined.
        """
        if self._has_write_command("set_nb"):
            await self._write("set_nb", state="1" if on else "0")
        elif self._has_write_command("set_nb_level"):
            if on:
                current = self._state.main.nb_level
                level = current if current > 0 else self._default_nb_level()
                await self.set_nb_level(level, receiver=receiver)
            else:
                await self.set_nb_level(0, receiver=receiver)

    async def set_nr(self, on: bool, receiver: int = 0) -> None:
        """Enable or disable noise reduction.

        For radios with ``level_is_toggle`` (e.g. FTX-1), translates to
        ``set_nr_level(0)`` for off and ``set_nr_level(default)`` for on.
        No-op if neither ``set_nr`` nor ``set_nr_level`` is defined.
        """
        if self._has_write_command("set_nr"):
            await self._write("set_nr", state="1" if on else "0")
        elif self._has_write_command("set_nr_level"):
            if on:
                current = self._state.main.nr_level
                level = current if current > 0 else self._default_nr_level()
                await self.set_nr_level(level, receiver=receiver)
            else:
                await self.set_nr_level(0, receiver=receiver)

    async def set_dual_watch(self, on: bool) -> None:
        """Enable or disable dual watch.

        No-op with warning if the rig profile does not define a
        ``set_dual_watch`` command.
        """
        if self._has_write_command("set_dual_watch"):
            await self._write("set_dual_watch", state="1" if on else "0")
        else:
            logger.warning("set_dual_watch: no CAT command defined for %s", self.model)

    # -- Runtime profile introspection -------------------------------------

    def _has_command(self, name: str) -> bool:
        """Check if a command is defined in the rig profile."""
        spec = self._config.commands.get(name)
        return spec is not None and isinstance(spec, CatCommandSpec)

    def _has_write_command(self, name: str) -> bool:
        """Check if a write command is defined in the rig profile."""
        spec = self._config.commands.get(name)
        return (
            spec is not None
            and isinstance(spec, CatCommandSpec)
            and spec.write is not None
        )

    def supports_command(self, command: str) -> bool:
        """Check if a command is defined in the rig profile."""
        return self._has_command(command)

    def _default_nb_level(self) -> int:
        """Default NB level for turning on when current level is 0."""
        ctrl = (self._config.controls or {}).get("nb", {})
        range_max = int(ctrl.get("range_max", 10))
        return max(1, range_max // 2)

    def _default_nr_level(self) -> int:
        """Default NR level for turning on when current level is 0."""
        ctrl = (self._config.controls or {}).get("nr", {})
        range_max = int(ctrl.get("range_max", 15))
        return max(1, range_max // 2)

    # -- Internal helpers ---------------------------------------------------

    def _get_spec(self, name: str) -> CatCommandSpec:
        """Return the CatCommandSpec for *name*, raising CommandError if absent."""
        spec = self._config.commands.get(name)
        if spec is None:
            raise CommandError(
                f"Command {name!r} not found in profile {self._config.model!r}"
            )
        if not isinstance(spec, CatCommandSpec):
            raise CommandError(f"Command {name!r} is not a CAT command spec")
        return spec

    def _require_connected(self) -> None:
        if not self._transport.connected:
            raise RadioConnectionError("Radio not connected — call connect() first")

    async def _query(self, cmd_name: str) -> dict[str, Any]:
        """Send a read command and return the parsed response fields.

        The transport strips the trailing ``;`` from responses; we add it
        back before passing to the parser (templates include the semicolon).
        """
        self._require_connected()
        spec = self._get_spec(cmd_name)
        if spec.read is None:
            raise CommandError(f"Command {cmd_name!r} has no read template")

        raw = await self._transport.query(spec.read)

        parser = self._parsers.get(cmd_name)
        if parser is None:
            raise CommandError(f"Command {cmd_name!r} has no parse template")

        # Transport strips trailing ';'; add it back for the parser.
        return parser.parse(raw + ";")

    async def _write(self, cmd_name: str, **kwargs: Any) -> None:
        """Format and send a write command (no response expected)."""
        self._require_connected()
        spec = self._get_spec(cmd_name)
        if spec.write is None:
            raise CommandError(f"Command {cmd_name!r} has no write template")

        cmd = format_command(spec.write, **kwargs)
        await self._transport.write(cmd)

    # -- IF Bulk Query ------------------------------------------------------

    async def get_if_status(self) -> dict[str, Any]:
        """Send ``IF;`` and parse the composite response into state fields.

        The Yaesu IF response is a fixed-width string (after the ``IF`` prefix):
        freq(9) + sign(1) + rit_offset(4) + rit(1) + xit(1)
        + bank(1) + chan(2) + tx(1) + mode(1) + vfo(1) + scan(1) + split(1)

        Returns a dict with parsed fields and populates :attr:`radio_state`.
        """
        self._require_connected()
        raw = await self._transport.query("IF;")
        # Transport strips trailing ';'; raw starts with "IF" prefix.
        if not raw.startswith("IF") or len(raw) < 26:
            raise CommandError(f"Invalid IF response: {raw!r}")

        body = raw[2:]  # strip "IF" prefix
        freq = int(body[0:9])
        sign = body[9]
        rit_offset = int(body[10:14])
        rit_on = body[14] == "1"
        xit_on = body[15] == "1"
        # body[16] = bank, body[17:19] = channel — skipped
        tx = body[19] == "1"
        mode_code = body[20]
        vfo = int(body[21])
        # body[22] = scan — skipped
        split = body[23] == "1"

        rit_hz = rit_offset if sign == "+" else -rit_offset
        mode_name = self._code_to_mode.get(mode_code, f"UNKNOWN({mode_code})")

        # Populate state atomically.
        self._state.main.freq = freq
        self._state.main.mode = mode_name
        self._state.ptt = tx
        self._state.rit_on = rit_on
        self._state.rit_tx = xit_on
        self._state.rit_freq = rit_hz
        self._state.split = split

        return {
            "freq": freq,
            "mode": mode_name,
            "rit_offset": rit_hz,
            "rit_on": rit_on,
            "xit_on": xit_on,
            "tx": tx,
            "vfo": vfo,
            "split": split,
        }

    # -- Frequency ----------------------------------------------------------

    async def get_freq(self, receiver: int = 0) -> int:
        """Get the current VFO frequency in Hz.

        Args:
            receiver: 0 = main (VFO-A), 1 = sub (VFO-B).
        """
        cmd = "get_freq" if receiver == 0 else "get_freq_sub"
        result = await self._query(cmd)
        freq: int = result["freq"]
        if receiver == 0:
            self._state.main.freq = freq
        else:
            self._state.sub.freq = freq
        return freq

    async def set_freq(self, freq: int, receiver: int = 0) -> None:
        """Set the VFO frequency in Hz.

        Args:
            freq: Frequency in Hz (e.g. ``14_074_000``).
            receiver: 0 = main (VFO-A), 1 = sub (VFO-B).
        """
        cmd = "set_freq" if receiver == 0 else "set_freq_sub"
        await self._write(cmd, freq=freq)
        if receiver == 0:
            self._state.main.freq = freq
        else:
            self._state.sub.freq = freq

    # -- Mode ---------------------------------------------------------------

    async def get_mode(self, receiver: int = 0) -> tuple[str, int | None]:
        """Get the current operating mode.

        Returns:
            Tuple of (mode_name, None).  Mode names are from the rig
            profile (e.g. ``"USB"``, ``"LSB"``, ``"CW-U"``).
        """
        cmd = "get_mode" if receiver == 0 else "get_mode_sub"
        result = await self._query(cmd)
        code: str = result["mode"]
        mode_name = self._code_to_mode.get(code, f"UNKNOWN({code})")
        if receiver == 0:
            self._state.main.mode = mode_name
        else:
            self._state.sub.mode = mode_name
        return mode_name, None

    async def set_mode(
        self,
        mode: str,
        filter_width: int | None = None,
        receiver: int = 0,
    ) -> None:
        """Set the operating mode.

        Args:
            mode: Mode name from the rig profile (e.g. ``"USB"``).
            filter_width: Ignored (not supported by this backend).
            receiver: 0 = main, 1 = sub.
        """
        code = self._mode_to_code.get(mode)
        if code is None:
            raise CommandError(
                f"Unknown mode {mode!r} for {self._config.model!r}. "
                f"Available: {list(self._mode_to_code)}"
            )
        cmd = "set_mode" if receiver == 0 else "set_mode_sub"
        await self._write(cmd, mode=code)
        if receiver == 0:
            self._state.main.mode = mode
        else:
            self._state.sub.mode = mode

    # -- Data mode ---------------------------------------------------------

    async def get_data_mode(self) -> bool:
        """Whether DATA mode is active.

        On Yaesu radios, DATA mode is embedded in the mode string (e.g.
        ``USB-D``).  We derive it from the current mode name rather than
        issuing a separate CAT query.
        """
        mode = self._state.main.mode or ""
        return mode.endswith("-D") or "DATA" in mode.upper()

    async def set_data_mode(self, on: int | bool, receiver: int = 0) -> None:
        """Toggle DATA mode.

        On Yaesu radios this is done by switching the operating mode
        (e.g. USB ↔ USB-D).  A full implementation requires the rig
        profile to map mode pairs.  For now, log a warning if the
        profile lacks a ``set_data_mode`` CAT command.
        """
        if self._has_write_command("set_data_mode"):
            await self._write("set_data_mode", state="1" if on else "0")
        else:
            logger.warning("set_data_mode: no CAT command defined for %s", self.model)

    # -- Power switch (PS) -------------------------------------------------

    async def get_powerstat(self) -> bool:
        """Query the power switch state.

        Returns:
            ``True`` if the radio is powered on, ``False`` otherwise.
        """
        result = await self._query("get_powerstat")
        return bool(result["state"] == "1")

    async def set_powerstat(self, on: bool) -> None:
        """Set the power switch state.

        Args:
            on: ``True`` to power on, ``False`` to power off.
        """
        await self._write("set_powerstat", state="1" if on else "0")

    # -- PTT ----------------------------------------------------------------

    async def set_ptt(self, on: bool) -> None:
        """Key or un-key the transmitter.

        Args:
            on: ``True`` to transmit, ``False`` to receive.
        """
        await self._write("set_ptt", state="1" if on else "0")
        self._state.ptt = on

    async def get_ptt(self) -> bool:
        """Query the current PTT state.

        Returns:
            ``True`` if transmitting, ``False`` if receiving.
        """
        result = await self._query("get_ptt")
        ptt: bool = result["state"] == "1"
        self._state.ptt = ptt
        return ptt

    # -- S-meter ------------------------------------------------------------

    async def get_s_meter(self, receiver: int = 0) -> int:
        """Get the S-meter raw value.

        Args:
            receiver: 0 = main, 1 = sub.

        Returns:
            Raw S-meter reading (0–255, vendor scale).
        """
        cmd = "get_s_meter" if receiver == 0 else "get_s_meter_sub"
        result = await self._query(cmd)
        raw: int = result["raw"]
        if receiver == 0:
            self._state.main.s_meter = raw
        else:
            self._state.sub.s_meter = raw
        return raw

    # -- RM meters (COMP, ALC, Power, SWR, IDD, VDD) ----------------------

    async def _read_meter(self, meter_type: int) -> tuple[int, int]:
        """Read RM{type}; meter. Returns (main, sub) raw values 0–255."""
        self._require_connected()
        raw = await self._transport.query(f"RM{meter_type};")
        # Response: "RM{type}{main:03d}{sub:03d}" (transport strips trailing ;)
        body = raw[2:]  # strip "RM"
        if len(body) < 7:
            raise ValueError(f"Malformed RM meter response: {raw!r}")
        main_val = int(body[1:4])
        sub_val = int(body[4:7])
        return main_val, sub_val

    async def get_comp_meter(self) -> int:
        """Get COMP (compression) meter reading (0–255)."""
        main, _ = await self._read_meter(3)
        return main

    async def get_alc_meter(self) -> int:
        """Get ALC meter reading (0–255)."""
        main, _ = await self._read_meter(4)
        return main

    async def get_power_meter(self) -> int:
        """Get TX power meter reading (0–255)."""
        main, _ = await self._read_meter(5)
        return main

    async def get_swr_meter(self) -> int:
        """Get SWR meter raw reading (0–255)."""
        main, _ = await self._read_meter(6)
        return main

    async def get_swr(self) -> float:
        """Get SWR as a ratio (>= 1.0).

        Uses the piecewise-linear calibration table from
        ``[meters.swr.calibration]`` in the rig TOML when present
        (closes #440). Falls back to the legacy linear mapping
        ``1.0 + raw/255 * 8.9`` when no table is configured.
        """
        raw, _ = await self._read_meter(6)
        return float(_interpolate_swr(raw, self._config.meter_calibrations))

    async def get_id_meter(self) -> int:
        """Get IDD (current drain) meter reading (0–255)."""
        main, _ = await self._read_meter(7)
        return main

    async def get_vd_meter(self) -> int:
        """Get VDD (voltage) meter reading (0–255)."""
        main, _ = await self._read_meter(8)
        return main

    async def get_meter(self, meter_type: str | MeterType, receiver: int = 0) -> int:
        """Dispatch to the matching per-type getter by meter type name.

        Args:
            meter_type: A :class:`~rigplane.meter_cal.MeterType` enum value or
                its string equivalent (``"smeter"``, ``"comp"``, ``"alc"``,
                ``"power"``, ``"swr"``, ``"id"``, ``"vd"``).
            receiver: 0 = main, 1 = sub (only meaningful for ``"smeter"``).

        Returns:
            Raw meter reading (0–255, vendor scale).

        Raises:
            ValueError: If *meter_type* is not a recognised value.
        """
        if isinstance(meter_type, MeterType):
            mt = meter_type
        else:
            try:
                mt = MeterType(str(meter_type))
            except ValueError:
                raise ValueError(
                    f"Unknown meter type: {meter_type!r}. "
                    f"Valid values: {[m.value for m in MeterType]}"
                )
        if mt == MeterType.SMETER:
            return await self.get_s_meter(receiver=receiver)
        if mt == MeterType.COMP:
            return await self.get_comp_meter()
        if mt == MeterType.ALC:
            return await self.get_alc_meter()
        if mt == MeterType.POWER:
            return await self.get_power_meter()
        if mt == MeterType.SWR:
            return await self.get_swr_meter()
        if mt == MeterType.CURRENT:
            return await self.get_id_meter()
        if mt == MeterType.VOLTAGE:
            return await self.get_vd_meter()
        raise ValueError(f"Unhandled meter type: {meter_type!r}")

    async def get_rf_power(self) -> int:
        """Get configured TX power in watts.

        Note: Returns the configured (SET) power level via PC command,
        not measured output. For measured RF output use get_power_meter() (RM5).
        """
        _, watts = await self.get_power()
        return watts

    # -- D1: RX Audio Controls ----------------------------------------------

    async def get_af_level(self, receiver: int = 0) -> int:
        """Get the AF (audio) level (0–255)."""
        cmd = "get_af_level" if receiver == 0 else "get_af_level_sub"
        result = await self._query(cmd)
        level = int(result["level"])
        rx = self._state.main if receiver == 0 else self._state.sub
        rx.af_level = level
        return level

    async def set_af_level(self, level: int, receiver: int = 0) -> None:
        """Set the AF (audio) level (0–255)."""
        cmd = "set_af_level" if receiver == 0 else "set_af_level_sub"
        await self._write(cmd, level=level)
        rx = self._state.main if receiver == 0 else self._state.sub
        rx.af_level = level

    async def get_rf_gain(self, receiver: int = 0) -> int:
        """Get the RF gain (0–255)."""
        cmd = "get_rf_gain" if receiver == 0 else "get_rf_gain_sub"
        result = await self._query(cmd)
        level = int(result["level"])
        rx = self._state.main if receiver == 0 else self._state.sub
        rx.rf_gain = level
        return level

    async def set_rf_gain(self, level: int, receiver: int = 0) -> None:
        """Set the RF gain (0–255)."""
        cmd = "set_rf_gain" if receiver == 0 else "set_rf_gain_sub"
        await self._write(cmd, level=level)
        rx = self._state.main if receiver == 0 else self._state.sub
        rx.rf_gain = level

    async def get_squelch(self, receiver: int = 0) -> int:
        """Get the squelch level (0–255)."""
        cmd = "get_squelch" if receiver == 0 else "get_squelch_sub"
        result = await self._query(cmd)
        level = int(result["level"])
        rx = self._state.main if receiver == 0 else self._state.sub
        rx.squelch = level
        return level

    async def set_squelch(self, level: int, receiver: int = 0) -> None:
        """Set the squelch level (0–255)."""
        cmd = "set_squelch" if receiver == 0 else "set_squelch_sub"
        await self._write(cmd, level=level)
        rx = self._state.main if receiver == 0 else self._state.sub
        rx.squelch = level

    # -- D2: RF Front-End ---------------------------------------------------

    async def get_attenuator(self, receiver: int = 0) -> bool:
        """Get attenuator state (False = OFF, True = ON)."""
        result = await self._query("get_attenuator")
        return bool(int(result["state"]))

    async def set_attenuator(self, state: int, receiver: int = 0) -> None:
        """Set attenuator state (0 = OFF, 1 = ON)."""
        await self._write("set_attenuator", state=str(state))

    async def set_attenuator_level(self, db: int, receiver: int = 0) -> None:
        """Set attenuator by dB level.

        FTX-1 has a simple on/off attenuator: any db > 0 turns it on.
        """
        await self.set_attenuator(1 if db > 0 else 0, receiver=receiver)

    async def get_preamp(self, band: int = 0) -> int:
        """Get preamp setting (0–2).

        Args:
            band: 0 = HF/50 MHz (PA0). Sub-band variants not yet supported.
        """
        result = await self._query("get_preamp")
        return int(result["value"])

    async def set_preamp(self, level: int, receiver: int = 0, *, band: int = 0) -> None:
        """Set preamp setting.

        Args:
            level: Preamp level (0–2).
            receiver: Ignored (FTX-1 single preamp path). Present for protocol compat.
            band: 0 = HF/50 MHz, 1 = VHF, 2 = UHF.
        """
        await self._write("set_preamp", band=str(band), value=str(level))

    # -- D3: DSP (NB/NR/Notch) ----------------------------------------------

    async def get_nb_level(self, receiver: int = 0) -> int:
        """Get noise blanker level (0 = OFF, 1–10 = level)."""
        result = await self._query("get_nb_level")
        return int(result["level"])

    async def set_nb_level(self, level: int, receiver: int = 0) -> None:
        """Set noise blanker level (0 = OFF, 1–10 = level)."""
        await self._write("set_nb_level", level=level)

    async def get_nr_level(self, receiver: int = 0) -> int:
        """Get noise reduction level (0 = OFF, 1–15 = level)."""
        result = await self._query("get_nr_level")
        return int(result["level"])

    async def set_nr_level(self, level: int, receiver: int = 0) -> None:
        """Set noise reduction level (0 = OFF, 1–15 = level)."""
        await self._write("set_nr_level", level=level)

    async def get_auto_notch(self, receiver: int = 0) -> bool:
        """Get auto notch state (True = ON)."""
        result = await self._query("get_auto_notch")
        return bool(result["state"] == "1")

    async def set_auto_notch(self, state: bool, receiver: int = 0) -> None:
        """Set auto notch state."""
        await self._write("set_auto_notch", state="1" if state else "0")

    async def get_manual_notch(self, receiver: int = 0) -> tuple[bool, int]:
        """Get manual notch state and frequency index.

        Returns:
            Tuple of (enabled: bool, freq_index: int 0–255).
        """
        state_result = await self._query("get_manual_notch")
        freq_result = await self._query("get_manual_notch_freq")
        return bool(state_result["state"]), freq_result["freq"]

    async def set_manual_notch(self, state: bool, receiver: int = 0) -> None:
        """Set manual notch ON/OFF (BP00)."""
        await self._write("set_manual_notch", state=1 if state else 0)

    async def get_manual_notch_freq(self, receiver: int = 0) -> int:
        """Get manual notch frequency index (0–255, BP01).

        Standalone freq-only getter for symmetry with :meth:`set_manual_notch_freq`.
        Use :meth:`get_manual_notch` to fetch state+freq together in one call.
        """
        result = await self._query("get_manual_notch_freq")
        return int(result["freq"])

    async def set_manual_notch_freq(self, freq: int, receiver: int = 0) -> None:
        """Set manual notch frequency index (0–255, BP01)."""
        await self._write("set_manual_notch_freq", freq=freq)

    async def set_notch_filter(self, level: int, receiver: int = 0) -> None:
        """Set notch filter position (0–255).

        Cross-vendor alias delegating to Yaesu BP01
        (:meth:`set_manual_notch_freq`) — matches the Icom semantic of
        ``0x14 0x0D``.
        """
        await self.set_manual_notch_freq(level, receiver=receiver)

    async def get_notch_filter(self, receiver: int = 0) -> int:
        """Get notch filter position (0–255).

        Returns only the frequency index from the Yaesu manual-notch state
        tuple, mirroring the Icom ``0x14 0x0D`` read.
        """
        _, freq = await self.get_manual_notch(receiver=receiver)
        return int(freq)

    # -- D4: Filters --------------------------------------------------------

    def _filter_width_table(self, receiver: int = 0) -> tuple[int, ...] | None:
        """Return the filter-width table for the current mode, or None."""
        profile = self.profile
        if profile.filter_width_encoding != "table_index":
            return None
        target = self._state.receiver("SUB" if receiver else "MAIN")
        mode = getattr(target, "mode", None)
        rule = profile.resolve_filter_rule(mode)
        if rule and rule.table:
            return cast("tuple[int, ...]", rule.table)
        return None

    async def get_filter_width(self, receiver: int = 0) -> int:
        """Get filter width in Hz (SH0/SH1).

        Translates the radio's table-index code to Hz using the active
        profile's filter rule for the current mode. When no table is
        defined, the raw index is returned (compat fallback).

        Args:
            receiver: 0=MAIN, 1=SUB.

        Returns:
            Filter width in Hz.
        """
        cmd = "get_filter_width" if receiver == 0 else "get_filter_width_sub"
        result = await self._query(cmd)
        index = int(result["code"])
        target = self._state.receiver("SUB" if receiver else "MAIN")
        mode = getattr(target, "mode", None)
        rule = (
            self.profile.resolve_filter_rule(mode)
            if self.profile.filter_width_encoding == "table_index"
            else None
        )
        if rule and rule.fixed and rule.defaults:
            return int(rule.defaults[0])
        table = self._filter_width_table(receiver)
        if table is None:
            return index
        try:
            return int(table_index_to_hz(index, table=table))
        except ValueError:
            return index

    async def set_filter_width(self, width_hz: int, receiver: int = 0) -> None:
        """Set filter width in Hz (SH0/SH1).

        Translates Hz to the radio's table-index code using the active
        profile's filter rule for the current mode. When no table is
        defined, ``width_hz`` is sent as the raw index (compat fallback).
        """
        cmd = "set_filter_width" if receiver == 0 else "set_filter_width_sub"
        table = self._filter_width_table(receiver)
        index = width_hz if table is None else hz_to_table_index(width_hz, table=table)
        await self._write(cmd, code=index)

    async def get_if_shift(self, receiver: int = 0) -> int:
        """Get IF shift offset in Hz (signed, IS0).

        Returns:
            Signed offset in Hz (negative = downshift).
        """
        result = await self._query("get_if_shift")
        offset: int = result["offset"]
        return -offset if result["sign"] == "-" else offset

    async def set_if_shift(self, offset: int, receiver: int = 0) -> None:
        """Set IF shift offset in Hz (signed, IS0)."""
        sign = "+" if offset >= 0 else "-"
        await self._write("set_if_shift", sign=sign, offset=abs(offset))

    async def get_narrow(self, receiver: int = 0) -> bool:
        """Get narrow filter state (True = narrow)."""
        result = await self._query("get_narrow")
        return bool(result["state"] == "1")

    async def set_narrow(self, state: bool, receiver: int = 0) -> None:
        """Set narrow filter state."""
        await self._write("set_narrow", state="1" if state else "0")

    # -- D5: Split/Dual Watch -----------------------------------------------

    async def get_rx_func(self) -> int:
        """Get RX function (0 = Dual RX, 1 = Single RX)."""
        result = await self._query("get_rx_func")
        return int(result["mode"])

    async def set_rx_func(self, mode: int) -> None:
        """Set RX function (0 = Dual RX, 1 = Single RX)."""
        await self._write("set_rx_func", mode=mode)

    async def get_tx_func(self) -> int:
        """Get TX function (0 = MAIN TX, 1 = SUB TX)."""
        result = await self._query("get_tx_func")
        return int(result["vfo"])

    async def set_tx_func(self, vfo: int) -> None:
        """Set TX function (0 = MAIN, 1 = SUB)."""
        await self._write("set_tx_func", vfo=str(vfo))

    # -- TransceiverBankCapable --------------------------------------------

    @property
    def transceiver_count(self) -> int:
        """Number of independent transceivers exposed by this rig.

        FTX-1 is wired as two independent transceivers (HF+50 MHz and
        144+430 MHz) sharing a single control head; other Yaesu CAT rigs
        are single-transceiver.
        """
        return 2 if self._config.id == "yaesu_ftx1" else 1

    async def set_tx_source(self, xcvr: int) -> None:
        """Make ``xcvr`` the active TX transceiver (FTX-1 ``FT`` command).

        Args:
            xcvr: 0 = MAIN-side transmitter, 1 = SUB-side transmitter.
        """
        if xcvr not in (0, 1):
            raise ValueError(
                f"xcvr must be 0 or 1, got {xcvr!r} "
                f"(transceiver_count={self.transceiver_count})"
            )
        await self._write("set_tx_func", vfo=str(xcvr))

    async def get_tx_source(self) -> int:
        """Return the 0-based index of the currently active TX transceiver.

        Parses ``FT0;`` / ``FT1;`` from the radio (``FT`` command).
        """
        result = await self._query("get_tx_func")
        return int(result["vfo"])

    async def set_cross_band_split(self, rx_xcvr: int, tx_xcvr: int) -> None:
        """Route RX to ``rx_xcvr`` and TX to ``tx_xcvr`` for cross-band split.

        Emits three CAT commands in sequence:

        1. ``FR00;`` — enable Dual RX so both transceivers receive.
           The FTX-1 ``FR`` command (Function RX) uses a 2-digit mode code:
           ``00`` = Dual RX, ``01`` = Single RX.  It is **not** a transceiver
           selector; the single-digit form ``FR0;`` is not valid on FTX-1.
        2. ``VS{rx_xcvr};`` — focus the active receiver on ``rx_xcvr`` so
           S-meter reads and audio routing stay on the intended receive band.
        3. ``FT{tx_xcvr};`` — route TX to ``tx_xcvr``.

        Args:
            rx_xcvr: Primary receive transceiver (0 = MAIN HF/50 MHz, 1 = SUB 144/430 MHz).
            tx_xcvr: Transmit transceiver (0 = MAIN, 1 = SUB).

        Raises:
            ValueError: If ``rx_xcvr == tx_xcvr`` (same transceiver is regular
                split, not cross-band) or if either index is out of range.
        """
        count = self.transceiver_count
        if rx_xcvr < 0 or rx_xcvr >= count:
            raise ValueError(
                f"rx_xcvr {rx_xcvr} out of range for transceiver_count={count}"
            )
        if tx_xcvr < 0 or tx_xcvr >= count:
            raise ValueError(
                f"tx_xcvr {tx_xcvr} out of range for transceiver_count={count}"
            )
        if rx_xcvr == tx_xcvr:
            raise ValueError(
                f"cross-band split requires different rx_xcvr and tx_xcvr; "
                f"got rx={rx_xcvr}, tx={tx_xcvr}"
            )
        await self.set_rx_func(0)  # FR00: Dual RX
        await self.select_receiver(rx_xcvr)  # VS{rx_xcvr}: focus active receiver
        await self.set_tx_source(tx_xcvr)  # FT{tx_xcvr}: route TX

    async def get_split(self) -> bool:
        """Get split operation state."""
        result = await self._query("get_split")
        return bool(result["state"] == "1")

    async def set_split(self, state: bool) -> None:
        """Set split operation state."""
        await self._write("set_split", state="1" if state else "0")

    async def get_vfo_select(self) -> int:
        """Get VFO selection (0 = MAIN, 1 = SUB)."""
        result = await self._query("get_vfo_select")
        return int(result["vfo"])

    async def set_vfo_select(self, vfo: int) -> None:
        """Set VFO selection (0 = MAIN, 1 = SUB)."""
        await self._write("set_vfo_select", vfo=str(vfo))

    async def vfo_a_to_b(self) -> None:
        """Copy VFO-A to VFO-B."""
        await self._write("vfo_a_to_b")

    async def vfo_b_to_a(self) -> None:
        """Copy VFO-B to VFO-A."""
        await self._write("vfo_b_to_a")

    # -- ReceiverBankCapable -----------------------------------------------

    @property
    def receiver_count(self) -> int:
        """Number of independent receivers exposed by this rig.

        Profile-driven via ``[radio] receiver_count`` in the rig TOML.
        FTX-1 reports ``2`` (MAIN + SUB); single-RX Yaesu CAT profiles
        (e.g. FT-710, FT-991A — when added) report ``1``.
        """
        return int(self._config.receiver_count)

    @staticmethod
    def _normalize_receiver(which: int | str) -> int:
        """Normalize a ``select_receiver`` argument to a 0-based index.

        Accepts integer indices (``0`` / ``1``) or case-insensitive names
        (``"main"`` / ``"sub"``).  Raises :class:`ValueError` for any other
        value.
        """
        if isinstance(which, str):
            key = which.strip().lower()
            if key == "main":
                return 0
            if key == "sub":
                return 1
            raise ValueError(
                f"select_receiver: unknown receiver name {which!r} "
                "(expected 'main' or 'sub')"
            )
        if isinstance(which, bool) or not isinstance(which, int):
            raise ValueError(
                f"select_receiver: which must be int or str, got {type(which).__name__}"
            )
        return int(which)

    async def select_receiver(self, which: int | str) -> None:
        """Make ``which`` the active receiver for subsequent commands.

        On dual-RX Yaesu CAT rigs (FTX-1) issues ``VS{0|1};`` via the
        existing ``set_vfo_select`` template.  On single-RX profiles only
        ``which == 0`` is accepted and the call is a no-op (matching the
        :class:`~rigplane.radio_protocol.ReceiverBankCapable` contract).
        """
        index = self._normalize_receiver(which)
        count = self.receiver_count
        if index < 0 or index >= count:
            raise ValueError(
                f"select_receiver: receiver index {index} out of range "
                f"for receiver_count={count}"
            )
        if count <= 1:
            # Single-RX: nothing to switch.
            return
        await self._write("set_vfo_select", vfo=str(index))

    async def get_active_receiver(self) -> int:
        """Return the index of the currently active receiver.

        Reads ``VS;`` on dual-RX rigs; returns ``0`` on single-RX profiles.
        """
        if self.receiver_count <= 1:
            return 0
        result = await self._query("get_vfo_select")
        return int(result["vfo"])

    # -- VfoSlotCapable ----------------------------------------------------

    def _vfo_slot_supported(self) -> bool:
        """``True`` when the active profile exposes per-receiver A/B slots.

        The FTX-1 family uses ``vfo_scheme = "ab_shared"`` — there is no
        per-receiver A/B pair (each receiver has a single VFO addressed
        via ``FA;``/``FB;``).  Single-RX Yaesu CAT rigs use
        ``vfo_scheme = "ab"`` and route ``FR{vfo};`` through the shared
        ``set_vfo_select`` / ``get_vfo_select`` plumbing.
        """
        return bool(self._config.vfo_scheme == "ab")

    @staticmethod
    def _slot_to_index(slot: str) -> int:
        """Convert ``"A"`` / ``"B"`` (case-insensitive) to ``0`` / ``1``."""
        if not isinstance(slot, str):
            raise ValueError(f"slot must be str, got {type(slot).__name__}")
        norm = slot.strip().upper()
        if norm == "A":
            return 0
        if norm == "B":
            return 1
        raise ValueError(f"slot must be 'A' or 'B', got {slot!r}")

    def _check_single_receiver(self, receiver: int, *, operation: str) -> None:
        count = self.receiver_count
        if receiver < 0 or receiver >= count:
            raise ValueError(
                f"{operation}: receiver index {receiver} out of range "
                f"for receiver_count={count}"
            )

    async def get_vfo_slot(self, receiver: int = 0) -> str:
        """Return the active VFO slot (``"A"`` or ``"B"``) for ``receiver``.

        Raises :class:`NotImplementedError` on FTX-1 (``ab_shared`` scheme):
        the FTX-1 has no per-receiver A/B pair.  Use
        :meth:`select_receiver` for MAIN/SUB switching instead.
        """
        self._check_single_receiver(receiver, operation="get_vfo_slot")
        if not self._vfo_slot_supported():
            raise NotImplementedError(
                f"get_vfo_slot not supported on {self.model} "
                f"(vfo_scheme={self._config.vfo_scheme!r}); "
                "use select_receiver() for MAIN/SUB on dual-RX Yaesu rigs"
            )
        result = await self._query("get_vfo_select")
        return "B" if int(result["vfo"]) == 1 else "A"

    async def set_vfo_slot(self, slot: str, receiver: int = 0) -> None:
        """Make ``slot`` (``"A"`` or ``"B"``) the active VFO on ``receiver``.

        Raises :class:`NotImplementedError` on FTX-1 (``ab_shared`` scheme).
        """
        self._check_single_receiver(receiver, operation="set_vfo_slot")
        index = self._slot_to_index(slot)
        if not self._vfo_slot_supported():
            raise NotImplementedError(
                f"set_vfo_slot not supported on {self.model} "
                f"(vfo_scheme={self._config.vfo_scheme!r}); "
                "use select_receiver() for MAIN/SUB on dual-RX Yaesu rigs"
            )
        await self._write("set_vfo_select", vfo=str(index))

    async def swap_vfo_ab(self, receiver: int = 0) -> None:
        """Swap VFO A and VFO B state on ``receiver``.

        Yaesu CAT has no symmetric A↔B swap primitive: FTX-1 ``AB;``/
        ``BA;`` are MAIN→SUB / SUB→MAIN copies (one-way), and Lab599-style
        single-RX profiles do not expose a swap command at all.  This
        method therefore raises :class:`NotImplementedError` on every
        currently supported Yaesu CAT rig.
        """
        self._check_single_receiver(receiver, operation="swap_vfo_ab")
        raise NotImplementedError(
            f"swap_vfo_ab not supported on {self.model}: "
            "Yaesu CAT has no symmetric A↔B swap primitive"
        )

    async def equalize_vfo_ab(self, receiver: int = 0) -> None:
        """Copy the active VFO's state to the inactive VFO on ``receiver``.

        Not supported by Yaesu CAT: FTX-1 ``AB;``/``BA;`` copy between
        receivers (MAIN↔SUB), not between A/B within a receiver, and
        single-RX Yaesu profiles do not expose an equalize command.
        Raises :class:`NotImplementedError`.
        """
        self._check_single_receiver(receiver, operation="equalize_vfo_ab")
        raise NotImplementedError(
            f"equalize_vfo_ab not supported on {self.model}: "
            "Yaesu CAT has no per-receiver A→B copy primitive"
        )

    # -- D6: TX Stack -------------------------------------------------------

    async def get_power(self) -> tuple[int, int]:
        """Get TX power setting.

        Returns:
            Tuple of (head: int, watts: int).
        """
        result = await self._query("get_power")
        return int(result["head"]), result["watts"]

    async def set_power(self, watts: int, head: int = 2) -> None:
        """Set TX power.

        Args:
            watts: Power in watts.
            head: Head selector (default 2).
        """
        await self._write("set_power", head=str(head), watts=watts)

    async def set_rf_power(self, level: int) -> None:
        """Set TX power level — :class:`PowerControlCapable` interface.

        Yaesu's :attr:`native_power_unit` is ``"watts"``, so ``level`` is
        interpreted as watts directly. Internally delegates to
        :meth:`set_power` with the default ``head=2`` selector.
        """
        await self.set_power(watts=level)

    async def get_mic_gain(self) -> int:
        """Get microphone gain (0–100)."""
        result = await self._query("get_mic_gain")
        return int(result["level"])

    async def set_mic_gain(self, level: int) -> None:
        """Set microphone gain (0–100)."""
        await self._write("set_mic_gain", level=level)

    async def get_processor(self) -> bool:
        """Get speech processor state."""
        result = await self._query("get_processor")
        return bool(result["state"] == "1")

    async def set_processor(self, state: bool) -> None:
        """Set speech processor state."""
        await self._write("set_processor", state="1" if state else "0")

    async def get_processor_level(self) -> int:
        """Get processor level (0-100)."""
        result = await self._query("get_processor_level")
        return int(result["level"])

    async def set_processor_level(self, level: int) -> None:
        """Set processor level (0-100)."""
        await self._write("set_processor_level", level=level)

    async def get_monitor_on(self) -> bool:
        """Get monitor ON/OFF state — not supported on FTX-1."""
        raise NotImplementedError("Monitor not supported on this radio")

    async def set_monitor_on(self, state: bool) -> None:
        """Set monitor ON/OFF — not supported on FTX-1."""
        raise NotImplementedError("Monitor not supported on this radio")

    async def get_monitor_level(self) -> int:
        """Get monitor level — not supported on FTX-1."""
        raise NotImplementedError("Monitor not supported on this radio")

    async def set_monitor_level(self, level: int) -> None:
        """Set monitor level — not supported on FTX-1."""
        raise NotImplementedError("Monitor not supported on this radio")

    # -- D7: CW -------------------------------------------------------------

    async def get_keyer_speed(self) -> int:
        """Get CW keyer speed in WPM (4–60)."""
        result = await self._query("get_keyer_speed")
        return int(result["wpm"])

    async def set_keyer_speed(self, wpm: int) -> None:
        """Set CW keyer speed in WPM (4–60)."""
        await self._write("set_keyer_speed", wpm=wpm)

    async def get_key_pitch(self) -> int:
        """Get CW pitch index (0–75, maps to 300–1050 Hz)."""
        result = await self._query("get_key_pitch")
        return int(result["idx"])

    async def set_key_pitch(self, idx: int) -> None:
        """Set CW pitch index (0–75)."""
        await self._write("set_key_pitch", idx=idx)

    async def get_break_in(self) -> BreakInMode:
        """Get CW break-in mode.

        FTX-1 CAT exposes only binary on/off; map ``"1"`` to
        :attr:`BreakInMode.SEMI` and ``"0"`` to :attr:`BreakInMode.OFF`.
        :class:`BreakInMode` is an :class:`IntEnum` and remains
        bool-compatible at runtime.
        """
        result = await self._query("get_break_in")
        return BreakInMode.SEMI if result["state"] == "1" else BreakInMode.OFF

    async def set_break_in(self, mode: BreakInMode | int | bool) -> None:
        """Set CW break-in mode.

        FTX-1 CAT supports binary on/off only — :attr:`BreakInMode.OFF`
        maps to ``"0"`` and any non-OFF value (``SEMI``/``FULL``) maps
        to ``"1"``. ``bool`` values remain accepted for backward
        compatibility (``False``/``True`` → ``OFF``/``SEMI``).
        """
        on = BreakInMode(int(mode)) != BreakInMode.OFF
        await self._write("set_break_in", state="1" if on else "0")

    async def get_cw_spot(self) -> bool:
        """Get CW spot tone state."""
        result = await self._query("get_cw_spot")
        return bool(result["state"] == "1")

    async def set_cw_spot(self, state: bool) -> None:
        """Set CW spot tone state."""
        await self._write("set_cw_spot", state="1" if state else "0")

    async def send_cw(self, msg_type: str, mem: str) -> None:
        """Send a CW message (KY command).

        Args:
            msg_type: Message type character.
            mem: CW message text to send.
        """
        await self._write("send_cw", type=msg_type, mem=mem)

    async def get_break_in_delay(self) -> int:
        """Get CW break-in delay in milliseconds (30–3000)."""
        result = await self._query("get_break_in_delay")
        return int(result["delay"])

    async def set_break_in_delay(self, delay: int) -> None:
        """Set CW break-in delay in milliseconds (30–3000)."""
        await self._write("set_break_in_delay", delay=delay)

    # -- D8: Clarifier (RIT/XIT) --------------------------------------------

    async def get_clarifier(self, receiver: int = 0) -> tuple[bool, bool]:
        """Get clarifier state (CF000).

        Returns:
            Tuple of (rx_clar: bool, tx_clar: bool).
        """
        result = await self._query("get_clarifier")
        return result["rx"] == "1", result["tx"] == "1"

    async def set_clarifier(
        self, rx_clar: bool, tx_clar: bool, receiver: int = 0
    ) -> None:
        """Set clarifier RX/TX state (CF000)."""
        await self._write(
            "set_clarifier",
            rx="1" if rx_clar else "0",
            tx="1" if tx_clar else "0",
            pad=0,
        )

    async def get_clarifier_freq(self, receiver: int = 0) -> int:
        """Get clarifier offset frequency in Hz (signed, CF001)."""
        result = await self._query("get_clarifier_freq")
        offset: int = result["offset"]
        return -offset if result["sign"] == "-" else offset

    async def set_clarifier_freq(self, offset: int, receiver: int = 0) -> None:
        """Set clarifier offset frequency in Hz (signed, CF001)."""
        sign = "+" if offset >= 0 else "-"
        await self._write("set_clarifier_freq", sign=sign, offset=abs(offset))

    # -- Canonical RIT/XIT surface (RitXitCapable) --------------------------
    # Cross-vendor names that delegate to the *_clarifier* CAT helpers.
    # set_rit_status / set_rit_tx_status do read-modify-write on CF000 so a
    # single-bit setter does not clobber the other bit (P1-02 fix).

    async def get_rit_frequency(self) -> int:
        """Get RIT frequency offset in Hz (signed)."""
        return await self.get_clarifier_freq()

    async def set_rit_frequency(self, freq_hz: int) -> None:
        """Set RIT frequency offset in Hz (signed). Fire-and-forget."""
        await self.set_clarifier_freq(freq_hz)

    async def get_rit_status(self) -> bool:
        """Get RIT (RX clarifier) on/off status."""
        rx_clar, _tx_clar = await self.get_clarifier()
        return rx_clar

    async def set_rit_status(self, on: bool) -> None:
        """Set RIT (RX clarifier) on/off — preserves XIT bit (read-modify-write)."""
        _rx_clar, tx_clar = await self.get_clarifier()
        await self.set_clarifier(rx_clar=on, tx_clar=tx_clar)

    async def get_rit_tx_status(self) -> bool:
        """Get RIT TX / XIT (TX clarifier) on/off status."""
        _rx_clar, tx_clar = await self.get_clarifier()
        return tx_clar

    async def set_rit_tx_status(self, on: bool) -> None:
        """Set RIT TX / XIT on/off — preserves RIT bit (read-modify-write)."""
        rx_clar, _tx_clar = await self.get_clarifier()
        await self.set_clarifier(rx_clar=rx_clar, tx_clar=on)

    # -- D9: Tone/TSQL ------------------------------------------------------

    async def get_sql_type(self, receiver: int = 0) -> int:
        """Get squelch type code (CT0)."""
        result = await self._query("get_sql_type")
        return int(result["type"])

    async def set_sql_type(self, type_code: int, receiver: int = 0) -> None:
        """Set squelch type code (CT0)."""
        await self._write("set_sql_type", type=type_code)

    # -- D10: System --------------------------------------------------------

    async def get_id(self) -> str:
        """Get radio model ID string (e.g. '0840')."""
        result = await self._query("get_id")
        return str(result["model"]).zfill(4)

    async def get_auto_info(self) -> bool:
        """Get auto-info (AI) state."""
        result = await self._query("get_auto_info")
        return bool(result["state"] == "1")

    async def set_auto_info(self, state: bool) -> None:
        """Set auto-info (AI) state."""
        await self._write("set_auto_info", state="1" if state else "0")

    async def get_vox(self) -> bool:
        """Get VOX state."""
        result = await self._query("get_vox")
        return bool(result["state"] == "1")

    async def set_vox(self, state: bool) -> None:
        """Set VOX state."""
        await self._write("set_vox", state="1" if state else "0")

    async def get_lock(self) -> bool:
        """Get dial lock state."""
        result = await self._query("get_lock")
        return bool(result["state"] == "1")

    async def set_lock(self, state: bool) -> None:
        """Set dial lock state."""
        await self._write("set_lock", state="1" if state else "0")

    # -- Tuner (AC) --------------------------------------------------------

    async def get_tuner(self) -> int:
        """Get antenna tuner state (AC).

        Returns:
            0=OFF, 1=ON, 2=tuning, 3=tune-start.
        """
        result = await self._query("get_tuner")
        return int(result["state"])

    async def set_tuner(self, state: int, src: int = 0, typ: int = 0) -> None:
        """Set antenna tuner (AC). state: 0=OFF, 1=ON, 2=tune."""
        await self._write("set_tuner", src=str(src), type=str(typ), state=str(state))

    # -- Contour / S-DX (CO) -----------------------------------------------

    async def get_contour(self, receiver: int = 0) -> int:
        """Get contour (S-DX) on/off state (CO rx 0).

        Returns:
            0=OFF, >0 = ON (value).
        """
        result = await self._query("get_contour")
        return int(result["val"])

    async def set_contour(self, val: int, receiver: int = 0) -> None:
        """Set contour (S-DX) on/off. 0=OFF, 1=ON."""
        await self._write("set_contour", val=val)

    # -- APF (Audio Peak Filter, CO02/CO03) ------------------------------------

    async def get_apf(self, receiver: int = 0) -> bool:
        """Get APF on/off state (CO02). Returns True if APF is on."""
        result = await self._query("get_apf")
        return int(result["val"]) != 0

    async def set_apf(self, on: bool, receiver: int = 0) -> None:
        """Set APF on/off (CO02). 0=OFF, 1=ON."""
        await self._write("set_apf", val=1 if on else 0)

    async def get_apf_freq(self, receiver: int = 0) -> int:
        """Get APF centre frequency (CO03)."""
        result = await self._query("get_apf_freq")
        return int(result["val"])

    async def set_apf_freq(self, freq: int, receiver: int = 0) -> None:
        """Set APF centre frequency (CO03)."""
        await self._write("set_apf_freq", val=freq)

    # -- Clarifier Reset (RC) --------------------------------------------------

    async def reset_clarifier(self, receiver: int = 0) -> None:
        """Reset clarifier offset to zero (RC). Fire-and-forget."""
        await self._write("reset_clarifier")

    async def set_band(self, band: int, receiver: int = 0) -> None:
        """Set current band by index (BS, write-only on FTX-1).

        Note: FTX-1 does not support BS read (returns ?;).
        Band values: 00=1.8M, 01=3.5M, 02=5M, 03=7M, 04=10M,
        05=14M, 06=18M, 07=21M, 08=24.5M, 09=28M, 10=50M,
        11=70M/GEN, 12=AIR, 13=144M, 14=430M.
        """
        cmd = "set_band" if receiver == 0 else "set_band_sub"
        await self._write(cmd, band=band)

    async def band_up(self, receiver: int = 0) -> None:
        """Step up one band (BU0)."""
        await self._write("band_up")

    async def band_down(self, receiver: int = 0) -> None:
        """Step down one band (BD0)."""
        await self._write("band_down")

    # -- AGC ----------------------------------------------------------------

    async def get_agc(self, receiver: int = 0) -> int:
        """Get AGC mode (GT0).

        Returns:
            0=OFF, 1=FAST, 2=MID, 3=SLOW, 4=AUTO-F, 5=AUTO-M, 6=AUTO-S.
        """
        result = await self._query("get_agc")
        return int(result["mode"])

    async def set_agc(self, mode: int, receiver: int = 0) -> None:
        """Set AGC mode (GT0, 0–6)."""
        await self._write("set_agc", mode=str(mode))

    # -- Key speed (KS) -------------------------------------------------------

    async def get_key_speed(self) -> int:
        """Get CW keyer speed in WPM (KS)."""
        result = await self._query("get_keyer_speed")
        return int(result["wpm"])

    async def set_key_speed(self, speed: int) -> None:
        """Set CW keyer speed in WPM (KS)."""
        await self._write("set_keyer_speed", wpm=speed)

    # -- AdvancedControlCapable aliases ----------------------------------------

    async def get_cw_pitch(self) -> int:
        """CW pitch in Hz (300-1050).

        ``get_key_pitch`` is the Yaesu-internal helper and returns the FTX-1
        idx (0-75). The Icom-spelled ``CwControlCapable`` contract is Hz, so
        we map ``idx → 300 + idx * 10`` (FTX-1 documented mapping: 0=300 Hz,
        75=1050 Hz, 10 Hz step).
        """
        idx = await self.get_key_pitch()
        return 300 + idx * 10

    async def set_cw_pitch(self, freq: int) -> None:
        """Set CW pitch in Hz (300-1050).

        Maps Hz to FTX-1's 0-75 idx (10 Hz step). Raises ``ValueError`` on
        out-of-range input.
        """
        if not 300 <= freq <= 1050:
            raise ValueError(f"CW pitch must be 300-1050 Hz, got {freq}")
        idx = (freq - 300) // 10
        await self.set_key_pitch(idx)

    async def get_dial_lock(self) -> bool:
        """Alias for AdvancedControlCapable compatibility."""
        return await self.get_lock()

    async def set_dial_lock(self, on: bool) -> None:
        """Alias for AdvancedControlCapable compatibility."""
        await self.set_lock(on)

    async def get_compressor(self) -> bool:
        """Alias for VoiceControlCapable compatibility."""
        return await self.get_processor()

    async def set_compressor(self, on: bool) -> None:
        """Alias for AdvancedControlCapable compatibility."""
        await self.set_processor(on)

    async def get_tuner_status(self) -> int:
        """AdvancedControlCapable alias. Returns tuner state (0=OFF, 1=ON, 2=tuning)."""
        return await self.get_tuner()

    async def set_tuner_status(self, value: int) -> None:
        """AdvancedControlCapable alias."""
        await self.set_tuner(value)

    async def send_cw_text(self, text: str) -> None:
        """Send CW text via keyer (KY command), split into 24-character chunks.

        Uses ``send_cw(" ", text)`` — the space is the P1 type parameter
        (keyboard input), not a separator.  Wire format: ``KY {text};``.

        Args:
            text: CW text to send (A-Z, 0-9, punctuation).
        """
        if not text:
            await self.send_cw(" ", "")
            return
        chunk_size = 24
        for i in range(0, len(text), chunk_size):
            await self.send_cw(" ", text[i : i + chunk_size])

    async def stop_cw_text(self) -> None:
        """Stop CW sending by clearing the keyer buffer."""
        await self.send_cw(" ", "")

    # -- Icom-only stubs (AdvancedControlCapable conformance) -----------------
    # Yaesu does not support these features; methods exist for protocol compat.

    async def get_antenna_1(self) -> bool:
        raise NotImplementedError("Antenna switching not supported on Yaesu radios")

    async def set_antenna_1(self, on: bool) -> None:
        raise NotImplementedError("Antenna switching not supported on Yaesu radios")

    async def get_antenna_2(self) -> bool:
        raise NotImplementedError("Antenna switching not supported on Yaesu radios")

    async def set_antenna_2(self, on: bool) -> None:
        raise NotImplementedError("Antenna switching not supported on Yaesu radios")

    async def get_rx_antenna_ant1(self) -> bool:
        raise NotImplementedError("RX antenna switching not supported on Yaesu radios")

    async def set_rx_antenna_ant1(self, on: bool) -> None:
        raise NotImplementedError("RX antenna switching not supported on Yaesu radios")

    async def get_rx_antenna_ant2(self) -> bool:
        raise NotImplementedError("RX antenna switching not supported on Yaesu radios")

    async def set_rx_antenna_ant2(self, on: bool) -> None:
        raise NotImplementedError("RX antenna switching not supported on Yaesu radios")

    async def get_pbt_inner(self, receiver: int = 0) -> int:
        raise NotImplementedError("PBT (Icom) not supported on Yaesu radios")

    async def set_pbt_inner(self, level: int, receiver: int = 0) -> None:
        raise NotImplementedError("PBT (Icom) not supported on Yaesu radios")

    async def get_pbt_outer(self, receiver: int = 0) -> int:
        raise NotImplementedError("PBT (Icom) not supported on Yaesu radios")

    async def set_pbt_outer(self, level: int, receiver: int = 0) -> None:
        raise NotImplementedError("PBT (Icom) not supported on Yaesu radios")

    async def set_digisel(self, on: bool, receiver: int = 0) -> None:
        raise NotImplementedError("DIGI-SEL (Icom) not supported on Yaesu radios")

    async def set_ip_plus(self, on: bool, receiver: int = 0) -> None:
        raise NotImplementedError("IP+ (Icom) not supported on Yaesu radios")

    async def set_filter(self, filter_num: int, receiver: int = 0) -> None:
        raise NotImplementedError("Filter select (Icom) not supported on Yaesu radios")

    async def get_filter(self, receiver: int = 0) -> int | None:
        """Yaesu rigs do not expose discrete FIL1/2/3 — return ``None``."""
        return None

    async def set_filter_shape(self, shape: int, receiver: int = 0) -> None:
        raise NotImplementedError("Filter shape (Icom) not supported on Yaesu radios")

    async def get_attenuator_level(self, receiver: int = 0) -> int:
        raise NotImplementedError(
            "Attenuator level (Icom) not supported on Yaesu radios"
        )

    async def set_acc1_mod_level(self, level: int) -> None:
        raise NotImplementedError("ACC1 mod level (Icom) not supported on Yaesu radios")

    async def set_usb_mod_level(self, level: int) -> None:
        raise NotImplementedError("USB mod level (Icom) not supported on Yaesu radios")

    async def set_lan_mod_level(self, level: int) -> None:
        raise NotImplementedError("LAN mod level (Icom) not supported on Yaesu radios")

    async def get_system_date(self) -> tuple[int, int, int]:
        raise NotImplementedError("System clock (Icom) not supported on Yaesu radios")

    async def set_system_date(self, year: int, month: int, day: int) -> None:
        raise NotImplementedError("System clock (Icom) not supported on Yaesu radios")

    async def get_system_time(self) -> tuple[int, int]:
        raise NotImplementedError("System clock (Icom) not supported on Yaesu radios")

    async def set_system_time(self, hour: int, minute: int) -> None:
        raise NotImplementedError("System clock (Icom) not supported on Yaesu radios")

    async def get_vox_gain(self) -> int:
        raise NotImplementedError("VOX gain not supported on this radio")

    async def set_vox_gain(self, level: int) -> None:
        raise NotImplementedError("VOX gain not supported on this radio")

    async def get_anti_vox_gain(self) -> int:
        raise NotImplementedError("Anti-VOX gain not supported on this radio")

    async def set_anti_vox_gain(self, level: int) -> None:
        raise NotImplementedError("Anti-VOX gain not supported on this radio")

    async def get_monitor(self) -> bool:
        raise NotImplementedError("Monitor (Icom) not supported on Yaesu radios")

    async def set_monitor(self, on: bool) -> None:
        raise NotImplementedError("Monitor (Icom) not supported on Yaesu radios")

    async def get_monitor_gain(self) -> int:
        raise NotImplementedError("Monitor gain (Icom) not supported on Yaesu radios")

    async def set_monitor_gain(self, level: int) -> None:
        raise NotImplementedError("Monitor gain (Icom) not supported on Yaesu radios")

    async def get_dual_watch(self) -> bool:
        raise NotImplementedError("Dual watch query not supported on Yaesu radios")

    # -- Audio Peak Filter (canonical 3-mode adapter over Yaesu bool APF) -----

    async def set_audio_peak_filter(self, mode: int, receiver: int = 0) -> None:
        """Set Audio Peak Filter via the canonical 3-mode contract.

        The cross-vendor contract is `mode = 0=off, 1=soft, 2=sharp`
        (`DspControlCapable.set_audio_peak_filter`). Yaesu hardware exposes
        APF as a plain on/off toggle (CO02) plus a tunable centre frequency
        (CO03), so this adapter degrades the 3-mode form onto the bool form:

        - mode 0 → APF off.
        - mode 1 → APF on. The centre frequency is *not* touched here so
          a previously-tuned APF frequency survives an off/on toggle. Set
          it explicitly via `set_apf_freq()` if a particular pitch is
          required.
        - mode 2 → not supported; the hardware has no separate "sharp" mode.

        Delegates to the backend-internal `set_apf` CAT primitive.
        """
        if mode == 0:
            await self.set_apf(False, receiver=receiver)
        elif mode == 1:
            await self.set_apf(True, receiver=receiver)
        else:
            raise NotImplementedError(
                f"APF mode {mode} (sharp) not supported on Yaesu — "
                "only off (0) and on (1)"
            )

    async def get_audio_peak_filter(self, receiver: int = 0) -> int:
        raise NotImplementedError("APF not supported on Yaesu radios")

    # -- Twin Peak Filter (not supported on Yaesu) ----------------------------

    async def set_twin_peak_filter(self, on: bool, receiver: int = 0) -> None:
        raise NotImplementedError("Twin Peak not supported on Yaesu radios")

    async def get_twin_peak_filter(self, receiver: int = 0) -> bool:
        raise NotImplementedError("Twin Peak not supported on Yaesu radios")

    # -- SSB TX bandwidth (not supported) -------------------------------------

    async def set_ssb_tx_bandwidth(self, value: int) -> None:
        raise NotImplementedError("SSB TX bandwidth not supported on this radio")

    async def get_ssb_tx_bandwidth(self) -> int:
        raise NotImplementedError("SSB TX bandwidth not supported on this radio")

    # -- Manual notch width (not supported) -----------------------------------

    async def set_manual_notch_width(self, value: int, receiver: int = 0) -> None:
        raise NotImplementedError("Manual notch width not supported on this radio")

    async def get_manual_notch_width(self, receiver: int = 0) -> int:
        raise NotImplementedError("Manual notch width not supported on this radio")

    # -- VOX delay (not supported) --------------------------------------------

    async def set_vox_delay(self, level: int) -> None:
        raise NotImplementedError("VOX delay not supported on this radio")

    async def get_vox_delay(self) -> int:
        raise NotImplementedError("VOX delay not supported on this radio")

    # -- NB depth / width (not supported) -------------------------------------

    async def set_nb_depth(self, level: int, receiver: int = 0) -> None:
        raise NotImplementedError("NB depth not supported on this radio")

    async def get_nb_depth(self, receiver: int = 0) -> int:
        raise NotImplementedError("NB depth not supported on this radio")

    async def set_nb_width(self, level: int, receiver: int = 0) -> None:
        raise NotImplementedError("NB width not supported on this radio")

    async def get_nb_width(self, receiver: int = 0) -> int:
        raise NotImplementedError("NB width not supported on this radio")

    # -- CW dash ratio (not supported) ----------------------------------------

    async def set_dash_ratio(self, value: int) -> None:
        raise NotImplementedError("CW dash ratio not supported on this radio")

    async def get_dash_ratio(self) -> int:
        raise NotImplementedError("CW dash ratio not supported on this radio")

    # -- Main/Sub tracking (single-receiver Yaesu) ----------------------------

    async def set_main_sub_tracking(self, on: bool) -> None:
        raise NotImplementedError("Main/Sub tracking not supported on this radio")

    async def get_main_sub_tracking(self) -> bool:
        raise NotImplementedError("Main/Sub tracking not supported on this radio")

    # -- Memory (not supported on Yaesu) ----------------------------------------

    async def set_memory_mode(self, channel: int) -> None:
        raise NotImplementedError("Memory mode not supported on this radio")

    async def memory_write(self) -> None:
        raise NotImplementedError("Memory write not supported on this radio")

    async def memory_to_vfo(self, channel: int) -> None:
        raise NotImplementedError("Memory to VFO not supported on this radio")

    async def memory_clear(self, channel: int) -> None:
        raise NotImplementedError("Memory clear not supported on this radio")

    async def set_memory_contents(self, mem: "MemoryChannel") -> None:
        raise NotImplementedError("Memory contents not supported on this radio")

    async def set_bsr(self, bsr: "BandStackRegister") -> None:
        raise NotImplementedError("Band stack register not supported on this radio")

    def create_state_poller(
        self,
        *,
        callback: Callable[[RadioState], None],
        command_queue: "CommandQueue | None" = None,
    ) -> "YaesuCatPoller":
        """Construct a request-response state poller for this radio.

        Used by the web layer to drive periodic state-change broadcasts
        without depending on backend internals. Returns a
        :class:`YaesuCatPoller` instance — the caller is responsible
        for awaiting/spawning ``.start()``.

        The lazy import keeps :class:`YaesuCatRadio` from depending on
        its own poller at module-load time (the poller imports the
        radio, so a top-level import here would be a cycle).

        Args:
            callback: Invoked with the current :class:`RadioState`
                after every successful poll.
            command_queue: Optional outbound command queue drained on
                each poll cycle.

        Returns:
            A :class:`YaesuCatPoller` bound to this radio.
        """
        from .poller import YaesuCatPoller

        return YaesuCatPoller(
            self,
            callback=callback,
            command_queue=command_queue,
        )

    def rigctld_routing(
        self,
        cache: Any,
        max_power_w: float = 100.0,
    ) -> Any:
        """Construct a Yaesu-specific rigctld routing strategy.

        Returns a :class:`~rigplane.rigctld.routing.YaesuRouting` that
        translates rigctl ``get_level``/``set_level``/``get_func``/
        ``set_func``/``dump_state``/``get_info`` calls into the Yaesu CAT
        protocol semantics expected by the FTX-1 (and compatible)
        transceivers.

        The lazy import keeps :class:`YaesuCatRadio` from depending on
        the rigctld layer at module-load time (``rigctld`` sits above
        ``backends`` in the import-linter layered architecture, so a
        top-level import here would invert the layering). The argument
        and return types are annotated as :class:`~typing.Any` for the
        same reason; precise typing for the public surface lives on
        :class:`~rigplane.core.radio_protocol.RigctldRoutable`.

        Args:
            cache: Shared
                :class:`~rigplane.rigctld.handler._FallbackRigState`
                cache used by the rigctld handler to remember
                last-known meter/level values when the radio cannot
                answer.
            max_power_w: Rated maximum TX power in watts; used to scale
                normalised RFPOWER readings (defaults to 100 W).

        Returns:
            A :class:`~rigplane.rigctld.routing.YaesuRouting` instance
            bound to this radio.
        """
        from ...rigctld.routing import YaesuRouting  # noqa: TID251

        return YaesuRouting(self, cache, max_power_w)
