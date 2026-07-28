import { describe, expect, it } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { KnownTxTargetPublic } from '$lib/types/state';
import { getFrequencyPermit } from '$lib/utils/tx-permit';
import {
  deriveTxCapabilities,
  type TxCapabilityInput,
} from '../tx-capabilities';
const TARGET: KnownTxTargetPublic = {
  status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 150,
};
function caps(overrides: Partial<Capabilities> = {}): Capabilities {
  return {
    model: 'fixture', scope: false, audio: true, tx: true,
    audioTx: true, audioTxRoute: 'lan', audioTxRequiredModInputSource: 5,
    capabilities: ['audio', 'tx', 'mod_input_routing'],
    receivers: 2, vfoScheme: 'main_sub', freqRanges: [], modes: [], filters: [],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
    webrtc: { available: false, enabled: false },
    txBands: [{ name: 'test', start: 100, end: 200 }],
    ...overrides,
  };
}
function derive(
  overrides: Partial<Capabilities> = {},
  input: TxCapabilityInput = {
    txTarget: TARGET, modInputSource: { status: 'known', source: 5 },
  },
) {
  return deriveTxCapabilities(caps(overrides), input);
}
describe('deriveTxCapabilities', () => {
  it.each([
    [true, ['tx'], true],
    [false, [], false],
    [true, [], false],
    [false, ['tx'], false],
  ] as const)('requires canonical tx=%s/tag agreement', (tx, capabilities, expected) => {
    expect(derive({ tx, capabilities: [...capabilities] })).toMatchObject({
      catPttAvailable: expected, browserTxAudioAvailable: expected,
    });
  });

  it('keeps browser audio, native voice, and MOD routing independent', () => {
    const present = derive({ capabilities: ['audio', 'tx', 'voice_tx', 'mod_input_routing'] });
    expect(present).toMatchObject({
      catPttAvailable: true, browserTxAudioAvailable: true,
      nativeVoiceTxAvailable: true, modInputRoutingAvailable: true,
    });
    const absentAudioTx = derive({
      audioTx: undefined, audioTxRoute: undefined,
      audioTxRequiredModInputSource: undefined,
      capabilities: ['audio', 'tx', 'voice_tx'],
    });
    expect(absentAudioTx).toMatchObject({
      catPttAvailable: true, browserTxAudioAvailable: false,
      nativeVoiceTxAvailable: true, modInputRoutingAvailable: false,
      modInputReadiness: { status: 'not-applicable' },
    });
  });

  it.each([
    ['single', 1, { status: 'known', receiver: 'MAIN', slot: null, frequencyHz: 150 }],
    ['ab', 1, { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 150 }],
    ['ab_shared', 2, { status: 'known', receiver: 'SUB', slot: null, frequencyHz: 150 }],
    ['main_sub', 2, { status: 'known', receiver: 'SUB', slot: 'B', frequencyHz: 150 }],
  ] as const)('accepts explicit %s target identity', (vfoScheme, receivers, txTarget) => {
    expect(derive({ vfoScheme, receivers }, {
      txTarget, modInputSource: { status: 'unknown' },
    }).txTarget).toEqual(txTarget);
  });

  it.each([
    [null, { status: 'known', source: 5 }, 'not-applicable'],
    [5, { status: 'unknown' }, 'unknown'],
    [5, { status: 'known', source: 5 }, 'ready'],
    [5, { status: 'known', source: 2 }, 'mismatch'],
  ] as const)('derives required source %s readiness as %s', (required, modInputSource, status) => {
    expect(derive(
      { audioTxRequiredModInputSource: required },
      { txTarget: TARGET, modInputSource },
    ).modInputReadiness.status).toBe(status);
  });

  it.each([
    [{ status: 'unknown', reason: 'stale' }, 'unknown', 'tx-target-unknown'],
    [{ ...TARGET, frequencyHz: null }, 'known', 'tx-target-unknown'],
    [{ ...TARGET, slot: null }, 'unknown', 'tx-target-unknown'],
  ] as const)('fails %s target closed', (txTarget, targetStatus, permitReason) => {
    const result = derive({}, {
      txTarget: txTarget as TxCapabilityInput['txTarget'],
      modInputSource: { status: 'unknown' },
    });
    expect(result.txTarget.status).toBe(targetStatus);
    expect(result.frequencyPermit).toMatchObject({ status: 'unknown', reason: permitReason });
  });

  it('does not mutate capability, target, or band inputs', () => {
    const capabilities = caps();
    const input: TxCapabilityInput = {
      txTarget: structuredClone(TARGET),
      modInputSource: { status: 'known', source: 5 },
    };
    const before = structuredClone({ capabilities, input });
    deriveTxCapabilities(capabilities, input);
    expect({ capabilities, input }).toEqual(before);
  });
});

describe('getFrequencyPermit', () => {
  it('distinguishes inclusive ranges, deny-all, and unknown inputs without mutation', () => {
    const bands = [{ name: 'test', start: 100, end: 200 }];
    const before = structuredClone(bands);
    expect(getFrequencyPermit(100, bands)).toEqual({ status: 'allowed', band: 'test' });
    expect(getFrequencyPermit(200, bands)).toEqual({ status: 'allowed', band: 'test' });
    expect(getFrequencyPermit(99, bands)).toEqual({
      status: 'denied', reason: 'outside-configured-ranges',
    });
    expect(getFrequencyPermit(150, [])).toEqual({
      status: 'denied', reason: 'outside-configured-ranges',
    });
    expect(getFrequencyPermit(150, null)).toEqual({
      status: 'unknown', reason: 'ranges-unconfigured',
    });
    expect(getFrequencyPermit(null, bands)).toEqual({
      status: 'unknown', reason: 'tx-target-unknown',
    });
    expect(bands).toEqual(before);
  });
});
