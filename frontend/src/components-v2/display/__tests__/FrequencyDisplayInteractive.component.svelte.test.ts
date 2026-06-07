import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ComponentProps } from 'svelte';
import type { ServerState } from '$lib/types/state';
import FrequencyDisplayInteractive from '../FrequencyDisplayInteractive.svelte';
import { toVfoProps } from '../../wiring/state-adapter';

// This test exercises the REAL radio store + REAL state-adapter so the freq
// fix (MOR-475) is verified end-to-end: an in-flight causally-newer STALE poll
// must NOT drop the unlocked optimistic overlay, so click-to-tune steps from the
// COMMANDED freq, not the stale server value (no flash). We import the real store
// dynamically per test to reset its module-level optimistic/lock maps between cases.

let store: typeof import('$lib/stores/radio.svelte');

function makeMinimalState(overrides: Partial<ServerState> = {}): ServerState {
  const revision = overrides.stateRevision ?? overrides.revision ?? 1;
  return {
    revision,
    stateRevision: revision,
    freshnessRevision: overrides.freshnessRevision ?? 1,
    observationSeq: overrides.observationSeq ?? revision,
    updatedAt: '2026-03-07T00:00:00Z',
    active: 'MAIN',
    ptt: false,
    split: false,
    dualWatch: false,
    tunerStatus: 0,
    main: {
      freqHz: 14074000,
      mode: 'USB',
      filter: 1,
      dataMode: 0,
      sMeter: 50,
      att: 0,
      preamp: 0,
      nb: false,
      nr: false,
      afLevel: 100,
      rfGain: 255,
      squelch: 0,
      ...(overrides.main ?? {}),
    },
    sub: {
      freqHz: 7100000,
      mode: 'LSB',
      filter: 2,
      dataMode: 0,
      sMeter: 20,
      att: 0,
      preamp: 0,
      nb: false,
      nr: false,
      afLevel: 80,
      rfGain: 255,
      squelch: 0,
      ...(overrides.sub ?? {}),
    },
    connection: { rigConnected: true, radioReady: true, controlConnected: true },
    ...overrides,
  } as ServerState;
}

let components: ReturnType<typeof mount>[] = [];

function mountDisplay(props: ComponentProps<typeof FrequencyDisplayInteractive>): HTMLElement {
  const t = document.createElement('div');
  document.body.appendChild(t);
  components.push(mount(FrequencyDisplayInteractive, { target: t, props }));
  flushSync();
  return t;
}

beforeEach(async () => {
  vi.resetModules();
  store = await import('$lib/stores/radio.svelte');
  components = [];
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
});

describe('FrequencyDisplayInteractive click-to-tune over the radio store (MOR-475)', () => {
  it('scroll after an in-flight stale causal poll steps from the COMMANDED freq, not the stale server value', () => {
    // Initial server freq.
    store.setRadioState(makeMinimalState({
      revision: 1,
      stateRevision: 1,
      observationSeq: 1,
      freshnessRevision: 1,
      main: { ...makeMinimalState().main, freqHz: 14074000 },
    }));

    // Unlocked optimistic patch (click-to-tune) to 14100000.
    store.patchActiveReceiver({ freqHz: 14100000 });

    // An in-flight poll captured before the click lands: causally newer
    // (observationSeq + freshnessRevision advance) but still the OLD freq.
    store.setRadioState(makeMinimalState({
      revision: 1,
      stateRevision: 1,
      observationSeq: 2,
      freshnessRevision: 2,
      main: { ...makeMinimalState().main, freqHz: 14074000 },
    }));

    // Drive the prop through the real adapter — the overlay must SURVIVE the
    // stale poll so this reflects the commanded freq (14100000), no flash.
    const vfo = toVfoProps(store.getRadioState(), 'main');
    expect(vfo.freq).toBe(14100000);

    const onFreqChange = vi.fn();
    const t = mountDisplay({ freq: vfo.freq, onFreqChange });

    // Digits in DOM order for 14100000: MHz[1,4] kHz[1,0,0] Hz[0,0,0].
    // The 1 kHz digit (multiplier 1000) is the 5th .digit (index 4) — re-derived
    // against splitFrequencyToDigits/groupDigitsForDisplay for 14100000.
    const digits = Array.from(t.querySelectorAll<HTMLElement>('.digit'));
    const oneKhzDigit = digits[4];
    expect(oneKhzDigit).toBeDefined();

    oneKhzDigit.dispatchEvent(new WheelEvent('wheel', { deltaY: -1, bubbles: true }));
    flushSync();

    // Relative tune steps from the commanded 14100000, not the stale 14074000.
    expect(onFreqChange).toHaveBeenCalledTimes(1);
    expect(onFreqChange).toHaveBeenCalledWith(14101000);
    expect(onFreqChange).not.toHaveBeenCalledWith(14075000);
  });
});
