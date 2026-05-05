# `web` layer

## Charter

WebSocket + HTTP server for the rigplane Web UI. Hosts the built-in
browser app — real-time spectrum/waterfall, radio control, audio
streaming — accessible from any browser on the LAN. No external web
framework: pure stdlib HTTP + RFC 6455 WebSocket, by design (Tier-1
Open-Core principle: zero-bloat headless server).

## Public API

`web/__init__.py` exports:

- `WebServer` — async server class; entry point.
- `WebConfig` — typed runtime config (host/port/TLS/auth/…).
- `run_web_server(...)` — convenience runner used by the CLI.

Internal layout:

- `web/server.py` — server core; `start()`/`stop()` delegate to
  `web/web_startup.py`; routing delegates to `web/web_routing.py`.
- `web/handlers/` — control / scope / audio / state-update handlers.
- `web/radio_poller.py` — 200ms state poller; emits delta-encoded
  state_update over `/api/v1/ws`.
- `web/_delta_encoder.py` — payload diffing.
- `web/runtime_helpers.py`, `web/band_plan.py`, `web/dx_cluster.py`,
  `web/eibi.py`, `web/discovery.py`, `web/tls.py`, `web/protocol.py`,
  `web/rtc.py`, `web/websocket.py` — supporting modules.
- `web/static/` — built frontend (symlinked from `frontend/dist/`).

## Allowed dependencies

`core`, `commands`, `profiles`, `audio`, `scope`, `dsp`, `runtime`
(plan §3 matrix row `web`). Note `web` does **not** depend on
`backends` — `web_startup` consumes `StatePollable.create_state_poller`
and detects optional features via `isinstance(radio, *Capable)` rather
than backend-id branching (#1298, #1323-#1326). The transitional
`web → backends` ignores listed in plan §3.3 are gone.

`web` ⊥ `rigctld` is enforced by `independence-top` in `.importlinter`.

## Forbidden patterns

- `from rigplane.rigctld` — independence contract.
- Direct transport calls. Web talks to `runtime.IcomRadio`, never to
  `core.transport` directly. CLAUDE.md rule.
- Direct backend-id checks (`if radio.backend_id == "yaesu_cat":`).
  Use Capability Protocols + `isinstance` (epic #1322).
- Telemetry or analytics. Open-core hard constraint
  (`docs/architecture/open-core-policy.md`).
- Direct store/transport imports inside `frontend/components-v2/panels/`
  — the lint config bans them (frontend layering, see CLAUDE.md
  Architecture section). This LAYER.md covers the Python web server;
  frontend layering is enforced separately by ESLint.

## Common operations

- **Add a control endpoint** → handler in `web/handlers/control.py`
  (read-only commands go through the dispatch table per #1263); wire
  into `web_routing.py` if it is a new path; cover with
  `tests/test_web_*.py`.
- **Add a state field to the WS payload** → extend `RadioPoller`
  / `_delta_encoder.py`; verify the schema test
  `tests/test_web_state_*` and the frontend adapter accept it.
- **Touch `web_startup.py`** → keep all backend-aware decisions behind
  Capability Protocols; do not import from `backends/*` directly.

## See also

- `docs/plans/2026-04-29-modularization-plan.md` §1.2, §3.
- `docs/architecture/open-core-policy.md` — no-telemetry, headless
  sacred constraints.
- `docs/plans/2026-04-12-target-frontend-architecture.md` — frontend
  layering (separate concern).
- `tests/test_web_*.py`, `tests/test_runtime_helpers*.py`.
