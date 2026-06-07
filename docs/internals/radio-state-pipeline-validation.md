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

## Live hardware run — FTX-1 (Yaesu CAT) — 2026-06-04

First live check of the FTX-1 obs-backing work (MOR-454..458) against a REAL
Yaesu FTX-1. Branch `codex/mor-334-radio-state-pipeline` @ `9b117728`. The radio
is on a Silicon Labs CP2105 dual UART; macOS exposed two candidate ports. A heavy
RigPlane Pro PyInstaller build ran concurrently (1-min load average ~14.6,
Spotlight `mds`/`mdworker` also saturating CPU) — relevant to the timeout notes
below. RX-side only; no transmit.

**CAT port + baud: `/dev/cu.usbserial-01AE340D0` @ 38400 8N1.** Decisive raw
pyserial probe: `ID;` → `ID0840;` (FTX-1), `FA;` → `FA014074000;`
(VFO-A = 14.074000 MHz, 20m FT8). The sibling channel
`/dev/cu.usbserial-01AE340D1` opened but returned NO bytes to `ID;`/`FA;` (the
non-CAT CP2105 channel). Note: the first one-shot `rigplane status` against D0
returned empty (1.0 s CAT read-timeout missed) — attributable to CPU load, not a
wrong port; the raw 2 s-window probe and the web server both read D0 cleanly.

**Connection result: ESTABLISHED.** `rigplane --backend yaesu-cat --serial-port
/dev/cu.usbserial-01AE340D0 --serial-baud 38400 --model FTX-1 web --host
127.0.0.1 --port 8108 --no-rigctld --no-discovery` came up:
`web server listening on http://127.0.0.1:8108`, `/healthz` ok (v2.9.0a1),
`/readyz` `{"status":"ready","radioReady":true}`, observation poller +
`YaesuCatPoller` started. (`IF;` bulk query at connect failed non-fatally — the
FTX-1 does not answer the composite `IF;` the same way; freq/mode are polled
individually instead.)

**Observed live values (directly from the radio, `source: yaesu_poll_response`):**
- MAIN `freqHz = 14074000` (14.074 MHz) — `observed:true / fresh / available`,
  `nativeId: read_freq`. Corroborated by the raw `FA;` probe.
- MAIN `mode = "UNKNOWN(C)"` — `observed:true / fresh`, but the raw `MD0;` mode
  byte `C` is NOT in the mode map (decode gap; see anomalies).
- MAIN `filterWidth = 3000` Hz — `observed:true / fresh` (slow lane, maxAge 120 s).
- SUB `freqHz = 144000000` (144.000 MHz), `mode = FM` — both
  `observed:true / fresh`, `read_freq`/`read_mode` (plausible default sub-VFO).
- `ptt = False` — `observed:true / fresh` (RX confirmed; never transmitted).

**MOR-454..458 fields — backend reads WORK, public-state projection BLOCKED.**
A direct backend round-trip (`YaesuCatRadio` on D0) proves the new reads decode
correctly off the live radio:
- MOR-454 RIT/XIT: `read_clarifier(0)` → `(rit_on=False, rit_tx=False)`;
  `read_clarifier_freq(0)` → `0`. OK.
- MOR-455 tuner/dial-lock: `read_tuner` → `1` (ATU state 1); `read_lock` →
  `False`. OK.
- MOR-456 CW keyer: `read_keyer_speed` → `26` wpm; `read_cw_spot` → `False`;
  `read_break_in` → `1`. OK.
- MOR-458 CTCSS tone freq: `read_ctcss_tone_index(0)` → `12` (tone-chart index).
  OK.
- MOR-457 SQL type: `read_sql_type(0)` → **CatParseError**. The FTX-1 answers
  `CT0;` with `CT00;` (P2 is ONE digit, value 0), but the profile parse pattern
  is `^CT0(?P<type>\d{2});$` (expects TWO digits). This read RAISES.

  Despite every other MOR-454..456/458 read succeeding at the backend, NONE of
  these fields reach the public `/api/v1/state` — all show
  `observed:false / availability:missing`. Root cause is a **cascading-lane
  abort**: `poll_slow_controls()` (observations.py) calls `read_sql_type(0)`
  (MOR-457) which raises, so the entire slow-lane observation tuple — built from
  af_level/rf_gain/att/preamp/agc AND the MOR-454/455/456/458 reads that PRECEDE
  it or follow via `poll_tx_controls()` — is discarded before emission. Likewise
  the **fast lane**: `poll_rx_meters()` reads MAIN s-meter fine (`SM0;` →
  `SM0001` etc.) but then `read_s_meter(1)` (SUB) raises CatParseError
  (`SM1;` → `SM0000;` — the radio echoes a MAIN-shaped `SM0` frame to the SUB
  query, failing `^SM1(?P<raw>\d{3});$`), so the already-collected MAIN s-meter
  observation is dropped too. Net effect: only 6 fields ever populate —
  `main.freqHz`, `main.mode`, `main.filterWidth`, `sub.freqHz`, `sub.mode`,
  `ptt` (all from the medium/freq-mode lane). S-meters and all operator
  controls/toggles, including the entire MOR-454..458 set, stay `missing`.

**fieldStatus result: HONEST but mostly missing.** `fieldStatus` correctly
marks the 6 populated fields `observed:true / freshness:fresh /
availability:available` with full `source` provenance, and correctly marks all
non-populated fields `observed:false / freshness:unknown / availability:missing`.
The contract is faithful — it does not falsely advertise the broken fields as
fresh; it just has almost nothing to report because two parse failures abort
their lanes.

**No-snap-back test (af_level, MAIN) — PASS at the radio/backend level.**
Because the slow lane aborts, the PUBLIC state never projects af_level (stays
`missing`), so end-to-end pipeline convergence could NOT be exercised through
`/api/v1/state` on this firmware. The reversible round-trip was therefore run
directly against `YaesuCatRadio` on D0:
`af_original = 11` → `set_af_level(16)` → `af_readback_after_set = 16`
(`set_took_effect:true`, `snapped_back:false`) → `set_af_level(11)` (restore) →
`af_after_restore = 11` (`restored_ok:true`). A final independent re-read
confirmed `af_level = 11`, MAIN `14.074 MHz`, `ptt = False`. The radio honored
the write, did NOT snap back, and was restored exactly. Yaesu read-after-write
echo behaved correctly (the AG read returns the set value).

**Anomalies / notes.**
1. **MOR-457 SQL-type parser/firmware mismatch (BLOCKER for the obs lane).**
   FTX-1 `CT0;` → `CT00;` (1-digit P2); profile pattern expects 2 digits. Fix
   the `get_sql_type` parse (`^CT0(?P<type>\d);$` or tolerate variable width),
   AND make `poll_slow_controls()` resilient so one unparseable field does not
   discard the whole lane.
2. **SUB S-meter parser/firmware mismatch (BLOCKER for the fast lane).** `SM1;`
   → `SM0000;` (radio replies with a MAIN `SM0` frame; possibly no independent
   SUB S-meter in the current single-RX state). The SUB read should be tolerated
   / made optional so it does not abort the MAIN S-meter emission.
3. **MAIN mode decode gap.** `MD0;` mode byte `C` is unmapped →
   `mode = "UNKNOWN(C)"`. The radio was on a DATA/FT8-style mode; the mode table
   is missing this code. Non-fatal but user-visible.
4. **Lane-abort fragility (design).** Both blockers above are amplified by
   poll lanes returning a single all-or-nothing tuple: any one read raising
   discards every sibling observation already collected in that lane. Per-field
   try/except (emit what parsed) would have surfaced ~10+ live fields here
   instead of 0 from those lanes.
5. **CPU-load caveat.** The concurrent Pro PyInstaller build + Spotlight kept
   load ~14.6; the initial `status` 1.0 s timeout miss is attributable to that,
   not the radio. The persistent parse errors are NOT load-related — they
   reproduce deterministically in a direct backend probe.

**Overall verdict: PARTIAL / BLOCKED.** The CAT link is solid on D0 @ 38400 and
the pipeline DOES reflect the real radio for MAIN/SUB freq+mode, MAIN filter
width, and PTT (directly observed, correct values, honest fieldStatus). The
benign af_level RX round-trip converged and did not snap back at the radio level
and was restored exactly. HOWEVER, the headline MOR-454..458 obs-backing is NOT
yet observable end-to-end on this FTX-1: the new backend reads decode correctly
in isolation (RIT/XIT, tuner, dial-lock, keyer speed, CW spot, break-in, CTCSS
tone index all read real values), but a single MOR-457 `CT` parse failure aborts
the entire slow lane and a SUB-S-meter `SM1` parse failure aborts the fast lane,
so the public state surfaces none of them and shows no live S-meter. Two parser
fixes (CT 1-digit, SM1 tolerance) plus per-field lane resilience are required
before MOR-454..458 can be called live-validated. No transmit occurred; the
radio was left in its original state (af_level 11, 14.074 MHz, RX).

## Live hardware run — FTX-1 re-validation (post-MOR-473) — 2026-06-04

Re-ran the live FTX-1 validation AFTER the MOR-473 fixes landed. Branch
`codex/mor-334-radio-state-pipeline` @ `efcb7025` (both fix commits present:
`e835020c` CT/mode-code parser fixes, `efcb7025` per-field poll-lane
resilience). RX-only; no transmit. CAT port `/dev/cu.usbserial-01AE340D0` @
38400 8N1, model `FTX-1`, backend `yaesu-cat`. Web server on
`127.0.0.1:8771 --no-rigctld --no-discovery`. Ports were free, no RigPlane Pro
running, CPU free.

**Connection result: ESTABLISHED.** `/healthz` ok (v2.9.0a1), `/readyz`
`{"status":"ready","radioReady":true}`, observation poller + `YaesuCatPoller`
started. (The composite `IF;` bulk query at connect still fails non-fatally, as
before; freq/mode are polled individually.)

**HEADLINE — the fixes UNBLOCKED the pipeline. `/api/v1/state` `fieldStatus`
went from 6 observed fields to 45** (out of 184 known paths), all
`observed:true / freshness:fresh / availability:available`, all with
`source: yaesu_poll_response`. The 6 fields the prior run surfaced
(`main.freqHz`, `main.mode`, `main.filterWidth`, `sub.freqHz`, `sub.mode`,
`ptt`) are now joined by the entire slow lane and the MAIN s-meter.

Now-available fields (directly observed, with live values):
- **Slow-lane RX controls (MAIN):** `main.afLevel = 11`, `main.rfGain = 255`,
  `main.agc = 5`, `main.att = 0`, `main.preamp = 0`, `main.squelch = 0`,
  plus `main.nb/nbLevel/nr/nrLevel/autoNotch/manualNotch/manualNotchFreq/
  narrow/ifShift` — all observed. SUB exposes `sub.afLevel = 20`,
  `sub.rfGain = 255`, `sub.squelch = 64`.
- **MOR-454 RIT/XIT:** `ritOn = False`, `ritTx = False`, `ritFreq = 0` — observed
  (`read_clarifier` / `read_clarifier_freq`).
- **MOR-455 tuner / dial-lock:** `tunerStatus = 1` (ATU state 1),
  `dialLock = False` — observed (`read_tuner` / `read_lock`).
- **MOR-456 CW keyer:** `keySpeed = 26` wpm, `cwSpot = False`, `breakIn = 1` —
  observed (`read_keyer_speed` / `read_cw_spot` / `read_break_in`).
- **MOR-457 SQL type / repeater tone:** `main.repeaterTone = False`,
  `main.repeaterTsql = False` — observed (`read_sql_type`). The CT 1-digit parse
  fix means `CT0;` → `CT00;` now decodes instead of raising; the slow lane no
  longer aborts here.
- **MOR-458 CTCSS tone freq:** `main.toneFreq` / `main.tsqlFreq` observed
  (`read_ctcss_tone_index`).
- **Other operator controls now observed:** `powerLevel`, `micGain`, `cwPitch`,
  `compressorOn`, `compressorLevel`, `voxOn`, `split`, `active`.

**MAIN s-meter SURVIVES the SUB s-meter failure (per-field resilience works).**
In the SAME state snapshot:
- `main.sMeter`: `observed:true / fresh / available`, value `0`. The fast lane
  is actively running (`observationSeq` advanced 4512 → 4683 across samples);
  value 0 is legitimate (no signal on the FT8 calling frequency at sample time).
- `sub.sMeter`: `observed:false / freshness:unknown / availability:missing`.
  Degraded CLEANLY — the body shows the default `0` but `fieldStatus` honestly
  reports it as missing, NOT a false "fresh 0". The SUB `SM1;` → `SM0000;`
  mismatch no longer aborts the MAIN s-meter emission.

**MAIN mode decodes correctly: `main.mode = "DATA-U"`** (was `UNKNOWN(C)` in the
prior run). The hex mode-map fix decodes `MD0;` byte `C` (hex 12) → `DATA-U`.
This is consistent with the radio sitting on 14.074 MHz / 20m FT8 (data-mode
upper). **Operator: please confirm the front panel reads DATA-U on MAIN.**

**af_level end-to-end no-snap-back — PASS THROUGH `/api/v1/state` (now that the
slow lane emits af_level).** Original `main.afLevel = 11` (public state) →
`POST /api/v1/commands {set_af_level, level:16}` (`{"ok":true,...}`) → public
state converged to `16` within 2 polls and HELD `16` across 6 consecutive polls
(~3 s, no revert) → `set_af_level level:11` (restore) → public state converged
back to `11`. Final independent re-read: `main.afLevel = 11`,
`main.freqHz = 14074000`, `main.mode = "DATA-U"`, `ptt = False`. The radio
honored the write, did NOT snap back, and was restored exactly — and this time
the convergence was observed END-TO-END through the public state, not just at
the backend.

**`_safe_read` graceful-degradation logging confirmed (not silent).** The server
log shows exactly two distinct field-level skip WARNs, repeated each poll cycle
(1189 total over the run), with NO lane abort, traceback, or other error:
- `Skipping field sub.s_meter — malformed CAT response: Parse error for
  'SM1{raw:03d};' against 'SM0000;': Response does not match pattern
  '^SM1(?P<raw>\d{3});$'` (1080×) — the known SUB s-meter mismatch.
- `Skipping field break_in_delay — malformed CAT response: Parse error for
  'SD{delay:04d};' against 'SD09;': Response does not match pattern
  '^SD(?P<delay>\d{4});$'` (109×) — a NEW field-level mismatch the resilience
  surfaced: the FTX-1 answers `SD;` with a 2-digit value (`SD09;`) but the parse
  expects 4 digits. Previously this would have been masked by the whole-lane
  abort; now it skips cleanly and its siblings emit. `breakInDelay` correctly
  shows `observed:false / missing` in `fieldStatus`.

**Still missing (honest):**
- `sub.sMeter` — degrades cleanly (see above); SUB has no independent s-meter on
  this single-RX firmware state (`SM1;` echoes a MAIN `SM0` frame).
- `breakInDelay` — a residual parser-width mismatch (`SD09;` is 2 digits, parse
  wants 4). A follow-up `SD` 2-digit parse fix would recover it; non-blocking.
- The per-VFO nested slots (`main.vfoA/vfoB.*`, `sub.vfoA/vfoB.*`) remain
  default/stale (freqHz 0, USB) and `missing` — only the ACTIVE-slot projection
  (`main.freqHz`/`main.mode`) is observed. Meters other than s-meter
  (`swrMeter`/`alcMeter`/`powerMeter`/`idMeter`/`vdMeter`/`compMeter`) are
  TX-domain and stay `missing` on RX (expected). Scope controls, band edges,
  antenna, scan, tuning-step, dual-watch remain `missing` (not in the FTX-1 poll
  set / unsupported).

**Overall verdict: FIXED / UNBLOCKED.** The MOR-473 fixes resolve all three
prior blockers. The CT 1-digit parse fix stops the slow lane aborting; the
per-field `_safe_read` resilience lets the MAIN s-meter survive the SUB s-meter
failure (and surfaces the previously-masked `break_in_delay` skip without
killing its lane); the hex mode-map fix decodes `MD0C` → `DATA-U` instead of
`UNKNOWN(C)`. The public `/api/v1/state` now surfaces 45 live observed fields
(up from 6), including the full MOR-454..458 obs-backed set, and the af_level RX
round-trip converges and does NOT snap back END-TO-END through the public state.
`fieldStatus` remains honest — the two genuinely-unreadable fields (`sub.sMeter`,
`break_in_delay`) are marked `missing`, not falsely fresh. No transmit occurred;
the radio was left in its original state (af_level 11, 14.074 MHz, DATA-U, RX).

## Live hardware run — freq-pipeline trilogy (MOR-475/484/485) — IC-7610 — 2026-06-05

Live IC-7610 validation of the v2 click-to-tune snap-back fix, which turned out
to be a 3-layer freq-pipeline defect (the original MOR-475 "frontend-only, no
backend change" diagnosis was incomplete). Branch
`codex/mor-334-radio-state-pipeline` @ `84e8417a`.

**Context.** Live IC-7610 over LAN (`192.168.55.40`, CI-V `0x98`), v2 Web UI
served from `frontend/dist`. Validated via instrumented Playwright (WebSocket
frames + rendered VFO DOM + `/api/v1/state`, all at ms resolution) plus operator
front-panel knob turns.

**Defect.** v2 click-to-tune snap-back: clicking to tune showed the commanded
frequency briefly, then reverted to the old value. Three independent layers
contributed; each was fixed and shipped separately.

**Fixes (each: independent-review PASS + worktree falsification + green
`uv run` gate + ff-pushed).**
- **MOR-475 frontend** (commits `339031a4`, `6ca9e24d`): `OPTIMISTIC_FREQ_TTL`
  lowered to 1500 ms; the freq optimistic overlay now clears on value-match or
  TTL, not on a bare causal advance. The causal-advance clear flashed the old
  freq by reverting to a stale in-flight poll.
- **MOR-484** (commit `97c94f58`): IC-7610 freq/mode were not in the
  `polling_only` tier in `rigs/ic7610.toml`, so the readback was frozen at the
  connect-time value (the optimistic overlay had masked this). Added
  `receiver.{main,sub}.active.freq_mode.{freq_hz,mode}` to `polling_only` plus a
  0.5 s freq `field_policy`.
- **MOR-485** (commit `84e8417a`): the web `set_freq` executor
  (`_SharedControlCommandExecutor.execute`) returned no observation, so the
  commanded freq was never published into the command `StateStore` until a
  `pause_polling`-deferred readback (~2–4.3 s). It now emits a freq
  command-response `Observation` at `FieldPath.active("0"/"1","freq_mode",
  "freq_hz")` (the numeric-receiver key the CI-V readback emits), reconciled by
  the later poll via last-writer-wins.

**Live results (two instrumented runs).** Commanded freq published to
`/api/v1/state` in +102 ms (and +13 ms on the other run); first WS frame
carrying the commanded freq at +42 ms; ZERO snap-back/revert events across ~6 s
(API, WS, and rendered VFO); rendered VFO settles on the commanded freq at
~+26 ms. Front-panel knob turns move `main.freqHz` within the 0.5 s cadence
(MOR-484), and server == VFO == commanded after settle.

**Method note.** Systematic instrumented timeline debugging was decisive: two
hypothesis-driven frontend-only patch rounds did not fix the UX; the ms-timeline
isolated the backend (no-poll + no set-overlay) as the dominant cause.

**Open follow-up.** `set_mode` has the identical web-executor command-overlay
gap (only freq was shipped); a fast clone of the MOR-485 fix applies if a mode
snap-back is observed.
