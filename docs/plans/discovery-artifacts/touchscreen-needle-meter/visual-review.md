# Selected-region visual review

## Capture

- Reference: `reference-crop.png`, 640 by 240 pixels, from the normalized `main-multiscale-needle-meter` bounds in `face-map.json`.
- Current: `current-render.png`, 640 by 240 pixels, Chrome with `reducedMotion: reduce`.
- Aligned review: `same-crop-comparison.png`, reference / current / 50% overlay.
- Capture command: `node fixtures/design-a-face-v2/capture.mjs --out ../docs/plans/discovery-artifacts/touchscreen-needle-meter/current-render.png`.
- Comparison command: bundled Python runtime plus `visualize.py --map ... --render ... --comparison ...`.

## Correction history

The original overlay put the rendered needle pivot about 40 pixels to the right of the reference pivot, roughly 6.25% of crop width. The first bounded correction moved only the pivot and fixture sample. Owner review then blocked the crop on arc curvature, tick placement, label centers, lower-scale spacing, and pointer ink.

The owner-authorized follow-up used two bounded passes:

1. Replaced ellipse-derived ticks with normals calculated from the three rendered quadratic scale curves; raised the white/red outer arcs to the reference apex; increased tick density to 46 / 36 / 29; moved the literal labels to the overlay landmarks; and shortened/repositioned the blue and red lower arcs.
2. Added the missing lower white SWR baseline, shortened the outer arc's right endpoint, moved `Id`/`Vd` to the reference baseline, and changed the pointer from a wide blue stroke to a 2.4-pixel white stroke with a subtle 3.2-pixel cool edge.

The next owner review found four remaining presentation defects: the two lower red arcs crowded their labels, several labels touched a scale, curved-scale numerals used visibly uneven offsets, and the vector ink was too clean for the photographed display. The final bounded correction:

3. Derives every numeral position from the same quadratic curve used to draw its scale and applies one normal offset per scale; moves the lower legends below the red arcs; separates and raises those two red segments; and replaces the sharp-plus-halo filter with a restrained soft core and low-opacity bloom.

The owner then clarified that the lower red arcs were meant to disappear, not move, and rejected the one-formula label pass because it repaired one portion while displacing another. The correction removes both lower red paths, raises the standalone `S` clear of the outer scale, and retunes only the outer and power label parameters so their established horizontal landmarks are preserved while their normal offsets remain consistent.

No semantic state, public prop, neighbouring region, repeated instrument, or composition changed in any pass.

## Final comparison findings

| Review item | Finding |
| --- | --- |
| Selected-region bounds | Exact same 640 by 240 crop and viewport; passes the 1.5% bounds threshold mechanically. |
| Pointer pivot | Final centers overlap within about 2 pixels by direct overlay inspection; inside the 2% landmark threshold. |
| Pointer direction/tip | Direction and tip now overlap closely. The pointer is white-dominant with only a narrow cool edge; its remaining difference is reference blur and subpixel rasterization. |
| Primary outer arc | Apex and left endpoint now overlap closely. The current right endpoint is about 6 pixels beyond the long red reference stroke; this is under 1% of crop width. |
| Major/minor ticks | Density and radial placement now follow each curve rather than a separate ellipse. Minor length and reference blur still differ locally. |
| Label centers and offsets | Curved-scale numerals keep a constant normal offset within each scale, while the outer and power labels are retuned to their established horizontal landmarks. No label touches its scale in the final render. |
| White scale baselines | Four curved white baselines are now present, including the lower SWR boundary missing from the blocked render. Their primary intersections align materially in the overlay. |
| Scale-start `S` | Raised 13 pixels so its baseline is clear of the outer white arc instead of sitting on the scale start. |
| Lower scales | The blue path remains. Both lower red paths are absent as explicitly directed by the owner; `ALC`, `Id`, and `Vd` remain informational legends. This is an intentional divergence from the photographed source. |
| Ink levels | White/red/blue hierarchy now matches, and the pointer is no longer a thick blue stroke. A restrained sub-pixel soft core plus low-opacity bloom reproduces the old-display fuzz without making the scale illegible. |
| Neighbouring TX cell | The reference crop contains a partial neighbouring TX cell. The selected component correctly does not reproduce it. |

The prior render was owner-blocked. This correction implements the clarified lower-arc requirement and bounds the numeral correction to the two affected scale groups. Semantic and component tests still do not grant owner acceptance: the owner must review this exact comparison before any repeated instrument or full-face composition may begin.
