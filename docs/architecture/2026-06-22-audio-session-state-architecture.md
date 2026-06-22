# AudioSession state architecture — TX-stuck-in-"receiving" root-cause and clean fix

**Date:** 2026-06-22
**Status:** architectural analysis (read-only — no production code changed by this doc)
**Scope:** `rigplane-core` audio session/transport state machine
**Boundary:** open-core (generic LAN audio TX state machine and recovery)
**Companion bug instance:** after SSB (phone) → FT8 (data, MOD IN = LAN), TX
audio stops modulating (Po = 0 W); core `AudioSession` is stuck in
`receiving` and rejects every TX push with
`AudioNotStartedError: Cannot push TX in state receiving`.

> This document is the **authoritative architectural verdict and clean-fix
> design**. A concurrently-running diagnostic agent landed a working but
> narrow patch (commit `ad7737ce`, see §0); that patch is correct as a
> *symptom* fix and a good regression test, but it is the *third* instance
> of the exact anti-pattern that produced the bug. The recommendation below
> is to converge the three scattered arming sites into one declarative
> reconciler rather than keep adding per-edge special cases.

---

## 0. What the diagnostic agent already found (and shipped)

Commit `ad7737ce` on `codex/radio-session-lifecycle`:

> *Root cause:* a digital client holds ONLY a TX lease and no RX subscription,
> so the session sits in `TX_ONLY`. `AudioSession.reestablish` (the
> session-demand re-establishment that lifecycle recovery routes through)
> never handled the `TX_ONLY` desired state. It treated any non-IDLE demand
> as RX-bearing, called `bus.restart_rx` (a no-op with zero subscribers),
> saw `rx_active is False`, raised `RuntimeError` and stranded the session at
> `RX_ONLY`. The TX leg was never re-armed, so the fresh LAN stream stayed
> `RECEIVING` and every later `TxLease.push` hit it — `_ensure_tx_for_push`
> only arms from IDLE, so it no-op'd at `RX_ONLY` and the push was rejected.

That diagnosis is **correct and confirmed by reading the code**. The fix it
shipped adds a `TX_ONLY` branch to `reestablish`
(`session.py:624-640`). It makes the reported scenario work and ships a RED→GREEN
regression test (`test_reestablish_rearms_tx_only_digital_session`).

What that fix does **not** do — and what this document argues must be done —
is remove the *reason a third site needed a fourth special case*. After the
patch, "should the TX leg be armed?" is computed independently in **three**
places that must be kept mutually consistent by hand:

1. `_ensure_tx_for_push` (lazy arm on push, `session.py:348-376`),
2. `_reconcile`'s `TX_ONLY` branch (demand-edge arm, `session.py:473-493`),
3. `reestablish`'s new `TX_ONLY` branch (recovery arm, `session.py:624-640`).

The original bug *was* exactly site (3) disagreeing with sites (1) and (2).
There is no structural guarantee a fourth edge will not reintroduce the same
class. That is the architectural problem.

---

## 1. Root-cause as a CLASS

### 1.1 There are two stacked state machines, and they can disagree

There are **two** independent audio state machines, one layered on the other,
each with its own enum and its own transition rules:

| Layer | State enum | Owner | File |
| --- | --- | --- | --- |
| Session (demand) | `IDLE / RX_ONLY / RX_TX / TX_ONLY / RECOVERING / FAILED` | `AudioSession` | `audio/session.py:101` |
| Transport (stream) | `IDLE / RECEIVING / TRANSMITTING` | `IcomAudioStream` | `audio/lan_stream.py:78` |

The error string `Cannot push TX in state receiving`
(`lan_stream.py:622-623`) is raised by the **transport** layer; the bug is
that the **session** layer believed TX was (or should be) armed, or failed to
arm it, while the transport sat in `RECEIVING`. The two layers desynced.

The transport layer is itself lenient and correct in isolation:

- `start_tx` from `RECEIVING` is **allowed** — it overrides
  `RECEIVING → TRANSMITTING` (`lan_stream.py:602-606`). Full duplex on one
  UDP stream.
- `start_rx` requires `IDLE` (`lan_stream.py:523`).
- `stop_tx` from `TRANSMITTING` reverts to `RECEIVING` if the RX task is live,
  else `IDLE` (`lan_stream.py:645-651`).
- `push_tx` requires `TRANSMITTING` (`lan_stream.py:622`).

So the transport will *accept* a push **iff** somebody called `start_tx` since
the last teardown. The session is the *only* thing that calls `start_tx`. The
bug is therefore entirely a **session-layer** failure to call `start_tx` at a
moment when TX is demanded.

### 1.2 `_desired()` is NOT a pure function of intent — TX-intent is not an input

The intended design (`session.py:407-426`):

```python
def _desired(self) -> AudioSessionState:
    if not self._rx_subs:
        if self._state is AudioSessionState.TX_ONLY and self._tx_leases:
            return AudioSessionState.TX_ONLY
        return AudioSessionState.IDLE
    if self._tx_leases:
        return AudioSessionState.RX_TX
    return AudioSessionState.RX_ONLY
```

This is the architectural defect in one function. **`_desired()` reads its own
current `self._state` to decide whether TX is wanted.** That makes it *not a
pure function of intent* — it is a function of intent **and** the previous
output. Concretely:

- `tx_leases > 0 and rx_subs == 0` does **not** map to "TX wanted". It maps to
  `TX_ONLY` **only if the session already happened to be in `TX_ONLY`**;
  otherwise it maps to `IDLE` — i.e. "no TX wanted" — *even though a TX lease
  is held*.
- The genuine intent signal — "a TX lease is held, therefore TX is demanded"
  — is deliberately suppressed at the `acquire_tx` edge (the MOR-556 "don't
  arm TX before RX on the shared LAN stream" rule). The suppression was
  implemented by **dropping TX-intent out of `_desired()` entirely** and
  re-introducing it imperatively, lazily, at push time.

The consequence: **TX-intent is a second-class, latched signal.** The session
can be in a state where TX is demanded (a non-released `TxLease` exists *and*
the digital client is actively pushing frames) yet `_desired()` returns `IDLE`
or `RX_ONLY` — "TX not wanted" — so reconciliation never arms the transport.

### 1.3 Arming is push-time and edge-scattered, not reconciled

Because TX-intent was pulled out of `_desired()`, the actual arming is sprayed
across the imperative edges:

- **Acquire edge:** `acquire_tx` defers (arms nothing if no RX)
  (`session.py:327-346`).
- **Push edge:** `_ensure_tx_for_push` arms — *but only from `IDLE`*
  (`session.py:362-367` guard `self._state is AudioSessionState.IDLE and
  ... and not self._rx_subs`).
- **RX-drop edge:** `_reconcile`'s `RX_TX → TX_ONLY` branch arms (only reached
  when desired is already `TX_ONLY`, i.e. only after a push already latched it)
  (`session.py:476-483`).
- **RX-return edge:** `_enter_rx_tx`'s `TX_ONLY → RX_TX` branch re-arms
  (`session.py:503-514`).
- **Recovery edge:** `reestablish` — pre-patch did **not** arm TX-only;
  post-patch does (`session.py:624-640`).
- **Failure-recovery edge:** `_try_rearm_tx` (`session.py:670-685`).

Six edges, each with a hand-written decision about whether and how to call
`start_tx`. Every one of them must independently agree with the others on the
question "given this demand, should TX be live?". They are kept consistent
**by review, not by construction.** The reported bug is the proof that this
fails: `reestablish` was written (MOR-586) before `TX_ONLY` existed
(commit `95edb79d`), so it simply never learned the new rule.

### 1.4 Why the session was stuck in "receiving" while TX was demanded — exact trace

The companion's digital TX client (WSJT-X/FT8 over the companion bridge) holds
**only** a `TxLease` and never subscribes the session to RX. The SSB→FT8
operating sequence drives this trace:

1. **FT8 at start:** client `acquire_tx` (deferred, `IDLE`); first push →
   `_ensure_tx_for_push` arms from `IDLE` → session `TX_ONLY`, transport
   `TRANSMITTING`. Modulation reaches the radio. ✅ (This is why it works at
   session start.)
2. **SSB excursion / mode change / operator activity** triggers a recovery
   cycle: CI-V data stall or control loss → lifecycle `RECOVERING` →
   `soft_reconnect` (`_control_phase.py:667`). `soft_reconnect` tears the
   audio transport down (`_teardown_audio_transport`, `:740-745`), rebuilds it
   (`_ensure_audio_transport`, `:836-839`) — the fresh LAN stream comes back
   `RECEIVING`/idle — then calls `audio_runtime.recover(snapshot)` (`:845`),
   which routes session-managed audio to `AudioSession.reestablish`
   (`_audio_recovery.py:135-156`).
3. **`reestablish` (pre-patch)** computes `desired = _desired()`. The session
   was `TX_ONLY` so `_desired()` returns `TX_ONLY`. But the pre-patch code had
   no `TX_ONLY` branch: it fell through to the RX re-arm path, called
   `bus.restart_rx()` (no subscribers → no-op), saw `rx_active is False`,
   forced `self._state = RX_ONLY` and raised `RuntimeError`
   (`session.py:648-659`). **Session now claims `RX_ONLY`; transport is
   `RECEIVING`; TX leg never re-armed.**
4. **Next FT8 push** → `_ensure_tx_for_push`. Guard requires
   `self._state is IDLE` — but state is `RX_ONLY` — so it **no-ops**. The push
   goes straight to `transport.push_tx`, which is in `RECEIVING`, and raises
   `Cannot push TX in state receiving`. 💥 RX is unaffected because the RX leg
   genuinely is up (or the bus just has no subs); only TX is dead.

The exact architectural gap: **(a)** TX-intent (`tx_leases > 0`) is not a
first-class input to `_desired()`, so the recovery path computed a desired
state that *excluded* the held TX lease; **(b)** the lazy arm's `IDLE`-only
guard means once the session is parked in any non-IDLE state with TX still
demanded, **no push can ever recover it** — `push` rejects instead of
converging to a TX-armed state.

### 1.5 The class, stated precisely

> **TX-intent is a latched, non-reconciled signal.** Desired state is not a
> pure function of declared demand; it is a function of demand *plus* the
> previous state. Arming TX is performed imperatively at six edges that each
> re-derive the arming decision and can disagree. `push` is allowed to
> **reject** demanded TX instead of **converging** to it. Any new transition
> that forgets to special-case the latched `TX_ONLY` reintroduces the stuck
> state.

This is the *same class* the connect-path lifecycle work just eliminated:
imperative, scattered state mutation versus one declarative reconciler where
desired state is a pure function of intent and every transition converges.

---

## 2. Why the tests missed it

### 2.1 The missing invariant

No test asserts the two load-bearing invariants:

- **I1 (intent ⇒ armed):** *if a TX lease is held and pushes are arriving on a
  full-duplex transport, the transport is `TRANSMITTING` and the push
  succeeds — regardless of how the session got there.*
- **I2 (convergence):** *after any transition (acquire, push, subscribe,
  release, mode change, recovery, reconnect), the session state equals
  `_desired()` and the transport state is consistent with it.*

Both are absent. The suite tests *individual edges*, each in isolation, from a
clean session — never the **composition** of edges that the field hits.

### 2.2 The specific scenario gap: recovery × TX-only was never tested together

`tests/test_audio_session.py`:

- `test_rx_demand_returning_rearms_held_tx_lease` (`:361`) is the closest
  case — RX_TX → drop RX → re-subscribe → RX_TX with a lease held. But it
  **never pushes TX during the gap**, so the session goes to `IDLE`, *not*
  `TX_ONLY`, and the recovery-from-`TX_ONLY` path is never entered.
- `test_demand_permutations_converge` (`:217`) asserts convergence — but only
  for `rx_then_tx` vs `tx_then_rx`, both landing at `RX_TX`. It never includes
  a TX-only permutation, a recovery, or a mode switch.

`tests/test_web_audio_tx_session.py`:

- `test_digital_tx_no_rx_subscriber_pushes_without_error` (`:188`) covers the
  *happy* TX_ONLY path (acquire → push → `TX_ONLY`) — the start-of-session
  case that always worked. It does **not** then run a recovery and push again.

`tests/test_audio_recovery_session.py`:

- Covered RX-bearing reconnect (RX_ONLY/RX_TX) thoroughly, but had **zero**
  cases where `reestablish` is called with TX demand and no RX demand —
  precisely the hole the diagnostic agent's new test plugs.

So: a **test-gap**, but a gap the architecture *makes hard to close by
construction*. With six independent arming edges, the test matrix that would
catch every desync is the cross-product `{6 edges} × {prior state} × {demand
shape}` — combinatorially large and impossible to enumerate by hand. The
edges that were tested individually all passed; the bug lived only in the
*pair* (recovery-after-TX_ONLY) nobody thought to compose.

### 2.3 Does the fixture model the PTT-source × session-state interaction?

Partially, and that is itself a finding. `LanLikeRadio` / `_RecordingLanRadio`
(test doubles in `tests/test_audio_session.py`) model `start_rx/stop_rx/
start_tx/stop_tx` and a coarse `state` string, and `_audio_recovery` snapshots
`rx_active/tx_active` (`_audio_recovery.py:39-58`). What is **not** modelled:

- the **transport's own** `RECEIVING`/`TRANSMITTING`/`IDLE` machine and its
  "`push_tx` rejects unless `TRANSMITTING`" rule — the simulator's `state`
  string does not enforce that a push fails when TX was never armed, so a
  desync between session-state and transport-state is invisible to most tests;
- the **PTT source** dimension: a digital client keys via **CAT PTT** while
  audio rides the LAN stream; the session has no notion of "PTT asserted" as
  an input at all. The interaction "PTT asserted by CAT but audio TX leg not
  armed" is exactly the failure and is **not representable** in the current
  model because PTT is not a session input.

This is the deeper test-infra gap: the simulator cannot express the failing
state because the production model itself does not treat PTT/TX-frame-arriving
as a first-class input.

---

## 3. Architectural verdict

**Yes — there is an architectural problem**, and it is the same class the
connect-path `RadioSessionLifecycle` work was created to kill.

Precisely: `AudioSession` is *advertised* as a declarative reconciler
("computes the desired state from `(rx_demand>0, tx_demand>0)` and reconciles
under ONE asyncio lock" — module docstring, `session.py:13-16`) but is *not
one*. `_desired()` reads its own previous output, TX-intent is latched rather
than reconciled, and arming is performed imperatively at six edges. The
docstring describes the target; the code is the legacy shape with a
reconciler-flavoured wrapper.

The defect is **not** in the lazy-arm idea, nor in the `TX_ONLY` state, nor in
`reestablish`. Each is locally reasonable. The defect is that **the demand-to-
transport mapping has no single owner.** Six functions own a slice of it.

### 3.1 Option comparison

| Option | What it is | Pros | Cons |
| --- | --- | --- | --- |
| **A — Fold audio into `RadioSessionLifecycle`** | Make the connect-lifecycle state machine the single owner of audio re-arm too; audio legs become a sub-resource the lifecycle reconciles on `CONNECTED`/`RECOVERING`. | One owner for the whole radio session; recovery already lives there; observable events unified. | Large blast radius; couples audio demand (a Pro/companion concern) to the core connect FSM; audio demand changes far more often than connect state, so cadence mismatch; risks re-entangling layers MOR-579 deliberately separated (radio-owned audio singleton). |
| **B — Make `AudioSession` a *real* declarative reconciler** (recommended) | Keep `AudioSession` as the audio owner, but make `_desired()` a pure function of intent and route **every** edge through one `_converge()` that drives the transport to `_desired()`. Delete the five other arming sites. | Smallest correct change; fixes the *class* not the instance; mirrors the lifecycle's intent→desired→converge discipline without coupling the two FSMs; keeps the MOR-579 layering. | Requires reworking the MOR-556 ordering rule to live *inside* convergence (sequencing) rather than as intent suppression — needs care + hardware-aware ordering tests. |
| **C — Keep the diagnostic agent's patch as-is** | Per-edge special cases; add a `TX_ONLY` branch wherever a new edge needs it. | Zero further work; ships today. | Leaves the class intact; the next new edge (e.g. a session-owned retry loop, MOR-609; a poller PTT hook) reintroduces the stuck state. This is the band-aid the user explicitly rejected. |

**Verdict: Option B.** Option A over-couples; Option C is the band-aid.
Option B applies the *same pattern* as the lifecycle (declarative desired state
+ guaranteed convergence + observability) **at the right layer** — the audio
session — without folding two state machines that change at different cadences
into one.

The lifecycle relationship is **pattern-shared, not ownership-merged**:
`AudioSession` should *mirror* `RadioSessionLifecycle`'s discipline (pure
`_desired()`, single `_converge()`, `AudioSessionEvent` already exists as the
observability hook, `session.py:138-148`) and should be *driven by* the
lifecycle's `RECOVERING→CONNECTED` edge (which it already is, via
`reestablish`), but should remain a separate FSM with a separate enum.

---

## 4. The clean fix (design, not a band-aid)

### 4.1 Principle: desired state is a pure function of declared intent

Make `_desired()` depend **only** on inputs, never on `self._state`:

```
inputs  = (rx_demand>0, tx_demand>0, recovery_requested)
_desired = pure_function(inputs, transport_capabilities)
```

Mapping (full-duplex / `rx_first` transport):

| rx_demand | tx_demand | desired |
| --- | --- | --- |
| 0 | 0 | IDLE |
| >0 | 0 | RX_ONLY |
| 0 | >0 | **TX_ONLY** |
| >0 | >0 | RX_TX |

The single behavioural change vs today: `(0 RX, >0 TX) ⇒ TX_ONLY`
**unconditionally**, removing the `self._state is TX_ONLY` latch on
`session.py:421`. TX-intent becomes first-class: **holding a `TxLease` *is* the
TX demand**, full stop.

For exclusive/atomic USB transports, the same table holds except the
`(0 RX, >0 TX)` row maps to a transport-specific deferred state (their TX leg
requires the co-armed duplex stream); this is a **sequencing/capability**
decision made *inside* convergence (§4.3), not by mutating intent.

### 4.2 Principle: every edge recomputes and converges; nothing arms ad-hoc

Replace the six imperative arming sites with **one** `async _converge()` called
under the lock by every demand mutation, by recovery, and by push:

```
acquire_tx / release_tx / subscribe_rx / release_rx / reestablish / push
        └────────────────► _converge() ──► drive transport to _desired()
```

`_converge()` is the only function that calls `start_tx/stop_tx/start_rx/
stop_rx/restart_rx`. It:

1. computes `desired = _desired()`;
2. diffs `desired` against the **observed transport state** (RX leg live? TX
   leg live?) — not against a remembered `self._state` that can lie after a
   transport rebuild;
3. issues the minimal transport calls to reach `desired`, in the
   transport-declared order (`audio_setup_order`), honouring MOR-556/MOR-574
   sequencing (RX-before-TX arm; TX-before-RX teardown);
4. sets `self._state = desired` (or a typed failure state) and emits an
   `AudioSessionEvent`.

Because `_converge()` reconciles against the *observed transport*, it is
**idempotent and recovery-safe by construction**: after a `soft_reconnect`
rebuilds the LAN stream to `RECEIVING`/idle, `reestablish` becomes a one-liner
— `await self._converge()` — and the TX-only, RX-only, RX_TX, and IDLE cases
all fall out of the same code path. No `TX_ONLY` special branch is needed
anywhere because TX-only is just `_desired()` returning `TX_ONLY` and
`_converge()` arming the TX leg.

### 4.3 Where the MOR-556 ordering rule moves

Today the "don't arm TX before RX on the shared stream" rule is enforced by
*suppressing TX-intent until a push* — that suppression is the root cause.
Under the clean design the rule is enforced where it belongs: as **ordering
inside `_converge()`**. When `desired == RX_TX` and RX is not yet live,
`_converge()` arms RX first, then TX (it already does this in `_enter_rx_tx`,
`session.py:520-523`). The *deferral* semantics callers relied on
(`acquire_tx` at IDLE arming nothing) are preserved naturally: at IDLE with a
single TX lease and no RX, `desired == TX_ONLY` — but for a transport where
co-arming is required before frames flow, convergence may legitimately arm TX
immediately (the digital path *wants* this; that is the bug fix). For
exclusive USB, `_desired()`/`_converge()` keep deferring per capability. The
"lazy on push" timing is no longer needed for correctness; if a deliberate
delay until first frame is still desired as an *optimisation*, it becomes a
single explicit `defer_tx_until_first_push` capability flag consumed by
`_converge()`, not six scattered guards.

### 4.4 PTT / TX-frame-arriving as first-class TX-intent

To make I1 unconditionally true and to make the failure *representable in
tests*, model the TX-demand signal explicitly:

- **`TxLease` held ⇒ TX intent** (already the demand counter; just stop
  latching it out of `_desired()`).
- Optionally surface **"TX frame arriving"** / **"CAT PTT asserted"** as an
  idempotent demand pulse: a push that finds the transport not `TRANSMITTING`
  while a lease is held calls `_converge()` (which arms it) instead of relying
  on a state-specific guard. This guarantees `push` **converges, never
  rejects** — the second half of the clean fix. The transport's
  `Cannot push TX in state receiving` becomes unreachable from a session that
  holds a lease.

This also gives the simulator a dimension to model (§5).

### 4.5 Scope, risk, boundary

- **Files:** `audio/session.py` (the reconciler rewrite); `reestablish`
  collapses to a `_converge()` call; `_ensure_tx_for_push` is deleted (its job
  moves into `push → _converge`). No transport (`lan_stream.py`,
  `usb_driver.py`) change required — they are already correct.
- **LOC:** net **negative** in `session.py` (six arming branches → one
  reconciler). This is a refactor, not a feature.
- **Risk:** medium. The sequencing rules (MOR-556 rx-first, MOR-574
  tx-down-before-rx, MOR-559 atomic order) are load-bearing and live-validated;
  they must be preserved *inside* `_converge()` with tests asserting the exact
  call order per `audio_setup_order`. Mitigation: the existing order tests
  (`test_rx_first_order_honored_on_lan_graph`,
  `test_atomic_order_honored_on_exclusive_graph`,
  `test_teardown_stops_tx_before_rx`) become the regression harness for the
  reconciler; add the convergence/invariant tests of §5 on top.
- **Boundary:** **open-core.** Generic LAN/USB audio TX state machine and
  recovery. No licensing/commercial logic. The companion (`rigplane-pro`)
  consumes this via the radio-owned `audio_session` singleton; its bridge/web
  TX handlers are unaffected by the refactor (the public demand API —
  `subscribe_rx`/`acquire_tx`/`TxLease.push` — is unchanged).
- **Sequencing vs the diagnostic patch:** the shipped patch can stay as the
  immediate stop-gap; Option B then *replaces* the three `TX_ONLY` branches
  (lazy-arm, reconcile, reestablish) with the single reconciler and keeps the
  patch's regression test (it passes unchanged against the reconciler).

### 4.6 Relationship to `RadioSessionLifecycle`

- **Do not fold** `AudioSession` state into `LifecycleState` (Option A
  rejected): different cadence, different owner (MOR-579), different blast
  radius.
- **Do mirror** the lifecycle's three tenets in `AudioSession`: (1) desired
  state is a pure function of intent; (2) every transition converges; (3)
  every transition emits an observable event (`AudioSessionEvent` already
  exists — extend listeners to cover TX-leg arm/disarm and convergence, not
  just RX liveness).
- **Do keep the existing drive edge:** lifecycle `RECOVERING → CONNECTED`
  already calls `soft_reconnect → _ensure_audio_transport → recover →
  AudioSession.reestablish`. Under Option B that terminal call becomes
  `_converge()` and the two FSMs compose cleanly: the lifecycle owns *when the
  transport exists*; the session owns *what legs are armed on it*. One
  observable event stream per layer, correlated by the recovery edge.

---

## 5. Test strategy for the CLASS

Goal: assert the **invariants**, not enumerate edges. These catch the entire
desync class, including the next edge nobody has written yet.

### 5.1 Invariant / property tests

- **I1 — intent ⇒ armed.** For every reachable session state, *holding a
  `TxLease` and issuing a push on a full-duplex transport leaves the transport
  `TRANSMITTING` and the push succeeds.* Property test over a generated
  sequence of demand operations; after any prefix, a push with a held lease
  must not raise `AudioNotStartedError`.
- **I2 — convergence.** *After every public operation,
  `session.state == session._desired()`* (modulo the explicit transient
  `RECOVERING`), *and the transport leg liveness matches the state.* Assert as
  a post-condition helper invoked after each step of every test.
- **I3 — no phantom RX.** TX-only convergence/recovery never resurrects RX
  subscribers (`bus.subscriber_count == 0`) — already asserted by the new
  patch test; promote it to the invariant helper.
- **I4 — teardown order.** `stop_rx` is never called while the transport is
  `TRANSMITTING` (MOR-574), across *all* transitions — extend
  `test_teardown_stops_tx_before_rx` to run after a recovery, not only from a
  clean RX_TX.

### 5.2 The mode-switch / recovery × TX matrix

Parametrise a single test over the cross-product:

```
prior_demand ∈ {TX_ONLY, RX_ONLY, RX_TX, IDLE}
   × event   ∈ {mode_change, soft_reconnect/reestablish, transport_rebuild,
                drop_RX_then_push, drop_TX, PTT_assert}
   ⇒ assert I1 ∧ I2 ∧ I3 ∧ I4 hold, and a post-event push (if a lease is held)
     reaches the radio.
```

The reported bug is the single cell `(TX_ONLY, reestablish) → push`. The
matrix turns "the one pair nobody composed" into "every pair is asserted".

### 5.3 What the simulator/fixtures must model

- **Enforce the transport push rule in the double.** The LAN test double must
  reject `push_tx` unless `start_tx` was the last arming call since teardown
  (mirror `lan_stream.py:622`). Without this, session↔transport desync is
  invisible — the exact reason the bug escaped. (Some doubles like the
  `_strict_fake` already model AUHAL -50; extend the LAN double similarly for
  the RECEIVING/TRANSMITTING rule.)
- **Model transport rebuild on reconnect.** A `reestablish`/recovery fixture
  must reset the transport to `RECEIVING`/idle (the `_wipe(radio)` helper
  already does this in `test_audio_recovery_session.py`) so re-arm is actually
  exercised, not no-op'd against a still-armed double.
- **Add a PTT/TX-frame dimension.** Represent "CAT PTT asserted" / "TX frame
  arriving" as an explicit fixture input so the failing interaction (PTT on,
  TX leg unarmed) is *representable* and can be asserted against I1.

---

## 6. Recommendation

1. **Keep** the diagnostic agent's patch (`ad7737ce`) as the immediate
   field stop-gap and **keep its regression test** — it is correct and
   ships the fix the operator needs now.
2. **Adopt Option B** as the proper fix in a follow-up: make `AudioSession` a
   *real* declarative reconciler — `_desired()` pure in `(rx_demand,
   tx_demand, recovery)` with TX-intent first-class (drop the `TX_ONLY` latch),
   route every edge through one idempotent `_converge()` that reconciles
   against the **observed transport**, delete `_ensure_tx_for_push` and the
   three scattered `TX_ONLY` arming branches, and make `push` **converge, not
   reject**. Net-negative LOC; open-core; medium risk concentrated in
   preserving the live-validated arming order.
3. **Do not** fold audio-session state into `RadioSessionLifecycle` (Option A).
   Instead **mirror its pattern** (intent→desired→converge + observable
   events) at the audio layer and keep the existing
   `RECOVERING→reestablish→_converge` drive edge. One owner per layer, two
   FSMs, correlated by the recovery edge.
4. **Add the invariant/property tests of §5** *before or with* Option B so the
   reconciler is locked behind I1–I4 and the recovery×TX matrix — converting a
   hand-maintained edge-consistency contract into an enforced one.

---

## 7. Open Questions (for the user)

1. **Sequencing of the two fixes:** ship Option B as a dedicated refactor PR
   on top of the merged stop-gap, or roll the stop-gap into Option B before
   merge? (Recommendation: stop-gap first — it is already committed — then
   Option B as a clean, test-locked refactor.)
2. **Lazy-arm-on-push as policy:** is deferring the TX leg until the first
   frame still *wanted* (latency/wake-on-demand) once correctness no longer
   depends on it, or should a held lease arm TX eagerly on full-duplex
   transports? This decides whether `_converge()` carries a
   `defer_tx_until_first_push` capability flag or arms unconditionally.
3. **PTT as a session input:** should "CAT PTT asserted" become a real
   first-class session input (so the session can *know* TX is keyed even
   before audio frames arrive), or is "lease held + frame arriving" a
   sufficient proxy? The former is more correct and more testable but widens
   the session's contract.
4. **Exclusive-USB TX-only:** confirm the intended behaviour for
   `(0 RX, >0 TX)` on `exclusive`/`atomic` USB transports — keep deferring
   (today's behaviour) or define a real TX-only mode there too? The reconciler
   needs this row of `_desired()` pinned down per transport capability.
5. **MOR-609 (session-owned retry/FAILED loop):** Option B makes the
   reconciler the natural home for a retry loop. Should MOR-609 be folded into
   the Option B refactor, or stay a separate follow-up? (It is the *seventh*
   arming edge that Option B would otherwise have to anticipate.)
