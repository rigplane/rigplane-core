# IcomTouchNeedleMeter 0.1

Status: owner accepted for preservation on 2026-09-04. Further visual polishing is deferred.

## Component

- Source: `frontend/src/components-v2/meters/IcomTouchNeedleMeter.svelte`
- Version export: `ICOM_TOUCH_NEEDLE_METER_VERSION = '0.1'`
- Value domain: profile-normalized `0..1`; caller supplies the formatted reading.
- States: known, supported-but-unknown, structurally unsupported, and known-but-irrelevant.
- Scales: S, Po, SWR, ALC, COMP, Id, and Vd.

## Accepted artifact

- Render: `current-render.png`, 640 by 240.
- Comparison: `same-crop-comparison.png`.
- Visual review: `visual-review.md`.
- Design QA: `design-qa.md` in this artifact directory.

The lower red arcs are intentionally absent in 0.1 by owner direction. The `ALC`, `Id`, and `Vd` informational labels remain. This checkpoint does not add radio commands, control authority, or upstream fallback data.
