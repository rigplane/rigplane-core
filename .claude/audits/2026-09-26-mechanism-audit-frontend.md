# Mechanism audit — frontend (Svelte/TS) + rigs/ at `a04f7b1e51ff012ff1de0d42e876befbd06ead47`

**Method file read:** `.claude/skills/mechanism-audit/SKILL.md` (worktree copy, this revision). Steps 0–5 executed in order, including the step-3a dead-code sweep and the step-4 steelman before any verdict. Strictly read-only: no edits, no git writes, no test runs, no Linear/GitHub writes (MOR-2478 and prior audits read only).

**Scope:** `git diff --name-only 9a1b90f9..HEAD -- frontend/src rigs/`, test files used as consumers only. Layer map read: `CLAUDE.md` "Layer boundaries" + `.importlinter` (frontend has no import-linter tiers of its own; the governing boundaries are the web/`src/rigplane` split, the semantic-vs-components-v2 split, and MOR-2478's conversion rulings). Prior rulings read: `.claude/audits/README.md`, the 2026-09-15 control-conversion and command-path reports, the 2026-09-16 closing ledger, and Linear MOR-2478 (owner decisions 2026-09-15, quoted below where they bind).

**Observed facts 1–7 all verified** (each file in the diff read at this revision; counts below carry their grep rule).

---

## DELETIONS (ranked first; independent of design decisions)

### D1 — `RfFrontEndInstrumentHost.svelte: domainOf`: dead
```
Verdict:          dead
Elements:         frontend/src/semantic/RfFrontEndInstrumentHost.svelte:206 — module-local
                  arrow function `domainOf`, a one-line wrapper around levelDomain(field)
Consumers:        none. Literal grep `domainOf` over the whole worktree returns exactly two
                  hits: the definition (RfFrontEndInstrumentHost.svelte:206) and an
                  unrelated local helper of the same name in
                  semantic/__tests__/bar-meter-projector.test.ts:583 (a different function
                  in a different file — homonym, not a consumer). Every call site the
                  change touched calls `levelDomain(field)` directly (grep
                  `levelDomain` in the same file: pairInput:272, scalarInput:297, and the
                  removed `const domain` initializer).
Written / read:   written 1× (its own definition), read 0×.
Guards checked:   dynamic access — none plausible: a Svelte `<script>`-local const cannot
                  be reached by string-built names or entry points (literal grep stated).
                  out-of-repo — Pro extensions cannot import a component-local binding.
                  public API — no (not exported). tests-only — no (the test hit is a
                  homonym in another file).
Collateral:       none — no comment or test references it.
Depends on:       none
Confidence:       high
Falsifier:        a Svelte mechanism reaching script-local consts by name dynamically
                  (none exists in this codebase — unknown, but no precedent found).
Fix class:        delete
```
Leftover from the change's own mid-flight rename (`levelDomain` replaced the old shared `const domain`; `domainOf` is the wrapper that refactor abandoned). Observation.

---

## CONSOLIDATIONS (ranked by debugging cost)

### F1 — RF/SQL published-raw-range reading and normalized↔raw projection: three readers, two formulas, one ignores `raw_min`
```
Verdict:          B — gap: no shared seat owns "read the published raw range of a control";
                  each client re-derives it, and the two new derivations disagree about
                  raw_min
Rank:             diverged (the copies answer the same input differently — latent today)
Elements:         frontend/src/semantic/RfFrontEndInstrumentHost.svelte: rawRangeOf/:186
                  (with safeRawBound), normalizedToRaw/:209, rawToPercent/:215,
                  feedbackLaneOf/:237;
                  frontend/src/components-v2/wiring/SemanticRadioSurfaces.svelte:
                  rfFrontEndRawControlPublished/:650;
                  frontend/src/lib/runtime/commands/panel-commands.ts:
                  keyboardControlRawDomain/:1656 (pre-existing, changed by this diff)
Consumers:        host projections — RfFrontEndInstrumentHost pairInput/scalarInput/
                  valueText (all live, rendered by SemanticRadioSurfaces:2510 region);
                  seam predicate — RF_FRONT_END_LEVEL_INTENT (SemanticRadioSurfaces:658,
                  live); keyboard — adjust_rf_gain/adjust_af_level arms (live).
Definition site:  the data is defined in rigs/*.toml [controls.rf_gain]/[controls.squelch]
                  and published verbatim as caps.controls (capabilities.ts:
                  normalizeControls/:477); no shared reader of raw_min/raw_max exists —
                  getControlRange (lib/stores/capabilities.svelte.ts:183) returns the raw
                  entry unvalidated, and resolveControlContract
                  (lib/radio/filter-controls.ts, MOR-2475) requires display bounds a
                  range-only entry does not publish, so it cannot serve these controls.
Divergence:       observed, concrete. (i) Validation: keyboardControlRawDomain silently
                  defaults to 0/255 on absent/malformed entries; rawRangeOf and
                  rfFrontEndRawControlPublished are strict (safe integers, rawMax>rawMin,
                  null/false fallback to the legacy normalized lattice). (ii) Formula:
                  normalizedToRaw and feedbackLaneOf compute Math.round(normalized *
                  rawMax) — raw_min-blind — while panel-commands' adjust arm computes
                  rawMin + current * (rawMax - rawMin) (panel-commands.ts:1840 region)
                  and the same host's rawToPercent is raw_min-aware. With a published
                  range where raw_min > 0, the host's reading/feedback lanes and the
                  keyboard arm would disagree, and the host's own value text would
                  disagree with its own reading. Latent today: every profile that
                  publishes these controls publishes 0..255 (grep [controls.rf_gain]
                  across rigs/: ic705, ic7300, ic7610, ic9700, x6100, x6200, ftx1 —
                  all raw_min = 0, raw_max = 255; observation).
Prior ruling:     MOR-2478 decision 1 (2026-09-15): "The radio is the source of truth;
                  the browser keeps a verified copy… control-domain.ts stays for
                  latency-free sliders, pinned to Python by one shared vector fixture."
                  That ruling covers display↔raw exact-lattice conversion; it does not
                  assign normalized↔raw band projection, and control-domain.ts holds no
                  band primitive. The 2026-09-15 control-conversion audit's F1/Cleared
                  explicitly left control-domain.ts as presentation math over published
                  data — this new cluster grew beside it, not through it.
In-flight:        none — no shared band reader exists or is tracked (searched frontend/src
                  for a second reader by vocabulary: rawRange/rawMin/published/
                  controlRange; only the three above).
Required surface: one validated reader in the layer that already owns caps-derived
                  control math (lib/radio, beside resolveControlContract, or
                  stores/capabilities beside getControlRange):
                  `publishedRawRange(key): {rawMin, rawMax} | null` plus a
                  rawMin-aware `normalizedToRaw(range, n)`; consumed by the host, the
                  wiring seam, and the keyboard arm. The keyboard arm's deliberate
                  0/255 fallback can stay a wrapper that treats null as the legacy
                  default — its lenience is a documented choice (its own comment).
Depends on:       none (D1 is independent).
Confidence:       high on the three readers and the formula disagreement (all four sites
                  read); medium on the fix location (lib/radio vs stores is an owner call).
Falsifier:        a recorded ruling that each consumer must read caps locally (none
                  found); or raw_min > 0 being permanently unrepresentable for
                  range-published controls (then the formula split is cosmetic — but
                  the schema permits it, rigs/_schema.md, observation).
Fix class:        design (small): shared reader, then consolidate the three call clusters
Actionable:       yes — the reader is mechanical; the rawMin-blind projection should be
                  corrected in the same motion (it is one line each in normalizedToRaw
                  and feedbackLaneOf's project).
```
Steelman engaged: the seam's predicate exists because the seam must decide *whether to scale* before the host sees the value, and the host must decide *what domain to draw* — one could argue these are two legitimately different questions. But both answers derive from the identical validation of the identical caps entry, and the host already owns the decision the seam duplicates (`levelDomain` performs the same check). The steelman wins only for the keyboard arm's lenient defaults; it does not excuse the seam/host double read or the rawMin split.

### F2 — notch pending decision: `freshestNotchStrand` re-implements the lifecycle scan beside `latestPendingParam`
```
Verdict:          B — gap: the shared pending decision table returns values only, so
                  comparing the freshness of TWO intents grew a private second scan
Rank:             parallel (same module today; drift risk is in the liveness rules)
Elements:         panel-adapters.ts: freshestNotchStrand/:1395 (new, private) beside
                  latestPendingParam/:1272; public accessors getPendingNotchMode/:1374
                  and getPendingManualNotchWidth/:1393 (new)
Consumers:        getPendingNotchMode/getPendingManualNotchWidth — SemanticRadioSurfaces
                  :1730-1731 (live, the only production consumers) +
                  frontend/fixtures/stubs/panel-adapters.ts:167,170 (the established
                  verification-stub mirror, consumed by the fixture-seam test). All live.
Definition site:  the decision table is latestPendingParam + getCommandLifecycles
                  (runtime commands module), the same table every sibling pending
                  accessor uses (filter, NB, NR, preamp, repeater — read, all routed
                  through latestPendingParam).
Divergence:       observed. latestPendingParam's scan admits terminal records as
                  barriers ("a newer terminal same-key record is a barrier", its own
                  comment at :1300) and derives liveness after selection;
                  freshestNotchStrand instead filters to status pending|acknowledged
                  during the scan and hard-filters params.on !== true. For the tie-break
                  question ("which strand is freshest") the barrier rule is probably
                  inert, but the two scans now encode supersession (identical guard,
                  copied) and status semantics separately — the exact rule pair the
                  MOR-1541 family spent its history keeping in one table.
Prior ruling:     the pending-accessor family itself is the sanctioned pattern (MOR-1441,
                  MOR-2111 comments in-file; the 2026-09-16 closing audit counts these
                  accessors as the canonical pending mechanism). No ruling found that
                  assigns multi-intent freshness.
In-flight:        none.
Required surface: a record-returning variant in the same module —
                  latestPendingRecord(intent, paramKey, receiver) → {createdAt, value,
                  status…} | null — with latestPendingParam becoming a thin wrapper;
                  freshestNotchStrand then compares two records instead of re-scanning.
                  Cheaper alternative: keep the private helper but build both halves of
                  its guard (supersession check, status set) from named constants shared
                  with latestPendingParam so the rules cannot drift lexically.
Depends on:       none.
Confidence:       high on the duplication and the copied supersession guard; medium on
                  whether the barrier-vs-filter difference is behaviour-relevant (I could
                  not construct a lifecycle sequence where it changes the returned mode —
                  unknown).
Falsifier:        a lifecycle sequence where latestPendingParam's barrier rule and
                  freshestNotchStrand's filter disagree on which strand is freshest
                  (then the helper is not just duplicated, it is wrong on that path).
Fix class:        design (small surface) then consolidate
Actionable:       yes, but low urgency — single module, single consumer.
```

### F3 — manual-notch-width choice-group markup: two new hand-copied renderings, both live
```
Verdict:          C — legitimately local per the settled per-surface markup convention,
                  with the count now at three live instances
Rank:             parallel (identical behaviour; attribute sets differ observably but
                  deliberately)
Elements:         semantic/DspSurface.svelte: widthChoices snippet/:240 (new) +
                  nativeLevel branch/:231; semantic/DspScalarHost.svelte: widthChoices
                  snippet/:191 (new). Behavior layer shared: both consume
                  primitives/control-instruments/control-instrument-behavior.ts:
                  bindChoiceInstrument (the 2026-09-06 finite-choice audit's canonical
                  choice binding).
Consumers:        both live. DspSurface renders its own widthChoices when the
                  standalone adjustableLevels loop draws manualNotchWidth
                  (DspSurface.svelte:276-287 — reached whenever scalarLayout is absent
                  or via the each-loop for non-nbWidth fields), and renders
                  scalarHandles.manualNotchWidth (DspSurface.svelte:302) otherwise —
                  those handles are DspScalarHost's snippets, which carry their own
                  choice branch. The wiring feeds both identically
                  (SemanticRadioSurfaces.svelte:2074, 2513).
Definition site:  bindChoiceInstrument (shared); the markup trio — data-pending flag,
                  data-*-status attribute, sr-only pendingAnnouncement — is the
                  repository-wide convention (ModePanel.svelte:105, RfFrontEnd.svelte:172,
                  DspPanel.svelte:597 all pre-date this change).
Divergence:       DspScalarHost's group carries feedback-scope/busy/command-status
                  attributes DspSurface's lacks; DspSurface's carries the
                  dsp-name/dsp-choice-row layout classes. Follows each file's existing
                  scalar-row divergence exactly (the native slider vs hosted snippet
                  pair already dual-renders nrLevel, nbDepth, notchFreq the same way —
                  observation from DSP_LEVELS + DspScalarHost snippets).
Prior ruling:     2026-09-06 finite-choice audit: "Verdict: … C for finite markup" —
                  per-surface markup is deliberately local; the shared primitive is the
                  behavior binding, which both new sites consume. The rule of three is
                  now met (DspPanel + DspSurface + DspScalarHost carry the
                  pending-choice-group markup), but the prior ruling still governs and
                  nothing in this change contradicts it.
In-flight:        none.
Required surface: exists (bindChoiceInstrument); a shared markup snippet would be new.
Depends on:       none.
Confidence:       high.
Falsifier:        an owner ruling that the pending-announcement trio must become a shared
                  component (none found; would flip this to a consolidation).
Fix class:        none (record the third instance for the next rule-of-three review)
Actionable:       no — consolidation would ride against the recorded C ruling.
```

### F4 — Toast link-loss filter: a second toast-suppression gate, correctly scoped, on a hand-pinned reason literal
```
Verdict:          C — legitimately local; the literal pin follows the sanctioned
                  hand-mirrored-pair pattern
Rank:             parallel (with the literal pin as the drift risk)
Elements:         components/shared/Toast.svelte: isLinkLossTermination/:105 +
                  LINK_LOSS_TERMINATION_REASON/:102 + the onMessage guard/:118;
                  lib/transport/ws-client.ts: REFUSAL_NOTICE_DEBOUNCE_MS/:1099
                  (pre-existing, unchanged this diff)
Consumers:        Toast guard — every error notification through the one shipped toast
                  surface (ws-client.ts:437 names components/shared/Toast as the one
                  consumer); the ws-client debounce gates its own refusal notices.
Definition site:  the reason string is produced at src/rigplane/web/server.py:2144
                  ("provider generation invalidated") — observation; the frontend literal
                  is a hand-pin across the language boundary.
Divergence:       the two suppression mechanisms are NOT duplicates: ws-client deburses
                  client-side refusals (per-command bursts while the socket is live);
                  the Toast filter suppresses server-side terminations caused by a link
                  drop, gated on getRadioLinkState() !== 'connected'
                  (stores/connection.svelte.ts, the StatusBar's own state source). One
                  is rate-limiting, the other causal attribution — different jobs.
Prior ruling:     MOR-1422 (ws-client debounce, in-file); MOR-2472 pattern (hand-pinned
                  Python↔TS pairs are sanctioned when pinned by tests on both sides).
                  Both sides are pinned: server side tests/test_web_server_coverage.py:
                  3730 asserts the literal; frontend
                  components/shared/__tests__/Toast.link-loss.component.test.ts pins the
                  same reason string (read, :3-10).
In-flight:        none.
Required surface: exists (a structured reason-code enum would be nicer; not required).
Depends on:       none.
Confidence:       high.
Falsifier:        a third suppression site appearing with a different reason literal for
                  the same link-loss event (would show the pin is already leaking).
Fix class:        none
Actionable:       no
```

---

## Dead-code sweep (step 3a, per changed module)

Enumerated every new/changed symbol in the non-test diff and counted reads outside its definition (rule: literal grep over frontend/src + rigs/, tests excluded from production counts):

| Symbol | Writes | Prod reads | Verdict |
|---|---|---|---|
| panel-adapters: getPendingNotchMode, getPendingManualNotchWidth, freshestNotchStrand | 1 | 2 + stub mirror (freshestNotchStrand: 1 internal) | live |
| rf-front-end-instruments: RF_FRONT_END_RAW_CONTROL_KEY | 1 | 6 (host) | live |
| RfFrontEndInstrumentHost: safeRawBound, rawRangeOf, levelDomain, normalizedToRaw, rawToPercent, readingOf, feedbackLaneOf | 1 each | all ≥1 in-file | live |
| RfFrontEndInstrumentHost: domainOf | 1 | 0 | **dead — D1** |
| SemanticRadioSurfaces: rfFrontEndRawControlPublished, notchWidthChoices, pendingNotch, pendingNotchWidth | 1 each | ≥1 each | live |
| capabilities: NotchWidthChoice, isNotchWidthChoice, normalizeNotchWidthChoices | 1 each | 5 / 1 / 1 | live |
| smoothing: settled getter; MeterSmoother.settled | 1 | 1 (meter-ballistics ticker gate) | live |
| meter-ballistics: peakAboveCurrent | 1 | 2 | live |
| DspInstrumentHost: requestedNotch | 1 | 1 (notchSeat) | live |
| Toast: isLinkLossTermination, LINK_LOSS_TERMINATION_REASON | 1 each | 1 | live |
| DspSurface/DspScalarHost: widthChoiceValues, widthChoiceBehavior, widthChoices snippets | 1 each | ≥1 each | live |
| panel-commands: (refactor of adjust_rf_gain; no new symbol) | — | — | live |

The fixture-stub mirror (`frontend/fixtures/stubs/panel-adapters.ts:167,170`) mirrors each new accessor by hand — the established verification-stub pattern for this module (the whole file mirrors every accessor; read), not a parallel implementation.

## Cleared

- **notchWidthChoices capability normalisation** (capabilities.ts) — follows the file's established normaliser convention (freeze, filter blemishes, throw on non-array); the deliberate difference from `scanTypeValues`' requireInteger (drop vs reject) is documented in the function's own comment. Observation.
- **The pending-accessor family extension** (fact 1's public surface) — one mechanism (latestPendingParam table), one seam consumer, stub mirrored; consistent with MOR-1441/MOR-2111 siblings.
- **bindChoiceInstrument reuse in both new choice groups** — the shared behavior primitive from the 2026-09-06 ruling is consumed, not forked.
- **Toast vs ws-client** — two different jobs (F4); not duplicates.
- **smoothing/meter-ballistics settled gate (MOR-2613)** — single mechanism, single consumer, no orphan.
- **rigs/ftx1.toml [controls.rf_gain]/[controls.squelch]** — data declaration matching the identical entries every other profile already carries (all 0..255); and `rigs/_schema.md` first_code rows are data-contract docs consumed by the Python loader per their own text (out of this tract's adjudication beyond liveness: `radio_default_code` consumers live in `rig_loader.py` — Python tract).
- **The seam-vs-host handoff design** (raw published → dispatch as-is; legacy → scale once) — the mechanism is single-per-path; only its range reader is triplicated (F1).
- **The `.dsp-choice[data-pending]` / `.sr-only` CSS additions** — the per-component CSS convention (MOR-1519 comment, ModePanel.svelte:213) deliberately local; not consolidated.

## Weakest link

**F1's "diverged" rank.** The evidence that `normalizedToRaw`/`feedbackLaneOf` ignore `raw_min` is a reading of the formulas (RfFrontEndInstrumentHost.svelte:209, :237) against `rawToPercent`/the keyboard arm — all four read, the disagreement is arithmetic fact. What is *not* established is that a `raw_min > 0` range can ever be published for rf_gain/squelch: the schema permits it, no profile does it, and I found no test constructing one (unknown). If the owner rules that range-published controls are always 0-anchored by contract, F1 drops from "diverged" to "parallel" (three readers, one formula), and its fix shrinks to the shared reader alone. Check first: whether `radio_default_code`/range semantics anywhere assume a 0 anchor for `[controls.*]` entries without `display_*` — a schema ruling, one question to the owner, before any fixer touches the formulas.

