# Unified Radio Session Lifecycle (rigplane-core)

**Status:** DESIGN — for human review/approval. No production code yet.
**Date:** 2026-06-22
**Scope:** MAXIMUM — a single state-machine module in `rigplane-core` owns the
*entire* radio network lifecycle: connect + disconnect + scan +
auto-recovery/soft-reconnect (#1217).
**Blocks:** beta.9 flip is HELD until this lands (sequencing handled
separately; this doc only provides the rollout plan).

> Canonical control-phase code lives in `rigplane.runtime._control_phase`
> (`src/rigplane/_control_phase.py` is a `sys.modules`-alias shim). All
> `_control_phase.py:NNN` line references below are to the canonical module.

---

## 0. Problem Statement & Root Cause (the livelock to eliminate)

Observed failure (the cornerstone bug this design must make *impossible*):

1. Auth succeeds (`auth.py:395` — error not in `0xFEFFFFFF`/`0xFFFFFFFF`).
2. CI-V data-port allocation fails: `civ_port == 0` in the status response
   (`auth.py:406-431`, `civ_port` BE@0x42).
3. Core enters an **in-process cooldown that HOLDS the session**: the retry
   wrapper sleeps `_STATUS_RETRY_PAUSE=10.0` / `_STATUS_REJECT_COOLDOWN=30.0`
   (`_control_phase.py:128-141`, repeated `:334-341`) **without releasing the
   token** (no `_send_token(0x01)`).
4. Pro's supervisor sees no readiness within its poll window
   (`station_runtime.py:730-756`) and **SIGTERM/SIGKILLs the core process**
   (`_terminate_process_tree` `:1247` → SIGTERM `:1258` → SIGKILL `:1265`),
   bypassing core's async close (core SIGTERM handler ends with `os._exit`).
   No token-remove is ever sent.
5. Pro **relaunches within 1-4 s** (`_monitor_process` `:867-883`,
   backoff `_backoff_s*2**restart_count`), reopening UDP `50001` inside the
   radio's ~40-60 s keepalive window.
6. The radio still holds the prior session → returns `civ_port == 0` with
   `error == 0xFFFFFFFF` ("previous session active",
   `auth.py:395`, `_control_phase.py:258/815`).
7. GOTO 3 → **LIVELOCK.**

### Governing principle (the invariant the design encodes)

> A graceful connect/disconnect must cause **NO cooldown**. A cooldown means a
> session was torn down **non-gracefully**. Therefore: every exit path from a
> session — normal, exceptional, or signal-driven — MUST send token-remove
> (`0x01`) + OpenClose-close before the socket/process goes away, and the
> cooldown wait MUST happen **inside one resident process** that never
> re-claims `50001` while a stale session is still held by the radio.

Two structural causes, both eliminated below:

- **Cause A — held cooldown:** core waits out a cooldown while *holding* the
  token (Hole 4/5).
- **Cause B — cross-process restart:** Pro tears the process down and reopens
  the port faster than the radio's keepalive expires (Hole 3/6).

---

## 1. Invariant & Boundary: Pro = process supervisor only

### 1.1 The cornerstone invariant

**Pro must contain ZERO radio-connection-lifecycle logic.** All
connect/disconnect/scan/retry/cooldown/backoff/recovery intelligence lives in
`rigplane-core` (open-core). Pro is a **thin process supervisor**:

- spawn the core-station process;
- monitor **process liveness** only;
- send **SIGTERM** to stop and wait for graceful exit;
- restart **only on genuine process crash** (non-zero exit / OS death), never
  on "radio not ready yet".

### 1.2 What MOVES from Pro into core

| Today (Pro) | Map ref | Destination |
| --- | --- | --- |
| Connect retry + backoff loop | `station_runtime.py:663` `_launch_until_ready_with_retries`, backoff `:680` | core resident session runner (§2.3) |
| Readiness polling + terminate-on-not-ready | `station_runtime.py:730-756`, fail→`_terminate_process_tree` `:778` | core: readiness is an in-process state transition; no kill on "not ready" |
| Restart-on-not-ready backoff | `_monitor_process:848`, `:867-883` | DELETED. Restart only on crash. |
| Retry/cooldown semantics in opener | `radio_session.py` `RadioSession.open:260` (route fallback), `ManagedRuntimeOpener.open:108` | core API (§2.1) |
| Fleet enforce/switch lifecycle | `RadioFleetSessionManager.open_radio:341` / `switch_radio:371` / `_enforce_active_limit:384` | core `connect()`/`disconnect()` per radio (§2); Pro keeps only "which radio is selected" + process supervision |

### 1.3 What Pro is ALLOWED to keep

- Process spawn/Popen of the core-station entrypoint (`station_runtime.py:712`).
- Process-liveness monitor (PID alive? exit code?).
- SIGTERM-to-stop + bounded wait, then SIGKILL **only** as a last-resort
  hung-process safety (not as a lifecycle tool).
- Restart **only** on genuine crash (with crash backoff, not readiness
  backoff).
- Selection/preferences/UI plumbing (which radio, fleet membership, surfacing
  core-emitted lifecycle state to the UI).

### 1.4 The thin Pro↔core contract

Core station process exposes, over its existing local control surface:

- **Liveness:** process stays up while a session is *resident* (connecting,
  cooling-down, connected, or recovering). The process does **not** exit on
  transient radio failure — it stays alive and keeps retrying in-process.
- **Lifecycle status events:** core emits a structured lifecycle state
  (`DISCONNECTED/SCANNING/CONNECTING/COOLDOWN/CONNECTED/RECOVERING/CLOSING`,
  §2) for Pro/UI to *observe*. Pro never acts on these to kill/restart;
  they are display + selection signals only.
- **Stop:** SIGTERM → core runs full async graceful close (token-remove +
  OpenClose-close) BEFORE exit; exits 0. Pro waits for exit.
- **Crash contract:** any non-zero/abnormal exit = genuine crash → Pro may
  restart with crash backoff. A clean exit after SIGTERM = no restart.

### 1.5 Enforcing/testing the boundary

- Keep and tighten the import-linter `public-sdk-only` contract
  (`rigplane-pro/.importlinter`): Pro imports the public `rigplane.*` SDK only,
  never lifecycle internals (`rigplane.runtime._control_phase`,
  `rigplane.radio` lifecycle methods beyond what the public SDK blesses).
- **New static gate:** a lint/test that fails if `rigplane_pro` source
  references any of the retired lifecycle primitives (token send, OpenClose,
  cooldown constants, retry loops). The presence list to ban:
  `_STATUS_RETRY_PAUSE`, `_STATUS_REJECT_COOLDOWN`, `_DATA_PORT_COOLDOWN_RETRIES`,
  `_send_token`, `_send_open_close`, and the Pro-local retry/backoff in
  `station_runtime.py`/`radio_session.py`.
- **Behavioral gate:** a Pro-side test asserting the supervisor issues SIGTERM
  (never SIGKILL-first) on stop and does **not** restart on a core "not ready"
  status (only on process death). Guards against Cause B regressing.

---

## 2. Unified core API / state machine

New module (open-core): `rigplane/runtime/session_lifecycle.py` (proposed),
exposing a `RadioSessionLifecycle` (the resident state machine) that
`CoreRadio` (`radio.py`) delegates to. `_control_phase.ControlPhaseRuntime`
becomes the *mechanism* (packet I/O) driven by this *policy* layer; the retry
wrapper in `_control_phase.py:113-141` is removed and re-expressed as state
transitions.

### 2.1 Public API surface (the only lifecycle entry points)

```text
async def scan(targets) -> list[Presence]     # presence-probe only; NO session
async def connect() -> None                    # resident; cooldown-aware; never abandons a held session
async def disconnect() -> None                 # ALWAYS releases (token-remove + close) via finally/RAII
async def soft_reconnect() -> None             # RECOVERING; reuse control+token (#1217)
@property  state -> LifecycleState             # observable
async def __aenter__/__aexit__                 # delegate to connect/disconnect; aexit ALWAYS releases
```

`connect()` is **resident**: it does cooldown-aware retry *in-process* and
returns only on CONNECTED or on a caller-cancel; on transient failure it does
NOT raise out to a layer that would tear the process down. The process stays
alive across cooldowns. This is the structural fix for Cause A+B.

### 2.2 States and transitions

```text
        scan()                 connect()
DISCONNECTED ──► SCANNING ──► DISCONNECTED
     │                            
     │ connect()                  
     ▼                            
 CONNECTING ──auth+civ_port>0──► CONNECTED
     │  │                           │
     │  │ civ_port==0 (not ready    │ data stall / ctrl loss (#1217)
     │  │   OR 0xFFFFFFFF reject)   ▼
     │  ▼                        RECOVERING ──ok──► CONNECTED
     │  COOLDOWN ──wait, NO re-claim──► CONNECTING   │ fail (max)
     │  (same process; token NOT held)               ▼
     │                                             CLOSING
     └────────── disconnect()/SIGTERM ──────────► CLOSING ──► DISCONNECTED
```

Transition rules (each maps to a guaranteed-release point, §2.5):

- **DISCONNECTED → SCANNING → DISCONNECTED:** `scan()` opens no session; it is
  a stateless broadcast probe (§2.4). No token, no OpenClose, ever.
- **DISCONNECTED → CONNECTING:** begin `_connect_once`
  (`_control_phase.py:143-456`). From the moment auth succeeds (`:200-207`) the
  session is considered **claimed** and a release obligation is registered
  (RAII, §2.5) — this closes Hole 1.
- **CONNECTING → CONNECTED:** `civ_port > 0`, transports open, OpenClose-open
  sent (`:418`), pumps/watchdog up (`:423-451`). Emit CONNECTED.
- **CONNECTING → COOLDOWN:** `civ_port == 0` (not ready) OR `0xFFFFFFFF`
  (explicit reject). **Before** entering COOLDOWN the *current attempt's*
  partial session is fully released (token-remove + close + socket close) —
  this is the inversion of today's Hole 4: we release first, *then* wait.
- **COOLDOWN → CONNECTING:** after the wait (`10 s` not-ready / `30 s` reject,
  preserved as policy constants, now owned by the state machine), retry —
  **without ever having left the process** and only after release, so the
  radio's keepalive can expire normally. No fast cross-process re-claim.
- **CONNECTED → RECOVERING:** data-watchdog stall or control-loss (#1217);
  folds in existing `soft_reconnect` (`_control_phase.py:547-723`) and the
  data watchdog (`_civ_rx.py:822-924`).
- **RECOVERING → CONNECTED:** soft reconnect re-opens data path reusing
  control + token (`_control_phase.py:676`); on success resume.
- **RECOVERING → CLOSING:** recovery exhausted (`_MAX_RECONNECTS=3`) → full
  release, then DISCONNECTED (or retry via COOLDOWN per policy).
- **any → CLOSING → DISCONNECTED:** `disconnect()` / SIGTERM. CLOSING ALWAYS
  releases regardless of `conn_state` (today's `:494` guard is removed from the
  release path — see §2.5/Hole 8).

### 2.3 Resident session runner (Cause A+B fix)

A single async task owns the CONNECTING↔COOLDOWN loop **inside the core
process**:

- It NEVER abandons a stale/claimed session: a partial claim always carries a
  registered release obligation discharged before any wait or exit.
- It does NOT exit the process on transient failure → Pro never sees a "dead"
  process during a cooldown → Pro never relaunches → port `50001` is not
  re-opened inside the keepalive window.
- The cooldown wait is *cancellable*: SIGTERM cancels it and the runner
  transitions to CLOSING with full release (§2.6).
- Backoff/cooldown policy constants (`_STATUS_RETRY_PAUSE=10`,
  `_STATUS_REJECT_COOLDOWN=30`, `_DATA_PORT_COOLDOWN_RETRIES=3`) move here as
  the *only* home for retry timing.

### 2.4 `scan()` = presence-probe only

Reuse the stateless discovery path (`backends/discovery.py`): broadcast
are-you-there (AYT `0x03`, `transport.py:285`), parse IAH (`0x04`,
`transport.py:301`, remote_id off8 `:302`), close socket in `finally`
(`discovery.py:675`). `scan()` **never** sends login/token/conninfo/OpenClose
and never holds a session. It returns presence records only. This guarantees a
scan can never trip the radio's single-owner lock.

### 2.5 Guaranteed RELEASE on every exit (RAII / `finally`)

Release = `_send_token(0x01)` (`_control_phase.py:538`) + OpenClose-close
(`_send_open_close(...close)`, civ `:524` / audio `:517`) + control disconnect
(`:541`) + socket close.

The release becomes an **obligation object** registered the instant the session
is *claimed* (auth success, `:200-207`), discharged by:

- an `async with`/`AsyncExitStack`-style guard wrapping the whole
  `_connect_once` body, so any exception between auth and CONNECTED still
  releases (closes Hole 1);
- `__aexit__` always calling the release path (closes Hole 2);
- the CLOSING transition calling release **unconditionally** — the
  `conn_state != CONNECTED` early-return (`_control_phase.py:494`) is moved off
  the release path so a partially-claimed session is still released
  (closes Hole 8 and Hole 5).

`disconnect()` is **idempotent**: calling it on an unclaimed session is a
no-op; on any claimed/partial session it releases.

### 2.6 Graceful shutdown on SIGTERM

Replace the core SIGTERM handler's `os._exit` with an async-aware shutdown:

- SIGTERM sets a shutdown event; the resident runner (and any cooldown sleep,
  CONNECTING, or RECOVERING task) is cancelled cooperatively.
- The state machine transitions to CLOSING and runs the full release
  (§2.5) **before** the event loop stops and the process exits 0.
- A bounded shutdown deadline (e.g. 2-3 s, less than the radio keepalive)
  guards against a hung close; only after the deadline may the process
  hard-exit — and Pro's SIGKILL is the outer backstop, not the normal path.

This closes Hole 3 (kill-without-cleanup) and Hole 4 (SIGTERM during cooldown
leaving the session held).

### 2.7 Auto-recovery / soft_reconnect (#1217) folded in

`soft_reconnect` (`_control_phase.py:547-723`) and the data watchdog
(`_civ_rx.py:822-924`, consts `_CIV_DATA_WATCHDOG_TIMEOUT=2.0`,
`_RECONNECT_BACKOFF=(45,60,60)`, `_MAX_RECONNECTS=3`) become the CONNECTED→
RECOVERING→CONNECTED transitions. Key invariant preserved from #1217: soft
reconnect **reuses control + token** (re-open only, no close-first,
`:676`) and re-arms the watchdog (`_civ_rx.py:992-1017`). Recovery exhaustion
goes through CLOSING (full release), never an abandoned session.

---

## 3. Closing all 8 graceful-close holes (map section E)

| Hole | Map ref | Design fix |
| --- | --- | --- |
| **1** post-auth/pre-CONNECTED exception leaves control+token held | `_send_token_ack:217` / `_receive_guid:219` / `_send_conninfo:243` / civ-port wait | Release obligation registered at auth success (`:200-207`); `AsyncExitStack` around `_connect_once` discharges it on any exception (§2.5). |
| **2** `__aenter__` raises before `__aexit__` | `radio.py:1182-1184` | `__aenter__` wraps `connect()` so its own failure runs release; `__aexit__` always releases (§2.5). |
| **3** Pro kills process w/o cleanup | `station_runtime.py:1247`/`1258`/`1265`; core `os._exit` | Async SIGTERM handler runs full release before exit (§2.6); Pro stops via SIGTERM + wait, SIGKILL only as backstop (§1.3). |
| **4** in-process cooldown holds session — ROOT CAUSE | `_control_phase.py:128-141`, `:334-341` | Resident runner **releases before** cooldown wait, then waits inside one process; SIGTERM cancels the wait into CLOSING+release (§2.3/§2.6). |
| **5** cooldown loop raises into unguarded `__aenter__` | `_control_phase.py:122-127` | Cooldown is a state, not an exception path; CONNECTING/COOLDOWN failures release via the same obligation; `__aenter__` guarded (Hole 2). |
| **6** Pro retry-without-release relaunch reopens `50001` in 1-4 s | `station_runtime.py:867-883` | Restart-on-not-ready deleted; cooldown lives in-process; Pro restarts only on crash (§1.2/§1.3). |
| **7** `__del__` is warn-only | `radio.py:903-912` | `__del__` stays a warning (can't run async reliably); correctness moves to the always-run `__aexit__`/CLOSING release + SIGTERM handler, so a leaked object is no longer the only safety net. |
| **8** partial cleanup on rejection: ctrl-disconnect + DISCONNECTED, NO token-remove | `_control_phase.py:298-300`/`320-322`/`355-357`; `_cleanup_data_port_discovery_timeout:464-489` | Rejection/timeout cleanup routes through the unconditional release path (token-remove included), not the partial cleanup; remove the `conn_state` guard from the release path (§2.5). |

---

## 4. IC-7610 simulator (`MockIcomRadio` extension)

Extend the existing `MockIcomRadio` (`tests/mock_server.py:143-786`,
`_MockProtocol:110`) rather than replace it — it already models the control+CIV
UDP servers, AYT/IAH+login+token+conninfo→status handshake, a CI-V dispatcher,
state setters, and `auth_fail`/`response_delay` flags. Reuse all of that.

### 4.1 What to ADD (the session-realism the GAPS note calls out)

1. **Held single-owner session.** Track an owner identity = `(remote_addr,
   my_id)` where `my_id=(lport&0xFFFF)|0x10000` (`transport.py:216`). On
   conninfo (`auth.py:292`) from a *new* owner while a session is held, reply
   status with `civ_port=0` + `error=0xFFFFFFFF`
   (`auth.py:406-431` layout: error LE@0x30, civ_port BE@0x42). This is the
   "previous session active" reject.
2. **Keepalive timeout / immediate-release semantics.** On token-remove
   (`_send_token(0x01)`, `pkt[0x14]=0x01`, magic@0x15, `_control_phase.py:932-947`)
   release the held session **immediately**. With NO token-remove, hold the
   session for a `keepalive_hold_s` window (default modeling ~40-60 s,
   **configurable + accelerated for tests**, e.g. 0.5-2 s) before auto-release.
   Renew (`magic 0x05`, `:925`) and ack (`0x02`) refresh the keepalive.
3. **Autonomous state evolution + unsolicited CI-V streaming.** A background
   task emits unsolicited CI-V frames (freq/mode drift) on the CIV port at a
   configurable rate, independent of polled commands — exercises the pump
   (`_civ_rx.py` worker) and the "data flowing" watchdog reset.
4. **Packet loss / stall / reorder.** Configurable knobs:
   `drop_rate`, `reorder_window`, and a `stall_for(seconds)` injector that
   stops emitting CI-V to trip the data watchdog
   (`_CIV_DATA_WATCHDOG_TIMEOUT=2.0`, `_civ_rx.py:515-516`/`822-924`).
5. **Cooldown trigger knob.** `civ_port_unavailable_until(t)` to force
   `civ_port=0` (not-ready, no `0xFFFFFFFF`) for a window, distinct from the
   busy-reject, so tests can drive CONNECTING→COOLDOWN→CONNECTING.

### 4.2 What to REUSE

Handshake parsing/builders, control+CIV socket plumbing, the CI-V command
dispatcher, existing `auth_fail`/`response_delay`, and the `conftest.py`
fixtures (`mock_radio:150`, `connected_radio:159`). New behavior is added as
opt-in flags/methods so existing 60+ control/CIV tests
(`test_radio.py` `MockTransport:227-286`, `test_mock_integration.py:26-84`)
remain green.

---

## 5. Test matrix (with the simulator)

Each row maps to the hole/requirement it guards. All time-based cases use the
simulator's accelerated `keepalive_hold_s`.

| # | Scenario | Expected | Guards |
| --- | --- | --- | --- |
| T1 | Clean `connect()` → `disconnect()` | Radio free **immediately** (token-remove received); a subsequent `connect()` succeeds with NO cooldown observed | Governing principle; Hole 8 release-on-exit |
| T2 | Abort connect **without** close (simulate exception, no release) against an *unpatched-style* abort | Simulator holds session → next `connect()` gets `civ_port=0`+`0xFFFFFFFF` | Reproduces the bug; baseline for T3 |
| T3 | Same abort path **with the fix** (release obligation fires) | Radio free immediately → next `connect()` succeeds | Hole 1, Hole 5, §2.5 |
| T4 | Resident cooldown-aware retry: simulator forces `civ_port=0` for a window, then frees | Single process waits out cooldown (COOLDOWN→CONNECTING) and connects; **no second process, no `0xFFFFFFFF` livelock** | Cause A+B, Hole 4/6, §2.3 |
| T5 | SIGTERM mid-CONNECTING and mid-COOLDOWN | Core runs graceful close → token-remove sent → radio free; process exits 0; no held session | Hole 3, Hole 4, §2.6 |
| T6 | `scan()` against held + free radios | Presence returned; simulator records **no** login/token/conninfo from scan; session ownership unchanged | §2.4 |
| T7 | `soft_reconnect()` after data stall | Control + token reused (no new login), data path re-opened, CONNECTED resumes | #1217, §2.7 |
| T8 | Data-watchdog stall (`stall_for > 2 s`) | CONNECTED→RECOVERING→CONNECTED via soft reconnect; watchdog re-armed | `_civ_rx.py:822-924`, §2.7 |
| T9 | Duplicate/concurrent `connect()` | Second call is rejected/coalesced; exactly one session claimed; no double token | §2.1 idempotency |
| T10 | Fleet switch: connect A, switch to B | A is closed **gracefully** (token-remove for A) before B connects; A free immediately | §1.2 fleet move, Hole 8 |
| T11 | Recovery exhaustion (`_MAX_RECONNECTS=3` all fail) | Routes through CLOSING with full release; no abandoned/held session | §2.7 |
| T12 | Pro supervisor behavior (Pro-side) | On core "not ready" status, Pro does **not** restart; on SIGTERM Pro waits for clean exit; restart only on crash exit code | §1.3/§1.5 boundary |

Static/boundary tests (§1.5): import-linter `public-sdk-only` stays green; the
new "no lifecycle primitives in `rigplane_pro`" grep gate passes.

---

## 6. Migration / rollout plan (phased)

> beta.9 flip is HELD until this completes. Sequencing owned separately.

- **Phase A — core foundation (open-core).** Land the
  `RadioSessionLifecycle` state machine, the resident runner, the
  guaranteed-release obligation, the async SIGTERM handler, extend
  `MockIcomRadio`, and add the full test matrix (§5). No call-site changes yet;
  the new module can sit beside the existing path behind an internal switch.
- **Phase B — route all core call sites through it.** Make `CoreRadio.connect/
  disconnect/soft_reconnect/scan` (`radio.py:1165/1189/1270/3124`) and the
  control/runtime callers (`web/handlers/control.py:739`,
  `radio_reconnect.py:183`, `_civ_rx.py:939/2724/2737`) delegate to the state
  machine. Remove the retry wrapper `_control_phase.py:113-141` and the partial
  cleanup paths (Hole 8). Keep the public SDK signatures stable.
- **Phase C — Pro thin-supervisor refactor (Pro repo).** Strip lifecycle from
  `station_runtime.py` (`_launch_until_ready_with_retries:663`, readiness-kill
  `:778`, restart-on-not-ready `:867-883`) and `radio_session.py`
  (`RadioSession`/`RadioFleetSessionManager`/`ManagedRuntimeOpener`); reduce to
  spawn + liveness + SIGTERM + crash-only restart. Add the boundary gates
  (§1.5).
- **Phase D — core release + re-pin Pro + rebuild.** Cut a core release that
  contains the lifecycle module; bump Pro's pin and `CORE_VERSION` **in
  lockstep** (currently `rigplane[bridge]==2.10.1`, `pyproject.toml:16`) — the
  pin and `CORE_VERSION` must move together or the gate-pytest fails (known
  coupling). Rebuild the frozen Tauri sidecar (full build, not
  `--skip-sidecar`) so Python lifecycle changes ship in the app. Then unblock
  the beta.9 flip.

**Core publish gate reminder:** core's publish requires
`mypy --strict src/rigplane/web` green; the new module and any web/handler
delegation (`web/handlers/control.py:739`) must satisfy strict typing before
the Phase D release.

---

## 7. Open / private boundary per piece

| Piece | Boundary | Rationale |
| --- | --- | --- |
| `RadioSessionLifecycle` state machine, resident runner, guaranteed-release, SIGTERM handler | **open-core** | Generic radio session/transport lifecycle; this is the whole point of the invariant. |
| `scan()` presence-probe (reuses `backends/discovery.py`) | **open-core** | Generic discovery. |
| `soft_reconnect`/data-watchdog folded into RECOVERING | **open-core** | Already core (#1217). |
| `MockIcomRadio` extensions + core test matrix | **open-core** | Lives in core `tests/`. |
| Pro thin supervisor (spawn/liveness/SIGTERM/crash-restart) | **proprietary-only (Pro)** | Desktop packaging/process supervision is Pro's domain; it must contain no lifecycle logic. |
| Boundary gates (import-linter + no-lifecycle-primitives grep + Pro supervisor behavior test) | **mixed/split** | import-linter + grep gate in Pro; lifecycle-ownership tests in core. |

This is an **open-core candidate** for the lifecycle module and a
**proprietary-only** refactor for the Pro supervisor — i.e. `mixed/split`
overall, with the intelligence going open.

---

## 8. Risks

- **R1 — Resident process semantics change Pro's mental model.** Pro must stop
  treating "no readiness" as a failure. If any Pro path still kills on
  not-ready, Cause B regresses. Mitigation: §1.5 behavioral gate (T12).
- **R2 — SIGTERM graceful-close deadline vs hung close.** If the async close
  hangs and the deadline is too long, stop becomes slow; too short and we
  hard-exit without release. Mitigation: deadline < radio keepalive; Pro
  SIGKILL backstop only after core's own deadline.
- **R3 — Accelerated keepalive in tests vs real radio timing.** Simulator
  acceleration must not mask real timing bugs. Mitigation: keep at least one
  test at near-real `keepalive_hold_s`; document the chosen accelerated value.
- **R4 — Lockstep pin/release coupling (Phase D).** Forgetting to move
  `CORE_VERSION` and the `==` pin together fails gate-pytest. Mitigation:
  release skill checklist.
- **R5 — `mypy --strict src/rigplane/web` debt.** New delegation in web
  handlers could trip strict generics debt and block publish. Mitigation:
  type the new surface up front in Phase A/B.
- **R6 — Frozen sidecar staleness.** Lifecycle is Python; a `--skip-sidecar`
  build won't show it. Mitigation: Phase D full rebuild (documented gotcha).
- **R7 — soft_reconnect ↔ resident runner interaction.** RECOVERING and
  CONNECTING must not both drive a reconnect concurrently (existing
  `_civ_recovery_lock`, `_civ_rx.py:2680-2751`). Mitigation: single state
  machine owns mutual exclusion; T7/T8/T11 cover it.

---

## Open Questions

1. **Cooldown policy ownership at the API edge:** should `connect()` ever
   surface a "still cooling down" status to Pro/UI (for display), or stay fully
   opaque until CONNECTED/failed? (Affects the lifecycle-event contract §1.4.)
2. **SIGTERM graceful-close deadline value:** what exact bound (2 s? 3 s?) and
   how does it relate to the assumed radio keepalive (40-60 s) and Pro's
   SIGKILL backstop timer (`station_runtime.py` terminate timeout)?
3. **`connect()` resident vs caller-cancellable contract:** is there any case
   where `connect()` *should* give up and return failure (e.g. auth-cred fail
   `0xFEFFFFFF`, which is non-transient) vs retry forever? Propose: hard-fail
   on `0xFEFFFFFF`, resident-retry on `civ_port==0`/`0xFFFFFFFF`. Confirm.
4. **Fleet single-owner limit:** does the radio (and design) allow exactly one
   active session per radio only, or one per companion across radios? Affects
   `_enforce_active_limit` removal (§1.2) and T10.
5. **Crash-restart backoff retention in Pro:** keep a (smaller) crash backoff,
   and should repeated genuine crashes eventually surface as a hard error to
   the UI rather than infinite restart?
6. **Public SDK exposure of `scan()`/lifecycle state:** which of the new
   symbols become blessed Tier-1/2 public API (per
   `docs/api/public-api-surface.md`) so Pro can consume them without tripping
   import-linter?
7. **Accelerated keepalive default for the simulator:** pick the canonical
   accelerated `keepalive_hold_s` for the suite and the one near-real value for
   the slow-marked timing test (R3).
8. **Real-radio validation gate:** which of T1-T11 require human-in-the-loop
   validation against a physical IC-7610 before the beta.9 flip, given the
   simulator can't perfectly reproduce firmware keepalive behavior?

---

## Approved Decisions (2026-06-22)

The 8 open questions above are RESOLVED as follows. These decisions are
binding for the implementation plan
(`2026-06-22-radio-session-lifecycle-PLAN.md`). Several carry **refinements**
that reshape the design beyond a plain yes/no answer — read those in full.

### D1 — Rich lifecycle feedback is a FIRST-CLASS requirement (Q1)

Resolves Q1, but **strengthens** it: the lifecycle controller MUST NOT be
opaque. The core `RadioSessionLifecycle` MUST expose a **comprehensive,
structured observable surface** as a built-in capability of the controller —
not a UI afterthought bolted on later:

- every **state transition** (`DISCONNECTED/SCANNING/CONNECTING/COOLDOWN/
  CONNECTED/RECOVERING/CLOSING`) with from→to + cause;
- **connecting progress** and **cooldown progress + countdown** (remaining
  seconds until next retry attempt);
- **error reasons** (e.g. auth-cred fail `0xFEFFFFFF`, busy-reject
  `0xFFFFFFFF`, not-ready `civ_port==0`, data-watchdog stall);
- **recovery events** (RECOVERING entered/exited, attempt N of M).

This is exposed as a structured **event/status API in core** (an observable
state object plus an event stream/callback). The client (Pro/UI) consumes a
**subset** of it, but the CAPABILITY is part of the core controller's public
contract. This MUST be designed up front in Phase A2 alongside the state
machine, not retrofitted. It is part of the blessed public-SDK surface (D6).

### D2 — SIGTERM graceful-close timing (Q2)

- **Core close target: ~2-3 s.** The async SIGTERM handler runs the full
  release (token-remove + OpenClose-close) and aims to complete well under the
  radio keepalive window. Bounded shutdown deadline 2-3 s (§2.6).
- **Pro backstop: SIGTERM → wait ~5 s → SIGKILL.** Pro sends SIGTERM, waits
  ~5 s for a clean exit, and only then issues SIGKILL as a last-resort
  hung-process backstop. SIGKILL is NEVER the first action and NEVER a
  lifecycle tool. Pro's wait (~5 s) is deliberately longer than core's close
  target (~2-3 s) so the normal path is always a clean core exit.

### D3 — Retry policy, AND minimize cooldown by design (Q3)

- **Hard-fail on `0xFEFFFFFF` (auth/credentials).** Non-transient: `connect()`
  does NOT resident-retry; it transitions to CLOSING with full release and
  surfaces an auth error reason (D1). Pro/UI shows a hard credential error.
- **Cooldown-aware resident retry on `civ_port==0` and `0xFFFFFFFF`.** These
  are transient; the resident runner waits in-process and retries (§2.3).
- **REFINEMENT — minimize cooldown events by design.** Cooldown is NOT a
  normal operating mode. Because graceful close is now guaranteed on EVERY exit
  path (§2.5/§2.6), a self-inflicted held session must become essentially
  impossible. Therefore in normal operation cooldown should **almost never**
  occur. Cooldown handling exists ONLY for a genuinely **FOREIGN** held session
  (another client/host holds the radio), never for a session this process
  tore down itself. If tests or telemetry show cooldown firing after our own
  clean disconnect, that is a BUG in the release path, not expected behavior.

### D4 — ARCHITECTURE REFINEMENT: multi-radio = multiple core services (Q4)

This **reshapes the multi-radio model** and supersedes the simpler reading of
§1.2 / §1.3. The "Companion" is the **Tauri GUI app**, which is a **THIN
wrapper/interface to core**. Division of responsibility:

- **Pro stores the fleet.** Discovered radios + credentials are owned by Pro as
  the inventory/UI/storage layer.
- **ALL connections live in the embedded core service.** No connection
  lifecycle runs in Pro.
- **Multi-radio = MULTIPLE core service instances — one per radio — on
  different ports.** (Future evolution: in-process core objects rather than
  separate processes.) Switching between radios MAY also be done within a
  single core service.
- Therefore Pro's `RadioFleetSessionManager` becomes: **fleet inventory
  (storage) + spawn/supervise N core services (one per active radio).** The
  per-radio session lifecycle lives ENTIRELY in that radio's own core service.
- **Re-express single-owner / `_enforce_active_limit`
  (`radio_session.py:384`) as "number of concurrent core services," NOT
  multiple sessions inside one process.** There is exactly **one session per
  core service per radio**; the radio enforces single-owner on the wire anyway.
  Pro's job is to bound how many core services run concurrently, not to manage
  sessions within a process.

Implication for §1.2 / Phase C: the Pro refactor is not just "strip retry"; it
is "fleet storage + N-supervisor," where N core services each own one radio's
lifecycle. T10 (fleet switch) is re-expressed accordingly (graceful close in
the source radio's core service before/independent of the target's).

### D5 — Keep a smaller crash backoff in Pro; hard-error on repeated crashes (Q5)

Pro's **process supervisor** keeps a (smaller) **crash backoff** — for genuine
process death only, never for "not ready". Repeated genuine crashes
(threshold TBD by implementer, e.g. N crashes within a window) MUST be
surfaced as a **hard UI error** rather than infinite silent restart.

### D6 — Lifecycle API is blessed public-SDK, contract-grade (Q6)

The lifecycle API becomes **blessed public-SDK** and MUST therefore be
**contract-grade: minimal, stable, versioned, additive-only, documented**. Pro
consumes it **across the core/Pro version boundary**, so the surface must be
designed deliberately and kept small. Implications:

- The new symbols are added to `docs/api/public-api-surface.md` as supported
  exports so Pro can import them via the public `rigplane.*` SDK without
  tripping the import-linter `public-sdk-only` contract.
- Changes follow the cross-repo COMPATIBILITY policy
  (`rigplane-pro/docs/contracts/COMPATIBILITY.md`, MOR-885): additive-only,
  versioned; no breaking changes to the blessed surface without a coordinated
  core↔Pro cadence.
- The observable feedback surface (D1) is part of this contract — design the
  event/status types as stable public types from the start.

Candidate blessed symbols (finalized in the plan):
`RadioSessionLifecycle`, `LifecycleState` (enum), the lifecycle event/status
type(s) (e.g. `LifecycleEvent`, `LifecycleStatus`), and the
`scan/connect/disconnect/soft_reconnect/state` surface.

### D7 — Test keepalive acceleration (Q7) — orchestrator's call

Accelerated `keepalive_hold_s` ~**0.5 s** for the fast suite (fast tests), plus
**one near-real ~45-60 s `@slow`-marked timing test** to guard against
acceleration masking real timing bugs (R3). Exact value is the orchestrator's
call within that band; document the chosen accelerated value in the simulator
and the slow test.

### D8 — Physical IC-7610 validation scope before flip (Q8) — orchestrator's call

The radio IS available for human-in-the-loop validation. Recommended minimum
physical-radio set before the beta.9 flip (final scope is the orchestrator's
call):

- **T1** — clean `connect()` → `disconnect()` with NO cooldown observed;
- **T3** — abort→release fix (release obligation fires, radio free immediately);
- **T6** — `scan()` does not disturb session ownership / SIGTERM → radio free
  (validate graceful close frees the radio on a real keepalive window);
- **`soft_reconnect`** on the real radio (RECOVERING path).

These exercise the governing principle and the holes most sensitive to real
firmware keepalive behavior that the simulator cannot perfectly reproduce.
