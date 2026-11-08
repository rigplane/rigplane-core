import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mockCapabilities, mockState } from '../../../../tests/e2e/i18n/fixtures';
import type { FieldStatus, ServerState } from '$lib/types/state';

const source = vi.hoisted(() => ({
  state: null as ServerState | null,
  caps: null as typeof mockCapabilities | null,
  audio: { rxEnabled: false, muted: false, volume: 35 },
  connectionAudio: true,
  rxEnabled: false,
  controlSession: { state: 'connected' as const, epoch: 7 },
}));
vi.mock('$lib/runtime/frontend-runtime', () => ({ runtime: source }));
vi.mock('$lib/stores/radio.svelte', () => ({
  getRadioState: () => source.state,
  subscribeRadioState: () => () => {},
}));
vi.mock('$lib/stores/audio.svelte', () => ({ getAudioState: () => source.audio }));
vi.mock('$lib/stores/connection.svelte', () => ({
  getConnectionStatus: () => 'connected', getRadioPowerOn: () => true,
}));

import { deriveObservedRxAfLevel, deriveRxAudioProps } from '$lib/runtime/adapters/audio-adapter';
import { readListeningSnapshot } from '../host-listening';
import { getAfLevelControlFeedback } from '$lib/runtime/adapters/panel-adapters';
import { createLocalExtensionHostApi } from '../host-api';

const observed = (storePath: string): FieldStatus => ({
  storePath, observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 1,
});
const host = () => createLocalExtensionHostApi({
  getState: () => source.state, getCapabilities: () => source.caps,
  subscribeState: () => () => {}, dispatchCommand: () => false,
  setKeyboardScope: () => {}, register: () => {},
});

beforeEach(() => {
  source.state = { ...mockState, stateContractVersion: 1, providerGeneration: 3, active: 'MAIN', main: { ...mockState.main, afLevel: 0.4 },
    fieldStatus: { 'main.afLevel': { storePath: 'main.afLevel', observed: false,
      freshness: 'unknown', availability: 'missing' } } };
  source.caps = { ...mockCapabilities, stateContractVersion: 1, providerGeneration: 3, receivers: 1, vfoScheme: 'single', capabilities: ['audio', 'af_level'] };
  source.audio = { rxEnabled: false, muted: false, volume: 35 };
  source.rxEnabled = false;
});

describe('listening uses the audio adapter without changing panel props', () => {
  it('keeps unobserved radio AF null while preserving the panel value', () => {
    expect(deriveRxAudioProps().afLevel).toBe(0.4);
    expect(deriveObservedRxAfLevel()).toBeNull();
    expect(readListeningSnapshot()).toMatchObject({ target: 'radio', level: null });
  });

  it('matches Core AF qualification and refuses writes without observation evidence', () => {
    source.state = { ...source.state!, fieldStatus: {} };
    expect(getAfLevelControlFeedback()).toMatchObject({ availability: 'unavailable', confirmed: null });
    expect(deriveObservedRxAfLevel()).toBeNull();
    expect(host().getListening()).toMatchObject({ target: 'radio', level: null });
    expect(host().setListeningLevel(0.5)).toBe(false);
  });

  it.each([
    [1, 'MAIN', 0.4],
    [2, 'MAIN', 0.4],
    [2, 'SUB', 0.6],
  ] as const)('matches Core observed AF for %i receivers on %s', (receivers, active, level) => {
    source.caps = { ...source.caps!, receivers, vfoScheme: receivers === 1 ? 'single' : 'main_sub',
      capabilities: receivers === 2 ? ['audio', 'af_level', 'dual_rx'] : ['audio', 'af_level'] };
    source.state = { ...source.state!, active, sub: { ...mockState.sub, afLevel: 0.6 },
      fieldStatus: { active: observed('active'),
        'main.afLevel': observed('main.afLevel'), 'sub.afLevel': observed('sub.afLevel') } };
    expect(getAfLevelControlFeedback()).toMatchObject({ availability: 'available', confirmed: level });
    expect(deriveObservedRxAfLevel()).toBe(level);
    expect(host().getListening()).toMatchObject({ target: 'radio', level });
    source.caps = { ...source.caps!, capabilities: ['audio'] };
    expect(getAfLevelControlFeedback().availability).toBe('unavailable');
    expect(deriveObservedRxAfLevel()).toBeNull();
    expect(host().setListeningLevel(0.5)).toBe(false);
  });

  it('keeps playback volume available with unobserved radio AF, including mute teardown', () => {
    source.rxEnabled = true;
    source.audio.rxEnabled = true;
    expect(readListeningSnapshot()).toMatchObject({ mode: 'live', target: 'playback', level: 0.35 });
    source.audio.muted = true;
    expect(readListeningSnapshot()).toMatchObject({ mode: 'mute', target: 'playback', level: 0.35 });
    source.rxEnabled = false;
    source.audio.rxEnabled = false;
    expect(readListeningSnapshot()).toMatchObject({ mode: 'mute', target: 'radio', level: null });
  });
});
