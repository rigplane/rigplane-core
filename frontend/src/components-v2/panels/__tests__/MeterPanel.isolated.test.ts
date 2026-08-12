import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ComponentProps } from 'svelte';
import MeterPanel from '../MeterPanel.svelte';
import {
  normalize,
  formatPowerWatts,
  formatSwr,
  formatAlc,
  getNeedleMarks,
} from '../meter-utils';

// ---------------------------------------------------------------------------
// normalize
// ---------------------------------------------------------------------------

describe('normalize', () => {
  it('maps 0 to 0', () => {
    expect(normalize(0)).toBe(0);
  });

  it('maps 255 to 1', () => {
    expect(normalize(255)).toBe(1);
  });

  it('maps 128 to ~0.502', () => {
    expect(normalize(128)).toBeCloseTo(128 / 255, 5);
  });

  it('clamps negative values to 0', () => {
    expect(normalize(-10)).toBe(0);
  });

  it('clamps values above 255 to 1', () => {
    expect(normalize(300)).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// formatPowerWatts
// ---------------------------------------------------------------------------

describe('formatPowerWatts (calibrated: input is watts, MOR-1470)', () => {
  it('returns 0W for 0 W', () => {
    expect(formatPowerWatts(0)).toBe('0W');
  });

  it('returns 50W as-is, no re-interpolation', () => {
    expect(formatPowerWatts(50)).toBe('50W');
  });

  it('returns 100W at full scale', () => {
    expect(formatPowerWatts(100)).toBe('100W');
  });

  it('clamps negative values to 0W', () => {
    expect(formatPowerWatts(-50)).toBe('0W');
  });
});

// ---------------------------------------------------------------------------
// formatSwr
// ---------------------------------------------------------------------------

describe('formatSwr (calibrated: input is the ratio, MOR-1470)', () => {
  it('returns 1.0 for a perfect match', () => {
    expect(formatSwr(1.0)).toBe('1.0');
  });

  it('returns 1.5', () => {
    expect(formatSwr(1.5)).toBe('1.5');
  });

  it('returns 2.0', () => {
    expect(formatSwr(2.0)).toBe('2.0');
  });

  it('returns 3.0', () => {
    expect(formatSwr(3.0)).toBe('3.0');
  });

  it('returns the profile top label at the off-scale top knot', () => {
    expect(formatSwr(6.0)).toBe('6.0+');
  });
});

// ---------------------------------------------------------------------------
// formatAlc
// ---------------------------------------------------------------------------

describe('formatAlc (calibrated: input is normalized 0-1, MOR-1470)', () => {
  it('returns 0% for 0', () => {
    expect(formatAlc(0)).toBe('0%');
  });

  it('returns 100% at the redline (1.0)', () => {
    expect(formatAlc(1.0)).toBe('100%');
  });

  it('returns 50% for 0.5', () => {
    expect(formatAlc(0.5)).toBe('50%');
  });
});

// ---------------------------------------------------------------------------
// getNeedleMarks
// ---------------------------------------------------------------------------

describe('getNeedleMarks S-meter (IC-7610 profile: 130=S9, 240=S9+40)', () => {
  it('returns 7 marks for S source', () => {
    expect(getNeedleMarks('S')).toHaveLength(7);
  });

  it('S9 mark at 130/240 on the shared calibrated scale', () => {
    const marks = getNeedleMarks('S');
    const s9 = marks.find((m) => m.label === 'S9');
    expect(s9).toBeDefined();
    expect(s9!.pos).toBeCloseTo(130 / 240, 3);
  });

  it('last mark is +40', () => {
    const marks = getNeedleMarks('S');
    expect(marks[6].label).toBe('+40');
  });
});

describe('getNeedleMarks SWR (profile table, ratio/top-knot domain)', () => {
  it('returns one mark per declared knot', () => {
    expect(getNeedleMarks('SWR')).toHaveLength(5);
  });

  it('first mark carries the profile label at ratio/top position', () => {
    const marks = getNeedleMarks('SWR');
    expect(marks[0].label).toBe('1.0');
    expect(marks[0].pos).toBeCloseTo(1.0 / 6.0, 3);
  });

  it('3.0 sits at half scale; the top label closes the scale', () => {
    const marks = getNeedleMarks('SWR');
    expect(marks[3].label).toBe('3.0');
    expect(marks[3].pos).toBeCloseTo(0.5, 3);
    expect(marks[4].label).toBe('6.0+');
    expect(marks[4].pos).toBeCloseTo(1.0, 3);
  });
});

describe('getNeedleMarks POWER (profile table)', () => {
  it('returns one mark per declared knot', () => {
    expect(getNeedleMarks('POWER')).toHaveLength(3);
  });

  it('marks carry the profile labels at watts/top positions', () => {
    const marks = getNeedleMarks('POWER');
    expect(marks.map((m) => m.label)).toEqual(['0', '50', '100']);
    expect(marks[1].pos).toBeCloseTo(0.5, 3);
  });
});

// ---------------------------------------------------------------------------
// MeterPanel component
// ---------------------------------------------------------------------------

// MOR-1470: no meter has a hardcoded fallback curve in meter-utils.ts — the
// formatter/marks tests above and the component tests below seed the REAL
// capabilities store with an IC-7610-shaped profile (all seven tables) and
// exercise the engineering-domain contract (backend interpolates raw→actual
// at the observation boundary, MOR-469).
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
        { raw: 240, actual: 6.0, label: '6.0+' },
      ],
      alc: [
        { raw: 0, actual: 0, label: '0' },
        { raw: 120, actual: 100, label: '100' },
      ],
    },
  };
}

let components: ReturnType<typeof mount>[] = [];

function mountPanel(props: ComponentProps<typeof MeterPanel>) {
  const t = document.createElement('div');
  document.body.appendChild(t);
  const component = mount(MeterPanel, { target: t, props });
  flushSync();
  components.push(component);
  return t;
}

beforeEach(() => {
  components = [];
  setCapabilities(makeCaps());
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
  clearCapabilities();
});

// Engineering-domain props: +12 dB-rel-S9, 100 W, SWR 1.2, ALC at 50%.
const baseProps: ComponentProps<typeof MeterPanel> = {
  sValue: 12,
  rfPower: 100,
  swr: 1.2,
  alc: 0.5,
  txActive: false,
  meterSource: 'S',
  hasTx: true,
  onMeterSourceChange: vi.fn(),
};

describe('panel structure', () => {
  it('renders the METERS header', () => {
    const t = mountPanel(baseProps);
    expect(t.querySelector('.panel-header')?.textContent?.trim()).toBe('METERS');
  });

  it('renders needle section', () => {
    const t = mountPanel(baseProps);
    expect(t.querySelector('.needle-section')).not.toBeNull();
  });

  it('renders source selector', () => {
    const t = mountPanel(baseProps);
    expect(t.querySelector('.source-selector')).not.toBeNull();
  });

  it('renders S source button', () => {
    const t = mountPanel(baseProps);
    const btns = Array.from(t.querySelectorAll('.source-btn'));
    expect(btns.some((b) => b.textContent?.trim() === 'S')).toBe(true);
  });

  it('renders SWR source button when hasTx is true', () => {
    const t = mountPanel(baseProps);
    const btns = Array.from(t.querySelectorAll('.source-btn'));
    expect(btns.some((b) => b.textContent?.trim() === 'SWR')).toBe(true);
  });

  it('renders Po source button when hasTx is true', () => {
    const t = mountPanel(baseProps);
    const btns = Array.from(t.querySelectorAll('.source-btn'));
    expect(btns.some((b) => b.textContent?.trim() === 'Po')).toBe(true);
  });

  it('marks S button as active when meterSource is S', () => {
    const t = mountPanel(baseProps);
    const sBtn = Array.from(t.querySelectorAll('.source-btn')).find(
      (b) => b.textContent?.trim() === 'S',
    );
    expect(sBtn?.classList.contains('active')).toBe(true);
  });

  it('marks SWR button as active when meterSource is SWR', () => {
    const t = mountPanel({ ...baseProps, meterSource: 'SWR' });
    const swrBtn = Array.from(t.querySelectorAll('.source-btn')).find(
      (b) => b.textContent?.trim() === 'SWR',
    );
    expect(swrBtn?.classList.contains('active')).toBe(true);
  });
});

describe('TX meters visibility', () => {
  it('does not render tx-meters section when txActive is false', () => {
    const t = mountPanel(baseProps);
    expect(t.querySelector('.tx-meters')).toBeNull();
  });

  it('renders tx-meters section when txActive is true', () => {
    const t = mountPanel({ ...baseProps, txActive: true });
    expect(t.querySelector('.tx-meters')).not.toBeNull();
  });
});

describe('TX source buttons visibility', () => {
  it('hides SWR and Po buttons when hasTx prop is false', () => {
    const t = mountPanel({ ...baseProps, hasTx: false });
    const btns = Array.from(t.querySelectorAll('.source-btn'));
    expect(btns.every((b) => b.textContent?.trim() === 'S')).toBe(true);
    expect(btns).toHaveLength(1);
  });
});

describe('callbacks', () => {
  it('calls onMeterSourceChange with SWR when SWR button is clicked', () => {
    const onMeterSourceChange = vi.fn();
    const t = mountPanel({ ...baseProps, onMeterSourceChange });
    const swrBtn = Array.from(t.querySelectorAll<HTMLElement>('.source-btn')).find(
      (b) => b.textContent?.trim() === 'SWR',
    );
    swrBtn?.click();
    expect(onMeterSourceChange).toHaveBeenCalledWith('SWR');
  });

  it('calls onMeterSourceChange with S when S button is clicked', () => {
    const onMeterSourceChange = vi.fn();
    const t = mountPanel({ ...baseProps, meterSource: 'SWR', onMeterSourceChange });
    const sBtn = Array.from(t.querySelectorAll<HTMLElement>('.source-btn')).find(
      (b) => b.textContent?.trim() === 'S',
    );
    sBtn?.click();
    expect(onMeterSourceChange).toHaveBeenCalledWith('S');
  });

  it('calls onMeterSourceChange with POWER when Po button is clicked', () => {
    const onMeterSourceChange = vi.fn();
    const t = mountPanel({ ...baseProps, onMeterSourceChange });
    const poBtn = Array.from(t.querySelectorAll<HTMLElement>('.source-btn')).find(
      (b) => b.textContent?.trim() === 'Po',
    );
    poBtn?.click();
    expect(onMeterSourceChange).toHaveBeenCalledWith('POWER');
  });
});
