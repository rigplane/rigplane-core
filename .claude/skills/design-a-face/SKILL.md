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

The state vocabulary is the part designs get wrong. A reading is
`{status:'known', value}` or `{status:'unknown'}`, and an unknown carries a
reason: **`not-observed`** (never asked), **`stale`** (asked, answer aged out),
**`unsupported`** (this radio cannot), **`contradiction`** (conflicting
answers). Availability is carried separately from the reading.

Those four are not shades of "no value". *Unsupported* and *stale* mean
opposite things to an operator, and telling them apart matters more than any
aesthetic decision in the proposal.

Output of this phase: per surface, the fields, their state sets, and whether
this radio backs them. Say which are structurally absent and which are merely
unobserved right now — they are different findings.

## Phase 2 — read the design reference for its reasoning, not its layout

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
