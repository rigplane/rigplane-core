import { describe, expect, it } from 'vitest';

import { getControlRange, setCapabilities } from '$lib/stores/capabilities.svelte';
import type { Capabilities, ControlRange } from '$lib/types/capabilities';
import { controlRangeFromCaps, pbtRangeFromCaps } from './filter-controls';

const LEGACY_PBT: ControlRange = {
  raw_min: 0, raw_max: 255, raw_center: 128, display_min: -1200, display_max: 1200,
};
const LEGACY_NR: ControlRange = { raw_min: 0, raw_max: 255, display_min: 0, display_max: 15 };
const LEGACY_NB: ControlRange = { raw_min: 0, raw_max: 9, display_min: 1, display_max: 10 };

function caps(controls: Record<string, unknown>): Capabilities {
  return {
    model: 'Test Radio', scope: false, audio: false, tx: false, capabilities: [], receivers: 1,
    vfoScheme: 'ab', freqRanges: [], modes: [], filters: [],
    audioConfig: { sampleRate: 48_000, channels: 1, codecs: ['pcm'] },
    webrtc: { available: false, enabled: false }, txBands: null,
    stateContractVersion: 1, providerGeneration: 0,
    controls: controls as Capabilities['controls'],
  };
}

const domainNumbers = {
  raw_min: 0, raw_max: 255, raw_step: 1, raw_origin: 0,
  display_min: -0.125, display_max: 15, display_step: 0.125, display_origin: 0,
  display_unit: 'dB', mapping: 'linear', quantization: 'nearest_ties_down', restoration: 'exact',
};
const domainStrings = {
  ...domainNumbers,
  display_min: '-0.0000000000000000000000000000000000000001',
  display_max: '9'.repeat(400),
  display_step: '0.0000000000000000000000000000000000000001',
  display_origin: '-0.125',
};

describe('legacy numeric control-range compatibility fence (MOR-1724)', () => {
  it('preserves ordinary legacy PBT, NR, and NB ranges', () => {
    const capabilitySet = caps({ pbt_inner: LEGACY_PBT, nr_level: LEGACY_NR, nb_depth: LEGACY_NB });
    expect(pbtRangeFromCaps(capabilitySet)).toEqual({ rawCenter: 128, displayMin: -1200, displayMax: 1200 });
    expect(controlRangeFromCaps('nr_level', capabilitySet)).toEqual({ rawMin: 0, rawMax: 255, displayMin: 0, displayMax: 15 });
    expect(controlRangeFromCaps('nb_depth', capabilitySet)).toEqual({ rawMin: 0, rawMax: 9, displayMin: 1, displayMax: 10 });
  });

  it.each([
    ['legacy-number explicit domain', domainNumbers],
    ['normalized string exact domain', domainStrings],
  ])('fails closed for %s without coercing display values', (_name, domain) => {
    const capabilitySet = caps({ pbt_inner: domain, nr_level: domain, nb_depth: domain });
    expect(pbtRangeFromCaps(capabilitySet)).toBeUndefined();
    expect(controlRangeFromCaps('nr_level', capabilitySet)).toBeUndefined();
    expect(controlRangeFromCaps('nb_depth', capabilitySet)).toBeUndefined();
  });

  it('exposes only undiscriminated ranges through the store accessor', () => {
    expect(setCapabilities(caps({ legacy: LEGACY_NR, exact: domainStrings }))).toBe(true);
    expect(getControlRange('legacy')).toStrictEqual(LEGACY_NR);
    expect(getControlRange('exact')).toBeNull();
  });
});
