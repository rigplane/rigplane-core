/**
 * MOR-1073 — the `studioline` VFO renderer slice, over the four canonical
 * topology fixtures (MOR-1062 `semantic/fixtures/topologies`).
 *
 * The renderer is a pure projection of ONE frequency fact onto MOR-977 §2.3's
 * grammar: two-tier group ranking (MHz/kHz hero, Hz half-size and muted),
 * thin-space separators, leading MHz zeros shifted off exactly as
 * `splitFrequencyToDigits()` shifts them, and an underline as the tuning
 * affordance. It is driven through `invokeRenderer` in at least one test on
 * purpose — the structural gate is what makes a capability fork impossible,
 * so the language's own renderer must be shown to survive it rather than
 * being exercised only by direct call.
 */
import { describe, it, expect } from 'vitest';
import { invokeRenderer, RendererInputError } from '../../contract';
import { topologyFixtures } from '../../../../semantic/fixtures/topologies';
import { STUDIOLINE_TOKENS } from '../tokens';
import {
  HZ_GROUP_SIZE_PX, HERO_SIZE_PX, THIN_SPACE, renderFrequency, type StudiolineFrequency,
} from '../frequency-renderer';

const render = (
  fields: Record<string, string | number | boolean | null>,
): StudiolineFrequency =>
  renderFrequency({ kind: 'frequency', fields }, STUDIOLINE_TOKENS);

const groupText = (r: StudiolineFrequency, group: 'mhz' | 'khz' | 'hz'): string =>
  r.groups.find((g) => g.group === group)!.text;

/** Every VFO the four topology fixtures declare — the real acceptance surface. */
const topologyVfos = Object.entries(topologyFixtures).flatMap(([id, model]) =>
  model.vfos.map((vfo) => [id, vfo.label, vfo.frequencyHz] as const));

describe('four topology fixtures render under the studioline grammar', () => {
  it.each(topologyVfos)('%s / %s renders three ranked groups', (_topology, _label, hz) => {
    const r = render({ frequencyHz: hz });
    expect(r.groups.map((g) => g.group)).toEqual(['mhz', 'khz', 'hz']);
    expect(r.unknown).toBe(false);
    // Two-tier read: kHz stays hero-sized, Hz is the only demoted group.
    expect(r.groups.filter((g) => g.rank === 'hero').map((g) => g.group)).toEqual(['mhz', 'khz']);
    expect(groupText(r, 'khz')).toHaveLength(3);
    expect(groupText(r, 'hz')).toHaveLength(3);
  });

  it.each(topologyVfos)('%s / %s keeps the digits and the value in agreement', (_topology, _label, hz) => {
    const r = render({ frequencyHz: hz });
    expect(Number(r.groups.map((g) => g.text).join(''))).toBe(hz);
  });

  it('renders each fixture at its declared active frequency', () => {
    expect(groupText(render({ frequencyHz: topologyFixtures['1/single'].vfos[0].frequencyHz }), 'mhz')).toBe('14');
    expect(groupText(render({ frequencyHz: topologyFixtures['1/ab'].vfos[0].frequencyHz }), 'mhz')).toBe('7');
    expect(groupText(render({ frequencyHz: topologyFixtures['2/ab_shared'].vfos[0].frequencyHz }), 'mhz')).toBe('3');
    expect(groupText(render({ frequencyHz: topologyFixtures['2/main_sub'].vfos[0].frequencyHz }), 'mhz')).toBe('14');
  });
});

describe('group ranking is the hierarchy device (MOR-977 §2.3)', () => {
  it('demotes the Hz group in BOTH size and tone, and nothing else', () => {
    const r = render({ frequencyHz: 14_250_000 });
    const hz = r.groups.find((g) => g.group === 'hz')!;
    expect(hz).toMatchObject({ fontSizePx: HZ_GROUP_SIZE_PX, rank: 'ranked', tone: 'muted' });
    for (const g of r.groups.filter((g) => g.group !== 'hz')) {
      expect(g).toMatchObject({ fontSizePx: HERO_SIZE_PX, rank: 'hero', tone: 'primary' });
    }
  });

  it('demotion is roughly a half-size step, so the two tiers are unmistakable', () => {
    expect(HZ_GROUP_SIZE_PX * 2).toBe(HERO_SIZE_PX);
  });

  it('the space IS the grouping mark — no dot separators anywhere', () => {
    const r = render({ frequencyHz: 14_250_000 });
    expect(r.separator).toBe(THIN_SPACE);
    expect(r.text).toBe(`14${THIN_SPACE}250${THIN_SPACE}000`);
    expect(r.text).not.toMatch(/[.,]/);
  });

  it('carries the numeral typography the token set declares, not its own', () => {
    const r = render({ frequencyHz: 14_250_000 });
    expect(r.style.fontWeight).toBe(String(STUDIOLINE_TOKENS.frequency.digitWeight));
    expect(r.style.fontVariantNumeric).toBe('tabular-nums');
    expect(r.style.fontFamily).toBe(STUDIOLINE_TOKENS.typography.fontFamily);
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
});

describe('an unobserved frequency stays unobserved', () => {
  it('renders a null frequency as an explicit unknown, never as 0 Hz', () => {
    const r = render({ frequencyHz: null });
    expect(r.unknown).toBe(true);
    expect(r.groups).toEqual([]);
    expect(r.text).toBe('—');
    expect(r.text).not.toMatch(/0/);
  });

  it('renders a missing frequency field the same way — absence is not zero', () => {
    expect(render({}).unknown).toBe(true);
  });
});

describe('tuning affordance', () => {
  it('underlines the selected digit by multiplier, without changing its weight', () => {
    const r = render({ frequencyHz: 14_250_000, tuningMultiplier: 1000 });
    expect(r.underline).toEqual({ multiplier: 1000, group: 'khz', indexInGroup: 2, thicknessPx: 2 });
    // A weight change at 200 would reflow the whole readout (MOR-977 §2.3).
    expect(r.groups.every((g) => g.fontWeight === STUDIOLINE_TOKENS.frequency.digitWeight)).toBe(true);
  });

  it('places the underline in the MHz group after the leading-zero shift', () => {
    expect(render({ frequencyHz: 7_100_000, tuningMultiplier: 1_000_000 })!.underline)
      .toEqual({ multiplier: 1_000_000, group: 'mhz', indexInGroup: 0, thicknessPx: 2 });
  });

  it('has no underline when nothing is being tuned', () => {
    expect(render({ frequencyHz: 14_250_000 }).underline).toBeNull();
  });

  it('drops an underline whose multiplier is not on the readout', () => {
    expect(render({ frequencyHz: 14_250_000, tuningMultiplier: 5 }).underline).toBeNull();
  });
});

describe('the renderer survives the MOR-1072 structural gate', () => {
  it('renders through invokeRenderer', () => {
    const viewModel = { kind: 'frequency', fields: { frequencyHz: 14_250_000 } };
    expect(invokeRenderer(renderFrequency, viewModel, STUDIOLINE_TOKENS)).toMatchObject({ unknown: false });
  });

  it('cannot be reached with a capability-shaped payload', () => {
    const smuggled = { kind: 'frequency', fields: { frequencyHz: 14_250_000 }, capabilities: { modes: ['USB'] } };
    expect(() => invokeRenderer(renderFrequency, smuggled, STUDIOLINE_TOKENS)).toThrow(RendererInputError);
  });

  it('ignores any field it does not name — an extra field cannot alter the render', () => {
    const plain = render({ frequencyHz: 14_250_000 });
    const noisy = render({ frequencyHz: 14_250_000, radioModel: 'test-radio', ptt: true });
    expect(noisy).toEqual(plain);
  });
});
