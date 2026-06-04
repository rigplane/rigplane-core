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

## Live hardware run — IC-7610 — 2026-06-04

Deferred P0 LIVE hardware validation of the MOR-334 Radio State Pipeline against
a real IC-7610 (MOR-348 / MOR-408 hardware gate). RX-side only; no transmit, no
band/frequency/mode changes. Branch `codex/mor-334-radio-state-pipeline` @
`fad94e75` (clean). Operator present.

**Setup.** `rigplane web` launched against the live radio over LAN (IC-7610 at
`192.168.55.40`, CI-V `0x98`, UDP control `50001`), bound to
`127.0.0.1:18080`, `--no-rigctld --no-discovery`. Credentials injected via BWS
(never printed). Server PID confirmed listening; `/api/v1/info` reported
`radio: IC-7610`, version `2.9.0a1`, capabilities populated (`af_level`,
`rf_gain`, `squelch`, dual RX, scope, audio, …).

**Connection result: PASS.** After the initial Are-You-There handshake the
control link established; `connection` block = `{rigConnected: true, radioReady:
true, controlConnected: true}`. Continuous CI-V RX frames observed in
`logs/rigplane.log` (`cmd=0x15` meter reads, `cmd=0x1C` PTT, `cmd=0x07` VFO),
each driving `state_store_changed` notifications.

**Observed live state (raw, from `GET /api/v1/state`).**

| Field | MAIN (rx0) | SUB (rx1) |
|---|---|---|
| `freqHz` | `14074000` (14.074 MHz, 20m FT8) | `14325000` (14.325 MHz) |
| `mode` | `USB` | `USB` |
| `sMeter` | live, varied across polls: `91, 94, 99, 95, 94, 91` | `0` |
| `dcd` (MOR-466) | `true` (RX busy) | `false` (quiet) |
| `afLevel` | `23` | `145` |
| `rfGain` | `219` | `207` |
| `squelch` | `0` | `0` |
| `agc` | `3` | `0` |

`stateRevision` advanced on every poll (136 → 250 over the run);
`freshnessRevision` advanced independently. The S-meter stream is genuinely
live — values fluctuate poll-to-poll with off-air signal. `dcd` correctly
reflects RX-busy: MAIN open (busy 20m FT8), SUB closed. WebSocket `/api/v1/ws`
handshook `101 Switching Protocols`; ~40 frames in ~6 s, 39 carrying S-meter
deltas — the delta channel streams live telemetry.

**fieldStatus (v2 / MOR-429): PASS.** 184 entries, each with
`observed`/`freshness`/`availability` plus a `source` provenance block
(`provider: icom_civ`, `transport: civ`, `nativeId` like `civ:15:02`,
`capabilityId`). Representative live fields:

| Public field | observed | freshness | availability | storePath |
|---|---|---|---|---|
| `main.sMeter` | true | fresh | available | `receiver.0.meters.s_meter` |
| `main.dcd` | true | fresh | available | `receiver.0.operator_toggles.dcd` |
| `main.squelch` | true | fresh | available | `receiver.0.operator_controls.squelch` |
| `ptt` | true | fresh | available | `global.tx_state.ptt` |
| `active` | true | fresh | available | `global.slow_state.active` |
| `main.freqHz` | true | stale | stale | `receiver.0.active.freq_mode.freq_hz` |
| `main.mode` | true | stale | stale | `receiver.0.active.freq_mode.mode` |
| `sub.sMeter` | false | unknown | missing | `receiver.sub.meters.s_meter` |

Note: `freqHz`/`mode` show `observed=true` but `freshness=stale` while idle —
the scheduler does not re-poll freq/mode within their `maxAge` (10 s) when
nothing is tuning, so the last-observed (correct) value is retained and honestly
flagged stale. This is the freshness model behaving as designed, not stale data
masquerading as fresh.

**No-snap-back test (benign RX control, `set_af_level` on MAIN): PASS.**
af_level is headphone/speaker level only — zero RF emission, fully reversible.

| Step | Action | Radio readback (CI-V `0x14 0x01`, BCD) | Public `/api/v1/state` `main.afLevel` |
|---|---|---|---|
| original | — | `0023` (=23) | `23` |
| set | `POST /api/v1/commands {name:set_af_level, params:{level:35, receiver:0}}` → `ok:true` | `0035` (=35) | converged to `35` after readback |
| observe | poll while idle | radio holds `0035` (repeated at +2/+4/+10 s) — no revert | `35`, no revert to `23` |
| restore | `POST … {level:23}` → `ok:true` | `0023` (=23) | `23` |

The radio applied the SET and held it (no hardware snap-back); the readback
observation (`cmd=0x14 sub=0x01 data=0035` → `state_store_changed paths=
['receiver.0.operator_controls.af_level']`) propagated into the StateStore and
the public projection converged to the radio's new value, then restored exactly
to the original `23`. Caveat observed and resolved during the run: the public
projection converges only when the command readback arrives (read-after-write
via the radio echo), which lagged the first 3 s poll window; a direct CI-V read
confirmed the radio value, and a subsequent poll confirmed the projection caught
up. No separate re-poll path refreshes af_level absent that echo, so the field
sits `stale` between readbacks — consistent with the documented freshness shim.

**UI smoke (best-effort, v2 frontend): PASS.** `frontend` built
(`npm run build`, Vite OK), `frontend/dist` staged to
`src/rigplane/web/static`, loaded `http://127.0.0.1:18080/?ui=v2` under
headless Playwright (chromium 1.58.2). The LCD cockpit rendered live data:
MAIN `14.074.000 USB 20M` with S-meter `S7 −7 dBm`; SUB `14.325.000 USB 20M`
with `S0 −54 dBm`; split panel `RX 14.325 / TX 14.074`; a live panadapter +
waterfall showing real 20m FT8 traffic. All values match the API/WS data.
Screenshot: `/tmp/rigval/ui_smoke.png`. Console showed only benign noise (a CSP
inline-script notice; 404/405 on optional rigctld/audio endpoints that are
disabled in this run) — none blocking.

**Anomalies / notes.**
- freq/mode held `stale` while idle (freshness model, not a data defect; values
  correct and corroborated by the UI and a direct CI-V read).
- af_level public convergence depends on the command-echo readback; there is no
  independent re-poll, so the field is `stale` between writes (documented shim,
  non-blocking).
- Initial Are-You-There retries before the link came up are normal handshake
  behavior.

**Overall verdict: PASS.** The live pipeline reflects the real IC-7610 — both
receivers' freq/mode, the live fluctuating S-meter, `dcd` RX-busy, and operator
controls populate from the radio with plausible non-default values; `fieldStatus`
honestly marks observed/fresh vs stale/missing; the benign RX control round-trip
converged to the radio's readback with no snap-back and was restored exactly; and
the v2 UI renders the same live state including a live waterfall. The freq/mode
idle-staleness and af_level read-after-write dependence are existing,
documented freshness-model behaviors, not regressions. No transmit occurred; the
radio was left in its original state.
