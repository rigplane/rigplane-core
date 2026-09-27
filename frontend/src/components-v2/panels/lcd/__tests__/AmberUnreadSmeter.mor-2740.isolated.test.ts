/**
 * MOR-2740 — an unread S-meter draws nothing on the LCD faces.
 *
 * Owner rule: the operator never sees a made-up value, so an unread reading
 * draws no lit segment and no text. `AmberCockpit` faked an unread S-meter as
 * `0` dB-rel-S9 (a calibrated S9 at -73 dBm) in three places
 * (`subSValue`, the `meterValue` S branch, `mainSMeter`), and `AmberScope`
 * faked it as `-54` (S0). Each pin below is red on that old code when the
 * radio is calibrated (the calibration mock is what makes the fabrication
 * visible as S9/S0 text): unread draws no `.readout-s`/`.readout-dbm` text
 * and no `.seg.filled`, on both faces. A read S9 (`0`) draws exactly as
 * before — `S9` and `−73 dBm` — pinned literally.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ServerState } from '$lib/types/state';

const CAL = vi.hoisted(() => [
  { raw: 0, actual: -54, label: 'S0' },
  { raw: 26, actual: -48, label: 'S1' },
  { raw: 52, actual: -36, label: 'S3' },
  { raw: 78, actual: -24, label: 'S5' },
  { raw: 103, actual: -12, label: 'S7' },
  { raw: 130, actual: 0, label: 'S9' },
  { raw: 165, actual: 10, label: 'S9+10' },
  { raw: 200, actual: 20, label: 'S9+20' },
  { raw: 240, actual: 40, label: 'S9+40' },
]);

vi.mock('$lib/stores/capabilities.svelte', () => ({
  getSmeterCalibration: () => CAL,
  getSmeterRedline: () => null,
  isAudioFftScope: () => false,
  hasAudioFft: () => false,
  hasDualReceiver: () => false,
  getCapabilities: () => null,
  getMeterCalibration: () => null,
  getMeterRedline: () => null,
  getControlRange: () => null,
}));

const cockpitProps = vi.hoisted(() => ({
  value: {
    radioState: null as ServerState | null,
    caps: null,
    hasCapability: (_name: string) => true,
    hasAudioFft: false,
    hasDualReceiver: false,
  },
}));

const scopeProps = vi.hoisted(() => ({
  value: {
    radioState: null as ServerState | null,
    caps: null,
    hasCapability: (_name: string) => true,
    hasAudioFft: false,
    hasDualReceiver: false,
  },
}));

const amberCaps = {
  capabilities: [
    'rit', 'xit', 'vox', 'compressor', 'tuner', 'split', 'dial_lock', 'ip_plus',
  ],
} as any;

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  deriveAmberCockpitProps: () => cockpitProps.value,
  deriveAmberScopeProps: () => scopeProps.value,
  deriveAmberTelemetryProps: () => ({ vdRaw: null, idRaw: null }),
  getAmberCockpitHandlers: () => ({ onTuningChange: vi.fn() }),
  getVfoHandlers: () => ({ onFreqChange: vi.fn(), onModeChange: vi.fn() }),
  bindVfoTunerContext: () => ({ read: vi.fn(() => ({ view: null })) }),
}));

vi.mock('$lib/runtime/adapters/qsy-history-adapter', () => ({
  deriveQsyRecent: () => [],
}));

vi.mock('$lib/runtime/frontend-runtime', () => ({
  presentationResources: { acquire: vi.fn(() => ({})), release: vi.fn() },
  runtime: {
    send: vi.fn(),
    scope: {
      registerPresentationDriver: vi.fn(),
      subscribe: vi.fn(() => vi.fn()),
    },
  },
}));

import AmberCockpit from '../AmberCockpit.svelte';
import AmberScope from '../AmberScope.svelte';

let components: ReturnType<typeof mount>[] = [];

function baseReceiver(overrides: Record<string, unknown> = {}) {
  return {
    freqHz: 14_074_000, mode: 'USB', filter: 1, dataMode: 0, sMeter: 0,
    att: 0, preamp: 0, nb: false, nr: false, afLevel: 128, rfGain: 255,
    squelch: 0, agc: 2,
    ...overrides,
  };
}

function mountCockpit(state: ServerState | null, dual = false) {
  cockpitProps.value = {
    radioState: state,
    caps: amberCaps,
    hasCapability: (name: string) => amberCaps.capabilities.includes(name),
    hasAudioFft: false,
    hasDualReceiver: dual,
  };
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(AmberCockpit, { target });
  flushSync();
  components.push(component);
  return target;
}

function mountScope(state: ServerState | null, dual = false) {
  scopeProps.value = {
    radioState: state,
    caps: amberCaps,
    hasCapability: (name: string) => amberCaps.capabilities.includes(name),
    hasAudioFft: false,
    hasDualReceiver: dual,
  };
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(AmberScope, { target });
  flushSync();
  components.push(component);
  return target;
}

function readoutText(meter: Element | null, selector: string): string {
  return meter?.querySelector(selector)?.textContent ?? '';
}

function filledCount(meter: Element | null): number {
  return meter?.querySelectorAll('.seg.filled').length ?? 0;
}

beforeEach(() => {
  components = [];
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
});

describe('MOR-2740 — unread S-meter draws nothing on AmberCockpit', () => {
  it('draws no S text and no lit segment when both receivers are unread', () => {
    const state = {
      active: 'MAIN',
      main: baseReceiver({ sMeter: null }),
      sub: baseReceiver({ sMeter: null }),
    } as unknown as ServerState;
    const target = mountCockpit(state, true);
    const meters = target.querySelectorAll('.lcd-smeter');
    expect(meters.length).toBe(2);
    for (const meter of meters) {
      expect(readoutText(meter, '.readout-s')).toBe('');
      expect(readoutText(meter, '.readout-dbm')).toBe('');
      expect(filledCount(meter)).toBe(0);
      expect(meter.querySelector('.meter-readout')).not.toBeNull();
    }
  });

  it('draws no S text on the inactive main meter when its own reading is unread', () => {
    const state = {
      active: 'SUB',
      main: baseReceiver({ sMeter: null }),
      sub: baseReceiver({ sMeter: 0 }),
    } as unknown as ServerState;
    const target = mountCockpit(state, true);
    const mainMeter = target.querySelector('.lcd-vfo-a .lcd-smeter');
    expect(readoutText(mainMeter, '.readout-s')).toBe('');
    expect(readoutText(mainMeter, '.readout-dbm')).toBe('');
    expect(filledCount(mainMeter)).toBe(0);
    const subMeter = target.querySelector('.lcd-vfo-b .lcd-smeter');
    expect(readoutText(subMeter, '.readout-s')).toBe('S9');
    expect(readoutText(subMeter, '.readout-dbm')).toBe('−73 dBm');
  });

  it('draws S9 exactly as before when the reading is read', () => {
    const state = {
      active: 'MAIN',
      main: baseReceiver({ sMeter: 0 }),
      sub: baseReceiver({ sMeter: 0 }),
    } as unknown as ServerState;
    const target = mountCockpit(state, true);
    const meters = target.querySelectorAll('.lcd-smeter');
    expect(meters.length).toBe(2);
    for (const meter of meters) {
      expect(readoutText(meter, '.readout-s')).toBe('S9');
      expect(readoutText(meter, '.readout-dbm')).toBe('−73 dBm');
      expect(filledCount(meter)).toBeGreaterThan(0);
    }
  });
});

describe('MOR-2740 — unread S-meter draws nothing on AmberScope', () => {
  it('draws no S text and no lit segment when the active reading is unread', () => {
    const state = {
      active: 'MAIN',
      main: baseReceiver({ sMeter: null }),
    } as unknown as ServerState;
    const target = mountScope(state);
    const meter = target.querySelector('.lcd-smeter');
    expect(meter).not.toBeNull();
    expect(readoutText(meter, '.readout-s')).toBe('');
    expect(readoutText(meter, '.readout-dbm')).toBe('');
    expect(filledCount(meter)).toBe(0);
  });

  it('draws S9 exactly as before when the reading is read', () => {
    const state = {
      active: 'MAIN',
      main: baseReceiver({ sMeter: 0 }),
    } as unknown as ServerState;
    const target = mountScope(state);
    const meter = target.querySelector('.lcd-smeter');
    expect(readoutText(meter, '.readout-s')).toBe('S9');
    expect(readoutText(meter, '.readout-dbm')).toBe('−73 dBm');
    expect(filledCount(meter)).toBeGreaterThan(0);
  });
});
