# AudioSession reconciler refactor — executable, test-first implementation plan (Option B)

**Date:** 2026-06-22
**Status:** approved-design implementation plan (read-only on production code; builder agent executes)
**Scope:** `rigplane-core` — `src/rigplane/audio/session.py` only (+ test files)
**Boundary:** **open-core.** Generic LAN/USB audio TX state machine and recovery. No licensing/commercial/customer logic. The public demand API (`subscribe_rx` / `acquire_tx` / `TxLease.push` / `reestablish`) is unchanged.
**Primary input:** `docs/architecture/2026-06-22-audio-session-state-architecture.md` (the architectural verdict — read it first).
**Decision:** Option B (FIXED). Do not relitigate Options A or C.

---

## A. Plain-language restatement (the thermostat)

Make `AudioSession` a real reconciler. **Desired state is a pure function of declared intent** — `_desired(rx_demand, tx_demand, recovery)` reads only the demand counters, never its own `self._state`; holding a `TxLease` *is* TX demand, so `(0 RX, >0 TX)` always means `TX_ONLY` on a full-duplex transport. **Every edge** (acquire, release, subscribe, push, mode-change, recovery/`reestablish`) computes desired, then calls **one idempotent `_converge()`** that drives the transport to that desired state, diffing against the **observed transport leg liveness** (not a remembered `self._state` that can lie after a transport rebuild). `push` **converges** the session to the armed state instead of **rejecting** when TX is demanded but not yet armed. The six scattered arming branches collapse into this single owner; the live-hardware-validated MOR-556/559/574 arming order moves *inside* `_converge()` unchanged.

---

## B. Target design, concretely

### B.1 The new `_desired()` — pure, inputs explicit, no self-state read

Replace the current `_desired()` (`session.py:407-426`, including the `self._state is TX_ONLY` latch at line 421) with a pure function over the demand counters plus a per-transport capability flag. It MUST NOT read `self._state`.

```python
def _desired(self) -> AudioSessionState:
    """Desired session state — a PURE function of declared demand.

    Inputs (all read from demand counters, NEVER from self._state):
      rx = self.rx_demand  (len(self._rx_subs))
      tx = self.tx_demand  (len(self._tx_leases))
      full_duplex = self._setup_order() == "rx_first"

    | rx | tx | full_duplex | desired  |
    |----|----|-------------|----------|
    |  0 |  0 |  any        | IDLE     |
    | >0 |  0 |  any        | RX_ONLY  |
    | >0 | >0 |  any        | RX_TX    |
    |  0 | >0 |  True       | TX_ONLY  |   <- was IDLE-unless-latched (the bug)
    |  0 | >0 |  False      | IDLE     |   <- exclusive/atomic USB keeps deferring
    """
    rx = bool(self._rx_subs)
    tx = bool(self._tx_leases)
    if rx and tx:
        return AudioSessionState.RX_TX
    if rx:
        return AudioSessionState.RX_ONLY
    if tx and self._setup_order() == "rx_first":
        return AudioSessionState.TX_ONLY
    return AudioSessionState.IDLE
```

**The single behavioural change:** `(0 RX, >0 TX)` on a `rx_first` transport now returns `TX_ONLY` **unconditionally** — the `self._state is TX_ONLY` latch is deleted. TX-intent is first-class: a held `TxLease` is TX demand. The exclusive/atomic row (`(0 RX, >0 TX) → IDLE`) preserves today's "exclusive USB defers tx-only" behaviour (Open Question Q4 resolution: keep deferring; do NOT define a TX-only mode for exclusive transports in this refactor).

`RECOVERING` and `FAILED` are **never returned by `_desired()`** — they are transient/transport-owned overlays handled exactly as today (see B.6).

### B.2 The new `_converge()` — idempotent, reconciles against observed transport

`_converge()` becomes the **only** function that calls `start_rx` / `stop_rx` / `start_tx` / `stop_tx` / `restart_rx`. Called under the lock by every demand mutation, by `push`, and by `reestablish`.

It diffs `_desired()` against the **observed transport leg liveness**, not against `self._state`. Observed liveness:

- **RX leg live** ⇔ `self._bus.rx_active` (the bus is the single source of truth for the RX leg, already used by `_arm_rx` and `reestablish`).
- **TX leg live** ⇔ `self._tx_leg_live()` — a new tiny observer that reads the transport's observed TX state. Because the production transport (`IcomAudioStream`, `lan_stream.py:78`) and every test double expose a coarse state, read it via a duck-typed helper:
  ```python
  def _tx_leg_live(self) -> bool:
      # Observed transport TX state, never self._state. Works against the
      # production stream (AudioState.TRANSMITTING) and the test doubles
      # (LanLikeRadio.state == "transmitting", ExclusiveUsbRadio.tx_running).
      st = getattr(self._radio, "state", None)
      if st is not None:
          return str(getattr(st, "value", st)).lower() == "transmitting"
      return bool(getattr(self._radio, "tx_running", False))
  ```
  This makes convergence recovery-safe by construction: after `soft_reconnect` rebuilds the LAN stream to RECEIVING/idle, `_tx_leg_live()` correctly reports `False`, so `_converge()` re-arms TX even though `self._state` may still say `TX_ONLY`.

Shape:

```python
async def _converge(self, *, closing_rx: RxSubscription | None = None) -> None:
    """Drive the transport to _desired(). Idempotent; the SOLE arming site.

    Reconciles against OBSERVED transport leg liveness (bus.rx_active and
    _tx_leg_live()), never against self._state — so it is correct after a
    transport rebuild (reestablish) and after any prior partial transition.
    Preserves the MOR-556/559/574 ordering via _setup_order() (see B.3).
    """
    desired = self._desired()
    order = self._setup_order()

    if desired is AudioSessionState.IDLE:
        # Teardown: TX down BEFORE RX (MOR-574), then drop RX subs.
        if self._tx_leg_live():
            await self._disarm_tx()
        await self._drop_rx(closing_rx)
        self._state = AudioSessionState.IDLE

    elif desired is AudioSessionState.TX_ONLY:
        # Full-duplex, TX demand, no RX. Shed any RX first (TX-down then
        # RX-drop if a TX leg is up — MOR-574), then arm TX alone.
        if self._rx_subs or closing_rx is not None:
            if self._tx_leg_live():
                await self._disarm_tx()
            await self._drop_rx(closing_rx)
        await self._ensure_tx_live()           # idempotent start_tx
        self._state = AudioSessionState.TX_ONLY

    elif desired is AudioSessionState.RX_ONLY:
        if self._tx_leg_live():
            await self._disarm_tx()             # MOR-574: TX down before RX work
            if order in _REARM_RX_AFTER_TX_DROP:
                await self._bus.restart_rx()
        else:
            await self._arm_rx()                # may raise → caller unwinds
        self._state = AudioSessionState.RX_ONLY

    else:  # RX_TX
        await self._enter_rx_tx(order)          # the preserved ordering, B.3
        self._state = AudioSessionState.RX_TX

    self._emit_convergence(desired)             # observability hook, B.5
```

Helper `_ensure_tx_live()` wraps the `start_tx` + `AudioAlreadyStartedError` swallow used today in three places:

```python
async def _ensure_tx_live(self) -> None:
    if self._tx_leg_live():
        return
    try:
        await self._radio.start_tx()
    except AudioAlreadyStartedError:
        logger.debug("audio-session: TX already live on converge", exc_info=True)
```

`_arm_rx`, `_disarm_tx`, `_drop_rx`, `_enter_rx_tx` are **kept** (they already encode ordering and unwinding correctly). `_converge()` is the new single caller of them on the demand path.

### B.3 Where the MOR-556/559/574 ordering moves — exact quote and destination

The ordering is **load-bearing and live-hardware-validated** (IC-7610 / FTX-1). It is NOT changed — it moves verbatim into `_enter_rx_tx` (already its home) and the teardown branch of `_converge()`.

**Current `RX_TX` entry order — `_enter_rx_tx` (`session.py:500-561`), preserved exactly:**

- `TX_ONLY → RX_TX` (full-duplex, TX up, RX arriving): `stop_tx` → `start_rx` → `start_tx` (`session.py:510-513`).
- `IDLE → RX_TX`, `rx_first`: `start_rx` → `start_tx` (`session.py:520-523`).
- `IDLE → RX_TX`, `tx_first`/`atomic`: `start_tx` → `start_rx`, unwind `stop_tx` on RX failure (`session.py:531-546`).
- `RX_ONLY → RX_TX`, `rx_first`: `start_tx` (RX already up) (`session.py:548`).
- `RX_ONLY → RX_TX`, `tx_first`/`atomic`: `stop_rx` → `start_tx` → `restart_rx` (the MOR-559 live-validated order) (`session.py:556-560`).

**Current teardown order — `_reconcile` IDLE branch (`session.py:494-498`), preserved:** `_disarm_tx` (TX down BEFORE RX drops — the MOR-574 lesson) → `_drop_rx`.

**Where it goes:** `_enter_rx_tx` keeps its body **unchanged** except its signature drops the `current: AudioSessionState` parameter and instead derives the entry sub-case from **observed** liveness (`self._bus.rx_active`, `self._tx_leg_live()`) so it is correct after a rebuild. The mapping is mechanical:

| Old `current` discriminator | New observed-liveness discriminator |
|---|---|
| `current is TX_ONLY` | `self._tx_leg_live() and not self._bus.rx_active` |
| `current is IDLE` | `not self._tx_leg_live() and not self._bus.rx_active` |
| `current is RX_ONLY` (rx_first) | `self._bus.rx_active and not self._tx_leg_live()` (order `rx_first`) |
| `current is RX_ONLY` (atomic) | `self._bus.rx_active and not self._tx_leg_live()` (order != `rx_first`) |

The actual `start_*`/`stop_*` call sequences inside each branch are **copied without modification**. This is the highest-risk edit (Risk §E); the order-assertion tests (`test_rx_first_order_honored_on_lan_graph`, `test_atomic_order_honored_on_exclusive_graph`, `test_atomic_order_no_minus_50_on_strict_fake`, `test_teardown_stops_tx_before_rx`) are the regression harness and MUST stay green unchanged.

### B.4 The TX-intent input surface

TX-intent is modelled as **the `TxLease` demand counter, made first-class in `_desired()`** (drop the latch). No new field is required for the core fix. Concretely:

- **Lease acquire** (`acquire_tx`, `session.py:327`): already appends to `self._tx_leases`. This IS the "TX intended" signal. Keep it; route through `_converge()`.
- **Frame arrival / push** (`TxLease.push`, `session.py:193`): a push with a held, unreleased lease that finds the transport not transmitting calls `_converge()` (which arms TX) instead of `_ensure_tx_for_push`'s IDLE-only guard. This makes "TX frame arriving" an idempotent demand pulse that **converges, never rejects**.
- **CAT-PTT path:** in `rigplane-core`, CAT PTT is asserted by the radio control layer, not by `AudioSession`; the session learns TX intent through the lease the companion bridge / web TX handler holds while PTT is asserted. **Full CAT-PTT plumbing into the session is OUT OF SCOPE for this refactor** (it is a larger cross-layer change and would widen the session contract — Open Question Q3). The minimal clean input surface that satisfies decision #3 is: **the lease carries TX-intent, and `push` converges.** This makes I1 unconditionally true and makes the failing interaction representable in tests (B-test §D.6) without a new production field. If a future issue wants the session to *know* PTT is keyed before any audio frame arrives, add an explicit `note_tx_intent()` pulse that calls `_converge()` — designed-for but not built here.

### B.5 Code to DELETE and what each edge collapses into

| Deleted / collapsed | Lines | Collapses into |
|---|---|---|
| `_ensure_tx_for_push` (whole method) | `session.py:348-376` | `push` → `_converge()` (B.6 push) |
| `_desired()` `TX_ONLY` self-state latch | `session.py:421-422` | pure `_desired()` (B.1) |
| `_reconcile` `TX_ONLY` demand-edge branch | `session.py:473-493` | `_converge()` `TX_ONLY` branch (B.2) |
| `reestablish` `TX_ONLY` branch (the band-aid `ad7737ce`) | `session.py:624-640` | `reestablish` → `_converge()` (B.6) |
| `reestablish` bespoke RX/TX re-arm body | `session.py:641-668` | `reestablish` → `_converge()` (B.6) |
| `_reconcile` method (replaced) | `session.py:446-498` | `_converge()` (B.2) |
| `_enter_rx_tx(order, current)` signature | `session.py:500-501` | `_enter_rx_tx(order)` reading observed liveness (B.3) |
| `_try_rearm_tx` | `session.py:670-685` | folded into `_ensure_tx_live()` (B.2) — best-effort TX is now convergence-internal |

`_apply()` (`session.py:438-444`) is **renamed/retargeted** to call `_converge()` instead of `_reconcile()` (it keeps its `_recovering_from` reset + `_sync_watchdog()` finally wrapper). Demand mutators (`subscribe_rx`, `acquire_tx`, `_release_rx`, `_release_tx`) keep calling `self._apply(...)`; only the body it dispatches to changes.

**Net effect:** six arming decision sites (`_ensure_tx_for_push`, `_reconcile`'s TX_ONLY branch, `_enter_rx_tx`'s TX_ONLY re-arm, `reestablish`'s TX_ONLY branch, `reestablish`'s RX/TX body, `_try_rearm_tx`) → **one** (`_converge`, which delegates RX_TX ordering to `_enter_rx_tx`).

### B.6 How each edge becomes "compute desired → converge"

- **`acquire_tx`** (`session.py:327`): append lease → `await self._apply()` (→ `_converge`). With `(0 RX, >0 TX)` on full-duplex this now **eagerly arms TX** (decision #2 — eager arming; no wait for first push). Failure unwinds the lease (keep the existing try/except unwind).
- **`subscribe_rx` / `_release_rx` / `_release_tx`**: unchanged call sites; `_apply` → `_converge`. `_release_rx` still passes `closing_rx=sub` so TX-down-before-RX ordering holds (MOR-574).
- **`push`** (`TxLease.push`, `session.py:193`): replace `await self._session._ensure_tx_for_push()` with `await self._session._converge_for_push()`:
  ```python
  async def _converge_for_push(self) -> None:
      async with self._lock:
          if self._tx_leases and not self._tx_leg_live():
              await self._converge()        # arms TX; converges, never rejects
  ```
  Then `await self._radio.push_tx(...)` as today. (Guard `self._tx_leases` so a push from a fully-released session still hits the transport's own `AudioNotStartedError` rather than silently arming.)
- **`reestablish`** (`session.py:599`): collapses to:
  ```python
  async def reestablish(self) -> None:
      async with self._lock:
          self._recovering_from = None
          await self._converge()
          self._rx_armed_at = self._monotonic()
          await self._sync_watchdog()
          if self._rx_subs and not self._bus.rx_active:
              raise RuntimeError("radio RX failed to re-establish after reconnect ...")
  ```
  TX_ONLY, RX_ONLY, RX_TX, IDLE recovery all fall out of `_converge()` reconciling against the rebuilt (RECEIVING/idle) transport. The `RuntimeError` on demanded-but-dead RX is preserved so the recovery caller can surface FAILED (matching `_audio_recovery.py:155` expectation). TX-only recovery never raises (no RX demanded) — exactly the band-aid's behaviour, now structural.
- **mode-change**: a mode change is just a demand reshape (the companion drops/adds RX subs or TX leases) followed by `_converge()`. No special path. The SSB→FT8 excursion is `RX_TX → (drop RX) → TX_ONLY` then a recovery `reestablish → _converge`, all one code path.
- **recovery (`RECOVERING` watchdog)**: unchanged — see B.7.

### B.7 The two-state-machine correlation (session FSM vs transport IDLE/RECEIVING/TRANSMITTING)

The desync class is killed because `_converge()` reads **observed transport leg liveness** (`bus.rx_active`, `_tx_leg_live()`) as its diff baseline, then drives the transport and sets `self._state = desired` only after the transport calls succeed. The session FSM can therefore never claim a state the transport does not back:

- After any `_converge()`, `self._state == _desired()` AND the transport legs match it (Invariant I2).
- `RECOVERING` remains a transient watchdog-owned overlay (`_check_liveness_locked`, `session.py:714-732`) that shadows the live transport: demand transitions during RECOVERING act on `_recovering_from` (keep the existing `effective`-state shim by feeding `_recovering_from` into `_converge` when `self._state is RECOVERING` — the watchdog re-detects silence after any demand edge). On resume, `self._state = self._desired()` (already correct since `_desired()` is now pure).
- `FAILED` stays defined-but-unentered (MOR-609 follow-up; out of scope, Q5).

---

## C. TDD step sequence (RED → GREEN, with gates)

Each step writes the test(s) first, confirms RED on current `main`, then makes the production change GREEN. Gate after every step:
`uv run pytest tests/test_audio_session.py tests/test_audio_recovery_session.py tests/test_web_audio_tx_session.py -q` plus `uv run ruff check src/rigplane/audio/session.py`.

> All new tests live in the existing files; no new test module is created unless a property harness needs `tests/test_audio_session_invariants.py` (Step 1).

**Step 0 — Strengthen the test double (no production change).**
RED-enabling, not a behaviour test. Confirm `LanLikeRadio.push_tx` already rejects unless `state == "transmitting"` (it does, `_order_sensitive_radios.py:89-91`) and `start_rx` rejects from non-idle (it does, `:68-69`). Add the **missing** teeth: `LanLikeRadio.start_tx` is currently lenient (flips state). Verify the double models "push after a transport rebuild with no re-arm rejects" by adding a helper assertion used across the matrix (D.5). Gate: full audio suite still green (no behaviour change yet).

**Step 1 — Invariant harness + I1/I2 as failing tests (RED on `main`).**
Write `tests/test_audio_session_invariants.py` with a parametrized enumeration harness (D.1/D.2). The cell `(TX_ONLY, reestablish) → push` and `(RX_TX → drop RX) → push` MUST be RED on current code. Gate: these specific cells fail; rest of suite green.

**Step 2 — Pure `_desired()` (drop the latch).**
GREEN target: I2-for-TX_ONLY and `test_acquire_tx_at_idle_defers_arming` **changes** (see D.5 — this test's expectation flips to eager arm). Implement B.1. This alone makes `acquire_tx` at IDLE on full-duplex return `TX_ONLY` from `_desired()`; without `_converge` yet, wire `_reconcile`'s existing `TX_ONLY` branch to handle it. Confirm order tests still green.

**Step 3 — Introduce `_converge()` + `_tx_leg_live()` + `_ensure_tx_live()`; retarget `_apply`.**
Replace `_reconcile` with `_converge` (B.2), retarget `_enter_rx_tx` to observed liveness (B.3). GREEN: all existing demand/order/teardown/refcount tests, plus I2 for every reachable state. Gate: full audio suite.

**Step 4 — `push` converges; delete `_ensure_tx_for_push`.**
Implement `_converge_for_push` (B.6). GREEN: I1 (push with held lease from every reachable state never raises `AudioNotStartedError`), and the `(RX_TX→drop RX)→push` and `(any non-IDLE, lease held)→push` cells.

**Step 5 — `reestablish` collapses to `_converge`; delete the band-aid branch + bespoke body + `_try_rearm_tx`.**
Implement B.6 `reestablish`. GREEN: the `ad7737ce` seed test (now asserting the invariant, D.7), the recovery×TX matrix (D.3), and all existing recovery tests. Confirm `_audio_recovery.py` routing (`recover → reestablish`) still passes its desync tests.

**Step 6 — Observability + eager-arm + PTT-input-surface tests.**
Add D.6 (eager arm), D.6 (lease-carries-intent), and the convergence-event assertions (B.5 `_emit_convergence` — extend `AudioSessionEvent` emission to TX-leg arm/disarm; additive, no schema change). GREEN: full suite.

**Step 7 — Cleanup gate.**
`uv run pytest -m "not slow"` (catch exact-dict payload tests, per repo memory), `uv run ruff check . && uv run ruff format --check .`, then `cargo`-free (Python-only change). Confirm net-negative LOC in `session.py`.

---

## D. Test matrix (the ironclad part)

### D.1 Invariant I1 — "TX lease/intent held ⇒ TX leg armed" (property/parametrized)

Across **all** desired states × **all** entry edges: after any prefix of demand operations that leaves a held `TxLease`, a `lease.push(...)` on a full-duplex transport leaves the transport TRANSMITTING and the push does NOT raise `AudioNotStartedError`.

- Entry edges enumerated: `acquire_tx@IDLE`, `acquire_tx@RX_ONLY`, `push@TX_ONLY`, `subscribe_rx then drop`, `reestablish@TX_ONLY`, `reestablish@RX_TX`, `mode_change(drop RX while lease held)`.
- Implementation: parametrized enumeration over the operation prefix (see D.4 — no hypothesis). Assert via a shared `assert_push_succeeds(session, lease, radio)` helper.

### D.2 Invariant I2 — "any transition converges to `_desired()`" (property)

From every reachable session state, applying any single public input (`subscribe_rx`, `release rx`, `acquire_tx`, `release tx`, `push`, `reestablish`) drives `session.state == session._desired()` (modulo transient `RECOVERING`) AND transport leg liveness matches:
- `session.state in {RX_ONLY, RX_TX}` ⇒ `bus.rx_active is True`;
- `session.state in {TX_ONLY, RX_TX}` ⇒ `_tx_leg_live(radio) is True`;
- `session.state is IDLE` ⇒ both False.

Implemented as a `assert_converged(session, radio)` post-condition helper invoked after **every** step of every matrix test (D.3) and the enumeration harness (D.4).

Also assert **I3 (no phantom RX)**: TX-only convergence/recovery keeps `bus.subscriber_count == 0` (promote the band-aid assertion to the helper). And **I4 (teardown order)**: `stop_rx` is never called from a TRANSMITTING transport across all transitions — extend `test_teardown_stops_tx_before_rx` to also run **after a recovery** (`_RecordingLanRadio` + `_wipe` + `reestablish`), not only from a clean RX_TX.

### D.3 The composition matrix the old suite lacked

Parametrize one test over `prior_demand × event`:

```
prior_demand ∈ {IDLE, RX_ONLY, RX_TX, TX_ONLY}
   × event   ∈ {reestablish (transport rebuilt via _wipe), drop_RX_then_push,
                drop_TX, mode_change, transport_rebuild}
   ⇒ assert_converged(session, radio) AND, if a lease is held, a post-event
     push reaches the radio (I1).
```

- **The exact bug cell:** `(TX_ONLY, reestablish) → push` — RED on `main`, GREEN after Step 5.
- **Symmetric cells explicitly required:** `(RX_TX, reestablish)`, `(RX_ONLY, reestablish)`, `(IDLE, reestablish)` (demand dropped during outage stays dropped).
- Build `TX_ONLY` prior state via `acquire_tx` (eager-arm) OR `acquire_tx + push` — both must reach `TX_ONLY`.

### D.4 Property-based testing: enumeration, not hypothesis

`hypothesis` is **not** a dev dependency (confirmed: absent from `pyproject.toml` and the test tree). **Recommendation: do NOT add it for this refactor.** The state space is tiny and bounded (4 demand shapes × ~7 edges × small operation prefixes); a deterministic **parametrized enumeration** over `itertools.product` of operation sequences (length ≤ 4 drawn from `{sub_rx, rel_rx, acq_tx, rel_tx, push, reestablish}`) gives full coverage, deterministic CI, and no new dependency. Implement I1/I2 as such an enumeration in `tests/test_audio_session_invariants.py`, asserting `assert_converged` after each step and that any held-lease `push` succeeds. (If a future, larger audio FSM warrants it, adding `hypothesis` is a separate decision — note it, do not act on it here.)

### D.5 Test-double upgrade (make desync detectable)

The LAN double **already** enforces the core push rule (`push_tx` rejects unless `state == "transmitting"`, `_order_sensitive_radios.py:89-91`; `start_rx` rejects from non-idle, `:68-69`). The real gap is **two-fold**:

1. **A reachable "rebuilt-then-no-rearm" desync must reject.** The `_wipe(radio)` helper (`test_audio_recovery_session.py:44-50`) already resets `state="idle"`/`rx_callback=None`. Combined with the existing `push_tx` guard, a push after `_wipe` with no re-arm **already** rejects — confirm this in Step 0 with an explicit assertion so the harness can detect session↔transport desync. No double behaviour change is strictly required for LAN; the teeth already exist. **Action:** add a module-level `assert_push_rejects_when_not_armed(radio)` sanity test pinning that contract so a future lenient edit to the double is caught.
2. **`_tx_leg_live()` must read the double the same way for LAN and exclusive.** `LanLikeRadio` exposes `state == "transmitting"`; `ExclusiveUsbRadio`/`_AtomicExclusiveRadio` expose `tx_running`. The `_tx_leg_live()` helper (B.2) handles both. Add a one-line assertion per double that `_tx_leg_live` agrees with the double's own flag, so the observer can never silently diverge.

**Existing tests that MUST still pass unchanged against the (already-strict) double:** all of `test_rx_first_order_honored_on_lan_graph`, `test_atomic_order_honored_on_exclusive_graph`, `test_atomic_order_no_minus_50_on_strict_fake`, `test_teardown_stops_tx_before_rx`, `test_double_*_refcounted`, `test_*_start_failure_*`, `test_digital_tx_no_rx_subscriber_pushes_without_error`.

**One existing test changes behaviour (call it out in the PR):** `test_acquire_tx_at_idle_defers_arming` (`test_audio_session.py:234-245`) currently asserts `radio.calls == []` after `acquire_tx` at IDLE (deferral). Under **eager arming (decision #2)**, `acquire_tx` at IDLE on a `rx_first` transport now arms TX immediately → state `TX_ONLY`, `radio.calls == ["start_tx"]`. **Update this test** to assert the eager-arm contract (renamed `test_acquire_tx_at_idle_eager_arms_tx_only`), and keep a companion assertion that on an **exclusive/atomic** transport `acquire_tx` at IDLE still defers (`radio.calls == []`, state `IDLE`) — the `_desired()` `(0 RX, >0 TX, not full_duplex) → IDLE` row. This is the only intentional behavioural-contract change to an existing test and is the crux of the MOR-556 ordering preservation review.

### D.6 Eager-arm and PTT-first-class-input tests

- **Eager arm:** `acquire_tx@IDLE` on `LanLikeRadio` → `TX_ONLY`, `_tx_leg_live()` True, **before any push** (decision #2). Then a push succeeds without an additional `start_tx`.
- **Lease carries TX-intent:** assert that with a held lease and a `_wipe`'d transport, `reestablish` re-arms TX (`_tx_leg_live()` True) with `bus.subscriber_count == 0` — the lease alone is sufficient TX-intent (decision #3, minimal surface).
- **Push converges (PTT proxy):** drive `RX_TX`, drop the RX sub (→ `TX_ONLY`), then `_wipe` (rebuild), then `push` — the push must converge (arm TX) and succeed, asserting I1 without any CAT-PTT field.

### D.7 Regression test from the `ad7737ce` seed — asserting the INVARIANT

Keep `test_reestablish_rearms_tx_only_digital_session` but **re-anchor its assertions on the invariant, not the branch**: after `_wipe` + `reestablish`, assert `assert_converged(session, radio)` (which subsumes `state is TX_ONLY`, `_tx_leg_live()` True, `bus.subscriber_count == 0`) and that the previously-failing `lease.push(...)` now succeeds (I1). It must pass unchanged against the reconciler (the architectural analysis §4.5 promises this), proving the band-aid's scenario is covered structurally.

---

## E. Risk

**Highest risk: the MOR-556/559/574 arming order.** It is live-hardware-validated on the IC-7610 (LAN `rx_first`) and FTX-1 (exclusive `atomic`). The refactor moves it verbatim into `_enter_rx_tx`/`_converge` (B.3) and changes only the **discriminator** (observed liveness instead of remembered `self._state`), never the call sequences.

Mitigation:
- The four order-assertion tests (`test_rx_first_order_honored_on_lan_graph`, `test_atomic_order_honored_on_exclusive_graph`, `test_atomic_order_no_minus_50_on_strict_fake`, `test_teardown_stops_tx_before_rx`) are the regression harness and MUST stay green **unchanged**. If any requires editing, treat it as a red flag that the order moved — stop and review.
- I4 (no `stop_rx` from TRANSMITTING) is asserted across **all** transitions including post-recovery, closing the gap that the old suite only checked from a clean RX_TX.

**Required live-hardware re-test before release:** the SSB ↔ FT8 TX excursion on a direct-LAN IC-7610 (the exact field repro) and a same-device exclusive USB radio (FTX-1) — confirm modulation reaches the radio (Po > 0 W) after a recovery cycle in both `rx_first` and `atomic` orderings. Mark the PR as requiring this human validation before merge to a release branch.

**Secondary risk:** `_tx_leg_live()` duck-typing. It must agree with the production `IcomAudioStream.state` (`AudioState.TRANSMITTING`) and both doubles. Pinned by D.5 assertions.

**Open questions (with concrete recommendations — none left dangling):**
- **Q1 (sequencing the two fixes):** The band-aid `ad7737ce` is on an orphan branch and NOT to be shipped. **Recommendation:** ship Option B directly; do not merge the band-aid first. Keep only its test (re-anchored, D.7).
- **Q2 (lazy-arm-on-push as policy):** **Recommendation:** eager arm on full-duplex (decision #2 — FIXED). Do NOT add a `defer_tx_until_first_push` flag in this refactor; it is YAGNI and reintroduces a conditional. If wake-on-demand latency ever matters, a single capability flag consumed by `_converge()` is the future home — designed-for, not built.
- **Q3 (CAT-PTT as a session input):** **Recommendation:** out of scope. The held lease + push-converges is a sufficient, minimal, testable TX-intent surface. A first-class `note_tx_intent()` pulse is designed-for (B.4) but deferred to a follow-up issue to avoid widening the session contract beyond the reconciler.
- **Q4 (exclusive-USB TX-only):** **Recommendation:** keep deferring — `_desired()` maps `(0 RX, >0 TX, not full_duplex) → IDLE` (B.1), preserving today's behaviour. No TX-only mode for exclusive transports in this refactor.
- **Q5 (MOR-609 retry/FAILED loop):** **Recommendation:** keep separate. `FAILED` stays defined-but-unentered. The reconciler is the natural future home (it would be the "seventh edge" Option B otherwise anticipates), but folding it in now exceeds scope/guardrails.

---

## F. Scope / guardrails

**Files the implementation will touch:**
1. `src/rigplane/audio/session.py` — the reconciler rewrite (production).
2. `tests/test_audio_session.py` — update `test_acquire_tx_at_idle_defers_arming` (eager-arm contract change), add eager-arm/I-helper tests.
3. `tests/test_audio_recovery_session.py` — re-anchor the `ad7737ce` seed test on the invariant; add recovery×TX matrix.
4. `tests/test_audio_session_invariants.py` — **new** I1/I2 enumeration harness (the only new file).
5. `tests/_order_sensitive_radios.py` — additive sanity assertions only (the strict push rule already exists); no behaviour change.

That is 1 production file + 4 test files. The **production guardrail (≤3 files / ≤200 LOC delta)** is met: production change is **a single file with net-negative LOC** (six arming branches + `_try_rearm_tx` + `_ensure_tx_for_push` + bespoke `reestablish` body deleted; one `_converge` + two small helpers added). Test files are not counted against the production-file guardrail but are listed for completeness.

**LOC estimate:** production `session.py` ≈ **−40 to −70 net** (delete ~120 lines of scattered arming across `_ensure_tx_for_push`, `_reconcile` TX_ONLY branch, `reestablish` body, `_try_rearm_tx`; add ~55 lines of `_converge` + `_tx_leg_live` + `_ensure_tx_live` + `_converge_for_push`). Tests ≈ **+150 to +200** (the invariant harness + matrix). Net-negative production LOC is the goal and is achieved.

**Boundary confirmation:** **open-core**, stays in `rigplane-core`. Generic LAN/USB audio TX state machine and recovery. No licensing, commercial, customer, or desktop-packaging logic. The public demand API is byte-for-byte unchanged, so `rigplane-pro` (companion bridge / web TX handler, consuming `radio.audio_session`) needs no change.

**No transport change:** `lan_stream.py` / `usb_driver.py` are already correct (the transport accepts `start_tx` from RECEIVING and rejects `push_tx` unless TRANSMITTING — exactly the contract `_converge` reconciles against). Do not edit them.
