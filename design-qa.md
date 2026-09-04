# Design QA — A / Peer Split LCD glass

- Source visual truth: `/Users/moroz/.codex/visualizations/2026/09/03/01a06845-1e4a-7593-9295-a1b9dced9114/lcd-handoff-render/A-clean-idle.png`
- Rendered implementation: `/Users/moroz/.codex/visualizations/2026/09/03/01a06845-1e4a-7593-9295-a1b9dced9114/lcd-implementation-capture/peer-split-current-1280x540.png`
- Combined comparison: `/Users/moroz/.codex/visualizations/2026/09/03/01a06845-1e4a-7593-9295-a1b9dced9114/peer-split-reference-current-final.png` (SHA-256 `928d36e107a6cccc8fd496702cfc5d322f3602fdc8be6d1741679592070a5200`)
- Viewport: 1280×800 browser viewport; the fixed-native glass is the top 1280×540 CSS-pixel region.
- Pixel normalization: source 1280×540; implementation cropped losslessly from the 1280×800 capture to 1280×540; `deviceScaleFactor: 1`.
- State: source is authored `A-clean-idle` with VFO A active and demonstration values. Implementation is the real `peer-split-chassis` fixture with VFO B active and only facts available from its semantic model. Geometry and presentation are compared directly; content/state differences are not treated as visual defects when the source values are demonstrative or unbacked.
- Browser evidence: the existing fixture build/capture harness completed `1/1` assertions with `0 invalid`; its manifest records no console errors.

## Findings

No actionable P0/P1/P2 visual mismatch remains in the bounded, truth-backed
first slice.

- [Expected constraint] The source contains FFT peaks, filter widths, clock,
  named memory channels, and temperature that are not backed by current live
  facts. The implementation keeps their geometry but renders a flat ghosted
  AF-FFT, omits an unknown filter envelope, and reserves structural footer
  slots. Adding source demo values would be a truth regression, not a fidelity
  fix.
- [Truth contract] A known-off SPLIT keeps its fixed cell but renders a ghosted
  dash, because switch state alone does not prove a numeric zero delta. NOTCH
  and ANF are mutually exclusive renderings of the existing `manual | auto |
  off` mode: manual lights NOTCH, auto lights ANF, off ghosts both, and unknown
  leaves both unknown. They use their distinct handoff glyphs and never become
  two independent booleans.
- [Expected state difference] The source activates VFO A while the fixture
  activates VFO B. The active accent, strong/ghosted column treatment, and
  fixed 50/50 geometry correctly mirror across the divider.
- [P3] The source's populated footer has more visible weight than the current
  structural-empty footer. Revisit only when a real memory/telemetry semantic
  model provides those fields; do not fill the gap decoratively.

## Required fidelity surfaces

- Fonts and typography: the implementation uses the self-hosted DSEG7 Classic
  hero and Share Tech Mono labels. Hero size is 78px; the trailing three digits
  are exactly 62%; tabular fixed cells prevent digit reflow. Optical weight and
  hierarchy match the source at 1:1 capture.
- Spacing and layout rhythm: fixed 1280×540 stage, strict 50/50 VFO deck,
  symmetric 22px column padding, stable tag/chip, frequency, offset, meter,
  scope, fact-rail, and footer rows. Unsupported flags reserve invisible slots
  instead of shifting their neighbors.
- Colors and visual tokens: amber `#C8A030` ground, ink derived solely from
  `rgba(26,16,0,alpha)`, handoff bezel edge/shadow, scanline overlay, and only
  the specified TX/tune accent family.
- Image and asset fidelity: no raster screenshot is embedded. The FFT/filter
  layers remain vector primitives, and status icons are direct Svelte ports of
  the selected handoff `lcd-icons.jsx` paths rather than approximations.
- Copy and content: radio labels come from semantic facts or structural labels.
  No handoff memory, temperature, clock, named band-edge, IPO inference,
  simultaneously active NOTCH/ANF pair, or synthetic spectrum value is
  presented as real. When audio FFT is unsupported the heading says only
  `BANDPASS`, while the truthful filter/PBT overlay remains visible.

## Full-view and focused-region evidence

The combined before/reference/current sheet is the full-view comparison.
Focused regions were also inspected at native resolution in the standalone
1280×540 implementation crop: top status/icons, both frequency/offset/meter
stacks, AF scope grids, lower fact rail, and footer. Separate crop artifacts
were unnecessary because all display text and one-pixel rules remain legible
in the native image.

## Comparison history

1. First comparison found P2 drift: missing scanline/shadow texture,
   topology labels displayed as MAIN/SUB instead of VFO A/B for `ab_shared`,
   and status density lacked stable LOCK/ANF positions. It also found that the
   neutral FFT needed an explicit component-owned safe-empty contract.
2. Fixes: ported the handoff scanline/shadow treatment; mapped `ab_shared`
   labels from known topology; reserved unsupported status slots; added
   `LcdAfFft` with optional normalized bins and its own private zero buffer;
   ported the handoff status icon paths.
3. Post-fix evidence: the final 1280×540 crop and the combined comparison path
   above. The capture harness passed and reported no console errors.

## Implementation checklist

- [x] Preserve fixed-native geometry and uniform external scaling.
- [x] Keep glass markup read-only and non-focusable.
- [x] Render active/inactive/unknown/unsupported without reflow or fabricated values; split-off uses `—`, never a synthetic `+0.000`.
- [x] Keep FFT safe-empty allocation inside the passive FFT primitive.
- [x] Use real handoff SVG paths, self-hosted fonts, and amber ink tokens.
- [x] Capture 1:1 browser evidence without writing canonical baselines.

## Follow-up polish

- Populate the currently empty AF/footer positions only from the v3
  receiver-scoped FFT, receiver-scoped passband, memory, and telemetry
  mechanisms documented in the primitive-field map.

final result: passed
