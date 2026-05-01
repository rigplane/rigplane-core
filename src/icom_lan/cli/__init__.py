"""icom-lan CLI — command-line interface for Icom LAN control.

Usage:
    icom-lan status [--host HOST] [--user USER] [--pass PASS]
    icom-lan freq [VALUE] [--host HOST] [--user USER] [--pass PASS]
    icom-lan mode [VALUE] [--host HOST] [--user USER] [--pass PASS]
    icom-lan power [VALUE] [--host HOST] [--user USER] [--pass PASS]
    icom-lan meter [--host HOST] [--user USER] [--pass PASS]
    icom-lan audio caps [--json] [--stats]
    icom-lan audio rx --out rx.wav [--seconds 10]
    icom-lan audio tx --in tx.wav
    icom-lan audio loopback [--seconds 10]
    icom-lan att [VALUE] [--host HOST] [--user USER] [--pass PASS]
    icom-lan preamp [VALUE] [--host HOST] [--user USER] [--pass PASS]
    icom-lan ptt {on,off} [--host HOST] [--user USER] [--pass PASS]
    icom-lan antenna [--ant1 on|off] [--ant2 on|off] [--rx-ant1 on|off] [--rx-ant2 on|off]
    icom-lan date [YYYY-MM-DD]
    icom-lan time [HH:MM]
    icom-lan levels [--nr 0-255] [--nb 0-255] [--mic-gain 0-255] [--drive-gain 0-255] [--comp-level 0-255]
    icom-lan discover
"""

__all__ = ["main", "check_ports_available"]

import argparse
import asyncio
import errno
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import signal
import sys
from pathlib import Path
import time
import wave
from typing import Any

logger = logging.getLogger(__name__)

from icom_lan import __version__  # noqa: E402
from icom_lan.audio import AudioStats  # noqa: E402
from icom_lan.backends.config import (  # noqa: E402
    LanBackendConfig,
    SerialBackendConfig,
    YaesuCatBackendConfig,
)
from icom_lan.backends.factory import create_radio  # noqa: E402
from icom_lan.core.capabilities import (  # noqa: E402
    CAP_AF_LEVEL,
    CAP_ANTENNA,
    CAP_ATTENUATOR,
    CAP_AUDIO,
    CAP_CW,
    CAP_DUAL_WATCH,
    CAP_METERS,
    CAP_POWER_CONTROL,
    CAP_PREAMP,
    CAP_SCOPE,
    CAP_SYSTEM_SETTINGS,
    CAP_TUNER,
)
from icom_lan.core.radio_protocol import Radio  # noqa: E402
from icom_lan.core.types import Mode, get_audio_capabilities  # noqa: E402

_AUDIO_FRAME_MS = 20
_PCM_SAMPLE_WIDTH_BYTES = 2


def _get_env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    """Read an integer from an environment variable; exit with a clear error if non-numeric."""
    val = os.environ.get(name, "")
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        print(f"Error: ${name} must be an integer (got {val!r})", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Auto-discovery helpers
# ---------------------------------------------------------------------------

_DISCOVER_TIMEOUT = 3.0

# Sentinel: host was NOT explicitly provided by user or env var.
_HOST_NOT_SET = ""


async def _auto_discover_lan(timeout: float = _DISCOVER_TIMEOUT) -> str:
    """Run LAN discovery and return the host IP of the found radio.

    Exits with an error message if zero or multiple radios are found
    (multiple radios print a list so the user can pick).
    """
    from icom_lan.discovery import discover_lan_radios

    print(f"No --host specified, scanning LAN for radios ({timeout:.0f}s)...")
    try:
        radios = await discover_lan_radios(timeout=timeout)
    except OSError as exc:
        print(
            f"Error: LAN discovery failed: {exc}\n"
            "  Network may be unavailable. Specify --host <IP> explicitly.",
            file=sys.stderr,
        )
        sys.exit(1)
    if len(radios) == 1:
        host = str(radios[0]["host"])
        print(f"  Found radio at {host}")
        return host
    if len(radios) == 0:
        print(
            "Error: no radios found on LAN.\n"
            "  Check: radio powered on? LAN remote enabled? Same subnet?\n"
            "  Or specify --host <IP> explicitly.",
            file=sys.stderr,
        )
        sys.exit(1)
    # Multiple radios
    print(f"  Found {len(radios)} radios — please specify one with --host:")
    for i, r in enumerate(radios, 1):
        print(f"    {i}. {r['host']}")
    sys.exit(1)


async def _auto_discover_serial() -> tuple[str, int | None]:
    """Run serial discovery and return (device_path, baudrate | None).

    Exits with an error message if zero or multiple radios are found.
    """
    from icom_lan.discovery import discover_serial_radios

    print("No --serial-port specified, scanning serial ports...")
    radios = await discover_serial_radios()
    if len(radios) == 1:
        r = radios[0]
        print(f"  Found {r.model} on {r.port} ({r.baudrate} baud)")
        return r.port, r.baudrate
    if len(radios) == 0:
        print(
            "Error: no radios found on serial ports.\n"
            "  Check: radio connected via USB? Driver installed?\n"
            "  Or specify --serial-port <PATH> explicitly.",
            file=sys.stderr,
        )
        sys.exit(1)
    # Multiple
    print(f"  Found {len(radios)} radios — please specify one with --serial-port:")
    for i, r in enumerate(radios, 1):
        print(f"    {i}. {r.model} on {r.port} ({r.baudrate} baud)")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Presets — named flag combinations for common scenarios
# ---------------------------------------------------------------------------

_PRESETS: dict[str, dict[str, object]] = {
    "hamradio": {
        "web_bridge": "auto",
        "web_rigctld": True,
    },
    "digimode": {
        "web_bridge": "auto",
        "web_rigctld": True,
        "wsjtx_compat": True,
    },
    "serial": {
        "backend": "serial",
    },
    "headless": {
        "web_rigctld": True,
    },
}


_WEB_ONLY_PRESET_KEYS: frozenset[str] = frozenset({"web_bridge", "web_rigctld"})


def _apply_preset(args: argparse.Namespace, preset_name: str) -> None:
    """Apply a preset's defaults to args. User-explicit flags are NOT overridden.

    Web-only keys (web_bridge, web_rigctld) are silently skipped for non-web commands.
    """
    if preset_name not in _PRESETS:
        avail = ", ".join(sorted(_PRESETS))
        print(
            f"Error: unknown preset {preset_name!r}. Available: {avail}",
            file=sys.stderr,
        )
        sys.exit(1)
    command = getattr(args, "command", None)
    for key, value in _PRESETS[preset_name].items():
        # Skip web-specific preset keys when not running the web command.
        if key in _WEB_ONLY_PRESET_KEYS and command != "web":
            continue
        # Only set if the user did not explicitly provide the flag.
        # argparse sets defaults, so we can't distinguish perfectly —
        # but for store_true booleans the default is False, and for
        # optional args the default is None, so this heuristic works.
        current = getattr(args, key, None)
        if current is None or current is False:
            setattr(args, key, value)


class _DeprecatedPortAction(argparse.Action):
    """Deprecated --port alias — prints a warning and stores to control_port."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        print(
            "Warning: --port is deprecated, use --control-port instead",
            file=sys.stderr,
        )
        setattr(namespace, self.dest, values)


class _DeprecatedPassAction(argparse.Action):
    """Deprecated --pass flag — warns about leakage via `ps aux` / shell history.

    Prefer $ICOM_PASS environment variable or --pass-file PATH instead.
    """

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        print(
            "DeprecationWarning: --pass exposes the password on the process "
            "command line (visible in `ps aux` and shell history). "
            "Use $ICOM_PASS or --pass-file PATH instead.",
            file=sys.stderr,
        )
        setattr(namespace, self.dest, values)


def _resolve_password(args: argparse.Namespace) -> str:
    """Resolve password with precedence: --pass (CLI) > --pass-file > $ICOM_PASS.

    --pass takes precedence when explicitly supplied (it emits a deprecation
    warning at parse time via _DeprecatedPassAction). Otherwise, read from the
    file at --pass-file if given (stripping trailing newline), else fall back
    to the $ICOM_PASS environment variable.
    """
    cli_pass = getattr(args, "password_cli", None)
    if cli_pass is not None:
        return str(cli_pass)
    pass_file = getattr(args, "pass_file", None)
    if pass_file:
        try:
            lines = Path(pass_file).read_text(encoding="utf-8").splitlines()
            return lines[0] if lines else ""
        except OSError as e:
            print(
                f"Error: cannot read --pass-file {pass_file!r}: {e}",
                file=sys.stderr,
            )
            raise SystemExit(2) from e
    return _get_env("ICOM_PASS", "")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="icom-lan",
        description="Control Icom transceivers over LAN",
        epilog=(
            "examples:\n"
            "  icom-lan web                          # auto-discover radio, start web UI\n"
            "  icom-lan web --host 192.168.55.40     # explicit radio IP\n"
            "  icom-lan web --preset digimode        # bridge + rigctld + WSJT-X compat\n"
            "  icom-lan web --bridge                 # web UI + audio bridge\n"
            "  icom-lan serve                        # rigctld server only\n"
            "  icom-lan discover                     # find radios on the network\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show version and exit",
    )
    p.add_argument(
        "--host",
        default=_get_env("ICOM_HOST", _HOST_NOT_SET),
        help="Radio IP (default: $ICOM_HOST, or auto-discover if not set)",
    )
    p.add_argument(
        "--control-port",
        type=int,
        default=_env_int("ICOM_PORT", 50001),
        dest="control_port",
        help="Radio control port (default: $ICOM_PORT or 50001)",
    )
    p.add_argument(
        "--port",
        type=int,
        dest="control_port",
        default=argparse.SUPPRESS,
        action=_DeprecatedPortAction,
        help="Deprecated: use --control-port instead",
    )
    p.add_argument(
        "--user",
        default=_get_env("ICOM_USER", ""),
        help="Username (default: $ICOM_USER)",
    )
    p.add_argument(
        "--pass",
        dest="password_cli",
        default=None,
        action=_DeprecatedPassAction,
        help=(
            "Deprecated: exposes password in `ps aux`/shell history. "
            "Prefer $ICOM_PASS or --pass-file PATH."
        ),
    )
    p.add_argument(
        "--pass-file",
        dest="pass_file",
        default=None,
        metavar="PATH",
        help="Read password from file (first line, trailing newline stripped)",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Connection and auto-discovery timeout in seconds (default: 5)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON when supported by the selected command",
    )
    p.add_argument(
        "--backend",
        choices=["lan", "serial", "yaesu-cat"],
        default=None,
        help="Backend type: lan (default), serial, or yaesu-cat. Auto-inferred from --serial-port if set.",
    )
    p.add_argument(
        "--serial-port",
        dest="serial_port",
        default=_get_env("ICOM_SERIAL_DEVICE", ""),
        metavar="PATH",
        help="Serial device path for --backend serial (default: $ICOM_SERIAL_DEVICE)",
    )
    p.add_argument(
        "--serial-baud",
        dest="serial_baud",
        type=int,
        default=_env_int("ICOM_SERIAL_BAUDRATE", 0) or None,
        metavar="BAUD",
        help="Serial baud rate (default: $ICOM_SERIAL_BAUDRATE, or 115200 for serial / 38400 for yaesu-cat)",
    )
    p.add_argument(
        "--serial-ptt-mode",
        dest="serial_ptt_mode",
        choices=["civ"],
        default=_get_env("ICOM_SERIAL_PTT_MODE", "civ").lower(),
        metavar="MODE",
        help="Serial PTT mode for --backend serial (currently supported: civ)",
    )
    p.add_argument(
        "--rx-device",
        dest="rx_device",
        default=_get_env("ICOM_USB_RX_DEVICE") or None,
        metavar="NAME",
        help="USB audio RX device name for --backend serial (default: $ICOM_USB_RX_DEVICE or auto)",
    )
    p.add_argument(
        "--tx-device",
        dest="tx_device",
        default=_get_env("ICOM_USB_TX_DEVICE") or None,
        metavar="NAME",
        help="USB audio TX device name for --backend serial (default: $ICOM_USB_TX_DEVICE or auto)",
    )
    p.add_argument(
        "--list-audio-devices",
        dest="list_audio_devices",
        action="store_true",
        help="List available USB audio devices and exit (requires icom-lan[bridge])",
    )
    p.add_argument(
        "--model",
        type=str,
        default=None,
        help="Radio model (e.g. IC-7300). Resolves settings from rigs/*.toml",
    )
    p.add_argument(
        "--radio-addr",
        type=lambda x: int(x, 0),
        default=None,
        dest="radio_addr",
        help="CI-V radio address override (hex: 0x94, or decimal: 148)",
    )
    sub = p.add_subparsers(dest="command", help="Command")

    def _add_json(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--json", action="store_true", help="Output as JSON")

    def _add_stats(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--stats",
            action="store_true",
            help="Probe runtime audio stream stats (1 second RX sample)",
        )

    # status
    status_p = sub.add_parser("status", help="Show radio status (freq, mode, meters)")
    _add_json(status_p)

    # freq
    freq_p = sub.add_parser("freq", help="Get or set frequency")
    _add_json(freq_p)
    freq_p.add_argument(
        "value",
        nargs="?",
        type=str,
        help="Frequency in Hz, kHz (with 'k'), or MHz (with 'm')",
    )

    # mode
    mode_p = sub.add_parser("mode", help="Get or set mode")
    _add_json(mode_p)
    mode_p.add_argument(
        "value",
        nargs="?",
        type=str,
        choices=[m.name for m in Mode],
        help="Mode name (USB, LSB, CW, AM, FM, etc.)",
    )

    # power
    power_p = sub.add_parser("power", help="Get or set RF power level")
    _add_json(power_p)
    power_p.add_argument(
        "value",
        nargs="?",
        type=int,
        help="Power level (0-255)",
    )

    # meter
    meter_p = sub.add_parser("meter", help="Read all meters")
    _add_json(meter_p)

    # audio
    audio_p = sub.add_parser("audio", help="Audio helper commands")
    audio_sub = audio_p.add_subparsers(dest="audio_command", help="Audio command")
    audio_sub.required = True
    audio_caps_p = audio_sub.add_parser(
        "caps",
        help="Show icom-lan audio capabilities and defaults",
    )
    _add_json(audio_caps_p)
    _add_stats(audio_caps_p)

    audio_caps = get_audio_capabilities()

    def _add_audio_common_flags(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--sample-rate",
            type=int,
            default=audio_caps.default_sample_rate_hz,
            help=(
                f"PCM sample rate in Hz (default: {audio_caps.default_sample_rate_hz})"
            ),
        )
        sp.add_argument(
            "--channels",
            type=int,
            default=audio_caps.default_channels,
            help=f"PCM channels (default: {audio_caps.default_channels})",
        )
        sp.add_argument("--json", action="store_true", help="Output as JSON")
        sp.add_argument(
            "--stats",
            action="store_true",
            help="Print transfer statistics (ignored when --json is set)",
        )

    audio_rx_p = audio_sub.add_parser(
        "rx",
        help="Capture RX audio to a WAV file",
        description=(
            "Capture PCM audio from the radio and write a 16-bit PCM WAV file.\n"
            "Example: icom-lan audio rx --out rx.wav --seconds 10"
        ),
    )
    _add_audio_common_flags(audio_rx_p)
    audio_rx_p.add_argument(
        "--out",
        dest="output_file",
        required=True,
        help="Output WAV file path",
    )
    audio_rx_p.add_argument(
        "--seconds",
        type=float,
        default=10.0,
        help="Capture duration in seconds (default: 10)",
    )

    audio_tx_p = audio_sub.add_parser(
        "tx",
        help="Transmit audio from a WAV file",
        description=(
            "Read a 16-bit PCM WAV file and transmit it over LAN audio.\n"
            "Example: icom-lan audio tx --in tx.wav"
        ),
    )
    _add_audio_common_flags(audio_tx_p)
    audio_tx_p.add_argument(
        "--in",
        dest="input_file",
        required=True,
        help="Input WAV file path",
    )

    audio_loopback_p = audio_sub.add_parser(
        "loopback",
        help="RX->TX PCM loopback for quick audio path checks",
        description=(
            "Receive PCM audio and immediately feed it back to TX.\n"
            "Example: icom-lan audio loopback --seconds 10"
        ),
    )
    _add_audio_common_flags(audio_loopback_p)
    audio_loopback_p.add_argument(
        "--seconds",
        type=float,
        default=10.0,
        help="Loopback duration in seconds (default: 10)",
    )

    # bridge
    audio_bridge_p = audio_sub.add_parser(
        "bridge",
        help="Bridge radio audio to a virtual audio device (e.g. BlackHole)",
        description=(
            "Bidirectional PCM audio bridge between the radio and a system\n"
            "audio device. Allows WSJT-X, fldigi, JS8Call etc. to use the\n"
            "radio by selecting the virtual device as their sound card.\n\n"
            "Requires: pip install icom-lan[bridge]\n"
            "Virtual device: brew install blackhole-2ch\n\n"
            "Example: icom-lan audio bridge --device 'BlackHole 2ch'"
        ),
    )
    audio_bridge_p.add_argument(
        "--device",
        type=str,
        default=None,
        help="Audio device name (default: auto-detect BlackHole/Loopback)",
    )
    audio_bridge_p.add_argument(
        "--tx-device",
        type=str,
        default=None,
        help="Separate TX capture device name (default: same as --device)",
    )
    audio_bridge_p.add_argument(
        "--rx-only",
        action="store_true",
        help="RX only (don't bridge TX from device to radio)",
    )
    audio_bridge_p.add_argument(
        "--list-devices",
        action="store_true",
        help="List available audio devices and exit",
    )
    audio_bridge_p.add_argument(
        "--label",
        dest="bridge_label",
        default=None,
        metavar="LABEL",
        help="Descriptive label for log messages (default: auto from radio model)",
    )
    audio_bridge_p.add_argument(
        "--max-retries",
        type=int,
        default=5,
        metavar="N",
        help="Max reconnect attempts on device loss (0=infinite, default: 5)",
    )
    audio_bridge_p.add_argument(
        "--retry-delay",
        type=float,
        default=1.0,
        metavar="SEC",
        help="Initial reconnect backoff delay in seconds (default: 1.0)",
    )

    # ptt
    ptt_p = sub.add_parser("ptt", help="PTT control")
    ptt_p.add_argument(
        "state",
        choices=["on", "off"],
        help="PTT state",
    )

    # cw
    cw_p = sub.add_parser("cw", help="Send CW text")
    cw_p.add_argument("text", type=str, help="CW text to send")

    # power-on / power-off
    sub.add_parser("power-on", help="Power on the radio")
    sub.add_parser("power-off", help="Power off the radio")

    # att
    att_p = sub.add_parser("att", help="Get or set attenuator level")
    _add_json(att_p)
    att_p.add_argument(
        "value",
        nargs="?",
        type=str,
        help="Attenuation in dB (0, 3, 6, ..., 45) or 'on'/'off'",
    )

    # preamp
    preamp_p = sub.add_parser("preamp", help="Get or set preamp level")
    _add_json(preamp_p)
    preamp_p.add_argument(
        "value",
        nargs="?",
        type=str,
        help="Preamp level: 0 (off), 1 (PRE1), 2 (PRE2), or 'off'",
    )

    # antenna
    antenna_p = sub.add_parser("antenna", help="Antenna selection control")
    antenna_p.add_argument("--ant1", choices=["on", "off"], help="Set ANT1")
    antenna_p.add_argument("--ant2", choices=["on", "off"], help="Set ANT2")
    antenna_p.add_argument(
        "--rx-ant1",
        dest="rx_ant1",
        choices=["on", "off"],
        help="Set RX antenna on ANT1",
    )
    antenna_p.add_argument(
        "--rx-ant2",
        dest="rx_ant2",
        choices=["on", "off"],
        help="Set RX antenna on ANT2",
    )

    # date
    date_p = sub.add_parser("date", help="Get or set system date (YYYY-MM-DD)")
    date_p.add_argument("date", nargs="?", help="Date to set (get if omitted)")

    # time
    time_p = sub.add_parser("time", help="Get or set system time (HH:MM)")
    time_p.add_argument("time", nargs="?", help="Time to set (get if omitted)")

    # dualwatch
    dualwatch_p = sub.add_parser("dualwatch", help="Get or set dual-watch state")
    dualwatch_p.add_argument(
        "state",
        nargs="?",
        choices=["on", "off"],
        help="Set dual watch on or off (get if omitted)",
    )

    # tuner
    tuner_p = sub.add_parser("tuner", help="Get or set antenna tuner (ATU) state")
    tuner_p.add_argument(
        "action",
        nargs="?",
        choices=["on", "off", "tune"],
        help="on=enable, off=disable, tune=start tune cycle (get if omitted)",
    )
    _add_json(tuner_p)

    # levels (M4 dsp_levels family)
    levels_p = sub.add_parser("levels", help="Get or set M4 DSP/audio levels")
    levels_p.add_argument(
        "--nr", type=int, metavar="0-255", help="Noise reduction level"
    )
    levels_p.add_argument("--nb", type=int, metavar="0-255", help="Noise blanker level")
    levels_p.add_argument(
        "--mic-gain", type=int, metavar="0-255", help="Microphone gain"
    )
    levels_p.add_argument(
        "--drive-gain", type=int, metavar="0-255", help="Drive gain / TX power adjust"
    )
    levels_p.add_argument(
        "--comp-level", type=int, metavar="0-255", help="Speech compressor level"
    )
    levels_p.add_argument(
        "--receiver",
        type=int,
        default=0,
        choices=[0, 1],
        help="Receiver (0=main, 1=sub)",
    )
    _add_json(levels_p)

    # discover
    discover_p = sub.add_parser(
        "discover", help="Discover Icom radios on LAN and serial ports"
    )
    discover_p.add_argument(
        "--serial-only",
        action="store_true",
        default=False,
        help="Only scan serial (USB) ports; skip LAN broadcast",
    )
    discover_p.add_argument(
        "--lan-only",
        action="store_true",
        default=False,
        help="Only scan LAN via UDP broadcast; skip serial ports",
    )
    discover_p.add_argument(
        "--timeout",
        type=float,
        default=3.0,
        metavar="SECONDS",
        help="LAN broadcast listen timeout in seconds (default: 3.0)",
    )

    # serve
    serve_p = sub.add_parser("serve", help="Start rigctld-compatible TCP server")
    serve_p.add_argument(
        "--preset",
        choices=list(_PRESETS),
        default=None,
        metavar="NAME",
        help=f"Apply a named preset ({', '.join(_PRESETS)}). User flags override preset values.",
    )
    serve_p.add_argument(
        "--host",
        dest="serve_host",
        default="0.0.0.0",
        help="Server listen address (default: 0.0.0.0)",
    )
    serve_p.add_argument(
        "--port",
        dest="serve_port",
        type=int,
        default=4532,
        help="Server TCP port (default: 4532)",
    )
    serve_p.add_argument(
        "--read-only",
        action="store_true",
        default=False,
        help="Disallow set commands (read-only mode)",
    )
    serve_p.add_argument(
        "--max-clients",
        dest="max_clients",
        type=int,
        default=10,
        help="Maximum concurrent clients (default: 10)",
    )
    serve_p.add_argument(
        "--cache-ttl",
        dest="cache_ttl",
        type=float,
        default=0.2,
        help="Cache TTL in seconds (default: 0.2)",
    )
    serve_p.add_argument(
        "--wsjtx-compat",
        action="store_true",
        default=False,
        help="Enable WSJT-X compatibility pre-warm (auto-enable DATA mode on first client)",
    )
    serve_p.add_argument(
        "--log-level",
        dest="log_level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level for the rigctld server (default: INFO)",
    )
    serve_p.add_argument(
        "--audit-log",
        dest="audit_log",
        default=None,
        metavar="PATH",
        help="Path to write JSON audit log (one line per command; default: disabled)",
    )
    serve_p.add_argument(
        "--rate-limit",
        dest="rate_limit",
        type=float,
        default=None,
        metavar="N",
        help="Max commands per second per client (default: unlimited)",
    )

    # web
    web_p = sub.add_parser("web", help="Start built-in HTTP + WebSocket web UI server")
    web_p.add_argument(
        "--preset",
        choices=list(_PRESETS),
        default=None,
        metavar="NAME",
        help=f"Apply a named preset ({', '.join(_PRESETS)}). User flags override preset values.",
    )
    web_p.add_argument(
        "--host",
        dest="web_host",
        default="0.0.0.0",
        help="Server listen address (default: 0.0.0.0)",
    )
    web_p.add_argument(
        "--port",
        dest="web_port",
        type=int,
        default=8080,
        help="Server HTTP/WS port (default: 8080)",
    )
    web_p.add_argument(
        "--static-dir",
        dest="web_static_dir",
        default=None,
        metavar="PATH",
        help="Directory to serve static files from (default: built-in)",
    )
    web_p.add_argument(
        "--bridge",
        dest="web_bridge",
        default="auto",
        nargs="?",
        const="auto",
        metavar="DEVICE",
        help=(
            "Start audio bridge to virtual device (e.g. 'BlackHole 2ch'). "
            "Default: auto-detect; if no loopback is found, the bridge is "
            "skipped with a warning. Pass an explicit DEVICE to fail hard "
            "when missing."
        ),
    )
    web_p.add_argument(
        "--bridge-tx-device",
        dest="web_bridge_tx_device",
        default=None,
        metavar="DEVICE",
        help="Separate TX device for bridge (e.g. 'BlackHole 16ch'). Required for bidirectional audio.",
    )
    web_p.add_argument(
        "--bridge-rx-only",
        dest="web_bridge_rx_only",
        action="store_true",
        help="Bridge RX only (no TX from virtual device to radio)",
    )
    web_p.add_argument(
        "--bridge-label",
        dest="web_bridge_label",
        default=None,
        metavar="LABEL",
        help="Descriptive label for audio bridge log messages (default: auto from radio model)",
    )
    web_p.add_argument(
        "--bridge-max-retries",
        dest="web_bridge_max_retries",
        type=int,
        default=5,
        metavar="N",
        help="Bridge max reconnect attempts on device loss (0=infinite, default: 5)",
    )
    web_p.add_argument(
        "--bridge-retry-delay",
        dest="web_bridge_retry_delay",
        type=float,
        default=1.0,
        metavar="SEC",
        help="Bridge initial reconnect backoff delay in seconds (default: 1.0)",
    )
    web_p.add_argument(
        "--dx-cluster",
        dest="dx_cluster",
        default=None,
        metavar="HOST:PORT",
        help="Connect to DX cluster server (e.g. dxc.nc7j.com:7373). Feature is opt-in.",
    )
    web_p.add_argument(
        "--callsign",
        dest="callsign",
        default=None,
        metavar="CALL",
        help="Your callsign for DX cluster login (required with --dx-cluster)",
    )
    web_p.add_argument(
        "--no-rigctld",
        dest="web_rigctld",
        action="store_false",
        default=True,
        help="Disable the rigctld-compatible TCP server (enabled by default)",
    )
    web_p.add_argument(
        "--rigctld-port",
        dest="web_rigctld_port",
        type=int,
        default=4532,
        help="rigctld TCP port (default: 4532)",
    )
    web_p.add_argument(
        "--wsjtx-compat",
        action="store_true",
        default=False,
        help="Enable WSJT-X compatibility pre-warm on embedded rigctld",
    )
    web_p.add_argument(
        "--auth-token",
        dest="auth_token",
        default="",
        metavar="TOKEN",
        help="Bearer token for API authentication (empty = no auth)",
    )
    web_p.add_argument(
        "--tls-cert",
        dest="tls_cert",
        default="",
        metavar="PATH",
        help="Path to TLS certificate PEM file (empty = auto self-signed)",
    )
    web_p.add_argument(
        "--tls-key",
        dest="tls_key",
        default="",
        metavar="PATH",
        help="Path to TLS private key PEM file (empty = auto self-signed)",
    )
    web_p.add_argument(
        "--tls",
        dest="tls",
        action="store_true",
        default=False,
        help="Enable HTTPS with auto-generated self-signed certificate (needed for LAN TX audio)",
    )
    web_p.add_argument(
        "--no-discovery",
        dest="web_discovery",
        action="store_false",
        default=True,
        help="Disable UDP discovery responder (enabled by default on port 8470)",
    )

    # proxy
    proxy_p = sub.add_parser(
        "proxy", help="Transparent UDP relay for remote access via VPN"
    )
    proxy_p.add_argument(
        "--radio",
        required=True,
        help="Radio IP address (e.g. 192.168.55.40)",
    )
    proxy_p.add_argument(
        "--listen",
        default="0.0.0.0",
        help="Listen address (default: 0.0.0.0)",
    )
    proxy_p.add_argument(
        "--port",
        type=int,
        default=50001,
        help="Base port (default: 50001, uses +0/+1/+2)",
    )

    # scope
    scope_p = sub.add_parser("scope", help="Capture scope/waterfall and render image")
    scope_p.add_argument(
        "--output",
        "-o",
        default="scope.png",
        help="Output file path (default: scope.png)",
    )
    scope_p.add_argument(
        "--frames",
        "-n",
        type=int,
        default=50,
        help="Number of frames to capture for waterfall (default: 50)",
    )
    scope_p.add_argument(
        "--theme",
        choices=["classic", "grayscale"],
        default="classic",
        help="Color theme (default: classic)",
    )
    scope_p.add_argument(
        "--spectrum-only",
        action="store_true",
        help="Capture 1 frame and render spectrum only",
    )
    scope_p.add_argument(
        "--width",
        type=int,
        default=800,
        help="Image width in pixels (default: 800)",
    )
    scope_p.add_argument(
        "--json",
        action="store_true",
        help="Output raw frame data as JSON instead of image",
    )
    scope_p.add_argument(
        "--capture-timeout",
        type=float,
        default=None,
        help="Capture timeout in seconds (default: 10 for spectrum-only, 15 for waterfall)",
    )

    return p


def _parse_frequency(value: str) -> int:
    """Parse frequency from string with optional k/m suffix."""
    orig = value
    value = value.strip().lower()
    try:
        if value.endswith("m") or value.endswith("mhz"):
            num = value.rstrip("mhz").strip()
            return int(float(num) * 1_000_000)
        elif value.endswith("k") or value.endswith("khz"):
            num = value.rstrip("khz").strip()
            return int(float(num) * 1_000)
        else:
            return int(float(value))
    except (ValueError, ArithmeticError):
        raise ValueError(
            f"invalid frequency {orig!r} — use Hz (14074000), kHz (14074k), or MHz (14.074m)"
        )


def _rigs_dir() -> Path:
    """Return the path to the rigs/ directory shipped with the package."""
    # Installed layout: icom_lan/rigs/ next to the package directory
    # (this file is icom_lan/cli/__init__.py, so go up TWO levels to icom_lan/)
    pkg_rigs = Path(__file__).resolve().parent.parent / "rigs"
    if pkg_rigs.is_dir():
        return pkg_rigs
    # Development layout: repo_root/rigs/
    # (parents[3] from src/icom_lan/cli/__init__.py = repo root)
    return Path(__file__).resolve().parents[3] / "rigs"


def _resolve_model(
    args: argparse.Namespace,
) -> tuple[int | None, str | None]:
    """Resolve radio_addr and model name from --model / --radio-addr flags.

    Returns:
        (radio_addr, model_name) — either may be None.
    """
    from icom_lan.rig_loader import discover_rigs

    model_name: str | None = getattr(args, "model", None)
    radio_addr: int | None = getattr(args, "radio_addr", None)

    if model_name is None:
        return radio_addr, None

    rigs = discover_rigs(_rigs_dir())

    # Case-insensitive match by model name or by rig id
    matched = None
    for _name, rig in rigs.items():
        if (
            _name.lower() == model_name.lower()
            or rig.id.lower() == model_name.lower().replace("-", "_")
        ):
            matched = rig
            break

    if matched is None:
        available = ", ".join(sorted(rigs.keys()))
        raise ValueError(f"Unknown model {model_name!r}. Available: {available}")

    # --radio-addr overrides profile civ_addr
    if radio_addr is None:
        radio_addr = matched.civ_addr

    return radio_addr, matched.model


def _find_port_pid(port: int) -> str | None:
    """Return PID string using *port*, or None if not determinable."""
    try:
        import subprocess

        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        pid = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
        return pid if pid else None
    except Exception:
        return None


def check_ports_available(ports: list[int]) -> None:
    """Check that each port can be bound; raise RuntimeError with details if not.

    Args:
        ports: List of TCP port numbers to check.

    Raises:
        RuntimeError: If any port is already in use, with PID info when available.
    """
    import socket as _sock

    for port in ports:
        with _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM) as s:
            # Allow binding even if port is in TIME_WAIT from a recent shutdown.
            # Only fail if another process is actively listening.
            s.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEADDR, 1)
            if hasattr(_sock, "SO_REUSEPORT"):
                s.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEPORT, 1)
            try:
                s.bind(("", port))
            except OSError:
                pid = _find_port_pid(port)
                pid_info = f" (PID {pid})" if pid else ""
                raise RuntimeError(f"Port {port} already in use{pid_info}")


async def _build_backend_config(
    args: argparse.Namespace,
) -> LanBackendConfig | SerialBackendConfig | YaesuCatBackendConfig:
    """Build typed backend config from parsed CLI args.

    Runs auto-discovery when host / serial-port is not provided.
    Infers backend type from --serial-port when --backend is not set.
    """
    radio_addr, model_name = _resolve_model(args)

    # Infer backend from context when not explicitly set.
    backend = getattr(args, "backend", None)
    serial_port = getattr(args, "serial_port", "")
    if backend is None:
        if serial_port:
            backend = "serial"
        else:
            backend = "lan"

    if backend == "yaesu-cat":
        device = serial_port
        if not device:
            device, discovered_baud = await _auto_discover_serial()
            if discovered_baud and not getattr(args, "serial_baud", None):
                args.serial_baud = discovered_baud
        return YaesuCatBackendConfig(
            device=device,
            baudrate=getattr(args, "serial_baud", None) or 38400,
            model=model_name,
            rx_device=getattr(args, "rx_device", None) or None,
            tx_device=getattr(args, "tx_device", None) or None,
        )
    if backend == "serial":
        host = getattr(args, "host", _HOST_NOT_SET)
        if host and host != _HOST_NOT_SET:
            print(
                "Warning: --host is ignored when --backend serial is used.",
                file=sys.stderr,
            )
        device = serial_port
        if not device:
            device, discovered_baud = await _auto_discover_serial()
            if discovered_baud and not getattr(args, "serial_baud", None):
                args.serial_baud = discovered_baud
        return SerialBackendConfig(
            device=device,
            baudrate=getattr(args, "serial_baud", None) or 115200,
            timeout=args.timeout,
            radio_addr=radio_addr,
            model=model_name,
            rx_device=getattr(args, "rx_device", None) or None,
            tx_device=getattr(args, "tx_device", None) or None,
            ptt_mode=getattr(args, "serial_ptt_mode", "civ"),
        )
    # LAN backend — auto-discover if host not set.
    if serial_port:
        print(
            "Warning: --serial-port is ignored when --backend lan is used.",
            file=sys.stderr,
        )
    host = getattr(args, "host", _HOST_NOT_SET)
    if not host or host == _HOST_NOT_SET:
        host = await _auto_discover_lan(timeout=args.timeout)
    return LanBackendConfig(
        host=host,
        port=args.control_port,
        username=args.user,
        password=_resolve_password(args),
        timeout=args.timeout,
        radio_addr=radio_addr,
        model=model_name,
    )


async def _cmd_list_audio_devices(args: argparse.Namespace) -> int:
    """List available USB audio devices."""
    try:
        import sounddevice as _sd  # noqa: F401
    except ImportError:
        print(
            "Error: --list-audio-devices requires the sounddevice package.\n"
            "  Install with: pip install icom-lan[bridge]",
            file=sys.stderr,
        )
        return 1
    from icom_lan.audio.usb_driver import list_usb_audio_devices

    devices = list_usb_audio_devices(_sd)
    if getattr(args, "json", False):
        print(
            json.dumps(
                [
                    {
                        "index": d.index,
                        "name": d.name,
                        "input_channels": d.input_channels,
                        "output_channels": d.output_channels,
                        "default_samplerate": d.default_samplerate,
                        "is_default_input": d.is_default_input,
                        "is_default_output": d.is_default_output,
                    }
                    for d in devices
                ]
            )
        )
    else:
        if not devices:
            print("No audio devices found.")
        else:
            print(f"{len(devices)} audio device(s):")
            for d in devices:
                tags: list[str] = []
                if d.is_default_input:
                    tags.append("default-in")
                if d.is_default_output:
                    tags.append("default-out")
                tag_str = f"  [{', '.join(tags)}]" if tags else ""
                print(
                    f"  [{d.index}] {d.name}"
                    f"  (in={d.input_channels}, out={d.output_channels}){tag_str}"
                )
    return 0


async def _run(args: argparse.Namespace) -> int:
    wants_stats = bool(getattr(args, "stats", False))
    if args.command == "audio" and args.audio_command == "caps" and not wants_stats:
        return await _cmd_audio_caps(args)

    # Apply preset before building config (so discovery sees preset-applied backend).
    preset = getattr(args, "preset", None)
    if preset:
        _apply_preset(args, preset)

    try:
        config = await _build_backend_config(args)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    radio = create_radio(config)

    if args.command == "web":
        # Only the web port is required. rigctld is best-effort: if its port
        # is busy we log a warning and continue (handled in _cmd_web).
        try:
            check_ports_available([args.web_port])
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    elif args.command == "serve":
        try:
            check_ports_available([args.serve_port])
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    try:
        async with radio:
            if args.command == "audio" and args.audio_command == "caps":
                if CAP_AUDIO not in radio.capabilities:
                    print(
                        "Error: audio caps with --stats requires a radio that supports audio (e.g. LAN or serial with audio).",
                        file=sys.stderr,
                    )
                    return 1
                runtime_stats: dict[str, bool | int | float | str] = (
                    AudioStats.inactive().to_dict()
                )

                async def _noop_pkt(_pkt: Any) -> None:
                    pass

                await radio.start_audio_rx_opus(_noop_pkt)
                try:
                    await asyncio.sleep(1.0)
                    runtime_stats = await radio.get_audio_stats()
                finally:
                    await radio.stop_audio_rx_opus()
                return await _cmd_audio_caps(args, runtime_stats=runtime_stats)
            elif args.command == "status":
                return await _cmd_status(radio, args)
            elif args.command == "freq":
                return await _cmd_freq(radio, args)
            elif args.command == "mode":
                return await _cmd_mode(radio, args)
            elif args.command == "power":
                return await _cmd_power(radio, args)
            elif args.command == "meter":
                return await _cmd_meter(radio, args)
            elif args.command == "ptt":
                return await _cmd_ptt(radio, args)
            elif args.command == "cw":
                if CAP_CW not in radio.capabilities:
                    print(
                        "Error: this radio does not support CW control.",
                        file=sys.stderr,
                    )
                    return 1
                return await _cmd_cw(radio, args)
            elif args.command == "att":
                if CAP_ATTENUATOR not in radio.capabilities:
                    print(
                        "Error: this radio does not support attenuator control.",
                        file=sys.stderr,
                    )
                    return 1
                return await _cmd_att(radio, args)
            elif args.command == "preamp":
                if CAP_PREAMP not in radio.capabilities:
                    print(
                        "Error: this radio does not support preamp control.",
                        file=sys.stderr,
                    )
                    return 1
                return await _cmd_preamp(radio, args)
            elif args.command == "antenna":
                if CAP_ANTENNA not in radio.capabilities:
                    print(
                        "Error: this radio does not support antenna control.",
                        file=sys.stderr,
                    )
                    return 1
                return await _cmd_antenna(radio, args)
            elif args.command == "date":
                if CAP_SYSTEM_SETTINGS not in radio.capabilities:
                    print(
                        "Error: this radio does not support date control.",
                        file=sys.stderr,
                    )
                    return 1
                return await _cmd_date(radio, args)
            elif args.command == "time":
                if CAP_SYSTEM_SETTINGS not in radio.capabilities:
                    print(
                        "Error: this radio does not support time control.",
                        file=sys.stderr,
                    )
                    return 1
                return await _cmd_time(radio, args)
            elif args.command == "dualwatch":
                if CAP_DUAL_WATCH not in radio.capabilities:
                    print(
                        "Error: this radio does not support dual watch.",
                        file=sys.stderr,
                    )
                    return 1
                return await _cmd_dualwatch(radio, args)
            elif args.command == "tuner":
                if CAP_TUNER not in radio.capabilities:
                    print(
                        "Error: this radio does not support tuner control.",
                        file=sys.stderr,
                    )
                    return 1
                return await _cmd_tuner(radio, args)
            elif args.command == "levels":
                if CAP_AF_LEVEL not in radio.capabilities:
                    print(
                        "Error: this radio does not support level controls.",
                        file=sys.stderr,
                    )
                    return 1
                return await _cmd_levels(radio, args)
            elif args.command == "web":
                return await _cmd_web(radio, args)
            elif args.command == "scope":
                return await _cmd_scope(radio, args)
            elif args.command == "serve":
                return await _cmd_serve(radio, args)
            elif args.command == "audio":
                if args.audio_command == "rx":
                    return await _cmd_audio_rx(radio, args)
                elif args.audio_command == "tx":
                    return await _cmd_audio_tx(radio, args)
                elif args.audio_command == "loopback":
                    return await _cmd_audio_loopback(radio, args)
                elif args.audio_command == "bridge":
                    return await _cmd_audio_bridge(radio, args)
                print("Error: unknown audio command", file=sys.stderr)
                return 1
            elif args.command == "power-on":
                if CAP_POWER_CONTROL not in radio.capabilities:
                    print(
                        "Error: this radio does not support power on/off.",
                        file=sys.stderr,
                    )
                    return 1
                await radio.set_powerstat(True)
                print("Power ON")
                return 0
            elif args.command == "power-off":
                if CAP_POWER_CONTROL not in radio.capabilities:
                    print(
                        "Error: this radio does not support power on/off.",
                        file=sys.stderr,
                    )
                    return 1
                await radio.set_powerstat(False)
                print("Power OFF")
                return 0
            else:
                return await _cmd_status(radio, args)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        logger.debug("Traceback:", exc_info=True)
        return 1


def _audio_frame_bytes(
    sample_rate: int, channels: int, frame_ms: int = _AUDIO_FRAME_MS
) -> int:
    return sample_rate * channels * _PCM_SAMPLE_WIDTH_BYTES * frame_ms // 1000


def _validate_audio_format_args(sample_rate: int, channels: int) -> str | None:
    if sample_rate <= 0:
        return "--sample-rate must be > 0."
    if channels <= 0:
        return "--channels must be > 0."

    caps = get_audio_capabilities()
    if sample_rate not in caps.supported_sample_rates_hz:
        supported = ", ".join(str(rate) for rate in caps.supported_sample_rates_hz)
        return (
            f"Unsupported --sample-rate {sample_rate}. Supported values: {supported}."
        )
    if channels not in caps.supported_channels:
        supported = ", ".join(str(ch) for ch in caps.supported_channels)
        return f"Unsupported --channels {channels}. Supported values: {supported}."
    return None


def _validate_positive_seconds(seconds: float) -> str | None:
    if seconds <= 0:
        return "--seconds must be > 0."
    return None


def _emit_audio_result(
    args: argparse.Namespace,
    *,
    message: str,
    payload: dict[str, Any],
) -> None:
    if args.json:
        print(json.dumps(payload))
        return

    print(message)
    if args.stats:
        for key, value in payload.items():
            print(f"{key}: {value}")


def _print_audio_stats(stats: dict[str, bool | int | float | str]) -> None:
    print("Runtime stats:")
    print(f"  active: {stats['active']}")
    print(f"  state: {stats['state']}")
    print(f"  rx_packets_received: {stats['rx_packets_received']}")
    print(f"  rx_packets_delivered: {stats['rx_packets_delivered']}")
    print(f"  tx_packets_sent: {stats['tx_packets_sent']}")
    print(f"  packets_lost: {stats['packets_lost']}")
    print(f"  packet_loss_percent: {float(stats['packet_loss_percent']):.3f}")
    print(f"  reorder_depth_ema_ms: {float(stats['reorder_depth_ema_ms']):.3f}")
    print(f"  jitter_max_ms: {float(stats['jitter_max_ms']):.3f}")
    print(f"  underrun_count: {stats['underrun_count']}")
    print(f"  overrun_count: {stats['overrun_count']}")
    print(f"  estimated_latency_ms: {float(stats['estimated_latency_ms']):.3f}")
    print(f"  jitter_buffer_depth_packets: {stats['jitter_buffer_depth_packets']}")
    print(f"  jitter_buffer_pending_packets: {stats['jitter_buffer_pending_packets']}")
    print(f"  duplicates_dropped: {stats['duplicates_dropped']}")
    print(f"  stale_packets_dropped: {stats['stale_packets_dropped']}")
    print(f"  out_of_order_packets: {stats['out_of_order_packets']}")


async def _cmd_status(radio: Radio, args: argparse.Namespace) -> int:
    freq = await radio.get_freq()
    mode_name, _filt = await radio.get_mode()
    s_meter: int | str = 0
    power: int | str = 0
    if CAP_METERS in radio.capabilities:
        s_meter = await radio.get_s_meter()
        power = await radio.get_rf_power()
    else:
        s_meter = "n/a"
        power = "n/a"

    if args.json:
        import json

        print(
            json.dumps(
                {
                    "frequency_hz": freq,
                    "frequency_mhz": round(freq / 1e6, 6),
                    "mode": mode_name,
                    "s_meter": s_meter,
                    "power": power,
                }
            )
        )
    else:
        print(f"Frequency: {freq:>12,} Hz  ({freq / 1e6:.6f} MHz)")
        print(f"Mode:      {mode_name}")
        print(f"S-meter:   {s_meter}")
        print(f"Power:     {power}")
    return 0


async def _cmd_audio_caps(
    args: argparse.Namespace,
    *,
    runtime_stats: dict[str, bool | int | float | str] | None = None,
) -> int:
    caps = get_audio_capabilities()
    wants_stats = bool(getattr(args, "stats", False))

    if args.json:
        import json

        payload = caps.to_dict()
        if wants_stats:
            payload["runtime_stats"] = (
                runtime_stats
                if runtime_stats is not None
                else AudioStats.inactive().to_dict()
            )
        print(json.dumps(payload))
    else:
        print("Supported codecs:")
        for codec in caps.supported_codecs:
            print(f"  - {codec.name} (0x{int(codec):02X})")
        print(
            "Supported sample rates (Hz): "
            + ", ".join(str(rate) for rate in caps.supported_sample_rates_hz)
        )
        print(
            "Supported channels: "
            + ", ".join(str(channels) for channels in caps.supported_channels)
        )
        print("Defaults:")
        print(f"  codec: {caps.default_codec.name} (0x{int(caps.default_codec):02X})")
        print(f"  sample_rate_hz: {caps.default_sample_rate_hz}")
        print(f"  channels: {caps.default_channels}")
        print("Selection rules:")
        print("  codec: first supported codec in preference order")
        print("  sample_rate_hz: highest supported rate")
        print("  channels: from default codec (fallback to minimum)")
        if wants_stats:
            _print_audio_stats(
                runtime_stats
                if runtime_stats is not None
                else AudioStats.inactive().to_dict()
            )
    return 0


async def _cmd_audio_rx(radio: Radio, args: argparse.Namespace) -> int:
    if CAP_AUDIO not in radio.capabilities:
        print(
            "Error: this command requires a radio that supports audio (e.g. LAN or serial with audio).",
            file=sys.stderr,
        )
        return 1
    fmt_error = _validate_audio_format_args(args.sample_rate, args.channels)
    if fmt_error is not None:
        print(f"Error: {fmt_error}", file=sys.stderr)
        return 1

    seconds_error = _validate_positive_seconds(args.seconds)
    if seconds_error is not None:
        print(f"Error: {seconds_error}", file=sys.stderr)
        return 1

    # Validate output path is writable before starting capture
    out_path = Path(args.output_file)
    if not out_path.parent.exists():
        print(
            f"Error: output directory does not exist: {out_path.parent}",
            file=sys.stderr,
        )
        return 1
    try:
        out_path.touch()
        out_path.unlink()
    except OSError as exc:
        print(f"Error: cannot write to {out_path}: {exc}", file=sys.stderr)
        return 1

    frame_bytes = _audio_frame_bytes(args.sample_rate, args.channels)
    silence_frame = b"\x00" * frame_bytes
    frames: list[bytes] = []
    rx_frames = 0
    gap_frames = 0
    started = False
    start_time = time.monotonic()

    def on_pcm(frame: bytes | None) -> None:
        nonlocal rx_frames, gap_frames
        if frame is None:
            gap_frames += 1
            frames.append(silence_frame)
            return
        rx_frames += 1
        frames.append(frame)

    try:
        await radio.start_audio_rx_pcm(
            on_pcm,
            sample_rate=args.sample_rate,
            channels=args.channels,
            frame_ms=_AUDIO_FRAME_MS,
        )
        started = True
        await asyncio.sleep(args.seconds)
    finally:
        if started:
            try:
                await radio.stop_audio_rx_pcm()
            except Exception:
                logger.debug("audio-rx: stop_audio_rx_pcm failed", exc_info=True)

    pcm_bytes = b"".join(frames)
    try:
        with wave.open(args.output_file, "wb") as wf:
            wf.setnchannels(args.channels)
            wf.setsampwidth(_PCM_SAMPLE_WIDTH_BYTES)
            wf.setframerate(args.sample_rate)
            wf.writeframes(pcm_bytes)
    except Exception as exc:
        print(
            f"Error: failed to write WAV file '{args.output_file}': {exc}",
            file=sys.stderr,
        )
        return 1

    elapsed = round(time.monotonic() - start_time, 3)
    payload = {
        "command": "audio-rx",
        "output_file": args.output_file,
        "seconds_requested": args.seconds,
        "seconds_elapsed": elapsed,
        "sample_rate": args.sample_rate,
        "channels": args.channels,
        "rx_frames": rx_frames,
        "gap_frames": gap_frames,
        "bytes_written": len(pcm_bytes),
    }
    _emit_audio_result(
        args,
        message=f"Saved RX audio to {args.output_file}",
        payload=payload,
    )
    return 0


def _load_wav_pcm(input_file: str) -> tuple[int, int, int, bytes]:
    with wave.open(input_file, "rb") as wf:
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        sample_rate = wf.getframerate()
        pcm = wf.readframes(wf.getnframes())
    return sample_rate, channels, sample_width, pcm


async def _cmd_audio_tx(radio: Radio, args: argparse.Namespace) -> int:
    if CAP_AUDIO not in radio.capabilities:
        print(
            "Error: this command requires a radio that supports audio (e.g. LAN or serial with audio).",
            file=sys.stderr,
        )
        return 1
    fmt_error = _validate_audio_format_args(args.sample_rate, args.channels)
    if fmt_error is not None:
        print(f"Error: {fmt_error}", file=sys.stderr)
        return 1

    try:
        file_sample_rate, file_channels, sample_width, pcm = _load_wav_pcm(
            args.input_file
        )
    except FileNotFoundError:
        print(f"Error: input file not found: {args.input_file}", file=sys.stderr)
        return 1
    except wave.Error as exc:
        print(f"Error: invalid WAV file '{args.input_file}': {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: failed to read '{args.input_file}': {exc}", file=sys.stderr)
        return 1

    if sample_width != _PCM_SAMPLE_WIDTH_BYTES:
        print(
            (f"Error: input WAV must be 16-bit PCM (got {sample_width * 8}-bit)."),
            file=sys.stderr,
        )
        return 1
    if file_sample_rate != args.sample_rate:
        print(
            (
                f"Error: input WAV sample rate is {file_sample_rate} Hz, "
                f"but --sample-rate is {args.sample_rate}. "
                "Use a matching WAV or pass the file's sample rate."
            ),
            file=sys.stderr,
        )
        return 1
    if file_channels != args.channels:
        print(
            (
                f"Error: input WAV has {file_channels} channel(s), "
                f"but --channels is {args.channels}. "
                "Use a matching WAV or pass the file's channel count."
            ),
            file=sys.stderr,
        )
        return 1
    if not pcm:
        print("Error: input WAV contains no PCM frames.", file=sys.stderr)
        return 1

    frame_bytes = _audio_frame_bytes(args.sample_rate, args.channels)
    frame_interval = _AUDIO_FRAME_MS / 1000.0
    start_time = time.monotonic()
    tx_frames = 0
    started = False

    try:
        await radio.start_audio_tx_pcm(
            sample_rate=args.sample_rate,
            channels=args.channels,
            frame_ms=_AUDIO_FRAME_MS,
        )
        started = True
        for i in range(0, len(pcm), frame_bytes):
            chunk = pcm[i : i + frame_bytes]
            if not chunk:
                break
            if len(chunk) < frame_bytes:
                chunk = chunk + (b"\x00" * (frame_bytes - len(chunk)))
            await radio.push_audio_tx_pcm(chunk)
            tx_frames += 1
            await asyncio.sleep(frame_interval)
    finally:
        if started:
            try:
                await radio.stop_audio_tx_pcm()
            except Exception:
                logger.debug("audio-tx: stop_audio_tx_pcm failed", exc_info=True)

    elapsed = round(time.monotonic() - start_time, 3)
    payload = {
        "command": "audio-tx",
        "input_file": args.input_file,
        "seconds_elapsed": elapsed,
        "sample_rate": args.sample_rate,
        "channels": args.channels,
        "tx_frames": tx_frames,
        "bytes_read": len(pcm),
    }
    _emit_audio_result(
        args,
        message=f"Transmitted WAV audio from {args.input_file}",
        payload=payload,
    )
    return 0


async def _cmd_audio_loopback(radio: Radio, args: argparse.Namespace) -> int:
    if CAP_AUDIO not in radio.capabilities:
        print(
            "Error: this command requires a radio that supports audio (e.g. LAN or serial with audio).",
            file=sys.stderr,
        )
        return 1
    fmt_error = _validate_audio_format_args(args.sample_rate, args.channels)
    if fmt_error is not None:
        print(f"Error: {fmt_error}", file=sys.stderr)
        return 1

    seconds_error = _validate_positive_seconds(args.seconds)
    if seconds_error is not None:
        print(f"Error: {seconds_error}", file=sys.stderr)
        return 1

    frame_bytes = _audio_frame_bytes(args.sample_rate, args.channels)
    silence_frame = b"\x00" * frame_bytes
    frame_interval = _AUDIO_FRAME_MS / 1000.0
    queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=256)
    stop_event = asyncio.Event()

    rx_frames = 0
    gap_frames = 0
    tx_frames = 0
    dropped_frames = 0

    def on_pcm(frame: bytes | None) -> None:
        nonlocal rx_frames, gap_frames, dropped_frames
        if frame is None:
            gap_frames += 1
            out = silence_frame
        else:
            rx_frames += 1
            out = frame
        try:
            queue.put_nowait(out)
        except asyncio.QueueFull:
            dropped_frames += 1

    async def tx_worker() -> None:
        nonlocal tx_frames
        while not (stop_event.is_set() and queue.empty()):
            try:
                frame = await asyncio.wait_for(queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            await radio.push_audio_tx_pcm(frame)
            tx_frames += 1
            await asyncio.sleep(frame_interval)

    rx_started = False
    tx_started = False
    worker_task: asyncio.Task[None] | None = None
    worker_error: Exception | None = None
    start_time = time.monotonic()

    try:
        await radio.start_audio_tx_pcm(
            sample_rate=args.sample_rate,
            channels=args.channels,
            frame_ms=_AUDIO_FRAME_MS,
        )
        tx_started = True
        worker_task = asyncio.create_task(tx_worker())
        await radio.start_audio_rx_pcm(
            on_pcm,
            sample_rate=args.sample_rate,
            channels=args.channels,
            frame_ms=_AUDIO_FRAME_MS,
        )
        rx_started = True
        await asyncio.sleep(args.seconds)
    finally:
        if rx_started:
            try:
                await radio.stop_audio_rx_pcm()
            except Exception:
                logger.debug("audio-loopback: stop_audio_rx_pcm failed", exc_info=True)
        stop_event.set()
        if worker_task is not None:
            try:
                await worker_task
            except Exception as exc:
                worker_error = exc
        if tx_started:
            try:
                await radio.stop_audio_tx_pcm()
            except Exception:
                logger.debug("audio-loopback: stop_audio_tx_pcm failed", exc_info=True)

    if worker_error is not None:
        raise worker_error

    elapsed = round(time.monotonic() - start_time, 3)
    payload = {
        "command": "audio-loopback",
        "seconds_requested": args.seconds,
        "seconds_elapsed": elapsed,
        "sample_rate": args.sample_rate,
        "channels": args.channels,
        "rx_frames": rx_frames,
        "gap_frames": gap_frames,
        "tx_frames": tx_frames,
        "dropped_frames": dropped_frames,
    }
    _emit_audio_result(
        args,
        message="Audio loopback completed",
        payload=payload,
    )
    return 0


async def _cmd_freq(radio: Radio, args: argparse.Namespace) -> int:
    if args.value is not None:
        freq_hz = _parse_frequency(args.value)
        await radio.set_freq(freq_hz)
        print(f"Set: {freq_hz:,} Hz ({freq_hz / 1e6:.6f} MHz)")
    else:
        freq = await radio.get_freq()
        if args.json:
            import json

            print(
                json.dumps(
                    {"frequency_hz": freq, "frequency_mhz": round(freq / 1e6, 6)}
                )
            )
        else:
            print(f"{freq:,} Hz ({freq / 1e6:.6f} MHz)")
    return 0


async def _cmd_mode(radio: Radio, args: argparse.Namespace) -> int:
    if args.value is not None:
        await radio.set_mode(args.value)
        print(f"Set: {args.value}")
    else:
        mode_name, _ = await radio.get_mode()
        if args.json:
            import json

            print(json.dumps({"mode": mode_name}))
        else:
            print(mode_name)
    return 0


async def _cmd_power(radio: Radio, args: argparse.Namespace) -> int:
    if args.value is not None:
        if CAP_POWER_CONTROL not in radio.capabilities:
            print(
                "Error: this radio does not support setting power level.",
                file=sys.stderr,
            )
            return 1
        if not 0 <= args.value <= 255:
            print(
                f"Error: power level must be 0-255 (got {args.value})", file=sys.stderr
            )
            return 1
        await radio.set_rf_power(args.value)
        print(f"Set: {args.value}")
    else:
        if CAP_METERS not in radio.capabilities:
            print(
                "Error: this radio does not support reading power level.",
                file=sys.stderr,
            )
            return 1
        power = await radio.get_rf_power()
        if args.json:
            import json

            print(json.dumps({"power": power}))
        else:
            print(power)
    return 0


async def _cmd_meter(radio: Radio, args: argparse.Namespace) -> int:
    from icom_lan.exceptions import TimeoutError as IcomTimeout

    if CAP_METERS not in radio.capabilities:
        print(
            "Error: this radio does not support meters (S-meter, SWR, power).",
            file=sys.stderr,
        )
        return 1
    results: dict[str, int | float | str] = {}
    meter_getters: list[tuple[str, Any]] = [
        ("s_meter", radio.get_s_meter),
        ("power", radio.get_rf_power),
        ("swr", radio.get_swr),
    ]
    if CAP_METERS in radio.capabilities:
        meter_getters.append(("alc", radio.get_alc_meter))
    for name, getter in meter_getters:
        try:
            results[name] = await getter()
        except IcomTimeout:
            results[name] = "n/a"  # Not available (e.g. SWR/ALC in RX mode)

    if args.json:
        import json

        print(json.dumps(results))
    else:
        for name, val in results.items():
            label = name.replace("_", "-").upper().ljust(8)
            print(f"{label} {val}")
    return 0


async def _cmd_ptt(radio: Radio, args: argparse.Namespace) -> int:
    on = args.state == "on"
    await radio.set_ptt(on)
    print(f"PTT {'ON' if on else 'OFF'}")
    return 0


async def _cmd_cw(radio: Radio, args: argparse.Namespace) -> int:
    await radio.send_cw_text(args.text)
    print(f"CW: {args.text}")
    return 0


async def _cmd_att(radio: Radio, args: argparse.Namespace) -> int:
    if args.value is not None:
        val = args.value.strip().lower()
        if val == "on":
            await radio.set_attenuator(True)
            print("Attenuator: ON (18 dB)")
        elif val == "off":
            await radio.set_attenuator_level(0)
            print("Attenuator: OFF (0 dB)")
        else:
            try:
                db = int(val)
            except ValueError:
                print(
                    f"Error: invalid attenuator value {val!r}. "
                    "Use 'on', 'off', or a dB value (e.g. 0, 6, 12, 18).",
                    file=sys.stderr,
                )
                return 1
            await radio.set_attenuator_level(db)
            print(f"Attenuator: {db} dB")
    else:
        db = await radio.get_attenuator_level()
        if args.json:
            import json

            print(json.dumps({"attenuator_db": db, "attenuator_on": db > 0}))
        else:
            if db == 0:
                print("Attenuator: OFF (0 dB)")
            else:
                print(f"Attenuator: {db} dB")
    return 0


_PREAMP_NAMES = {0: "OFF", 1: "PRE1", 2: "PRE2"}


async def _cmd_preamp(radio: Radio, args: argparse.Namespace) -> int:
    if args.value is not None:
        val = args.value.strip().lower()
        if val == "off":
            level = 0
        else:
            try:
                level = int(val)
            except ValueError:
                print(
                    f"Error: invalid preamp value {val!r}. Use 'off', 0, 1, or 2.",
                    file=sys.stderr,
                )
                return 1
            if level not in (0, 1, 2):
                print(
                    f"Error: preamp level must be 0, 1, or 2 (got {level}).",
                    file=sys.stderr,
                )
                return 1
        await radio.set_preamp(level)
        print(f"Preamp: {_PREAMP_NAMES.get(level, str(level))}")
    else:
        level = await radio.get_preamp()
        if args.json:
            import json

            print(
                json.dumps(
                    {
                        "preamp_level": level,
                        "preamp_name": _PREAMP_NAMES.get(level, str(level)),
                    }
                )
            )
        else:
            print(f"Preamp: {_PREAMP_NAMES.get(level, str(level))}")
    return 0


async def _cmd_antenna(radio: Radio, args: argparse.Namespace) -> int:
    acted = False
    if args.ant1 is not None:
        on = args.ant1 == "on"
        await radio.set_antenna_1(on)
        print(f"ANT1: {'ON' if on else 'OFF'}")
        acted = True
    if args.ant2 is not None:
        on = args.ant2 == "on"
        await radio.set_antenna_2(on)
        print(f"ANT2: {'ON' if on else 'OFF'}")
        acted = True
    if args.rx_ant1 is not None:
        on = args.rx_ant1 == "on"
        await radio.set_rx_antenna_ant1(on)
        print(f"RX ANT1: {'ON' if on else 'OFF'}")
        acted = True
    if args.rx_ant2 is not None:
        on = args.rx_ant2 == "on"
        await radio.set_rx_antenna_ant2(on)
        print(f"RX ANT2: {'ON' if on else 'OFF'}")
        acted = True
    if not acted:
        ant1 = await radio.get_antenna_1()
        ant2 = await radio.get_antenna_2()
        rx_ant1 = await radio.get_rx_antenna_ant1()
        rx_ant2 = await radio.get_rx_antenna_ant2()
        print(f"ANT1: {'ON' if ant1 else 'OFF'}")
        print(f"ANT2: {'ON' if ant2 else 'OFF'}")
        print(f"RX ANT1: {'ON' if rx_ant1 else 'OFF'}")
        print(f"RX ANT2: {'ON' if rx_ant2 else 'OFF'}")
    return 0


async def _cmd_date(radio: Radio, args: argparse.Namespace) -> int:
    if args.date is not None:
        try:
            parts = args.date.split("-")
            if len(parts) != 3:
                raise ValueError("expected 3 parts")
            year, month, day = map(int, parts)
        except ValueError:
            print(
                f"Error: invalid date format '{args.date}' — expected YYYY-MM-DD (e.g. 2024-04-15)",
                file=sys.stderr,
            )
            return 1
        await radio.set_system_date(year, month, day)
        print(f"Date set: {year}-{month:02d}-{day:02d}")
    else:
        year, month, day = await radio.get_system_date()
        print(f"{year}-{month:02d}-{day:02d}")
    return 0


async def _cmd_time(radio: Radio, args: argparse.Namespace) -> int:
    if args.time is not None:
        try:
            parts = args.time.split(":")
            if len(parts) != 2:
                raise ValueError("expected 2 parts")
            hour, minute = map(int, parts)
        except ValueError:
            print(
                f"Error: invalid time format '{args.time}' — expected HH:MM (e.g. 14:30)",
                file=sys.stderr,
            )
            return 1
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            print(
                f"Error: invalid time '{args.time}' — hour must be 0-23, minute 0-59",
                file=sys.stderr,
            )
            return 1
        await radio.set_system_time(hour, minute)
        print(f"Time set: {hour:02d}:{minute:02d}")
    else:
        hour, minute = await radio.get_system_time()
        print(f"{hour:02d}:{minute:02d}")
    return 0


async def _cmd_dualwatch(radio: Radio, args: argparse.Namespace) -> int:
    if args.state is not None:
        on = args.state == "on"
        await radio.set_dual_watch(on)
        print(f"Dual watch: {'ON' if on else 'OFF'}")
    else:
        on = await radio.get_dual_watch()
        print(f"Dual watch: {'ON' if on else 'OFF'}")
    return 0


async def _cmd_tuner(radio: Radio, args: argparse.Namespace) -> int:
    if args.action is not None:
        value = {"on": 1, "off": 0, "tune": 2}[args.action]
        await radio.set_tuner_status(value)
        label = {0: "OFF", 1: "ON", 2: "TUNING"}[value]
        if getattr(args, "json", False):
            print(json.dumps({"tuner_status": value, "label": label}))
        else:
            print(f"Tuner: {label}")
    else:
        status = await radio.get_tuner_status()
        label = {0: "OFF", 1: "ON", 2: "TUNING"}.get(status, f"UNKNOWN({status})")
        if getattr(args, "json", False):
            print(json.dumps({"tuner_status": status, "label": label}))
        else:
            print(f"Tuner: {label}")
    return 0


async def _cmd_levels(radio: Radio, args: argparse.Namespace) -> int:
    """Get or set M4 DSP/audio levels (NR, NB, mic gain, drive, compressor)."""
    receiver = args.receiver
    result = {}

    # Validate ranges
    for name, val in [
        ("--nr", args.nr),
        ("--nb", args.nb),
        ("--mic-gain", args.mic_gain),
        ("--drive-gain", args.drive_gain),
        ("--comp-level", args.comp_level),
    ]:
        if val is not None and not 0 <= val <= 255:
            print(f"Error: {name} must be 0-255 (got {val})", file=sys.stderr)
            return 1

    # Set levels if provided
    if args.nr is not None:
        await radio.set_nr_level(args.nr, receiver)
        result["nr_level"] = args.nr
    if args.nb is not None:
        await radio.set_nb_level(args.nb, receiver)
        result["nb_level"] = args.nb
    if args.mic_gain is not None:
        await radio.set_mic_gain(args.mic_gain)
        result["mic_gain"] = args.mic_gain
    if args.drive_gain is not None:
        await radio.set_drive_gain(args.drive_gain)
        result["drive_gain"] = args.drive_gain
    if args.comp_level is not None:
        await radio.set_compressor_level(args.comp_level)
        result["compressor_level"] = args.comp_level

    # Get current levels (always show current state)
    result["nr_level"] = await radio.get_nr_level(receiver)
    result["nb_level"] = await radio.get_nb_level(receiver)
    result["mic_gain"] = await radio.get_mic_gain()
    result["drive_gain"] = await radio.get_drive_gain()
    result["compressor_level"] = await radio.get_compressor_level()

    if getattr(args, "json", False):
        print(json.dumps(result))
    else:
        print(f"NR Level: {result['nr_level']}")
        print(f"NB Level: {result['nb_level']}")
        print(f"Mic Gain: {result['mic_gain']}")
        print(f"Drive Gain: {result['drive_gain']}")
        print(f"Compressor Level: {result['compressor_level']}")

    return 0


async def _cmd_scope(radio: Radio, args: argparse.Namespace) -> int:
    if CAP_SCOPE not in radio.capabilities:
        print(
            "Error: scope requires a radio that supports scope/waterfall (e.g. LAN or serial).",
            file=sys.stderr,
        )
        return 1
    if args.frames < 1:
        print("Error: --frames must be >= 1", file=sys.stderr)
        return 1
    if args.width < 64:
        print("Error: --width must be >= 64", file=sys.stderr)
        return 1
    if args.capture_timeout is not None and args.capture_timeout <= 0:
        print("Error: --capture-timeout must be > 0", file=sys.stderr)
        return 1

    if not args.json:
        try:
            from icom_lan.scope_render import render_scope_image, render_spectrum
        except ImportError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        # Validate output path before the expensive capture operation.
        # Use os.access() instead of touch()/unlink() to avoid destroying an
        # existing file when the user re-runs with the same output path.
        out_path = Path(args.output)
        if not out_path.parent.exists():
            print(
                f"Error: output directory does not exist: {out_path.parent}",
                file=sys.stderr,
            )
            return 1
        if not os.access(out_path.parent, os.W_OK):
            print(
                f"Error: output directory is not writable: {out_path.parent}",
                file=sys.stderr,
            )
            return 1

    try:
        if args.spectrum_only:
            timeout = args.capture_timeout if args.capture_timeout is not None else 10.0
            print("Capturing 1 scope frame...", file=sys.stderr)
            frame = await radio.capture_scope_frame(timeout=timeout)

            if args.json:
                import json

                print(
                    json.dumps(
                        {
                            "receiver": frame.receiver,
                            "mode": frame.mode,
                            "start_freq_hz": frame.start_freq_hz,
                            "end_freq_hz": frame.end_freq_hz,
                            "out_of_range": frame.out_of_range,
                            "pixels": list(frame.pixels),
                        }
                    )
                )
            else:
                img = render_spectrum(frame, width=args.width, theme=args.theme)
                img.save(args.output, "PNG")
                print(f"Saved spectrum to {args.output}")
        else:
            n = args.frames
            timeout = args.capture_timeout if args.capture_timeout is not None else 15.0
            print(f"Capturing {n} scope frames...", file=sys.stderr)
            frames = await radio.capture_scope_frames(count=n, timeout=timeout)

            if args.json:
                import json

                data = [
                    {
                        "receiver": f.receiver,
                        "mode": f.mode,
                        "start_freq_hz": f.start_freq_hz,
                        "end_freq_hz": f.end_freq_hz,
                        "out_of_range": f.out_of_range,
                        "pixels": list(f.pixels),
                    }
                    for f in frames
                ]
                print(json.dumps(data))
            else:
                render_scope_image(
                    frames,
                    width=args.width,
                    theme=args.theme,
                    output=args.output,
                )
                print(f"Saved scope image to {args.output}")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        logger.debug("Traceback:", exc_info=True)
        return 1
    finally:
        # Always disable scope data output when CLI is done
        try:
            await radio.disable_scope()
        except Exception:
            logger.debug("scope: disable_scope failed", exc_info=True)

    return 0


async def _cmd_audio_bridge(radio: Radio, args: argparse.Namespace) -> int:
    """Bridge radio audio to a virtual audio device."""
    try:
        from icom_lan.audio_bridge import AudioBridge, derive_bridge_label, list_audio_devices
    except ImportError:
        print(
            "Error: audio bridge requires icom-lan[bridge].\n"
            "  Install: pip install 'icom-lan[bridge]'",
            file=sys.stderr,
        )
        return 1

    if not args.list_devices and CAP_AUDIO not in radio.capabilities:
        print(
            "Error: audio bridge requires a radio that supports audio (e.g. LAN or serial with audio).",
            file=sys.stderr,
        )
        return 1
    if args.list_devices:
        try:
            devices = list_audio_devices()
        except ImportError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print("Available audio devices:")
        for dev in devices:
            marker = ""
            name = dev.get("name", "?")
            idx = dev.get("index", "?")
            max_in = dev.get("max_input_channels", 0)
            max_out = dev.get("max_output_channels", 0)
            # Mark likely virtual devices
            for hint in ("BlackHole", "Loopback", "VB-Audio", "Virtual"):
                if hint.lower() in name.lower():
                    marker = " ← virtual"
                    break
            print(f"  [{idx}] {name}  (in={max_in}, out={max_out}){marker}")
        return 0

    bridge_label = derive_bridge_label(radio, getattr(args, "bridge_label", None))

    try:
        bridge = AudioBridge(
            radio,  # type: ignore[arg-type]
            device_name=args.device,
            tx_device_name=getattr(args, "tx_device", None),
            tx_enabled=not args.rx_only,
            label=bridge_label,
            max_retries=getattr(args, "max_retries", 5),
            retry_base_delay=getattr(args, "retry_delay", 1.0),
        )
        await bridge.start()
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    direction = "RX only" if args.rx_only else "RX+TX"
    print(f"Audio bridge running ({direction}). Press Ctrl+C to stop.")

    try:
        while True:
            await asyncio.sleep(10)
            s = bridge.stats
            logger.info(
                "audio-bridge: rx=%d tx=%d drops=%d",
                s["rx_frames"],
                s["tx_frames"],
                s["rx_drops"],
            )
    except asyncio.CancelledError:
        pass
    finally:
        await bridge.stop()
        s = bridge.stats
        print(
            f"\nBridge stopped. RX: {s['rx_frames']} frames, "
            f"TX: {s['tx_frames']} frames, drops: {s['rx_drops']}"
        )

    return 0


# ---------------------------------------------------------------------------
# Startup banner & hints
# ---------------------------------------------------------------------------


def _detect_loopback_hint() -> str | None:
    """Try to detect a loopback audio device; return a hint string or None."""
    try:
        from icom_lan.audio_bridge import find_loopback_device

        dev = find_loopback_device()
        if dev:
            name = dev.get("name", dev) if isinstance(dev, dict) else str(dev)
            return f"{name} detected — use --bridge to enable audio bridge"
    except Exception:
        pass
    return None


def _print_startup_banner(
    *,
    radio: Radio,
    web_url: str | None = None,
    rigctld_addr: str | None = None,
    bridge_info: str | None = None,
    loopback_hint: str | None = None,
    dx_cluster: str | None = None,
) -> None:
    """Print a structured startup summary."""
    model = getattr(radio, "model", "Unknown")
    # LAN radios have _host; serial radios have _transport._device
    host = getattr(radio, "_host", None) or getattr(radio, "host", None)
    if not host:
        transport = getattr(radio, "_transport", None)
        host = getattr(transport, "_device", None) or "?"

    lines = [
        f"--- icom-lan v{__version__} ---",
        f"  Radio:    {model} at {host}",
    ]
    if web_url:
        lines.append(f"  Web UI:   {web_url}")
    if rigctld_addr:
        lines.append(f"  rigctld:  {rigctld_addr}")
    if bridge_info:
        lines.append(f"  Bridge:   {bridge_info}")
    elif loopback_hint:
        lines.append(f"  Bridge:   {loopback_hint}")
    if dx_cluster:
        lines.append(f"  DX:       {dx_cluster}")
    lines.append("---")

    print("\n".join(lines))


async def _cmd_serve(radio: Radio, args: argparse.Namespace) -> int:
    import logging as _logging

    from icom_lan.rigctld.audit import AUDIT_LOGGER_NAME, RigctldAuditFormatter
    from icom_lan.rigctld.contract import RigctldConfig
    from icom_lan.rigctld.server import RigctldServer

    # Apply requested log level to the icom_lan logger hierarchy.
    log_level = getattr(args, "log_level", "INFO")
    _logging.getLogger("icom_lan").setLevel(getattr(_logging, log_level))

    # Configure JSON audit log if a path was provided.
    audit_log_path: str | None = getattr(args, "audit_log", None)
    if audit_log_path:
        parent = Path(audit_log_path).parent
        if not parent.is_dir():
            print(
                f"Error: audit log directory does not exist: {parent}",
                file=sys.stderr,
            )
            return 1
        fh = _logging.FileHandler(audit_log_path)
        fh.setFormatter(RigctldAuditFormatter())
        audit_logger = _logging.getLogger(AUDIT_LOGGER_NAME)
        audit_logger.addHandler(fh)
        audit_logger.setLevel(_logging.INFO)
        audit_logger.propagate = False

    config = RigctldConfig(
        host=args.serve_host,
        port=args.serve_port,
        read_only=args.read_only,
        max_clients=args.max_clients,
        cache_ttl=args.cache_ttl,
        wsjtx_compat=args.wsjtx_compat,
        command_rate_limit=getattr(args, "rate_limit", None),
    )
    _print_startup_banner(
        radio=radio,
        rigctld_addr=f"{args.serve_host}:{args.serve_port}",
    )
    try:
        await RigctldServer(radio, config).serve_forever()
    except asyncio.CancelledError:
        pass
    return 0


async def _cmd_web(radio: Radio, args: argparse.Namespace) -> int:
    import pathlib

    from icom_lan.web.server import WebConfig, WebServer

    static_dir = pathlib.Path(args.web_static_dir) if args.web_static_dir else None
    if static_dir is not None and not static_dir.is_dir():
        print(
            f"Error: --static-dir does not exist or is not a directory: {static_dir}",
            file=sys.stderr,
        )
        return 1
    config_kwargs: dict[str, Any] = {
        "host": args.web_host,
        "port": args.web_port,
    }
    if static_dir is not None:
        config_kwargs["static_dir"] = static_dir

    dx_cluster = getattr(args, "dx_cluster", None)
    if dx_cluster:
        host_part, sep, port_str = dx_cluster.rpartition(":")
        if not host_part or not sep or not port_str.isdigit():
            print(
                f"Error: --dx-cluster must be HOST:PORT (got {dx_cluster!r})",
                file=sys.stderr,
            )
            return 1
        config_kwargs["dx_cluster_host"] = host_part
        config_kwargs["dx_cluster_port"] = int(port_str)
        callsign = getattr(args, "callsign", None) or ""
        if not callsign:
            print(
                "Error: --dx-cluster requires --callsign to log in to the cluster.",
                file=sys.stderr,
            )
            return 1
        config_kwargs["dx_callsign"] = callsign

    auth_token = getattr(args, "auth_token", "")
    if auth_token:
        config_kwargs["auth_token"] = auth_token

    tls_cert = getattr(args, "tls_cert", "")
    tls_key = getattr(args, "tls_key", "")
    use_tls = getattr(args, "tls", False)
    if bool(tls_cert) != bool(tls_key):
        print(
            "Error: --tls-cert and --tls-key must both be provided.",
            file=sys.stderr,
        )
        return 1
    if tls_cert and not Path(tls_cert).is_file():
        print(f"Error: --tls-cert file not found: {tls_cert}", file=sys.stderr)
        return 1
    if tls_key and not Path(tls_key).is_file():
        print(f"Error: --tls-key file not found: {tls_key}", file=sys.stderr)
        return 1
    if tls_cert:
        config_kwargs["tls_cert"] = tls_cert
    if tls_key:
        config_kwargs["tls_key"] = tls_key
    if use_tls or tls_cert:
        config_kwargs["tls"] = True

    config_kwargs["discovery"] = getattr(args, "web_discovery", True)
    config_kwargs["radio_model"] = getattr(radio, "model", "IC-7610")
    config = WebConfig(**config_kwargs)
    server = WebServer(radio, config)

    # Start audio bridge.
    #
    # Default behaviour (issue #1088): `--bridge` is auto-enabled. When a
    # virtual loopback device is auto-detected, the bridge starts; if not,
    # we log a warning and continue serving the web UI without it. Only an
    # explicit `--bridge=<DEVICE>` (a concrete device name) preserves the
    # previous fail-hard behaviour.
    bridge_device = getattr(args, "web_bridge", None)
    bridge_info: str | None = None
    if bridge_device is not None:
        is_auto = bridge_device == "auto"
        device_name = None if is_auto else bridge_device
        tx_device_name = getattr(args, "web_bridge_tx_device", None)
        rx_only = getattr(args, "web_bridge_rx_only", False)
        bridge_label = getattr(args, "web_bridge_label", None)
        from icom_lan.audio_bridge import LoopbackNotFoundError

        try:
            await server.start_audio_bridge(
                device_name=device_name,
                tx_device_name=tx_device_name,
                tx_enabled=not rx_only,
                label=bridge_label,
                max_retries=getattr(args, "web_bridge_max_retries", 5),
                retry_base_delay=getattr(args, "web_bridge_retry_delay", 1.0),
            )
            direction = "RX only" if rx_only else "RX+TX"
            bridge_info = (
                f"auto-enabled ({direction})" if is_auto else f"active ({direction})"
            )
        except LoopbackNotFoundError as exc:
            if is_auto:
                # Graceful degrade — keep serving web UI without the bridge.
                logger.warning("Audio bridge auto-start failed: %s", exc)
                bridge_info = "loopback not found, bridge disabled"
            else:
                print(f"Error: audio bridge failed: {exc}", file=sys.stderr)
                print(
                    "The --bridge flag was explicitly requested with a device. "
                    "Fix the device configuration or remove --bridge to start without it.",
                    file=sys.stderr,
                )
                return 1
        except Exception as exc:
            # Non-loopback failure (unsupported radio audio, missing backend,
            # runtime error). Surface the actual cause — do NOT mask as a
            # missing loopback driver.
            if is_auto:
                logger.error("Audio bridge auto-start failed (non-loopback): %s", exc)
                bridge_info = f"disabled: {exc}"
            else:
                print(f"Error: audio bridge failed: {exc}", file=sys.stderr)
                print(
                    "The --bridge flag was explicitly requested with a device. "
                    "Fix the device configuration or remove --bridge to start without it.",
                    file=sys.stderr,
                )
                return 1

    # Detect loopback device availability for hint (only when bridge not requested
    # — i.e. when web_bridge was explicitly set to None, e.g. by tests).
    loopback_hint: str | None = None
    if bridge_device is None:
        loopback_hint = _detect_loopback_hint()

    # Start rigctld if requested. Failure (e.g. port already in use) is
    # logged as a warning and skipped — the web server keeps serving.
    rigctld_server = None
    rigctld_addr: str | None = None
    if getattr(args, "web_rigctld", False):
        from icom_lan.rigctld.contract import RigctldConfig
        from icom_lan.rigctld.server import RigctldServer

        rigctld_port = getattr(args, "web_rigctld_port", 4532)
        rigctld_config = RigctldConfig(
            host="0.0.0.0",
            port=rigctld_port,
            wsjtx_compat=getattr(args, "wsjtx_compat", False),
        )
        candidate = RigctldServer(radio, rigctld_config)
        try:
            await candidate.start()
        except OSError as exc:
            # Only port-busy (EADDRINUSE) is treated as graceful degrade —
            # another rigctld is likely already running. Other errno values
            # (EACCES on privileged ports, EMFILE on fd exhaustion,
            # ENETUNREACH, etc.) indicate a misconfigured environment and
            # must surface so the operator can fix it.
            if exc.errno == errno.EADDRINUSE:
                logger.warning(
                    "rigctld disabled: failed to bind port %d: %s "
                    "(another rigctld may already be running; pass --no-rigctld to silence)",
                    rigctld_port,
                    exc,
                )
                rigctld_server = None
            else:
                logger.error(
                    "rigctld failed to start on port %d: %s (errno=%s)",
                    rigctld_port,
                    exc,
                    exc.errno,
                )
                raise
        else:
            rigctld_server = candidate
            rigctld_addr = f"0.0.0.0:{rigctld_port}"

    scheme = "https" if config_kwargs.get("tls") else "http"
    web_url = f"{scheme}://{args.web_host}:{args.web_port}/"
    dx_info = dx_cluster if dx_cluster else None
    _print_startup_banner(
        radio=radio,
        web_url=web_url,
        rigctld_addr=rigctld_addr,
        bridge_info=bridge_info,
        loopback_hint=loopback_hint,
        dx_cluster=dx_info,
    )

    try:
        await server.serve_forever()
    except asyncio.CancelledError:
        pass
    finally:
        if rigctld_server is not None:
            await rigctld_server.stop()
    return 0


async def _cmd_discover(_radio: Radio | None, args: argparse.Namespace) -> int:
    """Discover Icom radios on LAN and/or serial ports."""
    from icom_lan.discovery import dedupe_radios, discover_lan_radios, discover_serial_radios

    serial_only: bool = getattr(args, "serial_only", False)
    lan_only: bool = getattr(args, "lan_only", False)
    timeout: float = getattr(args, "timeout", 3.0)

    if serial_only and lan_only:
        print(
            "Error: --serial-only and --lan-only are mutually exclusive.\n"
            "  Use one filter, or omit both to scan everything.",
            file=sys.stderr,
        )
        return 1

    tasks: list[Any] = []
    if not serial_only:
        tasks.append(discover_lan_radios(timeout=timeout))
    if not lan_only:
        tasks.append(discover_serial_radios())

    if not serial_only and not lan_only:
        print(f"Scanning for Icom radios ({timeout:.0f}s LAN + serial)...")
    elif serial_only:
        print("Scanning serial ports for Icom radios...")
    else:
        print(f"Scanning LAN for Icom radios ({timeout:.0f}s)...")

    results = await asyncio.gather(*tasks, return_exceptions=True)

    if not serial_only and not lan_only:
        lan_result, serial_result = results[0], results[1]
    elif serial_only:
        lan_result, serial_result = [], results[0]
    else:
        lan_result, serial_result = results[0], []

    import dataclasses

    lan_radios: list[dict[str, Any]] = (
        lan_result if not isinstance(lan_result, BaseException) else []
    )
    _serial_raw = serial_result if not isinstance(serial_result, BaseException) else []
    serial_radios: list[dict[str, Any]] = [
        dataclasses.asdict(r)
        if dataclasses.is_dataclass(r) and not isinstance(r, type)
        else r
        for r in _serial_raw
    ]

    scan_failed = False
    if isinstance(lan_result, BaseException):
        print(f"  Error: LAN discovery failed — {lan_result}", file=sys.stderr)
        scan_failed = True
    if isinstance(serial_result, BaseException):
        print(f"  Error: Serial discovery failed — {serial_result}", file=sys.stderr)
        scan_failed = True

    grouped = dedupe_radios(lan_radios, serial_radios)

    if not grouped:
        print("No radios found.")
        return 1 if scan_failed else 0

    total_connections = sum(len(r["lan"]) + len(r["serial"]) for r in grouped)
    n_radios = len(grouped)
    n_methods = total_connections

    print(
        f"\nFound {n_radios} radio{'s' if n_radios != 1 else ''}"
        f" with {n_methods} connection method{'s' if n_methods != 1 else ''}:\n"
    )
    for radio in grouped:
        print(f"{radio['model']}:")
        for lan in radio["lan"]:
            print(f"  \u2022 LAN: {lan['host']}")
        for serial in radio["serial"]:
            baud = serial.get("baudrate") or serial.get("baud", "?")
            print(f"  \u2022 Serial: {serial['port']} ({baud} baud)")
    return 0


def main() -> None:
    import logging

    parser = _build_parser()
    args = parser.parse_args()

    # Enable debug logging with ICOM_DEBUG=1 or any truthy value
    # ICOM_LOG_FILE=/path/to/file.log — log to file (default: logs/icom-lan.log)
    # ICOM_LOG_MAX_BYTES=50000000 — rotate when file reaches this size (default: 50 MB)
    # ICOM_LOG_BACKUP_COUNT=5 — keep N rotated backups (default: 5; 0 disables rotation)
    # Set ICOM_LOG_FILE=off to disable the default file log on daemon commands.
    debug_mode = os.environ.get("ICOM_DEBUG", "").strip() not in (
        "",
        "0",
        "false",
        "no",
    )
    log_file = os.environ.get("ICOM_LOG_FILE", "")
    log_file_disabled = log_file.strip().lower() in ("off", "none", "-")
    if log_file_disabled:
        log_file = ""

    handlers: list[logging.Handler] = []

    # Console handler (always present)
    console_handler = logging.StreamHandler()
    if debug_mode:
        console_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(name)s %(levelname)s %(message)s", datefmt="%H:%M:%S"
            )
        )
    else:
        console_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"
            )
        )
    handlers.append(console_handler)

    # Default to a rotating file log for long-running daemon commands so we
    # have a forensic trail for bug reports (EPIPE storms, reconnect loops,
    # etc.) that are easy to lose from stdout alone.
    is_daemon = getattr(args, "command", None) in ("web", "serve")

    # File handler (if log_file specified, debug mode, or daemon command)
    if (debug_mode or is_daemon) and not log_file and not log_file_disabled:
        log_file = "logs/icom-lan.log"

    if log_file:
        log_path = Path(log_file).expanduser().resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        max_bytes = _env_int("ICOM_LOG_MAX_BYTES", 50_000_000)
        backup_count = _env_int("ICOM_LOG_BACKUP_COUNT", 5)
        file_handler: logging.Handler = RotatingFileHandler(
            log_path,
            mode="a",
            maxBytes=max_bytes if backup_count > 0 else 0,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(name)s [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        handlers.append(file_handler)
        print(
            f"Logging to {log_path} (rotate at {max_bytes} bytes, keep {backup_count} backups)",
            file=sys.stderr,
        )

    logging.basicConfig(
        level=logging.DEBUG if debug_mode else logging.INFO,
        handlers=handlers,
        force=True,  # Override any existing config
    )

    if getattr(args, "list_audio_devices", False):
        sys.exit(asyncio.run(_cmd_list_audio_devices(args)))
    elif args.command == "discover":
        sys.exit(asyncio.run(_cmd_discover(None, args)))
    elif args.command == "proxy":
        from icom_lan.proxy import run_proxy

        try:
            asyncio.run(run_proxy(args.radio, args.listen, args.port))
        except Exception as exc:
            print(f"Error: proxy failed: {exc}", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)
    elif args.command is None:
        parser.print_help()
        sys.exit(0)
    else:
        # Convert SIGTERM to KeyboardInterrupt so asyncio.run() cleanup
        # properly unwinds the async context manager (radio.disconnect()).
        def _sigterm_handler(signum: int, frame: Any) -> None:
            raise KeyboardInterrupt()

        signal.signal(signal.SIGTERM, _sigterm_handler)

        # Optional PID file only for daemon-like commands (web, serve).
        # Set ICOM_PID_FILE to a path to enable; no default path to avoid multi-instance conflicts.
        pid_path = os.environ.get("ICOM_PID_FILE", "").strip()
        pid_file: Path | None = None
        if pid_path and args.command in ("web", "serve"):
            pid_file = Path(pid_path)
            try:
                pid_file.write_text(str(os.getpid()))
            except OSError as e:
                logger.warning("Could not write PID file %s: %s", pid_path, e)
                pid_file = None

        try:
            loop = asyncio.new_event_loop()
            try:
                exit_code = loop.run_until_complete(_run(args))
            except KeyboardInterrupt:
                exit_code = 130
            finally:
                # Cancel remaining tasks
                for task in asyncio.all_tasks(loop):
                    task.cancel()
                # Best-effort executor shutdown — don't let it hang
                try:
                    loop.run_until_complete(
                        asyncio.wait_for(loop.shutdown_default_executor(), timeout=1.0)
                    )
                except Exception:
                    pass
                loop.close()
            # Clean up PID file before force exit
            if pid_file is not None:
                try:
                    pid_file.unlink(missing_ok=True)
                except OSError:
                    pass
            # Force exit — prevents hang on orphaned executor threads
            # (PortAudio stream.read/write blocked in thread pool)
            os._exit(exit_code)
        except KeyboardInterrupt:
            os._exit(130)
        finally:
            if pid_file is not None:
                try:
                    pid_file.unlink(missing_ok=True)
                except OSError:
                    pass


if __name__ == "__main__":
    main()
