---
name: design-a-face
description: >
  Propose a new operator interface — a skin, a design language, a layout — that
  is constrained by the data the radio actually reports rather than by a picture.
  Use when asked to design a new face, propose a UI, redesign a screen, adapt a
  reference design or a vendor's conventions, or judge whether a mockup is
  buildable. Produces a proposal in which every element names the field backing
  it and every field's full state set has a treatment. Read-only up to the
  proposal; it does not implement.
---

# Design a face

A design proposal fails here in one of two ways, and both are silent.

**It draws a control no data can fill.** An elegant meter for a value this radio
never reports. This is not caught by taste, review, or types — it is caught by
asking, per element, which field feeds it.

**It draws only the happy state.** Every value in this system is a pair —
a reading and an availability — and a reading that is not `known` carries one
of four reasons. A design that shows a number and a dash has collapsed five
states into two, and two of the collapsed ones are safety-relevant.

This skill exists to make both impossible to ship by accident.

## Phase 1 — derive the data contract

**Never write this by hand.** A hand-maintained contract at a layer boundary
rots silently, and the design that consumed it goes on being confidently wrong.
Derive it from the code every time, even if a previous run left a document.

Read, and report what you actually read:

- `frontend/src/presentation/layouts/contract.ts` — `SEMANTIC_SURFACE_NAMES`
  is the closed set of surfaces a layout may mount.
- `frontend/src/semantic/radio-view-model.ts` — the field shape for each
  surface, and the state vocabulary.
- The rig profile at `rigs/<radio>.toml` — `rigs/ftx1.toml`, `rigs/ic7300.toml`
  — plus the capabilities it declares, for what this particular radio reports.
  A field existing in the view model does not mean this radio has it.

The state vocabulary is the part designs get wrong, and there are **two
mechanisms, not one**. Do not generalise either.

**General, carried by every field.** A reading is `{status:'known', value}` or
`{status:'unknown'}`, and `Availability` carries `structural` and `operational`
alongside it. So the states a design must draw are: known; unknown while the
radio has the capability; unknown because it does not. *Has it but cannot read
it* and *does not have it* are opposite things to an operator, and the flags
are the only way to tell them apart.

**Not general.** One type — `TxTargetViewModel` — carries an explicit
`reason` union of `not-observed`, `stale`, `unsupported`, `contradiction`.
It is the only one. An earlier version of the extractor took the first
occurrence of that union and printed it as the model-wide vocabulary; the
resulting document told a designer to distinguish four states that thirteen of
fourteen surfaces cannot express. Run the extractor and read what it reports
per type; never carry a vocabulary across from one field to the rest.

If a design needs *never asked* distinguished from *answer aged out* on a field
that does not carry the reason union, that is a request for a state the model
does not have, and belongs on the unbacked list rather than in the drawing.

Output of this phase: per surface, the fields, their state sets, and whether
this radio backs them. Say which are structurally absent and which are merely
unobserved right now — they are different findings.

**Verify what the extractor emits before building on it.** It is a script over
source files, and it has been wrong twice — once reporting a named-reason union
that exists on one type as the model-wide vocabulary, once reporting a feature
that the profile disables in a comment. Both were caught by an agent that
checked the output against the files rather than trusting it. Spot-check the
claims you are about to design against: open the type, count the occurrences,
parse the profile yourself. A generated contract is not evidence; it is a
starting point that happens to be fast.

## Phase 2 — read the design reference for its reasoning, not its layout

**Settle first what kind of thing the reference is.** A display shows state; a
control surface is operated. The two have different ergonomics and the rules do
not transfer between them: indicators are placed by how they are **read** —
peripheral vision, deliberate search, once an hour — and controls by how they
are **reached**. Reading a display as if it were a panel produces confident
rules about adjacency and separation that describe nothing.

A radio's own screen is a display, because the knobs are physical. An
application with no knobs is both, and which elements carry the operating is a
question for the owner rather than an assumption.

Not pixels. A pixel comparison between a reference and a render is close to
100% different and carries no signal — different data, different resolution,
different rasterisation.

An inventory is not enough either. **A list of what sits where does not
transfer, because the element set differs**: the reference will show things no
field can fill, and omit fields that exist. Only the reasoning survives that
gap.

So extract, in this order:

1. **The parts.** What elements exist, how they group.
2. **The measurements.** Relative size, aspect ratio, proportion between
   groups, what is dimmed against what is bright. Measured off the image, not
   estimated in adjectives.
3. **Why each part is where it is.** What is read continuously, what is glanced
   at, what is set once a session and then ignored. What must be adjacent to
   what because they are operated together. What is separated because
   confusing them is expensive.

**Step 3 is inference and must be labelled as such, per claim.** The designer's
reasons are not in the image. An agent will produce a confident rationale for a
placement that was arbitrary, and a plausible-but-wrong rationale is worse than
none — it will be carried forward as a constraint.

Split the output in two:

- **Established** — supported by something outside the picture: a vendor's
  documented convention, an operating practice, a physical panel that works
  this way.
- **Inferred** — a reading of the image alone. Ranked by confidence, and short
  enough that the owner can strike half of it in a minute.

A vendor's conventions (Icom, Yaesu) are a legitimate reference of the same
kind and can be used with no image at all — and they land in **established**,
which is why they are worth more than a screenshot.

**State the reference's own limits.** It shows one moment, usually with invented
values, and cannot show what transmit, a fault, or a lost connection look like.
Anything it implies about those is inference of the weakest kind.

## Phase 3 — cross them

First, three lists:

1. **Backed.** Element, the field that feeds it, and a treatment for *every*
   member of that field's state set. An element whose unknown-states have no
   treatment is not finished.
2. **Unbacked.** Elements the reference shows for which no field exists. These
   are not design problems — they are requests for new data, and each needs its
   own ticket before it can be drawn. Never quietly drop them; a reader must be
   able to see what the reference wanted.
3. **Unshown.** Fields that exist, that the reference has no element for.
   Usually the larger list, and usually where the real work is.

## Phase 4 — place, by the reasoning rather than by the picture

Group the backed fields into functional blocks — things operated together,
read together, or dangerous to confuse. Then place the blocks by the rules
Phase 2 extracted.

**The unshown fields are placed by the same rules, not appended.** This is the
test of whether Phase 2 produced reasoning or an inventory. If a rule says the
continuously-read value dominates, it decides where an antenna selector goes
just as it decided where the frequency goes. If it cannot, it was a description
of the picture and should be marked as such and discarded.

**Do not reproduce the reference's proportions when the content differs.** A
measurement is evidence for a rule — "the readout takes roughly a third of the
width because it is read at a glance from across the room" — and it is the rule
that carries over, not the third.

Every block states: which fields it holds, which rule placed it, and whether
that rule was **established** or **inferred**. A block placed by an inferred
rule is a proposal; a block placed by an established one is closer to a
finding.

**Say who draws each element, because that is where the cost is.** A proposal
that only says what an element looks like hides its price. Every element falls
into one of three tiers, and the difference between them is one line in a
document and weeks in the tree:

- **The design language reaches it.** Its markup comes from a semantic surface
  and its appearance is CSS over that markup. This is the cheap tier and the
  only one a stylesheet change can deliver.
- **Only the theme reaches it.** A shared component draws it and reads the
  `--v2-*` theme vocabulary rather than the design language's `--dl-*`. A change
  here lands in **every skin**, not just this one, so it is a different decision
  with different reviewers.
- **Nothing reaches it.** The form is code — SVG geometry, a segment count, a
  decay constant. Changing the look means changing a shared component that every
  skin renders.

The worked example: `components-v2/meters/LinearSMeter.svelte` reads fifteen
`--v2-*` variables, so its colours are theme-restylable — but `SEG_COUNT = 20`
and its bar geometry are computed in code, and its peak decay is a constant. A
design language declaring a meter track width and segment gap cannot move any of
it. That is why segmentline's meter tokens could not have worked even with a
functioning value channel: there was nothing on the other end reading them.

Check the tier per element **before** proposing its appearance. An element in
tier three whose proposed look differs from what the component draws is not a
styling task and must not be listed as one.

## Phase 5 — the state treatments, which are the hard half

**A reference can draw states the radio cannot occupy, and this is an element
grammar problem rather than a placement one.** Watch for independent indicators
drawn over what is one multi-valued field. A row of separate flags where the
contract carries a single three-valued field can express two of them lit at
once, which is unrepresentable — and no amount of rearranging fixes it. The fix
is one selector-shaped element per field. Check every group of adjacent flags
against the field or fields behind it before placing any of them.

**Two absences need two treatments.** A value that is momentarily unreadable and
a value this radio does not have are different facts, and the operator acts on
them differently. If the reference's convention is to dim rather than hide — as
a segmented display must, since a segment occupies its cell lit or unlit — then
dimming is right for the first and wrong for the second: a segment that can
never light should not have been etched. Propose the second treatment; the
reference will not have one.

**Check the visual budget before spending it.** Measure what the reference has
already allocated. If brightness is carrying on-versus-off, and an
active-versus-inactive multiplier sits on top of that, then level is spent and a
third state needs a change of **shape**. Count the levels and the multipliers
before assuming there is headroom.

**Freshness is not one horizon.** Per-field policies can differ by two orders of
magnitude within one panel — a meter stale in under a second beside a control
still authoritative after two minutes. A single "not fresh" treatment reads as
the same event in both places and is wrong in both. Either vary it with the
horizon or argue explicitly that it should not vary.

**Expect the reference to be silent here.** A design reference shows working
states. Stale, unsupported, contradictory and disconnected are usually drawn
nowhere in it — which means everything proposed for them is new work rather than
adaptation, and must be labelled that way when it lands.

## Rules

**The model proposes, the contract constrains, the owner decides.** Do not close
the loop. A process that both measures distance from a reference and edits to
reduce it will optimise that distance — and the reference shows one state, so it
will happily degrade the others at no cost to the score. This is the same defect
class as a test that cannot fail: a measurement everyone trusts, measuring
something adjacent to what matters.

**A design language annotates; it does not draw the markup.** It may supply
tokens, renderers and a stylesheet. The markup comes from shared semantic
surfaces and is the same for every family. A proposal that requires new markup
is a change to the shared layer and must say so.

**Two channels reach CSS, and they are not interchangeable.** Values CSS must
*consume* — lengths, colours — travel as custom properties, because CSS can read
those. Values CSS only *selects on* travel as data attributes, because an
attribute is a string. `annotate()` in `semantic/design-language-renderers.ts`
copies top-level primitives only; anything nested in the returned descriptor
reaches no attribute in any state.

**Every claim carries how it was established** — the command run or the file and
symbol opened. A count with no adjacent command is a claim, not a measurement,
and the grammar of a ratio makes it read as though it had already been counted.

## Verifying a proposal is buildable

Before handing it over, for each backed element:

- Name the surface it mounts on and confirm that surface is in
  `SEMANTIC_SURFACE_NAMES`.
- Confirm the layout can mount it. In the dual composition most optional
  surfaces render only through a declared zone; a manifest that declares no zone
  for a surface mounts nothing, silently.
- Confirm the styling reaches it. `design-language-selector-reachability.component.test.ts`
  reports any selector that addresses markup nothing emits.

A proposal that passes those three is implementable. One that does not is a
plan, and should say which of the three it fails.
