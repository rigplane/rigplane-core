/**
 * MOR-1074 — the `fieldline` VFO renderer slice, over the same four canonical
 * topology fixtures studioline's twin file uses (MOR-1062
 * `semantic/fixtures/topologies`). Running BOTH languages over one fixture set
 * is what makes "unchanged behavior, different language" checkable rather than
 * asserted: the digits agree, the presentation does not.
 *
 * The renderer is a pure projection of ONE frequency fact onto MOR-977 §2.2's
 * grammar: equal-weight slab digit cells, no group de-emphasis, gaps rather
 * than separator characters, leading MHz zeros shifted off exactly as
 * `splitFrequencyToDigits()` shifts them, and cell inversion as the tuning
 * affordance. It is driven through `invokeRenderer` on purpose — the structural
 * gate is what makes a capability fork impossible, so a second language must be
 * shown to survive it too.
 */
import { describe, it, expect } from 'vitest';
import { invokeRenderer, RendererInputError } from '../../contract';
import { topologyFixtures } from '../../../../semantic/fixtures/topologies';
import { renderFrequency as renderStudioline } from '../../studioline/frequency-renderer';
import { STUDIOLINE_TOKENS } from '../../studioline/tokens';
import { FIELDLINE_TOKENS } from '../tokens';
import {
  DIGIT_SIZE_PX, GROUP_GAP_PX, renderFrequency, type FieldlineFrequency,
} from '../frequency-renderer';

const render = (
  fields: Record<string, string | number | boolean | null>,
): FieldlineFrequency => renderFrequency({ kind: 'frequency', fields }, FIELDLINE_TOKENS);

const groupText = (r: FieldlineFrequency, group: 'mhz' | 'khz' | 'hz'): string =>
  r.digits.filter((d) => d.group === group).map((d) => d.char).join('');

/** Every VFO the four topology fixtures declare — the real acceptance surface. */
const topologyVfos = Object.entries(topologyFixtures).flatMap(([id, model]) =>
  model.vfos.map((vfo) => [id, vfo.label, vfo.frequencyHz] as const));

describe('four topology fixtures render under the fieldline grammar', () => {
  it.each(topologyVfos)('%s / %s renders equal-weight digit cells', (_topology, _label, hz) => {
    const r = render({ frequencyHz: hz });
    expect(r.unknown).toBe(false);
    expect(r.digits.length).toBeGreaterThanOrEqual(7);
    expect(groupText(r, 'khz')).toHaveLength(3);
    expect(groupText(r, 'hz')).toHaveLength(3);
    // The axis: nothing is demoted. One size, one weight, for every cell.
    expect(new Set(r.digits.map(() => `${r.fontSizePx}/${r.fontWeight}`)).size).toBe(1);
  });

  it.each(topologyVfos)('%s / %s keeps the digits and the value in agreement', (_topology, _label, hz) => {
    expect(Number(render({ frequencyHz: hz }).digits.map((d) => d.char).join(''))).toBe(hz);
  });

  it('renders each fixture at its declared active frequency', () => {
    expect(groupText(render({ frequencyHz: topologyFixtures['1/single'].vfos[0].frequencyHz }), 'mhz')).toBe('14');
    expect(groupText(render({ frequencyHz: topologyFixtures['1/ab'].vfos[0].frequencyHz }), 'mhz')).toBe('7');
    expect(groupText(render({ frequencyHz: topologyFixtures['2/ab_shared'].vfos[0].frequencyHz }), 'mhz')).toBe('3');
    expect(groupText(render({ frequencyHz: topologyFixtures['2/main_sub'].vfos[0].frequencyHz }), 'mhz')).toBe('14');
  });
});

describe('the digits are IDENTICAL to studioline; only the presentation differs', () => {
  // This is the acceptance core in one assertion pair: same facts, same digit
  // semantics, materially different grammar — with no runtime, adapter or
  // vocabulary change in between.
  it.each(topologyVfos)('%s / %s draws the same glyph run as studioline', (_topology, _label, hz) => {
    const mine = render({ frequencyHz: hz }).digits.map((d) => d.char).join('');
    const theirs = renderStudioline({ kind: 'frequency', fields: { frequencyHz: hz } }, STUDIOLINE_TOKENS)
      .groups.map((g) => g.text).join('');
    expect(mine).toBe(theirs);
  });

  it('ranks nothing, where studioline demotes the Hz group in size and tone', () => {
    const mine = render({ frequencyHz: 14_250_000 });
    const theirs = renderStudioline({ kind: 'frequency', fields: { frequencyHz: 14_250_000 } }, STUDIOLINE_TOKENS);
    expect(FIELDLINE_TOKENS.frequency.rankedGroups).toBe(false);
    expect(new Set(theirs.groups.map((g) => g.fontSizePx)).size).toBe(2);
    expect(mine.fontSizePx).toBe(DIGIT_SIZE_PX);
    // Size and weight are declared ONCE for the whole readout, so there is no
    // per-cell field a future edit could quietly demote a group with.
    for (const cell of mine.digits) {
      expect(Object.keys(cell).sort()).toEqual(['char', 'group', 'inverted', 'multiplier', 'startsGroup']);
    }
  });

  it('emits NO separator character at all — the 6px gap is the grouping mark', () => {
    const r = render({ frequencyHz: 14_250_000 });
    expect(r.separator).toBe('');
    expect(r.groupGapPx).toBe(GROUP_GAP_PX);
    expect(r.text).toBe('14250000');
    expect(r.text).not.toMatch(/[\s.,]/);
    // Three gap anchors: one per group, the first of which needs no gap drawn.
    expect(r.digits.filter((d) => d.startsGroup)).toHaveLength(3);
  });

  it('is left-aligned slab type: mono face, 700, tabular, tight tracking', () => {
    const r = render({ frequencyHz: 14_250_000 });
    expect(r.align).toBe('start');
    expect(r.style.fontFamily).toBe(FIELDLINE_TOKENS.typography.fontFamily);
    expect(r.style.fontFamily).toMatch(/mono/i);
    expect(r.style.fontWeight).toBe(String(FIELDLINE_TOKENS.frequency.digitWeight));
    expect(r.style.fontVariantNumeric).toBe('tabular-nums');
  });
});

describe('leading MHz zeros shift off, matching splitFrequencyToDigits()', () => {
  it.each([
    [14_250_000, '14'],
    [7_100_000, '7'],
    [3_573_000, '3'],
    [144_300_000, '144'],
    [500_000, '0'], // sub-MHz: the group never empties — one digit always remains
  ])('%i MHz group renders as "%s"', (hz, expected) => {
    expect(groupText(render({ frequencyHz: hz }), 'mhz')).toBe(expected);
  });

  it('does NOT strip zeros from the kHz or Hz groups — only the MHz group shifts', () => {
    const r = render({ frequencyHz: 14_000_000 });
    expect(groupText(r, 'khz')).toBe('000');
    expect(groupText(r, 'hz')).toBe('000');
  });

  it('shifts rather than ghosts: a dropped zero leaves no cell behind', () => {
    expect(render({ frequencyHz: 7_100_000 }).digits).toHaveLength(7);
    expect(render({ frequencyHz: 144_300_000 }).digits).toHaveLength(9);
  });
});

describe('an unobserved frequency stays unobserved', () => {
  it('renders a null frequency as an explicit unknown, never as 0 Hz', () => {
    const r = render({ frequencyHz: null });
    expect(r.unknown).toBe(true);
    expect(r.digits).toEqual([]);
    expect(r.text).toBe('—');
    expect(r.text).not.toMatch(/0/);
  });

  it('renders a missing frequency field the same way — absence is not zero', () => {
    expect(render({}).unknown).toBe(true);
  });
});

describe('tuning affordance: the cell inverts, it is not underlined', () => {
  it('inverts exactly the tuned cell, by multiplier', () => {
    const r = render({ frequencyHz: 14_250_000, tuningMultiplier: 1000 });
    const inverted = r.digits.filter((d) => d.inverted);
    expect(inverted).toHaveLength(1);
    expect(inverted[0]).toMatchObject({ multiplier: 1000, group: 'khz', char: '0' });
  });

  it('inverts a cell in the MHz group after the leading-zero shift', () => {
    const r = render({ frequencyHz: 7_100_000, tuningMultiplier: 1_000_000 });
    expect(r.digits.filter((d) => d.inverted)).toEqual([
      { char: '7', multiplier: 1_000_000, group: 'mhz', inverted: true, startsGroup: true },
    ]);
  });

  it('inverts nothing when nothing is being tuned', () => {
    expect(render({ frequencyHz: 14_250_000 }).digits.some((d) => d.inverted)).toBe(false);
  });

  it('drops an inversion whose multiplier is not on the readout', () => {
    expect(render({ frequencyHz: 14_250_000, tuningMultiplier: 5 }).digits.some((d) => d.inverted)).toBe(false);
    expect(render({ frequencyHz: 14_250_000, tuningMultiplier: 1_000_000_000 }).digits.some((d) => d.inverted))
      .toBe(false);
  });

  it('a shifted-off leading zero cannot be tuned — there is no cell to invert', () => {
    // 7 MHz: the 10^8 digit is shifted off, so asking for it inverts nothing.
    expect(render({ frequencyHz: 7_100_000, tuningMultiplier: 100_000_000 }).digits.some((d) => d.inverted))
      .toBe(false);
  });
});

describe('the renderer survives the MOR-1072 structural gate', () => {
  it('renders through invokeRenderer', () => {
    const viewModel = { kind: 'frequency', fields: { frequencyHz: 14_250_000 } };
    expect(invokeRenderer(renderFrequency, viewModel, FIELDLINE_TOKENS)).toMatchObject({ unknown: false });
  });

  it('cannot be reached with a capability-shaped payload', () => {
    const smuggled = { kind: 'frequency', fields: { frequencyHz: 14_250_000 }, capabilities: { modes: ['USB'] } };
    expect(() => invokeRenderer(renderFrequency, smuggled, FIELDLINE_TOKENS)).toThrow(RendererInputError);
  });

  it('ignores any field it does not name — an extra field cannot alter the render', () => {
    const plain = render({ frequencyHz: 14_250_000 });
    const noisy = render({ frequencyHz: 14_250_000, radioModel: 'test-radio', ptt: true });
    expect(noisy).toEqual(plain);
  });
});
