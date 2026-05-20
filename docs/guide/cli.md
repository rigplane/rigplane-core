---
description: Full RigPlane CLI reference — connect, scan, tune, serve the Web UI, run rigctld, and stream audio from the terminal on macOS, Linux, and Windows.
---

# CLI Reference

The `rigplane` CLI provides quick access to radio control from the terminal.

## Global Options

All commands accept these options:

| Option | Env Var | Default | Description |
|--------|---------|---------|-------------|
| `--host` | `ICOM_HOST` | auto-discover | Radio IP address (LAN backend). If omitted, discovers radio via UDP broadcast. |
| `--control-port` | `ICOM_PORT` | `50001` | Radio UDP control port (`--port` is a deprecated alias) |
| `--user` | `ICOM_USER` | `""` | Username (LAN backend) |
| `--pass` | `ICOM_PASS` | `""` | Password (LAN backend) |
| `--timeout` | — | `5.0` | Timeout in seconds |
| `--json` | — | `false` | Emit JSON when supported by the selected command |
| `--backend` | — | auto | Backend type: `lan`, `serial`, or `yaesu-cat`. Auto-inferred from `--serial-port` if set. |
| `--serial-port` | `ICOM_SERIAL_DEVICE` | auto-discover | Serial device path. If omitted with `--backend serial`, discovers via USB scan. |
| `--serial-baud` | `ICOM_SERIAL_BAUDRATE` | env or backend default | Serial baud (`115200` for `serial`, `38400` for `yaesu-cat` when env is unset) |
| `--serial-ptt-mode` | `ICOM_SERIAL_PTT_MODE` | `civ` | Serial PTT mode (`civ` currently supported) |
| `--rx-device` | `ICOM_USB_RX_DEVICE` | auto | USB audio RX device name (serial/CAT profiles with audio support) |
| `--tx-device` | `ICOM_USB_TX_DEVICE` | auto | USB audio TX device name (serial/CAT profiles with audio support) |
| — | `ICOM_AUDIO_SAMPLE_RATE` | profile/default | LAN audio sample-rate override (`8000`, `16000`, `24000`, or `48000`) |
| `--list-audio-devices` | — | — | List USB audio devices and exit |
| `--version` | — | — | Print version and exit |

!!! tip "Zero-config startup"
    If you have a single radio on the network, just run `rigplane web` — it auto-discovers the radio via LAN broadcast. No `--host` needed.

    For permanent setups, set environment variables in your shell profile:

    ```bash
    # ~/.bashrc or ~/.zshrc
    export ICOM_HOST=192.168.55.40
    export ICOM_USER=myuser
    export ICOM_PASS=mypass
    ```

## Auto-discovery

When `--host` is omitted (LAN backend), rigplane sends a UDP broadcast to find radios:

- **1 radio found** → uses it automatically, prints the IP
- **Multiple radios** → lists them, asks you to specify `--host`
- **No radios** → error with troubleshooting hints

Similarly, when `--backend serial` is set without `--serial-port`, serial ports are scanned automatically.

The `--backend` flag is auto-inferred:

- `--serial-port` provided → infers `--backend serial`
- `ICOM_SERIAL_DEVICE` set → infers `--backend serial`
- Otherwise → `lan` (default)

## Presets

Use `--preset` with `web` or `serve` commands for common scenarios:

| Preset | What it enables |
|--------|----------------|
| `hamradio` | Audio bridge + rigctld |
| `digimode` | Audio bridge + rigctld + WSJT-X compatibility |
| `serial` | Serial backend (auto-detect port) |
| `headless` | rigctld only (no web UI) |

```bash
rigplane web --preset digimode          # Full digital mode setup
rigplane web --preset hamradio          # General ham radio setup
```

User-provided flags override preset values: `--preset digimode --bridge "MyDevice"` uses your device name.

## Backend Selection

rigplane supports three backends: **LAN** (default), **serial** (USB CI-V), and
**yaesu-cat** (text CAT over serial).

### LAN backend (default)

```bash
# Auto-discover radio on LAN
rigplane status

# Explicit IP
rigplane --host 192.168.55.40 status
rigplane --backend lan status
```

### Serial backend

```bash
# Auto-discover serial port
rigplane --backend serial status

# Explicit port (--backend serial is inferred)
rigplane --serial-port /dev/tty.usbmodem-IC7610 status
```

Set via environment variable to avoid repeating:

```bash
export ICOM_SERIAL_DEVICE=/dev/tty.usbmodem-IC7610
rigplane status    # auto-infers --backend serial
```

### Yaesu CAT backend

```bash
# Connects via Yaesu CAT serial protocol (for example FTX-1 / FT-710 profiles)
rigplane --backend yaesu-cat --serial-port /dev/tty.usbserial-FTX1 status
rigplane --backend yaesu-cat --serial-port /dev/tty.usbserial-FTX1 freq
```

### Serial baud defaults by backend

If `--serial-baud` and `ICOM_SERIAL_BAUDRATE` are both unset:

- `--backend serial` defaults to `115200`
- `--backend yaesu-cat` defaults to `38400`

### Audio device selection (serial backend)

The serial backend uses USB audio devices exported by the radio. By default, devices are auto-detected.

```bash
# List all available audio devices
rigplane --list-audio-devices
rigplane --list-audio-devices --json

# Specify explicit devices
rigplane --backend serial --serial-port /dev/tty.usbmodem-IC7610 \
    --rx-device "IC-7610 USB Audio" \
    --tx-device "IC-7610 USB Audio" \
    audio rx --out rx.wav --seconds 10
```

### `discover` command — LAN + serial

The `discover` command scans both LAN (UDP broadcast) and USB serial ports concurrently:

```bash
rigplane discover                      # LAN + serial (default)
rigplane discover --lan-only           # UDP broadcast only
rigplane discover --serial-only        # USB serial ports only
rigplane discover --timeout 5          # Longer LAN listen window
rigplane --json discover               # Stable setup-wizard JSON
```

## Commands

### `status`

Show radio status (frequency, mode, S-meter, power).

```bash
rigplane status
rigplane status --json
```

```
Frequency:    14,074,000 Hz  (14.074000 MHz)
Mode:         USB
S-meter:      42
Power:        50
```

JSON output:

```json
{
  "frequency_hz": 14074000,
  "frequency_mhz": 14.074,
  "mode": "USB",
  "s_meter": 42,
  "power": 50
}
```

### `freq`

Get or set the operating frequency.

```bash
# Get current frequency
rigplane freq

# Set frequency (multiple formats)
rigplane freq 14074000      # Hz
rigplane freq 14074k        # kHz
rigplane freq 14.074m       # MHz
```

### `mode`

Get or set the operating mode.

```bash
# Get current mode
rigplane mode

# Set mode
rigplane mode USB
rigplane mode CW
rigplane mode LSB
```

Available modes: `LSB`, `USB`, `AM`, `CW`, `RTTY`, `FM`, `WFM`, `CW_R`, `RTTY_R`, `DV`

### `power`

Get or set the RF power level (0–255).

```bash
# Get current power
rigplane power

# Set power level
rigplane power 128
```

!!! note "Power Scale"
    The 0–255 value is the radio's internal representation. The mapping to actual watts depends on your radio model and mode.

### `meter`

Read all available meters.

```bash
rigplane meter
rigplane meter --json
```

```
S-METER  42
POWER    50
SWR      n/a
ALC      n/a
```

!!! info
    SWR and ALC are only available during TX. They show `n/a` when receiving.

### `audio caps`

Show rigplane audio capability metadata and deterministic defaults.

```bash
rigplane audio caps
rigplane audio caps --json
rigplane audio caps --stats
rigplane audio caps --json --stats
```

Text output includes:

- supported codecs
- supported sample rates
- supported channels
- default codec/rate/channels
- deterministic selection rules used for defaults
- with `--stats`: a 1-second RX probe and runtime audio quality stats snapshot

JSON output example:

```json
{
  "supported_codecs": [
    {"name": "ULAW_1CH", "value": 1},
    {"name": "PCM_1CH_8BIT", "value": 2}
  ],
  "supported_sample_rates_hz": [8000, 16000, 24000, 48000],
  "supported_channels": [1, 2],
  "default_codec": {"name": "PCM_1CH_16BIT", "value": 4},
  "default_sample_rate_hz": 48000,
  "default_channels": 1,
  "runtime_stats": {
    "active": false,
    "state": "idle",
    "packet_loss_percent": 0.0,
    "reorder_depth_ema_ms": 0.0
  }
}
```

### `audio rx`

Capture RX audio to a 16-bit PCM WAV file.

```bash
rigplane audio rx --out rx.wav --seconds 10
rigplane audio rx --out rx.wav --seconds 10 --sample-rate 48000 --channels 1
rigplane audio rx --out rx.wav --json
```

### `audio tx`

Transmit a WAV file (`16-bit PCM`, matching sample rate/channels).

```bash
rigplane audio tx --in tx.wav
rigplane audio tx --in tx.wav --sample-rate 48000 --channels 1
rigplane audio tx --in tx.wav --json
```

### `audio loopback`

Run a quick RX-to-TX PCM loopback window.

```bash
rigplane audio loopback --seconds 10
rigplane audio loopback --seconds 10 --sample-rate 48000 --channels 1
rigplane audio loopback --json
```

### Shared audio flags (`rx`/`tx`/`loopback`)

- `--sample-rate` — PCM sample rate in Hz (must be supported by `rigplane`)
- `--channels` — PCM channel count (must be supported by `rigplane`)
- `--json` — machine-readable JSON output
- `--stats` — print transfer counters/metrics (human-readable mode)

### `att`

Get or set the attenuator level.

```bash
# Get current attenuation
rigplane att
rigplane att --json

# Set level in dB (0–45, 3 dB steps)
rigplane att 18
rigplane att 0

# Toggle shortcuts
rigplane att on     # Sets 18 dB
rigplane att off    # Sets 0 dB
```

```
Attenuator: 18 dB
```

JSON output:

```json
{
  "attenuator_db": 18,
  "attenuator_on": true
}
```

!!! note "IC-7610 Levels"
    The IC-7610 supports 0, 3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36, 39, 42, 45 dB.
    Values not on 3 dB boundaries will be rejected.

### `preamp`

Get or set the preamplifier level.

```bash
# Get current preamp level
rigplane preamp
rigplane preamp --json

# Set level
rigplane preamp 0     # Off
rigplane preamp 1     # PREAMP 1
rigplane preamp 2     # PREAMP 2
rigplane preamp off   # Same as 0
```

```
Preamp: PRE1
```

JSON output:

```json
{
  "preamp_level": 1,
  "preamp_name": "PRE1"
}
```

### `antenna`

Get or set antenna selection.

```bash
# Get current antenna state
rigplane antenna

# Set antenna
rigplane antenna --ant1 on
rigplane antenna --ant2 on
rigplane antenna --rx-ant1 on
rigplane antenna --rx-ant2 off
```

| Flag | Default | Description |
|------|---------|-------------|
| `--ant1` | — | Set ANT1 (`on`/`off`) |
| `--ant2` | — | Set ANT2 (`on`/`off`) |
| `--rx-ant1` | — | Set RX antenna on ANT1 (`on`/`off`) |
| `--rx-ant2` | — | Set RX antenna on ANT2 (`on`/`off`) |

### `date`

Get or set the radio's internal date.

```bash
rigplane date
```

### `time`

Get or set the radio's internal time.

```bash
rigplane time
```

### `dualwatch`

Get or set dual watch mode.

```bash
rigplane dualwatch
```

### `tuner`

Control the antenna tuner.

```bash
rigplane tuner
```

### `levels`

Get or set radio levels (AF, RF, squelch, etc.).

```bash
rigplane levels
```

### `ptt`

Toggle Push-To-Talk.

```bash
rigplane ptt on
rigplane ptt off
```

!!! danger "Caution"
    Activating PTT will key your transmitter. Ensure your antenna is connected and you are authorized to transmit on the current frequency.

### `cw`

Send CW text via the radio's built-in keyer.

```bash
rigplane cw "CQ CQ DE KN4KYD K"
```

The text is sent in chunks of up to 30 characters. Supports A–Z, 0–9, and standard prosigns.

### `power-on` / `power-off`

Remote power control.

```bash
rigplane power-on
rigplane power-off
```

!!! warning
    `power-on` only works if the radio supports wake-on-LAN and the network connection is maintained in standby mode.

### `discover`

Discover Icom radios on LAN and USB serial ports. Results are grouped by radio identity — the same physical radio connected via both LAN and USB appears as one entry with two connection methods.

```bash
rigplane discover                   # LAN + serial
rigplane discover --lan-only        # UDP broadcast only
rigplane discover --serial-only     # USB serial ports only
rigplane discover --timeout 5       # Longer LAN listen window (default: 3s)
rigplane --json discover            # Stable setup-wizard JSON
```

```
Scanning for Icom radios (3s LAN + serial)...

Found 1 radio with 2 connection methods:

IC-7610:
  • LAN: 192.168.55.40
  • Serial: /dev/cu.usbserial-11320 (19200 baud)
```

Multiple radios:

```
Found 2 radios with 3 connection methods:

IC-7610:
  • LAN: 192.168.55.40
  • Serial: /dev/cu.usbserial-11320 (19200 baud)

IC-705:
  • Serial: /dev/cu.usbserial-54321 (115200 baud)
```

| Flag | Default | Description |
|------|---------|-------------|
| `--lan-only` | off | Only scan via UDP broadcast |
| `--serial-only` | off | Only scan USB serial ports |
| `--timeout SECONDS` | `3.0` | LAN broadcast listen timeout |

With global `--json`, `discover` emits `schema: rigplane.discovery.v1` for
first-run setup wizards. Each radio has stable `connections` entries for LAN
and USB serial candidates. LAN entries include `host`, `remoteId`, and
`requiresCredentials: true`; serial entries include `port`, `protocol`,
`profileId`, `baudrate`, CI-V/CAT `address`, OS `description`, and `hwid` when
available. Credentials are never included in discovery output.

The JSON payload also reports current platform limitations:

| Field | Meaning |
|-------|---------|
| `macosUsbAudio` | CoreAudio device selection is supported, but users may still need to grant microphone/input permission |
| `windowsUsbAudio` | USB audio topology may require explicit/manual device selection |
| `linuxUsbAudio` | PipeWire/PulseAudio device naming varies by distribution/session |

### `serve`

Start a rigctld-compatible TCP server so that logging and contesting software (WSJT-X, JS8Call, Ham Radio Deluxe, etc.) can control the radio without a full Hamlib installation.

```bash
# Basic rigctld server on default port 4532
rigplane serve

# Custom port, read-only, max 5 clients
rigplane serve --port 4533 --read-only --max-clients 5

# Write every command to an audit log
rigplane serve --audit-log /var/log/icom-audit.jsonl

# Rate-limit to 10 commands/sec per client, verbose debug logs
rigplane serve --rate-limit 10 --log-level DEBUG

# WSJT-X preset (enables DATA mode automatically on first connect)
rigplane serve --wsjtx-compat
```

| Option | Default | Description |
|--------|---------|-------------|
| `--host` | `0.0.0.0` | Server listen address |
| `--port` | `4532` | Server TCP port |
| `--read-only` | off | Reject all set commands; allow only reads |
| `--max-clients` | `10` | Maximum concurrent TCP clients |
| `--cache-ttl` | `0.2` | How long (seconds) to cache radio state before re-querying |
| `--wsjtx-compat` | off | Pre-warm for WSJT-X: auto-enable DATA mode on first client connect |
| `--log-level` | `INFO` | Log verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) |
| `--audit-log PATH` | — | Append one JSON line per command to `PATH` (disabled by default) |
| `--rate-limit N` | — | Max commands per second per client; excess commands are dropped (unlimited by default) |

!!! note "rigctld compatibility"
    The server speaks a subset of the Hamlib rigctld protocol over plain TCP. Tested with WSJT-X, JS8Call, and `rigctl` CLI.

### `proxy`

Transparent UDP relay that forwards all radio traffic between a remote client and the physical radio. Useful for accessing a shack radio over a VPN without exposing the radio's IP directly.

```bash
# Forward radio at 192.168.55.40 to all VPN clients
rigplane proxy --radio 192.168.55.40

# Listen only on VPN interface, custom base port
rigplane proxy --radio 192.168.55.40 --listen 10.8.0.1 --port 50010
```

| Option | Default | Description |
|--------|---------|-------------|
| `--radio` | *(required)* | Radio IP address to forward to |
| `--listen` | `0.0.0.0` | Local address to listen on |
| `--port` | `50001` | Base UDP port (proxy binds `port`, `port+1`, `port+2` for control/audio/data) |

### `web`

Start the all-in-one server: Web UI + optional audio bridge + rigctld.

```bash
# Web UI only (auto-discovers radio)
rigplane web

# Use a preset for common scenarios
rigplane web --preset digimode          # Bridge + rigctld + WSJT-X compat
rigplane web --preset hamradio          # Bridge + rigctld

# Web UI + audio bridge + rigctld (recommended for WSJT-X)
rigplane web --bridge "BlackHole 2ch"

# Web UI + WSJT-X compatibility on embedded rigctld
rigplane web --bridge --wsjtx-compat

# Web UI + bridge (RX only, no TX from virtual device)
rigplane web --bridge "BlackHole 2ch" --bridge-rx-only

# Disable rigctld (enabled by default on :4532)
rigplane web --no-rigctld

# Custom ports
rigplane web --port 9090 --rigctld-port 4533

# Require token for /api and WebSocket channels
rigplane web --auth-token "change-me"
rigplane web --auth-token-file ./runtime-token

# Managed local runtime for a supervising desktop app
RIGPLANE_AUTH_TOKEN="$(openssl rand -hex 24)" rigplane station --port 0
rigplane station --port 0 --auth-token-file ./runtime-token
```

| Option | Default | Description |
|--------|---------|-------------|
| `--host` | `0.0.0.0` | Web server bind address |
| `--port` | `8080` | Web server port |
| `--managed` | off | Use managed local defaults: loopback bind, auth required, embedded rigctld on loopback |
| `--static-dir PATH` | — | Serve static files from a custom directory (default: built-in assets) |
| `--bridge DEVICE` | — | Start audio bridge with named virtual device |
| `--bridge-tx-device DEVICE` | — | Separate TX-only device for bidirectional bridge (e.g. `BlackHole 16ch`) |
| `--bridge-rx-only` | — | Bridge receives only (no TX from virtual device) |
| `--no-rigctld` | — | Disable built-in rigctld server |
| `--rigctld-port` | `4532` | Rigctld listen port |
| `--dx-cluster HOST:PORT` | — | Connect to DX cluster server for real-time spot overlays (opt-in) |
| `--callsign CALL` | — | Your callsign for DX cluster login (required with `--dx-cluster`) |
| `--auth-token TOKEN` | — | Require `Authorization: Bearer <TOKEN>` for `/api/*` and WS channels |
| `--auth-token-file PATH` | — | Read API/WS bearer token from a file |

### `station`

Start the managed local station runtime. This is a convenience command for
supervisors such as desktop shells: it runs the web/API server on loopback,
requires API/WebSocket auth, and enables embedded rigctld on loopback for local
clients such as RigPlane Pro.

```bash
export RIGPLANE_AUTH_TOKEN="$(openssl rand -hex 24)"
rigplane station --port 0
```

`station` shares the radio connection flags from the top-level CLI, including
`--host`, `--user`, `--pass-file`, `--backend`, `--serial-port`, and model/CI-V
options. Prefer `--auth-token-file` or `RIGPLANE_AUTH_TOKEN` over `--auth-token`
so the local API token does not appear in process listings. Explicit
`--auth-token` wins over `--auth-token-file`; the file wins over
`RIGPLANE_AUTH_TOKEN`.

After the web listener binds, `station` writes one JSON startup event to stdout:

```json
{"type":"rigplane.runtime.started","pid":12345,"baseUrl":"http://127.0.0.1:58421","healthUrl":"http://127.0.0.1:58421/healthz","runtimeUrl":"http://127.0.0.1:58421/api/v1/runtime","logPath":"/Users/me/Library/Logs/rigplane.log"}
```

Use this event for supervisor discovery when `--port 0` asks the OS to allocate
the actual port.

When UDP discovery is enabled, `rigplane station` and `rigplane web` answer
`RIGPLANE_DISCOVER\n` broadcasts with a `rigplane.station.discovery.v1` JSON
payload. The payload includes the base URL, `/healthz`, `/readyz`,
`/api/v1/runtime`, `/api/v1/station`, version, display name, radio model,
backend, auth-required flag, and a readiness value such as
`ready_with_radio`, `no_usb_radio_connected`, or
`radio_powered_off_or_unreachable`.

### `audio bridge`

Route radio audio to/from a virtual audio device (BlackHole, Loopback, VB-Audio).

```bash
# List available audio devices
rigplane audio bridge --list-devices

# Start bridge
rigplane audio bridge --device "BlackHole 2ch"

# RX only (no TX from virtual device)
rigplane audio bridge --device "BlackHole 2ch" --rx-only
```

!!! tip "macOS Setup"
    Install BlackHole for virtual audio routing:
    ```bash
    brew install blackhole-2ch
    ```
    After install, reboot to load the audio driver. Then `BlackHole 2ch` appears as an audio device.

!!! note "Dependencies"
    Audio-bridge dependencies (`opuslib`, `sounddevice`, `numpy`) ship with
    the core install since v0.19 — `pip install rigplane` is sufficient.
    On macOS with Homebrew, you may also need:
    ```bash
    export DYLD_LIBRARY_PATH=/opt/homebrew/lib
    ```

## Scope / Waterfall

Capture spectrum and waterfall data from the radio's scope display and render as PNG.

Requires optional dependency: `pip install rigplane[scope]`

```bash
# Combined spectrum + waterfall (50 frames, ~3 seconds)
rigplane scope

# Spectrum only (1 frame, fast)
rigplane scope --spectrum-only

# Custom output and frame count
rigplane scope --output waterfall.png --frames 100

# Grayscale theme
rigplane scope --theme grayscale

# Wider image
rigplane scope --width 1200

# Raw JSON data (no Pillow needed)
rigplane scope --json
rigplane scope --spectrum-only --json

# Custom capture timeout
rigplane scope --capture-timeout 20
```

| Option | Default | Description |
|--------|---------|-------------|
| `--output`, `-o` | `scope.png` | Output file path |
| `--frames`, `-n` | `50` | Number of frames for waterfall |
| `--theme` | `classic` | Color theme (`classic` or `grayscale`) |
| `--spectrum-only` | — | Capture 1 frame, render spectrum only |
| `--width` | `800` | Image width in pixels |
| `--json` | — | Output raw frame data as JSON |
| `--capture-timeout` | `10`/`15` | Capture timeout in seconds |

## PID File (optional)

For daemon-like commands (`web`, `serve`), you can opt in to writing a PID file by setting the **`ICOM_PID_FILE`** environment variable to the desired path. The file is created only when starting `web` or `serve` and is removed automatically on clean exit or SIGTERM.

```bash
# Enable PID file for web/serve (e.g. in systemd or a wrapper script)
export ICOM_PID_FILE=/var/run/rigplane.pid
rigplane web

# Graceful shutdown
kill $(cat /var/run/rigplane.pid)

# Check if rigplane is running
test -f /var/run/rigplane.pid && ps -p $(cat /var/run/rigplane.pid)
```

If `ICOM_PID_FILE` is unset or empty, no PID file is written. This avoids conflicts when running multiple instances or in tests.

## Daemon Logging and Rotation (`web` / `serve`)

`web` and `serve` are long-running commands, so the CLI enables file logging by default
to preserve diagnostics across reconnects/restarts.

- Default file path: `logs/rigplane.log`
- Handler type: Python `RotatingFileHandler`
- Rotation defaults: `50_000_000` bytes per file, `5` backups

You can tune this behavior with environment variables:

| Variable | Default | Meaning |
|---|---:|---|
| `ICOM_LOG_FILE` | `logs/rigplane.log` (for `web`/`serve`) | Log file path. Set to `off`, `none`, or `-` to disable file logging entirely. |
| `ICOM_LOG_MAX_BYTES` | `50000000` | Rotate when file reaches this size (bytes). |
| `ICOM_LOG_BACKUP_COUNT` | `5` | Number of rotated files to keep. Set `0` to disable rotation. |
| `ICOM_DEBUG` | unset | Enables debug-level logging and also enables file logging if `ICOM_LOG_FILE` is not disabled. |

```bash
# Custom log location (systemd/container-friendly)
export ICOM_LOG_FILE=/var/log/rigplane/daemon.log
rigplane web

# Smaller files with more backups
export ICOM_LOG_MAX_BYTES=10000000
export ICOM_LOG_BACKUP_COUNT=10
rigplane serve

# Explicitly disable file logs (stdout/stderr only)
export ICOM_LOG_FILE=off
rigplane web
```

## Flag Reference

Compact per-flag reference for all notable options, including which subcommand accepts them, the default value, and a minimal working example.

### Global flags

These flags apply to **every** command and must come before the subcommand name.

| Flag | Command | Default | Description |
|------|---------|---------|-------------|
| `--version` | *(global)* | — | Print version and exit |
| `--control-port PORT` | *(global)* | `50001` (`$ICOM_PORT`) | Radio UDP control port; `--port` is a deprecated alias |
| `--model MODEL` | *(global)* | — | Radio model (e.g. `IC-7300`); resolves from `rigs/*.toml` |
| `--radio-addr ADDR` | *(global)* | — | CI-V address override (hex or decimal) |

```bash
# Print installed version
rigplane --version

# Connect to a radio on a non-default port
rigplane --control-port 50002 status

# Specify radio model explicitly
rigplane --model IC-7300 --backend serial --serial-port /dev/cu.usbserial-XXX status
```

### `serve` flags

| Flag | Command | Default | Description |
|------|---------|---------|-------------|
| `--audit-log PATH` | `serve` | *(disabled)* | Append one JSON line per command to `PATH` |
| `--cache-ttl N` | `serve` | `0.2` | Seconds to cache radio state before re-querying |
| `--log-level LEVEL` | `serve` | `INFO` | Log verbosity: `DEBUG` `INFO` `WARNING` `ERROR` `CRITICAL` |
| `--max-clients N` | `serve` | `10` | Maximum concurrent TCP clients |
| `--rate-limit N` | `serve` | *(unlimited)* | Max commands per second per client; excess are dropped |
| `--read-only` | `serve` | off | Reject all set (write) commands; allow reads only |
| `--wsjtx-compat` | `serve` | off | Auto-enable DATA mode on first client connect (WSJT-X pre-warm) |
| `--preset NAME` | `serve` | *(none)* | Apply a named preset: `hamradio`, `digimode`, `serial`, `headless` |

```bash
# Log every command to a JSONL audit trail
rigplane serve --audit-log /var/log/icom-audit.jsonl

# Tighten cache for faster state sync
rigplane serve --cache-ttl 0.05

# Verbose debug logging
rigplane serve --log-level DEBUG

# Limit to 3 simultaneous clients
rigplane serve --max-clients 3

# Drop commands faster than 10/sec per client
rigplane serve --rate-limit 10

# Prevent accidental frequency/mode changes
rigplane serve --read-only

# Enable WSJT-X compatibility preset
rigplane serve --wsjtx-compat
```

### `proxy` flags

| Flag | Command | Default | Description |
|------|---------|---------|-------------|
| `--radio IP` | `proxy` | *(required)* | Radio IP address to forward all UDP traffic to |
| `--listen ADDR` | `proxy` | `0.0.0.0` | Local interface address to bind on |

```bash
# Forward traffic to radio at 192.168.1.100 (listen on all interfaces)
rigplane proxy --radio 192.168.1.100

# Bind only on the VPN interface
rigplane proxy --radio 192.168.1.100 --listen 10.8.0.1
```

### `web` flags

| Flag | Command | Default | Description |
|------|---------|---------|-------------|
| `--host ADDR` | `web` | `0.0.0.0` | Bind Web UI server to a specific interface |
| `--bridge-tx-device DEVICE` | `web` | *(none)* | Separate TX-only audio device for bidirectional bridge |
| `--static-dir PATH` | `web` | *(built-in)* | Serve static web assets from a custom directory instead of the built-in UI |
| `--dx-cluster HOST:PORT` | `web` | *(none)* | Connect to a DX cluster server for real-time spot overlays |
| `--callsign CALL` | `web` | *(none)* | Your callsign for DX cluster login (required with `--dx-cluster`) |
| `--auth-token TOKEN` | `web` | *(none)* | Require Bearer auth for API/WS endpoints |
| `--auth-token-file PATH` | `web`, `station` | *(none)* | Read Bearer auth token from a file |
| `--tls` | `web` | off | Enable HTTPS with auto-generated self-signed certificate |
| `--tls-cert PATH` | `web` | *(none)* | Path to TLS certificate PEM file |
| `--tls-key PATH` | `web` | *(none)* | Path to TLS private key PEM file |
| `--bridge-label LABEL` | `web` | *(none)* | Descriptive label for audio bridge log messages |
| `--no-rigctld` | `web` | off | Disable built-in rigctld server |
| `--rigctld-port PORT` | `web` | `4532` | Rigctld listen port |
| `--wsjtx-compat` | `web` | off | Enable WSJT-X compatibility pre-warm on embedded rigctld |
| `--preset NAME` | `web` | *(none)* | Apply a named preset: `hamradio`, `digimode`, `serial`, `headless` |

```bash
# Bidirectional bridge: RX from BlackHole 2ch, TX through BlackHole 16ch
rigplane web --bridge "BlackHole 2ch" --bridge-tx-device "BlackHole 16ch"

# Serve a custom-built web UI from a local directory
rigplane web --static-dir /opt/icom-ui/dist

# Connect to a DX cluster and show spot overlays on the waterfall
rigplane web --dx-cluster dxc.nc7j.com:7373 --callsign KN4KYD

# Protect API + WS endpoints with a bearer token
rigplane web --auth-token "change-me"
```

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | Error (connection, auth, command failure) |

## Examples

```bash
# Monitor frequency in a loop
watch -n 1 rigplane freq --json

# Quick band change
rigplane freq 7.074m && rigplane mode USB

# Check RF chain setup
rigplane att && rigplane preamp

# Script-friendly JSON output
FREQ=$(rigplane freq --json | jq -r '.frequency_hz')
echo "Currently on $FREQ Hz"
```
