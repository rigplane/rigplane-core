/**
 * `studioline` token set (MOR-1073) — the reference design language selected
 * by MOR-977 §4.1: a borderless studio channel strip whose hierarchy is type
 * scale and negative space, not chrome. Values here implement §2.3's seven
 * axes; the CSS half of the same slice is `./studioline.css`.
 *
 * Contract instance only. This file declares no renderer, imports no runtime,
 * store, transport or capability code, and knows nothing about any radio.
 *
 * COLOUR IS ARITHMETIC HERE. Every tone below clears 3:1 against BOTH
 * surfaces in `STUDIOLINE_SURFACES`, computed in `__tests__/tokens.test.ts`
 * rather than asserted by eye, because MOR-977 §4.4 makes rail colour a state
 * channel and §3.2 makes the contrast regime part of the grammar. That is
 * also why studioline does not inherit the shared placeholder's
 * `var(--accent)` ring: `--accent` carries no such guarantee and fails 3:1 on
 * a light skin.
 */
import type { DesignLanguageTokens } from '../contract';

/**
 * The two grounds the language must hold on (MOR-977 §5): the dark shack is
 * primary, the warm off-white is the cheap inversion. Both are the surface a
 * rail or numeral sits directly on, so they are the contrast reference.
 */
export const STUDIOLINE_SURFACES = { dark: '#0E1113', light: '#FAF7F2' } as const;

/**
 * One mid-luminance palette serves both surfaces — the only way a single
 * declared tone can satisfy 3:1 in dark AND light without a per-mode fork
 * that a state channel cannot afford to get wrong.
 */
export const STUDIOLINE_PALETTE = {
  focus: '#00819F',
  rxIdle: '#67757C',
  rxActive: '#008F86',
  rxTuning: '#00819F',
  txIdle: '#7A6A66',
  txActive: '#D63A30',
  txTuning: '#A97400',
  inert: '#748086',
} as const;

/** `var()` with the literal fallback, so a bundle mounted without the CSS still renders the proven colour. */
const tone = (name: string, fallback: string): string => `var(--dl-studioline-${name}, ${fallback})`;

export const STUDIOLINE_TOKENS: DesignLanguageTokens = {
  // Proportional grotesque at 200, never a mono face: the ultralight numeral
  // IS the silhouette. `tabular-nums` is therefore mandatory rather than
  // incidental — a proportional face jitters digit width while tuning.
  typography: {
    fontFamily: '-apple-system, BlinkMacSystemFont, "Helvetica Neue", "Inter", system-ui, sans-serif',
    weight: 200,
    fontVariantNumeric: 'tabular-nums',
  },
  // Unenclosed: the 1px is the full-bleed rail, not a box. The one radius
  // studioline allows lives on the PTT pill and is declared with it, in
  // `state-feedback-renderer.ts`.
  geometry: { radius: '0px', borderWidth: '1px' },
  // Continuous bar rail: a 4px track with no gap is what makes it a rail
  // rather than the segment form `fieldline` owns.
  meters: { trackWidth: '4px', segmentGap: '0px' },
  frequency: { digitWeight: 200, rankedGroups: true },
  // The only animation is the tuning underline slide, which is decorative and
  // dropped outright under prefers-reduced-motion (MOR-977 §3.2).
  motion: { durationMs: 120, reducedMotionSafe: true },
  // Outline shorthand, not a box-shadow spread list (MOR-1232 D5): the
  // consumer assigns this straight to `outline`, where `0 0 0 2px …` would be
  // silently invalid and the ring would simply not appear.
  focusRing: `2px solid ${tone('focus', STUDIOLINE_PALETTE.focus)}`,
  rx: {
    idle: tone('rx-idle', STUDIOLINE_PALETTE.rxIdle),
    active: tone('rx-active', STUDIOLINE_PALETTE.rxActive),
    tuning: tone('rx-tuning', STUDIOLINE_PALETTE.rxTuning),
  },
  tx: {
    idle: tone('tx-idle', STUDIOLINE_PALETTE.txIdle),
    active: tone('tx-active', STUDIOLINE_PALETTE.txActive),
    tuning: tone('tx-tuning', STUDIOLINE_PALETTE.txTuning),
  },
};
