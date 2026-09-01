/**
 * `segmentline` frequency renderer (MOR-2149, VFO slice) — the seven-segment
 * readout.
 *
 * THREE OUTPUT CHANNELS, ONE DESCRIPTOR (`semantic/design-language-
 * renderers.ts`'s `renderSlot`/`annotate`): `annotate` walks every top-level
 * key EXCEPT `text` (its `if (key === 'text') continue;`) and turns each
 * remaining PRIMITIVE into a `data-dl-<kebab>` attribute; nested objects and
 * arrays are skipped entirely. So this descriptor is written on three levels
 * at once —
 *   - flat primitives that reach the DOM as `data-dl-*` (`kind`,
 *     `digitCellEm`, `dotCellEm`, `heroSizePx`, `hzSizePx`, `widthPx`,
 *     `unknown`);
 *   - `text` is excluded from that walk on purpose and reaches only
 *     `renderSlot`'s SEPARATE `.text` return channel, never a `data-dl-*`
 *     attribute. `VfoSurface.svelte`'s own comment records that no
 *     production caller reads it today (MOR-1482): a ranked grammar
 *     flattened to a tile-sized string reads worse than the plain fallback
 *     it would replace;
 *   - `groups` (with its nested `cells`) and `style` are private structure
 *     `annotate` skips because they are objects, not primitives — the same
 *     role studioline's `groups` plays, for a future hero-scale mount that
 *     can render ranked grouping (and the numeral typography) directly.
 *
 * WHY EVERY GLYPH GETS ITS OWN CELL. Measured against the bundled font
 * (`static/fonts/DSEG7Classic-Bold.woff2`, via fontTools' `hmtx` table):
 * every one of DSEG7 Classic Bold's ten digits advances 816/1000 em = 0.816em,
 * and `.` advances exactly 0 — the glyph overprints whatever precedes it. A
 * fallback face has neither property, so a plain text run would silently
 * change width if the webfont failed to load. DSEG7 IS bundled in this repo
 * (`static/fonts/`, `src/components-v2/theme/fonts/`), so that reflow is the
 * documented REASON for the per-glyph-cell design here, not a live risk this
 * file has to defend against. `DOT_CELL_EM` is not a font metric (the glyph
 * itself advances zero) — it is the width segmentline's own grammar reserves
 * for the separator's cell, so total advance stays a function of font-size
 * alone.
 *
 * Digit semantics match `splitFrequencyToDigits()` and the other two
 * languages: nine digits, leading MHz zeros shifted off but never below one
 * digit, grouped 10^8..10^6 / 10^5..10^3 / 10^2..10^0.
 *
 * INPUT. The renderer's one production call site is `semantic/VfoSurface.
 * svelte`'s `frequencyDisplay()`, which calls exactly
 * `renderSlot('frequencyDisplay', { frequencyHz: vfo.frequencyHz })` — one
 * named field. `presentation/languages/projection.ts`'s `vfo0FrequencyHz` is
 * a real field NAME (it appears in that module's own output shape and its
 * tests), but `projectRadioViewModel` has no production call site at all —
 * it is called only from its own test file — so no caller ever hands this
 * renderer a `vfo0FrequencyHz` field. Reading it here would be a fallback
 * parameter nobody passes.
 */
import type { DesignLanguageTokens, RendererViewModel } from '../contract';

/** DSEG7 Classic Bold digit advance, in em — measured against
 *  `static/fonts/DSEG7Classic-Bold.woff2` (fontTools `hmtx`: every digit
 *  glyph is 816 units wide at 1000 unitsPerEm). */
export const DIGIT_CELL_EM = 0.816;
/** The separator's own reserved cell width. Not a font metric — DSEG7's `.`
 *  itself advances zero; this is how much horizontal space the family's own
 *  grammar sets aside for the cell instead. */
export const DOT_CELL_EM = 0.29;
export const HERO_SIZE_PX = 56;
/** Hz group at 0.62 of the hero step: subordinate, still legible at arm's length. */
export const HZ_GROUP_RATIO = 0.62;
export const SEPARATOR = '.';
const UNKNOWN_TEXT = '-------';
const GROUP_NAMES = ['mhz', 'khz', 'hz'] as const;

export type FrequencyGroupName = (typeof GROUP_NAMES)[number];

export interface SegmentlineCell {
  readonly char: string;
  readonly widthEm: number;
  readonly isSeparator: boolean;
}

export interface SegmentlineFrequencyGroup {
  readonly group: FrequencyGroupName;
  readonly text: string;
  readonly cells: readonly SegmentlineCell[];
  readonly fontSizePx: number;
  readonly rank: 'hero' | 'ranked';
  readonly tone: 'primary' | 'muted';
}

export interface SegmentlineFrequency {
  // ── flat: these become data-dl-* annotations (annotate() excludes only `text`) ──
  readonly kind: 'segmentline-frequency';
  readonly unknown: boolean;
  readonly digitCellEm: number;
  readonly dotCellEm: number;
  readonly heroSizePx: number;
  readonly hzSizePx: number;
  /** Total advance in px — deterministic and font-independent. */
  readonly widthPx: number;
  // ── `.text` channel only: excluded from data-dl-* by annotate()'s own `key === 'text'` check ──
  readonly text: string;
  // ── private structure: skipped by the annotator because these are objects, not primitives ──
  readonly style: Readonly<Record<string, string>>;
  readonly groups: readonly SegmentlineFrequencyGroup[];
}

const finite = (fields: RendererViewModel['fields'], key: string): number | null => {
  const value = fields[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
};

const mhzShift = (digits: string): number =>
  Math.min(3 - digits.slice(0, 3).replace(/^0+/, '').length, 2);

const toCells = (text: string): readonly SegmentlineCell[] =>
  text.split('').map((char) => ({
    char,
    widthEm: char === SEPARATOR ? DOT_CELL_EM : DIGIT_CELL_EM,
    isSeparator: char === SEPARATOR,
  }));

export function renderFrequency(
  viewModel: RendererViewModel, tokens: DesignLanguageTokens,
): SegmentlineFrequency {
  const heroSizePx = HERO_SIZE_PX;
  const hzSizePx = Math.round(heroSizePx * HZ_GROUP_RATIO);
  const style = {
    fontFamily: tokens.typography.fontFamily,
    fontWeight: String(tokens.frequency.digitWeight),
    fontVariantNumeric: tokens.typography.fontVariantNumeric,
    letterSpacing: '0',
  };
  const base = { kind: 'segmentline-frequency' as const, digitCellEm: DIGIT_CELL_EM, dotCellEm: DOT_CELL_EM, heroSizePx, hzSizePx, style };

  const hz = finite(viewModel.fields, 'frequencyHz');
  if (hz === null) {
    return { ...base, text: UNKNOWN_TEXT, unknown: true, widthPx: 0, groups: [] };
  }

  const digits = String(Math.max(0, Math.floor(hz))).padStart(9, '0');
  const shift = mhzShift(digits);
  const groups = GROUP_NAMES.map((group, index): SegmentlineFrequencyGroup => {
    const start = index * 3;
    const ranked = group === 'hz';
    // The separator rides with the group it FOLLOWS, so mhz and khz carry
    // their own trailing dot and hz carries none.
    const body = digits.slice(group === 'mhz' ? shift : start, start + 3);
    const text = ranked ? body : `${body}${SEPARATOR}`;
    return {
      group,
      text,
      cells: toCells(text),
      fontSizePx: ranked ? hzSizePx : heroSizePx,
      rank: ranked ? 'ranked' : 'hero',
      tone: ranked ? 'muted' : 'primary',
    };
  });

  const widthPx = groups.reduce(
    (sum, g) => sum + g.cells.reduce((s, c) => s + c.widthEm * g.fontSizePx, 0), 0,
  );

  return {
    ...base,
    text: groups.map((g) => g.text).join(''),
    unknown: false,
    widthPx,
    groups,
  };
}
