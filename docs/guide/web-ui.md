---
description: RigPlane's built-in browser Web UI for ham radio control — live tuning, scope and waterfall, meters, and RX/TX audio streaming from any browser.
---

# Web UI

rigplane ships with a built-in browser UI for live control, scope/waterfall, meters,
and RX/TX audio.

This page documents the **current implementation** (Svelte frontend + asyncio backend),
public interfaces, and operational workflows.

## Quick Start

```bash
# Default: bind all interfaces on port 8080
rigplane web

# Explicit host/port
rigplane web --host 0.0.0.0 --port 9090

# Require API/WebSocket auth token
rigplane web --auth-token "change-me"
```

Open `http://<server-ip>:8080` (or your custom port).

## What Runs Where

| Layer | Implementation | Notes |
|------|----------------|-------|
| HTTP + WebSocket server | Python asyncio | Pure asyncio, no external web framework |
| WS handlers | Per-channel handlers | Control, scope, meters, and audio channels |
| Frontend app | Svelte + TypeScript | Built assets served from package by default |

The backend manages reconnect and recovery when the radio link drops; scope enable is deferred until `radio_ready` is true.

## Public HTTP Interface

| Method | Path | Purpose |
|-------|------|---------|
| `GET` | `/` | Serve UI entry page (`index.html`) |
| `GET` | `/api/v1/info` | Version, model, connection status, runtime capability summary |
| `GET` | `/api/v1/state` | Current radio state snapshot (camelCase, includes revision + updatedAt) |
| `GET` | `/api/v1/capabilities` | Capabilities, frequency ranges, supported modes/filters, scope/audio config |
| `GET` | `/api/v1/dx/spots` | Buffered DX spots |
| `GET` | `/api/v1/bridge` | Audio bridge status |

### Advanced operational HTTP endpoints

These are primarily used by automation, deployment scripts, and operator tooling:

| Method | Path | Purpose |
|-------|------|---------|
| `POST` | `/api/v1/radio/connect` | Trigger backend connect/reconnect |
| `POST` | `/api/v1/radio/disconnect` | Trigger backend disconnect |
| `POST` | `/api/v1/radio/power` | CI-V power control (`{"state":"on" \| "off"}`) |
| `POST` | `/api/v1/bridge` | Start audio bridge |
| `DELETE` | `/api/v1/bridge` | Stop audio bridge |
| `GET` | `/api/v1/band-plan/config` | Active band-plan region |
| `POST` | `/api/v1/band-plan/config` | Change region + reload band plans |
| `GET` | `/api/v1/band-plan/layers` | Loaded overlay layers |
| `GET` | `/api/v1/band-plan/segments?...` | Band-plan segments for selected range |
| `POST` | `/api/v1/eibi/fetch` | Download/refresh EiBi DB |
| `GET` | `/api/v1/eibi/status` | EiBi loader status |
| `GET` | `/api/v1/eibi/stations` | EiBi station list (paged/filterable) |
| `GET` | `/api/v1/eibi/segments?...` | EiBi overlay segments |
| `GET` | `/api/v1/eibi/identify?...` | Broadcast station identification |
| `GET` | `/api/v1/eibi/bands` | EiBi band list |

### Auth behavior (`--auth-token`)

- `GET /api/*` requires `Authorization: Bearer <token>`.
- WebSocket endpoints accept either:
  - `Authorization: Bearer <token>`, or
  - `?token=<token>` query parameter.
- Static files (`/`, JS, CSS, assets) are still served without token.

!!! note "Audio bridge control path"
    Runtime bridge activation is typically done from CLI flags
    (`rigplane web --bridge ...` / `--bridge-rx-only`).

## WebSocket Channels

| Endpoint | Direction | Payload type | Purpose |
|---------|-----------|--------------|---------|
| `/api/v1/ws` | bidirectional | JSON text | Commands, responses, notifications, `state_update` stream |
| `/api/v1/scope` | server -> client | Binary | Scope/waterfall frames |
| `/api/v1/meters` | server -> client | Binary | Meter frames (`meters_start` / `meters_stop` control messages) |
| `/api/v1/audio` | bidirectional | JSON + Binary | RX stream + TX uplink |

## Control Channel Workflow (`/api/v1/ws`)

### Command envelope

```json
{"type":"cmd","id":"42","name":"set_freq","params":{"freq":14074000,"receiver":0}}
```

Server response:

```json
{"type":"response","id":"42","ok":true,"result":{"freq":14074000,"receiver":0}}
```

### `state_update` payload formats

The backend emits `state_update` in two shapes:

1. Full snapshot:

```json
{"type":"state_update","data":{"type":"full","data":{"main":{"freqHz":14074000}},"revision":1}}
```

2. Delta update (only changed fields):

```json
{"type":"state_update","data":{"type":"delta","changed":{"main":{"freqHz":14074100}},"revision":2}}
```

Client integrations should support both formats. Assuming only full snapshots causes
state drift when delta updates are enabled.

### Connection control messages

- `{"type":"radio_connect","id":"..."}`
- `{"type":"radio_disconnect","id":"..."}`

If backend recovery is already in progress, `radio_connect` returns:

```json
{"type":"response","ok":false,"error":"backend_recovering"}
```

### Common commands

- Tuning/control: `set_freq`, `set_mode`, `set_filter`, `set_band`, `ptt`
- RF/audio levels: `set_power`, `set_rf_gain`, `set_af_level`, `set_squelch`
- DSP/features: `set_nb`, `set_nr`, `set_digisel`, `set_ipplus`, `set_comp`
- Receiver/routing: `select_vfo`, `vfo_swap`, `vfo_equalize`, `set_dual_watch`
- Scope control: `switch_scope_receiver`, `set_scope_during_tx`, `set_scope_center_type`

### Band switching with `set_band` (`bsrCode` workflow)

`set_band` is intended for profile bands that expose `bsrCode` in
`GET /api/v1/capabilities`:

```json
{
  "freqRanges": [
    {
      "label": "HF",
      "bands": [
        { "name": "20m", "default": 14200000, "bsrCode": 5 },
        { "name": "60m", "default": 5357000 }
      ]
    }
  ]
}
```

Control command:

```json
{"type":"cmd","id":"73","name":"set_band","params":{"band":5}}
```

Backend flow (`src/rigplane/web/radio_poller.py`):

1. Read Band Stack Register via CI-V `0x1A 0x01 <band> 0x01` (register 1).
2. If response is valid, apply recalled frequency and mode/filter.
3. If recall fails (timeout/exception/short response), fallback to profile
   `default_hz` for the matching `bsr_code`.
4. If no band with that `bsr_code` exists, no retune is applied and a warning is logged.

Practical rule:

- If a band has `bsrCode`, use `set_band` (radio recalls last freq/mode for that band).
- If `bsrCode` is absent, use `set_freq` with band `default`.

## Audio Workflow and Constraints

### RX/TX lifecycle

1. Client enables RX:
   - `{"type":"audio_start","direction":"rx"}`
2. Client requests PTT ON on control channel (`ptt: true`).
3. Client enables TX stream:
   - `{"type":"audio_start","direction":"tx"}`
   - then sends binary TX frames to `/api/v1/audio`.
4. Client requests PTT OFF.
5. Backend stops TX stream and restarts RX stream.

### Important constraints

- Browser TX frames are ignored while PTT is OFF (frontend and backend both enforce this).
- IC-7610 LAN behavior is effectively half-duplex for web audio flow: after TX ends,
  RX is restarted explicitly by backend logic.
- If audio send blocks for too long, server closes stale audio WS path and client
  reconnect logic re-establishes the stream.

## Frontend Runtime Workflow (Current Implementation)

The browser app startup path is implemented in `frontend/src/App.svelte` and
`frontend/src/lib/transport/http-client.ts`.

### Boot sequence

1. Initialize the skin selector from URL/localStorage (see "Layout and skin resolution" below).
2. Register MediaSession handlers (when API is available).
3. Start HTTP polling loop for `/api/v1/state` (interval set to `1000ms` in app bootstrap).
4. Start battery monitor (progressive enhancement) and adjust polling multiplier.
5. Fetch capabilities once from `/api/v1/capabilities`.
6. Connect control WebSocket (`/api/v1/ws`) and subscribe to events.

### Runtime ownership (actual code paths)

The frontend keeps one behavior path and splits responsibilities by module:

| Responsibility | Current implementation path | Notes |
|---|---|---|
| Runtime read/write entry point | `frontend/src/lib/runtime/frontend-runtime.ts` | Exposes state, capabilities, connection snapshot, audio actions, and command send helpers. |
| UI view-model mapping | `frontend/src/components-v2/wiring/state-adapter.ts` | Converts raw runtime state into panel props. |
| WS command dispatch | `frontend/src/components-v2/wiring/command-bus.ts` | Maps UI callbacks to `sendCommand(...)` calls and optimistic state patches. |
| HTTP system actions | `frontend/src/lib/runtime/system-controller.ts` via `runtime.system.*` | Owns radio connect/disconnect, power on/off, and EiBi identify calls. |

Current skin files in `frontend/src/skins/*` delegate to `components-v2/layout/*`;
behavior is implemented in the layout and wiring modules listed above.

### Backend CI-V poll cadence (state freshness)

`src/rigplane/web/radio_poller.py` interleaves meter and state queries:

- even cycles -> meter query
- odd cycles -> one state query

Poll interval is backend-specific:

- LAN backends: `25ms` fast cycle (`_FAST_INTERVAL`)
- serial backends: `100ms` fast cycle (`_FAST_INTERVAL_SERIAL`)

LAN meter polling uses a two-tier strategy:

- High tier (most cycles):
  - RX path: S-meter (`0x15 0x02`)
  - TX path: rotates RF power (`0x15 0x11`), SWR (`0x15 0x12`), ALC (`0x15 0x13`)
- Low tier (every 5th high cycle while RX): rotates COMP (`0x15 0x14`), Vd (`0x15 0x15`), Id (`0x15 0x16`)

Serial meter polling keeps a simpler high-priority loop focused on responsiveness:
S-meter, RF power, S-meter, SWR.

Practical implication: S-meter and TX safety meters update most frequently, while
secondary telemetry (COMP/Vd/Id) is intentionally sampled less often.

### State polling and conditional requests

- Polling uses `If-None-Match` with the previous `ETag`.
- `304 Not Modified` is treated as a successful poll with no state payload.
- The state `ETag` includes both `revision` and `healthRevision`. A
  radio-health-only transition therefore returns `200` with a fresh payload even
  when frequency/mode/meter state did not change.
- On transient HTTP errors, cached ETag is cleared to force a fresh `200` response.
- After repeated HTTP failures, the connection store marks HTTP as disconnected until recovery.

### Battery-aware polling behavior

`frontend/src/lib/utils/battery.ts` adjusts polling interval multiplier:

| Battery state | Multiplier | Effective poll interval (base 1000ms) |
|---|---:|---:|
| Charging or >20% | `1x` | `1000ms` |
| 10–20% and not charging | `2x` | `2000ms` |
| <=10% and not charging | `4x` | `4000ms` |

If the Battery Status API is unavailable, multiplier stays at `1x`.

### MediaSession mappings (mobile/headset controls)

When `navigator.mediaSession` is supported:

- `previoustrack` -> tune down one step (`set_freq`)
- `nexttrack` -> tune up one step (`set_freq`)
- `play` -> `ptt` ON
- `pause` -> `ptt` OFF

Implementation path: `frontend/src/lib/media/media-session.ts`.

!!! note "Receiver routing in MediaSession tuning"
    MediaSession tuning currently sends `set_freq` with `receiver: 0`
    (MAIN receiver).

## Keyboard Shortcuts (Desktop)

| Key | Action |
|-----|--------|
| `F1`-`F11` | Jump to preset amateur bands (160m .. 6m) |
| `M` | Cycle mode through supported modes |
| `ArrowUp` / `ArrowRight` | Tune up by current step |
| `ArrowDown` / `ArrowLeft` | Tune down by current step |
| `Space` | Toggle PTT |
| `Escape` | Close frequency-entry modal |

## Mobile Interaction Model

Mobile-first interaction logic is implemented in:

- `frontend/src/components-v2/layout/RadioLayout.svelte`
- `frontend/src/components-v2/layout/MobileRadioLayout.svelte`
- `frontend/src/components-v2/controls/BottomSheet.svelte`
- `frontend/src/components-v2/controls/CollapsiblePanel.svelte`

### Layout and skin resolution

Skin/layout is resolved in `frontend/src/components-v2/layout/RadioLayout.svelte`
using `resolveSkinId(...)` and `getLayoutMode()`:

1. `isMobile` is true when:
   - `min(window.innerWidth, window.innerHeight) < 640`, or
   - touch device and `min(window.innerWidth, window.innerHeight) < 500`.
2. If `isMobile` is true -> mobile skin.
3. Otherwise, layout preference from localStorage key `rigplane-layout` is used:
   - `lcd` -> amber LCD skin
   - `standard` -> desktop v2 skin
   - `auto` -> desktop v2 when any scope is available, amber LCD when no scope is available.

Status bar layout button behavior (`cycleLayoutMode(...)`):

- if scope is available: `auto -> lcd -> standard -> auto`
- if scope is not available: selecting layout forces `lcd`

### Bottom sheet gestures

Bottom sheets support swipe-to-dismiss:

- drag starts from the handle, or from content when scroll is at top
- downward dismiss triggers when either:
  - drag distance is >30% of sheet height, or
  - swipe velocity is >0.5 px/ms

### Collapsible panel swipe gestures

Panel headers support vertical swipe:

- swipe down collapses an expanded panel
- swipe up expands a collapsed panel
- threshold: 30px, with vertical-dominant movement guard

### Mobile PTT workflow

Mobile PTT button behavior:

- press-and-hold -> TX while held
- double-tap within 350ms -> latch TX lock
- tap while latched -> unlock and return to idle
- safety timeout forcibly disengages TX after 3 minutes

## Operations Runbook

### Run with DX cluster overlays

```bash
rigplane web --dx-cluster dxc.nc7j.com:7373 --callsign YOURCALL
```

### Run with custom UI assets

```bash
rigplane web --static-dir /opt/icom-ui/dist
```

### Quick health checks

```bash
curl http://127.0.0.1:8080/api/v1/info
curl http://127.0.0.1:8080/api/v1/state
```

### Verify v2 StatusBar system actions

These are the HTTP calls used by `runtime.system.*` in `StatusBar.svelte` and
`LcdLayout.svelte`:

```bash
# Trigger backend reconnect/disconnect
curl -X POST http://127.0.0.1:8080/api/v1/radio/connect
curl -X POST http://127.0.0.1:8080/api/v1/radio/disconnect

# Remote power control
curl -X POST http://127.0.0.1:8080/api/v1/radio/power \
  -H "Content-Type: application/json" \
  -d '{"state":"on"}'
curl -X POST http://127.0.0.1:8080/api/v1/radio/power \
  -H "Content-Type: application/json" \
  -d '{"state":"off"}'

# Optional EiBi "now playing" lookup used by status bar
curl "http://127.0.0.1:8080/api/v1/eibi/identify?freq=14074000"
```

If these endpoints return non-2xx, `runtime.system.*` raises the backend text
as an error and UI actions show an alert with that message.

## Dynamic UI — Radio-Aware Controls

The Web UI adapts to the active radio's capabilities. Capabilities are fetched once
from `GET /api/v1/capabilities` on startup and cached in
`frontend/src/lib/stores/capabilities.svelte.ts`.

### VFO Labels

VFO button labels change based on the radio's VFO scheme:

| Radio | Scheme | Button A label | Button B label |
|-------|--------|----------------|----------------|
| IC-7610 | `main_sub` | **MAIN** | **SUB** |
| IC-7300 | `ab` | **VFO A** | **VFO B** |

The `vfoLabel()` function in the capabilities store drives this:

```typescript
// Returns "MAIN" or "VFO A" depending on active profile
vfoLabel('A')

// Returns "SUB" or "VFO B"
vfoLabel('B')
```

### Capability-Based UI Guards

Controls that depend on hardware features are automatically hidden or disabled when the
active radio profile doesn't support them:

| Control | Capability flag | Visible on IC-7610 | Visible on IC-7300 |
|---------|----------------|--------------------|--------------------|
| DIGI-SEL toggle | `digisel` | ✅ | ❌ hidden |
| IP+ toggle | `ip_plus` | ✅ | ❌ hidden |
| SUB receiver panel | `dual_rx` | ✅ | ❌ hidden |
| TX controls, PTT | `tx` | ✅ | ✅ |
| Audio RX/TX | `audio` | ✅ | ✅ |
| Scope/waterfall | `scope` | ✅ | ✅ |

Use `hasCapability(name)` to check for a capability in Svelte components:

```typescript
import { hasCapability } from '$lib/stores/capabilities.svelte';

// In a Svelte component template:
// {#if hasCapability('digisel')}
//   <DigiSelControl />
// {/if}
```

### State Endpoint and Receiver Count

`GET /api/v1/state` omits the `sub` receiver for single-receiver radios.
Frontend code should guard against the missing `sub` key rather than assuming it is
always present.

```typescript
// Safe receiver access
const sub = state.sub ?? null;
```

## Common Pitfalls for Developers

- **Capability-gated commands:** commands fail with `command_failed` if active profile
  does not expose required capability (for example, `set_rf_gain` on unsupported radios).
- **Receiver indexing:** many commands expect `receiver=0` (MAIN) or `receiver=1` (SUB)
  and validate against runtime profile receiver count.
- **`sub` may be absent:** `GET /api/v1/state` omits `sub` for single-receiver radios —
  always guard with a null check.
- **VFO commands:** use `select_vfo("A")` / `select_vfo("B")` regardless of scheme;
  the backend translates to the correct CI-V codes for the active profile.
- **Authoritative state source:** use `state_update` payloads as source of truth; optimistic
  UI updates can be overwritten by server state.
- **Scope recovery behavior:** scope enable/re-enable is deferred until `radio_ready=true`;
  all-zero scope frames trigger automatic re-enable attempts.
- **UI version assumptions:** mobile v2 interactions (sheet/panel swipe, touch-first PTT flow)
  require `?ui=v2` or previously stored v2 selection; default is v1.
- **Layout mode expectations:** v2 layout preference (`rigplane-layout`) is capability-aware;
  `auto` resolves to desktop only when any scope exists, otherwise LCD is selected.
- **System action error surfacing:** connect/disconnect/power actions in v2 call
  `runtime.system.*` and surface backend HTTP errors directly in the UI.
- **Battery API availability:** polling slowdown on low battery is best-effort; browsers without
  `navigator.getBattery()` remain on normal polling cadence.
- **MediaSession availability:** headset/lock-screen controls are enabled only when
  `navigator.mediaSession` exists.

## Related Docs

- [CLI Reference](cli.md#web)
- [Troubleshooting](troubleshooting.md)
- [Reliability semantics](../internals/reliability-semantics.md) — timeouts, cache TTLs, and `radio_ready` / connection state behavior.
