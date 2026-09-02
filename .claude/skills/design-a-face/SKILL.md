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

## Phase 0 — check whether a design language for this reference already exists

**Search before you write, before anything else here.** This can change what
the rest of the exercise even is, which is why it comes before Phase 1 rather
than alongside it. Read `frontend/src/presentation/languages/` — every shipped
design language lives there. Checked: `segmentline` is on `main` in full —
three renderers (`frequency-renderer.ts`, `meters-renderer.ts`,
`state-feedback-renderer.ts`), `tokens.ts`, a stylesheet (`segmentline.css`)
and five test files (`find frontend/src/presentation/languages/segmentline
-type f`). Skipping this step lets a proposal read as greenfield work when it
is half-built — exactly the omission CLAUDE.md's "search before you write" and
"close enough is reuse" scope rules exist to prevent.

A hit is not "reuse it" and stop. Establish three things: what the existing
language already covers, what it does not, and whether closing the gap is a
stylesheet change (`tokens.ts` / the family's `.css`, the cheap tier Phase 4
names) or new markup (a change to the shared semantic-surface layer, not to
this design language). Only then does deriving a fresh contract in Phase 1
make sense to run.

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

The state vocabulary is the part designs get wrong, and there are **three
mechanisms**. Do not generalise any of them.

**The one that decides whether a block exists at all.** `RadioViewModel`
declares some of its groups optional — `readonly meters?:`, `readonly dsp?:`.
Run the extractor; its "How absence is expressed" section states exactly how
many, freshly, every time. An absent group is not the same fact as every field
in it being unsupported: a
present group can hold nothing but unsupported fields, and a design reaching
into an absent one throws. Each group's docstring names its own gate. This is
the first question for any block, before any question about its contents.

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
resulting document told a designer to distinguish four states that most
surfaces cannot express — `RxTxSurface` and `VfoSurface` are the only two
that read `txTarget` (`grep -rl txTarget frontend/src/semantic/*Surface.svelte`
names exactly these two). Run the extractor and read what it reports per
type; never carry a vocabulary across from one field to the rest.

If a design needs *never asked* distinguished from *answer aged out* on a field
that does not carry the reason union, that is a request for a state the model
does not have, and belongs on the unbacked list rather than in the drawing.

Output of this phase: per surface, the fields, their state sets, and whether
this radio backs them. Say which are structurally absent and which are merely
unobserved right now — they are different findings.

**The extractor is structurally incomplete, and spot-checking its output is not
enough.** It reads `radio-view-model.ts` and the profile. Reason vocabularies
declared in *imported* files are invisible to it no matter how its patterns are
fixed — `FrequencyPermit` lives in `$lib/utils/tx-permit`, and
`DisabledReasonCode` is carried by a by-name list rather than attached to any
field's type. Reading what it printed will not surface either.

So the check is not "read its output carefully". It is: **grep the model for
every reason literal and follow each one to where it is declared.** Four
vocabularies have been found this way where the extractor reported one, and the
one it is furthest from reporting — the by-name disabled-reason list — is the
one a design most needs, because it answers *why is this control not operable*.

It has also been wrong three times in ways patterns did fix, and the last was
the largest: reporting a union from one type as model-wide; reporting a
feature the profile disables in a comment; and, until it was fixed, silently
dropping all fourteen of `RadioViewModel`'s optional groups because its field
pattern did not accept the `readonly` modifier those groups declare — the
reported count read as 10 fields against a real 24. A generated contract is a
fast starting point, never evidence.

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
   groups, what is dimmed against what is bright. **Measured with a script, not
   estimated by eye** — see below.
3. **Why each part is where it is.** What is read continuously, what is glanced
   at, what is set once a session and then ignored. What must be adjacent to
   what because they are operated together. What is separated because
   confusing them is expensive.

### Measuring the reference

**Do not estimate proportions by eye. Run the script.**

    ./measure-reference.py selftest
    ./measure-reference.py bands   <image>
    ./measure-reference.py columns <image>
    ./measure-reference.py bands   <image> --crop L,T,R,B
    ./measure-reference.py bands   <image> --by colour
    ./measure-reference.py bands   <image> --smooth 8
    ./measure-reference.py bands   <image> --signal variation

`bands` profiles rows and finds the horizontal bands; `columns` profiles
columns and finds the vertical divisions. It reports every run as a **share of
the measured box**, with the pixel range beside it, and the gaps between runs.

**Run `selftest` first, every time, and read its output.** It plants three
bands — one of them deliberately low-contrast — plus a coloured patch, writes
them to a real file, and recovers all four by calling `load()` itself — the
same function `bands`/`columns` use — so a broken `--by colour` dispatch fails
it, not only a broken threshold. It fails loudly if any is not recovered
exactly. The low-contrast band is the one that matters: an earlier version
planted only strong bands, and deliberately breaking the threshold left it
green, so it verified nothing. An ideal case survives being measured badly.
It also checks `--smooth` (the same three bands must still be recovered,
strictly wider, at `smooth=5`, and a second, larger window must widen them
further still — not just the same window applied again) and `--signal
variation` (a scanline fixture that `level` cannot separate into a band and a
gutter by design, but `variation` can), both routed through `main()` rather
than called directly so a flag dropped between the CLI and `report()` fails
the check too, not only a broken function — and the degenerate-colour warning
(`DEGENERATE_CHROMA`, above). Confirmed by mutation to fail the self-test: a
no-op smoothing branch, `--smooth` dropped before it reaches `report()`,
`--signal` inverted at the CLI, a smoothing window hardcoded regardless of
the requested size, and the degenerate-colour threshold broken in either
direction (`selftest` in `measure-reference.py`).

**Two scales, same method.** A profile over the whole panel gives the bands and
the divider. For a fine element — glyph cells, segment pitch, chip padding —
`--crop` that region and profile it in its own resolution. Precision comes from
the crop, not from a better algorithm.

**`--by colour` profiles distance from grey rather than darkness.** On a panel
that is monochrome except where colour carries a meaning — a transmit group, an
alarm — this locates that meaning without being told where to look. It also
separates a *dimmed* indicator, which is the same hue with less ink, from a
*differently coloured* one: the ink profile confuses them, the colour profile
does not.

**This is a statistic over the measured box, not a judgement about "the
ground."** "Distance from grey" only isolates an accent if the ground has
none to compete with — but the median chroma is taken over whatever box is
measured, so the same warning fires whether the ground itself is tinted or a
crop is simply tight enough that the accent IS most of the box. On a
reference whose ground itself carries a tint — this one's amber LCD, not a
neutral panel with one coloured accent — most of the box already reads as
"coloured", and `--by colour` profiles the ground, not any meaning: `bands
/var/tmp/ftx1-reference.png --by colour` gives one run at ~94%, and
colour-distance there is *anti-correlated* with ink (`corrcoef ≈ -0.63` —
the mode sits over the LEAST-inked pixels, i.e. the ground). The script
detects this itself: `load()` prints a WARNING when the measured box's
median chroma exceeds a threshold (`DEGENERATE_CHROMA` in
`measure-reference.py`). `selftest` only exercises the whole-image case; it
does not claim the threshold behaves any particular way on a crop. If that
warning fires on a `--by colour` run, check whether it is because the ground
itself is tinted or because the crop is mostly the accent, before reporting
the run as a colour-based finding.

**`--smooth <window>` flattens a regular texture before profiling.** Design
references often carry one — scanlines on an LCD imitation, a dot grid —
whose period is small and whose amplitude rivals a real element's. Untreated,
the detector can return one run per texture stripe and bury the bands.
Measure the stripe spacing in the raw (unsmoothed) profile first, then choose
a window a few times that period: wide enough to flatten the stripes, narrow
enough to leave a genuine band's boundary where it is.

**`--signal variation` profiles spread instead of level.** A scanline ground
inks every row about equally, so the *mean* ink per row (`level`, the
default) cannot tell a band from a gutter — both read as inked. The *spread*
of ink within a row can: a row that is part of a real band varies across its
width (edges, glyphs, a filled meter segment sitting beside empty ones),
while a row that is only texture does not vary beyond the texture's own
period — verified on a planted fixture built for exactly this contrast
(`selftest`'s `--signal level` vs `--signal variation` check). It is not a
guaranteed fix — on this skill's own reference it does not separate
anything at all; see the "Known limit" note below. Reach for it when
`level` reports "no runs" or one run spanning nearly the whole image on a
reference you know is not blank; it is worth trying, not a promise.

**Known limit, and worth knowing before you trust a number:** on a reference
with a regular texture (this one has a fine scanline-style ground), the
floor-relative threshold reads the texture itself as ink almost everywhere,
so a whole-panel `bands` collapses to nearly one giant run instead of finding
the real bands. Reproduced: `bands /var/tmp/ftx1-reference.png` on the
2572×1100 reference returns exactly **one run spanning 98.0%** of the image
(`22-1100`). Neither treatment above rescues *this particular* reference:
`--smooth` 8/24/36/48 each still return one run, 98.3-99.7% of the image;
`--signal variation` returns the same single run as `level`, byte-identical
— and on the crop the trial used (`--crop 60,55,2515,1060 --min-run 8`),
`level` finds 12 runs against `variation`'s 11, not the clean split the
mechanism above predicts. The floor here is set by the bright out-of-panel
border rather than by the scanline texture the two treatments target, so
nearly everything clears the threshold in every mode. Where `--smooth` or
`--signal variation` does surface a real boundary on a given reference,
crop to it; where neither does, as here, do not crop blind — fall back to
reading proportions from the image directly with a stated, generous
tolerance instead of reporting a script number this instrument cannot
produce for a texture this uniform.

**Report proportions of the panel box, never pixels**, and state the tolerance.
The stage scales as one block, so a pixel figure is true only at one size,
while a share of the panel survives scaling. Say which image you measured and
its dimensions.

**No external service.** This is arithmetic over a local file. A service
returns a number that has to be taken on faith, which is the opposite of what
every other rule here demands — and it means sending the design somewhere,
which is a decision for the owner rather than a convenience for the agent.

**The constraint that actually stops this**, and it has: an agent given the
image *in conversation* has no file to run a script over, and cannot measure at
all. Say so plainly and fall back to reading proportions with a stated,
generous tolerance — do not present an eye estimate in the same form as a
measurement. If a file path exists, ask for it; it is the difference between
"about a third" and a number someone else can reproduce.

**And a measured proportion is evidence for a rule, not a result.** A precise
figure with no reason attached still does not transfer to a changed element
set. "Readout 14%, spectrum 38%" decides nothing; "the readout takes a fixed
share sized for legibility, the spectrum takes the remainder" decides
everything, and the measurement is what supports it. Precision makes a wrong
rule *more* convincing, so measure in order to test a rule, not instead of
having one.

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

**Two mechanical checks the Backed list must pass, not two more things to
remember:**

1. Every drawn element names the field backing it, from the contract
   `extract-contract.py` emitted — or is marked `unavailable` with a reason.
2. Every drawn control names the intent it dispatches, from
   `RADIO_INTENT_NAMES` (the extractor's "Send side" section) — and the
   feedback mechanism for it — or is marked `display only`.

A proposal failing either is a plan, not an implementable one.
`./extract-contract.py --checklist` emits a skeleton with one line per field
and per intent to fill in; `--checklist --validate <file>` fails, naming
what is missing, when a filled-in proposal drops one (`./extract-contract.py
--selftest` proves this discriminates: a complete proposal exits 0, one with
a field or an intent struck exits non-zero).

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

The worked example: `components-v2/meters/LinearSMeter.svelte` reads `--v2-*`
variables — run `./extract-contract.py` and check its "What already draws each
surface" section for the current count — so its colours are theme-restylable,
but its segment count and bar geometry are computed in code, and its peak
decay is a constant. A design language declaring a meter track width and
segment gap cannot move any of it. That is why segmentline's meter tokens
could not have worked even with a functioning value channel: there was
nothing on the other end reading them.

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

## Ask, and here is exactly when

The failure mode of this work is not confusion, it is **confident
plausibility**. A misread element gets a sensible name, the name gets a
rationale, the rationale becomes a constraint, and nothing downstream can tell
it was invented. So "ask if unsure" is too weak to act on. These are the
triggers, and at any of them stop and ask rather than pick the likely reading:

- **You cannot say what an element is.** Ask the owner. Name it as unidentified
  in the meantime; do not give it a plausible name — a wrong name is inherited
  silently by everything after it.
- **The measurement is ambiguous.** Ask the owner. Two elements with no gutter
  read as one run; ask which it is rather than splitting it by eye.
- **You cannot tell established from inferred.** Ask the owner. They know their
  own intent, and one sentence from them converts a guess into a fact — the
  cheapest question available.
- **The reference shows something no field can fill.** Ask the owner. Whether
  to widen the contract or drop the element is a scope decision, not a design
  one.
- **A rule you extracted cannot place an unshown field.** Ask the owner. That
  is the signal it described the picture rather than the reasoning — say so,
  instead of stretching it.

The instance: this skill's own earlier pass read the reference as a control
panel and derived placement rules about adjacency and reach. It is a display —
every element is an indicator, there is nothing to operate. Half the reasoning
was built on that before the owner corrected it in one sentence. The agent had
no way to know; it also never asked.

Asking costs one message. A wrong premise costs everything built on it, and it
does not announce itself.

**If no answer arrives** — a one-shot task, nobody to ask back — do not read
that as licence to produce nothing. Name the assumption you are making
explicitly (which trigger fired, and the reading you picked), mark everything
downstream of it as resting on that assumption rather than a finding, and
continue. An unanswered question is not permission to guess silently; it is
permission to guess out loud and label the guess.

## Traps

Every one of these was paid for. They are listed with the instance because an
abstract warning does not survive contact.

**A skin's markup is invisible to the design-language check.** It mounts the
semantic surfaces, not skin layouts. So a rule consumed only by a skin component
reports as an orphan, correctly by the check's own scope and wrongly in fact.
That is exactly how `dl-glass` came to be renamed away in `segmentline.css`
on main (`8cc5471d`, MOR-2163/#2968) while its only consumer,
`PeerSplitLayout.svelte`, existed on the unmerged branch
`codex/mor-2153-peer-split-chassis` (`968aec6f`) rather than on main. If you
retarget or delete a rule, grep the skins too.

**A component's own `<style>` block is invisible to its own tests.** Deleting
the entire block from `PeerSplitLayout.svelte` left all four of its tests green
— reported by the builder. The reviewer confirmed the file is in that state at
`codex/mor-2153-peer-split-chassis` (commit `968aec6f`, unmerged) but did not
re-run the deletion, so the claim is relayed with a checkable source rather
than independently reproduced. A design change that lives in a component's
styles cannot be verified by mounting it; a pin has to read the source text,
the way the stylesheet tests already do.

**A rendered value is a claim about the radio.** Do not draw a number you do not
have. A meter at zero says *the signal is at the noise floor*, which is a
statement about the world, and if the reading is merely absent that statement is
false. The danger is narrower than it first appears: when everything is absent
the panel is obviously dead and nobody is misled. It bites when the absence is
**partial** — one stale value among a dozen live ones, borrowing their
credibility. This repository already has a defect of that shape and a fix for
it: `panel-props.ts: toMeterProps` (MOR-1409 A12) stopped fabricating a
zero-meter reading for an unobserved receiver, because a real
S0/zero-power/zero-SWR reading is indistinguishable from one that was never
taken.

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

**Import boundaries are enforced, not advisory.** `semantic/` may not import
skins or runtime internals (`FORBIDDEN_SEMANTIC_IMPORTS`,
`frontend/eslint.config.js`); `presentation/` may not import transport,
stores, the runtime barrel, or `lib/runtime/commands/*`
(`FORBIDDEN_PRESENTATION_IMPORTS`, same file). A proposal that would need
either is a change to a lower layer, not a design-language change. Run
`./extract-contract.py` for the current send-side and feedback-adoption
facts these rules used to be typed out from by hand — they rot the same way
any hand-maintained count does.

## Verifying a proposal is buildable

Before handing it over, run the two mechanical checks Phase 3 states:

1. Every drawn element names its backing field from the extractor's output,
   or is marked `unavailable` with a reason.
2. Every drawn control names the intent it dispatches and the feedback
   mechanism for it, or is marked `display only`.

A proposal failing either is a plan, not an implementable one. Then, for
each backed element:

- Name the surface it mounts on and confirm that surface is in
  `SEMANTIC_SURFACE_NAMES`.
- Confirm the layout can mount it, and **do not assume an undeclared surface
  vanishes.** In the dual composition nine surfaces are mounted with
  `allowBare={false}` and those do vanish silently when no zone declares them.
  The rest render **bare** — visible, but with no wrapper and so no CSS hook.
  And `zoneShowsSurface` is fail-**open**: `zone === undefined ||
  zone.includes(surface)`, so a surface whose zone is not in the plan renders
  rather than disappearing.

  An earlier version of this sentence said an undeclared surface mounts nothing,
  full stop. An agent reasoned from it, reached a wrong conclusion about which
  strips render, and was corrected only by reading the code. Check the
  `allowBare` argument at the call site for the specific surface; there is no
  general answer.
- Confirm the styling reaches it. `design-language-selector-reachability.component.test.ts`
  reports any selector that addresses markup nothing emits.
- **Confirm the language can be selected at all — the stricter fourth check,
  and the one the first three don't cover.** `WORKSPACE_DESIGN_LANGUAGE_IDS` in
  `presentation/workspace/contract.ts` is the closed set an operator can pick;
  a language absent from it cannot activate in production regardless of what
  the other three checks say — `pickId` falls back to `studioline` instead.

A proposal that fails any of these four mount checks, or either of the two
mechanical checks above, is a plan, not an implementable one, and should say
which it fails.
