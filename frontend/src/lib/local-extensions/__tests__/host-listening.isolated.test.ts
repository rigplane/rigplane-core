import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mockCapabilities, mockState } from '../../../../tests/e2e/i18n/fixtures';
import type { ServerState } from '$lib/types/state';

const source = vi.hoisted(() => ({
  state: null as ServerState | null,
  caps: null as typeof mockCapabilities | null,
  audio: { rxEnabled: false, muted: false, volume: 35 },
  connectionAudio: true,
  rxEnabled: false,
}));
vi.mock('$lib/runtime/frontend-runtime', () => ({ runtime: source }));
vi.mock('$lib/stores/audio.svelte', () => ({ getAudioState: () => source.audio }));
vi.mock('$lib/stores/connection.svelte', () => ({
  getConnectionStatus: () => 'connected', getRadioPowerOn: () => true,
}));

import { deriveObservedRxAfLevel, deriveRxAudioProps } from '$lib/runtime/adapters/audio-adapter';
import { readListeningSnapshot } from '../host-listening';

beforeEach(() => {
  source.state = { ...mockState, active: 'MAIN', main: { ...mockState.main, afLevel: 0.4 },
    fieldStatus: { 'main.afLevel': { storePath: 'main.afLevel', observed: false,
      freshness: 'unknown', availability: 'missing' } } };
  source.caps = { ...mockCapabilities, receivers: 1, capabilities: ['audio', 'af_level'] };
  source.audio = { rxEnabled: false, muted: false, volume: 35 };
  source.rxEnabled = false;
});

describe('listening uses the audio adapter without changing panel props', () => {
  it('keeps unobserved radio AF null while preserving the panel value', () => {
    expect(deriveRxAudioProps().afLevel).toBe(0.4);
    expect(deriveObservedRxAfLevel()).toBeNull();
    expect(readListeningSnapshot()).toMatchObject({ target: 'radio', level: null });
  });

  it('reports observed radio AF only with the AF capability', () => {
    source.state!.fieldStatus = {};
    expect(deriveObservedRxAfLevel()).toBe(0.4);
    expect(readListeningSnapshot()).toMatchObject({ target: 'radio', level: 0.4 });
    source.caps!.capabilities = ['audio'];
    expect(deriveObservedRxAfLevel()).toBeNull();
    expect(readListeningSnapshot()).toMatchObject({ target: 'radio', level: null });
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
