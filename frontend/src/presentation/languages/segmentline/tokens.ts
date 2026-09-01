/**
 * `segmentline` token set (MOR-2148) — the amber-LCD instrument family.
 *
 * Grammar: a single warm glass field, ink printed ON it rather than lit
 * against black; every control is an outlined cell, never a filled button;
 * hierarchy comes from ink opacity, not colour. The seven-segment readout
 * and the segmented meter track are the two load-bearing shapes — hence the
 * family name.
 *
 * Contract instance only. Declares no renderer, imports no runtime, store,
 * transport or radio code. Renderers are MOR-2149's job: the manifest that
 * registers this token set (`../declarations.ts`) ships `renderers: {}`
 * until then, which `resolveRenderer` (`../contract.ts`) falls back on
 * safely — no renderer slot is filled yet.
 */
import type { DesignLanguageTokens } from '../contract';

/** The two grounds: the lit glass and the bezel it is set into. */
export const SEGMENTLINE_SURFACES = { glass: '#C8A030', bezel: '#1A1410' } as const;

/**
 * Ink ramp at HIGH contrast. DIM (sunlight/night preset) scales the same
 * ramp to 0.55 / 0.36 / 0.20 / 0.06 / 0.30 — a theme concern, not a language
 * one, so only the reference ramp is declared here.
 */
export const SEGMENTLINE_INK = {
  strong: 'rgba(26,16,0,1)',
  mid: 'rgba(26,16,0,0.65)',
  soft: 'rgba(26,16,0,0.34)',
  ghost: 'rgba(26,16,0,0.09)',
  telemetry: 'rgba(26,16,0,0.50)',
} as const;

export const SEGMENTLINE_PALETTE = {
  focus: 'rgba(26,16,0,1)',
  txHot: '#D61C08',
  txMark: '#7A1A0A',
  tuning: '#A97400',
} as const;

/** `var()` with the literal fallback, so a bundle mounted without the CSS still renders a defined colour. */
const tone = (name: string, fallback: string): string => `var(--dl-segmentline-${name}, ${fallback})`;

export const SEGMENTLINE_TOKENS: DesignLanguageTokens = {
  // A seven-segment face for values, a squared mono for labels. The readout
  // font is metric-critical: DSEG7's digit advance is fixed and its `.`
  // advances zero, so a plain text run changes width when the webfont is
  // missing. MOR-2149's frequency renderer is what lays each glyph in a
  // declared-width cell to guarantee against that — not declared here.
  typography: {
    fontFamily: '"DSEG7 Classic", "Share Tech Mono", ui-monospace, monospace',
    weight: 700,
    fontVariantNumeric: 'tabular-nums',
  },
  // Hairline outlined cells, barely rounded — a printed LCD segment box, not
  // a UI button. 2px is the chassis bezel; cells use 1.25px.
  geometry: { radius: '2px', borderWidth: '1.25px' },
  // Segmented track: 7px bars with a 3px gap is what reads as an LCD bar
  // graph rather than the continuous rail `studioline` owns.
  meters: { trackWidth: '7px', segmentGap: '3px' },
  frequency: { digitWeight: 700, rankedGroups: true },
  // The glass does not animate. Value changes step; only the TX perimeter
  // fades, and it is dropped entirely under prefers-reduced-motion.
  motion: { durationMs: 90, reducedMotionSafe: true },
  focusRing: `2px solid ${tone('focus', SEGMENTLINE_PALETTE.focus)}`,
  rx: {
    idle: tone('rx-idle', SEGMENTLINE_INK.soft),
    active: tone('rx-active', SEGMENTLINE_INK.strong),
    tuning: tone('rx-tuning', SEGMENTLINE_PALETTE.tuning),
  },
  tx: {
    idle: tone('tx-idle', SEGMENTLINE_INK.soft),
    active: tone('tx-active', SEGMENTLINE_PALETTE.txHot),
    tuning: tone('tx-tuning', SEGMENTLINE_PALETTE.tuning),
  },
};
