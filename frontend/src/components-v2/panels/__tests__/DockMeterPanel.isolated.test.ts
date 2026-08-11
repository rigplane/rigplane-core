import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ComponentProps } from 'svelte';
import DockMeterPanel from '../DockMeterPanel.svelte';

// Calibration/redline come from the runtime adapter; return null so the
// hardcoded IC-7610 knot fallbacks in meter-utils are exercised (matches the
// MetersDockPanel test setup). s_meter is the one exception (MOR-1451): it
// has no such fallback anymore, so the "calibrated" S-bar tests below need
// an explicit profile fixture instead.
const IC7610_LIKE_S_METER_CAL = [
  { raw: 0, actual: -54, label: 'S0' },
  { raw: 26, actual: -48, label: 'S1' },
  { raw: 52, actual: -36, label: 'S3' },
  { raw: 78, actual: -24, label: 'S5' },
  { raw: 103, actual: -12, label: 'S7' },
  { raw: 130, actual: 0, label: 'S9' },
  { raw: 165, actual: 10, label: 'S9+10' },
  { raw: 200, actual: 20, label: 'S9+20' },
  { raw: 240, actual: 40, label: 'S9+40' },
];
vi.mock('$lib/runtime/adapters/capabilities-adapter', () => ({
  getMeterCalibration: vi.fn((meterType: string) =>
    meterType === 's_meter' ? IC7610_LIKE_S_METER_CAL : null),
  getMeterRedline: vi.fn(() => null),
}));

let components: ReturnType<typeof mount>[] = [];
let roots: HTMLElement[] = [];

function mountPanel(props: ComponentProps<typeof DockMeterPanel>) {
  const t = document.createElement('div');
  document.body.appendChild(t);
  roots.push(t);
  const component = mount(DockMeterPanel, { target: t, props });
  flushSync();
  components.push(component);
  return t;
}

beforeEach(() => {
  components = [];
  roots = [];
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  roots.forEach((r) => r.remove());
  components = [];
  roots = [];
});

const baseProps: ComponentProps<typeof DockMeterPanel> = {
  sValue: 0,
  rfPower: 0,
  swr: 0,
  alc: 0,
  txActive: false,
  meterSource: 'S',
  onMeterSourceChange: () => {},
};

/** Returns the bar-fill width (%) for the row whose label matches `label`. */
function fillPctForLabel(root: HTMLElement, label: string): number {
  const rows = Array.from(root.querySelectorAll('.dock-row'));
  const row = rows.find((r) => r.querySelector('.dock-row-label')?.textContent === label);
  if (!row) throw new Error(`no DockMeterPanel row labelled ${label}`);
  const fill = row.querySelector('.dock-bar-fill') as HTMLElement;
  return parseFloat(fill.style.width);
}

describe('DockMeterPanel structure', () => {
  it('renders the four meter rows in fixed order S, Po, SWR, ALC', () => {
    const t = mountPanel(baseProps);
    const labels = Array.from(t.querySelectorAll('.dock-row .dock-row-label')).map(
      (el) => el.textContent,
    );
    expect(labels).toEqual(['S', 'Po', 'SWR', 'ALC']);
  });
});

describe('DockMeterPanel calibrated bar fill (MOR-482)', () => {
  // The bar fill must agree with the calibrated readout, not raw/255.
  it('fills the SWR bar to ~100% at SWR 3.0 (raw=120), not ~47%', () => {
    // raw/255 would give 120/255 = ~47%; the 3.0 full-scale knot gives ~100%.
    const t = mountPanel({ ...baseProps, swr: 120, txActive: true });
    expect(fillPctForLabel(t, 'SWR')).toBeGreaterThan(95);
  });

  it('fills the SWR bar to ~50% at SWR 2.0 (raw=80), not ~31%', () => {
    // raw/255 = 80/255 = ~31%; calibrated 2.0/3.0 = ~67%.
    const t = mountPanel({ ...baseProps, swr: 80, txActive: true });
    const pct = fillPctForLabel(t, 'SWR');
    expect(pct).toBeGreaterThan(60);
    expect(pct).toBeLessThan(70);
  });

  it('fills the S bar to the shared S9 position at calibrated 0 dB-rel-S9', () => {
    const t = mountPanel({ ...baseProps, sValue: 0 });
    const pct = fillPctForLabel(t, 'S');
    expect(pct).toBeGreaterThan(53);
    expect(pct).toBeLessThan(56);
  });

  it('fills the S bar to ~100% at the top calibrated S anchor (+40 dB)', () => {
    const t = mountPanel({ ...baseProps, sValue: 40 });
    expect(fillPctForLabel(t, 'S')).toBeGreaterThan(99);
  });

  it('fills the Po bar to ~100% at the full-scale power knot (raw=212), not ~83%', () => {
    // raw/255 = 212/255 = ~83%; calibrated 100W/100W = 100%.
    const t = mountPanel({ ...baseProps, rfPower: 212, txActive: true });
    expect(fillPctForLabel(t, 'Po')).toBeGreaterThan(99);
  });

  it('fills the ALC bar to ~100% at the redline (raw=120), not ~47%', () => {
    // raw/255 = 120/255 = ~47%; calibrated 120/120 redline = 100%.
    const t = mountPanel({ ...baseProps, alc: 120, txActive: true });
    expect(fillPctForLabel(t, 'ALC')).toBeGreaterThan(99);
  });
});
