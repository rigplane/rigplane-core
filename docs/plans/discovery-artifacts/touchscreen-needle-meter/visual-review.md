# Selected-region visual review

## Capture

- Reference: `reference-crop.png`, 640 by 240 pixels, from the normalized `main-multiscale-needle-meter` bounds in `face-map.json`.
- Current: `current-render.png`, 640 by 240 pixels, Chrome with `reducedMotion: reduce`.
- Aligned review: `same-crop-comparison.png`, reference / current / 50% overlay.
- Capture command: `node fixtures/design-a-face-v2/capture.mjs --out ../docs/plans/discovery-artifacts/touchscreen-needle-meter/current-render.png`.
- Comparison command: bundled Python runtime plus `visualize.py --map ... --render ... --comparison ...`.

## One bounded correction

The first overlay put the rendered needle pivot about 40 pixels to the right of the reference pivot, roughly 6.25% of crop width and outside the 2% primary-landmark threshold. One correction moved only the pointer pivot from x=320 to x=280 and adjusted the fixture sample from 0.34 to 0.38. The final overlay aligns the pivot and pointer direction closely; neighbouring elements and other graphic families were not changed.

## Final comparison findings

| Review item | Finding |
| --- | --- |
| Selected-region bounds | Exact same 640 by 240 crop and viewport; passes the 1.5% bounds threshold mechanically. |
| Pointer pivot | Final centers overlap within about 2 pixels by direct overlay inspection; inside the 2% landmark threshold. |
| Pointer direction/tip | Direction is close, but the rendered tip remains several pixels higher; owner review remains required. |
| Primary outer arc | The current arc has greater curvature and a higher apex than the reference. This is a visible miss, not accepted by tests. |
| Major ticks | The same radial family is present, but density and several positions differ beyond anti-aliasing. |
| Label centers | `S`, `1`, `5`, `9`, `+20`, `+40`, and `+60dB` preserve hierarchy and order; several centers remain displaced. |
| Lower scales | Po/SWR/COMP/ALC/Id/Vd families and colour hierarchy are present. Their exact baselines and tick counts remain approximate. |
| Ink levels | Current strokes are cleaner and brighter; the pointer is substantially thicker and bluer than the white reference pointer. |
| Neighbouring TX cell | The reference crop contains a partial neighbouring TX cell. The selected component correctly does not reproduce it. |

Semantic and component tests do not upgrade these deviations to visual acceptance. The result is a bounded first representative for owner review, not approval to compose the full face or repeat the instrument.
