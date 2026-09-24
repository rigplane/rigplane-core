# MOR-1400 production design-language baselines

These four images are the approved **production-root** pixel expectations for
the immutable built-dist i18n suite. They are not fixture-harness captures.
`playwright.i18n.config.ts` serves a copied `frontend/dist` through
`scripts/i18n-preview-server.mjs`, while the test stubs only the backend at the
page boundary and opens `/`.

## Linux re-pin provenance (superseded — 2026-09-14 MOR-2467 MAIN/SUB topology)

All four production-root scenes are `compared-fail` at source head
`2776ffdcad0549308c12973265216a10635233d7` in
[Tests (quick) run 34908938900](https://github.com/rigplane/rigplane-core/actions/runs/34908938900).
Their Linux ARM64 `actual.png` attachments were inspected and copied
byte-for-byte from the run's `mor-1400-production-visual-diagnostics` artifact.
The expected change is confined to the upper receiver deck: the obsolete
MAIN-A/B/SUB-A/B arrangement becomes exactly two receiver-level records, MAIN
and SUB. Playwright reported 1,522 changed pixels for StudioLine dark, 1,704
for StudioLine light, 1,120 for FieldLine dark, and 1,807 for FieldLine light.
The rest of each production composition remains unchanged. A subsequent
exact-head quick run must confirm these four comparisons pass.

## Re-pin procedure: per-scene disposition

When recording the outcome of a re-pin run, report each scene with one of
these three dispositions — never collapse the distinction into "clean":

- **compared-pass** — the case ran to completion and its screenshot diff
  matched (or was reviewed and accepted) against the existing baseline. Only
  this disposition may be written up as "passed" or "clean".
- **compared-fail** — the case ran to completion and the diff failed; the
  failure was inspected (per-image diff review, see entries below for the
  format) and either re-pinned with a documented reason or left open as a
  tracked follow-up.
- **SKIPPED-not-measured** — the case did not run to completion in this
  round (fail-fast abort, aborted CI run, etc.). This disposition proves
  nothing about the scene's baseline state and must never be reported as
  "clean" or folded into "no other failures" — serial's SKIPPED cascade
  burned one CI round per still-unmeasured scene in past re-pins here
  (MOR-1486 needed four rounds; MOR-1474 needed three, with rounds 1–2
  below establishing nothing about the FieldLine pair serial fail-fast
  never reached), leaving unmeasured scenes reportable as if they were
  reassuring.

As of MOR-1549, `i18n-visual.spec.ts` no longer sets
`test.describe.configure({ mode: 'serial' })` for this suite (it now uses
`mode: 'default'`), so a failing case no longer aborts the remaining cases
in the file. The four production-root scenes compare on every run from
here on; their sibling repair/inert cases (which assert CSS/attribute
state, not screenshots) likewise run to completion on every run instead of
risking a SKIP. `SKIPPED-not-measured` should not recur for this suite; if
it does, treat it as an infra failure, not routine behavior.

## Linux re-pin provenance (superseded — 2026-06 initial pin)

| Field | Value |
| --- | --- |
| Source commit | `629263ae483e97757017856417fa122420abe912` |
| Source run | [Tests (quick) #31292987673](https://github.com/rigplane/rigplane-core/actions/runs/31292987673) / job `93193315423` |
| Runner | self-hosted Linux ARM64 (`mm-build-core-1`) |
| Node / npm | `v20.20.2` / `10.8.2` |
| Playwright | `1.58.2` |
| Chromium | Chrome for Testing `145.0.7632.6`, Playwright revision `1208` |
| Context | Chromium; 1280×800; DPR 1; `en-US`; UTC; browser media dark |
| Comparator | `threshold: 0.2`, `maxDiffPixelRatio: 0.001` |

The initial source run was intentionally RED because these expected files did
not yet exist. It completed the unchanged 39 i18n rows successfully and failed
only the four named missing-expectation rows, producing the authentic Linux
images downloaded from its `mor-1400-production-visual-diagnostics` artifact.
The final head that commits these files must rerun this same suite green; CI
never automatically accepts or commits a baseline.

## Linux re-pin provenance (superseded — 2026-08-10 A09b honest cold-start StatusBar)

| Field | Value |
| --- | --- |
| Date | 2026-08-10 |
| Reason | A09b removes the store's optimistic-patch machinery and the HTTP polling writer (issue #2317); the idle StatusBar now honestly renders the cold-start state (`OFFLINE` badge, `CONNECT`, fault-colored transport icons) instead of the pre-A09b fabricated-connectivity presentation (green icons, `DISCONNECT`) baked into the superseded pin above. Adjudicated as a truthful-behavior change, not a regression — [issue comment 5243439946](https://github.com/rigplane/rigplane-core/issues/2317#issuecomment-5243439946). |
| Candidate commit | `f87dcda4` |
| Container image | `rigplane-ci-build-core-arm64-runner:85ab46b30c39` (linux-arm64) |
| Runner host | self-hosted Linux ARM64 (`mm-build-core-1`) |
| Node | `v20.20.2` |
| Playwright | `1.58.2` |
| Chromium | Chrome for Testing `145.0.7632.6`, Playwright revision `1208` |
| Context | Chromium; 1280×800; DPR 1; `en-US`; UTC; browser media dark |
| Comparator | `threshold: 0.2`, `maxDiffPixelRatio: 0.001` |
| Spec run | `playwright test -c ./playwright.i18n.config.ts --update-snapshots` — 43/43 passed |

Only the four named production-root images below were regenerated; all other
i18n baselines were left untouched by this re-pin. The delta against the
superseded images is confined to the StatusBar idle-state elements (OFFLINE
badge, CONNECT vs DISCONNECT, transport icon colors) — verified per-image
before acceptance.

## Linux re-pin provenance (superseded — 2026-08-12 MOR-1486 AUTO toggle)

| Field | Value |
| --- | --- |
| Date | 2026-08-12 |
| Reason | MOR-1486 turns the SpectrumToolbar's passive amber "A" mode-follow badge into a real, clickable AUTO toggle (`aria-pressed`, i18n title) — previously `setAutoStep(true)` was unreachable once any manual step change disabled it, so only a fresh browser profile ever restored it. All four production-root shots are desktop-v2 captures of `RadioLayout`/`SpectrumToolbar`, the only surface the toggle renders on (per ruling B, `MobileRadioLayout` passes `hideAutoStepToggle` and gets no toggle at all — not part of this contract). |
| Candidate commits | `76396047` (fieldline--dark), `3824279a` (fieldline--light) — plus the earlier `d5939e57` (studioline--dark) / `e4975003` (studioline--light) rounds in this same PR iteration; all four will be squashed together before merge review |
| Runner host | self-hosted Linux ARM64 (`mm-build-core-1`) |
| CI runs | studioline--dark: [31621614070](https://github.com/rigplane/rigplane-core/actions/runs/31621614070); studioline--light: [31624882412](https://github.com/rigplane/rigplane-core/actions/runs/31624882412); fieldline--dark: [31633319709](https://github.com/rigplane/rigplane-core/actions/runs/31633319709); fieldline--light: [31634582967](https://github.com/rigplane/rigplane-core/actions/runs/31634582967) |
| Node | `v20.x` (workflow-pinned `node-version: '20'`, `actions/setup-node@v6`) |
| Playwright | `1.58.2` (installed version, per `frontend/package-lock.json`) |
| Context | Chromium; 1280×800; DPR 1; `en-US`; UTC; browser media dark |
| Comparator | `threshold: 0.2`, `maxDiffPixelRatio: 0.001` |
| Verification | `test.describe.configure({ mode: 'serial' })` reveals one failing case per CI run; each round's `actual.png` was pulled from that run's `mor-1400-production-visual-diagnostics` artifact and diffed pixel-by-pixel against the prior baseline before acceptance. All four rounds produced the identical bounding box (x 354–883, y 328–351, 6701 changed pixels) — the STEP toolbar row only, where the passive "A" badge is replaced by the AUTO button. No other page region changed in any of the four images. |

Only the four named production-root images below were regenerated by this
re-pin; all other i18n baselines (including the pre-existing staleness
tracked separately under MOR-1510) were left untouched.

## Linux re-pin provenance (superseded — 2026-08-13 MOR-1474 TX wording, round 1: dark only)

| Field | Value |
| --- | --- |
| Date | 2026-08-13 |
| Reason | MOR-1474's operator-legible TX wording pass changes the RxTx unknown-target line (`"TX target unknown (not-observed)"` → `"Transmit frequency unconfirmed: the radio has not reported it yet"`) — the intended change this re-pin exists for. The capture also carries two documented, unrelated ride-alongs, neither introduced by this branch: (1) a StatusBar first-indicator colour flip (red → green) that is the MOR-1526 fix (commit `f14ad18a`, #2436) working as designed — the superseded 2026-08-10 baseline captured the pre-fix disconnected-chip bug the old red icon represented; the new green is the honest post-fix state. Accepted and documented, not re-litigated here. (2) No S-meter/band-strip drift on THIS image — that anomaly was isolated to the separate `visual.yml` pixel-diff baselines (`tx-phase-tx--desktop` and its studioline sibling, `frontend/fixtures/approved-baselines/`, not tracked by this README) and is documented in the PR body: five-day-old drift from MOR-1451 (#2393, commit `5177e010`, 2026-08-11), which deleted the hardcoded dense S1..+40 fallback curve and wired the 3-anchor `S_METER_CAL` into the fixtures; MOR-1451's own re-pin run reported those scenes "passed clean / not re-pinned" — a measurement gap — so the stale dense-scale PNG sat undetected until this re-pin's diff surfaced it. Pixel-correct for current code, independently corroborated by PR #2456's visual run `31659360703` passing the same scene clean against the corrected baseline. |
| Candidate commit | `60b05b33` |
| Runner host | self-hosted Linux ARM64 (`mm-build-core-1`) |
| CI run | [Tests (quick) #31657212698](https://github.com/rigplane/rigplane-core/actions/runs/31657212698) / job `94314207483` |
| Node | `v20.20.2` |
| Playwright | `1.58.2` |
| Chromium | Chrome for Testing `145.0.7632.6`, Playwright revision `1208` |
| Context | Chromium; 1280×800; DPR 1; `en-US`; UTC; browser media dark |
| Comparator | `threshold: 0.2`, `maxDiffPixelRatio: 0.001` |
| Verification | Diffed against the superseded 2026-08-12 baseline: TWO non-overlapping changed bands, both accounted for. Band 1, y273–287 (2141 px): the RxTx unknown-target wording line — the intended MOR-1474 change. Band 2, y4–23 (80 px): the StatusBar first connectivity indicator, red → green — attributed to the already-merged MOR-1526 (#2436) StatusBar fix, accepted per the Reason field above, not introduced by this branch. No other region of the frame changed. |

Only `studioline--dark--production-root.png` was regenerated by round 1
of this re-pin; the suite's `MOR-1400 production design-language
contract` block runs `test.describe.configure({ mode: 'serial' })`
(historical; superseded by the MOR-1549 note above), so once
`studioline--light` failed immediately after `studioline--dark`
passed, the remaining serial-fail-fast SKIPPED the `fieldline--dark`,
`fieldline--light`, and two repair/inert cases outright — **they were
not compared, and round 1 establishes nothing about them.**
`studioline--light--production-root.png` renders the exact same
MOR-1474 prose line as dark and was in fact ALSO stale (known-stale,
confirmed failing on the very next run) — re-pinned in round 2 below,
from CI run
[31660813171](https://github.com/rigplane/rigplane-core/actions/runs/31660813171).
The FieldLine pair (`fieldline--dark--production-root.png`,
`fieldline--light--production-root.png`) renders the TX-target line
compactly (`RX — TX —`) and contains none of MOR-1474's changed
strings — it also still carries the pre-MOR-1526 StatusBar chip state,
which is below this comparator's ~1024px/32×32 sensitivity floor on a
1280×800 frame (README "Sensitivity floor" note, per the sibling
`fixtures/approved-baselines/README.md`'s calibration data — the same
comparator and threshold back this suite). Tracked, not re-pinned,
pending round 3 confirmation.

## Linux re-pin provenance (superseded — 2026-08-13 MOR-1474 TX wording, round 2: persisted StudioLine × light)

| Field | Value |
| --- | --- |
| Date | 2026-08-13 |
| Reason | Round 1 above regenerated only `studioline--dark--production-root.png`; the suite's serial fail-fast then SKIPPED the other three cases without comparing them, so round 1 could not establish whether they were also stale. This round targets the very next case the suite would have compared: `studioline--light--production-root.png` renders the identical MOR-1474 RxTx wording line dark does, and CI run 31660813171 (the first run to actually reach and compare it) confirmed it was ALSO stale — 2470 px / 1% of frame, the same single-line wording delta as dark's round 1 PLUS the same StatusBar first-indicator red → green ride-along dark carried (the already-merged MOR-1526 fix, #2436, `f14ad18a` — not introduced by this branch). |
| Candidate commit | `db34b81c` (the code/pixel state CI run 31660813171 tested; `2228af7c`, this repo's docs-only follow-up commit, ships byte-identical frontend output) |
| Runner host | self-hosted Linux ARM64 (`mm-build-core-1`) |
| CI run | [Tests (quick) #31660813171](https://github.com/rigplane/rigplane-core/actions/runs/31660813171) / job `94325055063` |
| Node | `v20.20.2` |
| Playwright | `1.58.2` |
| Chromium | Chrome for Testing `145.0.7632.6`, Playwright revision `1208` |
| Context | Chromium; 1280×800; DPR 1; `en-US`; UTC; browser media dark |
| Comparator | `threshold: 0.2`, `maxDiffPixelRatio: 0.001` |
| Verification | Diffed against the pre-round-1 baseline (same content `studioline--light` had carried since 2026-08-12): TWO non-overlapping changed bands, both accounted for. Band 1, y273–287 (2406 px): the RxTx unknown-target wording line — the intended MOR-1474 change. Band 2, y12–22 (64 px): the StatusBar first connectivity indicator, red → green — the already-merged MOR-1526 (#2436, `f14ad18a`) fix, the same ride-along dark carried in round 1, not introduced by this branch. 64 + 2406 = the 2470 px Playwright reported. No other region changed. `fieldline--dark`/`fieldline--light` remain unverified by this round — still SKIPPED, tracked above for round 3. |

Only `studioline--light--production-root.png` was regenerated by round
2. `fieldline--dark--production-root.png` and
`fieldline--light--production-root.png` remained unverified pending a
run that reached them — resolved in round 3 below.

## Linux re-pin provenance (superseded — 2026-08-13 MOR-1474 TX wording, round 3: full-suite confirmation, no further i18n re-pin)

| Field | Value |
| --- | --- |
| Date | 2026-08-13 |
| Reason | This branch's separate merge with `origin/main` (resolving a conflict in the SIBLING `frontend/fixtures/approved-baselines/` directory this README does not track — see that directory's own `manifest.json`, re-pinned in the same merge cycle) produced a new candidate commit. Re-running the full i18n suite against it was the first opportunity to let `test.describe.configure({ mode: 'serial' })` (historical; superseded by the MOR-1549 note above) actually REACH `persisted FieldLine × dark` and `persisted FieldLine × light` — SKIPPED, never compared, in rounds 1 and 2. This round is a confirmation run, not a re-pin: all 43 cases passed, including both FieldLine cases, against the EXISTING baselines already on disk (no FieldLine PNG bytes changed). |
| Candidate commit | `7da03bf0` (the `origin/main` merge commit) |
| Runner host | self-hosted Linux ARM64 (`mm-build-core-1`) |
| CI run | [Tests (quick) #31668014909](https://github.com/rigplane/rigplane-core/actions/runs/31668014909) / job `94346677610` |
| Node | `v20.20.2` |
| Playwright | `1.58.2` |
| Chromium | Chrome for Testing `145.0.7632.6`, Playwright revision `1208` |
| Context | Chromium; 1280×800; DPR 1; `en-US`; UTC; browser media dark |
| Comparator | `threshold: 0.2`, `maxDiffPixelRatio: 0.001` |
| Verification | `Running 43 tests using 1 worker` → `43 passed`. Per-case: the earlier i18n-visual grid (36 cases) passed as always; `clean StudioLine × dark` (round 1's pin) passed; `persisted StudioLine × light` (round 2's pin) passed; `persisted FieldLine × dark` and `persisted FieldLine × light` — reached for the first time across all three rounds — BOTH passed clean against their pre-existing, never-modified baselines, confirming the round-1/round-2 tracking note (chip-state drift below this comparator's sensitivity floor) was correct. The two repair/inert cases also passed. **Totals: 43 expected / 0 unexpected / 0 skipped.** |

No PNG bytes changed in round 3 — this run confirms the FieldLine pair
needed no re-pin at all. All four `production-design-language` images
are now independently CI-confirmed clean on the same commit
(`7da03bf0`).

## Linux re-pin provenance (superseded — 2026-09-04 MOR-2309 peer-split dual surfaces)

| Field | Value |
| --- | --- |
| Date | 2026-09-04 |
| Reason | MOR-2309 changes `PeerSplitLayout`'s semantic mount from its default strip set to the explicit `strips="dual"` composition. That intentional registration makes the shared dual-receiver information strips visible in the production root under both StudioLine and FieldLine; the four pre-MOR-2309 expectations therefore described the old, incomplete mount. |
| Source code commit | `e5fe1ee1bd2c5837a501c9de902a9f45c7aa9c56` |
| Baseline source | The four `actual.png` attachments from the failed comparison below; the committed PNGs are byte-identical to those Linux captures. |
| Runner host | self-hosted Linux ARM64 `mm-build-core-1` (`self-hosted`, `linux`, `build`) |
| CI run | [Tests (quick) #33833489943](https://github.com/rigplane/rigplane-core/actions/runs/33833489943) / job `100901197290` |
| Node / npm | `v20.20.2` / `10.8.2` |
| Playwright | `1.58.2` |
| Chromium | Chrome for Testing `145.0.7632.6`, Playwright revision `1208` |
| Context | Chromium; 1280×800; DPR 1; `en-US`; UTC; browser media dark/light as named by each case |
| Comparator | `threshold: 0.2`, `maxDiffPixelRatio: 0.001` |
| Verification | The run executed all 43 cases: the 36 locale/viewport smoke cases passed; all four production-root comparisons failed against the superseded expectations and emitted stable Linux captures; the invalid-persistence repair, legacy-LCD inertness, and mobile inertness cases then passed. Per image, Playwright reported 5,962 changed pixels for StudioLine dark, 6,197 for StudioLine light, 2,986 for FieldLine dark, and 4,159 for FieldLine light. Artifact-to-commit SHA-256 comparison is exact for every replacement PNG. |

Only the four named production-root images below were regenerated. No other
i18n screenshot, production source, fixture, or test changed in this re-pin.

## Linux re-pin provenance (superseded — 2026-09-05 UTC MOR-2342 upper instruments)

| Field | Value |
| --- | --- |
| Source code commit | `ced40328a8c9d61156f46e443a79c105247be69c` |
| CI run / job | [Tests (quick) #33935459868](https://github.com/rigplane/rigplane-core/actions/runs/33935459868) / `101222416351` |
| Runner | self-hosted Linux `mm-build-core-2` |
| Context / comparator | Chromium, 1280×800, DPR 1, en-US, UTC; the unchanged production config uses `threshold: 0.2`, `maxDiffPixelRatio: 0.001`. |
| Source | Byte-identical `actual.png` attachments from `mor-1400-production-visual-diagnostics`; no macOS-generated baseline. |
| Reason | MOR-2342 restores receiver instrument panels and a central VFO bridge. Design-language typography/FieldLine rails remain active; instrument and button backgrounds use theme tokens in dark and light modes. |

All four scenes are **compared-fail**, inspected and accepted for re-pin:
StudioLine dark 8,378 changed pixels; StudioLine light 10,005; FieldLine dark
30,860; FieldLine light 36,712. Diffs are in the upper instrument area;
sidebars, spectrum and status chrome remain unchanged at the comparator's
sensitivity. Light actuals were checked for readable text after correcting
the earlier hardcoded dark background. The unchanged CSS/accessibility
assertions completed before the four pixel comparisons; the other 39 cases
passed, including presentation repair. No scene was skipped. A subsequent exact-head run must confirm
these expectations; this comparison run itself is not a visual PASS.

## Linux re-pin provenance (superseded — 2026-09-05 UTC MOR-2342 desktop controls)

| Field | Value |
| --- | --- |
| Source code commit | `6a3ae096209abffdea2184846dc5926e1386f3af` |
| CI run / job | [Tests (quick) #33936293197](https://github.com/rigplane/rigplane-core/actions/runs/33936293197) / `101224798047` |
| Runner | self-hosted Linux `mm-build-core-1` |
| Source | Byte-identical `actual.png` attachments from `mor-1400-production-visual-diagnostics`; no macOS-generated baseline. |
| Context / comparator | Unchanged Chromium production config: 1280×800, DPR 1, en-US, UTC; `threshold: 0.2`, `maxDiffPixelRatio: 0.001`. |
| Reason | The desktop shell now groups semantic controls into scrolling side columns, with TX/recovery first on the right, scope controls in the center and station meters below. This removes the clipped Standard deck. |

Each scene is **compared-fail**, inspected and accepted for re-pin:

| Scene | Changed pixels | Disposition |
| --- | ---: | --- |
| StudioLine dark | 21,565 | Accepted semantic side panels, visible Unkey, scope and meters placement. |
| StudioLine light | 145,338 | Same geometry and theme-correct control chrome; labels remain readable. |
| FieldLine dark | 35,057 | Same grouping; language borders and TX fault rail preserved. |
| FieldLine light | 162,867 | Same grouping and preserved rails; light panels and Unkey remain readable. |

The upper/status offsets follow the new grid gap and removal of the enclosing
clipped deck. UNKNOWN text and language typography are retained. Legacy band
and memory extras follow the semantic panels in the scrollable columns.
All four actual/diff pairs were inspected. CSS/accessibility assertions passed
before comparison; the other 39 cases passed, none skipped. These captures use
simulated production state and do not establish hardware acceptance. A subsequent
exact-head CI run must confirm the new expectations; this failed comparison is
not a visual PASS.

## Linux re-pin provenance (superseded — 2026-09-06 MOR-2384 / MOR-2385)

| Field | Value |
| --- | --- |
| Source code commit | `38eb8371c1ee6e9e23221ddc092c4b3ba9d8c46c` |
| CI run / artifact | [Tests (quick) #34012673065](https://github.com/rigplane/rigplane-core/actions/runs/34012673065) / `9982977739` |
| Source | Byte-identical Linux `actual.png` files from the run artifact; no macOS-generated baseline or image transformation. |
| Reason | MOR-2384/MOR-2385 intentionally render unavailable Filter Width as disabled and preserve the fixture's truthful `SRC hardware` / `inactive` / `HW off` scope state. The production capture now waits for those final DOM facts. |

The source run compared all four production scenes. StudioLine dark and
FieldLine dark were **compared-pass** and their baseline bytes remain unchanged.
StudioLine light and FieldLine light were **compared-fail**; root inspected both
complete expected/actual/diff triplets and DOM contexts, accepted the disabled
Filter Width and inactive/off scope output, and replaced only those two LIGHT
expectations. This source run predates the readiness assertion, so the final
candidate's natural CI result is not yet known and must confirm all four scenes.

| Scene | Disposition | SHA-256 |
| --- | --- | --- |
| StudioLine dark | compared-pass; unchanged | `64361ab2df44851f4ac357764684b2445476e149ddd58a4404dee4e76a7acfd7` |
| StudioLine light | compared-fail; inspected and accepted replacement | `78f6f139e8365b17a2ed39f9434ed8b6e66282201761fb0bec3f605b971fbda9` |
| FieldLine dark | compared-pass; unchanged | `ad07aeb66906aa4c761eb413fb44f2fb5c3944bd7a4f2a8a2b618229142526fc` |
| FieldLine light | compared-fail; inspected and accepted replacement | `e2fce8500f913ab2dad19aea454825330b0cca78f164f98a5708c6d0c0357e46` |

## Linux re-pin provenance (superseded — 2026-09-06 MOR-2388 Standard VFO)

| Field | Value |
| --- | --- |
| Source code commit | `c48d5d2bc044048a99a6104bf34366b5b77be002` |
| Capture environment | Isolated container from pinned Linux ARM64 image `rigplane-ci-build-core-arm64-runner:85ab46b30c39`, image ID `sha256:52573f10379c2bb5e7076f7484cf0d266ead0fe18abce2fd42eb6047758f9be1`; this was not a GitHub Actions capture. |
| Source | Byte-identical `actual.png` files from the isolated capture; no image editing, masking, transformation, or macOS-generated baseline. |
| Node / npm | `v20.20.2` / `10.8.2` |
| Playwright | `1.58.2` |
| Chromium | Chrome for Testing `145.0.7632.6`, Playwright revision `1208` |
| Command | `npx playwright test -c playwright.i18n.config.ts tests/e2e/i18n/i18n-visual.spec.ts --grep "activates from production dist"` |
| Context / comparator | Chromium, Linux ARM64, 1280×800, DPR 1, `en-US`, UTC; unchanged `threshold: 0.2`, `maxDiffPixelRatio: 0.001`. |
| Reason | MOR-2388 intentionally replaces Standard's receiver display with the reusable v2.11.1 VFO instrument driven by explicit v3 inputs. Root inspected and accepted all four complete expected/actual/diff pairs. |

All four production cases reached screenshot comparison after the unchanged
production-dist readiness, CSS, and accessibility assertions passed. The source
capture was intentionally **compared-fail** against the superseded expectations:
StudioLine dark changed by 3,487 pixels, StudioLine light by 53,439, FieldLine
dark by 17,697, and FieldLine light by 63,272. Root adjudicated every pair and
activated the exact four-image lease. The dark replacements also incorporate
the previously accepted MOR-2384/MOR-2385 inactive/HW-off scope and unavailable
Filter Width pixels; those are retained dependency output, not new MOR-2388 VFO
behavior. A subsequent exact-head CI run must confirm the new expectations.

| Scene | Disposition | SHA-256 |
| --- | --- | --- |
| StudioLine dark | compared-fail; inspected and accepted replacement | `0364d91633aedd4ab469fb5ab41277e5625d8b754d8cb139396d9ed79804ca40` |
| StudioLine light | compared-fail; inspected and accepted replacement | `9b6ef98b8aabb500ee3cecd008127ffb4cc42f4bba8a4454f4e9ac54c0143d4f` |
| FieldLine dark | compared-fail; inspected and accepted replacement | `84cc82fc887824ea3c67e0bb9a5f7302e3c091b8e11fb6eb92b2be52644c7c0d` |
| FieldLine light | compared-fail; inspected and accepted replacement | `88e857698e07fffb52c9e92c975546b8b39274695658e666a1ab90a9212a2238` |

## Linux re-pin provenance (superseded — 2026-09-08 MOR-2425 no per-field freshness)

| Field | Value |
| --- | --- |
| Source code commit | `d3677f0d8e70f27dd4c9044b85b2247edffc9542` |
| CI run / job | [Tests (quick) #34205828119](https://github.com/rigplane/rigplane-core/actions/runs/34205828119) / job `101994995085` |
| Runner | self-hosted Linux (job labels `self-hosted`, `linux`, `build`) |
| Node / npm | `v20.20.2` / `10.8.2` |
| Playwright | `1.58.2` |
| Chromium | Chrome for Testing `145.0.7632.6`, Playwright revision `1208` |
| Command | `npm run test:e2e:i18n` (`playwright test -c ./playwright.i18n.config.ts`) |
| Context / comparator | Chromium; 1280×800; DPR 1; `en-US`; UTC; unchanged `threshold: 0.2`, `maxDiffPixelRatio: 0.001`. |
| Source | Byte-identical `actual.png` attachments from `mor-1400-production-visual-diagnostics`; no macOS-generated baseline or image transformation. |
| Reason | Owner ruling: the v2 face carries no pixel protection and R41 forbids painting per-field freshness. This PR deletes the `†` stale-cue markup (VFO frequency, RFG badge, PBT filter fields), the SpectrumPanel `◷ <stale>` chip container, and widens the VFO/filter "usable" gates so a held (stale) reading stays enabled; it also widens the S-meter's retained-value gate. |

The run compared 87 i18n cases; 84 passed. Of the four named scenes,
`persisted FieldLine × dark` was **compared-pass** (unchanged); the other
three were **compared-fail** and are re-pinned below. Each flagged band was
located by cropping the `-expected.png`/`-actual.png` pair at Playwright's
reported coordinates and, where a shift was suspected, finding that text's
exact row band by pixel-intensity search rather than by eye:

- MAIN/SUB S-meter label (`y89-101`/`y106-111` in the two StudioLine scenes,
  `y100-121` in FieldLine light; two ~58px-wide bands
  symmetric left/right, 86/68/74 red px in dark/light StudioLine/FieldLine
  light respectively): `"S ?"` becomes `"?"` plus a new `"unit unknown"`
  line, and the calibrated scale marks disappear. Traced to the
  domain/`scaleMode` computation in `radio-view-model-adapter.ts` /
  `smeter-scale.ts`, downstream of this PR's S-meter retained-value
  widening — in this captured state the meter's own value stays `null`
  (receiver is `UNKNOWN`, not `stale`), so no value is actually held here;
  only the label format changed.
- The "Scope disconnected — reconnecting…" text (`x300-790`, `y426-437`)
  and the dB-scale tick column (`x260-276`, `y360-483`) were re-measured
  pixel-for-pixel — `old-*`/`new-*` PNG arrays (`old` = commit `ec90170d3`,
  byte-identical to `907609b0c`; `new` = this PR's head), numpy `abs` diff
  swept over `dy` (`uv run --with pillow --with numpy python3` against the
  extracted PNGs). The text block is a byte-identical **16px upward
  translation**: mean |Δ|=0.0000, max=0 at `dy=-16`, vs. mean=9.83/9.75,
  max=138 one pixel either side. The tick column does **not** translate as
  a block — it is not "uniform": best `dy=-15` still leaves mean |Δ|=1.51,
  max=124 (vs. mean=11.63, max=124 unshifted at `dy=0`); its marks re-spread
  within the taller `.db-scale` container instead of moving together. The
  16px figure matches the `.passband-freshness` div's `min-height: 1.4em`
  (~15.4px at its 11px font) — the `◷ <stale>` chip container removed from
  `SpectrumPanel.svelte`.
- Sidebar band-plan cards (`y619-694`, `x20-220`, the same reflow in all
  three scenes): `"160M TX"` renders as `"160MTX"`. This **is**
  explained, and it is main drift that predates this PR, not this PR's own
  change: at `907609b0c` (the commit these baseline PNGs were last pinned
  against) `frontend/src/semantic/BandSurface.svelte` wrote
  `>{choice.name}` followed by a newline then `<small>` — HTML collapses
  that whitespace to one space, rendering `"160M TX"`
  (`git show 907609b0c:frontend/src/semantic/BandSurface.svelte`). By
  `ec90170d3` (this PR's own base commit) the BandSurface→BandInstrumentHost
  extraction (`3536ae765`, #3334, between the two) had
  rewritten it as `frontend/src/semantic/BandInstrumentHost.svelte`'s
  `>{choice.name}<small>` with no whitespace between them, rendering
  `"160MTX"` (`git show ec90170d3:frontend/src/semantic/BandInstrumentHost.svelte`).
  Card borders and all other rows sit at unchanged y-coordinates (a pure
  horizontal reflow), and the markup change happened before this PR's base
  commit, so it is not attributed to this PR.
- StudioLine-only band flagged at `y340-348`, `x747-793`, swept over
  `y339-347`, `x744-796` (both StudioLine scenes;
  absent from FieldLine): re-measured the same way as above — a
  byte-identical **1px downward translation** of the "Classic"
  colour-scheme dropdown text (mean |Δ|=0.40, max=1 at `dy=+1`, vs.
  mean=18.37, max=112 unshifted at `dy=0`). This region is pixel-identical
  between StudioLine dark and StudioLine light (a native `<select>` ignores
  the page theme), and the adjacent "BANDS" button at `x830-900` is
  byte-identical unshifted (mean=0, max=0) — only the dropdown text moves.
  `frontend/src/components/spectrum/SpectrumPanel.svelte` **is** in this
  PR's diff and lays out this control's toolbar row (`SpectrumToolbar` is
  its first child, itself untouched by this PR); the only change in that
  file is the `.passband-freshness`/`◷` chip removal, which sits after the
  toolbar in the DOM. I could not trace, within this pass, how removing
  content positioned after the toolbar moves the toolbar's own text by one
  device pixel — reported as an untraced 1px shift inside a file this PR
  changes, not as unexplained-and-unrelated.

| Scene | Disposition | SHA-256 |
| --- | --- | --- |
| StudioLine dark | compared-fail (1,220 px); inspected and accepted | `382397c3276e161b047d10cc4727f711b928a88fa08a5d70a9eec1f0b736744d` |
| StudioLine light | compared-fail (1,296 px); inspected and accepted | `422305cef245bc71348c6be01aba3863b73acdca0290f622abccd7e5ef63d352` |
| FieldLine dark | compared-pass; unchanged | `e95e6f6a00c0231fea3b17941008df9eac4f8be74ece6ea2094d6267209e51f0` |
| FieldLine light | compared-fail (1,265 px); inspected and accepted | `1369eca0c91fa57ceaa2f2d5055ea6635cc86c00ab23bb1b047f3694a05de121` |

A subsequent exact-head CI run must confirm these expectations; this
comparison run is not itself a visual PASS.

## Linux re-pin provenance (superseded — 2026-09-09 MOR-2432 Standard meter readouts)

| Field | Value |
| --- | --- |
| Source code commit | `06aea378b01b8ca720ecfa40e36bcff247e28d39` |
| CI run / job | [Tests (quick) #34397437366](https://github.com/rigplane/rigplane-core/actions/runs/34397437366) / job `102620546676` |
| Runner | `mm-build-core-3`; self-hosted Linux (labels `self-hosted`, `linux`, `build`) |
| Command | `npm run test:e2e:i18n` (`playwright test -c ./playwright.i18n.config.ts`) |
| Context / comparator | Chromium; 1280×800; DPR 1; `en-US`; UTC; unchanged `threshold: 0.2`, `maxDiffPixelRatio: 0.001`. |
| Source | Exact final-run `actual.png` attachments from `mor-1400-production-visual-diagnostics`, copied byte-for-byte; no image transformation or fixture/macOS replacement. |
| Reason | Native Standard station meters expose readable projected captions, including independently relevant S and SWR readouts. SVGs fit below captions; the original shared 160px grid and non-Standard appearance remain intact. |

The source run completed all 89 i18n cases: 85 passed, including the production
geometry cases; only these four screenshot comparisons failed. Their final
actual/diff pairs were inspected and accepted by the coordinator. Counts and
command above were read from `gh run view 34397437366 --log-failed`; source
SHA and runner were checked through the Actions run/job API. Each committed
PNG was compared byte-for-byte with its named actual attachment and hashed
with SHA-256. No test, comparator, mask or threshold changed.

FieldLine dark also incorporates inherited VFO-meter baseline drift already
documented in the MOR-2425 provenance above: `S ?` becomes `?` plus
`unit unknown`, with no calibrated scale marks. Independent adjudication of
the earlier PR actuals at `49215e64` and `e13ac579` used Pillow/NumPy to
compare the entire 1280×245 top band: zero changed decoded pixels between
those actuals. The earlier expected/actual diff marked 160 comparator-red
pixels in that band, below the unchanged 1,024-pixel whole-image budget;
3,776 raw changed pixels were a different measure. This explains why the
older FieldLine-dark expectation could remain compared-pass. Those band
counts describe the earlier adjudication, not this final run's whole-image
counts below. The VFO domain/projection source is unchanged by MOR-2432;
this re-pin does not claim a new VFO behavior change.

| Scene | Disposition | SHA-256 |
| --- | --- | --- |
| StudioLine dark | compared-fail (1,411 px); inspected and accepted | `54bbe78a2eac8ed90193fa332159e17084ed3448ac50de7d72108d376877dd48` |
| StudioLine light | compared-fail (20,634 px); inspected and accepted | `8bdb9204889cf8392673d7a248be96c64e0a793b0bc96794b760aeca34acbc3f` |
| FieldLine dark | compared-fail (2,049 px); inspected and accepted | `41024a2504b70a94fa51adc2135cd9c20905307e4530ee035e3a90dbe7aba459` |
| FieldLine light | compared-fail (21,063 px); inspected and accepted | `dbad846332a8e3a3abe0ff299df1e1d056708eeccd704f7067b1bbe7e41375f2` |

This source capture is **compared-fail**, not a visual PASS. A subsequent
exact-head CI run must compare successfully against the replacements.
Physical radio/profile acceptance remains separate.

## Linux re-pin provenance (superseded — 2026-09-14 MOR-2467 Standard panel cleanup)

| Field | Value |
| --- | --- |
| Source code commit | `bc49462ceb5b4e85322b373e7b7a4182aad18751` |
| CI run / job | [Tests (quick) #34917273359](https://github.com/rigplane/rigplane-core/actions/runs/34917273359) / job `104217554871` |
| Runner | `mm-build-core-2`; self-hosted Linux (labels `self-hosted`, `linux`, `build`) |
| Command | `npm run test:e2e:i18n` (`playwright test -c ./playwright.i18n.config.ts`) |
| Context / comparator | Chromium; 1280×800; DPR 1; `en-US`; UTC; unchanged `threshold: 0.2`, `maxDiffPixelRatio: 0.001`. |
| Source | Exact `actual.png` attachments from `mor-1400-production-visual-diagnostics`, copied byte-for-byte; no image transformation or macOS baseline. |
| Reason | Standard removes the duplicate BAND surface and volatile TX/RX Audio diagnostic prose while retaining BANDS frequency entry and stable operator controls. |

The source run completed 95 i18n cases: 90 passed; one geometry assertion
needed to recognize clipped `sr-only` reasons as visually hidden, and the four
production-root comparisons failed against the superseded UI. The coordinator
inspected all four actual/diff pairs: changes are confined to the intended
BANDS, TX, RX Audio, and directly displaced panel regions. No comparator,
mask, threshold, or screenshot-generation code changed. A subsequent exact-head
CI run must compare successfully against these replacements.

| Scene | Disposition | SHA-256 |
| --- | --- | --- |
| StudioLine dark | compared-fail (7,544 px); inspected and accepted | `357a53837101374b37891edebe5e26da50e8036c01a92d185eaa1d033b008567` |
| StudioLine light | compared-fail (10,890 px); inspected and accepted | `75cbe648157d04ffd49a2b2f93f41426fa9d0686c672fa67bb6dbf0c732e546c` |
| FieldLine dark | compared-fail (6,867 px); inspected and accepted | `960cb61d8b5e9b5a00a3f8611043603df7723aa70aa4e387ce52036d02b1a7c7` |
| FieldLine light | compared-fail (13,366 px); inspected and accepted | `2d972c5647ea99eef22ac4bffa99f3e32683427af2ebbb98aab0111142b296d5` |

## Linux re-pin provenance (superseded — 2026-09-23 MOR-2545 PR1 scope controls in one row)

| Field | Value |
| --- | --- |
| Source code commit | `7fbb04c3d177ef4292a10837fa5e68103909397e` (branch `codex/scope-one-row-radio-held`, PR #3593) |
| CI run / job | [Tests (quick) #35879412060](https://github.com/rigplane/rigplane-core/actions/runs/35879412060) / job `107243761806` |
| Command | `npm run test:e2e:i18n` (`playwright test -c ./playwright.i18n.config.ts`) |
| Source | the `actual` attachments of the four MOR-1400 production-root cases in that run's `mor-1400-production-visual-diagnostics` artifact (`playwright-report/data/*.png`, 1280×800), matched to their scene as the attachment nearest to that scene's superseded baseline, copied byte-for-byte |
| Reason | MOR-2545 PR1: the scope controls above the spectrum become one row of flat keys (`CTR FIX REF ‹ › HOLD ⋯` in these fixtures) with the remaining controls behind `⋯`, so the scope toolbar and spectrum below it move up. Also changed and inspected: the right column's panels sit a few pixels higher, and the status bar's TOT key (y 10–36) no longer shows the focus ring it had in the superseded baseline. Deltas measured against the superseded baselines with Pillow `ImageChops.difference(...).convert('L') > 8`. |

| Scene | Disposition | Changed px (of 1,024,000) | Changed bbox (x0, y0, x1, y1) | SHA-256 |
| --- | --- | --- | --- | --- |
| StudioLine dark | compared-fail; inspected (one scope row, content below it moved) and accepted | 78,911 | (238, 10, 1275, 628) | `09949387810aefdf88900043435feb50b9ececadc0878bad5b10b7219776abb6` |
| StudioLine light | compared-fail; inspected (one scope row, content below it moved) and accepted | 86,731 | (238, 10, 1275, 628) | `274f04fc0a6f86ee64d4b8033793630117c364788ec88d46ed5a2c3de2081e02` |
| FieldLine dark | compared-fail; inspected (one scope row, content below it moved) and accepted | 91,009 | (238, 10, 1275, 631) | `29278a30e2f3ff826f5c895a9becfb9f5fa00de26a689faf9ece90d5e5447e15` |
| FieldLine light | compared-fail; inspected (one scope row, content below it moved) and accepted | 90,431 | (238, 10, 1275, 631) | `36b1363d4b59c6cc6fb6d6d6f51b2bf990522670c073bd5ddff604cbdc04ef03` |

A subsequent exact-head quick run must confirm these four comparisons pass.

## Linux re-pin provenance (superseded — 2026-09-23 MOR-2545 PR2 one-row panorama toolbar)

| Field | Value |
| --- | --- |
| Source code commit | `31e88927442006d6269c1283e84e82f12212e8a7` (branch `codex/scope-one-row-toolbar`, PR #3598) |
| CI run / job | [Tests (quick) #35916095127](https://github.com/rigplane/rigplane-core/actions/runs/35916095127) / job `107367888036` |
| Command | `npm run test:e2e:i18n` (`playwright test -c ./playwright.i18n.config.ts`) |
| Source | the `actual` attachments of the four MOR-1400 production-root cases in that run's `mor-1400-production-visual-diagnostics` artifact, identified by their attachment names (`<scene>--production-root-actual.png`) in the report's embedded test results, copied byte-for-byte |
| Reason | MOR-2545 PR2: the radio-held keys (`CTR FIX REF ‹ › HOLD ⋯`), STEP, BANDS, the compact `● SRC` status and fullscreen form one toolbar row. The separate status line ("SRC hardware inactive HW off") and the second toolbar row (AUTO, VIEW, AVG, PEAK, BRT, palette, BANDS) are gone from above the spectrum, and the spectrum area moves up. Deltas are measured against the superseded baselines with Pillow `ImageChops.difference(...).convert('L') > 8`. |

| Scene | Disposition | Changed px (of 1,024,000) | Changed bbox (x0, y0, x1, y1) | SHA-256 |
| --- | --- | --- | --- | --- |
| StudioLine dark | compared-fail; inspected (one toolbar row, spectrum area moved up) and accepted | 63,141 | (238, 251, 1042, 443) | `2a637b5acb3e5e98c5027cb4764f5ce5a965ec18a8b8b601d1dbbb580dd818c4` |
| StudioLine light | compared-fail; inspected (one toolbar row, spectrum area moved up) and accepted | 85,287 | (238, 250, 1042, 443) | `a17195a363ed392416c8c5c59fdb38dc295a63945a9b1fc126a7d361f69cb461` |
| FieldLine dark | compared-fail; inspected (one toolbar row, spectrum area moved up) and accepted | 76,607 | (238, 272, 1042, 459) | `b418b336b83343d773c6327adfc18f40a625968f93c105b53d7cc524c3740ece` |
| FieldLine light | compared-fail; inspected (one toolbar row, spectrum area moved up) and accepted | 84,757 | (238, 272, 1042, 459) | `44a175ceed647a030ff96b19c0f20aa8f7fb31b65788362be828689a84595287` |

A subsequent exact-head quick run must confirm these four comparisons pass.

## Linux re-pin provenance (current — 2026-09-23 MOR-2545 PR3 capsule-group row)

| Field | Value |
| --- | --- |
| Source code commit | `f96af8cbdd739534dfcbe4a475021d33544ca490` (branch `codex/scope-row-capsule-style`, PR #3606) |
| CI run / job | [Tests (quick) #35948673003](https://github.com/rigplane/rigplane-core/actions/runs/35948673003) / job `107472277272` |
| Command | `npm run test:e2e:i18n` (`playwright test -c ./playwright.i18n.config.ts`) |
| Source | the `actual` attachments of the four MOR-1400 production-root cases in that run's `mor-1400-production-visual-diagnostics` artifact, identified by their attachment names (`<scene>--production-root-actual.png`) in the report's embedded test results, copied byte-for-byte |
| Reason | MOR-2545 PR3: the panorama row takes the capsule-group style. CTR\|FIX, REF ‹ ›, HOLD, ‹ STEP value › and BANDS become bordered capsules on one ground, the ⋯ key becomes MORE ▾, and the SRC status and fullscreen join the same family. The spectrum area moves up by 3 px, found by aligning the old and new spectrum area (x 239–1041) over vertical offsets −8…+8. Deltas are measured against the superseded baselines with Pillow `ImageChops.difference(...).convert('L') > 8`. |

| Scene | Disposition | Changed px (of 1,024,000) | Changed bbox (x0, y0, x1, y1) | SHA-256 |
| --- | --- | --- | --- | --- |
| StudioLine dark | compared-fail; inspected (capsule row, spectrum area 3 px higher) and accepted | 20,344 | (239, 251, 1041, 408) | `e4dcd807a47cd0753d0e31cd21dba180a08c5ac3387d37aedbbee384fc02a9d2` |
| StudioLine light | compared-fail; inspected (capsule row, spectrum area 3 px higher) and accepted | 23,753 | (239, 251, 1041, 408) | `f19ed24d9a2abf060e49aaef21640599456e8d67c122e35f2c97efe12c311a5e` |
| FieldLine dark | compared-fail; inspected (capsule row, spectrum area 3 px higher) and accepted | 17,209 | (239, 273, 1041, 424) | `d9696f148607f6ed9b20e4b497b7f9b2ed54de0761e820c97c736f27d3e35d8b` |
| FieldLine light | compared-fail; inspected (capsule row, spectrum area 3 px higher) and accepted | 23,764 | (239, 273, 1041, 424) | `87139bd73a034f14e59d1dc47d21f326f62a4dbf49e3029daa67f1cd06be3ee5` |

A subsequent exact-head quick run must confirm these four comparisons pass.

## Linux re-pin provenance (superseded — 2026-09-22 MOR-2509 correction round 2: dim inactive receiver, v8 S-meter, hardware-key bridge)

| Field | Value |
| --- | --- |
| Source code commit | `99418f5d849a03d0796fa3cad4b9e11bce2456d5` (branch `codex/mor-2509-r2-batch`, PR #3570) |
| CI run / job | [Tests (quick) #35768979309](https://github.com/rigplane/rigplane-core/actions/runs/35768979309) / job `106885633422` |
| Command | `npm run test:e2e:i18n` (`playwright test -c ./playwright.i18n.config.ts`) |
| Source | the `actual` attachments of the four MOR-1400 production-root cases in that run's `mor-1400-production-visual-diagnostics` artifact (`playwright-report/data/*.png`, 1280×800), classified by content hash / red-pixel share / nearest scene with `v7_classify.py`, copied byte-for-byte |
| Reason | MOR-2509 correction round 2 (owner remarks at the stand 2026-09-22): the inactive receiver's frequency and name are dimmed and its panel quieter, the panel sheen applies in both colour modes, the S-meter follows mock-up v8 (548 px flex track, odd S-unit labels plus declared `+` knots), and the bridge is 212 px wide with `HardwareButton` edge-left keys filling the block; the deck's height changes, so every row below it moves. Deltas measured against the superseded baselines with Pillow `ImageChops.difference(...).convert('L') > 8`, so the threshold is 8/255 on the luminance of each RGB difference; the bounding box spans from the deck's top edge (y 46–52) to the last content row (y 630–631) in every scene. |

| Scene | Disposition | Changed px (of 1,024,000) | Changed bbox (x0, y0, x1, y1) | SHA-256 |
| --- | --- | --- | --- | --- |
| StudioLine dark | compared-fail; inspected (deck redrawn, rows below shifted) and accepted | 134,156 | (5, 52, 1275, 630) | `873b45ed9cc2bd7352b8746c05ac5787217b1e864edcd14cc098e67e7c1c1f88` |
| StudioLine light | compared-fail; inspected (deck redrawn, rows below shifted) and accepted | 158,646 | (5, 46, 1275, 630) | `499d6b3eca0731d593c92f675071189f846e9b957d88e5922faafc1f9f95e92f` |
| FieldLine dark | compared-fail; inspected (deck redrawn, rows below shifted) and accepted | 262,575 | (5, 52, 1275, 631) | `f2c1182ad58ab237531bc38eee3d45be6620b87030d4eded370a0f6efb51ea0a` |
| FieldLine light | compared-fail; inspected (deck redrawn, rows below shifted) and accepted | 188,776 | (5, 52, 1275, 631) | `060f58f2bc767b88ed2fbe40ee27d46297b99c50ff79d9691eb21774534d003e` |

A subsequent exact-head quick run must confirm these four comparisons pass.

## Linux re-pin provenance (superseded — 2026-09-22 MOR-2526 per-receiver band)

| Field | Value |
| --- | --- |
| Source code commit | `977ff1d2` (branch `codex/mor-2526-per-receiver-band` after merging `main` ac730b76) |
| CI run / job | [Tests (quick) #35755221418](https://github.com/rigplane/rigplane-core/actions/runs/35755221418) / job `106839257328` |
| Command | `npm run test:e2e:i18n` (`playwright test -c ./playwright.i18n.config.ts`) |
| Source | the `actual` attachments of the four MOR-1400 production-root cases in that run's `mor-1400-production-visual-diagnostics` artifact (`playwright-report/data/*.png`, 1280×800), classified by content hash / red-pixel share / nearest scene with `v7_classify.py` |
| Reason | MOR-2526: the tray band tab lights from each receiver's own frequency (`BandViewModel.receiverBands`) instead of from the radio-wide reading gated on the active receiver. The i18n mock state carries known MAIN (20 m) and SUB (40 m) frequencies but no observed active receiver, so both tabs read unlit `BAND` before and `20M` / `40M` lit now; the change is confined to the two tray rows (bbox x 253–1027, y 53–73 dark / 53–349 light incl. anti-aliasing). Deltas measured with `v6_pixdiff.py` (threshold 8/255). |

| Scene | Disposition | Changed px (of 1,024,000) | SHA-256 |
| --- | --- | --- | --- |
| StudioLine dark | compared-fail; inspected (both tray band tabs lit) and accepted | 2,218 | `1a921f02992218117c736c1c25e445d6b9b6b7d6c2c40f997137f089df19d6ad` |
| StudioLine light | compared-fail; inspected and accepted | 2,421 | `a7e1a4107a03c0bebcba9f4056e360b3254eb2f8dc07f8494246f61c1785fb9a` |
| FieldLine dark | compared-fail; inspected and accepted | 2,415 | `46aa2dd27713d0d8ca717594a03562004d37e9a2ddce7dfdc38cd19e518d9b5c` |
| FieldLine light | compared-fail; inspected and accepted | 2,433 | `5f130150b42bc86b07bce8c5678ced45b066a75c637937a641fd5d56644cbb4c` |

A subsequent exact-head quick run must confirm these four comparisons pass.

## Linux re-pin provenance (superseded — 2026-09-22 MOR-2530 no filter selector → width follows the `filter_width` tag)

| Field | Value |
| --- | --- |
| Source code commit | `4dfcedbba7219a0fb89797b553932b442dc8379b` |
| CI run / job | [Tests (quick) #35751501254](https://github.com/rigplane/rigplane-core/actions/runs/35751501254) / job `106826588818` |
| Command | `npm run test:e2e:i18n` (`playwright test -c ./playwright.i18n.config.ts`) |
| Source | the `actual` image attachments of the four MOR-1400 production-root cases in that run's `mor-1400-production-visual-diagnostics` artifact (`playwright-report/data/*.png`, 1280×800), classified against the previous baselines by content hash (expected), red-pixel share (diff) and nearest scene (actual) with `v7_classify.py` |
| Reason | MOR-2530 splits "has a filter selector" (a non-empty `caps.filters`) from "has a width control" (`filter_width` in `caps.capabilities`). The i18n `mockCapabilities` (`fixtures.ts`, model IC-7610) declares `filters` but carries only `['scope','audio','tx']`, so in this synthetic scene the width facts read structurally absent: the tray `BW` tab and the filter panel's Width row are no longer drawn and the rows below move up. A real IC-7610 profile declares `filter_width` (all eight `rigs/*.toml` except x6100/x6200) and keeps both. Pixel deltas measured against the superseded baselines with `v6_pixdiff.py` (threshold 8/255 per channel). |

| Scene | Disposition | Changed px (of 1,024,000) | SHA-256 |
| --- | --- | --- | --- |
| StudioLine dark | compared-fail; inspected (BW tab and Width row absent, rows below shifted) and accepted | 20,287 | `0f7fcda01a0fc0a7d4531202d6668660c7dc4041019eed6dae0cb0930b27638c` |
| StudioLine light | compared-fail; inspected and accepted | 19,567 | `d27167565d017c06f05388625f3f592a2adc27c86082fe1b0b21b6984635ca66` |
| FieldLine dark | compared-fail; inspected and accepted | 25,455 | `83c9f215aac3d70ac4e1315491e9d2e5fbfa55b9a0af3a0ff7357028d3311db9` |
| FieldLine light | compared-fail; inspected and accepted | 18,914 | `f0bff7753086b32dad21adcd08c700036c890687838530676a3ee4948744828b` |

A subsequent exact-head quick run must confirm these four comparisons pass.

## Linux re-pin provenance (superseded — 2026-09-22 MOR-2509 VFO deck batch)

| Field | Value |
| --- | --- |
| Source code commit | `e4474ba6b8486a2d4a7e97f84fd32ece18c8207d` |
| CI run / job | [Tests (quick) #35699527345](https://github.com/rigplane/rigplane-core/actions/runs/35699527345) / job `106654026064` |
| Command | `npm run test:e2e:i18n` (`playwright test -c ./playwright.i18n.config.ts`) |
| Source | the `actual` image attachments of the four MOR-1400 production-root cases in that run's `mor-1400-production-visual-diagnostics` artifact (`playwright-report/data/*.png`, 1280×800; the `.output/` directory is not uploaded because `actions/upload-artifact@v4` skips hidden paths), each matched to its scene as the attachment nearest to the previously committed PNG, copied byte-for-byte. |
| Reason | MOR-2509 rebuilds the Standard VFO deck (packages A–E on `codex/mor-2509-vfo-batch`): tray tabs, MODE/FIL chips with a three-way presence, frameless annunciators that wrap on compact panels, the v7 S-meter face, the bridge as hardware keys, the alive finish, bold (800) tabular digits, studioline light-mode ground. |

The source run reported 96 passed and 4 failed; the four failures are the
production-root comparisons below (pixel counts as Playwright reported them).

| Scene | Disposition | SHA-256 |
| --- | --- | --- |
| StudioLine dark | compared-fail (2,462 px); inspected and accepted | `21360c6fd797c556850849db2f35f85d7aaa1a5770baf95a009c776d36c80a02` |
| StudioLine light | compared-fail (52,832 px); inspected and accepted | `1423af0d14624c203bb175281280d622d036647e109aeacad7f38892e6bc96b7` |
| FieldLine dark | compared-fail (13,965 px); inspected and accepted | `b964712f344da345c9e2e6d57b28286b674179edf4a4c1bfac6a697c3bb882b0` |
| FieldLine light | compared-fail (28,563 px); inspected and accepted | `5874204a8f64ffec91a0c9adc0898e828d210150121cb1b34df2eb716749fbe3` |

A subsequent exact-head quick run must confirm these four comparisons pass.

## Linux re-pin provenance (superseded — 2026-09-21 MOR-2509 VFO panel skeleton)

| Field | Value |
| --- | --- |
| Source code commit | `40a01126239559421ff80e9388152ba76e022faa` |
| CI run / job | [Tests (quick) #35654800078](https://github.com/rigplane/rigplane-core/actions/runs/35654800078) / job `106516106686` |
| Command | `npm run test:e2e:i18n` (`playwright test -c ./playwright.i18n.config.ts`) |
| Source | `actual.png` attachments from that run's `mor-1400-production-visual-diagnostics` artifact, copied byte-for-byte. |
| Reason | MOR-2509 rebuilds the Standard VFO panel as three rows — identity, frequency beside the meter, indicator chips — and sizes the bridge controls to 24 px. |

The source run reported 91 passed and 4 failed; the four failures are the
production-root comparisons below.

| Scene | Disposition | SHA-256 |
| --- | --- | --- |
| StudioLine dark | compared-fail (2,439 px); inspected and accepted | `a600e3b6e1f38305994ca0bcac0db40ee782ad4b3a889291990869193d891d65` |
| StudioLine light | compared-fail (35,111 px); inspected and accepted | `be19cd5f21ba974e4e742bb1d361960ce2faba17dde32fca1ec1019fe80fb30e` |
| FieldLine dark | compared-fail (9,579 px); inspected and accepted | `392db9700990aa20985fc805ddd9bcd45ea98493fb471d439b6025b4c9d761d9` |
| FieldLine light | compared-fail (39,220 px); inspected and accepted | `2eb89eb987e4b007185eeeab9dfee7e83d0538a4608a11c7cb20ee04e7b3d03f` |

## Named expectations

| File | Workspace/theme case | SHA-256 |
| --- | --- | --- |
| `studioline--dark--production-root.png` | clean StudioLine × dark | `e4dcd807a47cd0753d0e31cd21dba180a08c5ac3387d37aedbbee384fc02a9d2` |
| `studioline--light--production-root.png` | persisted StudioLine × light | `f19ed24d9a2abf060e49aaef21640599456e8d67c122e35f2c97efe12c311a5e` |
| `fieldline--dark--production-root.png` | persisted FieldLine × dark | `d9696f148607f6ed9b20e4b497b7f9b2ed54de0761e820c97c736f27d3e35d8b` |
| `fieldline--light--production-root.png` | persisted FieldLine × light | `87139bd73a034f14e59d1dc47d21f326f62a4dbf49e3029daa67f1cd06be3ee5` |

All images are RGB PNGs at 1280×800. Changes to any expected image require a
new reviewed Linux re-pin with the same provenance record; macOS/local output
is not an acceptable replacement.
