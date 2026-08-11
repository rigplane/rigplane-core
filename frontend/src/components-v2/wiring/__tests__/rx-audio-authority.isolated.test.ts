import { beforeEach, describe, expect, it, vi } from 'vitest';
import { existsSync } from 'node:fs';

vi.mock('$lib/transport/ws-client', () => ({
  getControlSession: vi.fn(() => ({ state: 'connected', epoch: 1 })),
  onCommandDelivery: vi.fn(() => () => undefined),
  onControlSessionTransition: vi.fn(() => () => undefined),
  sendCommand: vi.fn(),
}));
vi.mock('$lib/runtime/commands/radio-intents', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/commands/radio-intents')>();
  const { sendCommand } = await import('$lib/transport/ws-client');
  return {
    ...actual,
    dispatchRadioIntent: ({ name, params }: { name: string; params: Record<string, unknown> }) => sendCommand(name, params),
  };
});

vi.mock('$lib/stores/radio.svelte', () => ({
  getActiveReceiver: vi.fn(() => null),
  getRadioState: vi.fn(() => ({ active: 'MAIN', main: { afLevel: 0.42 } })),
}));

vi.mock('$lib/stores/capabilities.svelte', () => ({
  getCapabilities: vi.fn(() => ({ capabilities: ['af_level'], receivers: 1, vfoScheme: 'ab' })),
  getControlRange: vi.fn(() => null),
}));

vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    rxEnabled: false,
    setAudioConfig: vi.fn(),
    setRxVolume: vi.fn(),
    startRx: vi.fn(),
    stopRx: vi.fn(),
  },
}));

vi.mock('$lib/stores/audio.svelte', () => ({
  setMuted: vi.fn(),
  setVolume: vi.fn(),
}));

vi.mock('$lib/runtime/frontend-runtime', async () => {
  const { audioManager } = await import('$lib/audio/audio-manager');
  const { setMuted, setVolume } = await import('$lib/stores/audio.svelte');
  return {
    runtime: {
      setRxLive: vi.fn((live: boolean) => {
        if (live) audioManager.startRx();
        else audioManager.stopRx();
      }),
      get rxEnabled() { return audioManager.rxEnabled; },
      setRxVolume: vi.fn((level: number) => { audioManager.setRxVolume(level); }),
      setVolume: vi.fn((level: number) => { setVolume(level); }),
      setMuted: vi.fn((muted: boolean) => { setMuted(muted); }),
    },
  };
});

import { audioManager } from '$lib/audio/audio-manager';
import { setMuted, setVolume } from '$lib/stores/audio.svelte';
import { sendCommand } from '$lib/transport/ws-client';
import { makeRxAudioHandlers as makeRuntimeRxAudioHandlers } from '$lib/runtime/commands/panel-commands';


describe('RX-audio presentation command authority (MOR-1124)', () => {
  beforeEach(() => {
    vi.mocked(sendCommand).mockClear();
    vi.mocked(setMuted).mockClear();
    vi.mocked(setVolume).mockClear();
    vi.mocked(audioManager.startRx).mockClear();
    vi.mocked(audioManager.stopRx).mockClear();
    vi.mocked(audioManager.setRxVolume).mockClear();
    Object.defineProperty(audioManager, 'rxEnabled', { configurable: true, value: false });
  });

  // MOR-1409 A15: the wiring compatibility path is deleted, so "the shim
  // holds no saved-AF state and executes no RX itself" is now provable in its
  // strongest form — the shim does not exist. The dual-path identity check
  // this replaces was vacuous once only one path remained.
  it('leaves no wiring compatibility path for RX audio to re-enter through', () => {
    expect(existsSync('src/components-v2/wiring/command-bus.ts')).toBe(false);
  });

  it('shares one saved AF value across repeated handlers from the sole public path', () => {
    makeRuntimeRxAudioHandlers().onMonitorModeChange('mute');
    makeRuntimeRxAudioHandlers().onMonitorModeChange('radio');

    expect(sendCommand).toHaveBeenNthCalledWith(1, 'set_af_level', { level: 0, receiver: 0 });
    expect(sendCommand).toHaveBeenNthCalledWith(2, 'set_af_level', { level: 0.42, receiver: 0 });
    expect(setMuted).toHaveBeenNthCalledWith(1, true);
    expect(setMuted).toHaveBeenNthCalledWith(2, false);
  });

  it('keeps LIVE start, settled exit stop, and browser-volume semantics on the shared authority', async () => {
    const desktop = makeRuntimeRxAudioHandlers();
    const essentials = makeRuntimeRxAudioHandlers();

    essentials.onMonitorModeChange('live');
    Object.defineProperty(audioManager, 'rxEnabled', { configurable: true, value: true });
    desktop.onAfLevelChange(0.37);
    desktop.onMonitorModeChange('radio');

    expect(audioManager.startRx).toHaveBeenCalledTimes(1);
    await vi.waitFor(
      () => expect(audioManager.stopRx).toHaveBeenCalledTimes(1),
      { timeout: 100, interval: 1 },
    );
    await Promise.resolve();
    await Promise.resolve();
    expect(audioManager.stopRx).toHaveBeenCalledTimes(1);
    expect(audioManager.setRxVolume).toHaveBeenCalledWith(0.37);
    expect(setVolume).toHaveBeenCalledWith(37);
  });
});
