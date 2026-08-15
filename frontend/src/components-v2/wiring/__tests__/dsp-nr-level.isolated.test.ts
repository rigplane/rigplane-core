import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Capabilities, ControlDomain } from '$lib/types/capabilities';

vi.mock('$lib/transport/ws-client', () => ({
  sendCommand: vi.fn(),
}));
vi.mock('$lib/runtime/commands/radio-intents', async () => {
  const { sendCommand } = await import('$lib/transport/ws-client');
  return { dispatchRadioIntent: ({ name, params }: { name: string; params: Record<string, unknown> }) => sendCommand(name, params) };
});

vi.mock('$lib/stores/radio.svelte', () => ({
  getActiveReceiver: vi.fn(() => ({ nr: false, nrLevel: 0 })),
  getRadioState: vi.fn(() => ({
    active: 'MAIN', main: { nr: false, nrLevel: 0 }, sub: { nr: false, nrLevel: 0 },
  })),
}));

vi.mock('$lib/stores/capabilities.svelte', () => ({
  getCapabilities: vi.fn(),
  getControlRange: vi.fn(() => null),
}));

vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    setAudioConfig: vi.fn(),
    startRx: vi.fn(),
    stopRx: vi.fn(),
    setRxVolume: vi.fn(),
    rxEnabled: false,
  },
}));

import { sendCommand } from '$lib/transport/ws-client';
import * as capabilitiesStore from '$lib/stores/capabilities.svelte';
import * as radioStore from '$lib/stores/radio.svelte';
import {
  controlRangeFromCapsOrDefault,
  nrRawToDisplay,
  resolveNrLevelContract,
} from '$lib/radio/filter-controls';
import { makeDspHandlers } from '$lib/runtime/commands/panel-commands';

const EXACT_NR_DOMAIN: ControlDomain = {
  mapping: 'identity',
  raw_min: 0,
  raw_max: 10,
  raw_step: 1,
  raw_origin: 0,
  display_min: '0' as never,
  display_max: '10' as never,
  display_step: '1' as never,
  display_origin: '0' as never,
  display_unit: 'level',
  quantization: 'reject',
  restoration: 'exact',
};

function caps(controls: Record<string, unknown> = {}): Capabilities {
  return {
    model: 'Test Radio', scope: false, audio: false, tx: false,
    capabilities: ['nr'], receivers: 2, vfoScheme: 'main_sub',
    freqRanges: [], modes: [], filters: [],
    audioConfig: { sampleRate: 48_000, channels: 1, codecs: ['pcm'] },
    webrtc: { available: false, enabled: false }, txBands: null,
    stateContractVersion: 1, providerGeneration: 0,
    controls: controls as Capabilities['controls'],
  };
}

function useCaps(value: Capabilities): void {
  vi.mocked(capabilitiesStore.getCapabilities).mockReturnValue(value);
}

const METADATA_TRAPS: ReadonlyArray<readonly [string, () => Capabilities]> = [
  ['caps.controls getter', () => {
    const value = caps();
    Object.defineProperty(value, 'controls', { get: () => { throw new Error('controls getter'); } });
    return value;
  }],
  ['controls own-property descriptor', () => caps(new Proxy({}, {
    getOwnPropertyDescriptor: () => { throw new Error('descriptor trap'); },
  }))],
  ['controls array', () => caps([] as unknown as Record<string, unknown>)],
  ['own nr_level getter', () => {
    const controls: Record<string, unknown> = {};
    Object.defineProperty(controls, 'nr_level', { get: () => { throw new Error('nr_level getter'); } });
    return caps(controls);
  }],
  ['nr_level candidate has trap', () => caps({ nr_level: new Proxy({}, {
    has: () => { throw new Error('candidate has trap'); },
  }) })],
  ['nr_level candidate property trap', () => caps({ nr_level: new Proxy({}, {
    has: () => true,
    get: () => { throw new Error('candidate property trap'); },
  }) })],
];

beforeEach(() => {
  vi.mocked(sendCommand).mockClear();
  useCaps(caps());
});

describe('exact NR-level contract (MOR-1733)', () => {
  it.each([0, 1, 4, 10])('round-trips raw/display %i unchanged through the conversion contract', (value) => {
    const exactCaps = caps({ nr_level: EXACT_NR_DOMAIN });
    const conversion = controlRangeFromCapsOrDefault('nr_level', exactCaps);
    const contract = resolveNrLevelContract(exactCaps);

    expect(nrRawToDisplay(value, conversion)).toBe(value);
    expect(contract.displayToRaw(value)).toBe(value);
  });

  it('emits the displayed FTX level without rescaling and preserves receiver identity', () => {
    useCaps(caps({ nr_level: EXACT_NR_DOMAIN }));

    makeDspHandlers().onNrLevelChange(4);

    expect(sendCommand).toHaveBeenCalledWith('set_nr_level', { level: 4, receiver: 0 });
  });

  it.each([-1, 11, 0.5, Number.NaN, Number.POSITIVE_INFINITY, Number.MAX_SAFE_INTEGER + 1])(
    'emits nothing for invalid exact-domain display input %s',
    (value) => {
      useCaps(caps({ nr_level: EXACT_NR_DOMAIN }));
      makeDspHandlers().onNrLevelChange(value);
      expect(sendCommand).not.toHaveBeenCalled();
    },
  );

  it.each([
    ['non-restorable', { ...EXACT_NR_DOMAIN, restoration: 'unavailable' }],
    ['non-round-tripping', {
      ...EXACT_NR_DOMAIN,
      mapping: 'lookup',
      lookup: [0, 1, 4, 10].map((value) => ({ raw: value, display: String(value) })),
    }],
    ['malformed exact-present', { ...EXACT_NR_DOMAIN, display_max: '9' }],
    ['malformed legacy-present', {}],
  ])('fails closed for a %s nr_level domain', (_name, domain) => {
    useCaps(caps({ nr_level: domain }));
    makeDspHandlers().onNrLevelChange(4);
    expect(sendCommand).not.toHaveBeenCalled();
  });

  it('never treats controls.nr as the NR-level domain', () => {
    useCaps(caps({ nr: EXACT_NR_DOMAIN }));
    makeDspHandlers().onNrLevelChange(4);
    expect(sendCommand).toHaveBeenCalledWith('set_nr_level', { level: 68, receiver: 0 });
  });

  it.each(METADATA_TRAPS)('returns an invalid contract for trapped %s metadata', (_name, makeCaps) => {
    expect(() => resolveNrLevelContract(makeCaps()).displayToRaw(4)).not.toThrow();
    expect(resolveNrLevelContract(makeCaps()).displayToRaw(4)).toBeNull();
    expect(() => nrRawToDisplay(4, controlRangeFromCapsOrDefault('nr_level', makeCaps()))).not.toThrow();
    expect(nrRawToDisplay(4, controlRangeFromCapsOrDefault('nr_level', makeCaps()))).toBeUndefined();
  });

  it.each(METADATA_TRAPS)('emits nothing when the command seam encounters trapped %s metadata', (_name, makeCaps) => {
    useCaps(makeCaps());
    expect(() => makeDspHandlers().onNrLevelChange(4)).not.toThrow();
    expect(sendCommand).not.toHaveBeenCalled();
  });
});

describe('legacy NR-level fallback', () => {
  it.each([
    [0, 0],
    [8, 136],
    [15, 255],
  ])('maps display %i to raw %i only when an exact nr_level domain is absent', (display, raw) => {
    makeDspHandlers().onNrLevelChange(display);
    expect(sendCommand).toHaveBeenCalledWith('set_nr_level', { level: raw, receiver: 0 });
  });

  it.each([
    [0, 0],
    [128, 8],
    [255, 15],
  ])('preserves raw %i to display %i', (raw, display) => {
    expect(nrRawToDisplay(raw, controlRangeFromCapsOrDefault('nr_level', caps()))).toBe(display);
  });

  it('has no optimistic store path left to write through (MOR-1409 A09b)', () => {
    makeDspHandlers().onNrLevelChange(15);
    expect(Object.keys(radioStore)).not.toContain('patchActiveReceiver');
  });
});
