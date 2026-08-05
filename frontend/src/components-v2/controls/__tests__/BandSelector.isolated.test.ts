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

function mountPanel(overrides?: Partial<typeof mockProps>) {
  if (overrides) Object.assign(mockProps, overrides);
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(BandSelector, { target });
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
