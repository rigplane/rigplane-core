# Radio State Pipeline Release Validation

Status: MOR-348 executable release gate
Date: 2026-06-03

This is the canonical end-to-end readiness checklist for the radio-state-pipeline
migration. It verifies the shared `StateStore`, acquisition scheduler,
command/read-after-write path, Web HTTP/WebSocket projections, rigctld
consumers, frontend v2 controls, and cleanup guards as one release gate.

This document is intentionally separate from `docs/validation/*`. The
`docs/validation/*` tree covers profile and CAT validation tooling. This file
covers release/E2E readiness for the migrated state delivery pipeline.

## Completion Rule

The migration is complete only when:

1. Every automated blocker gate below passes at the integration head.
2. The fake-backend live Web v2 run passes with no retained server process.
3. Hardware blocker cases either pass or have an explicit release decision.
4. Residual shims listed in this document are accepted as non-blocking.

## Automated Gates

Run commands from the repository root unless `cwd` says `frontend/`.

| Gate | cwd | Command | Expected outcome | Blocks release |
|---|---|---|---|---|
| Python full regression | root | `uv run pytest tests/ -q --tb=short` | All non-opt-in tests pass. No hardware dependency. | Yes |
| Python E2E marker | root | `uv run pytest -m e2e -q --tb=short` | CLI, PCM, diagnostics, and other marked E2E tests pass or are explicitly deselected if no marker matches. | Yes |
| Focused state pipeline batch | root | `uv run pytest -q tests/test_state_pipeline_contracts.py tests/test_state_store.py tests/test_acquisition_scheduler.py tests/test_state_pipeline_diagnostics.py tests/test_web_server_coverage.py tests/test_rigctld_handler.py tests/test_civ_rx_coverage.py tests/test_radio_poller_coverage.py tests/test_delta_encoder.py tests/test_bsr_band_switching.py --tb=short` | StateStore invariants, freshness, acquisition, Web, rigctld, CI-V, poller, delta encoder, and BSR tests pass. | Yes |
| MOR-347/MOR-346 web type boundary | root | `uv run mypy --strict src/rigplane/web` | Web boundary type-checks under the same focused CI command used by quick/full/publish workflows. | Yes |
| Python lint | root | `uv run ruff check src tests` | No Ruff violations. | Yes |
| Import boundaries | root | `uv run lint-imports` | Layer contracts remain intact. | Yes |
| Whitespace/conflict markers | root | `git diff --check 250ad8ebf935708ddb4ffc6b3ec02ca01d54b6f2..HEAD` | No whitespace errors or conflict markers in the MOR-348 range. Use `origin/codex/mor-334-radio-state-pipeline..HEAD` only when validating the whole integration branch range. | Yes |
| Frontend build | `frontend/` | `npm run build` | Vite build succeeds and produces `frontend/dist`. | Yes |
| Frontend type/check | `frontend/` | `npm run check` | `svelte-check` and Node TypeScript checks pass. | Yes |
| Frontend unit tests | `frontend/` | `npx vitest run` | Vitest suite passes. | Yes |
| Frontend i18n E2E | `frontend/` | `npm run test:e2e:i18n` | Stubbed Playwright i18n smoke passes against `vite preview`. | Yes |
| Live v2 Playwright | `frontend/` | `RIGPLANE_V2_URL=http://127.0.0.1:<port>/?ui=v2 npm run test:e2e` | v2 interactive audit passes against a live local WebServer backed by fake radio state. | Yes |

Recommended optional breadth before release:

```bash
uv run pytest tests/ -q --tb=short --ignore=tests/integration
uv run ruff format --check src tests
uv run mypy src
```

### Final gate run

Date: 2026-06-04
Integration head: `27084af8`

This is the final, all-green execution of the gate above after the MOR-437
dead-code/mirror cleanup. The checklist tables in this document remain the
canonical definitions; this subsection records the outcome of running them.

| Gate | Result |
|---|---|
| Focused state pipeline batch | 1016 passed |
| Python full regression (`uv run pytest tests/`) | 7081 passed |
| Python E2E marker (`uv run pytest -m e2e`) | 59 passed |
| Web type boundary (`uv run mypy --strict src/rigplane/web`) | Success |
| Python lint (`uv run ruff check src tests`) | Clean |
| Import boundaries (`uv run lint-imports`) | 4 contracts kept, 0 broken |
| Frontend type/check (`npm run check`) | 0 errors / 0 warnings |
| Frontend unit tests (`npx vitest run`) | 1895 passed |
| Frontend build (`npm run build`) | OK |
| Frontend i18n E2E (`npm run test:e2e:i18n`) | 36 passed |
| Live fake-backend v2 Playwright | PASS — 27/28 audit cases |

Live v2 audit notes:

- The one expected non-pass is the pre-existing mode-gated DSP/CW Pitch case
  (CW Pitch is only writable in a CW mode); it is a known-fail, not a
  regression.
- No local test server remained after the run (no listener on `:8765`).
- The working tree was clean at the end of the run.

## Live Fake-Backend Web Recipe

Use this recipe when no hardware is connected. It starts a real `WebServer` with
`SerialMockRadio`, serves the built frontend, and leaves cleanup to the shell
trap.

```bash
cd /path/to/rigplane-core
cd frontend
npm run build
cd ..

PORT=8765
RIGPLANE_TEST_PORT=$PORT uv run python - <<'PY' &
import asyncio
import os
from pathlib import Path

from rigplane.backends.icom7610.drivers.serial_stub import SerialMockRadio
from rigplane.web.server import WebConfig, WebServer


async def main() -> None:
    radio = SerialMockRadio()
    await radio.connect()
    server = WebServer(
        radio,
        WebConfig(
            host="127.0.0.1",
            port=int(os.environ["RIGPLANE_TEST_PORT"]),
            discovery=False,
            static_dir=Path("frontend/dist"),
            radio_model=getattr(radio, "model", "IC-7610"),
        ),
    )
    await server.start()
    print(f"http://127.0.0.1:{server.port}/?ui=v2", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await server.stop()
        if hasattr(radio, "disconnect"):
            await radio.disconnect()


asyncio.run(main())
PY
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true; wait "$SERVER_PID" 2>/dev/null || true' EXIT

until curl -fsS "http://127.0.0.1:$PORT/api/v1/state" >/dev/null; do sleep 0.2; done
cd frontend
RIGPLANE_V2_URL="http://127.0.0.1:$PORT/?ui=v2" npm run test:e2e
cd ..
```

After the run, confirm no local test server remains:

```bash
lsof -nP -iTCP:8765 -sTCP:LISTEN
```

Expected outcome: no listener.

## Scenario Matrix

| Scenario | Automated coverage | Pass condition |
|---|---|---|
| StateStore invariants | `tests/test_state_store.py` | No-op observations do not advance `stateRevision`; every observation advances `observationSeq`; snapshots/deltas cannot be mutated by callers; direct writer API is absent. |
| Freshness and reconciliation | `tests/test_state_store.py`, `tests/test_acquisition_scheduler.py`, `tests/test_civ_rx_coverage.py`, `tests/test_rigctld_handler.py` | Stale transitions advance only `freshnessRevision`; stale critical fields queue bounded acquisition; readback observations repair stale state without legacy cache shortcuts. |
| Meter cadence and coalescing | `tests/test_acquisition_scheduler.py`, `tests/test_civ_rx_coverage.py`, `tests/test_radio_poller_coverage.py`, `tests/test_state_pipeline_diagnostics.py` | Latest due meter sample wins; drop counts are visible; same-value meter refreshes can satisfy scheduler requests; Web emits meter-only deltas without unrelated state changes. |
| Frequency latency | `tests/test_radio_poller_coverage.py`, `tests/test_rigctld_handler.py`, live v2 Playwright | Command readback enters `StateStore`; user commands preempt background telemetry; rigctld and v2 UI observe bounded read-after-write behavior. |
| Mode/buttons | `tests/test_rigctld_handler.py`, `tests/test_radio_poller_coverage.py`, `tests/test_bsr_band_switching.py`, live v2 Playwright | Mode, split, dual-watch, VFO, PTT, power, quick split/DW, and BSR state updates reconcile through the shared pipeline or documented compatibility mirrors. |
| Encoder-style controls | `tests/test_civ_rx_coverage.py`, `tests/test_radio_poller_coverage.py`, live v2 Playwright | AF/RF gain, PBT, NR/NB, filter/scope controls keep receiver scoping and command correlation. |
| HTTP/WS consistency | `tests/test_web_server_coverage.py`, `tests/test_delta_encoder.py`, frontend `npx vitest run`, live v2 Playwright | HTTP snapshots and initial WS full state use the same canonical state/freshness revisions; `transportSeq` is ordering metadata only. |
| Web field availability metadata | `tests/test_web_server_coverage.py` | Legacy `/api/v1/state` fields remain for compatibility, but `fieldStatus` marks each Store-backed public field as observed/fresh, observed/stale, or missing so empty or partial Stores do not present `RadioState` defaults as confirmed state. |
| rigctld consumer behavior | `tests/test_rigctld_handler.py`, `tests/test_rigctld_server.py`, `tests/integration/test_rigctld_wsjtx.py` when integration is opted in | GET projects from shared state when fresh; stale GET requests acquisition or falls through safely; SET uses scoped pending overlays and preserves Hamlib-style text behavior. |
| Command ingress/read-after-write | `tests/test_web_server_coverage.py`, `tests/test_radio_poller_coverage.py`, `tests/test_rigctld_handler.py`, live v2 Playwright | HTTP, WS, and rigctld command IDs are scoped; lifecycle failure/timeout expires overlays; matching observations confirm state. |
| Reconnect/resync | `tests/test_web_server_coverage.py`, `tests/test_civ_rx_coverage.py`, live v2 Playwright | Web full-state reconnect preserves canonical revisions; CI-V watchdog/backlog recovery does not publish stale scope backlog as control truth. |
| Cleanup guards | `tests/test_state_pipeline_contracts.py`, `tests/test_civ_rx_coverage.py`, `tests/test_lifecycle_diagnostics.py` | Web poller public revision API is not reintroduced; CIV waiters/watchdog/server lifecycle cleanup paths do not leak tasks or stale waiters. |

## Latency And Cadence Policy

These are release policies for fake-backend and CI behavior. Hardware numbers
must be recorded separately in the hardware checklist.

| Path | Target or policy | Status |
|---|---|---|
| S-meter / meters | Meter samples may coalesce, but the latest due sample must publish on the next flush without waiting for unrelated frequency/mode revision changes. LAN high-tier polling emits consecutive RX S-meter samples; TX rotates power/SWR/ALC. | Policy-defined and fake-backend tested. |
| Frequency tuning | Command readback observations must update `StateStore` before compatibility mirrors; user-facing commands must not be starved by scheduler polling. | Policy-defined and fake-backend tested. Hardware latency measured later. |
| Mode/buttons | Command lifecycle is separate from confirmed state; only matching observations or explicit compatibility readback confirm state. | Policy-defined and fake-backend tested. |
| Encoder controls | Receiver-scoped control paths must keep source/session/correlation metadata and never overwrite another receiver or global family. | Policy-defined and fake-backend tested. |
| Freshness repair | Expired critical fields mark stale without semantic revision, queue bounded acquisition, and reconcile on readback. | Policy-defined and fake-backend tested. |
| Reconnect | Web transport sequence may reset; canonical `stateRevision` and `freshnessRevision` must not roll back. | Policy-defined and fake-backend tested. |
| CI-V watchdog/backlog cleanup | Stale scope backlog is shed while control packets are preserved; stale waiters are cleaned and logged. | Policy-defined and unit tested. |

## Hardware/Human Checklist

Do not run these in default CI. Record date, rig model/firmware, transport,
operator, command log, and observed latency/cadence notes.

| Case | Release status | Checklist |
|---|---|---|
| IC-7610 LAN CI-V | Blocker for release candidate when an IC-7610 is available; otherwise needs explicit release waiver. | Verify unsolicited frequency and S-meter bursts reach Web v2; compare `/api/v1/state`, WS full/delta, and rigctld GET; confirm scope/audio background traffic does not starve state updates. |
| X6200 serial | Blocker for serial readiness claim; waiver allowed if release notes exclude serial latency claim. | Tune externally and through Web; measure frequency update latency; confirm polling-only/readback path reconciles without Icom-specific branches. |
| Yaesu-like polling | Blocker for backend-neutral polling claim. | Use Yaesu CAT profile or fake/hardware adapter; verify request/response frequency/mode updates shared state and scheduler dedupe prevents polling flood. |
| External `rigctld` / Hamlib | Blocker for Hamlib-provider readiness claim. | Start external `rigctld`; verify Core consumes it through the Radio/capability boundary, GET/SET responses publish observations, read-only behavior remains intact, and no direct `libhamlib` binding is introduced. |
| Operator notes: UI smoothness, perceived knob feel, meter jitter | Informational unless a blocker symptom appears. | Capture subjective notes separately from pass/fail. Do not block solely on non-reproducible perception. |
| Private validation matrix / customer device quirks | Informational for Core. | Keep private evidence out of this repo; file generic protocol/backend defects only. |

## Residual Shims And Follow-Ups

| Item | Status | Blocks release |
|---|---|---|
| Legacy Web `revision` field remains as alias for `stateRevision`. | Compatibility shim preserved by MOR-347. | No |
| Web `transportSeq` is additive and representation-local. | Required for reconnect ordering; not a freshness signal. | No |
| Web public state retains legacy default-valued fields. | Additive `fieldStatus` metadata is the compatibility contract: `observed=false`/`freshness=unknown`/`availability=missing` means the visible legacy value is only a fallback default, while observed stale fields use `observed=true`/`freshness=stale`/`availability=stale`. | No |
| `sync_state_store_from_radio_state(...)` remains a one-way compatibility adapter at explicit ingress/startup points. | Keep out of normal delivery paths; covered by static and Web tests. | No |
| Legacy `RadioState` mutable object remains public compatibility surface. | Removal/deprecation requires a separate API issue. | No |
| `StateCacheCapable` and rigctld cache fallback remain for older backends. | Must not be preferred over fresh shared state where `StateStore` exists. | No |
| Hardware latency targets are not yet universal numeric guarantees. | Current policy is fake-backend/CI defined plus hardware-measured notes. | No, unless release claims numeric hardware latency. |
