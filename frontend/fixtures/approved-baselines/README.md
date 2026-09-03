# Approved pixel-diff baselines (MOR-1090)

Tracked, reviewed screenshots compared by `npm run test:e2e:visual` (`playwright.visual.config.ts`, whose `testDir` runs every `*.spec.ts` under `tests/e2e/visual/`): `visual-baselines.spec.ts` for a representative slice of the MOR-1070/1085 fixture matrix, and `gallery-baselines.spec.ts` (MOR-2219) for the `gallery-*` rows below, which preserve individual instrument looks rendered on the `?demo=control-buttons` page. Distinct from `frontend/fixtures-baselines/` (gitignored capture OUTPUT from `capture.mjs`/`capture-ptt.mjs`) — this directory is the APPROVED, committed comparison target.

## Why a slice, not the full 60+13 matrix

`capture.mjs`/`capture-ptt.mjs` already run the deterministic manifest/assertion layer on every invocation — that is the correctness floor and needs no pixels to be meaningful. Pixel-diffing is a second, purely visual floor on top, so it only needs the combinations a token/CSS regression could plausibly hit differently: topology shape, RX/TX/fault state, design language, and light/dark mode. The first 14 captures below (the `dual-*`/`topology-*`/`tx-phase-*`/`ptt-*` rows) span all four plus the MOR-1088 mobile PTT pair; the rest of that matrix is viewport/media/focus permutations of the same render paths. The 9 `gallery-*` rows are a separate axis — MOR-2219's per-component "looks gallery" for the Button family (PR A) and the ValueControl renderer / ProfessionalKnob families (PR B), pinning each instrument's visual identity on the demo gallery page rather than a cockpit-state permutation. The 2 `peer-split-chassis` rows (MOR-2243) are a third axis again — the segmentline chassis, which is neither a cockpit-state permutation nor a gallery instrument — captured at two frame sizes because its stage scale is the identity at 1280×800 and drops below 1 only at a frame narrower than its 1280px canvas or shorter than its 540px one.

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
| `gallery-dotbutton` | MOR-2219 looks gallery — preserves DotButton's look, carried by `lib/Button/ControlButton.svelte` + `components-v2/controls/control-button.css` (DotButton itself has no `<style>`/SVG) |
| `gallery-fillbutton` | MOR-2219 looks gallery — same carriers, FillButton's `indicatorStyle="fill"` variant |
| `gallery-hardwarebutton` | MOR-2219 looks gallery — same carriers, HardwareButton's hardware-surface variant |
| `gallery-hardwareplainbutton` | MOR-2219 looks gallery — same carriers, HardwarePlainButton's plain+warm-glow variant |
| `gallery-statusindicator` | MOR-2219 looks gallery — preserves StatusIndicator's look, carried by the `.v2-status-indicator` rule set in `control-button.css` |
| `gallery-valuecontrol-hbar` | MOR-2219 looks gallery — preserves the HBar renderer's look; carried by `components-v2/controls/value-control/HBarRenderer.svelte` (owns its own track/thumb div geometry — no SVG) |
| `gallery-valuecontrol-bipolar` | MOR-2219 looks gallery — preserves the Bipolar renderer's look; carried by `BipolarRenderer.svelte` |
| `gallery-valuecontrol-knob` | MOR-2219 looks gallery — preserves the Knob renderer's look; carried by `KnobRenderer.svelte` (builds its own `<svg>` arc geometry via `value-control-core.ts` helpers) |
| `gallery-professionalknob` | MOR-2219 looks gallery — preserves the ProfessionalKnob skin's look; carried by `value-control/skins/ProfessionalKnob.svelte` itself — its arc/indicator/tick geometry comes from the same shared `value-control-core.ts` helpers (`calculateArcPath`, `calculateIndicatorPosition`, `generateTickPositions`) that `KnobRenderer.svelte` uses, but its SVG markup (radial gradients, glow filter, element structure) and `.pro-*` styles are local to this file, not shared with any other renderer or skin |
| `peer-split-chassis--desktop` | MOR-2243 — the segmentline peer-split glass chassis (`PeerSplitLayout.svelte`, the only production file that mounts `ScaledStage`); at this frame size its stage scale resolves to exactly 1, the identity transform |
| `peer-split-chassis--1100x800` | MOR-2243 — the same chassis at the only frame size in this set where `computeStageScale` resolves below 1 (1100/1280 ≈ 0.859), so the stage transform is exercised rather than skipped |

The `dual-main-sub--desktop--studioline--light` and `tx-phase-fault--desktop--fieldline` captures came from `verify-mor-1090.md`'s representative-set adequacy ranking (§4): of the gaps it found against the MOR-1085 axis matrix, these were the two ranked highest by "what a token/CSS regression could plausibly hit differently" that a 2-capture budget could close. Media variants (reduced-motion / forced-colors / contrast-more) stay out on purpose — animations are already disabled for every screenshot, and forced-colors is an OS-level render mode the assertion layer already pins.

## Comparator, threshold, calibration

`toHaveScreenshot()` uses Playwright's own bundled pixelmatch comparator — already a transitive dependency of `@playwright/test`, no new npm package. Two knobs in `playwright.visual.config.ts`: `threshold: 0.2` (pixelmatch's default per-pixel YIQ distance) and `maxDiffPixelRatio: 0.001` (0.1% of the frame allowed to differ). MOR-1087/1088/1282 found raw PNG bytes are not a valid identity check on this host — repeat runs of an unchanged tree differ by antialiasing noise on text/icon edges.

**Calibration data.** At `threshold: 0.2` with zero tolerance, three same-tree reruns measured a noise ceiling of 3-123 differing pixels per capture (≤0.012% of a 1280×800 frame, less on the smaller phone viewport) — never zero, but always small. `maxDiffPixelRatio: 0.001` (0.1%) sits ~8x above that ceiling. Three consecutive full runs at the shipped threshold came back green (0 failing captures each, `manifest.json` regenerated identically bar `generatedAt`). A deliberate sabotage — a 60×60px (0.35%-of-frame) opaque overlay injected into one capture — came back red (3185 differing pixels) with an `-actual`/`-expected`/`-diff` artifact trio in `test-results/`, then was reverted.

**Sensitivity floor (required reading before trusting a green run).** `maxDiffPixelRatio: 0.001` over a 1280×800 desktop capture (1,024,000 px) is a hard floor of **~1024 differing pixels, roughly 32×32 px**, before a change turns the comparator red — and roughly **304 px, ~17×17**, on the smaller 375×812 phone capture, a 3.4x tighter absolute floor nobody had stated before this. MOR-2243 adds a third frame size: **880 px, ~29×29**, over the 1100×800 `peer-split-chassis--1100x800` capture (880,000 px). Measured directly (`verify-mor-1090.md` §3, a 9-case injected-regression probe against the real committed baselines):

- A 32×32px overlay (1027 px) is caught; 24×24px (under budget) and a 16×16px indicator dot (under budget) are not.
- **A whole-frame low-amplitude colour shift is NOT caught, and this is the more surprising miss.** A 3% brightness lift across the entire frame — a plausible design-token regression — produced fewer than 1024 differing pixels, because `threshold: 0.2` (pixelmatch's per-pixel YIQ distance) filters the delta away *before* the `maxDiffPixelRatio` ratio budget is ever consulted. **This comparator cannot police whole-frame contrast/brightness drift at all**, regardless of how the ratio knob is tuned.
- Small localized changes below the pixel floor (status LEDs, badges, single-glyph text swaps) are likewise invisible to this layer.

**None of this is evidence tooling has a gap** — it is a coarse net with a stated mesh size. `capture.mjs`'s own assertion layer (`tx-readout`, `fault-affordance`, and the rest) already pins exactly these classes of change on every capture, every invocation, independent of pixels. **Do not treat a green pixel job as evidence that nothing visual changed** — treat it as evidence that nothing changed ABOVE ~1024 px / ~32×32 (desktop), ~304 px / ~17×17 (phone), or ~880 px / ~29×29 (the 1100×800 peer-split capture), with the assertion layer covering the finer mesh below that for the first 14 captures, including all contrast/brightness drift this layer structurally cannot see. The `gallery-*` rows have no such assertion layer — `?demo=control-buttons` is never loaded by `capture.mjs` or `capture-ptt.mjs` — so for those rows the pixel floor is the only floor. The two `peer-split-chassis` rows are in neither bucket: `capture.mjs` does capture that fixture, but it declares no `expect`, so its recorded assertion cell holds only a hardcoded pass plus the harness-appended `meter-ballistics-honor-reduced-motion` — nothing that pins the composition. For those rows too, the pixel floor is the only floor on composition.

## Platform note

MOR-1396 re-pinned this set on the Linux ARM64 Core runner. The provisioning run was `31180561550` at branch head `ad37689d`; its `visual-diff-report` artifact contains real Linux `*-actual.png` outputs for the 12 captures whose macOS baseline exceeded the comparator tolerance. The remaining two captures, `ptt-idle--mobile` and `ptt-held--mobile`, passed the same Linux comparison and therefore emitted no failure-only actual file. `manifest.json` records the exact Linux regeneration environment. `.github/workflows/visual.yml` is now blocking.

**Regenerating from Linux (required for a future platform re-pin — do NOT do this on macOS):** the workflow produces Linux renders as a side effect of a failing run — `steps.visual.outcome == 'failure'` uploads `frontend/test-results/`, and every failing capture's `*-actual.png` inside it IS the Linux render (Playwright's own output naming for the actual-vs-expected-vs-diff trio). To re-pin from Linux:

```bash
# 1. Trigger visual.yml on a PR (or push [full-ci]-style if a push trigger
#    exists at that point) so it runs on the [self-hosted, linux, build]
#    runner and — expectedly — fails against the macOS-captured baselines.
# 2. Download the `visual-diff-report` artifact from that run.
gh run download <run-id> -n visual-diff-report -D /tmp/visual-diff-report

# 3. Copy each *-actual.png over its corresponding approved baseline —
#    NOT the *-expected.png (that is just a copy of the old baseline) and
#    NOT the *-diff.png (that is the highlighted difference image).
for actual in /tmp/visual-diff-report/**/*-actual.png; do
  name=$(basename "$actual" | sed 's/-actual\.png$/.png/')
  /bin/cp -f "$actual" "frontend/fixtures/approved-baselines/$name"
done

# 4. Re-run the comparator on Linux (or trust the just-copied images) to
#    confirm 0 diffs, open each changed PNG to eyeball it, then commit.
git status frontend/fixtures/approved-baselines/
git add fixtures/approved-baselines/
```

Do not regenerate this set on macOS for the pre-blocking re-pin — that reproduces the exact platform mismatch the Platform note warns about, just with a fresher timestamp.

**Batching (do not regenerate per rework slice).** S6a/S7/S8/MOR-1355 and future S-slices each change what the cockpit renders on `desktop-v2`. If every slice regenerated this set, reviewers would rubber-stamp the image diff within two rounds and each pass burns ~700KB of unreclaimable binary into git history (PNGs do not delta-compress). Regenerate only for an intentional, reviewed visual change, using Linux output so the blocking gate keeps one platform provenance.

## Updating baselines (intentional change)

```bash
cd frontend
npm run test:e2e:visual -- --update-snapshots=all
git status fixtures/approved-baselines/
# Open each changed PNG — confirm the diff is the change you intended.
git add frontend/fixtures/approved-baselines/
```

`manifest.json` regenerates every run (`tests/e2e/visual/global-teardown.ts`), recording the commit, platform, and tool versions behind the committed set — commit it with the PNGs. Its `commit`/`commitShort` fields are read at run time, so when the regeneration is part of the commit being made, they necessarily record that commit's PARENT (the manifest can't know its own future commit hash) — this is documented, honest behaviour, not a bug. A PR that intentionally changes cockpit/PTT visuals MUST include regenerated baselines + `manifest.json`, reviewed as an image diff, not waved through — and when the change is a **regeneration** (as opposed to a from-scratch capture), the PR description must state which captures are expected to change and why, so the reviewer is confirming a named expectation rather than rubber-stamping N changed PNGs.
