# Troubleshooting

## Connection Issues

### "Radio did not respond to discovery"

**Symptom:** `TimeoutError: Radio did not respond to discovery after 10 attempts`

**Causes:**

1. **Wrong IP address** — verify your radio's IP in its network settings menu
2. **Radio not on network** — ensure the radio is powered on and connected to your LAN
3. **Firewall blocking UDP** — allow UDP ports 50001–50003
4. **Different subnet** — the client and radio must be on the same subnet (or have routing configured)
5. **Network Control disabled** — enable "Remote Control" in your radio's network settings

**Debug:**

```bash
# Can you reach the radio?
ping 192.168.1.100

# Is the port open?
nc -u -z 192.168.1.100 50001

# Try discovery
rigplane discover
```

### "Authentication failed"

**Symptom:** `AuthenticationError: Authentication failed (error=0xFEFFFFFF)`

**Causes:**

1. **Wrong username/password** — check your radio's Network User settings
2. **Too many connections** — the radio supports limited concurrent connections. Disconnect other clients (RS-BA1, wfview, etc.)
3. **Account disabled** — ensure the network user account is enabled

### "CI-V response timed out"

**Symptom:** `TimeoutError: CI-V response timed out`

**Causes:**

1. **CI-V port negotiation failed** — this usually means the conninfo exchange didn't complete properly
2. **Radio busy** — another application may be holding the CI-V stream
3. **Network congestion** — try increasing the timeout
4. **Command pacing too aggressive for current link/rig state** — increase `ICOM_CIV_MIN_INTERVAL_MS` (e.g., 50-80) for LAN, or `ICOM_SERIAL_CIV_MIN_INTERVAL_MS` for serial backend

**Debug:**

```python
import logging
from rigplane import create_radio, LanBackendConfig

logging.basicConfig(level=logging.DEBUG)

# This will show the full handshake sequence
config = LanBackendConfig(host="192.168.1.100", username="u", password="p")
async with create_radio(config) as radio:
    freq = await radio.get_freq()
```

Look for:
- `Status: civ_port=50002` — confirms port negotiation succeeded
- `CI-V port not in status, using default` — port negotiation failed, likely a GUID issue

### "Scope over serial requires baudrate >= 115200"

**Symptom:** `CommandError` on `enable_scope()` / `capture_scope_frame()` with a low
serial baudrate.

**Cause:** Scope/waterfall CI-V traffic over serial is high-rate; low baud can starve
regular command responses. The serial backend enforces a deterministic guardrail.

**Fixes:**

1. Set the serial CI-V speed to at least `115200` (recommended).
2. If you must run lower for diagnostics, use explicit override:
   - Python API: `SerialBackendConfig(..., allow_low_baud_scope=True)` when using `create_radio(config)`
   - Env var: `ICOM_SERIAL_SCOPE_ALLOW_LOW_BAUD=1`

When override is used, the backend logs a warning because timeout risk increases.

### Connection drops after ~30 seconds

**Symptom:** Commands work initially, then start timing out.

**Cause:** Keep-alive pings stopped. This shouldn't happen under normal use, but can occur if:

- The event loop is blocked for extended periods
- The Python process is suspended

The library sends pings every 500ms automatically. If the radio doesn't receive pings for its timeout period (usually 10–30 seconds), it drops the connection.

### Web UI API returns 401 Unauthorized

**Symptom:** `GET /api/v1/info` (or WebSocket connect) fails with HTTP 401.

**Cause:** Web server was started with `--auth-token`, but request does not include token.

**Fixes:**

```bash
# HTTP API
curl -H "Authorization: Bearer <TOKEN>" http://127.0.0.1:8080/api/v1/info
```

For WebSocket clients, send either:

- `Authorization: Bearer <TOKEN>` header, or
- `?token=<TOKEN>` query parameter.

### `radio_connect` returns `backend_recovering`

**Symptom:** WebSocket response:

```json
{"type":"response","ok":false,"error":"backend_recovering"}
```

**Cause:** Backend is already in reconnect/recovery state. Parallel manual reconnect
requests are rejected intentionally.

**Fix:** Wait for `radio_ready=true` in state updates before retrying manual connect.
Do not spam reconnect requests from frontend automation loops.

### Mobile headset/lock-screen controls do nothing

**Symptom:** Volume/media buttons do not tune frequency and play/pause does not toggle PTT.

**Cause:** Browser does not expose the MediaSession API (`navigator.mediaSession`), so handlers are not registered.

**Fixes:**

1. Verify behavior in a browser/platform with MediaSession support.
2. Use on-screen controls as fallback (expected behavior on unsupported browsers).

### Web UI polling appears slower on low battery

**Symptom:** State updates arrive less frequently on mobile devices with low battery.

**Cause:** Frontend intentionally increases `/api/v1/state` polling interval when battery is low and not charging:

- 10–20% -> 2x interval
- <=10% -> 4x interval

**Fixes / Notes:**

1. Charge device to restore normal polling cadence.
2. This optimization is skipped automatically on browsers without Battery Status API support.

### Mobile v2 gestures are not available

**Symptom:** Swipe-to-dismiss bottom sheets and touch-first mobile layout are missing.

**Cause:** UI version defaults to v1 unless v2 is selected.

**Fixes:**

1. Open Web UI with `?ui=v2` query parameter.
2. Keep v2 selected in localStorage for subsequent sessions.

## Command Issues

### "Radio rejected set_frequency"

**Symptom:** `CommandError: Radio rejected set_frequency(999999999)`

The radio returned NAK (0xFA). Possible causes:

- Frequency out of the radio's supported range
- Radio is in a mode that doesn't allow frequency changes
- VFO lock is enabled

### SWR/ALC always returns 0

These meters only report values during transmit. When receiving, they return 0.

### CW text not sending

- Ensure the radio is in CW mode (`await radio.set_mode("CW")`)
- Check that CW keying speed is set appropriately on the radio
- Text must be ASCII A–Z, 0–9

## Network Tips

### Static IP

Assign a static IP to your radio to avoid DHCP lease changes:

- IC-7610: **Menu → Set → Network → IP Address** — set to Manual
- Use an IP outside your DHCP range

### WiFi vs Ethernet

- **Ethernet** is more reliable with lower latency
- **WiFi** (IC-705) works but may experience higher packet loss
- For WiFi radios, increase the timeout: `timeout=10.0`

### VPN / Remote Access

The library works over VPN tunnels if UDP traffic is forwarded:

- Ensure your VPN supports UDP
- Allow ports 50001–50003
- Increase timeout for high-latency links
- Discovery (broadcast) won't work over VPN — specify the radio's IP directly

## Retry / Fail-Fast Policy

Recommended policy for real-radio automation:

- **Retry allowed (soft-fail):** idempotent reads (`get_*`) and non-critical telemetry.
- **Retry with recovery:** command timeout after all retries -> one reconnect recovery path -> retry command once.
- **Fail-fast (no blind retries):** safety-critical toggles (`PTT`, `power_control`, CW stop/start transactions) when state uncertainty is dangerous.
- **Always log on timeout:** command name, attempt, timeout flag, recovered flag, duration.

In integration soak, use structured JSON logs with canonical fields:

- `test`, `step`, `cmd`, `attempt`, `timeout`, `recovered`, `duration_ms`

## Soak / Integration Diagnostics

Use soak mode to capture rare timeout behavior with structured logs:

```bash
export ICOM_SOAK_SECONDS=120
pytest -m integration tests/integration/test_radio_integration.py::TestSoak::test_soak_retries_and_logging -q -s
```

Look for:

- `{"ev":"timeout", ...}` — timeout event
- `{"ev":"recover", ...}` — reconnect recovery attempts
- `SOAK_SUMMARY {...}` — final counters (`timeouts`, `timeouts_unrecovered`, etc.)

## Getting Help

1. Enable debug logging (see above)
2. [Open an issue](https://github.com/rigplane/rigplane-core/issues) with:
    - Your radio model
    - Python version
    - OS
    - Debug log output
    - Steps to reproduce

## CI-V Commands Timeout During Scope/Waterfall

**Symptom:** `get_frequency()`, `get_power()` etc. return cached values or raise
`TimeoutError` while scope/waterfall is active.

**Cause:** Fixed in v0.8.0. In earlier versions, the RX pump processed one packet
at a time. Scope data (~225 packets/sec) would queue ahead of command responses.

**Solution:** Upgrade to v0.8.0+. The drain-all RX pattern processes all queued
packets each iteration.

## UDP Errors in Logs (`peer=<host:port>`)

**Symptom:** log lines like:

- `UDP error [peer=192.168.55.40:50002] (#1): ...`
- `UDP error [peer=192.168.55.40:50001] (#100, suppressed 97): ...`

**What changed:** transport logs now include the remote endpoint in each UDP error
line. This helps distinguish which logical channel is failing.

**Port mapping (defaults):**

- `:50001` -> control/keepalive channel
- `:50002` -> CI-V command/state channel
- `:50003` -> audio channel

These are Icom's factory defaults and match the vast majority of deployments.
If the radio's menu has been changed, or the client was started with a custom
`--control-port` / `ICOM_PORT` (see `src/rigplane/cli.py`), the actual ports may
differ — CI-V and audio ports can also be reassigned by the radio's status
report (see `src/rigplane/_control_phase.py`). In that case, substitute your
session's ports in the filters below.

**How to interpret quickly:**

1. Mostly `:50003` errors -> audio path issue (network jitter/loss, remote link).
2. Repeated `:50002` errors -> command/state instability; expect CI-V retries/timeouts.
3. `:50001` errors -> session-level risk (keepalive/control path), reconnect likely.

**Important logging behavior:**

- First 3 UDP errors are logged individually.
- After that, logs are throttled and emitted every 100th event
  (`suppressed 97`) to avoid log storms.

**Runbook:**

```bash
# Keep only UDP diagnostics from the daemon log
rg "UDP error|UDP connection lost" logs/rigplane.log

# Focus on CI-V channel failures only (replace 50002 if your session uses a custom CI-V port)
rg "peer=.*:50002" logs/rigplane.log
```

If only one peer/port is noisy, troubleshoot that path first (audio, CI-V, or
control) instead of treating it as a generic network failure.

## Reconnect After Disconnect Takes Too Long

**Symptom:** After clicking Disconnect then Connect, the radio doesn't respond
for 30-60 seconds.

**Cause:** Fixed in v0.8.0. Earlier versions did a full disconnect (including
the control transport) and then a full reconnect with discovery. IC-7610 doesn't
respond to discovery for ~60s after a recent session.

**Solution:** v0.8.0 uses soft disconnect/reconnect — only the CI-V data stream
is closed. The control transport stays alive, so reconnect skips discovery and
authentication entirely. Reconnect takes ~1 second.

## Connection Fails With `civ_port=0`

**Symptom:** Log shows `Status: civ_port=0, audio_port=0` repeatedly.

**Cause:** The radio needs time to recover between connections. Rapid reconnects
(especially during development/testing) cause this.

**Solutions:**
1. Wait 30–60 seconds before reconnecting
2. v0.8.0+ uses optimistic default ports — connects instantly even with `civ_port=0`
3. If persistent: power-cycle the radio's network (Menu → Set → Network → LAN → Off/On)

## Audio Cuts Out on Mobile (Safari iOS)

**Symptom:** Audio stops when Safari goes to background, doesn't resume on return.

**Cause:** iOS suspends WebSocket connections and AudioContext in background tabs.

**Solution:** v0.8.0+ adds `visibilitychange` listener that resumes AudioContext
and reconnects the audio WebSocket when the tab returns to foreground. Audio
may stutter briefly during reconnection.

## Audio Stutters Over VPN/Tailscale

**Symptom:** Audio playback has gaps or stutters when accessing the Web UI remotely.

**Cause:** Network jitter from VPN tunneling.

**Solution:** v0.8.0+ uses a 200ms jitter buffer (up from 50ms). For very high
latency connections, this may still be insufficient — consider a local deployment.

## LAN Audio Breaks Over WireGuard or Other UDP Tunnels

**Symptom:** Control and CI-V commands work, but LAN audio is choppy, chipmunky,
or loses whole chunks over a VPN/tunnel. This is most visible with high-rate
PCM modes such as IC-7610 `PCM_2CH_16BIT` at 48 kHz.

**Cause:** The radio may send one 20 ms audio frame as multiple UDP payloads.
For IC-7610 48 kHz stereo PCM, one frame is 3840 audio bytes and is normally
split across three payloads. If a tunnel path silently drops IP fragments, the
receiver may see only the small trailing packets and cannot reassemble the
original audio. This is a tunnel/path-MTU problem, not a radio capability
failure.

**Quick workaround:**

```bash
export ICOM_AUDIO_SAMPLE_RATE=16000
uv run rigplane --host 192.168.55.40 --user USER --pass-file .rigplane-pass web
```

16 kHz stereo PCM fits a 20 ms frame in one UDP packet and is usually adequate
for remote voice monitoring. Keep 48 kHz for full-fidelity LAN or well-tuned
VPN paths.

**MTU runbook:**

1. Capture on both tunnel endpoints while audio is running:
   `tcpdump -ni wg0 'udp and host <radio-or-peer-ip>'`.
2. Look for IP fragments, especially first fragments with `MF` set.
3. If first fragments disappear but small trailing fragments arrive, lower the
   tunnel interface MTU on both ends.
4. For WireGuard, remember to budget for outer IP/UDP/WireGuard overhead. The
   correct value depends on the WAN path; cellular/CGNAT/cloud paths often need
   smaller MTUs than a normal Ethernet LAN.
5. Re-run `rigplane audio probe --candidate-cooldown 35 --retry-rejected 1`
   after changing MTU and confirm packet counts are stable.

## TX Audio Has No Modulation on macOS (Web UI / Bridge)

**Symptom:** PTT toggles ON, but transmitted audio is silent or very weak.

**Typical logs:**

- `audio: TX transcoder unavailable (opus codec missing?)`
- Opus library import failures from `opuslib`.

**Cause:** On macOS, `opuslib` may fail to locate Homebrew `libopus` automatically.

**Fixes:**

```bash
# Install libopus via Homebrew
brew install opus

# Apply project patch for opuslib path detection
python scripts/patch_opuslib_macos.py
```

Then restart the app and retest TX audio. See detailed notes:
`docs/opuslib-macos-fix.md`.

## Serial Backend Issues (IC-7610 USB)

### "No such file or directory: /dev/cu.usbserial-..."

**Symptom:** `FileNotFoundError` or `SerialException` when connecting.

**Causes:**
1. USB cable not connected
2. Radio not powered on
3. Wrong device path

**Solutions:**
```bash
# List available serial devices (macOS)
ls -l /dev/cu.usbserial-*

# Linux
ls -l /dev/ttyUSB*

# Wait 5-10 seconds after power-on for device to appear
```

### "CI-V USB Port must be set to CI-V, not REMOTE"

**Symptom:** Serial connection opens, but CI-V commands fail with timeout or NAK.

**Cause:** Radio's `CI-V USB Port` setting is in `[REMOTE]` mode (RS-BA1), not `Link to [CI-V]`.

**Solution:**
1. On IC-7610: Menu → Set → Connectors → CI-V → **CI-V USB Port**
2. Set to **`Link to [CI-V]`** (NOT `[REMOTE]`)
3. Disconnect/reconnect USB cable
4. Retry connection

!!! danger "Critical Hardware Finding"
    This was confirmed with live IC-7610 hardware validation (issue #146, 2026-03-06). `[REMOTE]` mode blocks serial CI-V commands. Use `Link to [CI-V]` for rigplane serial backend.

### "Audio device 'IC-7610 USB Audio' not found"

**Symptom:** `AudioError` or `sounddevice` exception when starting audio.

**Causes:**
1. USB audio not enabled on radio
2. Wrong device name
3. sounddevice/numpy not installed

**Solutions:**
```bash
# List available audio devices
rigplane --list-audio-devices
rigplane --list-audio-devices --json

# Check radio settings:
# Menu → Set → Connectors → USB Audio → USB Audio (RX/TX) → Enabled

# Audio dependencies ship with the core install (since v0.19); the legacy
# `[bridge]` extra still resolves but is now a no-op alias.
pip install rigplane
```

### "Scope over serial requires baudrate >= 115200"

**Symptom:** `CommandError` when calling `enable_scope()` or `capture_scope_frame()` on serial backend with baud < 115200.

**Cause:** Scope CI-V traffic is high-rate (~225 packets/sec on LAN). Lower serial baud rates cannot sustain this without starving command responses. The serial backend enforces a deterministic guardrail.

**Solutions:**
1. **Recommended:** Set CI-V USB Baud Rate to 115200 or higher in radio settings:
   - Menu → Set → Connectors → CI-V → CI-V USB Baud Rate → **115200**
2. **Override** (use with caution for debugging only):
   ```bash
   export ICOM_SERIAL_SCOPE_ALLOW_LOW_BAUD=1
   rigplane --backend serial --serial-baud 19200 scope
   ```
   Python API:
   ```python
   config = SerialBackendConfig(..., allow_low_baud_scope=True)
   ```
   The library will log a warning about increased timeout risk.

### Serial CI-V commands time out under load

**Symptom:** `TimeoutError` on CI-V commands when audio or scope is active.

**Causes:**
1. Baud rate too low for combined traffic
2. CI-V pacing too aggressive

**Solutions:**
```bash
# Increase serial CI-V pacing interval (default 50 ms)
export ICOM_SERIAL_CIV_MIN_INTERVAL_MS=80
rigplane --backend serial status

# Or use higher baud rate (radio setting)
# Menu → Set → Connectors → CI-V → CI-V USB Baud Rate → 115200
```

### USB audio RX works, but TX does not

**Symptom:** Can hear radio RX audio, but transmit from computer does not work.

**Causes:**
1. Radio USB Audio TX not enabled
2. PTT not activated
3. Wrong TX device selected

**Solutions:**
```bash
# Check radio setting:
# Menu → Set → Connectors → USB Audio → USB Audio TX → Enabled

# Verify TX device name matches RX
rigplane --list-audio-devices

# Explicitly set TX device
rigplane --backend serial --tx-device "IC-7610 USB Audio" audio tx --in test.wav

# Ensure PTT is active during TX (library handles this automatically for audio tx)
```

### Permission denied on /dev/cu.usbserial-*

**Symptom:** `PermissionError` when opening serial device.

**Cause:** User lacks permissions (rare on macOS, more common on Linux).

**Solutions:**
```bash
# Linux: add user to dialout group
sudo usermod -a -G dialout $USER
# Logout/login required

# macOS: check ownership (typically world-readable by default)
ls -l /dev/cu.usbserial-*
# Should show: crw-rw-rw- root wheel

# Temporary workaround (not recommended for production)
sudo chmod 666 /dev/cu.usbserial-XXXXXX
```
