# Reconstruct a radio face from an image

Use this reference only when an image or screenshot is the visual source and
the user has asked to generate components. It separates visual reconstruction
from semantic wiring so a correct component contract cannot disguise a poor
drawing.

## Stage A — analysis only

Do not write component code in this stage.

1. Record the source file, SHA-256, pixel dimensions, and the panel crop that
   defines normalized coordinates.
2. Run `measure-reference.py selftest`, then measure the whole panel and useful
   crops. Preserve commands and tolerances; label eye-read bounds as estimates.
3. Identify regions, groups, repeated structures, literal labels, and visible
   landmarks. Keep ambiguous elements in the map with `status: unidentified`.
4. Classify the image:
   - `flat`: rectilinear cells, segmented readouts, simple envelopes, few
     graphic families, and no dense curved scale or natural texture;
   - `compound`: an analog dial, curved tick/label geometry, trace or
     waterfall, complex chrome, or more than two unfamiliar graphic families.
5. Choose one `selectedRegion`. For a compound face, choose the highest-risk
   distinctive instrument or one complete repeated cluster. Prefer an analog
   meter over surrounding chrome because it discriminates faithful geometry
   from a merely plausible radio-themed drawing.
6. Write `face-map.json` and run `validate-face-map.py validate <map>`.
7. Produce an annotated reference image with the normalized boxes and region
   IDs when local image tooling permits. The JSON remains authoritative.

Required map shape:

```json
{
  "version": 1,
  "complexity": "compound",
  "source": {
    "file": "/absolute/reference.png",
    "sha256": "64 lowercase hex characters",
    "width": 1738,
    "height": 984,
    "panelCrop": {"x": 0, "y": 0, "width": 1, "height": 1}
  },
  "selectedRegion": "main-s-meter",
  "regions": [
    {
      "id": "main-s-meter",
      "kind": "scalar-meter",
      "status": "identified",
      "confidence": "confirmed",
      "label": "S / Po / SWR / COMP",
      "group": "main-receiver-cluster",
      "bounds": {"x": 0.13, "y": 0.08, "width": 0.36, "height": 0.24},
      "renderer": "svg",
      "evidence": ["curved scale and radial needle visible in source"]
    }
  ]
}
```

Bounds are shares of `panelCrop`, not source pixels. `repeatedFrom` may name
another region with identical geometry; never measure or generate the second
copy independently when the picture shows a repeated cluster.

## Choose the drawing technology from geometry

- Use SVG for analog scales, arcs, radial ticks, labels positioned around an
  arc, needles, masks, and other geometry that must remain crisp while scaling.
- Use Canvas for changing sample buffers such as FFT traces and waterfall
  pixels. Canvas owns pixels, not surrounding labels, filters, or controls.
- Use HTML/CSS for layout, numeric text, rectangular status cells, bezels, and
  actual touch controls.
- Reuse an existing renderer when it already owns the same semantic domain and
  can reach the required geometry honestly.

Do not approximate a compound analog instrument with a few borders, rotated
divs, or a generic semicircle. Calculate its landmarks from the measured crop.
Do not rasterize static labels into a background image merely to make the
comparison pass; the result must remain a real component.

## Stage B — generate one selected region

1. Crop the selected region from the reference using its recorded bounds.
2. Find the nearest repository primitive and semantic/calibration contract.
3. Implement only the selected region in an isolated fixture. Fixture data may
   position a needle or populate a trace, but must stay outside production code.
4. Render at the exact crop aspect ratio and at a stated viewport. Disable
   animation or use the repository's reduced-motion path for the capture.
5. Produce one comparison sheet containing:
   - the reference crop;
   - the current render;
   - a 50% reference/current overlay or clearly aligned landmark view.
6. Inspect the sheet. Record deviations in region bounds, primary arc/baseline,
   major tick positions, label centers, hierarchy, and ink levels. Text
   anti-aliasing may differ; geometry may not drift silently.
7. Iterate on the selected region only. Do not improve neighbouring elements
   while scoring this crop.

Use these default review tolerances unless the owner supplies stricter ones:

- selected-region bounds: within 1.5% of panel width and height;
- primary landmarks inside the crop: within 2% of crop width/height;
- major proportional relationships: within 3%;
- text: same hierarchy and centers, with rasterisation differences explicitly
  excluded from geometric acceptance.

These are review thresholds, not an optimisation target. A human owner still
accepts the visual form.

## Advance to composition

Only after the selected region has a reviewed comparison may the next
instrument class be generated. A repeated receiver cluster is built once and
mounted twice; the second instance changes data, not geometry. Full-face
composition begins only after every unfamiliar graphic family has one accepted
representative.

Keep these gates separate:

- semantic state tests prove truthfulness;
- component tests prove contracts and lifecycle;
- same-crop comparison proves visual reconstruction;
- owner review decides whether the face is good enough to continue.

A green test suite cannot upgrade a visibly weak reconstruction.
