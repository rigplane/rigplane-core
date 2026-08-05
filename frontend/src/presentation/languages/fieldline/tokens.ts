/**
 * `fieldline` token set (MOR-1074) — the independent PROOF family selected by
 * MOR-977 §4.1: a rugged field console for a deployed portable station operated
 * with gloves on, in daylight, possibly one-handed. Values here implement
 * §2.2's seven axes; the CSS half of the same slice is `./fieldline.css`.
 *
 * Its whole reason to exist is that it differs from `studioline` on ALL SEVEN
 * acceptance axes at once (§4.3.1) while consuming the identical contract — so
 * the token set is deliberately the opposite value on every axis a token can
 * express: mono 700 against proportional 200, a 3px opaque border against no
 * border, a 3px segment gap against a continuous rail, and `rankedGroups: false`
 * against `true`. Nothing else about the contract changes.
 *
 * Contract instance only. This file declares no renderer, imports no runtime,
 * store, transport or capability code, and knows nothing about any radio.
 *
 * COLOUR IS ARITHMETIC HERE, exactly as in studioline's slice: every tone below
 * clears 3:1 against BOTH surfaces in `FIELDLINE_SURFACES`, computed in
 * `__tests__/tokens.test.ts` rather than asserted by eye. fieldline supplies its
 * own ring tone rather than inheriting the shared placeholder's `var(--accent)`,
 * which carries no such guarantee (owner decision Q1, 2026-08-04).
 */
import type { DesignLanguageTokens } from '../contract';

/**
 * The two grounds the language must hold on (MOR-977 §5). For fieldline the
 * second one is not "light mode", it is DAYLIGHT MODE — pure black on pure
 * white, the grammar's best condition rather than a compromise — so the pair is
 * harder than studioline's warm off-white and near-black.
 */
export const FIELDLINE_SURFACES = { dark: '#0A0A0A', light: '#FFFFFF' } as const;

/**
 * One mid-luminance palette serves both surfaces: a state channel cannot afford
 * a per-mode fork. Every entry clears 3.9:1 on BOTH grounds — a wider margin
 * than the 3:1 floor, because flat fills give this grammar no gradient or glow
 * to fall back on when the sun is on the screen.
 */
export const FIELDLINE_PALETTE = {
  focus: '#0A6ED1',
  rxIdle: '#6E7A85',
  rxActive: '#008A5C',
  rxTuning: '#0A6ED1',
  txIdle: '#8A6A2E',
  txActive: '#E03A2F',
  txTuning: '#B25A00',
  inert: '#6F7A85',
} as const;

/**
 * Knocked-out label text on a filled slab or band. Black in BOTH modes, unlike
 * studioline's mode-dependent knockout to its own surface — §2.2's "solid red
 * with knocked-out black text" is a fixed treatment, and pinning it to one
 * value is what lets a single arithmetic assertion cover both modes. Clears
 * 4.5:1 on `txActive`, which is why that red is lighter than studioline's.
 */
export const FIELDLINE_KNOCKOUT = '#0A0A0A';

/**
 * Label TEXT is per-mode, unlike the state channel above. A single tone cannot
 * be 4.5:1 against both black and white at once (the two required luminance
 * bands do not overlap), and taking the 3:1 non-text floor for label text is
 * what produced finding MOR-1277 against studioline. So text forks by mode and
 * each half is asserted at 4.5:1 against its OWN ground.
 */
export const FIELDLINE_LABEL_TONES = {
  dark: { text: '#F2F5F7', muted: '#C2CBD2' },
  light: { text: '#000000', muted: '#3A4249' },
} as const;

/** `var()` with the literal fallback, so a bundle mounted without the CSS still renders the proven colour. */
const tone = (name: string, fallback: string): string => `var(--dl-fieldline-${name}, ${fallback})`;

export const FIELDLINE_TOKENS: DesignLanguageTokens = {
  // Monospace 700 slab digits, never a proportional face: the chunky uniform
  // numeral IS the silhouette. `tabular-nums` stays mandatory even on a mono
  // face, because the contract requires the declaration rather than the
  // happy accident of a family that already advances uniformly.
  typography: {
    fontFamily: 'ui-monospace, "SF Mono", Menlo, "DejaVu Sans Mono", "Roboto Mono", monospace',
    weight: 700,
    fontVariantNumeric: 'tabular-nums',
  },
  // Hard-edged blocks: zero radius, 3px opaque borders. No inset, no glow, no
  // gradient anywhere — gradients wash out in sunlight (MOR-977 §2.2).
  geometry: { radius: '0px', borderWidth: '3px' },
  // Discrete segments: a non-zero gap is precisely what makes this a segment
  // form rather than the continuous rail `studioline` owns.
  meters: { trackWidth: '18px', segmentGap: '3px' },
  // `false` is the axis: all nine digits at one size and one weight, because in
  // the field the Hz digits are read as often as the kHz ones (MOR-977 §2.2).
  frequency: { digitWeight: 700, rankedGroups: false },
  // Zero, not merely "safe": every fieldline state change is a discrete step,
  // so the grammar is fully intact under the global reduced-motion clamp.
  motion: { durationMs: 0, reducedMotionSafe: true },
  // Outline shorthand, not a box-shadow spread list (MOR-1232 D5). 3px, one
  // step above studioline's 2px: thick borders give the ring room, so it can be
  // heavier without competing with the block edge (MOR-977 §3.2).
  focusRing: `3px solid ${tone('focus', FIELDLINE_PALETTE.focus)}`,
  rx: {
    idle: tone('rx-idle', FIELDLINE_PALETTE.rxIdle),
    active: tone('rx-active', FIELDLINE_PALETTE.rxActive),
    tuning: tone('rx-tuning', FIELDLINE_PALETTE.rxTuning),
  },
  tx: {
    idle: tone('tx-idle', FIELDLINE_PALETTE.txIdle),
    active: tone('tx-active', FIELDLINE_PALETTE.txActive),
    tuning: tone('tx-tuning', FIELDLINE_PALETTE.txTuning),
  },
};
