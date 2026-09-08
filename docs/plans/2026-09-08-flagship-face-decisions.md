# The v3 flagship face — recorded design decisions

**Date:** 2026-09-08
**Status:** Decided by the owner, 2026-09-08. None of it is enforced by a test.
**Scope:** the v3 flagship face only — type ladder, frequency sizing, layout
thresholds, side-panel width, text contrast, colour vocabulary, and two
removals. Nothing here governs the v2 shell, the LCD skin, or mobile.

## 1. Why this document exists

The values below were arrived at on 2026-09-08 while measuring a standalone
HTML bench. That bench is roughly 632 KB, is not in this repository, and
compiles to nothing — so it is not linked here, and no bench-measured value
below can be re-measured from a tracked file. If the values are not written
down,
the next person re-derives them and gets different ones.

Every entry therefore states three things: the value, what it governs, and how
it was established. Where the provenance is a measurement I was given, it says
so. Where the provenance is reasoning rather than measurement, or where the
arithmetic I was given does not reproduce the value in force, it says that
instead of inventing a derivation. Section 11 collects every such gap.

**Verification revision:** `origin/main` at `331ef1c72`. Every code claim below
was established by running the named command, or reading the named file, in a
worktree at that revision on 2026-09-08.

**Two conventions:** figures labelled *given* come from the owner's bench
measurements and I did not reproduce them. Figures labelled *derived here* are
arithmetic I ran over the given figures in writing this document; they are as
good as the inputs and no better.

## 2. Type ladder

**Control label floor: 13 px.** Governs every label on the face; labels are
set in all caps. The stated reason is that cap height runs about 0.7 em, so
13 px of type is about 9 px of actual letter, and the users are 45 and older —
below this, reading becomes work. This is the reasoning, not a measurement:
what fixed 13 rather than 12 or 14 is not recorded, and the 0.7 em cap-height
figure is not derived from anything in this repository.

**Control height: label × 2.4, floored at 24 px.** The 24 is WCAG 2.2 SC 2.5.8
Target Size (Minimum), which applies to pointer inputs generally rather than to
touch alone. That is an external standard, not a repository fact.

*Derived here:* at the 13 px label floor, 13 × 2.4 = 31.2 px, so the 24 px
floor does not bind. It only binds below a 10 px label (24 / 2.4 = 10). Today
the 24 is a guard against a future smaller label, not an active constraint.

**The proportional rule "label = frequency / 5" is inert.** It was called a
ladder for half a day while behaving as a constant. At every frequency size
this face uses, f/5 comes out under 13, so the 13 px floor wins every time.

*Derived here:* f/5 reaches 13 only at f = 65 px, and at the 33 px frequency
floor (§3) f/5 is 6.6. The rule would become live only above a 65 px
frequency. The ceiling in force is the face's size knob and is not a fixed
number, so I cannot state that the rule is unreachable — only that nothing in
the recorded range reaches it.

**Must-not-miss keys — PTT and its peers — are one tier up: × 1.4 of the
normal height.** Explicitly **not** 44 px. 44 comes from touch guidance, and
this layout is pointer-only; mobile has its own. A borrowed 44 was in place
until the owner caught it.

*Derived here:* × 1.4 of the 31.2 px normal height is 43.68 px, within about
0.3 px of the 44 that was rejected. What changed is the provenance, not this
month's pixel count: the derived tier moves with the label floor, a literal 44
would not.

**Named sizes reduced from five to two.** The old set was 22 / 28 / 32 / 36 /
44, all picked by eye. The new set is «обычный» — the ladder's output — and
«крупный» — × 1.4 of it.

## 3. Frequency

**Sized from its container, not in px:**

```
clamp(floor, calc((100cqw - 125px) / 6.2), ceiling)
```

**125 px** is what the block spends before the digits start.

*Flag — the itemisation does not reproduce the number.* The terms given are
9.5 px gutter, 66 px badge column, two 12 px grid gaps, and 12 px padding,
stated as summing to 118, with 125 being 118 plus margin. Those five terms sum
to 111.5, not 118. I could not account for the missing 6.5 px. The value in
force is 125; the itemisation is recorded as given and does not check out.

**6.2** is the readout's width in units of its own font size. *Given:* measured
6.07 on the live page at three different sizes, then rounded up so the fit is
never exact. *Derived here:* 6.2 / 6.07 gives about 2.1% headroom.

**Floor 33 px** — derived, not chosen. The frequency must stay at least 2.5×
the largest non-frequency text, and 2.5 × the 13 px label floor is 32.5,
rounded up to 33. So the relation in force is `floor = ceil(2.5 × labelFloor)`,
not equality; §10 depends on that distinction.

A floor of 20 was in place first. *Flag:* the failure it caused is recorded as
"the ratio fell to 2.0", and I cannot reproduce that — 20 against a 13 px label
floor is a ratio of about 1.54. Either the label floor differed when the 20 was
in force, or one of the two numbers is misremembered. The decision (33, derived
from 13) stands; the historical 2.0 is unreconciled.

**The size knob became the ceiling on the face** and stays the size elsewhere.

## 4. Layout switch at 1760 px

Below a face width of 1760 px the receivers span the full width **above** the
side panels; the panels keep their width and start lower; the panorama stays
**between** them.

**1760 is set by the meters, not by the digits.** The three small instruments
carry a base width of 170. *Given* — with the panels flanking, they measure:

| Face width | Small instrument |
|---|---|
| 1900 | 194 |
| 1800 | 177 |
| 1750 | 169 |
| 1600 | 144 |
| 1300 | 94 (visible mush) |

So they fall under their own design width just under 1760.

*Derived here,* by linear interpolation over the 1800 and 1750 rows only:
8 px of instrument per 50 px of face, putting the 170 crossing at about
1756 px. 1760 sits just above that crossing, so the switch fires before the
instruments go under-width. This is interpolation over two of the five points,
not a measurement.

**Two earlier thresholds were wrong for the same reason.** 1575 (where the
digits leave their ceiling) and 1280 (where they reach their floor) were both
derived from the frequency. Neither has anything to do with the part that
breaks first. **This is the most reusable lesson here: derive a breakpoint from
the element that fails earliest, not from the element you happen to be looking
at.** Both figures are recorded as given; I did not re-derive either.

**Receiver minimum width: 578 px**, also from the meters — three instruments at
170 plus row gaps, label gutter and block padding. *Given:* arithmetic produced
572 and measurement produced 578; 578 is the value in force. *Derived here:*
3 × 170 = 510, so the arithmetic attributed 62 px to gaps, gutter and padding,
and the measurement came out 6 px above it. Those 62 px are not itemised, so I
could not check them.

Below 578 px the deck scrolls sideways rather than crushing, because the two
receivers stay side by side. That last point is a standing owner ruling; I found
no record of it under `docs/` (`git grep -in "side by side\|side-by-side" --
docs/` returns five lines, none of which records it), so its only provenance is
the owner.

## 5. Side panel width

Computed from a **clone of the widest real key on the face**, never from a
literal: two keys + gap + padding.

Currently **226 px outer / 194 px inner**, from a 93 px key. *Derived here,* the
decomposition is self-consistent: 2 × 93 = 186, plus an 8 px gap gives the
194 px inner; 226 − 194 leaves 32 px of padding, 16 per side. The 8 and the 16
are residuals I computed, not values that were handed to me.

A hand-built probe under-measured by about 32 px once, because it lacked the
indicator's padding, and clipped `DIGI-SEL` to `DIGI-`. The residual padding
above is also 32 px; whether these are the same 32 px is not established.

## 6. Contrast

Nothing that carries words sits below **7:1**, measured on **both** grounds the
page uses — page `#0b0e12` and panel `#12181e` — because a colour that clears 7
on one ground can miss on the other.

Text tokens, *given:* `--ink` 15.0:1, `--ink-2` 8.7:1, `--ink-3` 7.9:1. *Flag:*
one ratio per token was supplied while the rule is stated over two grounds, so
the recorded figure is presumably the worse of the two — but which ground
produced it is not recorded.

Before this pass, captions, tab labels and the footer sat at 3.1–3.4:1 at
10–13 px.

WCAG 1.4.3 asks 4.5:1 and 1.4.6 asks 7:1, so the face targets the AAA level
for text.

**Dim instrument elements are dim on purpose and are not counted** — unlit
segments, separators, and the like. They carry no words.

None of these tokens exists in the tree yet. `git grep` at `331ef1c72` finds no
occurrence of `--ink-2`, `--ink-3`, `0b0e12` or `12181e`, and the bare `--ink-*`
family that does exist belongs to another skin
(`frontend/src/skins/segmentline/CenterstageDisplay.svelte` defines
`--ink-strong`, `--ink-mid`, `--ink-soft` and others). The flagship names will
need namespacing when they land.

## 7. Colour vocabulary

| Treatment | Meaning |
|---|---|
| grey | not in the path |
| red edge | live in the path — powered, relays closed, will transmit the moment you key |
| red fill, white text | on air right now |
| blue fill | mode |
| green edge | processing on |
| white edge | a selected setting |
| ochre edge | RF gain backed off |

**The inversion belongs to the state, not to a badge.** TX inverts while
transmitting; ATU inverts while tuning. TX belongs to a receiver, so only the
live one goes red. ATU belongs to the station, so it goes red in both blocks.

**Red appears nowhere else on the face.**

## 8. No "unknown" state

**Owner ruling, 2026-09-08:** a control is drawn with a value or not drawn at
all. Two states, not three. A bad link surfaces as the whole-face veil, never
as a per-control mark.

**This removes something that renders today.** The cost is real and was
verified at `331ef1c72`:

- `frontend/src/semantic/radio-view-model.ts: DisplayObservation` is a live
  three-branch type: `{state: 'current' | 'stale', value}`, `{state: 'unknown',
  reason}` — with four reasons, `not-observed`, `invalid-value`,
  `invalid-evidence` and `identity-unresolved` — and `{state: 'unsupported'}`.
- Seven non-test source files under `frontend/src/` reference it
  (`git grep -ln "DisplayObservation" -- 'frontend/src/**' | grep -v
  __tests__`): `frontend/src/lib/runtime/adapters/display-observation.ts`,
  `frontend/src/lib/runtime/adapters/panel-adapters.ts`,
  `frontend/src/lib/runtime/adapters/radio-view-model-adapter.ts`,
  `frontend/src/lib/runtime/adapters/scope-passband-display.ts`,
  `frontend/src/semantic/VfoSurface.svelte`,
  `frontend/src/semantic/radio-view-model.ts`, and
  `frontend/src/semantic/tx-meter-display.ts`.
- `frontend/src/semantic/VfoIndicatorRow.svelte` reaches the type through
  `frontend/src/semantic/radio-view-model.ts: DisplayObservedField`, and does
  render the unknown case: its RF-gain entry is gated on the display state not
  being `unsupported`, carries a `data-display-state` attribute that falls back
  to `unknown`, and prints an em dash when the state is neither `current` nor
  `stale`. The same component renders a dedicated element for an unknown
  S-meter reading, under the test id `receiver-s-meter-unknown`.

So honouring the ruling on the flagship face means the veil has to cover
everything those per-control marks covered. Whether the three-way survives
outside the flagship face — in the v2 skins, or the LCD — is not decided here.

## 9. Two labels with no instrument behind them

**`AGC-F` — removed. Claim verified.** `git grep -c "AGC-F" origin/main` exits
1: no match anywhere at `331ef1c72`, in tracked text or in binary files. The
real rendering is `"AGC "` plus a value from `agcMode`:
`frontend/src/semantic/VfoIndicatorRow.svelte` renders `AGC` followed by the
formatted `agcMode`, and `frontend/src/semantic/VfoSurface.svelte` builds a
badge labelled `AGC` plus the value or an em dash. Both read
`agcMode` on `frontend/src/semantic/radio-view-model.ts:
ReceiverIndicatorViewModel`.

**`MOX` — removed. The ViewModel claim holds; the framing needs a
correction.** `git grep -rn "MOX" origin/main -- 'frontend/src/**'` exits 1, so
no `*ViewModel` declares such a field. But the string is **not** absent from
`frontend/`: `frontend/scripts/i18n-check.mjs` lists `MOX` in its allowlist of
radio and operating abbreviations. Consequence worth acting on separately: removing
the label leaves an allowlist entry with no referent.

## 10. What is testable, and how

No test can reference any of these values today, because none of them is in the
tree: the token names and both ground colours are absent at `331ef1c72` (§6),
and so is the rest of the flagship face. Three of the decisions can be tested
once it lands, at very different prices.

**1. The frequency floor against the label floor — cheapest.** A pure unit test
over two exported constants: no DOM, no fixture, no rendering. It is the
cheapest of the three by a wide margin.

One caveat that would otherwise make the test fail on its first run: **it cannot
assert equality.** 2.5 × 13 = 32.5 and the floor in force is 33 (§3). The
assertion has to be `freqFloor >= 2.5 * labelFloor`, or
`freqFloor === Math.ceil(2.5 * labelFloor)`. An equality test as originally
stated fails against the values recorded here.

**2. No text token below 7:1 on either ground — nearly as cheap, and there is a
template.** A pure function over the token table, computing WCAG luminance and
contrast in-test. This does not need inventing:
`frontend/src/presentation/languages/studioline/__tests__/tokens.test.ts`
already does exactly this shape — it computes relative luminance and contrast
ratio from hex literals and asserts every state tone against both surfaces
declared in `frontend/src/presentation/languages/studioline/tokens.ts:
STUDIOLINE_SURFACES`, which is a two-ground `{dark, light}` record. The
flagship's page and panel grounds are the same shape with a 7:1 floor instead
of 3:1. A rendered-DOM variant also exists if one is ever wanted:
`frontend/fixtures/assertions.ts: runAssertions` measures contrast from real
`getComputedStyle()` values.

**3. The layout threshold against the meter base width — most expensive.** This
one cannot be a pure function: it needs a rendered DOM at several face widths
and a real measurement of the small instrument's box, so it is a component or
browser test. It is also the assertion that would have caught the 1575 and 1280
mistakes, which the other two would not.

If only one is written, (1) is cheapest but catches a single relation; (2) costs
little more, has a template to copy, and covers a whole class of regressions
that would otherwise reach users as unreadable text.

## 11. Gaps in provenance

Collected so no reader mistakes a recorded number for a verified derivation:

- **§3** The 125 px itemisation sums to 111.5, not the 118 stated. 6.5 px
  unaccounted.
- **§3** The historical "ratio fell to 2.0" under a 20 px floor is not
  reproducible against a 13 px label floor, which gives about 1.54.
- **§2** The 13 px label floor has a recorded reason but no recorded
  measurement, and the 0.7 em cap-height figure has no source in this
  repository.
- **§4** The 62 px of row gaps, label gutter and block padding inside the 572 px
  arithmetic is not itemised, so the 572 → 578 gap could not be checked.
- **§4** 1575 and 1280 are recorded as given; I did not re-derive either.
- **§4** The ruling that the two receivers stay side by side has no record under
  `docs/`; its only provenance is the owner.
- **§5** Whether the probe's missing 32 px is the same 32 px as the computed
  padding is not established.
- **§6** One contrast ratio per token was supplied against a two-ground rule;
  which ground produced each figure is not recorded.
- **§1** The bench is not in this repository, so no bench-measured value here
  can be re-measured from a tracked file. The code claims in §8 and §9 can be,
  and each names the command that establishes it.
