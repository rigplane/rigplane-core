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

## Linux re-pin provenance (current — 2026-08-10 A09b honest cold-start StatusBar)

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

## Named expectations

| File | Workspace/theme case | SHA-256 |
| --- | --- | --- |
| `studioline--dark--production-root.png` | clean StudioLine × dark | `98f6aa6f4a9c541f6c601fc6ba88aa80373df82055923322e79bc3398827bdd6` |
| `studioline--light--production-root.png` | persisted StudioLine × light | `0a947f7d763d6cbd015ce4396af4b4ba0b684d93c863983b3085270dd25074fb` |
| `fieldline--dark--production-root.png` | persisted FieldLine × dark | `91d853f54bf625809cc4db9bdb9b70f995281f6031a7cfed43636fd5b1ad2da6` |
| `fieldline--light--production-root.png` | persisted FieldLine × light | `552d2b2446f855bdcfd7184aa804a84fccfc6fedff7f881b9ac39310d29746dc` |

All images are RGB PNGs at 1280×800. Changes to any expected image require a
new reviewed Linux re-pin with the same provenance record; macOS/local output
is not an acceptable replacement.
