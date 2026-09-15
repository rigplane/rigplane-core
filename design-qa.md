# Design QA — Standard TX indication on PTT

- Source visual truth: `/var/folders/gt/c_czgx6x5bxc1sb3ntph9mgr0000gn/T/codex-clipboard-7b90d4d1-43b9-4041-9b1c-42c5e4bd22e1.png`, plus the operator direction that the top `TX / KEY DOWN / latched` banner is removed and keyed state is carried by a strongly red PTT button.
- Rendered implementation: `/Users/moroz/.codex/visualizations/2026/09/14/01a0a12d-1c41-7c63-adc5-cb5ba44cb581/tx-banner-fix/standard-tx-after.png` (SHA-256 `ea2d954df2741ea24387fc8f155b5050936be1ca9fa13578a01d399d6fb7bc02`).
- Focused before/after comparison: `/Users/moroz/.codex/visualizations/2026/09/14/01a0a12d-1c41-7c63-adc5-cb5ba44cb581/tx-banner-fix/tx-panel-before-after.png` (SHA-256 `415ead1cf075b28e967ff328ee411a38905032a751d3d51f7c1111f2bfa1a826`).
- Viewport: 1440×900 CSS px, Chromium, `deviceScaleFactor: 1`.
- Pixel normalization: source crop 466×786 px; implementation full view 1440×900 px; the TX panel was cropped to 235×310 CSS px and scaled 2× to 470×620 px for the focused comparison. Both panels are top-aligned; the unused lower area is padding, not page content.
- State: Standard desktop, dark theme, simulated authoritative managed-TX state (`intent=transmit`, observed PTT on). The browser harness intercepts API/WS state and forwards no radio command.
- Audited implementation revision: `ed60a1219d44eccb3badd4ff650c1cce6fcc07a3`.

## Findings

No actionable P0/P1/P2 visual mismatch remains in the requested TX-panel slice.

- The duplicate local `TX / KEY DOWN / latched` banner is absent.
- The existing PTT control is the dominant local TX indication: opaque, saturated red fill, bright red border/glow, white label, and no disabled-opacity washout.
- UNKEY remains immediately below PTT and visually distinct.
- The panel keeps its original width, control order, spacing, and surrounding layout.

## Required fidelity surfaces

- Fonts and typography: the existing Roboto Mono/hardware-control typography, PTT weight, uppercase copy, and letter spacing are unchanged. The PTT label remains centered and legible over the stronger fill.
- Spacing and layout rhythm: removing the banner closes its entire vertical slot; PTT moves directly under the panel header without altering button geometry or the two-column auxiliary-control grid.
- Colors and visual tokens: keyed PTT uses the existing red semantic family with a 76% red fill, explicit full opacity, bright border, and red glow. It is substantially louder than idle without animation or flashing.
- Image and asset fidelity: no image asset, icon, SVG, or placeholder is introduced; this is native control styling only.
- Copy and content: the redundant visible `TX / KEY DOWN / latched` text is gone. `PTT` and `UNKEY` remain unchanged. The TX status row remains in the accessibility tree with its authority-derived text and `role=status`.

## Full-view and focused-region evidence

The full 1440×900 browser capture confirms the Standard outer grid, panel placement, and absence of reflow outside TX. The focused side-by-side comparison is required because the source is a cropped high-density panel image; it makes the removed banner, reclaimed space, and strengthened PTT treatment directly legible.

## Browser and interaction evidence

- Mac Mini Playwright production-path check: `MOR-2458 keeps RX/TX geometry fixed and attributes TX only to the known split target` passed at the exact revision.
- The test asserts the status row is `sr-only` with a ≤1×1 px visual box, PTT has `data-active=true` and `aria-pressed=true`, computed opacity is `1`, and its computed background and shadow are non-empty.
- The same run asserts zero outgoing radio commands and zero browser console/page errors. No live radio or PTT action was performed.
- Component test: 92/92 passed; Svelte/TypeScript check reported 0 errors and 0 warnings; production build passed.

## Comparison history

1. The source showed a P1 hierarchy problem: two simultaneous local TX indicators, while the actionable PTT remained visually muted.
2. The implementation keeps the authority status as screen-reader-only in Standard, resets competing panel chrome on that hidden row, restores full opacity for active PTT, and strengthens its fill/border/glow.
3. Exact-state browser capture and the focused combined image show the banner removed and PTT carrying the local keyed state. No post-fix P0/P1/P2 issue was found.

## Implementation checklist

- [x] Remove the visible Standard keyed banner without deleting status semantics.
- [x] Make active PTT strongly red and immune to generic disabled opacity.
- [x] Preserve `aria-pressed`, `role=status`, and ungated UNKEY.
- [x] Verify the production Standard path with mocked TX state and no radio commands.
- [x] Check the same viewport for layout drift and browser errors.

## Follow-up polish

None for this bounded request.

final result: passed
