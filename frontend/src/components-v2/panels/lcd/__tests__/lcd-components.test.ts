/**
 * Component-level render tests for AmberFrequency and AmberSmeter.
 *
 * Uses native svelte mount() in jsdom. AmberSmeter depends on smeter-scale
 * which reads from capabilities store — mocked to return IC-7610 defaults.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount } from 'svelte';

// Mock capabilities store before any component import
vi.mock('$lib/stores/capabilities.svelte', () => ({
  getSmeterCalibration: () => null, // null → falls back to DEFAULT_CAL in smeter-scale
  getSmeterRedline: () => null,
  isAudioFftScope: () => false,
  hasAudioFft: () => false,
  hasDualReceiver: () => false,
  getCapabilities: () => null,
  // meter-utils calibrated formatters resolve through these; null → the
  // hardcoded IC-7610 fallback knots (matching the formatters under test).
  getMeterCalibration: () => null,
  getMeterRedline: () => null,
  getControlRange: () => null,
}));

vi.mock('$lib/stores/radio.svelte', () => ({
  radio: { current: null },
  getActiveReceiver: vi.fn(),
  getRadioState: vi.fn(),
  patchActiveReceiver: vi.fn(),
  patchRadioState: vi.fn(),
  patchReceiver: vi.fn(),
}));

vi.mock('$lib/transport/ws-client', () => ({
  getChannel: () => ({
    connect: vi.fn(),
    disconnect: vi.fn(),
    onBinary: vi.fn(() => vi.fn()),
  }),
}));

vi.mock('$lib/stores/connection.svelte', () => ({
  markScopeFrame: vi.fn(),
}));

vi.mock('../../wiring/state-adapter', () => ({
  resolveFilterModeConfig: () => null,
}));

import AmberFrequency from '../AmberFrequency.svelte';
import AmberSmeter from '../AmberSmeter.svelte';
import {
  formatPowerWatts,
  formatSwr,
  formatAlc,
  formatCompDb,
} from '../../meter-utils';

let target: HTMLDivElement;

beforeEach(() => {
  target = document.createElement('div');
  document.body.appendChild(target);
});

afterEach(() => {
  document.body.removeChild(target);
});

// ── AmberFrequency ──────────────────────────────────────────────────────────

describe('AmberFrequency', () => {
  it('mounts without errors', () => {
    const component = mount(AmberFrequency, { target, props: { freqHz: 14_074_000 } });
    expect(target.querySelector('.lcd-freq')).not.toBeNull();
    unmount(component);
  });

  it('renders frequency digits for 14.074.000 Hz', () => {
    const component = mount(AmberFrequency, { target, props: { freqHz: 14_074_000 } });
    const active = target.querySelector('.freq-active')!;
    const mhz = active.querySelector('.seg-mhz')!.textContent;
    const khz = active.querySelector('.seg-khz')!.textContent;
    const hz = active.querySelector('.seg-hz')!.textContent;
    expect(mhz).toBe('14');
    expect(khz).toBe('074');
    expect(hz).toBe('000');
    unmount(component);
  });

  it('displays correct digit segments (mhz + dot + khz + dot + hz)', () => {
    const component = mount(AmberFrequency, { target, props: { freqHz: 7_200_500 } });
    const active = target.querySelector('.freq-active')!;
    // 5 span children: seg-mhz, seg-dot, seg-khz, seg-dot, seg-hz
    const spans = active.querySelectorAll('span');
    expect(spans.length).toBe(5);
    expect(spans[0].textContent).toBe('7');    // mhz
    expect(spans[1].textContent).toBe('.');    // dot
    expect(spans[2].textContent).toBe('200');  // khz
    expect(spans[3].textContent).toBe('.');    // dot
    expect(spans[4].textContent).toBe('500');  // hz
    unmount(component);
  });

  it('handles zero frequency gracefully (shows dashes)', () => {
    const component = mount(AmberFrequency, { target, props: { freqHz: 0 } });
    const active = target.querySelector('.freq-active')!;
    expect(active.querySelector('.seg-mhz')!.textContent).toBe('--');
    expect(active.querySelector('.seg-khz')!.textContent).toBe('---');
    expect(active.querySelector('.seg-hz')!.textContent).toBe('---');
    unmount(component);
  });

  it('handles negative frequency as zero (shows dashes)', () => {
    const component = mount(AmberFrequency, { target, props: { freqHz: -100 } });
    const active = target.querySelector('.freq-active')!;
    expect(active.querySelector('.seg-mhz')!.textContent).toBe('--');
    unmount(component);
  });

  it('renders ghost segments (all-8s) for LCD look', () => {
    const component = mount(AmberFrequency, { target, props: { freqHz: 14_074_000 } });
    const ghost = target.querySelector('.freq-ghost')!;
    expect(ghost.querySelector('.seg-khz')!.textContent).toBe('888');
    expect(ghost.querySelector('.seg-hz')!.textContent).toBe('888');
    unmount(component);
  });

  it('applies large size class by default', () => {
    const component = mount(AmberFrequency, { target, props: { freqHz: 14_074_000 } });
    expect(target.querySelector('.lcd-freq-large')).not.toBeNull();
    unmount(component);
  });

  it('applies small size class when size="small"', () => {
    const component = mount(AmberFrequency, {
      target,
      props: { freqHz: 14_074_000, size: 'small' },
    });
    expect(target.querySelector('.lcd-freq-small')).not.toBeNull();
    unmount(component);
  });
});

// ── AmberSmeter ─────────────────────────────────────────────────────────────

describe('AmberSmeter', () => {
  it('mounts without errors', () => {
    const component = mount(AmberSmeter, { target, props: { value: 0 } });
    expect(target.querySelector('.lcd-smeter')).not.toBeNull();
    unmount(component);
  });

  it('renders 192 bar segments', () => {
    const component = mount(AmberSmeter, { target, props: { value: 100 } });
    const segs = target.querySelectorAll('.seg');
    expect(segs.length).toBe(192);
    unmount(component);
  });

  it('displays signal level indicator (readout)', () => {
    const component = mount(AmberSmeter, { target, props: { value: 100 } });
    const readout = target.querySelector('.meter-readout')!;
    expect(readout.querySelector('.readout-s')).not.toBeNull();
    expect(readout.querySelector('.readout-dbm')).not.toBeNull();
    unmount(component);
  });

  it('handles zero signal — no filled segments', () => {
    const component = mount(AmberSmeter, { target, props: { value: 0 } });
    const filled = target.querySelectorAll('.seg.filled');
    expect(filled.length).toBe(0);
    // S-unit readout should show S0
    expect(target.querySelector('.readout-s')!.textContent).toBe('S0');
    unmount(component);
  });

  it('fills proportional segments for mid-range signal', () => {
    const component = mount(AmberSmeter, { target, props: { value: 128 } });
    const filled = target.querySelectorAll('.seg.filled');
    // 128/255 * 192 ≈ 97 segments
    expect(filled.length).toBeGreaterThan(80);
    expect(filled.length).toBeLessThan(120);
    unmount(component);
  });

  it('fills all segments for max signal (255)', () => {
    const component = mount(AmberSmeter, { target, props: { value: 255 } });
    const filled = target.querySelectorAll('.seg.filled');
    expect(filled.length).toBe(192);
    unmount(component);
  });

  it('marks over-S9 segments with over-s9 class', () => {
    const component = mount(AmberSmeter, { target, props: { value: 200 } });
    const overS9 = target.querySelectorAll('.seg.filled.over-s9');
    expect(overS9.length).toBeGreaterThan(0);
    unmount(component);
  });

  it('renders scale ticks and labels', () => {
    const component = mount(AmberSmeter, { target, props: { value: 100 } });
    const scale = target.querySelector('.meter-scale')!;
    // Major ticks have labels (S1, S3, S5, S7, S9, +10, +20, +30, +40)
    const labels = scale.querySelectorAll('.tick-label');
    expect(labels.length).toBeGreaterThan(0);
    // S label present
    expect(scale.querySelector('.scale-s-label')!.textContent).toBe('S');
    unmount(component);
  });

  it('applies tx class when txActive is true', () => {
    const component = mount(AmberSmeter, { target, props: { value: 100, txActive: true } });
    const txSegs = target.querySelectorAll('.seg.filled.tx');
    expect(txSegs.length).toBeGreaterThan(0);
    unmount(component);
  });

  it('shows dBm in readout', () => {
    const component = mount(AmberSmeter, { target, props: { value: 162 } });
    const dbm = target.querySelector('.readout-dbm')!;
    // At S9 (raw=162), dBm should be 0
    expect(dbm.textContent).toContain('0');
    expect(dbm.textContent).toContain('dBm');
    unmount(component);
  });

  // ── MOR-483 part 2: PO/SWR/ALC/COMP readouts use calibrated formatters ──

  it('PO readout uses calibrated formatPowerWatts, not raw/255*100', () => {
    const component = mount(AmberSmeter, { target, props: { value: 143, source: 'PO' } });
    const sub = target.querySelector('.readout-dbm')!;
    expect(sub.textContent).toBe(formatPowerWatts(143)); // '50W', not '56W'
    unmount(component);
  });

  it('SWR readout uses calibrated formatSwr, not 1.0+raw/255*8.9', () => {
    const component = mount(AmberSmeter, { target, props: { value: 80, source: 'SWR' } });
    const sub = target.querySelector('.readout-dbm')!;
    expect(sub.textContent).toBe(formatSwr(80)); // '2.0', not '3.8'
    unmount(component);
  });

  it('ALC readout uses calibrated formatAlc, not raw/255*100', () => {
    const component = mount(AmberSmeter, { target, props: { value: 60, source: 'ALC' } });
    const sub = target.querySelector('.readout-dbm')!;
    expect(sub.textContent).toBe(formatAlc(60)); // '50%', not '24%'
    unmount(component);
  });

  it('COMP readout uses calibrated formatCompDb, not raw/255*20', () => {
    const component = mount(AmberSmeter, { target, props: { value: 75, source: 'COMP' } });
    const sub = target.querySelector('.readout-dbm')!;
    expect(sub.textContent).toBe(formatCompDb(75)); // '15 dB', not '6dB'
    unmount(component);
  });
});
