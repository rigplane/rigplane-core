import { beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';

vi.mock('$lib/transport/ws-client', () => ({
  sendCommand: vi.fn(),
}));
vi.mock('$lib/runtime/commands/radio-intents', async () => {
  const { sendCommand } = await import('$lib/transport/ws-client');
  return { dispatchRadioIntent: ({ name, params }: { name: string; params: Record<string, unknown> }) => sendCommand(name, params) };
});

vi.mock('$lib/stores/radio.svelte', () => ({
  getActiveReceiver: vi.fn(() => null),
  getRadioState: vi.fn(() => ({ active: 'MAIN', main: { afLevel: 0.42 } })),
  patchActiveReceiver: vi.fn(),
  patchRadioState: vi.fn(),
  patchReceiver: vi.fn(),
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
import { patchActiveReceiver } from '$lib/stores/radio.svelte';
import { sendCommand } from '$lib/transport/ws-client';
import { makeRxAudioHandlers as makeRuntimeRxAudioHandlers } from '$lib/runtime/commands/panel-commands';
import { makeRxAudioHandlers as makeWiringRxAudioHandlers } from '../command-bus';

const commandBusSource = readFileSync('src/components-v2/wiring/command-bus.ts', 'utf8');

describe('RX-audio presentation command authority (MOR-1124)', () => {
  beforeEach(() => {
    vi.mocked(sendCommand).mockClear();
    vi.mocked(patchActiveReceiver).mockClear();
    vi.mocked(setMuted).mockClear();
    vi.mocked(setVolume).mockClear();
    vi.mocked(audioManager.startRx).mockClear();
    vi.mocked(audioManager.stopRx).mockClear();
    vi.mocked(audioManager.setRxVolume).mockClear();
    Object.defineProperty(audioManager, 'rxEnabled', { configurable: true, value: false });
  });

  it('exports the runtime factory through the wiring compatibility path', () => {
    expect(makeWiringRxAudioHandlers).toBe(makeRuntimeRxAudioHandlers);
  });

  it('keeps no local saved-AF state or direct RX execution calls in command-bus', () => {
    expect(commandBusSource).not.toMatch(/\bsavedAfLevel\b/);
    expect(commandBusSource).not.toMatch(/audioManager\.(startRx|stopRx)\(/);
  });

  it('shares one saved AF value across repeated handlers from both public paths', () => {
    makeWiringRxAudioHandlers().onMonitorModeChange('mute');
    makeRuntimeRxAudioHandlers().onMonitorModeChange('radio');

    expect(sendCommand).toHaveBeenNthCalledWith(1, 'set_af_level', { level: 0, receiver: 0 });
    expect(sendCommand).toHaveBeenNthCalledWith(2, 'set_af_level', { level: 0.42, receiver: 0 });
    expect(patchActiveReceiver).toHaveBeenCalledWith({ afLevel: 0.42 }, true);
    expect(setMuted).toHaveBeenNthCalledWith(1, true);
    expect(setMuted).toHaveBeenNthCalledWith(2, false);
  });

  it('keeps LIVE start, settled exit stop, and browser-volume semantics on the shared authority', async () => {
    const desktop = makeWiringRxAudioHandlers();
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
