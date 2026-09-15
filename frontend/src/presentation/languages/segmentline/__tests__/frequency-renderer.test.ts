/**
 * MOR-2149 — the `segmentline` VFO renderer slice, over the four canonical
 * topology fixtures (MOR-1062 `semantic/fixtures/topologies`), mirroring
 * `studioline/__tests__/frequency-renderer.test.ts`'s shape.
 *
 * The renderer is a pure projection of ONE frequency fact onto the
 * seven-segment grammar: per-glyph cells of a declared em width (so the
 * total advance is a function of font-size alone, independent of whichever
 * font actually loads), a trailing-dot separator riding with the group it
 * follows, and the Hz group demoted in size only (never in digit weight).
 */
import { describe, it, expect } from 'vitest';
import { invokeRenderer, RendererInputError } from '../../contract';
import type { DesignLanguageTokens } from '../../contract';
import { topologyFixtures } from '../../../../semantic/fixtures/topologies';
import { SEGMENTLINE_TOKENS } from '../tokens';
import {
  DIGIT_CELL_EM, DOT_CELL_EM, HERO_SIZE_PX, renderFrequency, type SegmentlineFrequency,
} from '../frequency-renderer';

const render = (
  fields: Record<string, string | number | boolean | null>,
): SegmentlineFrequency => renderFrequency({ kind: 'frequency', fields }, SEGMENTLINE_TOKENS);

const groupText = (r: SegmentlineFrequency, group: 'mhz' | 'khz' | 'hz'): string =>
  r.groups.find((g) => g.group === group)!.text;

/** Every VFO the four topology fixtures declare — the real acceptance surface. */
const topologyVfos = Object.entries(topologyFixtures).flatMap(([id, model]) =>
  model.vfos.map((vfo) => [id, vfo.label, vfo.frequencyHz] as const));

describe('four topology fixtures render under the segmentline grammar', () => {
  it.each(topologyVfos)('%s / %s renders three groups', (_topology, _label, hz) => {
    const r = render({ frequencyHz: hz });
    expect(r.groups.map((g) => g.group)).toEqual(['mhz', 'khz', 'hz']);
    expect(r.unknown).toBe(false);
  });

  it.each(topologyVfos)('%s / %s keeps the digits and the value in agreement', (_topology, _label, hz) => {
    // Cells strip the dot separators, so the digit characters alone reconstruct the value.
    const r = render({ frequencyHz: hz });
    const digitsOnly = r.groups.flatMap((g) => g.cells.filter((c) => !c.isSeparator).map((c) => c.char)).join('');
    expect(Number(digitsOnly)).toBe(hz);
  });

  it('renders each fixture at its declared active frequency', () => {
    expect(groupText(render({ frequencyHz: topologyFixtures['1/single'].vfos[0].frequencyHz }), 'mhz')).toBe('14.');
    expect(groupText(render({ frequencyHz: topologyFixtures['1/ab'].vfos[0].frequencyHz }), 'mhz')).toBe('7.');
    expect(groupText(render({ frequencyHz: topologyFixtures['2/ab_shared'].vfos[0].frequencyHz }), 'mhz')).toBe('3.');
    expect(groupText(render({ frequencyHz: topologyFixtures['2/main_sub'].vfos[0].frequencyHz }), 'mhz')).toBe('14.');
  });
});

describe('the Hz group is demoted in SIZE only, never in digit weight', () => {
  it('sizes the Hz group at hzSizePx/ranked/muted, everything else at heroSizePx/hero/primary', () => {
    const r = render({ frequencyHz: 14_250_000 });
    const hz = r.groups.find((g) => g.group === 'hz')!;
    expect(hz).toMatchObject({ fontSizePx: r.hzSizePx, rank: 'ranked', tone: 'muted' });
    for (const g of r.groups.filter((g) => g.group !== 'hz')) {
      expect(g).toMatchObject({ fontSizePx: HERO_SIZE_PX, rank: 'hero', tone: 'primary' });
    }
  });

  it('every cell in every group carries the SAME digit weight — nothing per-cell can quietly demote one', () => {
    const r = render({ frequencyHz: 14_250_000 });
    for (const g of r.groups) {
      for (const c of g.cells) expect(Object.keys(c).sort()).toEqual(['char', 'isSeparator', 'widthEm']);
    }
  });
});

describe('the dot rides with the group it follows — mhz and khz carry it, hz carries none', () => {
  it('marks exactly the trailing dot of the mhz and khz groups as the separator cell', () => {
    const r = render({ frequencyHz: 14_250_000 });
    const mhz = r.groups.find((g) => g.group === 'mhz')!;
    const khz = r.groups.find((g) => g.group === 'khz')!;
    const hzGroup = r.groups.find((g) => g.group === 'hz')!;
    expect(mhz.cells.at(-1)).toMatchObject({ char: '.', isSeparator: true, widthEm: DOT_CELL_EM });
    expect(khz.cells.at(-1)).toMatchObject({ char: '.', isSeparator: true, widthEm: DOT_CELL_EM });
    expect(hzGroup.cells.some((c) => c.isSeparator)).toBe(false);
  });

  it('every non-separator cell is a digit at the full DIGIT_CELL_EM width', () => {
    const r = render({ frequencyHz: 14_250_000 });
    for (const g of r.groups) {
      for (const c of g.cells.filter((c) => !c.isSeparator)) {
        expect(c.widthEm).toBe(DIGIT_CELL_EM);
        expect(c.char).toMatch(/^\d$/);
      }
    }
  });

  it('joins into a dot-separated run: "14.250.000"', () => {
    expect(render({ frequencyHz: 14_250_000 }).text).toBe('14.250.000');
  });
});

describe('leading MHz zeros shift off, matching splitFrequencyToDigits()', () => {
  it.each([
    [14_250_000, '14.'],
    [7_100_000, '7.'],
    [3_573_000, '3.'],
    [144_300_000, '144.'],
    [500_000, '0.'], // sub-MHz: the group never empties — one digit always remains
  ])('%i MHz group renders as "%s"', (hz, expected) => {
    expect(groupText(render({ frequencyHz: hz }), 'mhz')).toBe(expected);
  });

  it('does NOT strip zeros from the kHz or Hz groups — only the MHz group shifts', () => {
    const r = render({ frequencyHz: 14_000_000 });
    expect(groupText(r, 'khz')).toBe('000.');
    expect(groupText(r, 'hz')).toBe('000');
  });
});

describe('an unobserved frequency stays unobserved', () => {
  it('renders a null frequency as an explicit unknown, never as 0 Hz', () => {
    const r = render({ frequencyHz: null });
    expect(r.unknown).toBe(true);
    expect(r.groups).toEqual([]);
    expect(r.widthPx).toBe(0);
    expect(r.text).not.toMatch(/0/);
  });

  it('renders a missing frequency field the same way — absence is not zero', () => {
    expect(render({}).unknown).toBe(true);
  });

  // The defect an earlier draft of this file carried: it read a
  // `vfo0FrequencyHz` fallback field that `VfoSurface.svelte` (the one real
  // caller) never supplies. If that fallback were still present, this input
  // would render a known frequency instead of unknown.
  it('does not read vfo0FrequencyHz — no real caller ever supplies it', () => {
    expect(render({ vfo0FrequencyHz: 14_250_000 }).unknown).toBe(true);
  });
});

describe('metric-critical width: total advance is a function of font-size alone', () => {
  // Independent hand computation, not the renderer's own formula: mhz "14." =
  // (0.816+0.816+0.29)*56, khz "250." = (0.816*3+0.29)*56, hz "000" =
  // (0.816*3)*Math.round(56*0.62). A one-unit change to DIGIT_CELL_EM,
  // DOT_CELL_EM, HERO_SIZE_PX or HZ_GROUP_RATIO moves this total detectably.
  it('pins the exact total width for 14.25 MHz', () => {
    expect(render({ frequencyHz: 14_250_000 }).widthPx).toBeCloseTo(346.64, 9);
  });

  it('pins the exported DSEG7 digit advance to the measured value (static/fonts/DSEG7Classic-Bold.woff2)', () => {
    expect(DIGIT_CELL_EM).toBe(0.816);
  });

  // The constant→field WIRING, not just the constants: `widthPx` alone
  // cannot prove digitCellEm/dotCellEm/heroSizePx individually reach the
  // descriptor rather than being computed inline and discarded.
  it('pins the constant-to-field wiring for digitCellEm, dotCellEm and heroSizePx', () => {
    const r = render({ frequencyHz: 14_250_000 });
    expect(r.digitCellEm).toBe(DIGIT_CELL_EM);
    expect(r.dotCellEm).toBe(DOT_CELL_EM);
    expect(r.heroSizePx).toBe(HERO_SIZE_PX);
  });
});

describe('style carries the numeral typography the token set declares, not its own', () => {
  it('pins fontFamily/fontWeight/fontVariantNumeric to SEGMENTLINE_TOKENS (mirrors studioline/fieldline\'s own pin)', () => {
    const r = render({ frequencyHz: 14_250_000 });
    expect(r.style.fontFamily).toBe(SEGMENTLINE_TOKENS.typography.fontFamily);
    expect(r.style.fontWeight).toBe(String(SEGMENTLINE_TOKENS.frequency.digitWeight));
    expect(r.style.fontVariantNumeric).toBe('tabular-nums');
  });

  // A token set that DIFFERS from SEGMENTLINE_TOKENS on exactly these two
  // fields — a literal `{ mutated: 'yes' }` (or any other value the renderer
  // did not actually read from `tokens`) cannot produce this output.
  it('changes when the token set changes — the read is load-bearing, not a private default', () => {
    const otherTokens: DesignLanguageTokens = {
      ...SEGMENTLINE_TOKENS,
      typography: { ...SEGMENTLINE_TOKENS.typography, fontFamily: 'Other Font, monospace' },
      frequency: { ...SEGMENTLINE_TOKENS.frequency, digitWeight: 321 },
    };
    const r = renderFrequency({ kind: 'frequency', fields: { frequencyHz: 14_250_000 } }, otherTokens);
    expect(r.style.fontFamily).toBe('Other Font, monospace');
    expect(r.style.fontWeight).toBe('321');
  });
});

describe('the renderer survives the MOR-1072 structural gate', () => {
  it('renders through invokeRenderer', () => {
    const viewModel = { kind: 'frequency', fields: { frequencyHz: 14_250_000 } };
    expect(invokeRenderer(renderFrequency, viewModel, SEGMENTLINE_TOKENS)).toMatchObject({ unknown: false });
  });

  it('cannot be reached with a capability-shaped payload', () => {
    const smuggled = { kind: 'frequency', fields: { frequencyHz: 14_250_000 }, capabilities: { modes: ['USB'] } };
    expect(() => invokeRenderer(renderFrequency, smuggled, SEGMENTLINE_TOKENS)).toThrow(RendererInputError);
  });

  it('ignores any field it does not name — an extra field cannot alter the render', () => {
    const plain = render({ frequencyHz: 14_250_000 });
    const noisy = render({ frequencyHz: 14_250_000, radioModel: 'test-radio', ptt: true });
    expect(noisy).toEqual(plain);
  });
});
