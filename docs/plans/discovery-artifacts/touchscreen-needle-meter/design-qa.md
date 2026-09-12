# Design QA: touchscreen needle meter

## Evidence

- Source: `reference-crop.png`, 640 by 240.
- Current: `current-render.png`, 640 by 240, Chrome reduced-motion capture.
- Comparison: `same-crop-comparison.png`, source / current / 50% overlay at the same viewport.

## Findings

| Severity | Area | Result |
| --- | --- | --- |
| P2 | Lower red arcs | Fixed after clarification: both red paths are removed; the text legends remain. |
| P2 | Text/scale collisions | Fixed: SWR, COMP, ALC, Id, and Vd have clear baselines outside scale ink. |
| P2 | Numeral spacing | Fixed locally: outer and power numerals retain their horizontal landmarks and use a constant normal offset within each scale; the already-correct inner group is unchanged. |
| P2 | Scale-start `S` | Fixed: raised clear of the first white arc. |
| P2 | Display softness | Fixed: restrained soft core and bloom approximate photographed old-display fuzz while preserving legibility. |

No P0, P1, or unresolved P2 finding remains in the selected meter region. Full-face composition and owner visual acceptance are separate gates.

final result: passed
