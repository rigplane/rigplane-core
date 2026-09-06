import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';

const h = vi.hoisted(() => ({
  session: { state: 'disconnected' as 'connected' | 'disconnected', epoch: -1 },
}));

// The runtime facade is the only outer mock. Its state and capability getters
// read the real reactive stores; controlSession deliberately remains plain,
// matching the production getter rather than creating a test-only dependency.
vi.mock('$lib/runtime/frontend-runtime', async () => {
  const radio = await import('$lib/stores/radio.svelte');
  const capabilities = await import('$lib/stores/capabilities.svelte');
  return { runtime: {
    get state() { return radio.getRadioState(); },
    get caps() { return capabilities.getCapabilities(); },
    get controlSession() { return h.session; },
  } };
});
vi.mock('$lib/runtime/commands/radio-intents', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/runtime/commands/radio-intents')>(),
  currentControlSessionEpoch: () => h.session.epoch,
}));

import TxAuxFeedbackDerivedProbe from './support/TxAuxFeedbackDerivedProbe.svelte';
import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';
import { resetRadioState, setRadioState } from '$lib/stores/radio.svelte';
import {
  acknowledgeCommand, beginCommand, resetCommandLifecycle,
} from '$lib/stores/commands.svelte';

const receiver = {
  freqHz: 14_200_000, mode: 'USB', filter: 1, dataMode: 0, sMeter: 0,
  att: 0, preamp: 0, nb: false, nr: false, afLevel: 0.5, rfGain: 0.5, squelch: 0,
};
const fresh = (marker: number) => ({
  storePath: 'fixture', observed: true, freshness: 'fresh' as const,
  availability: 'available' as const, lastObservedMonotonic: marker,
});
function capabilities(providerGeneration: number): Capabilities {
  return {
    stateContractVersion: 1, providerGeneration, receivers: 1,
    capabilities: ['tx'],
  } as unknown as Capabilities;
}
function radioState(
  providerGeneration: number, revision: number, micGain: number, marker: number,
): ServerState {
  return {
    stateContractVersion: 1, providerGeneration,
    revision, stateRevision: revision, freshnessRevision: revision, observationSeq: revision,
    updatedAt: `2026-09-06T14:00:0${revision}Z`, active: 'MAIN',
    ptt: false, split: false, dualWatch: false, tunerStatus: 0,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: receiver.freqHz },
    main: { ...receiver },
    connection: { rigConnected: true, radioReady: true, controlConnected: true },
    micGain,
    fieldStatus: { micGain: fresh(marker) },
  } as unknown as ServerState;
}

let component: ReturnType<typeof mount> | null = null;
let target: HTMLDivElement | null = null;
function render(): HTMLOutputElement {
  target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(TxAuxFeedbackDerivedProbe, { target });
  flushSync();
  const output = target.querySelector('output');
  if (!(output instanceof HTMLOutputElement)) throw new Error('TX/VOX probe did not mount');
  return output;
}

afterEach(() => {
  if (component !== null) void unmount(component);
  component = null; target?.remove(); target = null;
  resetCommandLifecycle(); resetRadioState(); clearCapabilities();
  h.session = { state: 'disconnected', epoch: -1 };
});

describe('mounted raw TX/VOX feedback recovery', () => {
  it('reactively recovers one persistent derived consumer across lifecycle and authority replacement', () => {
    resetCommandLifecycle(); resetRadioState(); clearCapabilities();
    h.session = { state: 'disconnected', epoch: -1 };
    const probe = render();
    expect(probe.dataset).toMatchObject({
      phase: 'unavailable', availability: 'unavailable', confirmed: '',
      target: '', outcome: '', provider: '', session: '-1',
    });

    h.session = { state: 'connected', epoch: 7 };
    flushSync();
    expect(probe.dataset.phase).toBe('unavailable');
    expect(setCapabilities(capabilities(3))).toBe(true);
    expect(setRadioState(radioState(3, 1, 120, 1))).toBe(true);
    flushSync();
    expect(probe.dataset).toMatchObject({
      phase: 'idle', availability: 'available', confirmed: '120',
      target: '', provider: '3', session: '7',
    });

    const command = beginCommand({
      id: 'mic-7', name: 'set_mic_gain', params: { level: 128 }, originalEpoch: 7,
    });
    flushSync();
    expect(probe.dataset).toMatchObject({ phase: 'submitted', confirmed: '120', target: '128' });

    acknowledgeCommand(command.id, 7, 7);
    flushSync();
    expect(probe.dataset).toMatchObject({
      phase: 'awaiting-confirmation', confirmed: '120', target: '128', outcome: '',
    });

    expect(setRadioState(radioState(3, 2, 127, 2))).toBe(true);
    flushSync();
    expect(probe.dataset.phase).toBe('awaiting-confirmation');
    expect(setRadioState(radioState(3, 3, 128, 3))).toBe(true);
    flushSync();
    expect(probe.dataset).toMatchObject({
      phase: 'confirmed', confirmed: '128', target: '', outcome: 'confirmed',
    });

    h.session = { state: 'disconnected', epoch: 8 };
    flushSync();
    expect(probe.dataset.phase).toBe('confirmed');
    resetRadioState(); clearCapabilities();
    flushSync();
    expect(probe.dataset).toMatchObject({
      phase: 'unavailable', availability: 'unavailable', confirmed: '',
      provider: '', session: '8',
    });

    h.session = { state: 'connected', epoch: 8 };
    expect(setCapabilities(capabilities(4))).toBe(true);
    expect(setRadioState(radioState(4, 1, 130, 1))).toBe(true);
    flushSync();
    expect(probe.dataset).toMatchObject({
      phase: 'idle', availability: 'available', confirmed: '130',
      target: '', outcome: '', provider: '4', session: '8',
    });

    beginCommand({
      id: 'mic-8', name: 'set_mic_gain', params: { level: 140 }, originalEpoch: 8,
    });
    flushSync();
    expect(probe.dataset).toMatchObject({
      phase: 'submitted', confirmed: '130', target: '140',
      provider: '4', session: '8',
    });
    expect(target!.querySelector('output')).toBe(probe);
  });
});
