import { describe, expect, it } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { KnownTxTargetPublic } from '$lib/types/state';
import { getFrequencyPermit, getTxPermit } from '$lib/utils/tx-permit';
import { deriveTxCapabilities, type TxCapabilityInput } from '../tx-capabilities';
import goldenFile from '../../../transport/__tests__/fixtures/state-wire-goldens.json';
// MOR-2467: `main_sub` (this suite's default scheme) is slot-less — the
// default target names MAIN with no A/B slot, the IC-7610's real shape.
const TARGET: KnownTxTargetPublic = {
  status: 'known', receiver: 'MAIN', slot: null, frequencyHz: 150,
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
    // MOR-2467: a `main_sub` target is receiver-level and slot-less.
    ['main_sub', 2, { status: 'known', receiver: 'SUB', slot: null, frequencyHz: 150 }],
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
    // MOR-2467 mixed versions: a legacy A/B slot under `main_sub` is
    // normalized (test below), so the contradiction row is an invalid receiver.
    [{ ...TARGET, receiver: 'BOTH' as unknown as typeof TARGET.receiver }, 'unknown', 'tx-target-unknown'],
  ] as const)('fails %s target closed', (txTarget, targetStatus, permitReason) => {
    const result = derive({}, {
      txTarget: txTarget as TxCapabilityInput['txTarget'],
      modInputSource: { status: 'unknown' },
    });
    expect(result.txTarget.status).toBe(targetStatus);
    expect(result.frequencyPermit).toMatchObject({ status: 'unknown', reason: permitReason });
  });
  it.each([null, 'A', 'B'] as const)(
    'main_sub accepts slot %s and returns a slot-null target', (slot) => {
      expect(derive({}, {
        txTarget: { ...TARGET, slot }, modInputSource: { status: 'unknown' },
      }).txTarget).toEqual({ ...TARGET, slot: null });
    },
  );
  it('still requires an A/B slot under ab, rejecting the slot-less shape', () => {
    expect(derive({ vfoScheme: 'ab', receivers: 1 }, {
      txTarget: { ...TARGET, slot: null }, modInputSource: { status: 'unknown' },
    }).txTarget).toEqual({ status: 'unknown', reason: 'contradiction' });
    expect(derive({ vfoScheme: 'ab', receivers: 1 }, {
      txTarget: { ...TARGET, slot: 'B' }, modInputSource: { status: 'unknown' },
    }).txTarget).toEqual({ ...TARGET, slot: 'B' });
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
    expect(getFrequencyPermit(99, bands)).toEqual(
      { status: 'denied', reason: 'outside-configured-ranges' },
    );
    expect(getFrequencyPermit(150, [])).toEqual(
      { status: 'denied', reason: 'outside-configured-ranges' },
    );
    expect(getFrequencyPermit(150, null)).toEqual(
      { status: 'unknown', reason: 'ranges-unconfigured' },
    );
    expect(getFrequencyPermit(null, bands)).toEqual(
      { status: 'unknown', reason: 'tx-target-unknown' },
    );
    expect(bands).toEqual(before);
  });
  // MOR-3782: the IC-7300 golden is the shipped capabilities payload —
  // its freqRanges cover the 4 m receive range while its txBands declare
  // no 4 m entry, so 70 MHz denies TX by data even though it is tunable.
  it('IC-7300 golden: 4 m (70 MHz) is tunable but not a TX band', () => {
    const { capabilities } = (goldenFile as unknown as Record<string, {
      capabilities: {
        freqRanges: { start: number; end: number }[];
        txBands: { name: string; start: number; end: number }[];
      };
    }>)['IC-7300'];
    const { freqRanges, txBands } = capabilities;

    expect(
      freqRanges.some((range) => range.start <= 70_200_000 && range.end >= 70_200_000),
    ).toBe(true);
    expect(getFrequencyPermit(70_200_000, txBands)).toEqual(
      { status: 'denied', reason: 'outside-configured-ranges' },
    );
    expect(getFrequencyPermit(14_074_000, txBands)).toEqual(
      { status: 'allowed', band: '20m' },
    );
  });
  // MOR-3632: the IC-705 golden is the shipped capabilities payload with
  // the receive-only band rows (2200m/630m/WFM/Air/Gen) flagged rx_only:
  // they stay tunable band-selector entries, but txBands no longer lists
  // them, so a frequency whose only covering band is receive-only denies
  // TX by data. Amateur rows (HF, 2 m, 70 cm) keep their TX permission.
  it('IC-705 golden: WFM/Air/Gen are tunable but not TX bands; 2 m, 70 cm and HF are', () => {
    const { capabilities } = (goldenFile as unknown as Record<string, {
      capabilities: {
        freqRanges: { start: number; end: number }[];
        txBands: { name: string; start: number; end: number }[];
      };
    }>)['IC-705'];
    const { freqRanges, txBands } = capabilities;

    const tunable = (freq: number) =>
      freqRanges.some((range) => range.start <= freq && range.end >= freq);
    // 100 MHz sits in the WFM broadcast row, 121.5 MHz in the Air row, and
    // 29.8 MHz only in the general-coverage Gen row (10 m ends at 29.7 MHz,
    // 6 m starts at 50 MHz) — all three tunable, none TX-permitted.
    expect(tunable(100_000_000)).toBe(true);
    expect(tunable(121_500_000)).toBe(true);
    expect(tunable(29_800_000)).toBe(true);
    for (const freq of [100_000_000, 121_500_000, 29_800_000]) {
      expect(getFrequencyPermit(freq, txBands)).toEqual(
        { status: 'denied', reason: 'outside-configured-ranges' },
      );
    }
    expect(getFrequencyPermit(146_520_000, txBands)).toEqual(
      { status: 'allowed', band: '2m' },
    );
    expect(getFrequencyPermit(446_000_000, txBands)).toEqual(
      { status: 'allowed', band: '70cm' },
    );
    // No HF behaviour change: the 20 m amateur row keeps its TX permission.
    expect(getFrequencyPermit(14_074_000, txBands)).toEqual(
      { status: 'allowed', band: '20m' },
    );
  });
  it.each([
    [150, undefined, 'denied'], [150, null, 'denied'], [150, [], 'denied'],
    [Number.NaN, [{ name: 'test', start: 100, end: 200 }], 'denied'],
    [Infinity, [{ name: 'test', start: 100, end: 200 }], 'denied'],
    [99, [{ name: 'test', start: 100, end: 200 }], 'denied'],
    [100, [{ name: 'test', start: 100, end: 200 }], 'allowed'],
    [200, [{ name: 'test', start: 100, end: 200 }], 'allowed'],
  ] as const)('keeps transitional permit fail-closed at %s Hz', (freq, bands, expected) => {
    expect(getTxPermit(freq, bands)).toBe(expected);
  });
});
