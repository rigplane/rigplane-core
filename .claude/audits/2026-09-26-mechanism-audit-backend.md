# Mechanism audit — backend tract, changes merged 2026-09-26

**Method file read:** `.claude/skills/mechanism-audit/SKILL.md` (this worktree, revision `a04f7b1e51ff012ff1de0d42e876befbd06ead47`). Steps 0–5 in order, including the step-3a sweep and the step-4 steelman.

**Scope:** `git diff --name-only 9a1b90f9..HEAD -- src/` (18 files, +554/−83). Prior rulings read: `.claude/audits/2026-09-15-mechanism-audit-command-path.md`, `…control-conversion.md`, `2026-09-16-mechanism-audit-closing-mor2478.md`, `README.md`; layer map `CLAUDE.md` "Layer boundaries" + `.importlinter`; Linear MOR-2478 (owner decisions 2026-09-15, read via Linear, read-only). Revision pinned above; all citations are observations unless labelled inference.

---

## DELETIONS (ranked first)

### D1 — `web/handlers/control.py: _enqueue_rc_power` PTT arms: dead
```
Verdict:          dead
Elements:         web/handlers/control.py: _enqueue_rc_power — the "ptt" (:2682–2691),
                  "ptt_on" (:2692–2696), "ptt_off" (:2697–2701) match arms (the only
                  production constructors of queue PttOn(); see D2)
Consumers:        none reachable. `_enqueue_command` (:1674) intercepts every
                  ptt/ptt_on/ptt_off request into `_enqueue_managed_ptt` at :1682–1683
                  (`_MANAGED_PTT_COMMANDS` :692) — unconditionally, before the legacy
                  dispatch. The legacy dispatch (`_enqueue_legacy_command` :1930) is
                  reached only via `_execute_intent` :1911 ← `_ControlCommandExecutor`
                  (:228) ← CommandService.execute, and every intent producer of the
                  name "ptt" goes through `_enqueue_command` first: WS ingress (:1474),
                  HTTP batch (`server.py: _prepare_http_batch_step` calls
                  `_enqueue_command`, :4831), which intercepts too.
                  `_HttpCommandExecutor` (server.py:428) executes only
                  raw_civ_transaction/set_powerstat. runtime/sync.py uses "set_ptt",
                  never "ptt". No test reaches the arms (grep: zero `"ptt"` batch or
                  legacy-dispatch tests; tests/test_web_managed_tx_ingress.py:213–265
                  exercises the managed intercept only).
Written / read:   `grep -rn "PttOn(" src/` non-case, non-isinstance → exactly
                  control.py:2689, :2695, and rigctld/handler.py:320 (that one feeds
                  `_classify_rigctld_tx_intent`, a TX-policy classifier, not the queue).
                  Literal grep; no getattr/string-built command construction found.
Guards checked:   dynamic access — none (literal grep over src/ and tests/; the WS
                  command name arrives as a string but is matched against
                  `_MANAGED_PTT_COMMANDS` before any dispatch). out-of-repo — a Pro
                  extension driving the public WS vocabulary still passes through
                  `_enqueue_command`; only a Pro import of the private method would
                  reach the arms. public API — the arms are not importable surface;
                  the WS wire name "ptt" stays served (by the managed path). tests-only
                  — no tests found.
Collateral:       `_TX_COMMANDS` (:685) and `_COMMANDS` (:526) keep "ptt"/"ptt_on"/
                  "ptt_off" — they remain served names; do not remove the names.
Depends on:       D2 (the arms are the only feed of the poller PttOn arm; retire
                  together or explicitly keep the arm as a test seam).
Confidence:       high on unreachability from this repo
Falsifier:        any code path that hands a CommandIntent named "ptt"/"ptt_on"/
                  "ptt_off" directly to CommandService.execute bypassing
                  `_enqueue_command` (e.g. a Pro module constructing intents).
Fix class:        delete
```

### D2 — `web/radio_poller.py: RadioPoller._execute` `PttOn` branch: production-dead, tests-only
```
Verdict:          vestigial-fork (dead in production; the PttOff twin is live)
Elements:         web/radio_poller.py: _execute PttOn arm (:2420, logs "poller: PTT
                  ON"); abandoned side = the legacy queue-PTT path; live side = the
                  managed positive-TX submission path (server.py:
                  enqueue_managed_positive_tx :1420 → queue.put_ordered(None,
                  positive_tx_submission=…) :1435 → _run :826
                  execute_positive_tx_queue_entry; rigctld twin at
                  rigctld/handler.py: _execute_managed_ptt :522).
Consumers:        PttOn arm — tests only: 15 test files construct PttOn(); e.g.
                  tests/test_radio_poller_coverage.py:5658, tests/test_web_managed_tx_owner.py:197
                  call `poller._execute(PttOn())` directly. PttOff arm — live
                  (teardown unkey control.py:1622, radio_poller.py:1005; TX-safety
                  drain :1075). backends/yaesu_cat/poller.py:1233 mirrors the same
                  shape (its own PttOn arm, same unreachable feed).
Written / read:   established in D1's grep — zero production enqueuers of PttOn.
Guards checked:   dynamic access — none found (literal). out-of-repo — a Pro or
                  future consumer could re-open the queue-PTT ingress deliberately;
                  unknown. public API — `_execute` is private. tests-only — yes,
                  ~15 files; deleting the arm deletes those assertions, a human
                  decision. TX/PTT safety boundary (AGENTS.md) — the arm carries the
                  lease/audio-leg ordering the tests pin; whether the managed
                  positive-TX composition re-implements identical ordering is
                  unknown from this pass.
Collateral:       the test files above; radio_poller.py:2113 managed-PTT pre-gate.
Depends on:       D1 first (the arms and the arm retire together or by explicit
                  owner ruling).
Confidence:       medium (unreachable: high; "should delete": low — safety code)
Falsifier:        a production path that enqueues PttOn onto the shared command
                  queue (none found), or an owner ruling that the queue-PTT path is
                  the retained executor for a future ingress.
Fix class:        delete (after owner decision on the TX-safety semantics)
Actionable:       no without owner sign-off — TX/PTT safety boundary
```

This pair also answers observed fact 5: the missing `poller: PTT ON` line is **expected**, not a logging defect. All three observed key-downs (web latched TRANSMIT, WS `ptt_on`, rigctld `T 1` — rigctld/handler.py: `_execute_managed_ptt` puts `positive_tx_submission`, not PttOn) travel the managed positive-TX lane and never enter the `PttOn` arm. Observation: all three cited ingress sites read and confirmed.

### D3 — `profiles`: `break_in_labels` / `ssb_tx_bw_labels`: stored, never read — undetermined
```
Verdict:          undetermined (public-field guard), reported from the step-3a sweep
                  of the touched module rig_loader.py
Elements:         profiles/__init__.py: RadioProfile.break_in_labels (:421),
                  ssb_tx_bw_labels (:425); parsed by rig_loader.py: _parse_enumerated_domain
                  (:2521, :2546) and threaded through :844/:848 to :2864/:2868.
Consumers:        production — none (grep over src/: only the dataclass field, the
                  loader plumbing, and sibling `*_values` consumers at
                  runtime/radio.py:3204/:3437, which read the values, not the labels).
                  tests — tests/test_rig_loader.py:3421 reads break_in_labels.
Written / read:   reads outside definition/plumbing: 1 (test). Values twins are live.
Guards checked:   public API — RadioProfile is an importable public dataclass; a
                  downstream consumer (Pro, rig authors) may read label maps. Fails
                  the public-API guard → undetermined, not deletable.
Collateral:       rigs/*.toml declare the labels; the orphan-label load-time check
                  depends on them being parsed.
Depends on:       none
Confidence:       high on "no production reader in this repo"
Falsifier:        a Pro extension or rig-author tooling reading these fields.
Fix class:        none (record; owner decides publish-or-delete)
```

Sweep counts for the touched modules (instance attrs/constants/functions added by the range): transport.py (+`_conninfo_notice_callback`, `CONNINFO_NOTICE_SIZE` — both live), acquisition_scheduler.py (+`_execute_started_at`, `tx_active_hint` — live), acquisition_drain.py (+`_tx_active`, `tx_active_hint` — live), state_acquisition_policy.py (+`expires_by_time` — read by `freshness_ttl_seconds`), yaesu_cat/radio.py (+`_remembered_nb_level`/`_remembered_nr_level` — read in set_nb/set_nr), web/server.py (+`tx_active_hint`, `_bind_managed_tx_hint`, `_refresh_managed_tx_keyed`, `_read_managed_tx_keyed`, `_managed_tx_hint_authority`, `_managed_tx_keyed`, `_serialize_notch_width_choices` — all live), runtime/_civ_rx.py (+`_soft_recovery_epoch`, `request_recovery_now`, `_SILENT_OPENCLOSE_DEADLINE` — live), _control_phase.py (+`_session_reject_hint`, `_after_reconnect`, `_on_conninfo_notice` — live), rig_loader.py (+notch label check — live), validation/hardware.py (+`_read_filter_width`, `_write_filter_width`, `restorable` — live), rigctld/handler.py, dx_cluster.py, cli/__init__.py, audio/bus.py, radio_poller.py, web_startup.py, radio_protocol.py, observations.py: no write-only names added. **Zero new write-only fields beyond D1–D3.**

---

## CONSOLIDATIONS (ranked by debugging cost)

### F1 — "observed PTT or managed hint": helper written twice in `core/`
```
Verdict:          A (trivial displacement inside the owning layer — a shared helper
                  belongs once in core, beside derive_tx_active)
Rank:             parallel (identical behaviour)
Elements:         core/acquisition_drain.py: AcquisitionDrain._tx_active (:167–180);
                  core/acquisition_scheduler.py: StateFreshnessService._tx_active
                  (:2455–2474) — byte-equivalent bodies: derive_tx_active(store) or
                  bool(hint()), hint failure → debug log + False.
Consumers:        drain — _drain (:256); freshness — tick (:2454). Both web-wired
                  (radio_poller.py:3811, web_startup.py:425, server.py:828/:1127).
Definition site:  the shared primitive derive_tx_active lives in core
                  (state_pipeline_contracts); the hint combiner exists twice.
Divergence:       none today; the only difference is the store parameter (drain
                  receives it, freshness owns it).
Prior ruling:     none found for this helper. ADR docs/plans/2026-09-01-runtime-transmit-authority.md
                  designates ManagedTxAuthority the sole stateful TX owner — the hint
                  *reads* the authority, so the ADR is satisfied by both copies.
In-flight:        none.
Required surface: exists after extraction: one module-level `tx_active(store, hint)`
                  in core next to derive_tx_active.
Depends on:       none
Confidence:       high
Falsifier:        a ruling that the drain and the tick must keep independent failure
                  handling (none found; the bodies are identical today).
Fix class:        consolidate (mechanical)
Actionable:       yes — small, no design decision
```

### F2 — PTT keying: one canonical managed mechanism, one abandoned queue twin
```
Verdict:          A — displacement already resolved in the right direction; the
                  migration is incomplete only in that the abandoned side was left
                  behind (D1/D2)
Rank:             diverged (the two paths differ observably: the queue arm logs
                  "poller: PTT ON", runs the audio-leg/lease ordering inside the
                  poller; the managed lane runs admission inside ManagedTxAuthority
                  and only joins the wire result in the queue)
Elements:         canonical: runtime/managed_tx_authority + server.py:
                  enqueue_managed_positive_tx + _poller_types.py:
                  execute_positive_tx_queue_entry (:1162) — consumed by web WS
                  (control.py: _enqueue_managed_ptt), rigctld managed (handler.py:
                  _execute_managed_ptt), and the latched TRANSMIT path; abandoned:
                  control.py:2682–2701 + radio_poller.py:2420 PttOn arm.
Consumers:        canonical — all three production ingress points (observed fact 5
                  confirmed at each site); abandoned — tests only (D2).
Definition site:  runtime (ManagedTxAuthority composition) per the accepted
                  2026-09-01 ADR — correctly located below the consumers.
Divergence:       the hardware observation itself (missing log line) is the
                  observable difference; no functional defect found on the managed
                  lane in this pass.
Prior ruling:     MOR-2478 owner decision 3 + the 2026-09-01 transmit-authority ADR
                  (read in MOR-2478 description and the 2026-09-15 command-path
                  report F3): authority is the sole stateful owner; the audit does
                  not re-litigate it.
In-flight:        none — the managed lane is landed and is the only live path.
Required surface: exists.
Depends on:       D1, D2 (delete the abandoned side first; do not merge it into the
                  authority — that would carry dead behaviour into working code).
Confidence:       high on topology; the arm-vs-authority semantic equivalence is
                  unverified (see D2's unknown)
Falsifier:        a production queue-PttOn enqueuer (none found).
Fix class:        delete (via D1/D2), not consolidate
Actionable:       yes, as D1/D2 with the TX-safety caveat
```

### F3 — notch label completeness: notch-only rule beside the generic domain parser
```
Verdict:          C — legitimately local today; a named generalization condition
Rank:             parallel (trivial)
Elements:         profiles/rig_loader.py: load_rig notch-only check (:2531–2546)
                  beside _parse_enumerated_domain (orphan-*label* rule only).
Consumers:        the check guards the one consumer that indexes labels —
                  web/server.py: _serialize_notch_width_choices `labels[str(value)]`
                  (:625, KeyError at request time without the check). Sibling labels
                  (break_in, ssb_tx_bw) have no production reader (D3).
Divergence:       four domains share the generic parser; the value-has-label rule
                  applies to notch only. All current rigs declare complete maps
                  (observation: ic705/ic7300/ic7610 [notch] and [ssb_tx_bw] read).
Prior ruling:     MOR-1685 / MOR-2634 (commit messages; no layer ruling found).
In-flight:        none.
Required surface:  the rule moves into _parse_enumerated_domain the day a second
                  domain's labels gain an indexing consumer — until then the local
                  check is the cheaper, correct shape (steelman accepted).
Depends on:       D3 (if sibling labels are ever published, the rule generalizes
                  together with that publication).
Confidence:       high
Falsifier:        a sibling label consumer appearing (then the split becomes
                  displacement).
Fix class:        none now; consolidate later under the named condition
Actionable:       no
```

### F4 — LAN-session recovery: one layered mechanism, new trigger correctly reuses it
```
Verdict:          already-shared / correctly located
Rank:             none (healthy)
Elements:         runtime/_civ_rx.py: _civ_data_watchdog (detector, now
                  quiet-vs-stalled with _SILENT_OPENCLOSE_DEADLINE), request_recovery_now
                  (:1535, new entry from the 0x90 busy=0 notice), _watchdog_recover
                  (:1560, one-shot escalation to full reconnect via
                  _soft_recovery_epoch); runtime/_control_phase.py: _on_conninfo_notice
                  (:941), soft_reconnect + the new shared tail _after_reconnect
                  (:866 — a consolidation, both soft-reconnect arms now share
                  managed-TX/audio re-arm); core/transport.py: connect per-session
                  reset (:214–226); escalation path `remote_id = 0` reuses
                  IcomTransport.reconnect's documented discovery fallback.
Consumers:        each layer's own; the new conninfo trigger enters the *existing*
                  recovery entry (_watchdog_recover), not a second recovery system.
                  No pre-existing transport reset helper was bypassed (grep:
                  send_seq/rx_missing/_tx_guards cleared only in rollover and
                  gap-reset paths, which are different invariants).
Divergence:       none found; the escalation flag resets on any CI-V data
                  (:1730), the epoch guard prevents repeat escalation per session.
Prior ruling:     MOR-2626/2627/2618 commit messages; no conflicting audit ruling.
Confidence:       high. Falsifier: a second, independent reconnect driver racing
                  _watchdog_recover (the new live/done guard at :1519 and
                  request_recovery_now's own guard close exactly that).
Fix class:        none
```

---

**Weakest link** — D1/D2's unreachability verdict. It rests on the exhaustiveness of one literal sweep (`PttOn(` constructors) plus the control-flow argument that `_enqueue_command`'s `_MANAGED_PTT_COMMANDS` intercept precedes every legacy dispatch. Check first: any Pro extension or out-of-repo module constructing a `CommandIntent` named "ptt" and calling a `CommandService` directly; second, whether an owner ruling intentionally keeps the queue-PttOn arm as the executor for a planned ingress (then D1 alone stands and D2 becomes in-flight, not dead).

**Cleared** — examined and healthy: the transport per-session reset (core/transport.py: connect — no duplicate reset mechanism; reconnect deliberately preserves counters); `_serialize_notch_width_choices` (web/server.py — one publication helper, the two call sites are the standard `_serve_info`/`_serve_capabilities` pair, frontend consumes `notchWidthChoices` at SemanticRadioSurfaces.svelte:694, rigctld publishes none); Yaesu remembered NB/NR levels (backends/yaesu_cat/radio.py — backend-local state for CAT switch-doubles-as-level semantics, correct layer; the set_nb/set_nr twin shape is pre-existing and was extended symmetrically); `tx_active_hint` plumbing (web cache reads the authority, consistent with the 2026-09-01 ADR; F1 covers the one real duplication); `note_execute_started(now=)` credit window (acquisition_scheduler.py — extends the single MOR-2594 seat); `expires_by_time`/None TTL (state_acquisition_policy.py — single policy seat; demotion lands on MENU which is also non-expiring); disconnect provider-generation invalidation (web/server.py:5410 — the store's designed API, each lifecycle site owns its own trigger, matching web_startup.py:72/:378/:541 and rigctld/server.py:834); DX-cluster connect timeout (dx_cluster.py); CLI second-SIGTERM ignore (cli/__init__.py); audio-bus CancelledError re-raise removal (audio/bus.py, MOR-2620 semantics); rigctld ERJCTED mapping (handler.py — error mapping stays per-protocol per the 2026-09-15 command-path F8 ruling); validation SKIP for unreadable filter width (validation/hardware.py — the `None` gate precedes the RMVR cycle; note, inference, medium confidence: `yaesu_cat/poller.py:1428` and `observations.py:514` now pass a possible `None` into the legacy mirror/observation without a visible None guard — worth a follow-up read, not a finding on this pass).

*Scratch notes: this file only. No edits, no git writes, no test runs, no GitHub/Linear writes.*
