> **Point-in-time audit snapshot (2026-08-30).** Produced by the `auditor`
> subagent (Claude Opus) following `.claude/skills/mechanism-audit/SKILL.md`.
> Tree audited: `8c8a70d4` (merged to main via #2801). Every citation in this
> document — including the file:line forms used where no symbol encloses the
> evidence — is frozen at that revision; resolve against `8c8a70d4`, not HEAD.
> This file is an archived report, not maintained documentation. Owner rulings
> recorded 2026-08-30 (see the Linear ticket package referencing this audit)
> supersede any recommendation here where they differ — in particular: the
> nested state-feedback descriptors are ruled deletable (Q3 verification,
> 2026-08-30), and `delta_since` deletion is ruled pre-release (Q4).

# Mechanism audit — state / control-feedback path

**Method:** `.claude/skills/mechanism-audit/SKILL.md`, read from `/Users/moroz/Projects/rigplane-core/.claude/skills/mechanism-audit/SKILL.md` (tracked, present in the working tree). Followed as written, with the dispatch's citation override (file + symbol, not file:line).

**Tree audited:** `8c8a70d4` (`8c8a70d435a2b94bef7bb8dce1c549dfe3798b05`), branch `codex/mechanism-audit-skill`, tracking `origin/codex/mechanism-audit-skill`. `git status --short --branch` reported a **clean** tree. No writes of any kind were made to it; scratch scripts live in the session scratchpad.

**Hypothesis handling:** the dispatch supplied three audit *questions* and a known-ticketed exclusion list, not a conclusion. The one place it came close — "check panels/layouts for logic that belongs lower" — I treated as the thing under test; §F4 and the Cleared list record where the steelman won.

**Search disclosure:** every count below comes from a **literal** `grep` over `src/`, `tests/`, `frontend/src/`, `docs/` (excluding `node_modules/` and the generated `site/`). Literal search misses `getattr`/string-built names; where a deletion is proposed I ran the specific dynamic-access check and say so.

---

## Deletions

### D1 — `RadioPoller.state_is_fresh` / `mark_polled` / `_last_polled` / `_DEFAULT_POLL_FIELD_TTL`: dead

**Verdict:** dead
**Elements:** `/Users/moroz/Projects/rigplane-core/src/rigplane/web/radio_poller.py` — `RadioPoller.mark_polled`, `RadioPoller.state_is_fresh`, the `self._last_polled: dict[str, float]` slot, and the module constant `_DEFAULT_POLL_FIELD_TTL: float = 0.2` (a bare constant with no enclosing symbol; it is used only as `state_is_fresh`'s default argument).
**Consumers:** none.
**Written / read:** `mark_polled` — 4 call sites, all in `radio_poller.py` (inside `_execute`'s freq/mode paths and the BSR readback path); `state_is_fresh` — **0 call sites anywhere**; `_last_polled` — written by `mark_polled`, read only by `state_is_fresh`. Established with `grep -rn "state_is_fresh\|mark_polled" --include="*.py" src/ tests/`: 6 hits in `src/` (2 definitions + 4 writes), **0 in `tests/`**.
**Guards checked:** dynamic access — searched literally, plus `grep` for `getattr(...poller`/`getattr(self, ` in `src/rigplane/web/`: the only `getattr` hits are `_uses_fallback_provider_generation`, `_zombie_reaper_task`, `_radio_model`, `_radio_poller`; none constructs `state_is_fresh`/`mark_polled` dynamically. Out-of-repo — `rigplane.web.radio_poller.RadioPoller` is not re-exported from `rigplane/__init__.py` and is absent from `docs/api/public-api-surface.md`; the RigPlane Pro seam per `CLAUDE.md` is the Radio protocol + `local-extensions/`, and there is no `local-extensions/` directory in this tree — **unknown** whether a private tier subclasses this poller. Public API — not exported. Tests-only — no, zero tests either.
**Collateral:** none — no test asserts either symbol.
**Depends on:** none.
**Confidence:** high (observation).
**Falsifier:** a Pro-tier or downstream subclass calling `state_is_fresh`; a reflective call built from a string.
**Fix class:** delete.

**Why this matters beyond tidiness (inference):** the *appearance* of a third backend freshness mechanism — poll-cadence TTL alongside `StateStore.FreshnessState` and `StateCache.is_fresh` — dissolves once this is read as write-only. `docs/internals/legacy-state-writer-inventory.md` sanctions it as `executor_cache_keep` with the constraint "Do not use poll cadence markers as delivery freshness for Web consumers". Nothing does, because nothing reads them at all. Deleting first is exactly the method's ordering point.

---

### D2 — `frontend/src/components-v2/layout/layout-utils.ts`: whole module dead

**Verdict:** dead
**Elements:** `layout-utils.ts` — `extractVfoState`, `extractMeterState`, `hasLiveAudioFromState` (all three exports).
**Consumers:** tests only. `extractVfoState` + `extractMeterState` + `hasLiveAudioFromState` are imported by `frontend/src/components-v2/layout/__tests__/RadioLayout.isolated.test.ts` and nothing else; one prose mention in `keyboard-map.ts` (a comment, not an import).
**Written / read:** `grep -rn "extractMeterState" frontend/src/` → 1 definition + 9 test lines, 0 production imports. Same shape for the other two.
**Guards checked:** dynamic access — searched literally; there is no dynamic component/helper registry in the frontend (`grep -rln "import(.*Panel\|components: {"` over `frontend/src/` excluding tests returned nothing). Out-of-repo — none, this is app-internal Svelte source. Public API — no. **Tests-only — yes, flag it**: deleting the module means deleting the three `describe` blocks in `RadioLayout.isolated.test.ts`.
**Collateral:** those test blocks; also `frontend/src/components-v2/panels/MeterPanel.svelte` (see D3), which `extractMeterState`'s docstring names as its consumer.
**Depends on:** none (independent of D3, but they are the same cluster).
**Confidence:** high (observation).
**Falsifier:** a production import I missed under a re-export alias — I searched the symbol names, not just the module path.
**Fix class:** delete.

**Note (observation, on-scope for "per-surface derivation"):** `extractMeterState` fabricates defaults — `radioState?.main?.sValue ?? radioState?.main?.sMeter ?? 0` — the exact pattern `frontend/src/lib/runtime/props/__tests__/panel-props.no-fabricated-defaults.test.ts` exists to forbid on the live path. It is dead, so it harms nothing today; it would be a trap for anyone who revived it.

---

### D3 — `frontend/src/components-v2/panels/MeterPanel.svelte`: dead component

**Verdict:** dead
**Elements:** `MeterPanel.svelte` (the whole component).
**Consumers:** its own isolated test only (`components-v2/panels/__tests__/MeterPanel.isolated.test.ts` mounts it). One incidental mention in `components-v2/meters/__tests__/BarGauge.test.ts` calling it "BarGauge's sole non-compact consumer".
**Written / read:** `grep -rn "MeterPanel"` over `frontend/src/`, excluding `MetersDockPanel`/`DockMeterPanel` and the file itself: no `<MeterPanel` element anywhere, no non-test import.
**Guards checked:** dynamic access — searched literally; no dynamic Svelte component registry exists (same check as D2). Out-of-repo — none. Public API — no. **Tests-only — yes, flag it.**
**Collateral:** `MeterPanel.isolated.test.ts`; and BarGauge's non-compact rendering path loses its only claimed consumer, which is a fact worth telling whoever owns `BarGauge`.
**Depends on:** none. D2's `extractMeterState` was built for this component; taking both together is cleaner than either alone.
**Confidence:** high (observation).
**Falsifier:** a skin mounting it through a path my symbol search missed.
**Fix class:** delete.

---

### D4 — `web/runtime_helpers.build_public_state_payload`: no production consumer

**Verdict:** vestigial fork — the abandoned side of the two payload front-doors.
**Elements:** `/Users/moroz/Projects/rigplane-core/src/rigplane/web/runtime_helpers.py` — `build_public_state_payload` (the `RadioState`-dataclass front door). Its live twin is `build_public_state_payload_from_snapshot`; both delegate to the shared `_build_public_state_payload_from_dict`, which stays.
**Consumers:** tests only — `tests/test_web_runtime_helpers.py` (1 call site) and `tests/web/test_state_schema_conformance.py` (3 call sites). Production: **zero**. The live path is `web/server.py`'s `_build_public_state_payload_from_snapshot_impl` → `build_public_state_payload_from_snapshot`.
**Written / read:** repo-wide literal `grep -rn "build_public_state_payload"` across `*.py|*.md|*.ts|*.svelte|*.toml|*.yml|*.sh` (minus `node_modules`, `site/`): the snapshot variant has 1 production call site plus `scripts/gen_field_status_fixture.py` and ~60 test lines; the dataclass variant has 0 production call sites.
**Guards checked:** dynamic access — searched literally; `grep` for `getattr(...runtime_helpers` / `getattr(rh` returned nothing. Out-of-repo — the symbol is in `runtime_helpers.__all__`, so `from rigplane.web.runtime_helpers import build_public_state_payload` works for a downstream importer; it is **not** in `rigplane/__init__.py`'s `__all__` and **not** in `docs/api/public-api-surface.md`. Public API — weak: module-level `__all__` only. **Tests-only — yes, flag it.**
**Collateral:** the 4 test call sites; `docs/internals/legacy-state-writer-inventory.md`'s Web-runtime row for `web/handlers/control.py`, which claims "Fallback `build_public_state_payload(... revision=0)` remains compatibility-only for handler tests without a server" — that fallback no longer exists in `web/handlers/control.py`, whose only envelope call is `self._server.build_state_update_envelope(force_full=True)`. **Stale doc claim** (observation).
**Depends on:** none. Related to F3 — see there for why leaving it is not merely untidy.
**Confidence:** high for "no production consumer" (observation); medium for "safe to delete" (the `__all__` export is a real, if thin, public surface).
**Falsifier:** a downstream/Pro import of `rigplane.web.runtime_helpers.build_public_state_payload`.
**Fix class:** delete (or, if the export must stay, correct the inventory row).

---

### D5 — `web/_delta_encoder.apply_delta`: test-only mirror of a TypeScript rule

**Verdict:** dead
**Elements:** `src/rigplane/web/_delta_encoder.py` — `apply_delta`, and the `_Missing` class / `_MISSING` sentinel it is the only user of.
**Consumers:** tests only — 29 matching lines under `tests/`. Production: zero. `web/server.py` constructs `DeltaEncoder` (alive); the *client* half of the wire contract is implemented in TypeScript by `frontend/src/lib/transport/ws-client.ts: applyDeltaEnvelope`.
**Written / read:** `grep -rn "DeltaEncoder"` over `src/` → 2 production lines (import + construction in `web/server.py`); `grep -rn "apply_delta"` over `src/` → only its own definition and `__all__`.
**Guards checked:** dynamic access — searched literally; no `getattr` on `apply_delta`. Out-of-repo — `_delta_encoder` is a private module (leading underscore) and the `web/server.py` import carries `# noqa: TID251`, i.e. it is already flagged as an internal reach; not a plausible downstream surface. Public API — no. **Tests-only — yes, flag it.**
**Collateral:** the 29 test lines. `_Missing`/`_MISSING` fall out with it — grep shows zero other references (observation).
**Depends on:** none.
**Confidence:** high (observation).
**Falsifier:** a Python client in a sibling repo consuming deltas.

**Adjudication note (inference):** this is *not* a live diverged duplicate of `applyDeltaEnvelope` — they no longer do the same job. The TS side gates on provider generation, `stateContractVersion`, revision monotonicity and capability topology; `apply_delta` is a shallow `dict.update` plus key removal. A Python test that "proves a delta round-trips" through `apply_delta` therefore proves something materially weaker than the real client's acceptance rules. That is the cost of keeping it, and the reason to say so rather than let a fixer merge it into anything.
**Fix class:** delete.

---

### D6 — `StateCache` level updaters with no writer

**Verdict:** dead — but fails a deletion guard, so: **undetermined**
**Elements:** `src/rigplane/core/_state_cache.py` — `StateCache.update_af_level`, `.update_alc`, `.update_attenuator`, `.update_preamp`, `.update_rf_gain`, `.invalidate_powerstat`, `.invalidate_data_mode`. (The matching `alc`/`rf_gain`/`af_level`/`attenuator`/`preamp` entries in the `CacheField` `Literal` and their `_ts` fields are the same cluster.)
**Consumers:** repo-wide literal search across all `*.py`: **6 hits in `src/rigplane/core/_state_cache.py` (the definitions), 36 hits in `tests/test_state_cache_coverage.py`, and nothing else**. No production writer, therefore no production reader can ever see a non-zero timestamp for these fields, therefore `is_fresh` returns `False` for them unconditionally.
**Written / read:** as above.
**Guards checked:** dynamic access — searched literally *and* for constructed names (`getattr(..."update_`, `f"update_`, `"update_" +`): **no hits**. Out-of-repo — `StateCacheCapable` is a public protocol (`rigplane.StateCacheCapable`, listed in `docs/api/public-api-surface.md`), so a downstream backend could hold a `StateCache` and call these. **Public API — fails**: `docs/api/types.md` renders the class via `::: rigplane.core._state_cache.StateCache`, i.e. mkdocstrings publishes every method. Tests-only — yes.
**Collateral:** 36 assertions in `tests/test_state_cache_coverage.py`.
**Depends on:** none.
**Confidence:** high that they are unwritten in production (observation); the verdict is `undetermined` purely on the public-API guard, per the method.
**Falsifier:** a downstream `StateCacheCapable` implementer that writes these.
**Fix class:** none without an owner ruling — but note the **precedent**: `docs/plans/2026-08-20-transmit-authority.md` row 13a already ruled the identically-shaped `StateCache.ptt`/`ptt_ts`/`update_ptt` deletable ("zero readers, verified"). This is the same class of dead truth, unclaimed.

---

### D7 — `StateStore.delta_since` and the history retention it alone serves

**Verdict:** dead in production; **undetermined** for deletion (pinned as public API by a contract test)
**Elements:** `src/rigplane/core/state_store.py` — `StateStore.delta_since`, `StateStore._requires_full_snapshot`, `StateStore._append_history`, `StateStore._prune_history`, the `_history` / `_history_floor_state_revision` / `_history_floor_freshness_revision` / `_max_history_count` slots, the module constant `_DEFAULT_MAX_HISTORY_COUNT = 4096`, and `SnapshotDelta.requires_full_snapshot`. Also `StateSnapshot.as_dict`.
**Consumers:** `delta_since` — **zero `src/` call sites**; `tests/test_state_store.py` has 4. `StateSnapshot.as_dict` — **zero `src/` call sites**; ~20 test lines (`tests/test_radio_poller_coverage.py`, `tests/test_state_store.py`). `_history` is read only inside `delta_since` (`for delta in self._history:`); it is written on every applied observation via `_append_history`.
**Written / read:** established with `grep -rn "delta_since\|\.as_dict()" --include="*.py" src/ tests/` and by reading every `_history` reference in `state_store.py`.
**Guards checked:** dynamic access — searched literally; `grep` for `getattr(..."delta_since"` / `getattr(...as_dict` returned nothing. Out-of-repo — `StateStore` is exposed publicly through `StateStoreCapable`/`StateModelCapable` (`core/radio_protocol.py`), so a downstream consumer could call `delta_since`. **Public API — fails, explicitly**: `tests/test_state_store.py: test_direct_writer_api_is_not_exposed` asserts `{"apply", "delta_since", "mark_stale_due", "snapshot"} <= public_callables`. Tests-only — yes.
**Collateral:** the 4 `delta_since` tests and that surface pin.
**Depends on:** none.
**Confidence:** high on liveness (observation); `undetermined` on action because the public-API guard fails.
**Falsifier:** a Pro-tier or downstream delta consumer.

**Blast radius (inference, and the reason this is worth a ruling rather than a shrug):** `_append_history` runs `_copy_changes(...)` — a deep copy of every `FieldChange` — on **every** applied observation, and appends to a list bounded at 4096. The IC-7300 profile polls `receiver.main.meters.s_meter` at `cadence_seconds = 0.2` (`rigs/ic7300.toml`, `[state_acquisition.field_policies."receiver.main.meters.s_meter"]`), so this is a steady per-sample copy-and-retain for a structure nothing reads. That is a live cost, not dormant code.

**Adjudication against the obvious competing explanation:** "these are two halves of one delta mechanism" is wrong. The production delta mechanism is `web/_delta_encoder.DeltaEncoder`, which diffs the *public payload dict*; `delta_since` diffs *FieldPaths* over retained store history. They are two independent designs for one capability, and only one has consumers — a vestigial fork, not a duplication to merge. `mark_stale_due` (which shares `SnapshotDelta`, `FreshnessTransition` and `ReconciliationRequest`) **is** live via `core/acquisition_scheduler.py: StateFreshnessService.tick`, so those three types must not be swept along with `delta_since`.

---

### D8 — Prose describing modules deleted at `91f12f69`

**Verdict:** dead (the method's "a comment or docstring describing behaviour the code no longer has")
**Elements:**
- `/Users/moroz/Projects/rigplane-core/CLAUDE.md`, Frontend-layering section: "`components-v2/wiring/` → state-adapter + command-bus (adapter layer)".
- `/Users/moroz/Projects/rigplane-core/frontend/README.md`, tree diagram lines listing `state-adapter.ts` ("Radio state → component props") and `command-bus.ts` ("User actions → radio commands").
- `/Users/moroz/Projects/rigplane-core/frontend/src/lib/runtime/adapters/radio-view-model-adapter.ts` — the docstring on `topFieldAvailable`, which cites "`components-v2/wiring/state-adapter.ts`" as a live site using the same gate.

**Consumers:** n/a — these are claims, not code.
**Evidence:** `frontend/src/components-v2/wiring/` now contains only `SemanticRadioSurfaces.svelte`, `double-click.ts`, `dual-receiver-strips.ts`, `mobile-ptt-surface.ts`, `tx-ptt-gesture.ts`. `git log --diff-filter=D` for both paths returns `91f12f69 refactor(#2317): delete legacy wiring modules and close the authority migration (A15)`. (Observation; read-only `git log`, no writes.)
**Guards checked:** n/a. `frontend/fixtures/stubs/command-bus.ts` still exists and is still imported by `frontend/fixtures/stubs/panel-adapters.ts` — it is a **live fixture helper** that merely retains the deleted module's name, and is **not** in `vite.fixtures.config.ts`'s `STUBS` table. Do not delete it.
**Collateral:** none.
**Depends on:** none.
**Confidence:** high (observation).
**Falsifier:** none plausible.
**Fix class:** delete/correct the three prose sites.

**Instructions-found-in-files:** none of the text I read attempted to direct an agent. The closest is `design-language-renderers.ts`'s "do not silently 'clean this up' as dead without checking whether such a mount has landed" — that is a maintenance note about the code, and I treated it as evidence (it is the recorded MOR-1482 ruling I cite in F2), not as a directive.

---

## Consolidations

### F1 — S-unit rendering: two independent calibrated-S-meter readers, observably diverging

**Verdict:** A — displaced/duplicated; both belong in one shared reader
**Rank:** **diverged** — same input, different operator-visible output, on both live bench radios. First in the list for that reason.
**Elements (definition sites, step 0):**
- `frontend/src/components-v2/panels/meter-utils.ts` — `formatSMeter`, `sLevel`, and its private `getSmeterKnots`, `isSmeterCalibrated`, `getSmeterMaxRaw`, `calibratedSmeterToRaw`.
- `frontend/src/components-v2/meters/smeter-scale.ts` — `calibratedToSUnit`, `calibratedToRaw`, `calibratedToSegments`, `calibratedToDbm`, `rawToSUnit`, `rawToSFloat`, and its own `isSmeterCalibrated`, `getS9Raw`, `getScaleMaxRaw`, `interpolateActual`.

**Consumers — per implementation (the discriminator):**
- `formatSMeter`: `components-v2/panels/MetersDockPanel.svelte` (mounted by `components-v2/layout/RadioLayout.svelte` whenever the active layout does **not** declare a `meters` zone), `components-v2/panels/DockMeterPanel.svelte` (mounted by `components-v2/layout/MobileRadioLayout.svelte`), `components-v2/panels/MeterPanel.svelte` (dead — see D3).
- `calibratedToSUnit`: `components-v2/meters/LinearSMeter.svelte` (mounted by `components-v2/vfo/VfoPanel.svelte` and `MobileRadioLayout.svelte`), `components-v2/panels/lcd/AmberSmeter.svelte` (mounted by `AmberCockpit.svelte` ×2 and `AmberScope.svelte`), `components-v2/layout/mobile-layout-logic.ts`, `skins/sdr-test/SdrVfoScreen.svelte`.
- Both are live. Neither is a fork of the other in the liveness sense.

**Definition site of the shared primitive:** both read the *same* data. `smeter-scale.ts` calls `getSmeterCalibration()` from `$lib/stores/capabilities.svelte`, which is literally `getMeterCalibration('s_meter')`; `meter-utils.ts` calls `getMeterCalibration('s_meter')` via `lib/runtime/adapters/capabilities-adapter.ts`, which is a one-line passthrough to the same store function. Same table, same units, two algorithms.

**Divergence (observation, computed):** I re-implemented both functions from the source and ran them over the two shipped profiles' `[[meters.s_meter.calibration]]` tables (`rigs/ftx1.toml`, `rigs/ic7300.toml`) across dB-rel-S9 in 0.5 dB steps.

- `formatSMeter` derives the S-unit from a **hardcoded 6 dB ladder off the table's bottom knot**: `s = floor((clamped - minActual) / 6)`.
- `calibratedToSUnit` inverse-interpolates to raw and then walks the table's **own declared `S0..S9` label knots**.

| profile | samples differing | example |
|---|---|---|
| FTX-1 | **147 / 229** | at the profile's own declared `S7` anchor (`actual = -18.0`), `formatSMeter` → **`S6`**, `calibratedToSUnit` → **`S7`** |
| IC-7300 | **119 / 229** | across the whole over-S9 span, `formatSMeter` → `S9+37` (continuous), `calibratedToSUnit` → **`S9+`** (snaps to the nearest declared over-S9 label, of which IC-7300 declares exactly one at raw 241) |

FTX-1's table is strongly non-linear (S6 at −33 dB, S7 at −18 dB, S8 at −9 dB), so the fixed 6 dB ladder cannot track it. IC-7300's is 2-segment-linear at 6 dB/S-unit, which is why the two agree below S9 there — and why testing only on the IC-7300 would hide this entirely.

The two files know about each other: `meter-utils.ts`'s `isSmeterCalibrated` docstring says "mirrors `smeter-scale.ts`'s `isSmeterCalibrated`" and `calibratedSmeterToRaw`'s says "matching `smeter-scale.ts`'s `calibratedToRaw`". The *inverse interpolation* was deliberately mirrored; the *forward S-unit derivation* was not, and that is where they part.

**Co-visibility (observation):** `MobileRadioLayout.svelte` passes the same `mainVfo.sValue` to `LinearSMeter` (top S-meter bar) and to `DockMeterPanel` (TX meter dock) in the same component tree. Across skins the split is cleaner and worse: the desktop dock and the amber-LCD cockpit render the same reading through different ladders.

**Prior ruling:** none found for the fork itself (searched `docs/` for `formatSMeter`, `smeter-scale`, "S-unit" — only `docs/plans/2026-04-18-desktop-meters-panel.md` lists `formatSMeter` among the dock's helpers). The relevant ruling points the *other* way: `docs/architecture/level-meter-calibrated-domain.md` (Accepted, MOR-453 Phase 0) states "the DOMAIN, the unit vocabulary, and the interpolation **ALGORITHM** are GLOBAL … the correction CURVES are PER-RIG", and lists as a defect to remove "the frontend … carries ad-hoc display formulas … `raw / 120 * 9` S-units". `formatSMeter`'s `/6` ladder is a surviving instance of exactly that. The owner's data-driven doctrine says the same: radio-specific values come from profile data, not code.

**Adjudication — competing explanation rejected:** *"Two different visual grammars legitimately need different scales; the segment ladder and the tile readout are not the same job."* Rejected on three grounds. (1) The disagreement is in the **text label** — the S-unit string — not in bar geometry; `sLevel` and `calibratedToSegments` both inverse-interpolate through the table and agree to within rounding. (2) `formatSMeter`'s ladder is not a grammar choice, it is a **hardcoded radio constant** (6 dB/S-unit) applied to a table that declares its own knot labels; the profile is the authority and one implementation ignores it. (3) Both read the identical capability payload, so there is no "different input" defence.

A second competing explanation, *"the eslint panel lockdown forced two copies"*, is a real partial cause but not a justification: `src/components-v2/panels/**` is barred from `$lib/stores/*` (`frontend/eslint.config.js`, `FORBIDDEN_PANEL_IMPORTS`) while `src/components-v2/meters/**` is not, which is why one twin routes through `capabilities-adapter.ts` and the other imports the store directly. Widening that boundary is deferred to Tier 3 by `docs/plans/2026-04-29-panel-adapter-migration.md` ("`$lib/stores/*` is **out of scope** for this issue (Tier 3)") — a genuine recorded ruling. But the adapter is a passthrough, so the boundary is not what makes the two answers differ; the ladder is.

**Blast radius:** 2 helper modules, 6 mounting components across 4 skins (desktop-v2, amber-lcd, mobile, sdr-test) plus the v3 semantic meters surface. Consolidating on `smeter-scale.ts`'s table-driven derivation and deleting `formatSMeter`'s ladder is the smaller move; `MetersSurface.svelte` already imports `sLevel` and does **not** import `formatSMeter`, so the v3 surface is unaffected.
**Required surface:** exists — `smeter-scale.ts: calibratedToSUnit` is table-driven and already serves four surfaces. What is missing is a home both directories may import; `capabilities-adapter.ts` is the obvious candidate and already has the right shape.
**Expensive contract:** **No.** Purely a display-side derivation; the wire value (`ServerState.main.sMeter`, calibrated dB-rel-S9) and the profile schema are untouched. No persisted workspace data, no WS payload change.
**Depends on:** none. Independent of D3 (`MeterPanel` is one of `formatSMeter`'s three consumers and is dead; deleting it first shrinks the job).
**Confidence:** high (observation for both implementations and their consumers; the divergence table is a computed re-implementation of the two functions, which is inference from source — a reader should re-run it against the real modules before acting).
**Falsifier:** a test or runtime path that normalises one of the two outputs before display; or a profile-loading step that rewrites the FTX-1 table into 6 dB steps before it reaches the frontend.
**Fix class:** consolidate.
**Actionable:** yes.

*Adjacency to the exclusion list:* MOR-1374 concerns `LinearSMeter`'s **peak-hold** reimplementation, not its S-unit source; I confirmed that ticket's substance (`LinearSMeter.svelte` carries its own `PEAK_HOLD_MS = 1000` and an rAF glide against `meter-utils.ts`'s `PEAK_DECAY_MS = 1500` + pure `updatePeakHold`/`peakHoldDisplay`) and am **not** re-reporting it. The two are separable.

---

### F2 — TX state-feedback treatment: one decision, four expressions, one of them load-bearing

**Verdict:** A — displaced (the decision belongs below the presentation layer), with a liveness twist
**Rank:** displaced (a safety-relevant decision multiplied across clients)
**Elements:**
- `frontend/src/presentation/languages/fieldline/state-feedback-renderer.ts` — `renderStateFeedback`, `SESSION_RAIL`, `DOUBT_RAIL`, `KEY_TREATMENT`, `stringField`.
- `frontend/src/presentation/languages/studioline/state-feedback-renderer.ts` — `renderStateFeedback`, `SESSION_RAIL`, `DOUBT_RAIL`, `KEY_TREATMENT`, `stringField` (same names, same file-local scope).
- `frontend/src/presentation/languages/fieldline/fieldline.css` and `.../studioline/studioline.css` — the `[data-design-language=…] .rx-tx-surface:has([data-session='…'])` / `:has([data-rf='…'])` rule blocks.

**Consumers — per implementation:**
- The two TS renderers are reached from `frontend/src/semantic/RxTxSurface.svelte`, via `frontend/src/semantic/design-language-renderers.ts: renderSlot('stateFeedback', …)`.
- The CSS rules are reached from the `data-session` / `data-rf` attributes `RxTxSurface.svelte` writes on `.rx-tx-state` itself.

**Divergence — and the liveness fact that reframes it (observation):** `design-language-renderers.ts: annotate` copies only **top-level primitives** of a descriptor into `data-dl-*` attributes; nested objects and arrays are skipped by construction. The state-feedback descriptors expose `rail`, `band`, `slab` (fieldline) and `rail`, `key` (studioline) as nested objects — so `widthPx`/`thicknessPx`, `tone`, `treatment`, `edgeStyle`, `filled`, `fill`, `textTone`, `focusRing` **never reach the DOM**. What survives is `kind`, `numeralTone`, `meterScaleLabel`, and studioline's `micro`. `grep -rn "data-dl"` over all `*.css` in `frontend/src`: **zero matches** — no stylesheet reads even those. `RxTxSurface.svelte` spreads `{...stateFeedback?.attributes ?? {}}` onto its `<section>` and reads nothing back structurally.

So the visual conclusions of both renderers are computed per render and discarded; the painting is done entirely by the two stylesheets off `data-session`/`data-rf`. Outside their own unit tests (`presentation/languages/*/__tests__/state-feedback-renderer.test.ts`), the descriptor bodies have no consumer.

**The duplicated decision itself (observation):** stripped of visual vocabulary, the two TS renderers are the same function —

```
const known = SESSION_RAIL[session];
const state = known && (session !== 'idle' || rf === 'receiving') ? known : DOUBT_RAIL;
const held = KEY_TREATMENT[session];
const treatment = held !== undefined && held !== 'idle' ? held
  : blocked ? 'blocked' : state === DOUBT_RAIL ? 'pending' : 'idle';
```

identical in both files, along with `KEY_TREATMENT`'s five entries, `stringField`, `numeralTone: state.keyed ? 'tx-target' : 'primary'`, `meterScaleLabel: state.keyed ? 'PO' : 'S'`, and the `TX FAULT: ${fault}` composition. Both files also carry a near-identical `F3/N3` comment describing the *same bug* — an unconditional `blocked ? 'blocked' : …` reporting an inert slab/key under a flooded rail — found and fixed **twice**. That is the cost of the duplication, recorded in the source by the people who paid it.

**Prior ruling:** `frontend/src/presentation/languages/declarations.ts` states the two bundles "export the same three renderer names: that symmetry IS the proof — the second language plugs into identical slots with no contract change". That ruling covers having two *renderers*; it does not rule on the decision logic inside them being byte-identical. Separately, `design-language-renderers.ts` records an owner ruling (MOR-1482, session 19) that `RendererDisplay.text` is "currently UNCONSUMED by every production caller" and is deliberately kept for a future hero-scale mount — "do not silently 'clean this up' as dead". That ruling is about `.text`; whether it extends to the nested descriptor bodies is **unknown**, and is precisely the question a fixer must not guess at.
**In-flight:** none found for the shared decision. Note the contrast with the meters slot: `fieldline/meters-renderer.ts` and `studioline/meters-renderer.ts` genuinely differ (quantised segment ladder vs continuous rail with fractional fill) and share no decision layer — that pair is **legitimately local** and I cleared it.

**Adjudication — competing explanation rejected:** *"Design languages are sanctioned multi-implementation; two renderers is the design."* Accepted for the *visual descriptors* — rail width vs rail thickness, band vs micro-label, knockout vs palette are exactly what a design language is for. Rejected for the *state resolution*: which session lands on the doubt rail, whether a `blocked` flag outranks a `keyed` session, and whether the meter re-zones to `PO` are **safety conclusions about the transmitter**, not visual grammar. Both files' own headers say so — "SAFETY INVARIANT R9. This renderer DISPLAYS the App TX authority's conclusions; it never forms its own" — and then each forms the doubt-rail conclusion independently. `semantic/rx-tx-surface.ts` already owns `rfState()` / `txSessionState()` and is where a resolved `feedbackState` would sit, one layer below both languages and above the CSS.

**Blast radius:** 2 renderer modules (~280 lines combined), 2 stylesheets, 1 surface (`RxTxSurface.svelte`), 2 renderer unit-test files. A third design language registered tomorrow inherits the copy.
**Required surface:** design, not a move. Something like a `resolveTxFeedbackState(rf, session, fault, blocked) -> { state, treatment, keyed, doubt }` in `semantic/rx-tx-surface.ts`, which both renderers map to their own vocabulary and which the CSS's `:has()` rules can be pinned against. The presentation-layer eslint zone bans transport/stores/runtime imports but not `semantic/` types, so the direction is legal.
**Expensive contract:** **No** for the TS half. **Qualified yes** for anything that changes what `RxTxSurface.svelte` writes as `data-session`/`data-rf`: those attributes are the live styling contract for both stylesheets and are asserted by `presentation/languages/*/__tests__/stylesheet.test.ts`. Not persisted user data, not a WS payload, not public API.
**Depends on:** none, but the liveness question above should be settled *first* — consolidating descriptor logic that turns out to be deletable would be wasted work. Ask the owner whether the nested descriptors are, like `.text`, deliberately kept for a future mount.
**Confidence:** high on the duplicated decision and on `annotate`'s discard behaviour (both observations, read from source); medium on the recommended fix location (inference).
**Falsifier:** a consumer of the nested descriptors I did not find — e.g. a built-dist browser suite reading `data-dl-*`, which `production-activation.test.ts` alludes to ("the built-dist browser suite proves reachability"). I did not inspect that suite; treat it as the first thing to check.
**Fix class:** design.
**Actionable:** yes, after the liveness ruling.

*(Post-audit note, 2026-08-30: the liveness question was resolved the same day — the built-dist browser suite is `frontend/tests/e2e/i18n/i18n-visual.spec.ts` and consumes neither the nested descriptors nor `data-dl-*`; no consumer exists and none is structurally reachable. Owner ruled: consolidate the decision, delete the nested descriptor bodies, keep `.text` per MOR-1482.)*

---

### F3 — `s_meter`: one field name, two units, across the two live state models

**Verdict:** B — gap. The shared layer has no way to state which unit a mirrored field carries.
**Rank:** diverged (a latent double-conversion, of the exact class the frontend already needed a static test to prevent)
**Elements:**
- `src/rigplane/runtime/_civ_rx.py` — `CivRxMixin._calibrated_meter_value` produces the **calibrated** value (dB-rel-S9 when the profile declares `[meters.s_meter]`) and it is what `_observations_from_frame` attaches to the `receiver.*.meters.s_meter` observation, with `quality = ("confirmed", "calibrated")`.
- `src/rigplane/runtime/_civ_rx.py`, the 0x15 `RadioState` mirror handler — writes `rs.receiver(rs.active).s_meter = raw`, i.e. the **raw device byte**, to the same logical field on the legacy model.
- `src/rigplane/web/state_schema.py` — `ReceiverStatePublic` types the public `sMeter` with no unit discriminator.
- `src/rigplane/web/runtime_helpers.py` — `build_public_state_payload_from_snapshot` (calibrated source) and `build_public_state_payload` (raw `RadioState` source) both produce payloads validated against that one schema.

**Consumers — per implementation:** the calibrated `StateStore` value reaches the browser (`web/server.py` builds every payload from `command_state_store.snapshot()`); the raw `RadioState` value reaches rigctld (`src/rigplane/rigctld/handler.py`, the four inline STRENGTH branches reading `main_state.s_meter` / `state.sub.s_meter`).

**Divergence:** none *today* — I checked, and the steelman wins on correctness. `rigctld/handler.py`'s `get_level` STRENGTH branch guards the calibrated path explicitly (`if "calibrated" in projected.quality: return str(int(projected.value))`) before falling back to the hand-rolled formula, and the four inline branches read the raw `RadioState` mirror, so each formula meets the unit it expects. The frontend has a dedicated static guard (`components-v2/meters/__tests__/smeter-no-double-conversion.test.ts`) that greps eight named files for `rawToDbm(` near an `sMeter`/`sValue` identifier — it exists because this bug was caught once in review and reverted.

**The gap:** the discrimination is carried by (a) a `quality` tuple on one path, (b) a grep-based test on another, and (c) nothing at all in the schema. `build_public_state_payload` (D4) will happily emit a payload whose `sMeter` is the raw byte and whose shape validates against the same `ServerStatePublic` model that the calibrated payload validates against — and `tests/web/test_state_schema_conformance.py` asserts exactly that both validate. The schema cannot tell the two apart, so nothing downstream can either.

**Prior ruling:** `docs/architecture/level-meter-calibrated-domain.md` §Phase 1 realized-outcomes: "The shared converter is `interpolate_meter(raw, meter_calibrations, meter_key) -> (value, calibrated)`" and "`FieldSpec` … carries `unit` only" — the unit vocabulary exists (`_FIELD_UNITS` in `core/state_pipeline_contracts.py`) but is attached to `FieldSpec`, not to the public web schema. Phase 4 ("remove device-scale assumptions … the legacy `RadioState` raw mirrors") is the recorded end-state and is not yet reached. `docs/internals/legacy-state-writer-inventory.md` classifies the `RadioState` mirror as `compatibility_only` + `deferred_follow_up`.
**In-flight:** yes — this is Phase 4 of an accepted ADR. Report as **migration incomplete**, not as a new defect.

**Adjudication — competing explanation rejected:** *"Two consumers legitimately want different units; the calibrated one for the UI, the raw one for Hamlib STRENGTH."* Partly true and partly the problem. Hamlib's STRENGTH is dB-rel-S9 — the *same* domain the calibrated value is already in — so the raw mirror exists only because the conversion is done later, by hand, in `rigctld`. The right shape is one calibrated value plus one profile-driven converter, which the ADR already ratified.
**Blast radius:** 1 backend mirror write, 1 schema, 2 payload builders, 5 rigctld formatting sites, 1 frontend static guard.
**Required surface:** a unit discriminator on the public field-status/schema (the FieldSpec `unit` vocabulary already exists and could be projected), or completion of ADR Phase 4 so only one unit is ever in flight.
**Expensive contract:** **Yes.** The public WS/HTTP payload shape and the Hamlib wire text are both involved; `docs/internals/legacy-state-writer-inventory.md`'s Public Compatibility Callouts explicitly ring-fence them.
**Depends on:** D4 should land first — deleting the raw-source front door removes half the hazard for free and costs no design decision.
**Confidence:** high on the two-units observation; high that no live double-conversion exists today (I traced all five rigctld sites and the frontend guard); medium on "the schema is the right place for the discriminator" (inference).
**Falsifier:** a `FieldSpec`-derived unit already projected into the public payload that I did not find.
**Fix class:** design.
**Actionable:** not until the owner rules on ADR Phase 4 sequencing.

---

### F4 — The strict freshness gate, written twice in the adapter layer

**Verdict:** A — displaced, movable essentially as-is
**Rank:** parallel (maintenance cost today; would become *diverged* the moment one side is tightened)
**Elements:**
- `frontend/src/lib/runtime/props/panel-props.ts` — `fieldObserved`, and alongside it `hasCap`, `topFieldAvailable`, `activeReceiverKey`, `activeRx`.
- `frontend/src/lib/runtime/adapters/radio-view-model-adapter.ts` — `seen`, and alongside it `hasCap`, `topFieldAvailable`, `activeReceiverId`.

**Consumers — per implementation:** `fieldObserved` — 2 call sites, both inside `relativeVfoIdentityUnknown` in its own file. `seen` — 13 occurrences in `radio-view-model-adapter.ts`, feeding `activeReceiverId`, `boolFact`, `readings` and the per-family `derive*` functions. Both live.

**Divergence (observation):** the two predicates are logically identical —

- `fieldObserved`: `status?.observed === true && status.freshness === 'fresh' && status.availability === 'available'`
- `seen`: the same three conjuncts, same order.

`topFieldAvailable` is identical in both (`return isFieldAvailable(state, field)`) — a one-line re-wrap of the genuinely shared `lib/state/field-status.ts: isFieldAvailable`. `hasCap` **does** differ: `panel-props.ts` wraps the expression in `try { … } catch { return false }`; `radio-view-model-adapter.ts` does not. On a malformed capabilities payload where `capabilities` is a non-array object, the v2 props path degrades to "capability absent" while the v3 adapter throws. Narrow, but it is a real behavioural split in a shared predicate.

**Definition site of the shared primitive:** `frontend/src/lib/state/field-status.ts` already hosts `getFieldStatus`, `getFieldAvailability`, `isFieldAvailable`, `areFieldsAvailable`, `isAnyFieldAvailable` and is consumed by 10 modules. It is the obvious home for the strict three-part gate and for `hasCap`; the strict gate simply was never put there.

**Prior ruling:** `radio-view-model-adapter.ts`'s `topFieldAvailable` docstring records a deliberate ruling on *which* gate `txAux` uses ("the SAME field-status gate `toTxProps`/`toVoxProps` use … deliberately the looser … not this file's own stricter `seen()` three-part gate"). That ruling is about gate *selection* per field family, and it is correct and worth preserving. It does not rule that the strict gate must be defined twice — the same comment calls it "this file's own", which is the displacement, stated.
**In-flight:** none.

**Adjudication — competing explanation rejected:** *"v2 props and the v3 view model are two derivation stacks by design (the cutover), so private helpers on each side are correct."* I take the premise seriously and largely accept it — see Cleared, where I clear the two stacks as a whole. But the premise justifies two *outputs* (collapsing props vs unknown-preserving view model), not two definitions of "has this field been observed, fresh and available". A shared module already exists at the right level, both files already import from it, and the eslint zones permit it (`lib/runtime/**` may import `$lib/state/*`; the ban is on `components-v2/`, `presentation/` and `semantic/` value imports). There is no boundary forcing the split.

**Blast radius:** 2 modules, ~15 call sites, no component changes. Small.
**Required surface:** exists — `lib/state/field-status.ts`. Add the strict gate (and one `hasCap`) there and delete both private copies.
**Expensive contract:** **No.** Internal frontend helper; no persisted data, no wire shape, no public API.
**Depends on:** none.
**Confidence:** high (observation — I read both definitions and counted both call sites).
**Falsifier:** an eslint zone rule I misread that bars one of the two files from `$lib/state/*`.
**Fix class:** consolidate.
**Actionable:** yes — this is the cheapest consolidation in the report.

---

### F5 — rigctld STRENGTH: a helper exists and four sibling branches ignore it

**Verdict:** A — displaced; the helper is already in the shared module and is bypassed
**Rank:** parallel, trending diverged (two divisors already in play)
**Elements:**
- `src/rigplane/rigctld/routing.py` — `_format_strength(value, *, raw_divisor)`, the single shared implementation: `str(round((raw / raw_divisor) * 114.0 - 54.0))`.
- `src/rigplane/rigctld/handler.py` — `_format_strength` is imported and called at exactly **one** site (the `StateStore`-projected, uncalibrated branch, with `raw_divisor=241.0`). **Four** further branches in the same `if level == "STRENGTH":` block inline the identical expression `str(round((raw / 241.0) * 114.0 - 54.0))` instead: the SUB-receiver `RadioState` read, the SUB `get_s_meter` read, the MAIN `RadioState` read, and the MAIN `get_s_meter` read.
- `src/rigplane/rigctld/routing.py` — the Yaesu routing class's `format_state_level` calls the same helper with `raw_divisor=255.0`.

**Consumers — per implementation:** all five formatting sites are live on the Hamlib GET path; which one serves a read depends on whether a `StateStore` projection exists, whether the radio is dual-RX, and which backend's routing is installed.
**Divergence (observation):** the same raw byte formats differently depending on divisor — `241.0` on the generic/Icom handler path, `255.0` on the Yaesu routing path. Neither consults the connected rig's declared `[[meters.s_meter.calibration]]` table; FTX-1's own table places S9 at raw 130 and its top knot at raw 240, and is non-linear, so both linear approximations are wrong for it in different ways. The comment beside one inline site — "IC-7610 S-meter: 0→S0(−54 dB), 120→S9(0 dB), 241→S9+60 dB" — names a radio retired from the bench on 2026-08-04.

**Prior ruling:** `docs/architecture/level-meter-calibrated-domain.md` inventories this precisely — "rigctld … converts STRENGTH / RFPOWER / LEVEL inline with hand-rolled formulas … a distinct `(raw / 241.0) * 114.0 - 54.0` for STRENGTH" — and defers the fix to Phase 3 ("simplify the rigctld GET/SET conversions"). So the *existence* of the hand-rolled formula is recorded and I am not re-reporting it as a discovery.
**In-flight:** yes, ADR Phase 3.

**What is not covered by that ruling (the finding):** the ADR names one formula. There are five sites, one helper that four of them decline to call, and two different divisors. Consolidating the four inline sites onto the existing `_format_strength` is a mechanical change that needs no ADR progress, and it makes the eventual Phase-3 swap a one-function edit instead of a five-site hunt.

**Adjudication — competing explanation rejected:** *"241 vs 255 is a deliberate per-vendor difference."* Plausible on its face — the 255 site is inside the Yaesu routing class. Rejected as a *justification* because neither divisor matches the vendor's own declared curve: FTX-1's `rigs/ftx1.toml` table tops out at raw 240 with a non-linear ladder, so 255 is not "the Yaesu scale", it is a second guess. Whether the divisor split was intended is **unknown**; either way it belongs as data in the profile, not as a literal at two call sites.

**Correctness check I ran (and which cleared a suspected bug):** I checked whether the calibrated `StateStore` value could reach a hand-rolled formula and be double-converted — the mirror of the frontend's MOR-1451 bug. It cannot: `handler.py`'s projected branch tests `"calibrated" in projected.quality` first, and the four inline branches read the `RadioState` mirror, which `_civ_rx` writes **raw**. Stated plainly because a clean result is a result.

**Blast radius:** 1 file (`rigctld/handler.py`), 4 call sites, plus the shared helper's signature if the divisor becomes profile-derived.
**Required surface:** exists for the mechanical half (`_format_strength`). For the real fix, `runtime/meter_cal.py: interpolate_meter` is the ADR's designated global converter and is already used by `_civ_rx`.
**Expensive contract:** **Yes** — Hamlib wire text. `docs/internals/legacy-state-writer-inventory.md`: "State migration must not alter text output without explicit compatibility callout." Collapsing the four inline sites onto the helper with the same divisor is text-neutral; changing a divisor is not.
**Depends on:** F3 (unit discrimination) for the profile-driven half. The mechanical de-duplication depends on nothing.
**Confidence:** high (observation).
**Falsifier:** a comment or test I missed justifying 255 for Yaesu on measured evidence.
**Fix class:** consolidate (mechanical half) / design (profile-driven half).
**Actionable:** yes for the mechanical half; the divisor question needs an owner ruling.
**Scope note:** `rigctld/` is a sibling consumer and is not named in this audit's scope. Included because it is the same S-meter capability as F1 and F3; treat as adjacent, not central.

---

### F6 — Pending/confirmed tracking: two mechanisms, adoption incomplete

**Verdict:** already-shared, migration incomplete — **reported as counts, not as a discovery** (this is MOR-1686's subject)
**Rank:** parallel
**Elements:**
- Mechanism A (older, name+param keyed): `frontend/src/lib/runtime/adapters/panel-adapters.ts` — `latestPendingParam`, `armedFact`, `ArmedFact`, and the accessors `getPendingFrequencyHz`, `getPendingFilterSelection`, `getPendingPreampLevel`, `getPendingNbOn`, `getPendingNrOn`, `getModeArmed`, `getAgcArmed`, `getFilterArmed`, `getPreampArmed`, `getAttenuatorArmed`, `getDataModeArmed`, `getAutoNotchArmed`, `getManualNotchArmed`.
- Mechanism B (declarative, state-backed): `frontend/src/lib/stores/commands.svelte.ts` — `StateBackedCommandDescriptor`, `STATE_BACKED_COMMAND_DESCRIPTORS`, `getStateBackedCommandDescriptor`.
- Shared presentation contract (MOR-1711): `frontend/src/primitives/control-feedback/control-feedback-presentation.ts` — `projectControlFeedbackPresentation`, `confirmedPressed`, `confirmedChecked`, `confirmedSelected`.

**Consumers — the counts:** Mechanism B has **2** registered descriptors (`FILTER_WIDTH_COMMAND_DESCRIPTOR`, `BREAK_IN_DELAY_COMMAND_DESCRIPTOR`). Mechanism A has **13** accessors. The shared presentation contract has **2** production consumers (`components-v2/panels/CwPanel.svelte`, `semantic/CwKeyerSurface.svelte`) against 54 lines mentioning `ControlFeedback`/`controlFeedback` across the frontend.
**Prior ruling:** `panel-adapters.ts`'s `latestPendingParam` docstring explicitly records the consolidation decision — "Shared by `getPendingFrequencyHz` above (leg 1, MOR-1478) and the four … — no second, parallel pending-state" — i.e. mechanism A was itself already consolidated from scattered copies. Mechanism B is the intended successor. `docs/internals/legacy-state-writer-inventory.md` records the frontend store's "Optimistic maps remain local pending overlays" as `compatibility_only`.
**Adjudication:** not drift. A 2-of-15 adoption ratio is what a partly-landed migration looks like, and reporting it as duplication would send a fixer to build a third. Recorded here only so the ratio is on the record.
**Materially bigger than ticketed?** **No** — I found nothing outside MOR-1686's stated subject.
**Expensive contract:** **No.**
**Confidence:** high (observation).
**Fix class:** none (owned by MOR-1686).
**Actionable:** no.

---

### F7 — `RadioPoller`: two unrelated classes, one name

**Verdict:** name collision
**Rank:** name-collision (cheap to detect, rarely urgent — listed for completeness per the method)
**Elements:** `src/rigplane/web/radio_poller.py: RadioPoller` (4386-line web acquisition executor) and `src/rigplane/rigctld/poller.py: RadioPoller` (the rigctld background cache poller).
**Consumers:** both live and unrelated; they sit in sibling layers that never import each other.
**Divergence:** total — different constructors, different responsibilities, different state models (`StateStore` vs `StateCache`).
**Why it costs something (observation):** `docs/api/rigctld.md` documents "`RadioPoller`" as `rigplane.rigctld.poller.RadioPoller`, and `docs/superpowers/specs/2026-06-02-radio-state-pipeline-design.md` refers to "Web `RadioPoller` … `mark_polled`" — a reader who follows the public doc from the spec sentence lands on the wrong class. D1's dead symbols live on the *web* one.
**Prior ruling:** none found.
**Expensive contract:** **No** for the web class (not publicly exported). **Yes** for the rigctld class — it is documented as an importable public symbol in `docs/api/rigctld.md`, so it must not be the one renamed.
**Depends on:** none.
**Confidence:** high (observation).
**Falsifier:** none.
**Fix class:** none required; if ever addressed, rename the web-side class.
**Actionable:** no.

---

## Weakest link

**F2's liveness verdict** — the claim that the state-feedback descriptors' nested bodies reach no production consumer.

It rests on reading `design-language-renderers.ts: annotate` (which skips non-primitives) and on `grep -rn "data-dl"` returning no CSS matches. Both are solid. What I did **not** inspect is the "built-dist browser suite" that `presentation/languages/__tests__/production-activation.test.ts` names as the thing that "proves reachability". If that suite asserts on `data-dl-*` attributes, or if a mount outside `frontend/src/` consumes descriptors, the liveness half of F2 is wrong and only the duplicated-decision half survives. Check that suite first. *(Resolved 2026-08-30 — see the post-audit note under F2: the suite is `i18n-visual.spec.ts` and consumes neither.)*

Second-weakest: **F1's divergence table**. The two implementations and their consumers are directly observed, but the 147/229 and 119/229 figures come from my re-implementation of both functions in Python against the shipped TOML tables, not from executing the TypeScript. The FTX-1 `S6` vs `S7` case at the profile's own declared anchor is the single claim worth re-deriving before anyone acts, and it is easy to re-derive.

---

## Cleared

Capabilities examined and found healthy, named:

- **Public web payload assembly (HTTP vs WS).** One implementation. `web/runtime_helpers.py: _build_public_state_payload_from_dict` is the sole assembler; `web/server.py` reaches it through `build_public_state_payload_from_snapshot` for both `_serve_state` and `_broadcast_state_update`. No HTTP/WS fork. (The unused second front-door is D4, a separate matter.)
- **`StateStore` / `StateCache` / `RadioState` as "three parallel state models".** This is a governed, documented migration, not drift: `docs/internals/legacy-state-writer-inventory.md` (MOR-407, updated for MOR-437, dated 2026-06-03) classifies **every** writer as `deleted` / `migrated` / `protocol_local_keep` / `compatibility_only` / `executor_cache_keep` / `deferred_follow_up`, names the guard test for each, and lists the intentionally-kept mirrors by CI-V sub-command. Reporting it as a finding would re-litigate a settled ruling. I spot-checked the classification against the code and found it accurate.
- **Backend freshness ownership.** Exactly one decaying mechanism: `core/state_store.py: StateStore.mark_stale_due`, driven by `core/acquisition_scheduler.py: StateFreshnessService.tick`, with per-field TTLs from `rigs/*.toml` `[state_acquisition.field_policies]`. The two apparent rivals are not rivals: `StateCache.is_fresh` is executor-local and sanctioned, and `RadioPoller.state_is_fresh` has no readers at all (D1).
- **`core/observation_adapter.py: ProviderObservationAdapter`.** Genuinely shared — three independent backends construct it (`backends/yaesu_cat/observations.py`, `backends/yaesu_cat/poller.py`, `backends/rigctld_client/observations.py`). No per-backend copy.
- **`core/state_pipeline_contracts.py`.** Broadly consumed across 25 modules in `core/`, `runtime/`, `web/`, `rigctld/`, `backends/`, `profiles/`. Not a parallel model.
- **Frontend field-availability.** One implementation: `lib/state/field-status.ts` (`getFieldStatus`, `getFieldAvailability`, `isFieldAvailable`, `areFieldsAvailable`, `isAnyFieldAvailable`), consumed by 10 modules including both derivation stacks and the LCD panels. Only the *strict* gate is duplicated (F4); the loose one is properly shared.
- **`semantic/radio-view-model.ts` vs `lib/runtime/adapters/radio-view-model-adapter.ts`.** Contract and producer, not duplicates. The adapter imports the contract type-only under a recorded eslint exception and annotates its return type so contract drift is a compile error at the producer.
- **The v2-props / v3-view-model dual derivation stack.** Two stacks over one `ServerState`, and the steelman wins: they produce deliberately different shapes (v2 collapsing props vs the MOR-988 unknown-preserving view model), MOR-1095 keeps the legacy skin selectable by design, and shared derivation *is* factored out where it can be (`$lib/radio/filter-controls.ts: projectNrLevel`, `deriveIfShift`, `pbtRawToHz`, `nbDepthRawToDisplay` are imported by both). Only the private helper copies are a finding (F4).
- **The two `meters-renderer.ts` design-language implementations.** Legitimately local: quantised 12-segment ladder vs continuous fractional-fill rail, sharing no decision layer. Correctly located.
- **Panel → `$lib/stores/*` lockdown.** Enforced for all 18 panels by `frontend/eslint.config.js` (`FORBIDDEN_PANEL_IMPORTS`). The ~15 remaining direct store imports under `components-v2/layout/`, `components-v2/meters/`, `components-v2/vfo/`, `components-v2/controls/` and `skins/` are explicitly deferred: `docs/plans/2026-04-29-panel-adapter-migration.md` — "`$lib/stores/*` is **out of scope** for this issue (Tier 3)". Recorded, not drift.
- **No double conversion of calibrated meters on any path I traced.** Frontend: guarded by `components-v2/meters/__tests__/smeter-no-double-conversion.test.ts` across eight named call sites. Backend/rigctld: `rigctld/handler.py`'s STRENGTH branch checks `"calibrated" in projected.quality` before applying its formula, and the `RadioState` mirror it otherwise reads carries raw. Checked specifically because F3 makes it the obvious failure mode.
- **Ticketed items, confirmed present and deliberately not re-reported:** MOR-1374 (`LinearSMeter.svelte`'s local `PEAK_HOLD_MS = 1000` + rAF glide vs `meter-utils.ts`'s `PEAK_DECAY_MS = 1500` + pure `updatePeakHold`/`peakHoldDisplay`), MOR-1352 (`hardwareConnected`), MOR-1380 (cross-skin suppression channel, recorded in `docs/validation/desktop-v2-v3-parity.md` P16), MOR-1686 (see F6). MOR-1373 skipped per the dispatch.
