import { describe, it, expect, beforeEach, vi } from 'vitest';
import type { Capabilities } from '../../types/capabilities';

function makeCaps(overrides: Partial<Capabilities> = {}): Capabilities {
  return {
    model: 'IC-7610',
    scope: true,
    audio: true,
    tx: true,
    capabilities: ['scope', 'dual_rx', 'tx', 'tuner', 'cw'],
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
    ...overrides,
  };
}

describe('capabilities store', () => {
  let store: typeof import('../capabilities.svelte');

  beforeEach(async () => {
    vi.resetModules();
    store = await import('../capabilities.svelte');
  });

  it('starts with null capabilities', () => {
    expect(store.getCapabilities()).toBeNull();
  });

  it('getters return false/[] before setCapabilities is called', () => {
    expect(store.hasSpectrum()).toBe(false);
    expect(store.hasAudio()).toBe(false);
    expect(store.hasDualReceiver()).toBe(false);
    expect(store.hasTx()).toBe(false);
    expect(store.getSupportedModes()).toEqual([]);
    expect(store.getSupportedFilters()).toEqual([]);
  });

  describe('setCapabilities + getCapabilities', () => {
    it('stores and returns capabilities', () => {
      const caps = makeCaps();
      store.setCapabilities(caps);
      expect(store.getCapabilities()).toStrictEqual(caps);
    });

    it('can be updated with new capabilities', () => {
      store.setCapabilities(makeCaps({ model: 'IC-7300' }));
      store.setCapabilities(makeCaps({ model: 'IC-9700' }));
      expect(store.getCapabilities()?.model).toBe('IC-9700');
    });
  });

  it('fails closed for missing or invalid contract epochs and clears on request', () => {
    expect(store.setCapabilities(makeCaps() as Capabilities)).toBe(true);
    expect(store.capabilitiesMatchGeneration(0)).toBe(true);
    expect(store.setCapabilities({ ...makeCaps(), stateContractVersion: 2 } as Capabilities)).toBe(false);
    expect(store.getCapabilities()).toBeNull();
    expect(store.setCapabilities({ ...makeCaps(), providerGeneration: -1 } as Capabilities)).toBe(false);
    expect(store.getCapabilities()).toBeNull();
    store.setCapabilities(makeCaps());
    store.clearCapabilities();
    expect(store.getCapabilities()).toBeNull();
  });

  it('notifies subscribers synchronously for accepted install and clear only while subscribed', () => {
    const seen: Array<Capabilities | null> = [];
    const unsubscribe = store.subscribeCapabilities((caps) => { seen.push(caps); });
    const generation0 = makeCaps({ providerGeneration: 0 });
    const generation1 = makeCaps({ providerGeneration: 1, model: 'IC-7300' });

    expect(seen).toEqual([null]);
    expect(store.setCapabilities(generation0)).toBe(true);
    store.clearCapabilities();
    expect(store.setCapabilities(generation1)).toBe(true);
    expect(seen).toEqual([null, generation0, null, generation1]);

    unsubscribe();
    unsubscribe();
    store.clearCapabilities();
    expect(seen).toEqual([null, generation0, null, generation1]);
  });

  describe('hasSpectrum', () => {
    it('returns true when scope is true', () => {
      store.setCapabilities(makeCaps({ scope: true }));
      expect(store.hasSpectrum()).toBe(true);
    });

    it('returns false when scope is false', () => {
      store.setCapabilities(makeCaps({ scope: false }));
      expect(store.hasSpectrum()).toBe(false);
    });
  });

  describe('hasAudio', () => {
    it('returns true when audio is true', () => {
      store.setCapabilities(makeCaps({ audio: true }));
      expect(store.hasAudio()).toBe(true);
    });

    it('returns false when audio is false', () => {
      store.setCapabilities(makeCaps({ audio: false }));
      expect(store.hasAudio()).toBe(false);
    });
  });

  describe('hasDualReceiver', () => {
    it('returns true when capabilities includes dual_rx', () => {
      store.setCapabilities(makeCaps({ capabilities: ['dual_rx'] }));
      expect(store.hasDualReceiver()).toBe(true);
    });

    it('returns false when dual_rx is absent', () => {
      store.setCapabilities(makeCaps({ capabilities: ['scope', 'tx'] }));
      expect(store.hasDualReceiver()).toBe(false);
    });

    it('returns false for empty capabilities array', () => {
      store.setCapabilities(makeCaps({ capabilities: [] }));
      expect(store.hasDualReceiver()).toBe(false);
    });
  });

  describe('hasTx', () => {
    it('returns true when tx is true', () => {
      store.setCapabilities(makeCaps({ tx: true }));
      expect(store.hasTx()).toBe(true);
    });

    it('returns false when tx is false', () => {
      store.setCapabilities(makeCaps({ tx: false }));
      expect(store.hasTx()).toBe(false);
    });
  });

  describe('getSupportedModes', () => {
    it('returns modes array after setCapabilities', () => {
      store.setCapabilities(makeCaps({ modes: ['USB', 'LSB', 'CW'] }));
      expect(store.getSupportedModes()).toEqual(['USB', 'LSB', 'CW']);
    });

    it('returns empty array before setCapabilities', () => {
      expect(store.getSupportedModes()).toEqual([]);
    });
  });

  describe('getSupportedFilters', () => {
    it('returns filters array after setCapabilities', () => {
      store.setCapabilities(makeCaps({ filters: ['FIL1', 'FIL2'] }));
      expect(store.getSupportedFilters()).toEqual(['FIL1', 'FIL2']);
    });

    it('returns empty array before setCapabilities', () => {
      expect(store.getSupportedFilters()).toEqual([]);
    });
  });

  describe('getMeterCalibration / getMeterRedline', () => {
    it('returns null before setCapabilities', () => {
      expect(store.getMeterCalibration('s_meter')).toBeNull();
      expect(store.getMeterRedline('s_meter')).toBeNull();
    });

    it('returns calibration for any meter type', () => {
      const cal = [{ raw: 0, actual: 0, label: '0W' }, { raw: 255, actual: 100, label: '100W' }];
      store.setCapabilities(makeCaps({
        meterCalibrations: { power: cal, s_meter: [{ raw: 0, actual: 0, label: 'S0' }] },
      }));
      expect(store.getMeterCalibration('power')).toEqual(cal);
      expect(store.getMeterCalibration('s_meter')).toEqual([{ raw: 0, actual: 0, label: 'S0' }]);
      expect(store.getMeterCalibration('swr')).toBeNull();
    });

    it('returns redline for any meter type', () => {
      store.setCapabilities(makeCaps({
        meterRedlines: { s_meter: 120, power: 200, swr: 64 },
      }));
      expect(store.getMeterRedline('s_meter')).toBe(120);
      expect(store.getMeterRedline('power')).toBe(200);
      expect(store.getMeterRedline('swr')).toBe(64);
      expect(store.getMeterRedline('alc')).toBeNull();
    });

    it('getSmeterCalibration delegates to getMeterCalibration', () => {
      const cal = [{ raw: 0, actual: 0, label: 'S0' }];
      store.setCapabilities(makeCaps({ meterCalibrations: { s_meter: cal } }));
      expect(store.getSmeterCalibration()).toEqual(cal);
    });

    it('getSmeterRedline delegates to getMeterRedline', () => {
      store.setCapabilities(makeCaps({ meterRedlines: { s_meter: 120 } }));
      expect(store.getSmeterRedline()).toBe(120);
    });
  });

  describe('receiverLabel', () => {
    it('returns "MAIN" for MAIN receiver id', () => {
      expect(store.receiverLabel('MAIN')).toBe('MAIN');
    });

    it('returns "SUB" for SUB receiver id', () => {
      expect(store.receiverLabel('SUB')).toBe('SUB');
    });

    it('is independent of vfoScheme', () => {
      store.setCapabilities(makeCaps({ vfoScheme: 'ab' }));
      expect(store.receiverLabel('MAIN')).toBe('MAIN');
      expect(store.receiverLabel('SUB')).toBe('SUB');
    });
  });

  describe('vfoSlotLabel', () => {
    it('returns "VFO A" for slot A', () => {
      expect(store.vfoSlotLabel('A')).toBe('VFO A');
    });

    it('returns "VFO B" for slot B', () => {
      expect(store.vfoSlotLabel('B')).toBe('VFO B');
    });

    it('is independent of vfoScheme', () => {
      store.setCapabilities(makeCaps({ vfoScheme: 'main_sub' }));
      expect(store.vfoSlotLabel('A')).toBe('VFO A');
      expect(store.vfoSlotLabel('B')).toBe('VFO B');
    });
  });

  describe('vfoLabel (deprecated shim)', () => {
    it('preserves legacy main_sub behaviour', () => {
      store.setCapabilities(makeCaps({ vfoScheme: 'main_sub' }));
      expect(store.vfoLabel('A')).toBe('MAIN');
      expect(store.vfoLabel('B')).toBe('SUB');
    });

    it('preserves legacy ab-scheme behaviour', () => {
      store.setCapabilities(makeCaps({ vfoScheme: 'ab' }));
      expect(store.vfoLabel('A')).toBe('VFO A');
      expect(store.vfoLabel('B')).toBe('VFO B');
    });

    it('emits exactly one console.warn on first call', () => {
      const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
      try {
        store.vfoLabel('A');
        store.vfoLabel('B');
        store.vfoLabel('A');
        expect(warn).toHaveBeenCalledTimes(1);
        expect(warn).toHaveBeenCalledWith(
          '[deprecated] vfoLabel(...) — use receiverLabel/vfoSlotLabel',
        );
      } finally {
        warn.mockRestore();
      }
    });
  });
});
