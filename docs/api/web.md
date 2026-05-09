# Web Server API

Built-in HTTP + WebSocket interface used by the browser UI and automation clients.

## Stability Contract

The managed-supervisor contract is versioned separately from the browser UI.
The current contract version is `1`.

Stable routes are source-compatible within the same major `/api/v1` namespace:

- existing required response fields remain present;
- new optional fields may be added without a version bump;
- new enum/string values may be added when clients can ignore unknown values;
- removing fields, changing field type, or changing route semantics requires a
  new versioned route or an explicit migration note.

Stable contract metadata is maintained in `rigplane.web.api_contract` and
covered by tests. Pro and other supervisors should depend on the stable routes
below, not on browser static assets, private handler classes, or routes marked
diagnostic/experimental.

## Auth Model

If web server is started with `--auth-token`, `--auth-token-file`, or
`RIGPLANE_AUTH_TOKEN` in managed mode, all `/api/*` HTTP routes require:

```
Authorization: Bearer <token>
```

WebSocket routes additionally allow query token:

```
ws://host:8080/api/v1/ws?token=<token>
```

## HTTP Endpoints

### Stable Supervisor Endpoints

These routes are part of the Pro/supervisor compatibility surface:

| Method | Path | Purpose |
|-------|------|---------|
| `GET` | `/healthz` | Process/API liveness, no API token required |
| `GET` | `/readyz` | Station readiness, returns `503` until radio is ready |
| `GET` | `/api/v1/runtime` | Process, bind, radio, rigctld, bridge, and diagnostic runtime status |
| `GET` | `/api/v1/info` | Runtime model/capability summary |
| `GET` | `/api/v1/state` | Canonical current state snapshot |
| `GET` | `/api/v1/capabilities` | Full profile-backed capabilities |
| `GET` | `/api/v1/audio/analysis` | Audio analysis snapshot when analyzer is active |
| `GET` | `/api/v1/bridge` | Audio bridge status |
| `POST` | `/api/v1/bridge` | Start audio bridge |
| `DELETE` | `/api/v1/bridge` | Stop audio bridge |

### Other Web UI Endpoints

| Method | Path | Purpose |
|-------|------|---------|
| `GET` | `/api/v1/dx/spots` | Buffered DX spots |
| `GET` | `/api/v1/band-plan/config` | Active band-plan region |
| `GET` | `/api/v1/band-plan/layers` | Band-plan layers metadata |
| `GET` | `/api/v1/band-plan/segments?start=<hz>&end=<hz>&layers=<csv>` | Band-plan overlay segments |
| `GET` | `/api/v1/eibi/status` | EiBi loader status |
| `GET` | `/api/v1/eibi/stations` | EiBi station list (paged/filterable) |
| `GET` | `/api/v1/eibi/segments?start=<hz>&end=<hz>&on_air=true` | EiBi overlay segments |
| `GET` | `/api/v1/eibi/identify?freq=<hz>&tolerance=<hz>` | Identify probable broadcast stations |
| `GET` | `/api/v1/eibi/bands` | EiBi frequency bands |

### Control Endpoints

| Method | Path | Purpose |
|-------|------|---------|
| `POST` | `/api/v1/radio/connect` | Connect/reconnect radio control path |
| `POST` | `/api/v1/radio/disconnect` | Disconnect radio control path |
| `POST` | `/api/v1/radio/power` | Power on/off via CI-V power control |
| `POST` | `/api/v1/band-plan/config` | Change active region and reload band plans |
| `POST` | `/api/v1/eibi/fetch` | Fetch/refresh EiBi dataset |

### WebSocket Routes

Stable supervisor routes:

| Path | Purpose | Availability |
|------|---------|--------------|
| `/api/v1/ws` | Control events and commands | always |
| `/api/v1/scope` | Hardware or audio FFT spectrum stream | when scope or audio FFT is available |
| `/api/v1/audio` | Audio control and media frames | when radio/audio backend supports audio |
| `/api/v1/audio-scope` | Audio FFT spectrum stream | when audio FFT is available |

WebSocket auth accepts the same bearer header as HTTP. Query token auth is also
accepted for browser/WebSocket clients that cannot set headers.

### Internal And Diagnostic Surface

Browser static files, `src/rigplane/web/static*`, handler class names, queue
shapes, diagnostic upload routes, EiBi/Band Plan helpers, and DX cluster helper
routes are not the Pro supervisor contract unless they are promoted into
`rigplane.web.api_contract`.

---

## `GET /healthz`

Process liveness probe for local supervisors. This endpoint is intentionally
outside `/api/*`, so it does not require bearer auth.

```json
{
  "status": "ok",
  "pid": 12345,
  "version": "2.0.3"
}
```

## `GET /readyz`

Station readiness probe. Returns HTTP `200` when the attached radio is ready
and HTTP `503` while the process is alive but the station is not ready.

```json
{
  "status": "ready",
  "radioReady": true
}
```

## `GET /api/v1/runtime`

Machine-readable runtime status for managed local supervisors and diagnostics.
When auth is configured, this endpoint requires the same bearer token as other
`/api/*` routes.

```json
{
  "pid": 12345,
  "uptimeSeconds": 12.3,
  "version": "2.0.3",
  "bind": { "host": "127.0.0.1", "port": 8080 },
  "logPath": "/Users/me/Library/Logs/rigplane.log",
  "authRequired": true,
  "backend": "rigplane",
  "radio": {
    "model": "IC-7610",
    "connected": true,
    "controlConnected": true,
    "radioReady": true
  },
  "rigctld": {
    "enabled": true,
    "address": "127.0.0.1:4532"
  },
  "bridge": {
    "enabled": false,
    "running": false
  },
  "lastError": null
}
```

Managed runtimes also emit a single machine-readable startup event to stdout
after the web listener has bound successfully. Supervisors should use this JSON
line as the startup contract instead of parsing the human-readable banner:

```json
{
  "type": "rigplane.runtime.started",
  "pid": 12345,
  "baseUrl": "http://127.0.0.1:58421",
  "healthUrl": "http://127.0.0.1:58421/healthz",
  "runtimeUrl": "http://127.0.0.1:58421/api/v1/runtime",
  "logPath": "/Users/me/Library/Logs/rigplane.log"
}
```

---

## `GET /api/v1/info`

Version, model, capability summary, and connection metadata.

```json
{
  "server": "rigplane",
  "version": "0.18.0",
  "proto": 1,
  "radio": "IC-7300",
  "model": "IC-7300",
  "capabilities": {
    "hasSpectrum": true,
    "hasAudio": true,
    "hasTx": true,
    "hasDualReceiver": false,
    "hasTuner": false,
    "hasCw": true,
    "maxReceivers": 1,
    "tags": ["audio", "cw", "meters", "scope", "tx"],
    "modes": ["USB", "LSB", "CW", "CW-R", "AM", "FM", "RTTY", "RTTY-R"],
    "filters": ["FIL1", "FIL2", "FIL3"],
    "vfoScheme": "ab",
    "hasLan": false
  },
  "connection": {
    "rigConnected": true,
    "radioReady": true,
    "controlConnected": true,
    "wsClients": 2
  }
}
```

## `GET /api/v1/state`

Canonical full state payload for web consumers (camelCase keys).

- Includes `revision`, `healthRevision`, and `updatedAt`.
- Includes `connection` object (`rigConnected`, `radioReady`, `controlConnected`).
- Includes `radioHealth`, a classified server/radio health contract.
- For single-receiver profiles, `sub` key is omitted.
- Supports `ETag` based on `revision` and `healthRevision` for conditional
  requests, so radio-health-only changes are not hidden behind `304 Not Modified`.

```json
{
  "main": { "freqHz": 14074000, "mode": "USB", "filter": 1 },
  "revision": 42,
  "healthRevision": 7,
  "updatedAt": "2026-03-15T10:00:00+00:00",
  "radioDetail": { "status": "connected" },
  "radioHealth": {
    "serverReachable": true,
    "radioLink": "connected",
    "readiness": "ready",
    "likelyCause": "unknown",
    "sinceMs": 0,
    "lastError": null
  },
  "wsClients": { "scope": 1, "control": 1, "audio": 0 },
  "connection": {
    "rigConnected": true,
    "radioReady": true,
    "controlConnected": true
  }
}
```

`radioHealth.likelyCause` distinguishes these public states:

| Value | Meaning |
|---|---|
| `server_unreachable` | Browser/client cannot reach the web or proxy server. The server normally cannot emit this for itself; clients derive it from HTTP/WS failures. |
| `radio_network_lost` | Server is reachable, but the radio link is disconnected or reconnecting. |
| `radio_not_responding` | Radio link still exists, but CI-V/control data is delayed or stalled. |
| `radio_powered_off_likely` | Server is reachable, the radio was previously available, and repeated timeout/recovery evidence suggests the hardware is off or unreachable. |
| `unknown` | Insufficient evidence or healthy/ready state. |

## `GET /api/v1/capabilities`

Profile-backed capabilities used to build dynamic UI.

Notable fields:

| Field | Type | Notes |
|------|------|-------|
| `receivers` | `int` | Receiver count from active profile |
| `vfoScheme` | `"ab"` \| `"main_sub"` | VFO label scheme |
| `freqRanges[].bands[].bsrCode` | `int` (optional) | Band Stack Register code for `set_band` |
| `scopeSource` | `"hardware"` \| `"audio_fft"` \| `null` | Spectrum data source |
| `scopeConfig.defaultSpan` | `int` | Hardware scope span or audio FFT bandwidth |
| `audioConfig` | object | Web audio transport defaults |

When radio has audio but no hardware scope support, backend enables `AudioFftScope`
and reports `scopeSource: "audio_fft"`.

---

## WebSocket Endpoints

| Path | Direction | Payload |
|------|-----------|---------|
| `/api/v1/ws` | bi-directional | JSON commands/events/state updates |
| `/api/v1/scope` | server -> client | Binary scope frames |
| `/api/v1/audio` | bi-directional | JSON control + binary audio frames |

### `/api/v1/ws` command envelope

```json
{"type":"cmd","id":"42","name":"set_freq","params":{"freq":14074000,"receiver":0}}
```

Response:

```json
{"type":"response","id":"42","ok":true,"result":{"freq":14074000,"receiver":0}}
```

Error response:

```json
{"type":"response","id":"42","ok":false,"error":"command_failed","message":"..."}
```

Rate-limited high-frequency `set_*` commands are ACKed with:

```json
{"type":"response","id":"42","ok":true,"result":{"throttled":true}}
```

### State update stream (`/api/v1/ws`)

Server publishes `state_update` messages in two shapes:

- Full snapshot:
  ```json
  {"type":"state_update","data":{"type":"full","data":{...},"revision":1}}
  ```
- Delta update:
  ```json
  {"type":"state_update","data":{"type":"delta","changed":{"main":{"freqHz":14075000}},"revision":2}}
  ```

### `set_band` workflow (`/api/v1/ws`)

```json
{"type":"cmd","id":"73","name":"set_band","params":{"band":5}}
```

`band` is BSR code from `freqRanges[].bands[].bsrCode`.
Backend path:

1. Try BSR recall (`0x1A 0x01 <band> 0x01`).
2. If recall fails, fallback to profile `default_hz` for matching `bsr_code`.
3. If profile has no matching `bsr_code`, command is acknowledged but no retune happens.

---

## Operational Runbook

### Power on/off through HTTP

```bash
curl -X POST http://127.0.0.1:8080/api/v1/radio/power \
  -H "Content-Type: application/json" \
  -d '{"state":"on"}'
```

### Audio bridge start/status/stop

```bash
curl -X POST http://127.0.0.1:8080/api/v1/bridge
curl http://127.0.0.1:8080/api/v1/bridge
curl -X DELETE http://127.0.0.1:8080/api/v1/bridge
```

### Change band-plan region

```bash
curl -X POST http://127.0.0.1:8080/api/v1/band-plan/config \
  -H "Content-Type: application/json" \
  -d '{"region":"IARU-R1"}'
```

### Refresh EiBi cache

```bash
curl -X POST http://127.0.0.1:8080/api/v1/eibi/fetch \
  -H "Content-Type: application/json" \
  -d '{"force":true}'
```

## Modules

- `server.py` — asyncio HTTP/WebSocket server, endpoint routing
- `handlers.py` — control/scope/audio channel handlers
- `radio_poller.py` — state polling and command queue execution
- `runtime_helpers.py` — canonical public state/capability shaping
- `dx_cluster.py` — DX spot ingest and buffering

## See Also

- [Web UI Guide](../guide/web-ui.md)
- [Audio](audio.md)
- [Scope](scope.md)
