# MOR-1400 production design-language baselines

These four images are the approved **production-root** pixel expectations for
the immutable built-dist i18n suite. They are not fixture-harness captures.
`playwright.i18n.config.ts` serves a copied `frontend/dist` through
`scripts/i18n-preview-server.mjs`, while the test stubs only the backend at the
page boundary and opens `/`.

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
contract` block runs `test.describe.configure({ mode: 'serial' })`, so
once `studioline--light` failed immediately after `studioline--dark`
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

## Linux re-pin provenance (current — 2026-08-13 MOR-1474 TX wording, round 2: persisted StudioLine × light)

| Field | Value |
| --- | --- |
| Date | 2026-08-13 |
| Reason | Round 1 above regenerated only `studioline--dark--production-root.png`; the suite's serial fail-fast then SKIPPED the other three cases without comparing them, so round 1 could not establish whether they were also stale. This round targets the very next case the suite would have compared: `studioline--light--production-root.png` renders the identical MOR-1474 RxTx wording line dark does, and CI run 31660813171 (the first run to actually reach and compare it) confirmed it was ALSO stale — 2470 px / 1% of frame, same single-line wording delta as dark's round 1, no additional component (no StatusBar/chip drift on this image; visually confirmed against the run's own diff attachment). |
| Candidate commit | `db34b81c` (the code/pixel state CI run 31660813171 tested; `2228af7c`, this repo's docs-only follow-up commit, ships byte-identical frontend output) |
| Runner host | self-hosted Linux ARM64 (`mm-build-core-1`) |
| CI run | [Tests (quick) #31660813171](https://github.com/rigplane/rigplane-core/actions/runs/31660813171) / job `94325055063` |
| Node | `v20.20.2` |
| Playwright | `1.58.2` |
| Chromium | Chrome for Testing `145.0.7632.6`, Playwright revision `1208` |
| Context | Chromium; 1280×800; DPR 1; `en-US`; UTC; browser media dark |
| Comparator | `threshold: 0.2`, `maxDiffPixelRatio: 0.001` |
| Verification | Diffed against the pre-round-1 baseline (same content `studioline--light` had carried since 2026-08-12): ONE changed region — the RxTx unknown-target wording line, 2470 px (1% of the 1280×800 frame). No StatusBar/chip-state delta on this image (unlike dark, which also carried the unrelated MOR-1526 chip flip) and no other region changed. `fieldline--dark`/`fieldline--light` remain unverified by this round — still SKIPPED, tracked above for round 3. |

Only `studioline--light--production-root.png` was regenerated by round
2. `fieldline--dark--production-root.png` and
`fieldline--light--production-root.png` remain unverified pending a run
that reaches them (both cases before them must pass first, per the same
serial fail-fast).

## Named expectations

| File | Workspace/theme case | SHA-256 |
| --- | --- | --- |
| `studioline--dark--production-root.png` | clean StudioLine × dark | `378e2fe599dd9fd1cd478af38128db1e944ceb37d17ee0e267c87ca332b9082d` |
| `studioline--light--production-root.png` | persisted StudioLine × light | `db68136932527caadfd8fb969f4e97d61ac27fd5c2dc14093c59ff2b465e7455` |
| `fieldline--dark--production-root.png` | persisted FieldLine × dark | `43fe4530d1e753aafaab10926bf500e00c1fe88eb64077733fd16e5c577f3cf5` |
| `fieldline--light--production-root.png` | persisted FieldLine × light | `c9cd377efc46e39f0ae17f1c0252c33932a0b78a9d805c65ea9fcdeb0e8b53a5` |

All images are RGB PNGs at 1280×800. Changes to any expected image require a
new reviewed Linux re-pin with the same provenance record; macOS/local output
is not an acceptable replacement.
