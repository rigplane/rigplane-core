Method: `.claude/skills/mechanism-audit/SKILL.md`. I read it in full and followed steps 0 to 5 in order. The operating policy came from `docs/internals/coordinator-policy.md`.

# Mechanism audit: PR #3763 (MOR-2688 S2), revision `8cff8d60`

**Setup**
- The worktree is detached at `8cff8d60` and clean. The merge base is `f90eace9`.
- `origin/main` has since gained `990ca4dc` (#3760, MOR-2692), which rewrote the dual-sdr dash sites. This matters for F2.
- Nothing was executed except git, grep and scratch scripts outside the tree. No tests and no type checks.

**What I did not open**
- Linear.
- The S1 audit. It is not archived: `git grep "MOR-2688\|readingText" -- .claude docs` finds only the audits README.
- `reading-text.test.ts`.
- The S4 and S5 files beyond their grep hits.

**Conventions**
- [O] marks an observation, [I] an inference.
- Two hypotheses were under test: the body's census claim (27 copies before, 4 after), and the builder's claim that no mount reaches `fmt`'s boolean branch.
- No file text tried to direct an agent. The maintainer directives in the `reading-text.ts` docstring were treated as data.

## Steps 0–3

- **Definition site** [O]: one, `primitives/reading-text.ts: readingText`, added by f2a69ba0 (#3761), the only commit that touches it. By `git grep -c "readingText("` there are 27 production call sites in 9 files:
  - S1: FilterInstrumentHost 1, FilterSurface 2, ScopeControlsSurface 3.
  - S2: VfoIndicatorRow 10, RitXitScanSurface 4, DspSurface 2, RxAudioInstrumentHost 2, RfFrontEndInstrumentHost 2, RepeaterSurface 1.
- **Prior rulings** [O]:
  - Quoted from the `reading-text.ts` docstring: "Drawing code must not decide "known/unknown" itself … (owner decision, 2026-09-27)" and "The predicate is EXACTLY `reading.status === 'known'` — the audit (R1) pins it".
  - The archived `2026-09-27-mechanism-audit-unread-readouts.md`, F1: "no shared 'unread display' primitive exists" (verdict B).
  - The S1 notes F1/F2 come from the dispatch only; I could not verify them from a file.
- **Layer** [O]: skins may import `primitives/`. Precedent: `skins/segmentline/PeerSplitLayout.svelte`. The eslint `**/primitives/**` ban applies only to workspace code.
- **3a sweep** [O]:
  - A scratch script listed every declared name at head (imports, const/let/function/type/snippet/@const). Counts: DspSurface 51, RepeaterSurface 29, RfFrontEndInstrumentHost 152, RitXitScanSurface 49, RxAudioInstrumentHost 129, VfoIndicatorRow 27, total 437.
  - For each name it counted literal whole-word occurrences outside the definition, across `frontend/` for exported names.
  - Zero-read names: none. Two flags, `basePolicy` and `feedbackIntegratedControl`, turned out to be `...spread` reads that the regex skipped.
  - From base to head, these names were removed: `textOf` (RitXit), `numeric`, `agc`, `sharedOffset`, `{@const agcTime}`, and `fmt`'s local `v`. The only name added in each file is the `readingText` import.
  - No reference to a removed name remains under `frontend/` (literal search). No module imports these files by namespace.
  - Limitation: these are occurrence counts, not separate write and read counts. Every name the change touches is a const or function that is never reassigned, so the extra occurrences are reads.

## Q1: how many mechanisms, and does the census hold

**The body's commands reproduced** [O]: 58 raw lines at base, 39 at head. Under the body's rule, head leaves 4 copies: `BandSurface: textOf`, `RxAudioSurface: afText`, and DualSdrFace's P.AMP and MODE.

**The "after" count reproduces; the "before" count does not** [O]:
- The stated rule gives 26 at base: 23 regex display lines plus the 3 named two-line forms.
- 27 is reached only by also counting `DspSurface: fmt`, which is a `!==` form the rule does not name.
- The per-file list in the body adds up to 24, not the stated 23.
- VfoIndicatorRow comes to 14 under the rule, not 15. The "9 inline ternaries" note names NOTCH and TUNE, which span several lines and are invisible to the regex.

**My counting rule (W)**:
- A copy is one helper, or one inline template expression, that tests a reading's status in any form (`===` or `!==`, multi-line, destructured) and produces visible or accessible text, with `''`, a placeholder, or the bare label when unread.
- Excluded: `data-*` and ARIA tokens, selections, value-or-null bases (turning that null into text in a second step is the other vocabulary from the S1 notes, F2), validators, and the primitive itself.
- How I enumerated: `git grep -nE "status *[!=]==? *'(known|unknown)'"` (275 lines in 60 files at base, 253 in 60 at head). A scratch script joined each hit with its next two lines, and I classified them by hand.

**Results under rule W**:
- The six S2 files had **25** copies at base: VfoIndicatorRow 16 (5 helpers plus 11 inline, including NOTCH and TUNE), RitXit 2, RxAudioHost 2, Dsp 2, Repeater 1, RfFront 2. All 25 now go through `readingText` [O].
- **10** copies remain at head outside S4:
  - 5 in S3 files (F1);
  - 2 on the dual-sdr face (F2);
  - 3 in no listed slice (F3, F4).
- S4 is planned and I did not re-audit it. It covers `VfoSurface: standardPanelSections` (about 9 lamp-text sites, judged from grep hits only) and VfoIndicatorRow's RF gain and S-meter.
- **The PR created no new fork** [O]. No new name is declared in any of the six files. None of the rewritten helpers (`fmt`, `signedOffset`, `unlitTextOf`, `afPercent`, `booleanLabel`, `sharedBoolean`) tests the status.
- Outside rule W:
  - MemorySurface's `UNKNOWN_TEXT` sites branch on `facts.vfoIdentityKnown`, not on a reading; S3 lists them anyway.
  - The null/NaN two-step copies (the `canonical` + `formatValue` pairs in TxAuxScalarHost and CwKeyerInstrumentHost, and `RfFrontEndInstrumentHost: valueText`) belong to F2 of the S1 notes.

## Q2: every migrated site, one by one

**Routing** [O]: every migrated site calls `readingText` directly, or through a helper that only formats. No helper checks the status and then calls `readingText`.

**The remaining status reads in the six files** [O]:
- Paint: `booleanState`, `aggregateState`, `data-state`, `data-display-state`.
- Behaviour: `usable`, `isOn`, `scanningOn`, `stepAvailable`, and the `selected` bases.
- Value bases: `numberOf`, `rfGainShown`, `readingOf`.
- Two display decisions the PR does not claim:
  - `RitXitScanSurface: decodedOffset` (F3);
  - RfFrontEnd's PRE/ATT off-list guard, `{#if … === 'known' && !values.includes(…)}`, which chooses which element to show (cleared).

**Exact text** [O, from reading the code, not from running it]:
- The text is identical for `afPercent` (`42%`), `booleanLabel` (" ON"/" OFF"), `sharedBoolean`, `signedOffset` (finite-value guard kept), BW/AGC/ATT/P.AMP/ANT, NOTCH/TUNE (`String(v)` of a string changes nothing), `fmt`, and AGC-T.
- `formatToneHz`, `preampChoiceText` and `attenuatorChoiceText` each take one parameter, so passing them directly behaves the same.

**VfoIndicatorRow paint is untouched** [O]:
- The sorted `data-state`, `data-display-state` and `data-indicator-*` expressions have identical md5 hashes at base and head: 8 use `reading.status` and 4 use `booleanState(`.
- `booleanState`'s body is byte-identical.

**Test evidence** [O]:
- The new pin `manualNotchWidth → '10'` cannot tell the row formatter from `String`. The fixture is `known(10)`, and `NOTCH_WIDTH_LABELS` only has keys 0–2.
- The AGC-T span (`0.1s`) has no literal pin (`agc-time-value`: 0 hits in tests), although the body calls `fmt` the only migrated site without one.

**Prose** [O]: the new MOR-2688 comment sits directly above `booleanState`, which does not use `readingText`.

## Steelman

The strongest case for leaving everything as it is:
- The local formatters are legitimately local. ON/OFF versus on/off, units, the sign and the leading space are text specific to each surface, and the S1 note F1 requires each migration to pass its own formatter.
- Keeping `fmt`'s boolean branch makes S2 identical by construction.
- `decodedOffset` feeds the slider and uses `usable`. Moving it to the `known` predicate would change behaviour, which R1 forbids.
- `readoutParts` drops unread parts from a joined list, and `switchLabel` falls back to the label rather than `''`. Neither fits `readingText` as it stands.

This case holds for the formatters, for `decodedOffset`, and for `readoutParts` and `switchLabel`. It does not justify keeping a branch that nothing reaches, and it does not rescue the census arithmetic.

## Deletions

### D1: `DspSurface: fmt` boolean branch, dead

**Verdict**: dead. The branch existed before S2; S2 kept it and added a comment describing it.

**Elements**: `DspSurface.svelte: fmt`, the `typeof v === 'boolean' ? (v ? 'on' : 'off')` arm.

**Consumers**: none.
- `fmt` has one call site, in the snippet `nativeLevel`.
- `nativeLevel` has one render site, in `adjustableLevels`, which walks `DSP_LEVELS` with `field: DspLevelField`.
- Every field in that walk is typed `DspField<number>` in `radio-view-model.ts: DspViewModel`.
- The validator `radio-view-model.ts: num` rejects anything that is not a finite number.

**Written / read**:
- `fmt`: one definition and one call (`grep -n "fmt("`).
- A literal search for any test or fixture that sets a level key to `true` or `false`: 0 hits.

**Guards checked**:
- Dynamic access: `dsp[field]` is indexed only through the literal `DSP_LEVELS` tuple.
- Out-of-repo and public API: `fmt` is an unexported module-script constant, so neither applies.
- Tests only: no test reaches this branch.

**Collateral**: the clause "the `on`/`off` boolean rendering … stay in the formatter" in the comment, and the matching line in the PR body.

**Depends on**: none.

**Confidence**: high.

**Falsifier**: a boolean-typed key added to `DSP_LEVELS`, or `nativeLevel` rendered from somewhere other than `adjustableLevels`.

**Fix class**: delete.

## Consolidations

### F1: local copies in the S3 files (migration incomplete)

**Verdict**: A.

**Rank**: parallel.

**Elements**: `BandSurface: textOf`, `RxAudioSurface: afText`, `CwKeyerSurface: textOf` and `rxMode`, `AntennaInstrumentHost: textOf`.

**Consumers** [O]: one production importer each for BandSurface, RxAudioSurface and CwKeyerSurface; three for AntennaInstrumentHost, including a re-export through AntennaSurface.

**Definition site**: each surface has its own; the shared target is `readingText`.

**Divergence**: none for unread values, all `''`. The CwKeyer and Antenna copies write booleans as lowercase `on`/`off` [O].

**Prior ruling**: the owner decision of 2026-09-27; R1.

**In-flight**: `readingText` has 27 call sites and none of them is in these files. The body's regex misses 3 of these 5 copies (`!==` and multi-line forms) [O].

**Required surface**: exists.

**Depends on**: none.

**Confidence**: high.

**Falsifier**: a ruling that these surfaces keep a rule of their own.

**Fix class**: consolidate.

**Actionable**: yes, in S3.

### F2: dual-sdr face copies (the slice assignment is out of date)

**Verdict**: A.

**Rank**: diverged at `8cff8d60`; parallel on current main.

**Elements**: DualSdrFace's inline P.AMP and MODE expressions, and `ReceiverInstrumentCluster`'s bandwidth, which is made into a value first and into text second.

**Consumers** [O]: one production importer each.

**Definition site**: local to the skin.

**Divergence** [O]:
- At `8cff8d60` all three show `'—'` when unread.
- On `origin/main`, after #3760, they are `known ? String(v) : ''`, `… : ''` and `bwRaw === null ? '' : formatBandwidth(…)`.

**Prior ruling**: MOR-2520 (no dashes), MOR-2692.

**In-flight**: #3760 already removed the divergence. The body routes these sites to "separate tickets" because of the dashes, so after the merge the remaining plain copies belong to no slice [I].

**Required surface**: exists.

**Depends on**: none.

**Confidence**: medium.

**Falsifier**: a ticket that already owns the dual-sdr copies.

**Fix class**: consolidate.

**Actionable**: yes, once a slice is named.

### F3: `RitXitScanSurface: decodedOffset` and `signedOffset` use different unread predicates in the same surface

**Verdict**: C under R1; whether the difference is intended is undetermined.

**Rank**: diverged, if the difference is unintended.

**Elements**:
- `decodedOffset` drives `ritxit-offset-value`. It uses `usable` (structural, operational and known) plus decoding through the control domain.
- `signedOffset` drives `rit-offset-value` and `xit-offset-value`, and uses `known`.

**Consumers** [O]: both outputs are rendered in the branch without `instrumentLayout`.

**Definition site**: RitXitScanSurface.

**Divergence**:
- For a known offset while `operational` is false, `rit-offset-value` shows "+250 Hz" and `ritxit-offset-value` shows `''` [I, from reading the code].
- No test pins this: the only non-default availability in `RitXitScanSurface.test.ts` is `OFF_AVAIL`, which is structural false [O].
- The difference existed before S2.

**Prior ruling**: R1; MOR-2653.

**In-flight**: none.

**Required surface**: a ruling on the predicate, not code.

**Depends on**: none.

**Confidence**: medium.

**Falsifier**: a ruling that the slider's value text follows `usable`.

**Fix class**: none, or design.

**Actionable**: no; it needs that ruling first.

### F4: text builders outside the plan

**Verdict**: undetermined.

**Rank**: parallel.

**Elements**: `ScopeDisplaySurface: readoutParts`, and `VfoOperationGroup: switchLabel`, which takes a bare reading and falls back to the label.

**Consumers** [O]:
- `readoutParts` feeds `indicatorText` (tooltip and accessible name) and `readoutText` (the visible readout); both are used in the template.
- `switchLabel` feeds 6 `aria-label` attributes.

**Definition site**: local to each surface.

**Divergence**: `readoutParts` leaves unread parts out of a joined list. `readingText` returns `''` both for an unread value and for a known value that formats to `''`, so it cannot tell the two apart [I].

**Prior ruling**: none found on accessible names.

**In-flight**: none.

**Required surface**: unknown until someone rules whether the 2026-09-27 decision covers accessible names and joined lists.

**Depends on**: that ruling.

**Confidence**: medium.

**Falsifier**: the plan already listing these two.

**Fix class**: design.

**Actionable**: no.

## Weakest link

F3. I treated `decodedOffset` as legitimately local because it also feeds the slider. If the owner wants every offset readout to follow `known`, it becomes a diverged duplicate of the most expensive rank. Check first: what MOR-2653 and the O1 ruling say about the slider's output, and whether a known offset should print while `operational` is false.

## Cleared

- `readingText` itself: one definition, in the right layer, with a type-only import and exactly the R1 predicate.
- All 25 S2 migrations: the text is exact, no new names were added, and there are no dead names in the six modules.
- VfoIndicatorRow's paint: `data-state`, `booleanState` and `aggregateState` are unchanged.
- `sharedAggregate` handles the `''` result safely, because none of the formatters it uses returns `''` for a known value [I].
- `unlitTextOf` is a live pass-through with one consumer, not a fork.
- Removing `RitXitScanSurface: textOf` was safe: it had 0 importers at base (literal search).
- `signedOffset`'s finite-value guard was moved, not added. It belongs to the null/NaN vocabulary.
- `DspSurface: numberOf` is a value base, not display text.
- RfFrontEnd's off-list guards are legitimately local (C): they choose which element to show (MOR-2527).

Files: `frontend/src/primitives/reading-text.ts`, `frontend/src/semantic/DspSurface.svelte`, `frontend/src/semantic/RitXitScanSurface.svelte`, `frontend/src/semantic/VfoIndicatorRow.svelte`, `frontend/src/semantic/ScopeDisplaySurface.svelte`, `frontend/src/semantic/VfoOperationGroup.svelte`, `frontend/src/skins/dual-sdr-face/DualSdrFace.svelte`