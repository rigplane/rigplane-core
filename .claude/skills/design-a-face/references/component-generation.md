# Generate radio instrument components

Read this reference only when the user has asked to implement a selected radio
face, instrument, or radio control. It extends the analysis phases in
`../SKILL.md`; it does not replace them. The field map, measured geometry,
backed/unbacked/unshown lists, and owner decisions are inputs to this workflow.

## Build boundary

Generate radio-specific components, not a screenshot-shaped demo.

This skill is deliberately narrow: it generates radio display instruments,
radio controls, and compositions of those instruments. It does not generate
general application shells, navigation, forms, settings, CRUD, or arbitrary
frontend components. When a reference contains those things, exclude them from
the generated slice and report them as out of scope.

- A display component is passive. It receives semantic facts or an explicitly
  App-owned pixel resource and emits no command intent, gesture, tuning action,
  or hidden resource demand.
- A control component has two explicit modes: fixture-only mockup or production
  wiring. Never let mock interaction become an implicit radio command path.
- Production paths contain no sample frequencies, decorative peaks, convenient
  zeros, guessed labels, or inferred capabilities. Realistic values belong only
  in named fixtures and tests.
- `semantic/` may project the canonical `RadioViewModel`; skins and presentation
  components must not reach around it into transports, stores, or runtime
  command internals.
- A component implements the states its actual input type can distinguish. It
  must not manufacture a richer freshness or error vocabulary than the source
  carries.
- Keep readout and action ownership separate even when the reference places them
  together. If the user selected only the glass or only the controls, build only
  that slice and list the other part as separate scope.

## Classify the instrument before choosing code

Name the instrument's semantic kind. This determines both the reusable contract
and the correct state grammar.

| Kind | Typical examples | Input boundary |
| --- | --- | --- |
| Discrete state | ATT, PRE, AGC, NOTCH/ANF, ATU/TUNE | one bool, enum, or ordinal field plus availability |
| Scalar meter | S, Po, SWR, ALC, Vd, Id | calibrated domain or preformatted text plus normalized geometry |
| Numeric readout | frequency, offset, bandwidth | known unit-bearing value plus availability |
| Trace | AF FFT, hardware RF spectrum | optional frame/pixels plus receiver and freshness identity |
| Envelope | filter, IF shift, PBT | bandwidth and shift facts independent of any trace availability |
| Radio control | tuning knob, RIT/XIT, filter selector, ATT/PRE switch | typed intent plus capability, admission, and observed feedback |
| Composite face | receiver glass, dual-VFO display | a narrow display model projected from the canonical view model |

Do not merge kinds because they share a rectangle. A filter envelope can remain
truthful when no FFT frame exists. An AF FFT and a hardware RF panadapter may
both contain pixels but are different instruments with different capability
gates and future renderers.

## Recognise a radio control in two passes

Do not infer function and mechanics as one guess. Identify them independently,
then cross them with the repository contract.

### Pass 1 — physical/visual control type

Use the reference's shape, detents, travel, pointer, legends, grouping, and
vendor conventions to classify the affordance. The common radio set includes:

| Control type | Interaction contract |
| --- | --- |
| Momentary button / softkey | one activation intent; no locally-latched truth |
| Latching/toggle button | requested bool/enum plus observed on/off feedback |
| Relative rotary encoder / tuning dial | signed step or delta intents; unbounded visual rotation |
| Absolute knob / potentiometer | bounded value with unit, range, step, and current readback |
| Press-and-turn encoder | separate press and rotation contracts; neither implies the other |
| Dual-concentric knob | two independent values/intents and two distinct hit targets |
| Slider/fader | bounded absolute value with orientation and readback |
| Rocker/up-down pair | repeated increment/decrement or next/previous intent |
| Two/three-position switch | closed enum with one observed selected position |
| Rotary selector | closed or profile-provided enum, not an arbitrary numeric knob |
| Keypad/key matrix | one discrete intent per key or a typed digit-entry protocol |

If visual evidence cannot distinguish two mechanics — for example an absolute
knob from a relative encoder, or a pressable encoder from a plain one — record
both candidates and ask. Do not choose the more convenient component.

### Pass 2 — radio function

Read printed labels and nearby grouping as hypotheses, then search the current
code. Normalise case, punctuation, common vendor abbreviations, and paired
labels (`RF/SQL`, `RIT/XIT`, `PBT/SHIFT`) only for search; preserve the actual
label in the rendered component.

Search for the label and likely aliases in:

- `RADIO_INTENT_NAMES` and typed intent payloads;
- existing semantic/action surfaces and control primitives;
- rig profiles, capability labels, enum choices, ranges, units, and steps;
- feedback/adoption and disabled-reason mappings;
- vendor-specific aliases already encoded in the repository.

Adjacent controls are supporting evidence, not proof. `MULTI`, `CLAR`, `M-CH`,
and multifunction softkeys are intentionally ambiguous across radios and modes.
Do not assign them a production function from label alone.

Write this decision table before generating a control:

| Reference label | Control type | Candidate function | Confidence | Intent/payload | Feedback | Capability/admission | Result |
| --- | --- | --- | --- | --- | --- | --- | --- |

Use only these confidence outcomes:

- **confirmed** — mechanics are visually established or owner-confirmed, and
  code provides one exact intent plus payload/unit, capability/admission, and
  feedback path. Production generation is allowed.
- **probable** — the label/vendor convention strongly suggests a function, but
  mechanics or one contract leg is not established. Generate fixture-only
  mockup and name the missing evidence.
- **ambiguous** — multiple mechanics/functions remain plausible. Keep it as an
  unidentified placeholder and ask the owner before choosing a component.

A visual label can select search terms; it cannot create an intent. A matching
intent can prove a function; it cannot prove that a pictured round object is a
pressable or dual-concentric encoder. Both passes must close independently.

### Geometry survives uncertainty

An unidentified control is still a measured layout element. Do not let one
ambiguous knob block a complete face or disappear from the composition. Assign
it a stable ID and record:

| Field | Required record |
| --- | --- |
| Bounds | `x`, `y`, `width`, `height` as shares of the measured panel box, plus source pixel dimensions |
| Shape | observed outline/aspect only; do not promote it to a mechanics claim |
| Orientation | horizontal, vertical, radial, or unknown |
| Label | literal transcription, including uncertainty; never a cleaned-up invented name |
| Group | enclosing block and relative position to named neighbours |
| Candidates | possible control types/functions with evidence and confidence |
| Status | `unidentified` and `unwired` until the relevant pass closes |

Keep that box in the layout manifest and continue generating independent,
confirmed instruments. A fixture may show a neutral, non-interactive placeholder
occupying the measured box; it must not look operational or dispatch anything.
Batch questions for unidentified elements at the end. Replacing a placeholder
with a confirmed control later must not require re-measuring or rearranging the
rest of the face.

## Reuse ladder

Search before creating a component. Inspect at least:

```text
frontend/src/semantic/
frontend/src/components-v2/
frontend/src/skins/
frontend/src/presentation/languages/
frontend/src/lib/renderers/
```

Then choose the first honest rung:

1. **Reuse unchanged.** Mount the existing semantic surface or primitive and
   supply tokens, attributes, or a display descriptor.
2. **Compose.** Wrap an existing component or combine several primitives while
   keeping their value domains and state ownership intact.
3. **Parameterise shared drawing.** Add a narrow prop or extract a pure utility
   only when the old and new instruments share the same semantic domain and the
   change preserves existing callers.
4. **Create a sibling renderer.** Use the existing contract and calibration but
   draw different geometry when the visual form genuinely differs — for example
   a needle meter beside a segmented meter.
5. **Fork deliberately.** Copy code only when shared parameterisation would
   couple unrelated skins or preserve the wrong ballistics/state grammar. Name
   the source and the reason the fork must diverge; do not leave an accidental
   near-duplicate.

Svelte components do not gain useful reuse through class inheritance. Treat
"use this as a parent" as composition, a shared typed contract, or extracted
pure geometry/calibration functions unless the repository already establishes a
different pattern. Preserve public compatibility and the component census or
registry that governs the chosen directory.

## Derive the component input

Write a small table before code:

| Visual element | Semantic source | Component prop | known | inactive/relevance (only if carried) | unknown/stale | unsupported |
| --- | --- | --- | --- | --- | --- | --- | --- |

Every production prop must trace to the Phase 1 contract or to an explicitly
named App-owned resource. Prefer a narrow immutable display model when a face
combines several facts. That model may derive presentation facts only when all
inputs needed to prove them are known. It must fail closed rather than selecting
a plausible receiver, VFO, mode, or zero.

Keep instrument-specific fallbacks inside the instrument that owns their
meaning. The accepted AF-FFT example is intentionally narrow: an optional or
unusable frame becomes the component's own zero-filled buffer and a ghosted flat
trace. Do not generalise that rule to meters or numeric readouts; zero there is a
real measurement and an absent reading must remain unknown.

Pixel streams may remain outside `RadioViewModel` when they are App-owned
resources. Pass them through a passive seam; do not create a second controller,
socket, or demand lifecycle inside a display component.

## Radio controls: mockup and production are different deliverables

Choose the mode from the user's request and name it in the output.

### Fixture-only mockup

Use this when the user asks for a layout, visual prototype, or at least a mockup
before command wiring exists.

- Local interaction may update fixture-local state so the owner can feel the
  control, but it must live only in fixture/story code.
- The production component remains passive or exposes a typed callback without
  binding it to a radio command.
- Mark the control `mockup`/`unwired` in the field-intent map and list the
  missing intent, feedback, capability, or authority seam.
- Do not imitate success, pending, rejection, or radio readback with timers in a
  production path.

### Production control

Wire a control only when all of these are established from code:

1. The typed intent and its payload/unit contract.
2. The capability or structural gate that decides whether the control exists.
3. The admission/disabled-reason path that decides whether it is operable now.
4. The canonical observed feedback that confirms, rejects, or supersedes the
   requested value.
5. Existing authority, queue, coalescing, and safety behavior for that intent.

Reuse the existing command/action surface and value-control primitive where one
exists. A generated control must not call transports, mutate raw stores, create
a second queue, or infer success from the click itself. Pending and rejected
states come from the repository's feedback contract, not from visual optimism.
If any item above is missing, stop at fixture-only mockup and report the exact
missing seam; do not silently downgrade a requested production control.

## Generate the smallest complete slice

For each new instrument or face, create only what the selected composition
needs:

1. A projection or props adapter if the existing semantic contract is not
   already component-shaped.
2. A passive display component, a fixture-only mock control, or a production
   control using the existing action surface; each keeps stable geometry across
   live, inactive, pending, rejected, and unknown states it can actually express.
3. A fixture registered in the repository's existing fixture mechanism. Fixture
   values must be visibly and structurally confined to fixture/test code.
4. Focused tests for the state matrix and the semantic boundary.
5. Source-level style/selector assertions where mount tests cannot detect a
   missing component `<style>` block or unreachable selector.
6. Registry/census updates required by the component family.
7. A same-viewport render and, when an image reference file exists, a
   reference/current comparison image. Owner-specified geometry without an image
   still requires a fixture render, but fidelity to an unseen reference remains
   unclaimed.

Do not create a general component factory, base-class hierarchy, schema
generator, or renderer registry for one caller. Revisit an abstraction after a
second concrete instrument exposes the same seam; generalise only when the
shared contract is evident.

## Meter-specific contract

Meters are particularly easy to make visually convincing and semantically
wrong.

- Preserve the repository's calibrated domains. For an S-meter, reuse the
  table-driven calibration and formatting already owned by
  `smeter-scale.ts`/`meter-utils`; never replace it with a fixed dB-per-S-unit
  formula. Current `smeter-scale.ts` imports the capabilities store, so a new
  renderer must not import it merely to obtain convenient conversions. Reuse an
  already-approved component seam or pass a pure calibration/tick/format
  descriptor from the layer that owns capability access. If no such seam exists,
  extract one explicitly rather than letting a new visual primitive read stores.
- A meter renderer either receives the calibrated S-meter domain directly or a
  preformatted value plus a domain-free normalized fraction. Register a new
  renderer under the correct domain in the meter census.
- Reuse the existing smoother, peak behavior, and reduced-motion contract when
  the instrument needs ballistics. Do not start an independent animation loop
  in a semantic surface.
- Keep relevance separate for each reading. A receiver-scoped S-meter is not a
  radio-wide TX meter, and two sibling scales must not multiply each other's dim
  opacity.
- Unknown is not zero. Unsupported usually removes the instrument or preserves
  an explicitly designed structural spacer; inactive may keep the scale ghosted
  without fabricating a pointer reading.
- Do not assume current unknown handling keeps the renderer mounted. If the
  semantic surface replaces an unknown meter with fallback text, stable dial
  geometry requires an explicit nullable/unknown prop and a widened mounted
  component contract. Prove that seam; do not pass zero to keep the needle alive.

### Needle S-meter example

A needle S-meter should normally be a sibling of `LinearSMeter`, not its copied
calibration and not a subclass. It can share the calibrated dB-relative-to-S9
input, `smeter-scale.ts` conversions, smoothing/reduced-motion behavior, display
formatting, and meter registry contract. Its own code owns only dial geometry,
tick placement, needle angle, and the visual treatments for inactive/unknown.
The needle component does not read the radio store. A sibling file is not by
itself selectable: if the current semantic surface hard-codes `LinearSMeter`, add
or name a minimal presentation-owned `linear | needle` selection/composition
seam. Do not branch on radio model and do not import a skin into `semantic/`.

## Verification gate

Before presenting the result:

- Run changed-file lint/type checks and the focused component/semantic tests.
- Run the repository's contract extractor and the checklist validation relevant
  to the face.
- Confirm the generated component does not directly import or read a raw store,
  transport, command bus, handler, or fixture value. Check transitive helpers and
  document any already-existing store-owning seam instead of falsely claiming a
  pure dependency graph. For a production control, confirm the only action path
  is the established typed intent/action surface; for a mockup, confirm
  interaction and sample values are fixture-local only.
- Confirm every enum is rendered as one mutually exclusive state rather than as
  independently true flags.
- Confirm derived numbers appear only when every required source fact is known;
  `inactive` must not be formatted as a numeric zero unless the domain explicitly
  defines that reading.
- When an exact image reference exists, capture reference and current at the same
  viewport and inspect them together. Compare hierarchy, proportions, ink
  levels, stable cells, and state truth; do not use a pixel-difference score as
  acceptance. Without an image file, inspect the fixture render against the
  owner-specified geometry and state contract and explicitly leave visual
  fidelity unclaimed.
- Use a fresh verifier for non-trivial implementation. The author runs tests;
  the verifier reviews the exact head and the combined visual independently.

Return the live fixture or large render first, then the exact component paths,
state coverage, reuse decision, and any still-unbacked elements. Do not freeze
visual baselines or claim hardware acceptance without the owner's separate gate.
