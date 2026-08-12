import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ComponentProps } from 'svelte';
import DockMeterPanel from '../DockMeterPanel.svelte';

// MOR-1470: no meter has a hardcoded fallback curve — the REAL capabilities
// store is seeded with an IC-7610-shaped profile and the props below carry
// engineering units (watts / ratio / normalized ALC / dB-rel-S9), exactly
// what the backend publishes for a rig with these tables (MOR-469).
import type { Capabilities } from '$lib/types/capabilities';
import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';

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
function makeCaps(): Capabilities {
  return {
    model: 'IC-7610',
    scope: true,
    audio: true,
    tx: true,
    capabilities: ['scope', 'tx'],
    receivers: 2,
    vfoScheme: 'main_sub',
    freqRanges: [{ start: 1800000, end: 30000000, label: 'HF' }],
    modes: ['USB', 'LSB', 'CW', 'AM', 'FM'],
    filters: ['FIL1', 'FIL2', 'FIL3'],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['opus'] },
    webrtc: { available: true, enabled: false },
    txBands: null,
    stateContractVersion: 1,
    providerGeneration: 0,
    meterCalibrations: {
      s_meter: IC7610_LIKE_S_METER_CAL,
      power: [
        { raw: 0, actual: 0, label: '0' },
        { raw: 143, actual: 50, label: '50' },
        { raw: 212, actual: 100, label: '100' },
      ],
      swr: [
        { raw: 0, actual: 1.0, label: '1.0' },
        { raw: 48, actual: 1.5, label: '1.5' },
        { raw: 80, actual: 2.0, label: '2.0' },
        { raw: 120, actual: 3.0, label: '3.0' },
      ],
      alc: [
        { raw: 0, actual: 0, label: '0' },
        { raw: 120, actual: 100, label: '100' },
      ],
    },
  };
}

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
  setCapabilities(makeCaps());
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  roots.forEach((r) => r.remove());
  components = [];
  roots = [];
  clearCapabilities();
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
  it('fills the SWR bar to ~100% at the 3.0 full-scale knot', () => {
    // Ratio 3.0 over a table topping at 3.0 -> full bar.
    const t = mountPanel({ ...baseProps, swr: 3.0, txActive: true });
    expect(fillPctForLabel(t, 'SWR')).toBeGreaterThan(95);
  });

  it('fills the SWR bar to ~67% at ratio 2.0 (2.0/3.0)', () => {
    const t = mountPanel({ ...baseProps, swr: 2.0, txActive: true });
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

  it('fills the Po bar to ~100% at full-scale watts', () => {
    // 100 W of the 100 W top knot -> full bar.
    const t = mountPanel({ ...baseProps, rfPower: 100, txActive: true });
    expect(fillPctForLabel(t, 'Po')).toBeGreaterThan(99);
  });

  it('fills the ALC bar to ~100% at the redline (normalized 1.0)', () => {
    const t = mountPanel({ ...baseProps, alc: 1.0, txActive: true });
    expect(fillPctForLabel(t, 'ALC')).toBeGreaterThan(99);
  });
});
