# Implementation Plan — Unified Radio Session Lifecycle

**Status:** PLAN — execution-ready, decomposed for parallel dispatch.
**Date:** 2026-06-22
**Design doc:** `2026-06-22-radio-session-lifecycle.md` (read its
"Approved Decisions (2026-06-22)" section first — D1-D8 are binding here).
**Repos:** `rigplane-core` (open-core, Phases A/B/D-core, E), `rigplane-pro`
(proprietary, Phase C/D-pro). Boundary gates straddle both (Phase D).

> All `_control_phase.py:NNN` references are to the canonical module
> `rigplane.runtime._control_phase` (the `src/rigplane/_control_phase.py` shim
> is a `sys.modules` alias). `radio.py` = `src/rigplane/radio.py`. Pro paths are
> relative to `rigplane-pro/`.

---

## 0. Orchestration model & file-ownership map

Tasks are decomposed so **parallel agents never edit the same file**. Each task
lists OWNED FILES (exclusive write) and READ-ONLY deps. A file owned by one
task in a phase is not written by any other task in that phase.

| File | Owning task | Phase |
| --- | --- | --- |
| `tests/mock_server.py` (core) | A1 | A |
| `tests/conftest.py` (core, additive fixtures) | A1 | A |
| `src/rigplane/runtime/session_lifecycle.py` (NEW) | A2 | A |
| `src/rigplane/runtime/_control_phase.py` | A2 (state-machine extraction) then A3 (call-routing) — **sequential, A2→A3** | A/B |
| `src/rigplane/radio.py` | A3 / B-route | B |
| `src/rigplane/web/handlers/control.py` | B-route | B |
| `src/rigplane/runtime/radio_reconnect.py` | B-route | B |
| `src/rigplane/runtime/_civ_rx.py` | B-route (call sites only) | B |
| `tests/test_session_lifecycle.py` (NEW) | B-T* (test matrix) | B |
| `docs/api/public-api-surface.md` | A2-api (the blessed-surface edit) | A |
| `rigplane-pro/companion/.../station_runtime.py` | C | C |
| `rigplane-pro/companion/.../radio_session.py` | C | C |
| `rigplane-pro/.importlinter` | D-boundary | D |
| new grep-gate (Pro CI script/test) | D-boundary | D |
| `rigplane-pro/tests/...` supervisor behavior test | D-behavior | D |
| `rigplane-pro/pyproject.toml` + `CHANGELOG.md` + core release | E | E |

**Builder ≠ verifier:** every phase has an independent-verifier checkpoint; the
agent that wrote a task does NOT sign off its own verification.

---

## Phase A — core foundation (open-core)

Land the new module beside the existing path behind an internal switch; no core
call-site behavior change yet. A1 and A2/A2-api are parallelizable; A3 depends
on A2.

### A1 — Extend `MockIcomRadio` (INDEPENDENT — start first, fully parallel)

**Owns:** `tests/mock_server.py`, additive fixtures in `tests/conftest.py`.
**Read-only:** `src/rigplane/core/auth.py`, `src/rigplane/core/transport.py`,
`src/rigplane/runtime/_civ_rx.py`, `src/rigplane/runtime/_control_phase.py`.
**Depends on:** nothing. **Parallel with:** A2, A2-api.

Extend (do not replace) `MockIcomRadio` (`tests/mock_server.py:143-786`,
`_MockProtocol:110`). Reuse handshake parsing/builders, control+CIV sockets,
CI-V dispatcher, `auth_fail`/`response_delay`, fixtures `mock_radio:150` /
`connected_radio:159`. Add as **opt-in flags/methods** so the existing 60+
control/CIV tests stay green (`test_radio.py` `MockTransport:227-286`,
`test_mock_integration.py:26-84`).

Add (per §4.1):
1. **Held single-owner session.** Owner identity `(remote_addr, my_id)`,
   `my_id=(lport&0xFFFF)|0x10000` (`transport.py:216`). On conninfo
   (`auth.py:292`) from a NEW owner while held → status with `civ_port=0` +
   `error=0xFFFFFFFF` (`auth.py:406-431`: error LE@0x30, civ_port BE@0x42).
2. **Keepalive timeout / immediate-release.** On token-remove
   (`_send_token(0x01)`, `pkt[0x14]=0x01`, magic@0x15,
   `_control_phase.py:932-947`) release the held session **immediately**. With
   NO token-remove, hold for `keepalive_hold_s` (default models ~40-60 s,
   **configurable + accelerated**, canonical fast value **~0.5 s** per D7)
   before auto-release. Renew (magic `0x05`, `:925`) + ack (`0x02`) refresh it.
3. **Autonomous state + unsolicited CI-V streaming.** Background task emits
   unsolicited CI-V (freq/mode drift) at a configurable rate, independent of
   polled commands — exercises the pump and the watchdog reset.
4. **Loss / stall / reorder knobs.** `drop_rate`, `reorder_window`,
   `stall_for(seconds)` to trip the data watchdog
   (`_CIV_DATA_WATCHDOG_TIMEOUT=2.0`, `_civ_rx.py:515-516`/`822-924`).
5. **Cooldown trigger knob.** `civ_port_unavailable_until(t)` forcing
   `civ_port=0` (not-ready, no `0xFFFFFFFF`), distinct from busy-reject, to
   drive CONNECTING→COOLDOWN→CONNECTING.

**TDD:** write micro-tests for each new knob (held-session reject, immediate
release on token-remove, accelerated keepalive expiry, stall trips watchdog)
before/with the simulator code; these are simulator unit tests, separate from
the T1-T12 matrix.

**Verify:**
```
uv run pytest tests/test_mock_integration.py tests/test_radio.py -q   # existing stay green
uv run pytest tests/test_mock_server_extensions.py -q                 # new knob tests (NEW file, also A1-owned)
uv run ruff check tests/ && uv run ruff format --check tests/
```
**Risk:** acceleration hides real timing (R3) — mitigated by D7 slow test in B.
Knobs leaking into default behavior break existing tests — keep all opt-in.

### A2 — `RadioSessionLifecycle` state machine (parallel with A1)

**Owns:** `src/rigplane/runtime/session_lifecycle.py` (NEW), and the
state-machine **extraction** edits in `_control_phase.py` (delete retry wrapper
`:113-141`; demote `_control_phase` to packet mechanism). **A2 owns
`_control_phase.py` first; A3 takes it after A2 lands** (sequential on that
file).
**Read-only:** `radio.py` (host protocol), `auth.py`, `transport.py`,
`_civ_rx.py`, `_connection_state.py`.
**Depends on:** nothing for the new module; the `_control_phase` extraction can
proceed in parallel with A1.

Implement `RadioSessionLifecycle` (the resident policy layer) per §2:

- **States + transitions** (§2.2): `DISCONNECTED/SCANNING/CONNECTING/COOLDOWN/
  CONNECTED/RECOVERING/CLOSING` with the exact transition rules.
- **Release obligation registered at session-claim** (auth success,
  `_control_phase.py:200-207`), discharged on EVERY exit via
  `AsyncExitStack`/RAII `finally` — independent of any `conn_state` guard
  (remove the `:494` early-return from the release path). Closes Holes 1/2/5/8.
- **Cooldown-aware RESIDENT retry** (§2.3): one async task owns
  CONNECTING↔COOLDOWN inside the process; **release BEFORE the cooldown wait**;
  never abandons a held session; never exits the process on transient failure.
  Policy constants `_STATUS_RETRY_PAUSE=10`, `_STATUS_REJECT_COOLDOWN=30`,
  `_DATA_PORT_COOLDOWN_RETRIES=3` move here as their only home.
- **Retry policy (D3):** hard-fail (CLOSING + release + auth error reason) on
  `0xFEFFFFFF`; resident retry on `civ_port==0` / `0xFFFFFFFF`. Design so that
  after a clean self-disconnect cooldown effectively never fires (cooldown only
  for a FOREIGN held session).
- **SIGTERM async graceful-close (§2.6):** replace the `os._exit` path with a
  shutdown event that cancels the resident runner / cooldown sleep / RECOVERING
  task; transition to CLOSING; run full release; bounded deadline **~2-3 s**
  (D2) then exit 0.
- **soft_reconnect / #1217 folded in** as CONNECTED→RECOVERING→CONNECTED
  (reuse `_control_phase.py:547-723`, watchdog `_civ_rx.py:822-924`); recovery
  exhaustion (`_MAX_RECONNECTS=3`) → CLOSING + full release.
- **Rich observable surface (D1) — FIRST CLASS:** a structured event/status API
  built into the controller: `state` property (`LifecycleState`), a
  `LifecycleStatus` snapshot (state + connecting/cooldown progress + countdown
  + last error reason + recovery attempt N/M), and an event stream/callback
  emitting every transition with from→to + cause. Design these as **stable
  public types** (D6). Demote `_control_phase` retry wrapper (`:113-141`) to
  pure transitions; `_control_phase` keeps only packet I/O.

**TDD:** unit-test the state machine against the A1 simulator (or a thin fake
host) — transition table, release-on-every-exit, cooldown-then-connect,
hard-fail on `0xFEFFFFFF`, event emission completeness — before wiring call
sites. (These unit tests live in `tests/test_session_lifecycle_unit.py`,
A2-owned, distinct from the B matrix file.)

**Verify:**
```
uv run pytest tests/test_session_lifecycle_unit.py -q
uv run mypy --strict src/rigplane/web        # publish gate; type new surface up front (R5)
uv run mypy src/rigplane/runtime/session_lifecycle.py
uv run ruff check src/rigplane/runtime/ && uv run ruff format --check src/rigplane/runtime/
```
**Risk:** RECOVERING vs CONNECTING concurrency (R7) — single state machine owns
mutual exclusion (replaces `_civ_recovery_lock` role). Removing `:494` guard
could over-release an unclaimed session — make `disconnect()` idempotent (§2.5).

### A2-api — Bless the public-SDK surface (small, parallel)

**Owns:** `docs/api/public-api-surface.md` (+ the `__all__`/`__init__` export
edits for the blessed symbols, coordinated to not collide with A2 — put exports
in `rigplane/__init__.py`, A2-api-owned).
**Depends on:** A2 type names finalized (coordinate names early; can stub-land
the doc rows and tighten once A2 fixes the symbols).

Per D6, add to the supported-exports table: `RadioSessionLifecycle`,
`LifecycleState`, `LifecycleStatus`, `LifecycleEvent` (event/status types),
and document `scan/connect/disconnect/soft_reconnect/state`. Mark
additive-only, versioned, contract-grade. Note the COMPATIBILITY policy
(`rigplane-pro/docs/contracts/COMPATIBILITY.md`, MOR-885) implication: Pro
imports ONLY these via `rigplane.*`.

**Verify:** `uv run pytest tests/test_public_api*.py -q` (if a public-surface
test exists); import-linter dry run that the blessed symbols are importable from
`rigplane`.

### A3 — Route `CoreRadio` + core call sites through the lifecycle

**Owns:** `radio.py` (lifecycle methods), and takes over `_control_phase.py`
**after A2 lands** (sequential dependency on that file).
**Depends on:** A2 (module + extraction). **Sequential after A2.**

Make `CoreRadio.connect/disconnect/soft_reconnect/scan`
(`radio.py:1165/1189/1270/3124`) delegate to `RadioSessionLifecycle`. Fix all 8
graceful-close holes (§3): `__aenter__`/`__aexit__` (`radio.py:1182-1184`)
always release; `__del__` (`radio.py:903-912`) stays warn-only; rejection/
timeout cleanup (`_control_phase.py:298-300/320-322/355-357`,
`_cleanup_data_port_discovery_timeout:464-489`) routes through the unconditional
release path. Keep public SDK signatures stable.

**Verify:**
```
uv run pytest tests/test_radio.py tests/test_session_lifecycle_unit.py -q
uv run mypy --strict src/rigplane/web
uv run ruff check src/rigplane && uv run ruff format --check src/rigplane
```

**Phase A independent-verifier checkpoint:** a verifier agent (≠ A1/A2/A3
builders) runs the full fast suite, confirms existing tests green, confirms the
new module sits behind an internal switch with no behavior regression, and
confirms the blessed-surface doc matches the actual exports.

---

## Phase B — Test matrix T1-T12 (core)

**Owns:** `tests/test_session_lifecycle.py` (NEW). One file; can be split into
per-group test modules (`..._connect.py`, `..._recover.py`) if parallelized —
each split file is owned by exactly one builder.
**Depends on:** A1 (simulator) + A3 (routed call sites). **Sequential after A.**
**Parallelizable internally** across the listed groups (different test files).

TDD note: T2 (repro) is written to PASS against pre-fix abort behavior, T3 is
the post-fix pair — write the T2/T3 pair together. All time-based cases use the
accelerated `keepalive_hold_s` (~0.5 s, D7).

| # | Maps to | Group (parallel split) |
| --- | --- | --- |
| T1 | governing principle, Hole 8 — clean connect/disconnect, NO cooldown | connect |
| T2 | bug repro: abort w/o close → held + `0xFFFFFFFF` | connect (T2/T3 pair) |
| T3 | Hole 1/5, §2.5 — abort WITH fix → free immediately | connect (T2/T3 pair) |
| T4 | Cause A+B, Hole 4/6, §2.3 — resident cooldown→connect, one process | connect |
| T5 | Hole 3/4, §2.6 — SIGTERM mid-CONNECTING and mid-COOLDOWN → token-remove, exit 0 | shutdown |
| T6 | §2.4 — `scan()` vs held+free, no login/token/conninfo from scan | scan |
| T7 | #1217, §2.7 — soft_reconnect reuses control+token | recover |
| T8 | `_civ_rx.py:822-924`, §2.7 — watchdog stall → RECOVERING → CONNECTED | recover |
| T9 | §2.1 idempotency — duplicate/concurrent connect coalesced | connect |
| T10 | §1.2 fleet move (re-expressed per D4), Hole 8 — graceful close source before target | fleet |
| T11 | §2.7 — recovery exhaustion → CLOSING + full release | recover |
| T12 | §1.3/§1.5 — Pro supervisor behavior (Pro-side; see Phase D-behavior) | (Pro) |

Plus the **D1 feedback test:** assert the event/status surface emits cooldown
countdown, error reasons, and recovery events for the relevant scenarios.
Plus the **D7 slow test:** one `@pytest.mark.slow` near-real (~45-60 s)
keepalive timing case.

**Verify:**
```
uv run pytest tests/test_session_lifecycle.py -q          # fast matrix
uv run pytest tests/test_session_lifecycle.py -m slow     # the near-real timing case
```
**Risk:** flaky time-based tests — use deterministic simulator knobs, not wall
clock, for fast cases; reserve real timing for the single slow test.
**Verifier checkpoint:** verifier reruns the matrix, confirms each row maps to
its hole/requirement and that T2 actually reproduces the bug before T3 fixes it.

---

## Phase C — Pro thin-supervisor refactor (proprietary)

**Owns:** `station_runtime.py`, `radio_session.py` (Pro).
**Depends on:** Phase A/B landed in core (semantics) but **C code can be
written in parallel against the agreed contract** once §1.4/D4 is frozen; it
cannot be MERGED until the core release (Phase E) re-pins. Treat C as
parallelizable-with-B in authoring, sequential-with-E in merge.

Per D4 (the reshaped multi-radio model):

- Pro keeps **fleet storage** (discovered radios + credentials) + **spawn/
  supervise N core services** (one per active radio, different ports) +
  **SIGTERM-to-stop with ~5 s wait then SIGKILL backstop** (D2).
- **REMOVE all retry/cooldown/backoff lifecycle logic:**
  `_launch_until_ready_with_retries` (`station_runtime.py:663`), backoff
  (`:680`), readiness polling + terminate-on-not-ready (`:730-756`, `:778`),
  restart-on-not-ready (`_monitor_process:848`, `:867-883`).
- **Re-express `RadioFleetSessionManager`** (`radio_session.py:341/371/384`):
  `_enforce_active_limit` becomes "number of concurrent core services," NOT
  multiple sessions in one process. One session per core service per radio.
  `RadioSession`/`ManagedRuntimeOpener` reduce to spawn + liveness + SIGTERM +
  **crash-only** restart with a **smaller crash backoff**; **repeated genuine
  crashes → hard UI error** (D5).
- Surface core-emitted lifecycle state (D1 subset) to the UI; NEVER act on it
  to kill/restart.

**Verify:**
```
cd rigplane-pro
uv run ruff check . && uv run ruff format --check .
uv run pytest -m "not slow" tests/test_station_runtime*.py tests/test_radio_session*.py
uv run pytest -m "not slow"      # payload-dict tests are exact-match; run the broad fast suite
```
**Risk (R1):** any residual kill-on-not-ready regresses Cause B — guarded by
Phase D-behavior test. Multi-service supervision is new surface — keep it small;
do not invent abstractions beyond N-supervisor.

---

## Phase D — Boundary enforcement (mixed/split)

**Depends on:** A2-api (blessed symbols) + C (Pro refactor). Three
parallelizable sub-tasks, distinct owners.

### D-importlinter — `public-sdk-only` contract
**Owns:** `rigplane-pro/.importlinter`. Tighten so Pro imports only the blessed
`rigplane.*` SDK (D6), never lifecycle internals
(`rigplane.runtime._control_phase`, non-blessed `rigplane.radio` lifecycle).
**Verify:** `cd rigplane-pro && uv run lint-imports` (or
`uv run python -m importlinter`).

### D-grep-gate — ban lifecycle primitives in `rigplane_pro`
**Owns:** new CI script/test (e.g. `rigplane-pro/scripts/check_no_lifecycle.py`
+ a pytest wrapper). Fail if Pro source references retired primitives:
`_STATUS_RETRY_PAUSE`, `_STATUS_REJECT_COOLDOWN`, `_DATA_PORT_COOLDOWN_RETRIES`,
`_send_token`, `_send_open_close`, or Pro-local retry/backoff in
`station_runtime.py`/`radio_session.py`.
**Verify:** `cd rigplane-pro && uv run pytest tests/test_no_lifecycle_gate.py -q`.

### D-behavior — supervisor behavioral test (T12)
**Owns:** `rigplane-pro/tests/test_supervisor_behavior.py` (NEW). Assert: on a
core "not ready" status Pro does **NOT** restart; on stop Pro issues SIGTERM
(never SIGKILL-first) and waits for clean exit; restart only on crash exit code;
repeated crashes surface a hard error (D5).
**Verify:** `cd rigplane-pro && uv run pytest tests/test_supervisor_behavior.py -q`.

**Verifier checkpoint:** verifier confirms all three gates fail on a deliberately
re-introduced primitive / kill-on-not-ready, then pass on the clean tree.

---

## Phase E — Integration, core release, re-pin, rebuild, QA, flip

**Sequential, single-threaded, gated.** Owns: `rigplane-pro/pyproject.toml`,
`rigplane-pro/CHANGELOG.md`, core release artifacts.

1. **Core release.** Bump core version; changelog; PR; **independent agent
   review (builder ≠ reviewer)**; tag; publish to PyPI. Gate:
   `mypy --strict src/rigplane/web` green (R5), full `uv run pytest`.
2. **Re-pin Pro in lockstep.** Move `rigplane[bridge]==X` (`pyproject.toml:16`,
   currently `==2.10.1`) AND `CORE_VERSION` **together** (R4 coupling — else
   gate-pytest fails). Regenerate lock.
3. **Rebuild all platforms.** FULL Tauri sidecar build (NOT `--skip-sidecar`,
   R6) so Python lifecycle ships in the app; `scripts/tauri-beta-gate.sh`.
4. **Physical IC-7610 QA (D8).** Run T1, T3, T6, and `soft_reconnect` on the
   real radio; confirm no cooldown after clean disconnect, radio freed on
   SIGTERM, recovery works on real keepalive timing.
5. **Flip beta.9** once all gates green and QA passes.

**Risk:** R2 (close deadline vs hung close) and R3 (accel vs real timing) only
fully observable here — physical QA is the backstop.

---

## Public-SDK API surface to bless (D6)

Exact NEW blessed symbols (additive-only, versioned, contract-grade):

- `RadioSessionLifecycle` — the resident controller.
- `LifecycleState` — enum: `DISCONNECTED/SCANNING/CONNECTING/COOLDOWN/
  CONNECTED/RECOVERING/CLOSING`.
- `LifecycleStatus` — snapshot: state + connecting/cooldown progress +
  cooldown countdown + last error reason + recovery attempt N/M (D1).
- `LifecycleEvent` — transition event: from→to + cause (D1 event stream).
- Methods/property on the public surface: `scan()`, `connect()`,
  `disconnect()`, `soft_reconnect()`, `state`.

**Version/compat implications:** these cross the core↔Pro boundary; changes are
additive-only and versioned per `docs/contracts/COMPATIBILITY.md` (MOR-885).
The import-linter `public-sdk-only` contract is the enforcement; the lockstep
pin (Phase E) is the cadence mechanism.

---

## Critical path & parallelism summary

```
A1 (simulator) ─────────────┐
A2 (state machine) ─► A3 (route core) ─► B (matrix) ─► E (release/qa/flip)
A2-api (bless surface) ─────┘                          ▲
C (Pro thin-supervisor) ──► D (boundary gates) ────────┘
```

- **Parallel first wave:** A1, A2, A2-api (3 agents). C authoring can also
  start in parallel once D4 contract is frozen.
- **Strictly sequential:** A2 → A3 (same `_control_phase.py`/`radio.py`),
  A3 → B, (A+C) → D, everything → E.
- **Critical path:** A2 → A3 → B → E. (A1 must finish before B; A2-api before
  D; C before D.)
- **File-ownership guarantee:** no two concurrently-running tasks own the same
  file (see §0 map). `_control_phase.py` and `radio.py` are the only shared
  hotspots and are handled by the A2→A3 sequencing.
- **Builder ≠ verifier** at every phase checkpoint (A, B, D, E-review).
