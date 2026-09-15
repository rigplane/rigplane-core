# Mechanism audit — command-execution capability (four consumers, one radio)

Method file read: `.claude/skills/mechanism-audit/SKILL.md` (this run's contract; steps 0–5, dead-code sweep, steelman, verdict taxonomy, fixed report fields). Audited revision: `e3d435dc3b70ff1ed07bd8efce3e5e78a7bda46c` (2026-09-15).

Scope note: control-value unit conversion (display↔raw) is out of scope per contract; it is mentioned only where inseparable from a command-path finding (F7, and the rigctld RFPOWER clamp observation inside F5).

---

## Deletions

### D1 — `_FallbackRigState` fields and methods: dead
Verdict:          dead
Elements:         `src/rigplane/rigctld/handler.py: _FallbackRigState` (class at :425). Dead members: methods `update_freq` (:450), `update_ptt` (:463), `is_fresh` (:444); fields `freq`, `freq_ts`, `ptt`, `ptt_ts`, `s_meter`, `s_meter_ts`, `rf_power`, `rf_power_ts`, `swr`, `swr_ts`, `mode`, `mode_ts`, `filter_width` (all :427–442). Live remainder: `data_mode`/`data_mode_ts` + `update_data_mode` (written handler.py:1865, read handler.py:1457) and the writers `update_s_meter`/`update_rf_power`/`update_swr` (called `rigctld/routing.py: YaesuRouting.get_level` :236/:247/:252 — methods live, the fields they write are not).
Consumers:        production — none for the dead members (observation); tests only — `tests/test_rigctld_handler.py:890` reads `_cache.mode`; `tests/test_rigctld_routable_extension.py:18,58,70` constructs the class.
Written / read:   established by `grep -o "self\._[a-zA-Z_0-9]*" <file> | sort -u` + per-name counts (radio_poller 108 attrs, control.py 63, handler.py 69, server.py — **0 write-only attributes in all four**, sweep count reported as required); then method-level: `grep -rn "\.update_freq\|\.update_ptt" src/ tests/` → 0 hits for this class (hits are the `core/_state_cache.py: StateCache` homonym); `grep -rn "\._cache\.(mode|ptt|freq|s_meter|rf_power|swr|filter_width)" src/ tests/` → 1 hit, the test above; `is_fresh` → 0 callers anywhere.
Guards checked:   dynamic access — the only dynamic access is inside `is_fresh` itself (`getattr(self, f"{field}_ts")`, handler.py:446), searched literally; out-of-repo — open-core, a pro-repo import is possible but the class is underscore-private and not exported (`rigctld/__init__.py: __all__ = ["RigctldServer"]`); public API — no; tests-only — yes, two test sites (deleting `update_mode`'s assertion at test_rigctld_handler.py:890 and the constructor use in test_rigctld_routable_extension.py are human decisions).
Collateral:       doc references `core/radio_protocol.py` (:985) and `backends/yaesu_cat/radio.py` (:3121) cite the class by name (prose only); `routing.py` TYPE_CHECKING import.
Depends on:       none
Confidence:       high for the five never-called/written-never-read field groups; medium for `mode`/`filter_width` (one test reader).
Falsifier:        a caller of `_FallbackRigState.is_fresh`/`update_freq`/`update_ptt`, or a reader of `ptt`/`s_meter`/`rf_power`/`swr` (e.g. in rigplane-pro or via `getattr` string built elsewhere).
Fix class:        delete (shrink the class to `data_mode` or replace with a one-field local; `YaesuRouting` then stops writing meter fields nobody reads — that write side becomes dead in turn).

No other deletion candidates survived the sweep; `rigctld/state_cache.py` is a compat re-export of `core/_state_cache.py: StateCache` consumed by 5 test files — public-importable, open-core guard → **undetermined**, not reported deletable.

## Consolidations

### F1 — per-command dispatch (command name → radio method + per-command semantics): three large parallel dispatch tables beside a 12-entry shared registry
Verdict:          A (displacement) with an in-flight shared target
Rank:             diverged
Elements:         `web/handlers/control.py: ControlHandler._enqueue_rc_*` family (9 handlers, 116 `case "` arms — builds legacy `Command` dataclasses); `web/radio_poller.py: RadioPoller._execute_unlocked` (120 `case` arms, ~2,100 lines: validation + radio calls + compat state mirror + events); `rigctld/handler.py: _RigctldCommandExecutor.execute` (:607, if/elif by `intent.name` over ~14 names); nascent shared table `core/command_dispatch.py: _COMMAND_DESCRIPTORS` (:297–415, **12 descriptors**: levels, antennas, tuner, repeater_shift) consumed via `execute_command_intent` (:614).
Consumers:        web control arms — web only; poller arms — web only (plus `yaesu_cat/poller.py` has its own smaller seat, see F3); rigctld executor — rigctld only; descriptor registry — web `ControlHandler._enqueue_command`/`_execute_intent`, rigctld `_execute_managed_tuner`, poller `canonicalize_level_command` (`runtime/_poller_types.py:1003`).
Definition site:  shared primitive exists: `core/command_dispatch.py` (descriptor, `prepare_command_intent`, `bind_command_intent`, `enqueue_command_intent`, `execute_command_intent`) and `core/command_service.py: CommandService.execute` (:180).
Divergence:       observable — same input, different outcome: `select_vfo`/`set_vfo` on a Yaesu dual-RX rig is **refused by web** (`web/radio_poller.py: _execute` SelectVfo arm, :2947–2952 raises `CommandError("…no MAIN/SUB select code")` before consulting the backend) and **works via rigctld** (`handler.py: _execute_set_vfo` :2389 calls `select_receiver("SUB")`); recorded as MOR-1671 (In Progress) and GH #2633 (closed NOT_PLANNED) in `docs/plans/2026-08-29-v3-defect-inventory.md` (:270–345). Also: poller arms carry web-only semantics (VFO-switch-and-restore dance around `SetFreq`/`SetMode`, :2296–2324) that no other consumer gets — CLI/rigctld writes to SUB on FTX-1 behave differently from web MAIN writes.
Prior ruling:     "Add a high-level Radio method → declare on the Capability Protocol in `core.radio_protocol` first… Web / rigctld surfaces consume via `isinstance`" — `runtime/LAYER.md` Common operations; "MOR-2161: add descriptor-driven command dispatch reference path" landed 2026-09-02 (git log, `core/command_dispatch.py`); `web/LAYER.md`: web "does not depend on backends… detects optional features via isinstance" (charter places protocol dispatch below web).
In-flight:        migration incomplete — `core/command_dispatch.py` exists, consumed by web ingress (12 names), rigctld tuner path, and level canonicalization; ~230 `_COMMANDS` names (control.py:343–537) still route through the legacy dataclass path.
Required surface: exists (`CommandDescriptor` registry + `CommandService`); needs growth to cover the remaining ~220 names and relocation of the poller arms' semantics into runtime Radio methods.
Depends on:       F2 (receiver validation must sit in runtime before arms move), F6 (compat-mirror retirement).
Confidence:       high
Falsifier:        a ruling that the legacy `Command`-dataclass path is the permanent design (none found; MOR-2161 moves the other way).
Fix class:        consolidate (extend descriptors; move arm semantics into runtime; consumers keep protocol parsing only).
Actionable:       yes, incrementally per command family; expensive — treat as multi-change programme, not one PR.

### F2 — receiver/VFO validation: four mechanisms, one of them a byte-level copy of the runtime original
Verdict:          A
Rank:             diverged (via F1's MOR-1671; on its own: parallel)
Elements:         `runtime/_dual_rx_runtime.py: DualRxRuntimeMixin._require_receiver` (:46, raises `CommandError`, profile-driven); `web/radio_poller.py: RadioPoller._ensure_receiver_supported` (:1223 — **identical body and message**, observation); `web/handlers/control.py: ControlHandler._ensure_receiver_supported` (:751 — different: capability-sniff fallback, raises `ValueError`); `rigctld/handler.py: _resolve_target_vfo`/`_receiver_index_for` (:2309/:2290 — hamlib VFO-name space, raises `ValueError`→`EVFO`); `backends/yaesu_cat/radio.py: _check_single_receiver` (:1998, raises `ValueError`).
Consumers:        runtime seat — all runtime methods; poller copy — poller `_execute` arms; control seat — web ingress; rigctld pair — every rigctld `_*` handler; Yaesu seat — backend methods.
Definition site:  runtime (`_dual_rx_runtime.py`) is the original; web poller's is a copy (observation: identical strings).
Divergence:       exception type differs per seat (`CommandError` vs `ValueError`) — mapped differently to each consumer's error protocol; the MOR-1671 refusal itself is F1's guard (`vfo_main_code is None`), a *profile-code* check the runtime seat does not perform.
Prior ruling:     none found specific to receiver validation duplication; `runtime/LAYER.md` "Add a high-level Radio method" implies the runtime seat owns it.
In-flight:        none for the copies.
Required surface: exists (runtime `_require_receiver`); web/rigctld seats should call it (or the descriptor bind should).
Depends on:       none
Confidence:       high
Falsifier:        a ruling that web must pre-validate before enqueue for latency (the queue drain already re-validates in `_execute_unlocked`, so the copy adds no protection the drain lacks — inference).
Fix class:        consolidate (delete the web copies; keep rigctld's VFO-name mapping as protocol-local parsing that then calls the runtime seat).
Actionable:       yes — small, mechanical; best folded into F1 per command family.

### F3 — TX-write policy seat (refuse/order writes while transmitting): shared decision core, deliberately divergent per-seat outcomes, consolidation ruled and pending
Verdict:          C today (recorded divergence), A in ownership (2026-09-01 ADR rules the target)
Rank:             diverged (deliberate)
Elements:         shared core: `runtime/tx_interlock.py` (`evaluate_tx_interlock` :357, `classify_tx_interlock` :335, `DeferredTxCommandLane` :139, `get_tx_interlock_command_family_metadata` :272) + `core/tx_interlock_contract.py`; seats: `web/radio_poller.py: RadioPoller._enforce_tx_interlock` (:652) + `_stage_tx_interlocked_entries` (:731); `rigctld/handler.py: _classify_rigctld_tx_intent` (:308), `_defer_write_gate` (:942), executor BLOCK check (:621); `backends/yaesu_cat/poller.py` (imports the same primitives, :45–48).
Consumers:        web seat — web writes; rigctld seat — rigctld writes (only when `_has_canonical_state_store`, fail-open otherwise, handler.py:990); yaesu poller seat — backend-internal; CLI and validation harness — no seat at all (observation: zero `tx_interlock` references in `cli/`, `validation/`).
Divergence:       same input (a DEFER-class write while RF=TX) → web: `TxInterlockRefusal` → WS `radio_transmitting` error; rigctld: silent drop + `RPRT 0`. Recorded deliberate ruling: "Web's seat can refuse with a reason envelope… a third-party hamlib client on this wire protocol does not" — `handler.py: _defer_write_gate` docstring, MOR-1881 owner ruling 2026-08-17, superseding PR #2755's lane design (measured +2.003 s same-connection PTT-OFF delay).
Prior ruling:     `docs/plans/2026-09-01-runtime-transmit-authority.md` — "**Status:** Accepted architecture; implementation pending"; `runtime/managed_tx_authority.py: ManagedTxAuthority` is designated "the sole stateful owner" and `core/tx_safety.py: TxSafetySupervisor` "may be replaced or reduced to pure policy"; MOR-1884 pins the web seat at the head of `_execute` (radio_poller.py:2164–2168).
In-flight:        yes — the ADR; `ManagedTxAuthority` already exists and is consumed by web, rigctld, both backend pollers, and CLI/validation via `core/radio_protocol.py: ManagedTxApi.bind` (:646).
Required surface: exists (`ManagedTxApi` protocol facade, `runtime/managed_tx_composition.py` composition root); ADR rows pending (per-method admission pins INV-2).
Depends on:       none (proceeds independently of F1).
Confidence:       high
Falsifier:        the ADR being reverted — it is dated, accepted, and replaces the 2026-08-20 design.
Fix class:        none beyond executing the accepted ADR (this audit does not re-litigate it).
Actionable:       no new design needed — report stands as "migration incomplete: authority exists at runtime, consumed by all four; interlock seats remain per-consumer until ADR rows land".

### F4 — serialization / mutual exclusion (commands vs polling): shared queue in runtime, second mechanism in rigctld, none in CLI/validation
Verdict:          C for standalone consumers; already-shared for the queue primitive
Rank:             parallel (deliberate)
Elements:         `runtime/_poller_types.py: CommandQueue` (:1260, coalescing/dedup/PTT ordering, session liveness, connection-generation binding, currency validation :1128 `validate_command_queue_entry_currency`), drained solely by `web/radio_poller.py: RadioPoller._run` (:1715 `take_entry`); `rigctld/handler.py: _RIGCTLD_PREDECESSOR` ContextVar (:95) serialized per-connection inside `_RigctldCommandExecutor._wait_predecessor` (:490); web-local pacing/coalescing at `ControlHandler._handle_command` (:1079, MOR-1427/MOR-1499); `_vfo_command_lock` (radio_poller.py:601).
Consumers:        queue — web; embedded rigctld uses `queue.put_ordered` only for managed PTT/tuner (handler.py:541/:595) and `server.py: enqueue_managed_positive_tx`; standalone rigctld (`cli/__init__.py:3731` `RigctldServer(radio, config)` — no queue arg) uses predecessor-only; CLI one-shots and validation RMVR are strictly sequential.
Divergence:       none observable by design intent — MOR-1881 notes rigctld writes deliberately avoid the correlated queue (2 s in-band delay); CLI/validation have no concurrency to exclude (inference from lifecycle: process-per-command, sequential awaits).
Prior ruling:     MOR-1881 (above); `runtime/LAYER.md` charter already claims "command queue runtime" — the queue's location is correct per charter.
In-flight:        none.
Required surface: exists.
Depends on:       none
Confidence:       high
Falsifier:        a concurrency defect reproducible only on standalone rigctld (two TCP connections racing a write) — none found in docs; predecessor chain serializes per connection but cross-connection standalone ordering relies on the transport layer (unknown — flag, do not assert).
Fix class:        none
Actionable:       no — correctly located; standalone divergence is lifecycle-legitimate.

### F5 — value validation (range/lattice/type) on the command path: no single seat; runtime itself is internally inconsistent
Verdict:          B (gap in the runtime seat), with shared math already emerging
Rank:             parallel
Elements:         `runtime/radio.py: IcomRadio.set_rf_gain` (:2368, `0–255` + `_require_capability`), `set_af_level` (:2400, same), `set_squelch` (:2423, same) **vs** `set_rf_power` (:2326 — **no range check, no capability check**, observation) and `set_cw_pitch` (:2588 — none; the encoder `commands/levels.py: set_cw_pitch` :423 raises `ValueError` 300–900); `profiles/control_domain.py: validate_control_raw_value` (:67, lattice) consumed by `backends/yaesu_cat/radio.py` + `commands/command_map.py`; consumer-side: `web/handlers/control.py: _consume_normalized_level_unit` (:307, 0.0–1.0), `_level_for_power` (:265, delegates to shared `core/command_service.py: resolve_power_level_target` :933); `rigctld/handler.py: _execute_set_level` (:2708 — `RFPOWER` `round(value*255)` **unclamped**, only `_SET_LEVEL_FLOAT` clamps, observation); CLI inline per subcommand (`_cmd_power` :2856–2869 checks 0–255 and CAP_POWER_CONTROL itself).
Consumers:        all four; the runtime methods are the only seat every consumer passes through.
Divergence:       out-of-range power via rigctld (`L RFPOWER 1.5`) reaches the encoder unclamped → encoder-level error mapped `EIO`-ish vs web `command_failed` vs CLI pre-checked exit 1 (inference; wire-encoder behavior at >255 is out of scope to verify).
Prior ruling:     "Domain legality… is CoreRadio.set_filter_shape's job — the single validation seat, not a hardcoded 0/1 duplicate here (MOR-1534…)" — `web/radio_poller.py: SetFilterShape` arm comment (:2462–2467); the MOR-1534 precedent names runtime as the single seat.
In-flight:        `profiles/control_domain.py` (2026-09-15, HEAD commit) — the lattice owner, Yaesu CW pitch already migrated.
Required surface: runtime method entry checks (or descriptor bind validation) as the single seat; consumers keep only type/shape parsing.
Depends on:       none
Confidence:       high on the `set_rf_power` asymmetry (direct code observation); medium on the end-to-end divergence.
Falsifier:        a ruling that encoders (`commands/`) are the designated single seat — the `set_rf_gain`/`set_af_level` runtime checks would then be the duplicates to remove.
Fix class:        design-lite (pick the seat: runtime entry, per MOR-1534 precedent; make `set_rf_power` match its siblings).
Actionable:       yes, small: add the missing checks in `runtime/radio.py: set_rf_power` (+ clamp in `_execute_set_level` RFPOWER branch) independent of F1.

### F6 — state update after command: canonical pipeline exists; three optimistic/pending mirrors coexist
Verdict:          A → migration incomplete
Rank:             parallel
Elements:         canonical: `core/command_service.py: CommandService` pending overlays + readback expectations (`_record_intent_overlay` :677, `_request_write_confirmation` :255) into `core/state_store.py: StateStore`; `web/radio_poller.py` compat mirror writes (`_execute_unlocked` arms: `self._radio_state.receiver("SUB").freq = freq` :2321, `_apply_compatibility_mirror` :2596+) explicitly marked "Compatibility mirror until web state delivery reads StateStore" (:2317); `rigctld/handler.py: _PendingRigState` (:415, all four fields live via `_effective_pending_freq` :1418) + `_record_pending_overlay` (:1400); runtime's own `radio_state` mirror (`runtime/radio.py: set_freq` :2320).
Consumers:        CommandService — web, rigctld, yaesu poller, rigctld_client backend, sync facade (grep counts: radio_poller 43, server 28, rigctld handler 19, control 17, yaesu poller 16, sync 15, rigctld_client 9, cli 1); mirrors — per-seat.
Divergence:       none observable by contract (mirrors feed legacy readers); risk is drift — the mirrors are second writers beside the store (inference; MOR-895/pro#1200, cited at handler.py:1749–1761, records exactly this class of bug: a rigctld read path writing display semantics back clobbered canonical fields).
Prior ruling:     `docs/plans/2026-08-14-state-backed-command-lifecycle.md` (the CommandService programme); `docs/internals/ui-statestore-authority-contract.md` exists.
In-flight:        yes — mirrors are the documented transitional state.
Required surface: exists.
Depends on:       F1 (arms carry the mirror writes; they retire together).
Confidence:       high
Falsifier:        none plausible — the code comments themselves declare the migration intent.
Fix class:        consolidate (finish the migration; delete mirrors).
Actionable:       yes, but sequenced after F1 per command family.

### F7 — capability check ("does this radio support the operation"): protocol surface shared, enforcement seats local and uneven
Verdict:          already-shared (primitive) / B (mandated seat missing)
Rank:             parallel
Elements:         primitive: `core/radio_protocol.py` Capability Protocols + `radio.capabilities` set; `runtime/_dual_rx_runtime.py: _require_capability` (:55); seats: `web/handlers/control.py: _ensure_capability` (:766, string-based, profile+caps merge), CLI inline (`CAP_POWER_CONTROL not in radio.capabilities`, cli :2860/:2884…), `validation/hardware.py: _write_gate`/`_capability_present` (:1468/:307, template-driven), rigctld via `RigctldRoutable` routing + ENIMPL fallbacks (`get_mode_reader`).
Consumers:        every consumer, each with its own gate; runtime enforces only on some methods (see F5).
Divergence:       a capability refused on one path and silently no-op'd on another (e.g. poller `SetFilter` arm silently does nothing when `CAP_FILTER_WIDTH` missing, radio_poller.py:2333–2336, vs `_ensure_capability` raising elsewhere — observation).
Prior ruling:     epic #1322/#1324 Capability-Protocols-over-backend-id (rigctld/LAYER.md, web/LAYER.md); MOR-1534 single-seat precedent (F5).
In-flight:        descriptor `bind` is the designed ingress seat (`core/command_dispatch.py`), 12 names so far.
Required surface: exists (descriptor bind + runtime `_require_capability`); needs to become the only seat.
Depends on:       F1.
Confidence:       high
Falsifier:        none.
Fix class:        consolidate within F1.
Actionable:       as part of F1 only.

### F8 — error mapping to consumer protocol: legitimately local, one per protocol
Verdict:          C
Rank:             none (healthy)
Elements:         `web/handlers/control.py: _send_command_failure` (:1320, exception-type → WS `error` strings); `rigctld/handler.py: _RigctldCommandFailure` (:481) → `HamlibError` → RPRT codes with truthful-terminal-state mapping MOR-1882 (`_execute_write` :1007); CLI per-subcommand `except Exception → stderr + return 1` (e.g. cli :3531); `validation/hardware.py: _guard` (:1049) → `CheckResult` + `FailureDomain` + `RmvrOutcome` (MOR-2103).
Consumers:        one seat per consumer, as required by each wire protocol.
Divergence:       by design (hamlib cannot carry web's error vocabulary — the same MOR-1881 reasoning).
Prior ruling:     MOR-1882 truthful terminal results (handler.py:1007–1074).
In-flight:        none. Required surface: none. Depends on: none.
Confidence:       high. Falsifier: none. Fix class: none. Actionable: no.

---

## Target architecture sketch

| # | Concern | Today (per consumer) | Should own | Stays local |
|---|---|---|---|---|
| 1 | Capability check | web handler+poller, CLI inline, validation template, rigctld routing; runtime only on some methods | runtime method entry + descriptor bind (`core/command_dispatch.py`) | rigctld `RigctldRoutable` mapping; validation's template gate |
| 2 | Receiver/VFO validation | runtime `_require_receiver` + web copy ×2 + rigctld VFO-space + Yaesu inline | runtime `_require_receiver` (single) | hamlib VFO-name parsing (`_resolve_target_vfo`) |
| 3 | Value validation | runtime (uneven), encoders, profiles lattice, web, rigctld, CLI inline | runtime entry + `profiles/control_domain.py` lattice | JSON/type parsing, float→raw scaling at protocol edge |
| 4 | TX safety | shared interlock core + ManagedTxAuthority; per-seat policies (web refuse, rigctld drop+RPRT 0, CLI hold/unkey, validation lease) | `runtime/managed_tx_authority.py` per 2026-09-01 ADR | per-seat *outcome presentation* (error envelope vs RPRT 0 vs exit code) |
| 5 | Serialization | runtime `CommandQueue` (web + embedded-rigctld PTT/tuner); rigctld predecessor; none in CLI/validation | runtime `CommandQueue` | rigctld per-connection predecessor; web ingress pacing/coalescing (UI-specific) |
| 6 | Connection-generation guards | queue-bound capture (web, managed rigctld); backend pollers' own counters; none in CLI/validation | runtime `CommandQueue` + `validate_command_queue_entry_currency` | nothing |
| 7 | State update | CommandService+StateStore (canonical) + web compat mirror + rigctld `_PendingRigState` + runtime `radio_state` | core `CommandService`/`StateStore` | `_PendingRigState` until store is universal; rigctld projections (read-side) |
| 8 | Error mapping | one seat per consumer | — (no single owner possible) | all of it (WS strings, RPRT codes, exit codes, CheckResult) |

Ordered steps:
1. Execute D1 (independent, cheap).
2. F5 micro-fix: `runtime/radio.py: set_rf_power` gains its siblings' range+capability checks; RFPOWER clamp in `_execute_set_level`. No dependency.
3. F2: delete web's receiver-validation copies; rigctld VFO parsing calls the runtime seat. Independent of F1's bulk.
4. F3 per the accepted 2026-09-01 ADR (already scheduled work; this audit adds nothing).
5. F1 in command-family slices (levels → filters/DSP → VFO/scope → memory/system): grow `_COMMAND_DESCRIPTORS` (12 → ~230), move `_execute_unlocked` arm semantics (VFO-switch dance, readbacks) into runtime Radio methods; fold F7 in as each family's bind becomes the seat.
6. F6: retire web compat mirrors + `_PendingRigState` per family, after 5 lands that family.

Size estimate (greps above): ~120 poller arms + 116 control arms + 14 executor names to converge on 12 existing descriptors; 4 consumer files (radio_poller.py 4,247 l., control.py 3,150 l., handler.py 3,275 l., hardware.py 2,649 l.); expensive-to-reverse items: the WS command JSON vocabulary and rigctld wire behaviour (public, cross-tested with WSJT-X/fldigi — rigctld/LAYER.md), `ManagedTxApi`/Capability Protocols (public API surface), open-core boundary unchanged (all targets are in core/runtime, already public repo layers per `docs/architecture/open-core-policy.md`).

---

**Weakest link** — D1's "fields never read" for `s_meter`/`rf_power`/`swr`: `YaesuRouting.get_level` writes them on every meter poll, and an out-of-repo or dynamic reader (none found by literal grep) would flip it to undetermined. Check first: `getattr`/`vars()`/serialization over `_cache` in rigplane-pro. Second-weakest: F5's inference that rigctld's unclamped RFPOWER reaches the encoder as >255 (wire-encoder behaviour not verified — out of scope).

**Cleared** — examined and healthy: `CommandQueue` (runtime-owned per charter, session-liveness and currency included); `ManagedTxApi`/`ManagedTxAuthority` ingress facade (core+runtime, all four consumers bind through it — `core/radio_protocol.py: ManagedTxApi.bind`); TX-interlock decision core (`runtime/tx_interlock.py`, shared by web, rigctld, yaesu poller); power-level normalization math (`core/command_service.py: resolve_power_level_target`, shared by web and intent path); `CommandService` lifecycle/overlays (core, consumed by web, rigctld, both backend pollers, sync facade); error mapping per protocol (F8, legitimately local); standalone consumers' lack of queue/interlock (lifecycle-legitimate, F4).
