import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { flattenBands, findActiveBand } from '../band-utils';
import type { FreqRange } from '$lib/types/capabilities';

// ── Fixtures (must be declared before vi.mock factories) ──────────────────

const HF_RANGES: FreqRange[] = [
  {
    start: 1_800_000,
    end: 30_000_000,
    label: 'HF',
    bands: [
      { name: '160m', start: 1_800_000,  end: 2_000_000,  default: 1_900_000 },
      { name: '80m',  start: 3_500_000,  end: 4_000_000,  default: 3_700_000 },
      { name: '40m',  start: 7_000_000,  end: 7_300_000,  default: 7_100_000 },
      { name: '20m',  start: 14_000_000, end: 14_350_000, default: 14_225_000, bsrCode: 5 },
      { name: '15m',  start: 21_000_000, end: 21_450_000, default: 21_200_000 },
      { name: '10m',  start: 28_000_000, end: 29_700_000, default: 28_500_000 },
    ],
  },
  {
    start: 50_000_000,
    end: 54_000_000,
    label: 'VHF',
    bands: [
      { name: '6m', start: 50_000_000, end: 54_000_000, default: 50_125_000 },
    ],
  },
];

const mockProps = {
  currentFreq: 14_074_000,
};

const mockBandHandlers = {
  onBandSelect: vi.fn(),
};

const mockPresetHandlers = {
  onPresetSelect: vi.fn(),
  onFreqPreset: vi.fn(),
};

vi.mock('$lib/stores/capabilities.svelte', () => ({
  getCapabilities: vi.fn(() => ({ freqRanges: HF_RANGES })),
  getKeyboardConfig: vi.fn(() => null),
}));

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  deriveBandSelectorProps: () => mockProps,
  getBandHandlers: () => mockBandHandlers,
  getPresetHandlers: () => mockPresetHandlers,
}));

import BandSelector from '../BandSelector.svelte';

let components: ReturnType<typeof mount>[] = [];

function mountPanel(overrides?: Partial<typeof mockProps>, props?: { hamBands?: boolean }) {
  if (overrides) Object.assign(mockProps, overrides);
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(BandSelector, { target, props: props ?? {} });
  flushSync();
  components.push(component);
  return target;
}

beforeEach(() => {
  components = [];
  mockProps.currentFreq = 14_074_000;
  mockBandHandlers.onBandSelect = vi.fn();
  mockPresetHandlers.onPresetSelect = vi.fn();
  mockPresetHandlers.onFreqPreset = vi.fn();
});

afterEach(() => {
  components.forEach((component) => unmount(component));
  document.body.innerHTML = '';
});

const EMPTY_RANGES: FreqRange[] = [];

const NO_BANDS_RANGE: FreqRange[] = [
  { start: 1_000_000, end: 30_000_000, label: 'HF' },
];

// ── flattenBands ───────────────────────────────────────────────────────────

describe('flattenBands', () => {
  it('returns flat array of all bands across all ranges', () => {
    const result = flattenBands(HF_RANGES);
    expect(result).toHaveLength(7);
  });

  it('preserves band names in order', () => {
    const names = flattenBands(HF_RANGES).map(b => b.name);
    expect(names).toEqual(['160m', '80m', '40m', '20m', '15m', '10m', '6m']);
  });

  it('maps defaultFreq from band.default', () => {
    const result = flattenBands(HF_RANGES);
    expect(result[0].defaultFreq).toBe(1_900_000);
    expect(result[3].defaultFreq).toBe(14_225_000);
  });

  it('maps start and end correctly', () => {
    const result = flattenBands(HF_RANGES);
    expect(result[2]).toMatchObject({ name: '40m', start: 7_000_000, end: 7_300_000 });
  });

  it('preserves optional bsrCode metadata', () => {
    const result = flattenBands(HF_RANGES);
    expect(result[3]).toMatchObject({ name: '20m', bsrCode: 5 });
  });

  it('returns empty array for empty freqRanges', () => {
    expect(flattenBands(EMPTY_RANGES)).toEqual([]);
  });

  it('returns empty array when ranges have no bands field', () => {
    expect(flattenBands(NO_BANDS_RANGE)).toEqual([]);
  });

  it('handles ranges with empty bands array', () => {
    const ranges: FreqRange[] = [{ start: 1_000_000, end: 2_000_000, label: 'X', bands: [] }];
    expect(flattenBands(ranges)).toEqual([]);
  });
});

// ── findActiveBand ─────────────────────────────────────────────────────────

describe('findActiveBand', () => {
  it('returns band name when freq is within band', () => {
    expect(findActiveBand(14_074_000, HF_RANGES)).toBe('20m');
  });

  it('matches freq at lower edge (inclusive)', () => {
    expect(findActiveBand(7_000_000, HF_RANGES)).toBe('40m');
  });

  it('matches freq at upper edge (inclusive)', () => {
    expect(findActiveBand(7_300_000, HF_RANGES)).toBe('40m');
  });

  it('returns null for freq between bands (inter-band gap)', () => {
    // gap between 80m (ends 4_000_000) and 40m (starts 7_000_000)
    expect(findActiveBand(5_000_000, HF_RANGES)).toBeNull();
  });

  it('returns null for freq below all bands', () => {
    expect(findActiveBand(100_000, HF_RANGES)).toBeNull();
  });

  it('returns null for freq above all bands', () => {
    expect(findActiveBand(200_000_000, HF_RANGES)).toBeNull();
  });

  it('returns null for empty freqRanges', () => {
    expect(findActiveBand(14_000_000, EMPTY_RANGES)).toBeNull();
  });

  it('returns null when ranges have no bands', () => {
    expect(findActiveBand(14_000_000, NO_BANDS_RANGE)).toBeNull();
  });

  it('matches 6m in second range', () => {
    expect(findActiveBand(50_125_000, HF_RANGES)).toBe('6m');
  });

  it('returns null for freq just below 20m start', () => {
    expect(findActiveBand(13_999_999, HF_RANGES)).toBeNull();
  });

  it('returns null for freq just above 20m end', () => {
    expect(findActiveBand(14_350_001, HF_RANGES)).toBeNull();
  });

  it('matches 160m at its default frequency', () => {
    expect(findActiveBand(1_900_000, HF_RANGES)).toBe('160m');
  });

  it('matches 10m at its default frequency', () => {
    expect(findActiveBand(28_500_000, HF_RANGES)).toBe('10m');
  });

  it('freq=0 returns null', () => {
    expect(findActiveBand(0, HF_RANGES)).toBeNull();
  });

  it('uses first matching band when ranges overlap (priority to earlier range)', () => {
    const overlapping: FreqRange[] = [
      {
        start: 1_000_000,
        end: 20_000_000,
        label: 'A',
        bands: [{ name: 'bandA', start: 7_000_000, end: 8_000_000, default: 7_500_000 }],
      },
      {
        start: 1_000_000,
        end: 20_000_000,
        label: 'B',
        bands: [{ name: 'bandB', start: 7_000_000, end: 8_000_000, default: 7_500_000 }],
      },
    ];
    expect(findActiveBand(7_500_000, overlapping)).toBe('bandA');
  });
});

describe('BandSelector component', () => {
  function findButtonByText(container: HTMLElement, text: string): HTMLButtonElement | null {
    const buttons = container.querySelectorAll<HTMLButtonElement>('button');
    for (const btn of buttons) {
      if (btn.textContent?.trim() === text) return btn;
    }
    return null;
  }

  it('forwards bsrCode when a band is selected', () => {
    const target = mountPanel();

    const button = findButtonByText(target, '20m');
    button?.click();
    flushSync();

    expect(mockBandHandlers.onBandSelect).toHaveBeenCalledWith('20m', 14_225_000, 5);
  });
});

/**
 * MOR-1367 (v3-rework S8) — the HAM/broadcast split, at the component level.
 *
 * `docs/plans/2026-08-06-settings-modal-boundary.md` §4a: this component is two
 * independently-ruled halves. The HAM tab and grid duplicate `BandSurface` and
 * retire under `band` zone ownership; the LW/MW + SWL broadcast presets are
 * excluded from the vocabulary BY NAME (`semantic/radio-view-model.ts:494-496`)
 * and this component is their only production consumer, so they are permanent
 * on every manifest. Hence a prop, and hosts that never unmount the component.
 *
 * The composed-tree half of this contract (both hosts, the real desktop-v2
 * manifest) lives in
 * `components-v2/layout/__tests__/semantic-desktop-migration.component.test.ts`.
 */
describe('BandSelector HAM/broadcast split (hamBands prop)', () => {
  const tabs = (t: HTMLElement) =>
    [...t.querySelectorAll('.band-tab')].map((el) => el.textContent?.trim());
  const grid = (t: HTMLElement) =>
    [...t.querySelectorAll('.grid button')].map((el) => el.textContent?.trim());

  // Kills: making the prop default to `false`, or removing the default. Every
  // caller that predates the split (mobile, LCD, ControlButtonDemo) passes
  // nothing and must keep the pre-split three-tab component.
  it('defaults to the full three-tab component when the prop is omitted', () => {
    const t = mountPanel();
    expect(tabs(t)).toEqual(['HAM', 'LW/MW', 'SWL']);
    expect(grid(t)).toEqual(['160m', '80m', '40m', '20m', '15m', '10m', '6m']);
  });

  // Kills: gating only the grid and leaving a dead HAM tab, or vice versa.
  it('drops the HAM tab AND the HAM grid when hamBands is false', () => {
    const t = mountPanel(undefined, { hamBands: false });
    expect(tabs(t)).toEqual(['LW/MW', 'SWL']);
    expect(grid(t)).not.toContain('20m');
  });

  // Kills: gating the tab/grid but leaving `bandMode`'s `'ham'` initial value
  // (S10 §4a explicitly requires the DEFAULT to be gated too). Without this the
  // suppressed component opens on an empty grid with no control able to leave
  // that state.
  it('defaults bandMode to LW/MW — never to an unreachable empty HAM grid', () => {
    const t = mountPanel(undefined, { hamBands: false });
    expect(grid(t)).toEqual(['LW', 'MW', '120m', '90m', '75m', '60m']);
    expect(t.querySelector('.band-tab.active')?.textContent?.trim()).toBe('LW/MW');
  });

  // Kills: taking the broadcast half with the HAM half — the operator-affordance
  // loss §4a exists to prevent. Sixteen presets, counted: `broadcast-presets.ts`
  // ships 6 LW/MW + 10 SW. (The S10 doc says "17"; it counted the
  // `BroadcastPreset` interface's own `name:` field. Measured, not copied.)
  it('keeps every broadcast preset reachable with the HAM half suppressed', () => {
    const t = mountPanel(undefined, { hamBands: false });
    const lwmw = grid(t);
    (t.querySelectorAll('.band-tab')[1] as HTMLElement).click();
    flushSync();
    const swl = grid(t);
    expect(lwmw).toEqual(['LW', 'MW', '120m', '90m', '75m', '60m']);
    expect(swl).toEqual(['49m', '41m', '31m', '25m', '22m', '19m', '16m', '15m', '13m', '11m']);
    expect(lwmw.length + swl.length).toBe(16);
  });

  // Kills: suppressing the preset intent along with the tab. The presets fire
  // `onPresetSelect(freq, mode)` — a frequency+mode intent, NOT a band
  // selection — and that path must survive the split untouched.
  it('still forwards a broadcast preset intent with the HAM half suppressed', () => {
    const t = mountPanel(undefined, { hamBands: false });
    ([...t.querySelectorAll('.grid button')]
      .find((el) => el.textContent?.trim() === 'MW') as HTMLElement).click();
    flushSync();
    expect(mockPresetHandlers.onPresetSelect).toHaveBeenCalledWith(1_000_000, 'AM');
  });
});
