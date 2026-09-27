<!-- Archived verbatim from a read-only mechanism-audit run. Audited revision: f9b599e90249e19bfb8234f1722f8d9effa9029e (main after #3750). Method: .claude/skills/mechanism-audit/SKILL.md at that revision. Scope: commit 7ca6246f (#3743, MOR-2477). The auditor was from a different model family than the commit's author. -->

# Mechanism audit — Tract A: control-domain vectors

**Scope:** one commit, `7ca6246f` ("test(MOR-2477): pin control-domain snapping to shared Python/TypeScript vectors (#3743)", 6 files, +448/−7): new generator, new fixture, two new contract tests, docstring-only narrowing in `control_domain.py` (verified against the diff), one `.gitignore` allowlist line. Parsed rig profiles via discover_rigs and profile conversion.

**Layer map read:** `CLAUDE.md` "Layer boundaries" + `.importlinter`. Nothing the commit adds crosses a layer: the generator lives in `scripts/` (outside the layered tree), the math stays in `profiles/`, the TS mirror sits outside import-linter's reach.

**Prior rulings read:** `.claude/audits/README.md`; the 2026-09-15 control-conversion and command-path reports; the 2026-09-26 frontend/backend reports; the 2026-09-16 closing ledger. Linear (read-only): MOR-2470 (owner-approved 2026-09-15 decision: domain math in `profiles`, Radio boundary speaks display, TS keeps a consumer-side mirror **pinned by one shared vector fixture**), MOR-2478 (epic, same ruling), MOR-2520/MOR-2651 (no placeholders; nothing fabricated; nothing moves when a value arrives), MOR-2477 (the commit's own ticket, Done; acceptance: tie-rule mutation fails either suite, drift detected in CI).

**Step 0:** `discover_rigs` defined once (`src/rigplane/profiles/rig_loader.py:2909`), `RigConfig.to_profile` once (`rig_loader.py:683`), Python snapping/quantize once (`control_domain.py`), TS mirror once (`control-domain.ts`), float `snapToStep` once (`value-control-core.ts`).

**Step 3 liveness (discriminators):** Python `snap_control_domain` has a production consumer (`backends/yaesu_cat/radio.py:2988`); encode now consumed in production (`radio.py:2993`), closing the 2026-09-15 audit's "encode/quantize tests-only" gap. TS exports consumed by `filter-controls.ts:13` and `panel-props.ts:27`. Observation.

**Step 3a sweep (counts):** generator — 6 module-level names, 7 functions, all reachable; contract test — 2 constants, 1 loader, 2 tests, all reachable; fixture keys — `rig/control/domain/decode/encode/quantize`, each with a reader (vitest reads all six; Python coverage test reads results keys); `control_domain.py` — 8 exports (consumers above) and 19 private helpers, call graph read in full, all reachable; TS test interface — all fields read. **Result: no dead code.** Deletions list is deliberately empty.

**Step 4 steelman:** the strongest "second mechanism" cases survive differently for each fact: the machinery named in MOR-2477 is three *artifacts*, not a shared framework — a pattern to follow, not a harness to plug into; the two fixtures separate *shape* (hand-written golden) from *computed results* (generated); the snapping pair is the sanctioned mirror and this commit is its ordered pin; the generator reuses the canonical loader. All five premises failed or cleared. Verdicts below.

## Deletions

None. Sweep counts recorded above; no docstring claims left unverified.

## Consolidations

None ranked. The commit is itself the consolidation the 2026-09-15 control-conversion audit ordered (its F1, fix class "consolidate the *vectors*, not the implementations"), delivered under the MOR-2470/2478 owner ruling of 2026-09-15.

### F1 — drift-check generator: C, cleared
```
Verdict:          C — legitimately local instantiation of the existing pattern
                  (already-shared in substance)
Rank:             parallel (same shape per artifact; maintenance cost only)
Elements:         scripts/gen_control_domain_vectors.py: main, fixture_document
                  (new); siblings gen_state_types.py, gen_field_status_fixture.py,
                  run_consumer_contracts.py
Consumers:        imported by tests/contracts/test_control_domain_vectors.py via its
                  _load_generator; drift channel rides quick's pytest job, exactly as
                  test_consumer_contracts.py mirrors consumer-contracts-gate.yml
                  in-suite (its own docstring states this)
Definition site:  per-artifact script; no generic framework exists to share
Divergence:       CLI surface and stale-fixture phrasing match the siblings; the new one
                  adds a one-line-per-entry serializer (fixture_document) the siblings
                  do not need
Prior ruling:     MOR-2477 scope (Linear, 2026-09-15): "Follow the existing contract
                  machinery (...)" — a pattern reference
In-flight:        none
Required surface: the pattern itself is the surface; pytest is the shared drift channel
Depends on:       none
Confidence:       high
Falsifier:        a further sibling of this shape would start a rule-of-three case for a
                  shared harness; or a ruling that this fixture needs a path-filtered
                  CI gate like consumer-contracts-gate.yml
Fix class:        none
Actionable:       no
```

### F2 — two control-domain fixtures: cleared
```
Verdict:          premise fails — not one capability split in two
Rank:             not applicable
Elements:         tests/fixtures/control-domain-controls.json (hand-written golden
                  shape); tests/fixtures/control-domain-vectors.json (generated results)
Consumers:        controls.json — tests/test_web_api_contract.py:44 (web API shape),
                  capabilities.control-domains.test.ts:5 (TS parse shape); vectors.json
                  — tests/contracts/test_control_domain_vectors.py (drift + coverage),
                  control-domain.vectors.test.ts (parity)
Definition site:  shape authored by hand; results authored by the generator
Divergence:       none — shape vs results
Prior ruling:     2026-09-15 control-conversion F1 ordered a shared vector file; this is
                  the file it ordered
In-flight:        none
Required surface: exists
Depends on:       none
Confidence:       high
Falsifier:        a consumer asserting both documents are one contract, or the generator
                  absorbing the shape test
Fix class:        none
Actionable:       no
```

### F3 — Python/TS snap pair: already shared; the in-flight pin, landed
```
Verdict:          already shared — sanctioned mirror pair, now mechanically pinned
                  (closes the 2026-09-15 audit F1 enforcement gap)
Rank:             parallel by design; drift is now a CI failure
Elements:         control_domain.py: snap_control_domain/quantize_control_domain;
                  control-domain.ts: quantizeControlDomain;
                  pin: tests/fixtures/control-domain-vectors.json
Consumers:        Python snap — backends/yaesu_cat/radio.py:2988; TS —
                  filter-controls.ts:13, panel-props.ts:27 (all production)
Definition site:  one per language, as the ruling requires
Divergence:       none observable; the commit mutated each side's tie rule and showed
                  the failure (12/36 vector failures on the TS mutation; drift failure
                  on the Python mutation). Observation from the commit message.
Prior ruling:     MOR-2470 decision (Linear, 2026-09-15): the frontend keeps
                  control-domain.ts as a consumer-side mirror for latency-free slider
                  encode, pinned by a shared vector fixture; MOR-2520/2651 (owner,
                  2026-09-21/26): no fabricated values, nothing moves when a value
                  arrives — the reason the client-side mirror is load-bearing
In-flight:        none — this commit is the in-flight fix
Required surface: exists
Depends on:       none
Confidence:       high
Falsifier:        a third domain-snapping implementation (candidates evaluated and
                  excluded: value-control-core.ts: snapToStep — float ScalarDomain
                  family; decode/encode_legacy_control — sanctioned legacy band;
                  filter-controls.ts width quantizer — MOR-1518, distinct;
                  panorama-motion "hard snap" — animation semantics)
Fix class:        none (future drift is a pinned failure)
Actionable:       no
```

### F4 — profile discovery in the generator: cleared
```
Verdict:          already shared (reuse, not re-implementation)
Elements:         scripts/gen_control_domain_vectors.py: _domain_entries
Definition site:  rig_loader.py: discover_rigs (:2909), RigConfig.to_profile (:683)
Divergence:       none — canonical loader imports; no TOML walking/parsing itself
Prior ruling:     none needed; reuse is the default rule
Depends on:       none
Confidence:       high
Falsifier:        TOML parsing or path walking in the generator (absent)
Fix class:        none
Actionable:       no
```

### F5 — narrowed docstrings: cleared (claims verified)
```
Verdict:          the two claims re-verified against the code
Elements:         control_domain.py: validate_control_raw_value (required = four raw
                  keys; int-and-not-bool); snap_control_domain (forces nearest_ties_up
                  via quantize_control_domain; tie-to-upper branch)
Divergence:       old text wider than the code; new text names what the code does
Prior ruling:     CLAUDE.md "prose is a claim" — narrow or delete; this change narrowed
                  and tied the claim to the fixture
Depends on:       none
Confidence:       high
Fix class:        none
Actionable:       no
```

## Weakest link

The verdict most exposed is F3's negative claim that no third snapping implementation exists anywhere, frontend or backend. The exclusions (`value-control-core.ts: snapToStep`, legacy rational band) held on family arguments — float `ScalarDomain` input versus exact-decimal contract, legacy `ControlRange` versus normalized domain — which a grep cannot make airtight. What would overturn it: a shipped surface constructing a float `ScalarDomain` out of a normalized `[controls.*]` domain and snapping through `snapToStep` (check `semantic/dsp-scalars.ts`, `tx-aux-scalar.ts`, `wheel-control.ts` first), or a TS export beside the mirror. Secondary: F1 reads "follow the contract machinery" as pattern-following; if the owner intended a path-filtered CI gate like `consumer-contracts-gate.yml` for this fixture too, F1 becomes actionable and the dedicated gate should be ordered explicitly.

## Cleared

- **The drift-check generator** — sibling instance of the existing pattern, not a second mechanism (F1).
- **The two control-domain fixtures** — shape versus computed results; not one capability split (F2).
- **The Python/TS snap pair** — sanctioned mirror, now mechanically pinned by one shared vector file; the 2026-09-15 audit's ordered fix, landed (F3).
- **Profile discovery in the generator** — reuses `rig_loader.discover_rigs` / `RigConfig.to_profile` (F4).
- **Both narrowed docstrings** — claims verified against code (F5).
- **Dead code in the touched modules** — none; full enumeration with counts in step 3a.
- **`value-control-core.ts: snapToStep` and the legacy rational band** — evaluated as third-copy candidates, excluded by capability family.

All claims above are observations unless labelled inference; unknowns are marked unknown. Archived to the ignored `./tmp/` only — the run's contract is strictly read-only, so `.claude/audits/` was not touched.
