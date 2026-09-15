# Mechanism audit — control-value conversion (display ↔ raw) at `e3d435dc3b70ff1ed07bd8efce3e5e78a7bda46c`

**Method file read:** `.claude/skills/mechanism-audit/SKILL.md` (worktree copy, this revision). Steps 0–5 executed in order; per the audit contract this run is strictly read-only, so the method's archiving step was **not** performed.

**Scope:** `src/rigplane/profiles/control_domain.py`, `frontend/src/lib/radio/control-domain.ts`, `frontend/src/lib/radio/filter-controls.ts`, `src/rigplane/commands/levels.py` (cw-pitch helpers), `src/rigplane/runtime/radio.py` (cw-pitch decode), `src/rigplane/rigctld/{handler,routing,server}.py`, `src/rigplane/cli/__init__.py`, `src/rigplane/web/{server,handlers/control,radio_poller}.py`, `rigs/*.toml [controls.*]`.

**Observed facts 1–7 verified:** (1) control_domain.py added 2026-09-15 in #3485 (git log, single commit). (2) control-domain.ts added 2026-08-15 in #2680 (MOR-1722). (3) `web/server.py:3161` and `:3672` publish `profile.controls` verbatim into two capabilities payloads. (4) filter-controls.ts holds the named symbols; note `controlRawToDisplay`/`controlDisplayToRaw` now have **zero external callers** (see D1). (5) `handlers/control.py:2215` (`_cw_auto_tune` region) contains the `else 600` fallback. (6) rigctld calls radio `set_*` directly (`handler.py: _execute_set_level`, `routing.py: YaesuRouting.set_level`); `cli/__init__.py:3731` constructs `RigctldServer(radio, config)` bare and `:3934-3939` with `command_queue=server.command_queue`. (7) CLI subcommands verified at `cli/__init__.py:762,1013,1023,1077,1160,1231`. (8) `rigctld/LAYER.md:31-32,44-45`: "Allowed dependencies: `core`, `commands`, `runtime` … No … `profiles`". PR #3486 is not on this revision and no network was used — everything about it is reported-as-heard, marked unknown where it matters. (9) The quoted "prior audit" is **not in-tree** (searched `.claude/audits/`, 25 files, and `docs/`); the in-tree trace of its ruling is `control_domain.py` docstring ("must stay semantically identical", MOR-2472) and `tests/test_control_domain_math.py:1-7`.

---

## DELETIONS (ranked first; independent of design decisions)

### D1 — `filter-controls.ts` exported conversion surface with no external consumers
```
Verdict:          dead (export surface only; the functions themselves are alive internally)
Elements:         filter-controls.ts: FILTER_BIPOLAR_MIN, FILTER_BIPOLAR_MAX,
                  FILTER_WIDTH_MIN, FILTER_WIDTH_MAX, FILTER_WIDTH_STEP,
                  controlRawToDisplay, controlDisplayToRaw
Consumers:        controlRawToDisplay/controlDisplayToRaw — internal only
                  (legacyNrLevelContract, nrRawToDisplay, nbDepthRawToDisplay,
                  nbDepthDisplayToRaw); the five constants — internal only
                  (clampToBipolarRange, clampFilterWidth defaults).
Written / read:   per-symbol grep over frontend/src excluding the module and all
                  tests: prod_files=0 other_tests=0 for all seven (FILTER_WIDTH_STEP:
                  one reference inside filter-controls.test.ts). Internal reads
                  verified by reading the module.
Guards checked:   dynamic access — none (literal grep; no getattr-style dynamic
                  imports exist in the frontend bundle for $lib modules — observation
                  from grep for 'import(' in frontend/src/lib: not run per-symbol).
                  out-of-repo — Pro injects extensions into the frontend bundle and
                  *could* import $lib internals (open-core-policy.md §6 names
                  local-extensions host-api as the sanctioned contract; these symbols
                  are not in it — observation). public API — not listed in
                  docs/api/public-api-surface.md (observation). tests-only — no.
Collateral:       none beyond the export keyword; filter-controls.test.ts references
                  FILTER_WIDTH_STEP once.
Depends on:       none
Confidence:       high on the counts; the *action* (unexport vs leave) is undetermined
                  because of the Pro-import guard.
Falsifier:        a Pro extension or skin importing controlRawToDisplay/controlDisplayToRaw
                  from '$lib/radio/filter-controls'.
Fix class:        delete (the export, not the function bodies)
```
Note: PR #3484 (2026-09-15) already deleted dead conversion code here — this is the residue its sweep left. Observation.

---

## CONSOLIDATIONS (ranked by debugging cost)

### F1 — exact control-domain arithmetic: two deliberate mirrors, pinned by hand-copied test vectors
```
Verdict:          C — legitimately local (sanctioned dual implementation), with an
                  enforcement gap in the pin
Rank:             parallel (identical behaviour by construction; maintenance + drift risk)
Elements:         profiles/control_domain.py: decode_control_domain,
                  encode_control_domain, quantize_control_domain,
                  validate_control_raw_value;
                  frontend/src/lib/radio/control-domain.ts: decodeControlDomain,
                  quantizeControlDomain, encodeControlDomain
Consumers:        Python — decode+validate: backends/yaesu_cat/radio.py:1607,1746,2211,2708;
                  rig_loader.py re-export; encode/quantize: tests only (see In-flight).
                  TS — RitXitScanSurface.svelte:115, RitXitPanel.svelte:6,
                  panel-props.ts:25, filter-controls.ts:11 (all production).
Definition site:  each language's own module; no shared vector artifact.
Divergence:       none observed between the two exact implementations (observation:
                  compared regex anchors — Python \Z vs TS $ with the parity comment
                  at control_domain.py:35-38; tie rules nearest_ties_down/up identical
                  at control_domain.py:403-406 / control-domain.ts:208-209; floor-div
                  semantics aligned via floorDivide at control-domain.ts:49-52;
                  None/null symmetry throughout). Two asymmetries inside Python:
                  (i) validate_control_raw_value has no TS twin and does not apply the
                  2^53 safe-integer bound that _raw_index enforces
                  (control_domain.py:88-93 vs :149-154) — a hypothetical domain with
                  bounds beyond 2^53 validates but cannot decode; (ii) encode/
                  quantize have no production caller on either side.
Prior ruling:     MOR-2472, quoted in control_domain.py:3-9 ("must stay semantically
                  identical") and test_control_domain_math.py:1-7 ("a divergence here
                  is a contract break"). Dated 2026-09-15 (#3485).
In-flight:        PR #3486 (reported; not inspectable on this revision — unknown)
                  adds a Radio-level capability protocol for snapping display values,
                  which would give encode/quantize their first production consumer.
Required surface: exists (both modules); what does not exist is a mechanically shared
                  vector file — test_control_domain_math.py hand-mirrors
                  control-domain.test.ts case by case (observation: both files read;
                  no JSON/fixture shared between them).
Depends on:       none
Confidence:       high
Falsifier:        a vector that passes one suite and fails the other (would prove the
                  hand-mirror pin already leaked), or PR #3486 landing a Radio-level
                  protocol that makes server-side encode the only consumer path.
Fix class:        consolidate the *vectors* (shared fixture consumed by pytest+vitest),
                  not the implementations
Actionable:       yes — the vector-file consolidation is small and design-free
```

### F2 — rigctld level conversion: rig facts hardcoded in the adapter, diverged from the published domains
```
Verdict:          B — gap: the Radio layer lacks a display-value surface, and
                  rigctld is banned from reading the profiles that hold the data
Rank:             diverged
Elements:         rigctld/routing.py: YaesuRouting.set_level (:344 NR clamp
                  0..15; :349-363 CWPITCH clamp 300+75*10; _CW_PITCH_BASE/_STEP :137-138);
                  rigctld/handler.py: _execute_set_level (:2719-2723 hamlib 0-1 →
                  ×255; :2739-2741 CWPITCH round() with no clamp; :2743-2752
                  PREAMP snap to [0,12,20]; :2753-2760 ATT snap to [0,6,12,18])
Consumers:        handler.py: _cmd_set_level → _execute_write (command-intent
                  admission, not domain math); routing used only when the radio is
                  RigctldRoutable (core/radio_protocol.py:1041-1047;
                  yaesu_cat/radio.py:3098-3134).
Definition site:  the *data* is defined in rigs/*.toml [controls.*] and published
                  verbatim (web/server.py:3161) — but unreachable from rigctld by rule.
Divergence:       observed, concrete — rigs/ftx1.toml [controls.nr_level] declares
                  raw/display 0..10, while routing.py:344 clamps hamlib NR to 0..15
                  and backends/yaesu_cat/radio.py:1527 (set_nr_level) does no domain
                  validation, so `L NR 1.0` writes an off-domain 15 to the FTX-1 that
                  the web path (frontend encodes 0..10 via the exact domain) can never
                  produce. CWPITCH constants (300..1050 step 10) currently match
                  ftx1.toml [controls.cw_pitch] — in sync today, drift-prone tomorrow.
                  ATT/PREAMP tables diverge from every profile (ic7300 [0,20],
                  ic7610 [0..45 step 3], ftx1 [0,1]) — already documented as the
                  2026-08-30 audit's F1; still unfixed on this revision (observation:
                  handler.py:2743-2760 unchanged).
Prior ruling:     rigctld/LAYER.md:31-32,44-45 (no profiles import — quoted above,
                  2026-04-29 modularization plan §3 referenced). Prior audit
                  .claude/audits/2026-08-30-mechanism-audit-command-path.md F1
                  (verdict A/diverged, same ATT/PREAMP evidence, "Required surface:
                  … a profile-declared control domain per level name … consulted by
                  both branches").
In-flight:        PR #3486 (reported) — Radio-level capability protocol so rigctld
                  can ask the radio to snap display values without importing
                  profiles. This is precisely the missing surface; unknown contents.
Required surface: a Radio/capability-protocol method set (decode/snap/encode/
                  validate per control name), implemented over profiles.
                  control_domain.py in the backend, exposed without layer violation.
Depends on:       F1's Python module (already landed); the protocol design decision.
Confidence:       high on the divergence (ftx1.toml vs routing.py:344 both read);
                  unknown on whether #3486 covers NR/ATT/PREAMP or only CWPITCH-class
                  snapping.
Falsifier:        FTX-1 bench evidence that the CAT NR command accepts 11-15 legally
                  (then the profile, not the clamp, is wrong); or #3486 already
                  covering all four levels.
Fix class:        design (surface), then consolidate callers onto it
Actionable:       yes, after #3486 lands — do not start a second surface in parallel
```

### F3 — Icom CW-pitch scale: float formula written twice in Python, with the same numbers also declared as profile data
```
Verdict:          A — displaced (the scale belongs to the profile/control layer;
                  it is written in commands/ and duplicated inline in runtime/)
Rank:             diverged (mild: encode/decode are not exact inverses)
Elements:         commands/levels.py: _cw_pitch_from_level (:48-49),
                  _cw_pitch_to_level (:52-55, used by set_cw_pitch :423-437);
                  runtime/radio.py:2586 (IcomRadio.get_cw_pitch) — the identical
                  formula retyped inline; rigs/x6100.toml [controls.cw_pitch]
                  (:227-232) declaring raw 0-255 / display 300-900 as data.
Consumers:        commands/levels.py set_cw_pitch ← runtime/radio.py:2588-2592
                  (set_cw_pitch); runtime get_cw_pitch ← web/radio_poller.py:2652
                  region (state) and rigctld handler _GET_LEVEL_INT CWPITCH
                  (handler.py:200).
Definition site:  the numbers live in three places; none is authoritative.
Divergence:       observed — _cw_pitch_to_level uses math.ceil while
                  _cw_pitch_from_level rounds to 5 Hz; raw 2 and raw 3 both decode
                  to 305 Hz (float evaluation of the two formulas), so encode(305)=3
                  but decode(3)=decode(2)=305: the pair is not an exact involution.
                  The exact-domain machinery would return null here rather than
                  fabricate — the legacy float path accepts the collision. The
                  600/255 step is not representable as a finite decimal
                  display_step, so [controls.cw_pitch] for Icom stays a legacy
                  ControlRange (no mapping) — observation from x6100.toml and
                  control_domain.py's exact-lattice requirements.
Prior ruling:     none found for the float formula itself. x6100.toml:219-226
                  records a deliberate unfixed display-range divergence
                  (MOR-2018, X6100 300-900 vs X6200 400-1200) — a data ruling,
                  not a mechanism ruling.
In-flight:        #3485 migrated only the Yaesu CW pitch onto the exact domain;
                  Icom untouched (observation: git show of #3485 files limited to
                  profiles/yaesu path; encode side of control_domain.py unconsumed).
Required surface: for Icom, either a rational-step domain extension or an
                  explicit "approximate mapping" control kind; today none exists.
Depends on:       F1 (the module exists); a profile-format decision (expensive,
                  wire-visible).
Confidence:       high on the duplication and the ceil/round asymmetry; medium on
                  whether unifying is worth the profile-format cost.
Falsifier:        a CI-V capture showing the radios' own 5 Hz display quantization
                  differs from round-to-5 (then the formula is a wire fact, still
                  duplicated but not consolidatable into exact domains).
Fix class:        consolidate the two Python copies (mechanical); design for the
                  profile-format question
Actionable:       the runtime/radio.py:2586 inline copy can call
                  commands.levels._cw_pitch_from_level today — yes, cheap. The rest: no.
```

### F4 — PBT/NR state units: server publishes raw in state, eight browser files convert at render time
```
Verdict:          A — displaced decode: the display decoding of state the server
                  owns lives in the presentation layer
Rank:             displaced (single conversion cluster, no observed intra-cluster
                  divergence)
Elements:         frontend filter-controls.ts: pbtRawToHz/pbtHzToRaw (:95-113,
                  IC-7610 PBT_DEFAULTS :25), CONTROL_DEFAULTS (:132-135),
                  nrRawToDisplay/controlRawToDisplay family; consumed by 8 production
                  files (pbtRawToHz: FilterSurface.svelte, radio-view-model.ts,
                  band-plan.ts, panel-props.ts, scope-passband-display.ts,
                  panel-adapters.ts, radio-view-model-adapter.ts,
                  audio-spectrum-renderer.ts — grep counts in transcript).
                  State contract: frontend/src/lib/types/state.ts:175-177
                  nrLevel/pbtInner/pbtOuter (raw), generated from
                  web/state_schema.py (scripts/gen_state_types.py, state-types-gate).
Consumers:        all listed helpers have live consumers (sweep table under D1).
Divergence:       versus cw_pitch/if_shift, whose state and commands already carry
                  display units (handlers/control.py cases set_cw_pitch/set_if_shift
                  take Hz; yaesu radio decodes state cw_pitch via
                  decode_control_domain — poller.py:1609-1610) — the wire is MIXED:
                  some commands display-unit, some raw-unit, per control. Observation.
Prior ruling:     none found governing state units. MOR-1280/1284/1290/1291 (cited in
                  filter-controls.ts comments) progressively de-globalized the caps
                  lookup but preserved the raw-unit contract.
In-flight:        none found for state units.
Required surface: server-side decode into state + display-unit commands; exists
                  partially (cw_pitch path) — the missing piece is doing it for
                  pbt/nr/notch and committing the state-contract break.
Depends on:       F2's Radio-level surface for non-web consumers; the state-schema
                  break must be budgeted as a contract change (consumer contracts,
                  fixtures, generated types).
Confidence:       high on the topology; the *decision* is an owner call, not derivable.
Falsifier:        a recorded ruling that state must stay raw for bandwidth or
                  backward-compat reasons (none found); or Pro extensions depending
                  on raw state fields (unknowable from this repo).
Fix class:        design (wire-format decision), then consolidate
Actionable:       no — expensive-to-reverse contract; requires owner sign-off (this
                  is the (d) item of the decision brief).
```

### F5 — `handlers/control.py:2215` fabricated CW-pitch default (600 Hz)
```
Verdict:          C-adjacent minor displacement; a one-line local default in the
                  cw_auto_tune handler
Rank:             parallel (trivial)
Elements:         web/handlers/control.py: cw_auto_tune — `cw_pitch = state.cw_pitch
                  if state.cw_pitch else 600`
Consumers:        cw_auto_tune only.
Divergence:       the frontend explicitly refuses exactly this fabrication
                  (panel-props.ts:819-821, MOR-1409 A12: "no fabricated 600 Hz pitch
                  … stand-ins for an unobserved CW receiver"), and the profile
                  domains put 600 mid-range for Icom (300-900) but off-lattice-typical
                  for FTX-1 (300-1050 step 10 — 600 is on-lattice; observation).
                  The default is also falsy-zero-based: cw_pitch 0 can never be
                  legitimate (domains start at 300), so behaviour is safe today.
Prior ruling:     MOR-1409 (frontend side only).
In-flight:        none.
Required surface: exists — read the domain's display_origin, or refuse when unknown.
Depends on:       none.
Confidence:       high.
Falsifier:        a ruling that auto-tune must assume 600 Hz when unobserved.
Fix class:        consolidate (one line) — but note it is behaviour-visible; test first.
Actionable:       yes, small.
```

---

## Question B — why the consumers differ (fact 5 vs 6/7)

**E1 (standalone operation forced direct calls): partly supported.** Observation: CLI `_cmd_levels` (`cli/__init__.py:3384-3434`) calls `radio.set_*` directly with hardcoded 0-255 checks; `rigplane serve` builds `RigctldServer(radio, config)` with no web machinery (`cli/__init__.py:3731`); the web-composed variant passes the queue (`:3934-3939`). But standalone-ness explains the *queue-less direct calls*, not the *conversion duplication*: rigctld converts because it speaks display-unit hamlib protocol against a unit-mixed Radio API (`set_cw_pitch` takes Hz, `set_nr_level` takes raw), not because it lacks a queue.

**E2 (historical order): supported.** Observation (git log, `--date=short`): filter-controls.ts conversions MOR-490/498 era → rigctld routing calibrated levels 2026-06-12 (MOR-453/467) → TS exact math 2026-08-15 (#2680) → Python exact math 2026-09-15 (#3485). Each consumer's conversions predate the shared mechanism; the shared mechanism is one day old and only partially consumed (Yaesu decode/validate only; encode/quantize have no production caller).

**E3 (recorded ruling): partly supported.** The rigctld profiles ban is recorded and binding (`rigctld/LAYER.md:31-32`). The 2026-08-30 audit F1 recorded the displacement and named the missing surface. MOR-2472 recorded the Python↔TS mirror ruling. No recorded ruling found that assigns conversion ownership globally ("server computes" or "browser computes") — the fact-9 audit document is not in-tree; its surviving trace is the MOR-2472 docstrings.

**Validation of control values, per ingress (explicit):**
- **web:** handler does *not* validate domains before the queue (`handlers/control.py` set_if_shift/set_nr_level/set_cw_pitch cases: int coercion + `_ensure_capability` + `_ensure_receiver_supported` only). Reached inside the backend: Yaesu validates `cw_pitch`, `if_shift`, `manual_notch_freq` (`yaesu_cat/radio.py:2708,1746,1607` — `validate_control_raw_value` before any CAT write); `nr_level` is *not* validated. Icom: no profile-driven validation; ad-hoc `ValueError`s only (`_cw_pitch_to_level` range, `att_values`).
- **rigctld:** clamp/snap before the radio in `handler.py:_execute_set_level` and `routing.py:YaesuRouting.set_level` (hardcoded constants — see F2), then the backend behaviour above.
- **CLI:** hardcoded 0-255 pre-checks in `_cmd_levels`; raw-unit UX; then backend behaviour.

So: validation exists in *both* places for three Yaesu controls, nowhere for Icom profile controls, and in neither place for `nr_level` on any radio — the exact gap PR #3486 reportedly closes. Inference for the last clause; observation for each stated location.

---

## Owner decision brief

| Hyp | (a) correctness / divergence | (b) no-browser paths | (c) slider latency | (d) wire/state cost | (e) open-core | (f) size | (g) test cost |
|---|---|---|---|---|---|---|---|
| **H1 server computes all; state display-only** | Kills F2/F4 divergence; one arbiter | rigctld/CLI/third-party WS all validated | Sliders can optimistically echo display; server snap is authoritative — no local encode *needed* for geometry (domain published), only for echo | **Expensive, hard to reverse**: state.ts/state_schema.py regenerated; pbtInner/nrLevel raw→display; fixtures + consumer-contracts | Stays open (server is open-core); no §4 hollowing | ~20+ frontend files (8 pbtRawToHz consumers, NR/notch dispatch sites), 2 backends, poller, schema, contracts | Rewrite of state fixtures + contract tests; new server-side snap tests |
| **H2 browser computes; server raw-only** | Preserves today's asymmetry: rigctld/CLI keep re-implementing (F2 live: NR 15 vs 10) | Server-side validation stays partial (Yaesu 3 controls, Icom none, nr_level never) | Best: local encode exists today (RitXitScanSurface:214) | None — frozen mixed contract | Skins keep TS math (not in host-api contract though) | 0 now; every future consumer repeats F2 | None added; drift risk unpinned |
| **H3 both; server truth; TS mirror pinned by contract test** | Mirrors currently identical (F1); pin is hand-copied vectors, not mechanical | Same as H1 once Radio-protocol lands (#3486) | Keeps local encode + exact echo | **None for existing fields**; display-unit migration deferred per control | Prose ruling MOR-2472 already in-tree | ~4 files: shared vector fixture (pytest+vitest), Radio protocol, routing swap | Add shared-vector harness; keep both suites |
| **H4 Radio-level capability protocol owns server math; browser keeps pinned mirror; state units converge only when a contract break is budgeted** | Same authority as H1 for non-browser paths; TS stays for interaction | Solves rigctld without violating LAYER.md (radio exposes snap; profiles stay behind the Radio) | Identical to H3 | Splits (d): authority now, wire migration later per control | Matches policy §5 (Radio+capability protocols are *the* boundary; additive protocol = negotiated, allowed) | #3486 + vector file + routing consumers (~6 files) | Radio-protocol conformance tests + shared vectors |

**Recommendation (H4, which subsumes H3's pin):** land PR #3486's Radio-level decode/snap/encode/validate protocol over `profiles.control_domain`, migrate `rigctld/routing.py` + `handler.py` level arms onto it (closing the NR-15-vs-10 and ATT/PREAMP divergence), keep `control-domain.ts` as the interaction-side mirror, and replace the hand-mirrored vectors with one shared fixture consumed by both suites. Defer any state-unit migration (F4) until a contract break is budgeted — the mixed wire is the one expensive-to-reverse item and should not ride along tacitly.

**Falsifier:** PR #3486's protocol cannot express lookup/centered quantization without rigctld importing profiles (then the surface design, not the ownership, is wrong); or a bench capture showing slider interaction measurably degrades without local encode (then H1-full is wrong and the mirror is load-bearing, strengthening H3); or a Pro extension found importing `$lib/radio/control-domain` directly (moves F1 from sanctioned to contracted surface, adding a versioning obligation).

---

## Weakest link

The F2 "diverged" verdict for `nr_level` rests on reading `rigs/ftx1.toml [controls.nr_level]` (0..10) against `routing.py:344` (clamp 15) **and** the assumption that the FTX-1 CAT NR command actually rejects (or misbehaves on) 11-15 — `yaesu_cat/radio.py:set_nr_level` performs no validation, so nothing in-tree fails; only the profile *declares* 10 as the ceiling. If the CAT manual allows 0-15, the profile is wrong and F2's headline divergence downgrades to "constants duplicated, currently compatible". Check first: FTX-1 CAT OM NR level range, or a bench `L NR 1.0` through rigctld with wire capture.

## Cleared

- **`frontend/src/lib/radio/control-domain.ts`** — correctly located presentation math over published data; all three exports consumed in production; step 0 confirmed no other definition site.
- **`src/rigplane/profiles/control_domain.py` location** — correctly placed in `profiles` (importable by backends/runtime/web per `.importlinter` layer order); unconsumed exports are in-flight-migration residue, not mislocation.
- **Capabilities publishing** (`web/server.py:3161,3672`) — one mechanism, verbatim, no transformation layer.
- **`quantizeFilterWidthToRule` / filter-width segment snapping** (filter-controls.ts) — a distinct capability (filter-mode width rules), not a control-domain duplicate; deliberately tied to the backend's `filter_hz_to_index` acceptance.
- **`pbtRangeFromCaps`/`controlRangeFromCaps` family** — parameterization adapters, every symbol consumed (sweep table); not a parallel implementation.
- **rigctld↔web independence** — enforced (`independence-top`), no cross-import found; the `YaesuRouting` TYPE_CHECKING exemption is the documented, sanctioned one.
- **CLI level range checks** — hardcoded by design against the CI-V raw 0-255 contract; duplication with profiles is real but was already adjudicated by the 2026-08-30 audit and is subsumed by F2's surface.

*Not archived to `.claude/audits/` — this run's contract is strictly read-only; the repository's indexing step remains for the owner.*
