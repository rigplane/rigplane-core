# Closing mechanism audit — MOR-2478 (command path + control-value conversion)

**Audited revision:** `94361667103beb5626cb158f591f1fc8a583a5e0` (`git rev-parse --short HEAD` → `94361667`), read from a worktree detached at that commit.
**Tree state:** no tracked file modified, nothing committed or pushed.

**Method followed:** `.claude/skills/mechanism-audit/SKILL.md`, read from this worktree at this revision. Steps 0–5 executed in the order the file marks "do not reorder", including the step-3a systematic dead-code sweep and the step-4 steelman before any verdict. Report format is the method's (Deletions ranked first, then Consolidations, then **Weakest link** and **Cleared**).

**Deviation from the method, declared:** the commissioning question is closure of prior findings, so **Part 1** adjudicates the fifteen prior findings as a closure ledger before the method's two ranked lists, which appear as **Part 2** carrying only findings fresh at this revision. No verdict below was formed before its steelman; where the method and the dispatch differ on ordering only, this is the whole of the difference.

**Prior rulings under test (step 1):** `.claude/audits/2026-09-15-mechanism-audit-command-path.md` (D1, F1–F8) and `.claude/audits/2026-09-15-mechanism-audit-control-conversion.md` (D1, F1–F5), both taken at `e3d435dc`. Read in full. Per the dispatch their numbers are treated as claims, not evidence; every count relied on below was re-established at this revision and carries its counting rule.

**Instructions-in-files check:** no comment, docstring, TODO or plan document in the tract attempted to direct an agent. The docstrings quoted below (`rigctld/handler.py: _defer_write_gate`, `profiles/control_domain.py`) are claims about code and are treated as such.

---

## Attribution buckets

- **(a) this line's own pull requests** — `ca8ef0d3` #3502, `635dbee6` #3505, `d501a980` #3504, `6ddbe5fd` #3506, `6bf81ed7` #3508, `89c835fb` #3510, `79492bf0` #3512, `966f315e` #3514, `94361667` #3515.
- **(b) other work between `e3d435dc` and `94361667`** — the other 25 commits in the range.

Counting rule for the range: `git log --oneline e3d435dc..HEAD` returns **34 commits**; 9 are bucket (a), **25 are bucket (b)**. Bucket (a) touches, by `git show --stat`, only `frontend/` and `docs/` plus `CLAUDE.md` — **no bucket (a) commit modifies any file under `src/rigplane/`**. That single observation governs most of the ledger below: every Python-side closure in this range belongs to bucket (b).

---

# Part 1 — Closure ledger for the fifteen prior findings

Counting rule for the verdict totals: **one verdict per prior finding**, 15 findings (2 deletions + 13 consolidations across the two opening audits).

| Category | Count | Findings |
|---|---|---|
| **Closed** | 4 | cp-D1, cc-D1, cc-F3, cc-F5 |
| **Partly closed** | 2 | cp-F5, cc-F2 |
| **Moved, not closed** | 1 | cp-F6 |
| **Open, unchanged** | 4 | cp-F1, cp-F2, cp-F7, cc-F1 |
| **Open by owner decision, unchanged** | 1 | cc-F4 |
| **Stands; no closure was due** | 3 | cp-F3, cp-F4, cp-F8 |

`cp-` = command-path audit, `cc-` = control-conversion audit.

Bucket split of the closures: **bucket (b) closed cp-D1, cp-F5 (in part), cc-F2 (in part), cc-F3, cc-F5. Bucket (a) closed cc-D1, and nothing else.**

---

## cp-D1 — `rigctld/handler.py: _FallbackRigState` dead members — **CLOSED**

**Attribution: bucket (b)** — `18f9988e` #3492 (*delete the dead rigctld fallback-state members*) and `9056a609` #3496 (*delete caller-less pending/fallback state slice in rigctld handler*). Established by `git log --oneline -S'_FallbackRigState' e3d435dc..HEAD -- src/rigplane/rigctld/handler.py`, which returns `9056a609`; `git show --stat` on both commits shows the two-stage removal (#3492: −130 lines across handler/routing/radio_protocol; #3496: −100 lines, handler only).

Observation: `grep -rn "_FallbackRigState" src/ tests/` over the whole tree returns **1 hit**, and it is prose — a docstring sentence in `tests/test_tx_authority_characterisation.py`. The class and every member the prior audit named are gone. The prior audit's caveat that `update_s_meter`/`update_rf_power`/`update_swr`/`is_fresh` also appear on `core/_state_cache.py: StateCache` is confirmed: those remaining hits are the homonym, which is live (read at `runtime/_dual_rx_runtime.py` and `runtime/radio.py: IcomRadio.set_rf_power`).

The prior audit's collateral prediction also landed: #3492 removed the `core/radio_protocol.py` and `backends/yaesu_cat/radio.py` prose citations in the same change.

Confidence: high. Falsifier: a `_FallbackRigState` reference reachable through dynamic attribute construction — searched **literally** over `src/` and `tests/`; not searched in any sibling or private repository.

## cp-F1 — three parallel dispatch tables beside a 12-entry shared registry — **OPEN, UNCHANGED**

Re-established counts at this revision, each with its rule:

| Quantity | Rule | `e3d435dc` | `94361667` |
|---|---|---|---|
| `web/handlers/control.py` string-literal `case` arms | `grep -c '^\s*case "'` | 116 | **116** |
| `web/handlers/control.py` `case` arms, all forms | `grep -c '^\s*case '` | 125 | **125** |
| `web/radio_poller.py` `case` arms (all class-pattern, no string literals) | `grep -c '^\s*case '` | 120 | **120** |
| `core/command_dispatch.py: _COMMAND_DESCRIPTORS` entries | `grep -c 'CommandDescriptor('` | 12 | **12** |
| `web/handlers/control.py: ControlHandler._COMMANDS` names | AST parse of the `frozenset(...)` call, distinct string constants | 152 | **152** |

Not one of the five numbers moved. `git diff --stat e3d435dc..HEAD -- src/rigplane/web/radio_poller.py` is **empty**: the poller was not touched at all in the range. `core/command_dispatch.py` is likewise untouched (`git log --oneline e3d435dc..HEAD -- src/rigplane/core/command_dispatch.py` → empty).

**Two numeric corrections to the prior audit, both of which its own instruction to distrust would have caught.** (1) The owner's commissioning brief names "the 116 and 112 web dispatch arms". 116 reproduces exactly for `control.py`; **112 does not reproduce under any of the three rules run above** — the poller figure is 120 under the only rule that matches its arms (class patterns, not string literals), and 116/125 are the control.py figures. I did not find a rule that yields 112 and state the real counts instead. (2) The prior audit's "~230 `_COMMANDS` names (control.py:343–537)" is wrong: the frozenset holds **152** distinct name literals, by AST parse at both revisions. The "12 → ~230" gap in its ordered-steps section should read 12 → 152.

The 13 command names the 12 descriptors cover, by `grep -o 'name="[a-z_0-9]*"'` over `_COMMAND_DESCRIPTORS`: `set_af_level`, `set_antenna_1`, `set_antenna_2`, `set_att`, `set_attenuator_level`, `set_civ_output_ant`, `set_repeater_shift`, `set_rf_gain`, `set_rx_antenna`, `set_rx_antenna_ant1`, `set_rx_antenna_ant2`, `set_squelch`, `set_tuner_status` — levels, antennas, tuner, repeater shift, exactly as reported.

Verdict stands: **A (displacement), migration incomplete**, rank diverged. Nothing in this range advanced it.

## cp-F2 — receiver/VFO validation, four seats, one a byte-level copy — **OPEN, UNCHANGED**

Observation: `grep -rn "def _ensure_receiver_supported\|def _require_receiver\|def _check_single_receiver\|def _resolve_target_vfo\|def _receiver_index_for" src/` returns the same five definition sites the prior audit named — `runtime/_dual_rx_runtime.py: DualRxRuntimeMixin._require_receiver`, `web/radio_poller.py: RadioPoller._ensure_receiver_supported`, `web/handlers/control.py: ControlHandler._ensure_receiver_supported`, `rigctld/handler.py: _resolve_target_vfo` / `_receiver_index_for`, `backends/yaesu_cat/radio.py: _check_single_receiver`.

Observation: the poller copy remains byte-identical to the runtime original — same guard (`self._profile.supports_receiver(receiver)`), same `CommandError` message string interpolating `operation`, `receiver`, `self._profile.model` and `self._profile.receiver_count`. Read both bodies at this revision.

`git log --oneline e3d435dc..HEAD -- src/rigplane/web/radio_poller.py src/rigplane/runtime/_dual_rx_runtime.py` → **empty**. Verdict stands: **A**, required surface exists, fix class consolidate, actionable and small. Attribution: not applicable — nothing closed it.

## cp-F3 — TX-write policy seats — **STANDS; NO CLOSURE WAS DUE**

The prior verdict was C-today / A-in-ownership with fix class "none beyond executing the accepted 2026-09-01 ADR". `git log --oneline e3d435dc..HEAD -- src/rigplane/runtime/tx_interlock.py src/rigplane/core/tx_interlock_contract.py src/rigplane/runtime/managed_tx_authority.py` → **empty**. The ADR rows remain pending; the seats remain per-consumer. Nothing to attribute.

## cp-F4 — serialization / mutual exclusion — **STANDS; NO CLOSURE WAS DUE**

Prior verdict: C for standalone consumers, already-shared for the queue primitive, fix class none. `git log --oneline e3d435dc..HEAD -- src/rigplane/runtime/_poller_types.py` → **empty**. Correctly located, unchanged. Its one flagged unknown — cross-connection write ordering on standalone rigctld — remains **unknown**; nothing in this range bears on it.

## cp-F5 — value validation: no single seat, runtime internally inconsistent — **PARTLY CLOSED**

**Attribution: bucket (b)** throughout.

Closed (observation, `runtime/radio.py: IcomRadio.set_rf_power` read at this revision):

```python
if not 0 <= level <= 255:
    raise ValueError(f"RF power must be 0-255, got {level}")
self._check_connected()
self._require_capability("power_control", operation="set_rf_power")
```

Both the range check and the capability check the prior audit found missing are present, matching `set_rf_gain` / `set_af_level` / `set_squelch`. Established by `git log --oneline -S'def set_rf_power' e3d435dc..HEAD -- src/rigplane/runtime/radio.py` → `5f565904` #3490 (*validate Icom RF power at the runtime entry like its sibling levels*).

Closed, but by a different mechanism than the prior audit proposed: the rigctld RFPOWER branch. The prior audit asked for a clamp; `rigctld/handler.py: _RigctldCommandExecutor._execute_set_level` instead **rejects** out-of-domain input before any radio call, citing MOR-2480 in the branch comment — `if not 0.0 <= value <= 1.0: return HamlibError.EINVAL`. Same commit `5f565904` #3490 (`git log --oneline -S'MOR-2480' e3d435dc..HEAD`). Rejection is the stronger outcome and removes the prior audit's "medium confidence" end-to-end divergence inference entirely; its falsifier (encoder behaviour above 255) is now moot on this path because the encoder is never reached.

Closed: the `set_cw_pitch` asymmetry. `runtime/radio.py: IcomRadio.set_cw_pitch` now calls `profiles/control_domain.py: encode_legacy_control`, which raises `ValueError` when *display* is outside `[display_min, display_max]` read from the profile and "never falls back to a code constant" (its own docstring). `IcomRadio.get_cw_pitch` symmetrically calls `decode_legacy_control`. Attribution `d72cb8ef` #3494 (decode) and `f28d0453` #3495 (encode).

**Still open:** the headline — "no single seat" — stands. `cli/__init__.py: _cmd_levels` retains the hardcoded loop `if val is not None and not 0 <= val <= 255` over `--nr/--nb/--mic-gain/--drive-gain/--comp-level`, and `cli/__init__.py: _cmd_power` its own `if not 0 <= args.value <= 255`. `git log --oneline e3d435dc..HEAD -- src/rigplane/cli/__init__.py` → empty. The consumer-side seats in `web/handlers/control.py: _consume_normalized_level_unit` and `profiles/control_domain.py: validate_control_raw_value` are unchanged.

Net: the prior audit's two named micro-fixes both landed; the gap verdict (**B**) survives for the CLI and for the general absence of one seat.

## cp-F6 — three optimistic/pending mirrors beside the canonical pipeline — **MOVED, NOT CLOSED**

One element moved. The prior audit's `rigctld/handler.py: _PendingRigState` class is **gone**: `grep -rn "_PendingRigState" src/ tests/` returns **0 hits**. `rigctld/handler.py: _record_pending_overlay` survives with four call sites in the same module. Attribution for the class removal: **bucket (b)**, `9056a609` #3496 (same commit as cp-D1's second stage).

Everything else is unchanged: `git log --oneline e3d435dc..HEAD -- src/rigplane/core/command_service.py src/rigplane/core/state_store.py` → **empty**, and `web/radio_poller.py` is untouched, so both the compat-mirror writes in `RadioPoller._execute_unlocked` and `RadioPoller._apply_compatibility_mirror` remain. The prior verdict **A → migration incomplete** stands, with one fewer mirror.

Inference (labelled): because `_record_pending_overlay` remains and the class it fed does not, the rigctld pending-overlay path now writes somewhere else. I did not trace its new target and do not assert one; that is **unknown** here and is not needed for the closure verdict.

## cp-F7 — capability check: protocol shared, enforcement seats local and uneven — **OPEN, UNCHANGED**

`web/handlers/control.py: ControlHandler._ensure_capability` is the only remaining `_ensure_capability` definition in `src/` (`grep -rn "def _ensure_capability" src/`, 1 hit), exactly as before; the poller's silent-no-op path is untouched with the poller file. `core/command_dispatch.py`'s bind seat still covers 12 descriptors. Verdict stands: **already-shared (primitive) / B (mandated seat missing)**, depends on cp-F1.

## cp-F8 — error mapping per consumer protocol — **STANDS; HEALTHY**

Prior verdict C, fix class none, actionable no. No file in its element list changed in a way that affects it. Nothing to attribute; correctly located.

---

## cc-D1 — `filter-controls.ts` exported conversion surface with no external consumers — **CLOSED**

**Attribution: bucket (a)** — `89c835fb` #3510 (*unexport the seven filter-conversion symbols nothing imports*), `git show --stat`: one file, `frontend/src/lib/radio/filter-controls.ts`, 7 insertions and 7 deletions — the `export` keyword removed from exactly seven symbols and nothing else.

Verification at this revision, per symbol, by `grep -c "export .*<symbol>" frontend/src/lib/radio/filter-controls.ts`: `FILTER_BIPOLAR_MIN` 0, `FILTER_BIPOLAR_MAX` 0, `FILTER_WIDTH_MIN` 0, `FILTER_WIDTH_MAX` 0, `FILTER_WIDTH_STEP` 0, `controlRawToDisplay` 0, `controlDisplayToRaw` 0. All seven are now module-private; the function bodies survive and their internal consumers (`legacyNrLevelContract`, `nrRawToDisplay`, `nbDepthRawToDisplay`, `clampToBipolarRange`, `clampFilterWidth`) are unaffected.

This is the **only** prior finding closed by bucket (a), and the fix taken is precisely the one the prior audit's `Fix class` field specified ("delete (the export, not the function bodies)"), including its Pro-import caveat, which unexporting resolves conservatively.

Confidence: high. Falsifier: a Pro extension or skin importing those names from `$lib/radio/filter-controls` would now fail to build — which converts the prior audit's unknowable out-of-repo guard into a loud failure rather than a silent one.

## cc-F1 — Python↔TS exact control-domain mirror, pinned by hand-copied vectors — **OPEN, UNCHANGED**

The prior finding's verdict was C (sanctioned dual implementation) with one actionable item: replace the hand-mirrored vectors with a mechanically shared fixture. **That item did not land.** `tests/test_control_domain_math.py` still opens with "Python port of the TypeScript control-domain exact-math vectors (MOR-2472). Every case below mirrors a case in `frontend/src/lib/radio/__tests__/control-domain.test.ts` with the same inputs and expected outputs" — read at this revision.

Steelman considered and rejected: `tests/fixtures/control-domain-controls.json` **is** consumed by both suites — `tests/test_web_api_contract.py` and `frontend/src/lib/types/__tests__/capabilities.control-domains.test.ts` import it (`grep -rn "control-domain-controls" tests/ frontend/ scripts/`). But it is not the missing artefact: `git log --oneline -1 -- tests/fixtures/control-domain-controls.json` dates it to `ae87fd2e` #2679 (2026-08, MOR-1718), it predates `e3d435dc`, and it pins the **capabilities payload shape**, not the arithmetic vectors. The math pin remains hand-copied.

The file grew by 1,069 lines in the range (`git diff --stat`), which is the Yaesu/legacy-domain migration work adding cases — more hand-mirrored vectors, not fewer. Verdict stands: **C with an enforcement gap in the pin**, actionable and small.

## cc-F2 — rigctld level conversion: rig facts hardcoded in the adapter — **PARTLY CLOSED**

**Attribution: bucket (b)** for every part that closed — `6e45dbfc` #3486 (IF shift, CW pitch, notch), `0f315aa5` #3491 (NR), `7625924b` #3498 (ATT steps, NB scaling), `f50aaf5a` #3500 (manual-notch validation). Established by `git log --oneline -S'_set_snapped_level' e3d435dc..HEAD` and `git log --oneline -S'ControlDomainCapable' e3d435dc..HEAD`, which return exactly those four commits.

The missing surface the prior audit named now exists: `core/radio_protocol.py: ControlDomainCapable`, with `snap_control_display`, `decode_control_raw` and `control_display_bounds` — synchronous, wire-free, documented to return `None` when the radio publishes no domain "instead of each caller re-deriving the profile math". `rigctld/routing.py: YaesuRouting.set_level` now routes NB, NR, NOTCHF, IFSHIFT and CWPITCH through `_set_snapped_level` over that surface, and `YaesuRouting.get_level` through `_domain_level_fraction`.

**The headline divergence is closed.** The prior audit's concrete case was `rigs/ftx1.toml [controls.nr_level]` declaring raw/display 0..10 while routing clamped hamlib NR to 0..15, so `L NR 1.0` wrote an off-domain 15. Read at this revision: `rigs/ftx1.toml [controls.nr_level]` still declares `raw_max = 10` / `display_max = 10`, and the NR branch now asks `radio.control_display_bounds("nr_level")` first and only falls back to `max(0, min(15, round(value * 15)))` when the radio publishes no domain — the fallback carries the comment "the radio's own domain decides the band it maps onto (FTX-1: 0–10, per the CAT manual — not a code constant)". The FTX-1 is a `ControlDomainCapable` backend, so it takes the domain path.

**What remains open**, and it is two things, both observation:

1. **PREAMP is untouched.** `rigctld/handler.py: _PREAMP_IDX_TO_DB` is still the module-level constant `[0, 12, 20]` (`rigctld/handler.py:217`, a bare constant line with no enclosing symbol — the assignment `_PREAMP_IDX_TO_DB: list[int] = [0, 12, 20]`), read by `_execute_set_level`'s nearest-dB snap and by the get-level projection. `git log --oneline -S'_PREAMP_IDX_TO_DB' e3d435dc..HEAD` → **empty**. The prior audit's ATT/PREAMP pair — carried forward from the 2026-08-30 audit's F1 — is now **half** closed: ATT reads `self._attenuator_db_steps()` from radio-published data (#3498), PREAMP still reads the code constant that matches no profile.
2. **The surface is Yaesu-only.** `grep -n "def snap_control_display\|def decode_control_raw\|def control_display_bounds"` over `src/rigplane/runtime/radio.py`, `src/rigplane/backends/yaesu_cat/radio.py` and `src/rigplane/core/radio_protocol.py` finds the three implementations **only** in `backends/yaesu_cat/radio.py`. `runtime/radio.py: IcomRadio` does not implement `ControlDomainCapable`. Inference, labelled as such: for Icom radios the `isinstance` gates in `rigctld/routing.py` and `web/handlers/control.py` fall through to the legacy raw path. The prior audit's own falsifier — "#3486 already covering all four levels" — is therefore answered *no* on two axes: PREAMP, and the whole Icom family.

Verdict: **B, partly closed.** The surface exists and has consumers; migration incomplete on PREAMP and on the Icom backend.

## cc-F3 — Icom CW-pitch float scale written twice in Python — **CLOSED**

**Attribution: bucket (b)** — `d72cb8ef` #3494 (*decode CW pitch and key speed through the profile control domain*) and `f28d0453` #3495 (*encode …*).

Observation: `grep -rn "_cw_pitch_from_level\|_cw_pitch_to_level" src/ tests/` returns **1 hit**, and it is a prose line in `tests/test_control_domain_math.py`. Both helper definitions are gone from `commands/levels.py`; its `set_cw_pitch` docstring now says the Hz-to-level conversion "is the caller's profile domain (profiles/control_domain.py: encode_legacy_control)". The retyped inline copy in `runtime/radio.py: IcomRadio.get_cw_pitch` is gone, replaced by `decode_legacy_control(self._profile.controls, "cw_pitch", level)`.

The single seat is `profiles/control_domain.py: decode_legacy_control` / `encode_legacy_control`, computing over `Fraction` with the profile's declared `encode_rounding` (`ceil` or `nearest_half_down`). Consumers at this revision, by `grep -rn` excluding the definitions: `runtime/radio.py` (cw_pitch and key_speed, both directions) and `runtime/_civ_rx.py` (two decode sites).

This also resolves the prior audit's divergence: the `math.ceil` / round-to-5-Hz asymmetry it found between `_cw_pitch_to_level` and `_cw_pitch_from_level` cannot persist, because both directions now derive from one profile band with one declared rounding rule. The prior audit's harder half — the profile-format question for a non-representable 600/255 step — was answered by adding a legacy rational band with an explicit rounding rule, rather than by extending the exact-lattice format. That is a design answer, not a deferral.

Confidence: high. Falsifier: a CI-V capture showing the radios quantize the displayed pitch differently from `nearest_half_down` over the profile band — which would make the profile data wrong, not the mechanism.

## cc-F4 — PBT/NR state units: server publishes raw, browser converts at render time — **OPEN BY OWNER DECISION, UNCHANGED**

Prior verdict A with fix class "design (wire-format decision)" and `Actionable: no — requires owner sign-off". No such decision landed: `frontend/src/lib/types/state.ts` still carries `nrLevel`/`pbtInner`/`pbtOuter` as raw, and the conversion cluster still lives in the browser.

Production consumers of the one shipped `frontend/src/lib/radio/filter-controls.ts: pbtRawToHz` at this revision, by `grep -rl "pbtRawToHz" frontend/src` (17 files) minus the defining module and the 8 `*.test.ts` files = **8 production files**: `band-plan.ts`, `panel-adapters.ts`, `radio-view-model-adapter.ts`, `scope-passband-display.ts`, `panel-props.ts`, `FilterSurface.svelte`, `radio-view-model.ts`, `audio-spectrum-renderer.ts`.

**Correction to the prior ruling, and it matters for the method's step 0.** The prior audit listed those same eight files as *consumers* of the shared `pbtRawToHz`. That was wrong for one of them. At `e3d435dc`, `components-v2/panels/audio-scope/audio-spectrum-renderer.ts` **defined its own** `export function pbtRawToHz(raw, center = 128, maxHz = 1200)` — verified by `git show e3d435dc:<path> | grep -n "pbtRawToHz"`, which shows the definition and three local call sites and **no import** from `$lib/radio/filter-controls`. The prior audit matched the name at its usage sites and did not locate its definition site; it therefore recorded a ninth copy of the mechanism as a consumer of the shared one, and undercounted its own finding. Bucket (a) `6bf81ed7` #3508 closed that copy: the renderer now imports `pbtRawToHz` from `$lib/radio/filter-controls` and takes the range as a threaded argument rather than reaching the capabilities store, with isolated tests installing a non-default range so a renderer that drops it goes red.

So: cc-F4's topology verdict is unchanged and open, but the cluster is one implementation smaller and its worst member — a copy with hardcoded `center = 128, maxHz = 1200` defaults — is gone. Bucket (a), `6bf81ed7` #3508.

## cc-F5 — `handlers/control.py` fabricated 600 Hz CW-pitch default — **CLOSED**

**Attribution: bucket (b)** — `8b49ed98` #3489 (*stop auto-tune from assuming a 600 Hz CW pitch*).

Observation: `grep -n "600" src/rigplane/web/handlers/control.py` returns **0 hits** at this revision. The `cw_auto_tune` handler now reads `cw_pitch = state.cw_pitch` and branches on `if cw_pitch <= 0:` to emit `"cw_pitch": None` rather than fabricating a value — which is the behaviour the frontend already refused under MOR-1409 A12, so the two sides now agree. The prior audit's note that the fix is behaviour-visible was respected: `tests/test_cw_auto_tune_wiring.py` gained 66 lines in the range.

Confidence: high. Falsifier: an owner ruling that auto-tune must assume 600 Hz when the pitch is unobserved — none found in `docs/` at this revision.

---

# Part 2 — Fresh findings at `94361667`

Ranked below the closure verdicts, as commissioned. Deletions first, per the method.

## Deletions

### D2 — `IcomRadio._civ_retry_slice_timeout`: dead
```
Verdict:          dead
Elements:         runtime/radio.py: IcomRadio.__init__ (assignment
                  `self._civ_retry_slice_timeout: float = ...`);
                  runtime/_runtime_protocols.py (the protocol's field
                  declaration `_civ_retry_slice_timeout: float`)
Consumers:        none
Written / read:   AST sweep over all 751 .py files in src/ + tests/
                  (parse failures: 0) counting ast.Attribute in Load vs
                  Store context: 1 Store, 0 Load. Confirmed by literal
                  `grep -rn "_civ_retry_slice_timeout" src/ tests/` → 2
                  hits, both the assignment and the protocol declaration;
                  `grep -rn "retry_slice" src/ tests/` → the same 2.
Guards checked:   dynamic access — searched literally; the sibling fields
                  `_civ_recovery_lock` and `_civ_recovery_wait_timeout`
                  ARE read dynamically (`getattr(self._host, "...")` in
                  runtime/_civ_rx.py) and are therefore live, so this
                  codebase does use that pattern; no getattr with a
                  computed/f-string name exists in src/rigplane/runtime/
                  (grep for `getattr([^,]*, *f"` → 0 hits; the two
                  variable-name getattrs are session_lifecycle.py
                  (task_attr, a task handle) and _poller_types.py (an int
                  type check), neither reaching this name). The one
                  `vars(radio)` site, runtime/managed_tx_composition.py:
                  install_managed_tx_composition, reads only the literal
                  key "_local_tx_work" — read and confirmed.
                  out-of-repo — underscore-private instance attribute; a
                  Pro subclass could read it and this repo cannot see
                  that. public API — no; absent from docs/.
                  tests-only — no; zero test readers either.
Collateral:       the protocol field declaration in
                  runtime/_runtime_protocols.py goes with it.
Depends on:       none
Confidence:       medium — high on the counts, reduced by the open-core
                  guard alone.
Falsifier:        any reader of `_civ_retry_slice_timeout`, including one
                  in rigplane-pro.
Fix class:        delete
```

### D3 — `IcomRadio._scope_activity_counter`: incremented, never read
```
Verdict:          dead
Elements:         runtime/radio.py: IcomRadio.__init__
                  (`self._scope_activity_counter: int = 0`);
                  runtime/_civ_rx.py (`self._host._scope_activity_counter
                  += 1`); runtime/_runtime_protocols.py (field
                  declaration)
Consumers:        none — the counter is initialised, incremented, and
                  never read.
Written / read:   AST sweep as above: 1 Store, 0 Load (an AugAssign
                  target is Store-only). Literal
                  `grep -rn "scope_activity" src/ tests/` → 8 hits, of
                  which 3 are this counter (init, increment, protocol
                  declaration) and 5 belong to the sibling
                  `_scope_activity_event`, which IS live: cleared and
                  awaited in runtime/_scope_runtime.py and set in tests.
Guards checked:   dynamic access — same literal search and same
                  computed-getattr negative as D2. out-of-repo — same
                  open-core caveat. public API — no. tests-only — no;
                  tests touch the event, not the counter.
Collateral:       the protocol field declaration.
Depends on:       none — independent of D2 and of every Part 1 finding.
Confidence:       medium, for the open-core guard only.
Falsifier:        a reader of the counter, or a diagnostic that reports
                  it by name.
Fix class:        delete
```

### D4 — `runtime/radio.py: _check_protocol_compliance`: defined, never called
```
Verdict:          dead
Elements:         runtime/radio.py: _check_protocol_compliance
                  (module-level function; docstring "Verify IcomRadio
                  satisfies all Radio protocol variants")
Consumers:        none
Written / read:   AST sweep: 0 Load of the name anywhere in src/ +
                  tests/. Literal `grep -rn "_check_protocol_compliance"
                  src/ tests/` → 1 hit, its own `def` line. Not called at
                  import time, not registered, not referenced in docs/.
Guards checked:   dynamic access — searched literally; no plugin or
                  entry-point registry in this module and no
                  computed-name getattr in src/rigplane/runtime/ (see
                  D2). out-of-repo — module-private by leading
                  underscore. public API — no. tests-only — no; it is not
                  a test and no test calls it.
Collateral:       its own import block of protocol names.
Depends on:       none
Confidence:       high — nothing but the open-core guard applies, and a
                  leading-underscore module function is the weakest
                  possible candidate for it.
Falsifier:        a caller, or a decision that it documents the protocol
                  set deliberately, in which case it is prose in
                  executable form and should be a test.
Fix class:        delete — but note it asserts something real; if the
                  intent is kept, it belongs in tests/, not as an
                  uncalled function. That choice is a human decision.
```

Sweep discipline, reported as counts per the method's step 3a. AST enumeration over `src/` + `tests/` (751 files, **0 parse failures** — asserted, not assumed), per module in the tract: `rigctld/handler.py` 17 self-attrs / 22 module constants / 100 functions, **0 dead**; `rigctld/routing.py` 3 / 4 / 24, 0 dead (the one flagged name, `YaesuRouting.set_state_observer`, is reached dynamically at `rigctld/handler.py` via `getattr(self._routing, "set_state_observer", None)` — a literal-grep-only sweep would have deleted it, and that is the cost of the guard); `profiles/control_domain.py` 0 / 9 / 29, 0 dead; `web/handlers/control.py` 17 / 4 / 83, 0 dead; `core/command_dispatch.py` 0 / 7 / 30, 0 dead; `runtime/radio.py` 111 / 9 / 305, **3 dead** (D2, D3, D4). Names that survived are not mentioned further.

## Consolidations

### F9 — measured PBT lattice conversion: a second raw↔Hz conversion with no production consumer
```
Verdict:          undetermined — declared in-flight, not dead
Rank:             parallel
Elements:         frontend/src/lib/radio/filter-controls.ts:
                  measuredPbtRawToHz, measuredPbtHzToRaw,
                  PBT_MEASURED_STEP_HZ — beside the shipped
                  pbtRawToHz / pbtHzToRaw in the same module
Consumers:        tests only. `grep -rn "measuredPbtRawToHz\|
                  measuredPbtHzToRaw\|PBT_MEASURED_STEP_HZ" frontend/src`
                  excluding the defining module returns hits in exactly
                  one file, frontend/src/lib/radio/filter-controls.test.ts
                  (literal search; the frontend has no dynamic $lib
                  import). Production consumers: 0.
Definition site:  filter-controls.ts, the same module that defines the
                  shipped conversion — so this is not displacement.
Divergence:       the two conversions answer the same question
                  differently by construction: pbtRawToHz is a linear
                  map over a published PbtRange; measuredPbtRawToHz is a
                  lattice quantized to PBT_MEASURED_STEP_HZ = 50 and
                  parameterized by filter width, returning null outside
                  it. Observation from reading both.
Prior ruling:     the commit itself, 966f315e #3514 (MOR-2497): "Step 1
                  only: no consumer, profile, or existing-conversion
                  changes" — an explicit, dated declaration that the
                  absent consumer is intended, not an oversight. This is
                  why the verdict is not "dead": the method's step 2
                  exists precisely to stop a half-landed migration being
                  reported as a gap.
In-flight:        yes — MOR-2497 step 2 was under way at this revision,
                  converting audio-spectrum-renderer.ts,
                  scope-passband-display.ts and
                  radio-view-model-adapter.ts, which is where a consumer
                  lands. Not yet on `main` at this revision and therefore
                  not read into evidence here.
Required surface: exists.
Depends on:       none. Do NOT delete on this evidence.
Confidence:       high on the consumer count; the verdict is
                  undetermined by intent, not by weak evidence.
Falsifier:        the step-2 consumer landing (closes it), or the
                  MOR-2497 line being abandoned (converts it to a
                  vestigial fork, delete side: the measured trio).
Fix class:        none now
Actionable:       no — re-check after MOR-2497 step 2.
```

### F10 — bucket (a)'s frontend work is a fourth consumer class of the same published-domain surface
```
Verdict:          already-shared
Rank:             parallel
Elements:         frontend/src/lib/runtime/props/panel-props.ts,
                  frontend/src/lib/runtime/commands/panel-commands.ts,
                  frontend/src/semantic/DspSurface.svelte,
                  frontend/src/components-v2/panels/lcd/AmberAfScope.svelte
Consumers:        the DSP and audio-scope surfaces, via the adapters.
Definition site:  the published control domain, i.e. the same
                  `[controls.*]` data that cc-F2's Python surface reads —
                  reached in the browser through the capabilities
                  payload, not through a second copy of the math.
Divergence:       none observed.
Prior ruling:     none needed; this is the direction the 2026-09-15
                  control-conversion audit's H4 recommendation named.
In-flight:        no.
Required surface: exists.
Depends on:       none
Confidence:       medium — established from commit stats and the
                  unexport/consumer greps above, not from reading all
                  four files end to end. Say so rather than imply a full
                  read.
Falsifier:        a hardcoded domain constant surviving in any of the
                  four files.
Fix class:        none
Actionable:       no — recorded so a later audit does not re-open it.
```

---

## Steelman (step 4), stated before the verdicts above were fixed

**The strongest case that cp-F1, cp-F2, cp-F6 and cp-F7 should not have moved, and that nothing is wrong.** These four are one programme, not four defects: cp-F2 and cp-F7 are explicitly `Depends on: F1` or foldable into it, and cp-F6 retires with F1's arms. cp-F1's own `Actionable` field says "expensive — treat as a multi-change programme, not one PR", against a hard guardrail of 10 files and 1000 changed lines. A range of 34 commits that closed four independent findings and advanced two, while leaving the one entangled programme alone, is exactly what a correctly sequenced line of work looks like. Deleting the web receiver-validation copy (cp-F2) before the descriptor bind owns the seat would move the check to a seat that is about to move again.

That case is strong and I accept it: **cp-F1/F2/F6/F7 being open is not evidence of neglect**, and this report does not treat it as such. What the case does not license is the inference that they are *closing*. They are not: the five counts in cp-F1 are identical to the digit, and `web/radio_poller.py` — the largest single element across three of the four — received not one commit in the range.

**The strongest case for the other side, on cc-F2.** One could argue cc-F2 should be called closed: the surface exists, the named divergence is gone, the remaining PREAMP constant is three integers. I reject that. `_PREAMP_IDX_TO_DB` is exactly the shape of defect the 2026-08-30 audit recorded and the 2026-09-15 audit re-recorded — a code constant standing in for profile data, diverging from every shipped profile — and it survived a change (`#3498`) that fixed its twin in the adjacent branch of the same function. A finding whose sibling was fixed beside it and which was not is more likely to be forgotten, not less. And the Icom gap is larger than PREAMP: the whole `ControlDomainCapable` surface has one implementer.

**Where the steelman wins outright:** cp-F3, cp-F4, cp-F8 and the Cleared list below. Those are healthy and are named as such.

---

## Weakest link

**cc-F2's second open limb — "the surface is Yaesu-only, so Icom radios fall through to the legacy raw path" — is the verdict most likely to be wrong, and it is an inference, not an observation.** What I established is narrow and solid: `grep` for the three `ControlDomainCapable` method definitions over `runtime/radio.py`, `backends/yaesu_cat/radio.py` and `core/radio_protocol.py` finds implementations only in the Yaesu backend. What I did **not** do is trace an Icom `L NR`/`L PREAMP` call end to end, and I did not search the other backend modules under `src/rigplane/backends/` for the same method names, nor check whether a mixin supplies them to `IcomRadio` under a different definition shape. `ControlDomainCapable` is a bare `Protocol` (not `@runtime_checkable` at its class line, unlike the capability protocols below it in the same file), which makes the `isinstance` gates in `rigctld/routing.py` worth reading before acting. Check first: `grep -rn "ControlDomainCapable" src/rigplane/backends/` and the `runtime_checkable` decoration of that protocol; then one Icom-profile path through `YaesuRouting.set_level`'s gate.

Second-weakest: **D2 and D3's open-core guard**. Both are underscore-private instance attributes with a clean literal and AST record, but this repository cannot see a Pro subclass reading them, and the same module demonstrably uses `getattr(self._host, "<literal>")` to read three of their siblings across the mixin boundary. Nothing I can run here closes that; it is why both carry medium confidence rather than high.

---

## Cleared

Examined at this revision and found healthy, by name:

- **`core/_state_cache.py: StateCache`** — the homonym that cp-D1's dead class shadowed. Live on both sides: written by `runtime/radio.py: IcomRadio.set_rf_power` and `tests/test_golden_protocol.py`, read by `runtime/_dual_rx_runtime.py` and `runtime/radio.py` via `is_fresh`. The rigctld deletion did not touch it, which is the correct outcome.
- **`profiles/control_domain.py`** — sweep clean (0 dead of 9 constants and 29 functions), and it is now the single seat for the legacy rational bands that cc-F3 found written twice. Correctly located in `profiles`, importable by `runtime` and `backends` under the layer order.
- **`core/radio_protocol.py: ControlDomainCapable`** — a genuinely new shared primitive, consumed by four independent call sites across `rigctld/routing.py`, `rigctld/handler.py` and `web/handlers/control.py`. The surface cc-F2 asked for exists and has consumers; only its coverage is incomplete.
- **`rigctld/handler.py` and `rigctld/routing.py` as modules** — dead-code sweep clean at this revision (0 of 17 attrs / 22 constants / 100 functions, and 0 of 3 / 4 / 24 respectively), after two deletion PRs. The one false positive, `YaesuRouting.set_state_observer`, is dynamically reached and correctly alive.
- **`web/handlers/control.py`** — sweep clean (0 of 17 attrs, 4 constants, 83 functions). Its open findings are architectural, not litter.
- **`core/command_dispatch.py`** — sweep clean; the 12 descriptors all bind. Small, not rotten: cp-F1 is about what has *not* migrated onto it, never about its own health.
- **`runtime/tx_interlock.py`, `runtime/managed_tx_authority.py`, `runtime/_poller_types.py: CommandQueue`** — untouched in the range and unchanged in verdict; correctly located per their charters (cp-F3, cp-F4).
- **Error mapping per wire protocol** (cp-F8) — one seat per consumer, as each protocol requires. Legitimately local.
- **`frontend/src/lib/radio/filter-controls.ts` export surface** — cc-D1 closed; the seven names are private and their internal consumers intact.
- **`frontend/src/components-v2/panels/audio-scope/audio-spectrum-renderer.ts`** — the private `pbtRawToHz` copy is gone and the range is threaded as an argument rather than reached from the capabilities store, with a test that goes red if it is dropped. This is the one place in the range where a *duplicate implementation* was removed rather than a dead symbol.
- **`tests/fixtures/control-domain-controls.json`** — genuinely shared between pytest and vitest. Not the vector artefact cc-F1 wants, but not a duplicate either.

---

*Written read-only at `94361667`; no test suite was run, no other file in this worktree was modified, and nothing was committed or pushed.*
