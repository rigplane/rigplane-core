# MOR-1400 production design-language baselines

These four images are the approved **production-root** pixel expectations for
the immutable built-dist i18n suite. They are not fixture-harness captures.
`playwright.i18n.config.ts` serves a copied `frontend/dist` through
`scripts/i18n-preview-server.mjs`, while the test stubs only the backend at the
page boundary and opens `/`.

## Linux re-pin provenance

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

## Named expectations

| File | Workspace/theme case | SHA-256 |
| --- | --- | --- |
| `studioline--dark--production-root.png` | clean StudioLine × dark | `38da39964b62b96dddbbd016044aca980d52bf480f672b0c77c4161c044e1f92` |
| `studioline--light--production-root.png` | persisted StudioLine × light | `1e776e94b4cf0ee8561a9653452ba1f5b6797daa14e3be48205c0ff821e1fe58` |
| `fieldline--dark--production-root.png` | persisted FieldLine × dark | `7e12ecf5b39254fac309e37694656ccd78fb949ffbfcb5306a7de2b084f52d10` |
| `fieldline--light--production-root.png` | persisted FieldLine × light | `c7268db502631f93b77f58fc72c5ce47436590b6fb11fd5179ac81dbd9ed4260` |

All images are RGB PNGs at 1280×800. Changes to any expected image require a
new reviewed Linux re-pin with the same provenance record; macOS/local output
is not an acceptable replacement.
