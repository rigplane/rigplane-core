# Reliability semantics

This document defines the public contract for timeouts, retries, cache TTLs, and connection/readiness state. Web UI and rigctld documentation can reference this section for consistent behavior.

---

## Timeouts

### Default values and where they apply

| Context | Default | Where it applies | Override |
|--------|--------|-------------------|----------|
| **Connect / general** | 5 s | CLI `--timeout`; `LanBackendConfig.timeout`, `SerialBackendConfig.timeout`; `IcomTransport.receive_packet()` default | CLI: `--timeout N`; backend config: `timeout=N` |
| **Discovery** | 1 s per attempt | `transport.py`: `DISCOVERY_TIMEOUT`; each discovery wait uses this; up to `DISCOVERY_RETRIES` (10) attempts | Not configurable (constants) |
| **Control-phase steps** | 2 s total, 0.3 s per read | `_control_phase.py`: discovery/status steps use short receive timeouts (e.g. 0.1 s, 0.3 s) and 2 s deadlines | Not configurable |
| **CI-V GET** | min(connection timeout, 2 s) | Single CI-V request/response: `_civ_get_timeout` in `radio.py` is `min(timeout, 2.0)` | Set via backend `timeout` (capped at 2 s for GETs) |
| **CI-V recovery wait** | 12 s | `_wait_for_civ_transport_recovery()`: max wait before giving up | `ICOM_CIV_RECOVERY_WAIT_TIMEOUT_S` (env) |
| **CI-V data watchdog** | 2 s | If no CI-V data for this long, open_close is sent to restart stream (`_civ_rx.py`: `_CIV_DATA_WATCHDOG_TIMEOUT`) | Not configurable (constant) |
| **Rigctld client idle** | 300 s | TCP client disconnected after no activity for this long | `RigctldConfig.client_timeout` |
| **Rigctld command** | 2 s | Per-command execution; CI-V round-trip must complete within this | `RigctldConfig.command_timeout` |
| **Scope assembly** | 5 s | Incomplete scope frame discarded after this (`scope.py`: `_DEFAULT_ASSEMBLY_TIMEOUT`) | `ScopeAssembler(assembly_timeout=...)` |
| **Capture (CLI)** | 10 s (spectrum) / 15 s (waterfall) | `capture_scope_frame` / `capture_scope_frames` when invoked from CLI | `--capture-timeout N` |
| **Watchdog (LAN)** | 30 s | No control-packet activity for this long triggers reconnect (`LanBackendConfig.watchdog_timeout`) | Backend config: `watchdog_timeout=N` |
| **Circuit breaker recovery** | 5 s | After circuit opens, wait this long before one probe (HALF_OPEN) | `CircuitBreaker(recovery_timeout=N)` |

### Sync API

The sync wrapper (`sync.py`) uses a single operation timeout when running async code via `asyncio.run`; default is 5.0 seconds.

### Environment variables (CI-V tuning)

- `ICOM_CIV_RECOVERY_WAIT_TIMEOUT_S` — max wait for CI-V transport recovery (default `12.0`).
- `ICOM_CIV_READY_IDLE_TIMEOUT_S` — max idle time since last CI-V data for `radio_ready` to stay true (default `5.0`).
- `ICOM_CIV_RETRY_SLICE_MS` — slice used for retry/backoff (default `150` ms).
- `ICOM_CIV_ACK_SINK_GRACE_MS` — grace time for ACK sink (default `120` ms).

---

## Cache TTLs

### State cache (shared layer)

The shared state cache used by both the Web UI and rigctld is defined in `rigctld/state_cache.py` and consumed via `_shared_state_runtime.is_cache_fresh()`.

- **Default TTL:** `DEFAULT_STATE_CACHE_TTL` in `_shared_state_runtime.py` is **0.2** seconds. This is the same as `RigctldConfig.cache_ttl` and is used so that web and rigctld share the same freshness semantics for frequency, mode, and related fields.
- **“Stale” meaning:** A cache field is considered **stale** when either:
  - its timestamp is missing (never written), or
  - `(time.monotonic() - field_ts) >= max_age_s`.
  So “stale” = not fresh: too old or never set.
- **Fallback behavior:** When the cache is not fresh, the handler (e.g. rigctld) performs a CI-V round-trip to the radio and then updates the cache. If that round-trip times out, the command returns an error (e.g. Hamlib `ETIMEOUT`); the previous cached value is not returned as a fallback for that command.

### Internal radio cache (freq/mode/rf_power)

`IcomRadio` keeps an internal `_DEFAULT_CACHE_TTL` dict (e.g. freq: 10 s, mode: 10 s, rf_power: 30 s) used for internal caching of last-known values. This is separate from the shared state cache TTL (0.2 s) used by the server layers.

---

## radio_ready and connection states

### When the radio is “ready”

- **`radio_ready`** is a property on the radio protocol and `IcomRadio`. The radio is considered **ready** when:
  1. **Connected:** `conn_state == RadioConnectionState.CONNECTED` and the CI-V transport is present and not in a UDP error state.
  2. **Not recovering:** `_civ_recovering` is false and `_civ_stream_ready` is true.
  3. **Recent CI-V data:** `_last_civ_data_received` is set and `(time.monotonic() - _last_civ_data_received) <= _civ_ready_idle_timeout` (default 5 s).

So “ready” means: connection is up, CI-V stream is up and not in recovery, and the radio has sent CI-V data within the idle timeout window.

### Relation to RadioConnectionState

`RadioConnectionState` (in `_connection_state.py`) is the high-level connection state:

- **DISCONNECTED** — not connected or cleanly disconnected.
- **CONNECTING** — `connect()` in progress.
- **CONNECTED** — authenticated and operational.
- **DISCONNECTING** — `disconnect()` in progress.
- **RECONNECTING** — connection lost; auto-reconnect is waiting to retry (e.g. after watchdog timeout).

`radio_ready` is **true** only when state is **CONNECTED** and the CI-V stream is healthy and recently active. So you can be CONNECTED but not ready (e.g. CI-V data stalled or recovering).

### Public radio health classification

`GET /api/v1/state` exposes `radioHealth` plus `healthRevision` in addition to
the backward-compatible `connection` booleans. `revision` still tracks ordinary
radio-state changes. `healthRevision` tracks classified health transitions, so
HTTP polling and frontend stores can update degraded radio status even when the
last frequency/mode/meter snapshot did not change.

The public health model separates:

- **`server_unreachable`** — client-side server/proxy loss.
- **`radio_network_lost`** — server reachable, radio link disconnected or reconnecting.
- **`radio_not_responding`** — radio link exists, but CI-V/control data is delayed or stalled.
- **`radio_powered_off_likely`** — prior radio availability plus repeated timeout/recovery evidence indicates the radio is probably off or unreachable.
- **`unknown`** — healthy/ready state or insufficient evidence.

Temporary CI-V gaps are classified as `readiness: "delayed"` before they become
`readiness: "stalled"`; ordinary jitter should not be treated as likely power
loss.

### What consumers (web, rigctld) should expect

- **Web:** The Web UI and API expose `radio_ready`, `connection`, and
  `radioHealth`. Consumers should treat `radio_ready === false` and
  non-ready `radioHealth.readiness` as “do not rely on live CI-V” (e.g. scope or
  tuning may be deferred or show last-known state).
- **Rigctld:** Commands that hit the radio will get `ETIMEOUT` or connection errors if the radio is not responding; the server does not gate commands on `radio_ready`, but the circuit breaker will open after consecutive timeouts and fail commands quickly until recovery.

The helper `radio_ready(radio)` in `web/runtime_helpers.py` normalizes readiness: it uses `radio.radio_ready` if it is a boolean, otherwise falls back to `radio.connected`; `None` is treated as not ready.

---

## Fire-and-forget and retries

### Fire-and-forget (CI-V)

- Commands that do not expect a response (e.g. some set operations) can be sent in a **fire-and-forget** way: the sender registers an ACK sink so that the corresponding ACK does not block the request queue. There is **no automatic retry** for a single fire-and-forget send; if the transport fails, the caller sees the exception.
- GET-style commands (request/response) are **single-attempt** per call: one send, wait up to `_civ_get_timeout`; on timeout, `TimeoutError` is raised (and the rigctld handler maps this to Hamlib `ETIMEOUT`). The library does not retry GETs internally.

### Discovery

- Discovery uses **retries**: up to `DISCOVERY_RETRIES` (10) attempts, each with `DISCOVERY_TIMEOUT` (1 s) wait. If no response after that, connect fails with `TimeoutError`.

### Circuit breaker (rigctld)

- After **failure_threshold** consecutive timeouts (default 3), the circuit opens: subsequent commands fail immediately with `ETIMEOUT` without calling the radio.
- After **recovery_timeout** seconds (default 5) in the OPEN state, the circuit goes to HALF_OPEN and one probe command is allowed. Success → CLOSED; failure → OPEN again.

### Watchdog and reconnect

- If the control connection has no activity for **watchdog_timeout** (default 30 s for LAN), the runtime transitions to RECONNECTING and will try to reconnect. This is not a per-command retry but a connection-level recovery.

---

## Summary table

| Concept | Default / value | Config / override |
|--------|------------------|-------------------|
| Connect timeout | 5 s | CLI `--timeout`, backend `timeout` |
| CI-V GET timeout | min(5, 2) = 2 s | Backend `timeout` (capped at 2 s for GETs) |
| Rigctld command timeout | 2 s | `RigctldConfig.command_timeout` |
| Rigctld client idle timeout | 300 s | `RigctldConfig.client_timeout` |
| State cache TTL (web/rigctld) | 0.2 s | `RigctldConfig.cache_ttl`, CLI `--cache-ttl` |
| radio_ready idle window | 5 s | `ICOM_CIV_READY_IDLE_TIMEOUT_S` |
| CI-V data watchdog | 2 s | Constant |
| Circuit breaker recovery | 5 s | `CircuitBreaker(recovery_timeout=...)` |
