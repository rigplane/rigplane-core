# MOR-1400 production design-language baselines

These four images are the approved **production-root** pixel expectations for
the immutable built-dist i18n suite. They are not fixture-harness captures.
`playwright.i18n.config.ts` serves a copied `frontend/dist` through
`scripts/i18n-preview-server.mjs`, while the test stubs only the backend at the
page boundary and opens `/`.

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

## Linux re-pin provenance (current — 2026-09-09 MOR-2432 Standard meter readouts)

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

## Named expectations

| File | Workspace/theme case | SHA-256 |
| --- | --- | --- |
| `studioline--dark--production-root.png` | clean StudioLine × dark | `54bbe78a2eac8ed90193fa332159e17084ed3448ac50de7d72108d376877dd48` |
| `studioline--light--production-root.png` | persisted StudioLine × light | `8bdb9204889cf8392673d7a248be96c64e0a793b0bc96794b760aeca34acbc3f` |
| `fieldline--dark--production-root.png` | persisted FieldLine × dark | `41024a2504b70a94fa51adc2135cd9c20905307e4530ee035e3a90dbe7aba459` |
| `fieldline--light--production-root.png` | persisted FieldLine × light | `dbad846332a8e3a3abe0ff299df1e1d056708eeccd704f7067b1bbe7e41375f2` |

All images are RGB PNGs at 1280×800. Changes to any expected image require a
new reviewed Linux re-pin with the same provenance record; macOS/local output
is not an acceptable replacement.
