# Approved pixel-diff baselines (MOR-1090)

Tracked, reviewed screenshots for a representative slice of the MOR-1070/1085 fixture matrix, compared by `npm run test:e2e:visual` (`tests/e2e/visual/visual-baselines.spec.ts` + `playwright.visual.config.ts`). Distinct from `frontend/fixtures-baselines/` (gitignored capture OUTPUT from `capture.mjs`/`capture-ptt.mjs`) — this directory is the APPROVED, committed comparison target.

## Why a slice, not the full 60+13 matrix

`capture.mjs`/`capture-ptt.mjs` already run the deterministic manifest/assertion layer on every invocation — that is the correctness floor and needs no pixels to be meaningful. Pixel-diffing is a second, purely visual floor on top, so it only needs the combinations a token/CSS regression could plausibly hit differently: topology shape, RX/TX/fault state, design language, and light/dark mode. The 14 captures below span all four plus the MOR-1088 mobile PTT pair; the rest of the matrix is viewport/media/focus permutations of the same render paths.

| Capture | Spans |
|---|---|
| `dual-main-sub--desktop` | reference dual topology |
| `topology-1-single--desktop` | topology shape |
| `topology-2-ab-shared--desktop` | topology shape |
| `tx-phase-rx--desktop` | RX state |
| `tx-phase-tx--desktop` | TX state |
| `tx-phase-fault--desktop` | fault state |
| `dual-main-sub--desktop--studioline` | design language |
| `dual-main-sub--desktop--fieldline` | design language |
| `tx-phase-tx--desktop--studioline` | TX state × language |
| `dual-main-sub--phone-portrait` | viewport |
| `dual-main-sub--desktop--studioline--light` | light mode (a separate token resolution from dark — MOR-1073/1074; no other capture here exercises it) |
| `tx-phase-fault--desktop--fieldline` | fault state × language (fault previously only appeared in the default language) |
| `ptt-idle--mobile` | mobile PTT, idle |
| `ptt-held--mobile` | mobile PTT, held (real pointerdown) |

The last two additions came from `verify-mor-1090.md`'s representative-set adequacy ranking (§4): of the gaps it found against the MOR-1085 axis matrix, these were the two ranked highest by "what a token/CSS regression could plausibly hit differently" that a 2-capture budget could close. Media variants (reduced-motion / forced-colors / contrast-more) stay out on purpose — animations are already disabled for every screenshot, and forced-colors is an OS-level render mode the assertion layer already pins.

## Comparator, threshold, calibration

`toHaveScreenshot()` uses Playwright's own bundled pixelmatch comparator — already a transitive dependency of `@playwright/test`, no new npm package. Two knobs in `playwright.visual.config.ts`: `threshold: 0.2` (pixelmatch's default per-pixel YIQ distance) and `maxDiffPixelRatio: 0.001` (0.1% of the frame allowed to differ). MOR-1087/1088/1282 found raw PNG bytes are not a valid identity check on this host — repeat runs of an unchanged tree differ by antialiasing noise on text/icon edges.

**Calibration data.** At `threshold: 0.2` with zero tolerance, three same-tree reruns measured a noise ceiling of 3-123 differing pixels per capture (≤0.012% of a 1280×800 frame, less on the smaller phone viewport) — never zero, but always small. `maxDiffPixelRatio: 0.001` (0.1%) sits ~8x above that ceiling. Three consecutive full runs at the shipped threshold came back green (0 failing captures each, `manifest.json` regenerated identically bar `generatedAt`). A deliberate sabotage — a 60×60px (0.35%-of-frame) opaque overlay injected into one capture — came back red (3185 differing pixels) with an `-actual`/`-expected`/`-diff` artifact trio in `test-results/`, then was reverted.

## Platform note

These baselines were captured on macOS. `.github/workflows/visual.yml` runs on a Linux self-hosted runner and is `continue-on-error: true` for exactly this reason — font/antialiasing differences could redden a non-regression on first landing. Per MOR-1090's acceptance criteria, regenerate this set from a Linux CI run before the owner flips the job to blocking (see the workflow file's header comment).

## Updating baselines (intentional change)

```bash
cd frontend
npm run test:e2e:visual -- --update-snapshots
git status fixtures/approved-baselines/
# Open each changed PNG — confirm the diff is the change you intended.
git add frontend/fixtures/approved-baselines/
```

`manifest.json` regenerates every run (`tests/e2e/visual/global-teardown.ts`), recording the commit, platform, and tool versions behind the committed set — commit it with the PNGs. A PR that intentionally changes cockpit/PTT visuals MUST include regenerated baselines + `manifest.json`, reviewed as an image diff, not waved through.
