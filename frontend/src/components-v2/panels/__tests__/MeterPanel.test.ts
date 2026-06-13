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

describe('formatPowerWatts (IC-7610 CI-V p.4: 00=0%, 143=50%, 212=100%)', () => {
  it('returns 0W for raw 0', () => {
    expect(formatPowerWatts(0)).toBe('0W');
  });

  it('returns 50W for raw 143', () => {
    expect(formatPowerWatts(143)).toBe('50W');
  });

  it('returns 100W for raw 212', () => {
    expect(formatPowerWatts(212)).toBe('100W');
  });

  it('clamps negative raw to 0W', () => {
    expect(formatPowerWatts(-50)).toBe('0W');
  });
});

// ---------------------------------------------------------------------------
// formatSwr
// ---------------------------------------------------------------------------

describe('formatSwr (IC-7610 CI-V p.4: 0=1.0, 48=1.5, 80=2.0, 120=3.0)', () => {
  it('returns 1.0 for raw 0', () => {
    expect(formatSwr(0)).toBe('1.0');
  });

  it('returns 1.5 for raw 48', () => {
    expect(formatSwr(48)).toBe('1.5');
  });

  it('returns 2.0 for raw 80', () => {
    expect(formatSwr(80)).toBe('2.0');
  });

  it('returns 3.0 for raw 120', () => {
    expect(formatSwr(120)).toBe('3.0');
  });

  it('returns ∞ for raw 255', () => {
    expect(formatSwr(255)).toBe('∞');
  });
});

// ---------------------------------------------------------------------------
// formatAlc
// ---------------------------------------------------------------------------

describe('formatAlc (IC-7610 CI-V p.4: 0=Min, 120=Max)', () => {
  it('returns 0% for raw 0', () => {
    expect(formatAlc(0)).toBe('0%');
  });

  it('returns 100% for raw 120', () => {
    expect(formatAlc(120)).toBe('100%');
  });

  it('returns 50% for raw 60', () => {
    expect(formatAlc(60)).toBe('50%');
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

describe('getNeedleMarks SWR (IC-7610: 0=1.0, 48=1.5, 80=2.0, 120=3.0)', () => {
  it('returns 4 marks for SWR source', () => {
    expect(getNeedleMarks('SWR')).toHaveLength(4);
  });

  it('first mark is 1.0 at 0', () => {
    const marks = getNeedleMarks('SWR');
    expect(marks[0]).toEqual({ pos: 0, label: '1.0' });
  });

  it('last mark is 3.0 at 120/255', () => {
    const marks = getNeedleMarks('SWR');
    expect(marks[3].label).toBe('3.0');
    expect(marks[3].pos).toBeCloseTo(120 / 255, 3);
  });
});

describe('getNeedleMarks POWER', () => {
  it('returns 5 marks for POWER source', () => {
    expect(getNeedleMarks('POWER')).toHaveLength(5);
  });

  it('marks are 0, 25, 50, 75, 100', () => {
    const labels = getNeedleMarks('POWER').map((m) => m.label);
    expect(labels).toEqual(['0', '25', '50', '75', '100']);
  });
});

// ---------------------------------------------------------------------------
// MeterPanel component
// ---------------------------------------------------------------------------

vi.mock('$lib/runtime/adapters/capabilities-adapter', () => ({
  getMeterCalibration: vi.fn(() => null),
  getMeterRedline: vi.fn(() => null),
}));

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
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
});

const baseProps: ComponentProps<typeof MeterPanel> = {
  sValue: 120,
  rfPower: 100,
  swr: 50,
  alc: 64,
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
