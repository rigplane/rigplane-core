import { describe, expect, it } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import { derivePresentationCapabilities } from '../presentation-capabilities';

function caps(overrides: Partial<Capabilities> = {}): Capabilities {
  return {
    model: 'fixture', scope: true, audio: true, tx: true,
    capabilities: ['scope', 'audio', 'tx', 'dual_rx'],
    receivers: 2, vfoScheme: 'main_sub', freqRanges: [], modes: [], filters: [],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
    webrtc: { available: false, enabled: false }, txBands: null,
    scopeSource: 'hardware', audioFftAvailable: true, ...overrides,
  };
}

describe('derivePresentationCapabilities', () => {
  it.each([
    ['single', 1, ['MAIN'], { MAIN: null }],
    ['ab', 1, ['MAIN'], { MAIN: ['A', 'B'] }],
    ['ab_shared', 2, ['MAIN', 'SUB'], { MAIN: null, SUB: null }],
    ['main_sub', 2, ['MAIN', 'SUB'], { MAIN: ['A', 'B'], SUB: ['A', 'B'] }],
  ] as const)('maps %s topology exactly', (scheme, receivers, structuralReceivers, slots) => {
    const capabilities = scheme === 'single' || scheme === 'ab'
      ? ['scope', 'audio', 'tx']
      : ['scope', 'audio', 'tx', 'dual_rx'];
    expect(derivePresentationCapabilities(caps({
      vfoScheme: scheme, receivers, capabilities,
    })).topology).toEqual({
      scheme, structuralCount: receivers, structuralReceivers,
      operationalReceivers: structuralReceivers, slots,
    });
  });

  it('keeps structural dual RX while failing operational SUB closed', () => {
    const result = derivePresentationCapabilities(caps({
      capabilities: ['scope', 'audio', 'tx'],
    }));
    expect(result.topology).toMatchObject({
      structuralCount: 2,
      structuralReceivers: ['MAIN', 'SUB'],
      operationalReceivers: ['MAIN'],
    });
    expect(result.diagnostics).toContain('dual-rx-unavailable');
  });

  it('ignores contradictory dual_rx on a one-receiver topology', () => {
    const result = derivePresentationCapabilities(caps({
      vfoScheme: 'ab', receivers: 1,
    }));
    expect(result.topology?.operationalReceivers).toEqual(['MAIN']);
    expect(result.diagnostics).toContain('dual-rx-contradiction');
  });

  it.each([
    ['single', 2], ['ab', 2], ['ab_shared', 1], ['main_sub', 1],
    ['unknown', 1], ['single', 0],
  ])('fails closed for malformed topology %s/%s', (scheme, receivers) => {
    const result = derivePresentationCapabilities(caps({
      vfoScheme: scheme as Capabilities['vfoScheme'], receivers,
    }));
    expect(result.topology).toBeNull();
    expect(result.diagnostics).toContain('invalid-topology');
  });

  it.each([
    [false, false, null, []],
    [true, false, 'hardware', ['hardware']],
    [false, true, 'audio_fft', ['audio_fft']],
    [true, true, 'hardware', ['hardware', 'audio_fft']],
  ] as const)('keeps hardware=%s and FFT=%s independent', (scope, fft, source, sources) => {
    const tags = [scope && 'scope', 'audio', 'tx'].filter(Boolean) as string[];
    const result = derivePresentationCapabilities(caps({
      scope, audioFftAvailable: fft, scopeSource: source, capabilities: tags,
    }));
    expect(result.scope).toEqual({
      hardwareScopeAvailable: scope,
      audioFftAvailable: fft,
      availableSources: sources,
      defaultSource: source,
    });
  });

  it('fails scope and audio contradictions closed without inventing a default', () => {
    const result = derivePresentationCapabilities(caps({
      scope: true, audio: false, capabilities: ['audio'], scopeSource: 'hardware',
    }));
    expect(result.scope).toEqual({
      hardwareScopeAvailable: false, audioFftAvailable: false,
      availableSources: [], defaultSource: null,
    });
    expect(result.diagnostics).toEqual(expect.arrayContaining([
      'scope-capability-contradiction', 'audio-capability-contradiction',
      'audio-fft-without-audio', 'invalid-scope-source',
    ]));
  });

  it('does not mutate or return the raw capability object', () => {
    const input = caps({ extensionFact: { preserved: true } });
    const before = structuredClone(input);
    const result = derivePresentationCapabilities(input);
    expect(input).toEqual(before);
    expect(result).not.toBe(input);
    expect(result).not.toHaveProperty('extensionFact');
  });
});
