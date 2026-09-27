/**
 * MOR-2792 — the RX audio surface tells the operator when the server
 * receives only digital silence from the radio.
 *
 * Pins the literal English copy (not `t(key)`), so a locale/ICU regression
 * that drops the operator-facing sentence fails here.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import Fixture from './fixtures/RxAudioInstrumentHostFixture.svelte';
import { topologyFixtures, withRxAudio } from '../fixtures/topologies';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { RadioViewModel, RxAudioViewModel } from '../radio-view-model';
import type { RxAudioAuthorityPublication } from '../rx-audio-instruments';

const SILENCE_TEXT =
  "No audio from the radio: the server receives only digital silence. On macOS, give Microphone permission to the program that starts the server, or check the radio's USB audio output.";

const AUTHORITY_STATE = { providerGeneration: 1 } as ServerState;
const AUTHORITY_CAPS = {
  model: 'TEST', receivers: 1, vfoScheme: 'single', providerGeneration: 1,
  capabilities: ['audio', 'af_level'], scope: false, audio: true, tx: false,
  freqRanges: [], modes: [], filters: [],
  audioConfig: { sampleRate: 48_000, channels: 1, codecs: [] },
  webrtc: { available: false, enabled: false }, txBands: null, stateContractVersion: 1,
} as Capabilities;

function viewWith(over: Partial<RxAudioViewModel>): RadioViewModel {
  const base = withRxAudio(topologyFixtures['1/single']);
  return { ...base, rxAudio: { ...base.rxAudio!, ...over } };
}

function render(view: RadioViewModel) {
  const publication: RxAudioAuthorityPublication = {
    state: AUTHORITY_STATE,
    caps: AUTHORITY_CAPS,
    session: { state: 'connected', epoch: 1 },
    rxAudioTarget: { muted: false, rxEnabled: true },
  };
  const component = mount(Fixture, {
    target,
    props: {
      view,
      publication,
      subscribeControlAuthority: (handler) => {
        handler(publication);
        return () => undefined;
      },
    },
  });
  flushSync();
  return {
    dispose: () => unmount(component),
    text: () => target.textContent ?? '',
  };
}

let target: HTMLDivElement;
beforeEach(() => {
  target = document.createElement('div');
  document.body.appendChild(target);
});
afterEach(() => target.remove());

describe('MOR-2792 digital-silence notice on the RX audio surface', () => {
  it('shows the literal English sentence while the payload says silent', () => {
    const r = render(viewWith({ rxSilent: true }));
    expect(r.text()).toContain(SILENCE_TEXT);
    r.dispose();
  });

  it('shows nothing about silence when the payload does not say silent', () => {
    const r = render(viewWith({ rxSilent: false }));
    expect(r.text()).not.toContain(SILENCE_TEXT);
    expect(r.text()).not.toContain('digital silence');
    r.dispose();
  });
});
