import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ComponentProps } from 'svelte';
import type { ServerState } from '$lib/types/state';
import FrequencyDisplayInteractive from '../../../primitives/frequency/FrequencyDisplayInteractive.svelte';
import { toVfoProps } from '$lib/runtime/props/panel-props';

// This test exercises the REAL `panel-props` projection so the freq fix
// (MOR-475/MOR-1403) is verified end-to-end: click-to-tune steps from the last
// CONFIRMED server frequency, never a local intent (MOR-1409 A09b removed the
// last optimistic machinery; there is no overlay left to drop).
//
// MOR-1409 A15 — authored re-point, and a finding worth recording. This file
// used to project through the deleted `wiring/state-adapter` twin and assert
// `14074000`. That assertion passed for the WRONG reason: `setRadioState()`
// rejects this fixture (it gates on a capability epoch/topology the fixture
// never establishes), so `getRadioState()` was `null` and the twin's `!state`
// branch returned its hard-coded `14074000`/`'USB'`/`'FIL1'` stand-in. The
// test was reading a fabrication while its own comment claimed StateStore
// truth. `panel-props.toVfoProps` is honest — it returns `NaN` for an
// unobserved VFO — which is what exposed this.
//
// The projection is therefore fed the constructed observation directly, so
// the value is genuinely observed rather than fabricated. Restoring a real
// store round-trip needs a capabilities/epoch harness this file never had and
// is out of A15's scope; it is recorded as a follow-up rather than smuggled
// into a closure gate.

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

beforeEach(() => {
  vi.resetModules();
  components = [];
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
});

describe('FrequencyDisplayInteractive click-to-tune over the radio store (MOR-475)', () => {
  it('scroll steps from the last StateStore frequency, never an unconfirmed intent', () => {
    // A causally-newer observation confirming the frequency (analogous to an
    // in-flight poll captured before any local click lands).
    const observed = makeMinimalState({
      revision: 1,
      stateRevision: 1,
      observationSeq: 2,
      freshnessRevision: 2,
      main: { ...makeMinimalState().main, freqHz: 14074000 },
    });

    // The observation is the sole VFO truth seen by the projection — and it is
    // OBSERVED, not fabricated: the honest projection returns `NaN` when it is
    // not, which is exactly what this assertion now distinguishes.
    const vfo = toVfoProps(observed, 'main');
    expect(vfo.freq).toBe(14074000);
    expect(Number.isNaN(toVfoProps(null, 'main').freq)).toBe(true);

    const onFreqChange = vi.fn();
    const t = mountDisplay({ freq: vfo.freq, onFreqChange });

    // Digits in DOM order for 14074000: MHz[1,4] kHz[0,7,4] Hz[0,0,0].
    // The 1 kHz digit (multiplier 1000) remains index 4.
    const digits = Array.from(t.querySelectorAll<HTMLElement>('.digit'));
    const oneKhzDigit = digits[4];
    expect(oneKhzDigit).toBeDefined();

    oneKhzDigit.dispatchEvent(new WheelEvent('wheel', { deltaY: -1, bubbles: true }));
    flushSync();

    // Relative tune steps from confirmed 14074000.
    expect(onFreqChange).toHaveBeenCalledTimes(1);
    expect(onFreqChange).toHaveBeenCalledWith(14075000);
  });
});
