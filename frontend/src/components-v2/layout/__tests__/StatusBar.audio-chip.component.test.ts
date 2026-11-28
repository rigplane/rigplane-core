import { afterEach, describe, expect, it, vi } from 'vitest';
import { mount, unmount, flushSync, tick } from 'svelte';

vi.mock('$lib/runtime/commands/audio-retry', () => ({ retryAudio: vi.fn() }));

vi.mock('$lib/stores/connection.svelte', () => ({
  getRadioLinkState: vi.fn(() => 'connected'),
  getConnectionStatus: vi.fn(() => 'connected'),
  isAudioConnected: vi.fn(() => false),
  getWsConnected: vi.fn(() => true),
  getRadioPowerOn: vi.fn(() => true),
  getRigConnected: vi.fn(() => true),
  getRadioReady: vi.fn(() => true),
  getRadioHealth: vi.fn(() => null),
}));

vi.mock('$lib/stores/audio.svelte', () => ({
  getAudioState: vi.fn(() => ({
    rxEnabled: false,
    txEnabled: false,
    volume: 50,
    muted: false,
    micEnabled: false,
    bridgeRunning: false,
    txCodecFallback: false,
    rxError: false,
    txError: false,
  })),
}));

vi.mock('$lib/stores/capabilities.svelte', () => ({
  hasAnyScope: vi.fn(() => false),
  hasAudio: vi.fn(() => true),
  hasSpectrum: vi.fn(() => false),
  hasCapability: vi.fn(() => false),
}));

vi.mock('$lib/stores/layout.svelte', () => ({
  getLayoutMode: vi.fn(() => 'standard'),
  setLayoutMode: vi.fn(),
}));

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  getActiveFrequencyHz: vi.fn(() => null),
}));

vi.mock('$lib/runtime', async () => {
  const { radio } = await import('$lib/stores/radio.svelte');
  return {
    runtime: {
      get state() { return radio.current; },
      defaultScopeStatus: {
        source: null,
        available: false,
        resourceSelected: false,
        demand: 0,
        lifecycle: 'inactive',
        transport: 'disconnected',
        frameSeen: false,
      },
      system: {
        disconnect: vi.fn(),
        connect: vi.fn(),
        powerOn: vi.fn(async () => {}),
        powerOff: vi.fn(async () => {}),
        identifyFrequency: vi.fn(async () => null),
      },
      remoteController: false,
      connectionStale: false,
    },
  };
});

import StatusBar from '../StatusBar.svelte';
import { runtime } from '$lib/runtime';
import {
  getRadioPowerOn, isAudioConnected,
} from '$lib/stores/connection.svelte';
import { getAudioState } from '$lib/stores/audio.svelte';
import { radio, resetRadioState } from '$lib/stores/radio.svelte';
import { setLocale } from '$lib/i18n';
import type { LocaleCode } from '$lib/i18n/types';
import type { ServerState } from '$lib/types/state';
import { retryAudio } from '$lib/runtime/commands/audio-retry';

interface AudioScene {
  rxEnabled?: boolean;
  txEnabled?: boolean;
  rxError?: boolean;
  txError?: boolean;
  bridgeRunning?: boolean;
  audioConnected?: boolean;
  powerOn?: boolean;
  remote?: boolean;
}

describe('StatusBar audio chip honesty (MOR-3753)', () => {
  let target: HTMLElement | null = null;
  let instance: object | null = null;

  afterEach(() => {
    if (instance) unmount(instance);
    instance = null;
    target?.remove();
    target = null;
    (runtime as { remoteController: boolean }).remoteController = false;
    resetRadioState();
    setLocale('en-US');
    vi.mocked(retryAudio).mockReset();
  });

  function renderAudioChip(scene: AudioScene = {}): Element {
    vi.mocked(getAudioState).mockReturnValue({
      rxEnabled: scene.rxEnabled ?? false,
      txEnabled: scene.txEnabled ?? false,
      volume: 50,
      muted: false,
      micEnabled: false,
      bridgeRunning: scene.bridgeRunning ?? false,
      txCodecFallback: false,
      rxError: scene.rxError ?? false,
      txError: scene.txError ?? false,
    });
    vi.mocked(isAudioConnected).mockReturnValue(scene.audioConnected ?? false);
    vi.mocked(getRadioPowerOn).mockReturnValue(scene.powerOn ?? true);
    (runtime as { remoteController: boolean }).remoteController = scene.remote ?? false;

    target = document.createElement('div');
    document.body.appendChild(target);
    instance = mount(StatusBar, { target }) as object;
    flushSync();

    const chip = target.querySelector('[role="status"][title^="Audio"]');
    expect(chip, 'expected the audio indicator to render').not.toBeNull();
    return chip!;
  }

  it('[baseline behavior] idle with no browser demand is neutral, not red', () => {
    const chip = renderAudioChip({});
    expect(chip.getAttribute('style')).toContain('#666');
    expect(chip.getAttribute('style')).not.toContain('#ef4444');
  });

  it('[baseline behavior] requested and connected is normal', () => {
    const chip = renderAudioChip({ rxEnabled: true, audioConnected: true });
    expect(chip.getAttribute('style')).toContain('#4ade80');
  });

  it.each<[LocaleCode, string, string]>([
    ['en-US', 'RX audio could not be restored', 'Retry'],
    ['ru-RU', 'Не удалось восстановить звук приёма', 'Повторить'],
    ['ja-JP', '受信音声を復旧できませんでした', '再試行'],
  ])('renders and clears the server restoration failure in %s', (locale, message, retry) => {
    const chip = renderAudioChip({ rxEnabled: true, audioConnected: true });
    expect(target?.querySelector('[data-testid="rx-audio-restore-failed"] button')).toBeNull();
    setLocale(locale);
    radio.current = {
      audioReceive: {
        schemaVersion: 1,
        failure: {
          stage: 'rx_recovery', code: 'core_rx_restore_failed', reason: 'retries_exhausted',
        },
      },
    } as ServerState;
    flushSync();
    expect(target?.textContent).toContain(message);
    const status = target?.querySelector('[data-testid="rx-audio-restore-failed"]');
    expect(status?.querySelectorAll('button')).toHaveLength(1);
    expect(status?.querySelector('button')?.textContent).toBe(retry);
    expect(chip.getAttribute('style')).toContain('#ef4444');

    radio.current = { audioReceive: { schemaVersion: 1, failure: null } } as ServerState;
    flushSync();
    expect(target?.textContent).not.toContain(message);
    expect(target?.querySelector('[data-testid="rx-audio-restore-failed"] button')).toBeNull();
    expect(chip.getAttribute('style')).toContain('#4ade80');
  });

  it('keeps the desktop retry disabled pending its result and displays refusal', async () => {
    let resolve!: (result: Awaited<ReturnType<typeof retryAudio>>) => void;
    vi.mocked(retryAudio).mockImplementation(() => new Promise((done) => { resolve = done; }));
    renderAudioChip();
    radio.current = {
      audioReceive: {
        schemaVersion: 1,
        failure: { stage: 'rx_recovery', code: 'core_rx_restore_failed', reason: 'retries_exhausted' },
      },
    } as ServerState;
    flushSync();
    const retry = target!.querySelector<HTMLButtonElement>('[data-testid="rx-audio-restore-failed"] button')!;
    retry.click(); retry.click(); flushSync();
    expect(retryAudio).toHaveBeenCalledTimes(1);
    expect(retry.disabled).toBe(true);
    resolve({ accepted: false, reason: 'unsupported' });
    await tick();
    expect(retry.disabled).toBe(false);
    expect(target?.textContent).toContain('Audio retry is not supported.');
  });

  it('[baseline behavior] requested but socket absent is an attempt, never a fault', () => {
    const chip = renderAudioChip({ rxEnabled: true, audioConnected: false });
    expect(chip.getAttribute('style')).toContain('#facc15');
    expect(chip.getAttribute('style')).not.toContain('#ef4444');
  });

  it('[new contract] DISCRIMINATING: refusal stays failed/red after demand is cleared', () => {
    // stopRx() already ran (demand false, socket absent) yet rxError is
    // latched: demand-gated wiring must still show the real failure.
    const chip = renderAudioChip({ rxEnabled: false, rxError: true, audioConnected: false });
    expect(chip.getAttribute('style')).toContain('#ef4444');
  });

  it('[baseline behavior] explicit stop after a refusal returns to neutral', () => {
    const chip = renderAudioChip({ rxEnabled: false, rxError: false, audioConnected: false });
    expect(chip.getAttribute('style')).toContain('#666');
    expect(chip.getAttribute('style')).not.toContain('#ef4444');
  });

  it('[new contract] TX failure latches failed/red until an explicit stop', () => {
    const chip = renderAudioChip({ txEnabled: false, txError: true, audioConnected: false });
    expect(chip.getAttribute('style')).toContain('#ef4444');
  });

  it('[baseline behavior] Pro embedding with companion bridge and browser monitoring off is neutral', () => {
    const chip = renderAudioChip({
      bridgeRunning: true,
      rxEnabled: false,
      txEnabled: false,
      audioConnected: false,
      remote: true,
    });
    expect(chip.getAttribute('style')).toContain('#666');
    expect(chip.getAttribute('style')).not.toContain('#ef4444');
  });

  it('[baseline behavior] powered off with no browser demand is neutral, not red', () => {
    const chip = renderAudioChip({ powerOn: false });
    expect(chip.getAttribute('style')).toContain('#666');
    expect(chip.getAttribute('style')).not.toContain('#ef4444');
  });

  it('[new contract] powered off with a latched failure still shows failed/red', () => {
    const chip = renderAudioChip({ powerOn: false, rxEnabled: false, rxError: true });
    expect(chip.getAttribute('style')).toContain('#ef4444');
  });

  it('[new contract] powered off with live demand is unavailable/red', () => {
    const chip = renderAudioChip({ powerOn: false, rxEnabled: true, audioConnected: false });
    expect(chip.getAttribute('style')).toContain('#ef4444');
  });

  it.each<[AudioScene, string]>([
    [{}, 'Audio is off'],
    [{ rxEnabled: true }, 'Audio is connecting…'],
    [{ rxEnabled: true, audioConnected: true }, 'Audio is on'],
    [{ rxError: true }, 'Audio connection failed'],
    [{ powerOn: false, rxEnabled: true }, 'Audio unavailable — the radio is off'],
  ])('[new contract] renders operator-facing tooltip for %j', (scene, title) => {
    expect(renderAudioChip(scene).getAttribute('title')).toBe(title);
  });

});
