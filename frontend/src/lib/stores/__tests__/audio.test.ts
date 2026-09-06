import { describe, it, expect, beforeEach, vi } from 'vitest';

describe('audio store', () => {
  let store: typeof import('../audio.svelte');

  beforeEach(async () => {
    vi.resetModules();
    store = await import('../audio.svelte');
  });

  it('starts with default state', () => {
    const s = store.getAudioState();
    expect(s.rxEnabled).toBe(false);
    expect(s.txEnabled).toBe(false);
    expect(s.volume).toBe(50);
    expect(s.muted).toBe(false);
    expect(s.micEnabled).toBe(false);
    expect(s.bridgeRunning).toBe(false);
  });

  describe('setVolume', () => {
    it('sets volume to a valid value', () => {
      store.setVolume(75);
      expect(store.getAudioState().volume).toBe(75);
    });

    it('clamps to 0 at lower boundary', () => {
      store.setVolume(0);
      expect(store.getAudioState().volume).toBe(0);
    });

    it('clamps negative values to 0', () => {
      store.setVolume(-10);
      expect(store.getAudioState().volume).toBe(0);
    });

    it('clamps to 100 at upper boundary', () => {
      store.setVolume(100);
      expect(store.getAudioState().volume).toBe(100);
    });

    it('clamps values above 100 to 100', () => {
      store.setVolume(150);
      expect(store.getAudioState().volume).toBe(100);
    });

    it('rounds fractional values', () => {
      store.setVolume(42.7);
      expect(store.getAudioState().volume).toBe(43);
    });
  });

  describe('toggleMute', () => {
    it('toggles muted from false to true', () => {
      expect(store.getAudioState().muted).toBe(false);
      store.toggleMute();
      expect(store.getAudioState().muted).toBe(true);
    });

    it('toggles muted back to false', () => {
      store.toggleMute();
      store.toggleMute();
      expect(store.getAudioState().muted).toBe(false);
    });

    it('setMuted forces muted state without toggling', () => {
      store.setMuted(true);
      expect(store.getAudioState().muted).toBe(true);
      store.setMuted(false);
      expect(store.getAudioState().muted).toBe(false);
    });
  });

  describe('setRxEnabled / setTxEnabled', () => {
    it('setRxEnabled updates rxEnabled', () => {
      store.setRxEnabled(true);
      expect(store.getAudioState().rxEnabled).toBe(true);
      store.setRxEnabled(false);
      expect(store.getAudioState().rxEnabled).toBe(false);
    });

    it('setTxEnabled updates txEnabled', () => {
      store.setTxEnabled(true);
      expect(store.getAudioState().txEnabled).toBe(true);
      store.setTxEnabled(false);
      expect(store.getAudioState().txEnabled).toBe(false);
    });
  });

  describe('setBridgeRunning', () => {
    it('setBridgeRunning updates bridgeRunning', () => {
      store.setBridgeRunning(true);
      expect(store.getAudioState().bridgeRunning).toBe(true);
      store.setBridgeRunning(false);
      expect(store.getAudioState().bridgeRunning).toBe(false);
    });
  });

  it('publishes frozen RX target changes synchronously and excludes no-ops and audio noise', () => {
    const seen: Array<{ muted: boolean; rxEnabled: boolean }> = [];
    const unsubscribe = store.subscribeRxAudioTarget((snapshot) => {
      expect(Object.isFrozen(snapshot)).toBe(true);
      seen.push(snapshot);
    });

    expect(seen).toEqual([{ muted: false, rxEnabled: false }]);
    store.setMuted(true);
    expect(seen.at(-1)).toEqual({ muted: true, rxEnabled: false });
    store.setMuted(false);
    expect(seen.at(-1)).toEqual({ muted: false, rxEnabled: false });
    store.setRxEnabled(true);
    expect(seen.at(-1)).toEqual({ muted: false, rxEnabled: true });
    store.setRxEnabled(false);
    expect(seen.at(-1)).toEqual({ muted: false, rxEnabled: false });
    store.toggleMute();
    expect(seen.at(-1)).toEqual({ muted: true, rxEnabled: false });

    const publications = seen.length;
    store.setMuted(true);
    store.setRxEnabled(false);
    store.setVolume(23);
    store.setTxEnabled(true);
    store.setMicEnabled(true);
    store.setBridgeRunning(true);
    store.setTxCodecFallback(true);
    expect(seen).toHaveLength(publications);

    unsubscribe();
    unsubscribe();
    store.setMuted(false);
    expect(seen).toHaveLength(publications);
  });

  it('removes a subscriber whose synchronous initial delivery fails', () => {
    const failure = new Error('initial delivery failed');
    const subscriber = vi.fn(() => { throw failure; });

    expect(() => store.subscribeRxAudioTarget(subscriber)).toThrow(failure);
    store.toggleMute();
    expect(subscriber).toHaveBeenCalledTimes(1);
  });
});
