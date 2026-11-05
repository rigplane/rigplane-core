/**
 * MOR-4213 — the Now Playing lookup debounce is armed by a frequency change,
 * not by every state delivery.
 *
 * The status bar's identification effect read the active frequency straight
 * from the radio state, so every delivery (tens per second) re-ran it; its
 * cleanup cleared the 800 ms debounce and it armed a new one. While the radio
 * streamed state the timer never fired (QA trace: 69 timers armed per second,
 * none fired) and the lookup never ran.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

vi.mock('$lib/stores/connection.svelte', () => ({
  getRadioLinkState: vi.fn(() => 'connected'),
  getRadioStatus: vi.fn(() => 'connected'),
  getConnectionStatus: vi.fn(() => 'connected'),
  isAudioConnected: vi.fn(() => false),
  getWsConnected: vi.fn(() => true),
  getRadioPowerOn: vi.fn(() => true),
  getRigConnected: vi.fn(() => true),
  getRadioReady: vi.fn(() => true),
  getRadioHealth: vi.fn(() => null),
}));

vi.mock('$lib/stores/capabilities.svelte', () => ({
  hasAnyScope: vi.fn(() => false),
  hasAudio: vi.fn(() => false),
  hasSpectrum: vi.fn(() => false),
  hasCapability: vi.fn(() => false),
}));

vi.mock('$lib/stores/layout.svelte', () => ({
  getLayoutMode: vi.fn(() => 'standard'),
  setLayoutMode: vi.fn(),
}));

vi.mock('$lib/runtime/adapters/panel-adapters', async () => {
  const { activeFrequencyProbe } = await import('./support/active-frequency-probe.svelte');
  return { getActiveFrequencyHz: () => activeFrequencyProbe.read() };
});

type Identification = { stations: Array<Record<string, string | boolean>> } | null;
const identifyFrequency = vi.hoisted(() => vi.fn(async (_freq: number): Promise<Identification> => null));
vi.mock('$lib/runtime', () => ({
  runtime: {
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
      identifyFrequency,
    },
  },
}));

import StatusBar from '../StatusBar.svelte';
import { activeFrequencyProbe } from './support/active-frequency-probe.svelte';

describe('StatusBar Now Playing debounce (MOR-4213)', () => {
  let target: HTMLElement | null = null;
  let instance: object | null = null;

  beforeEach(() => {
    vi.useFakeTimers();
    identifyFrequency.mockClear();
    activeFrequencyProbe.frequencyHz = 14_074_000;
  });

  afterEach(() => {
    if (instance) unmount(instance);
    instance = null;
    target?.remove();
    target = null;
    vi.useRealTimers();
  });

  function mountBar(): void {
    target = document.createElement('div');
    document.body.appendChild(target);
    instance = mount(StatusBar, { target }) as object;
    flushSync();
  }

  it('state deliveries with an unchanged frequency do not re-arm the debounce', () => {
    mountBar();
    const armed = vi.spyOn(globalThis, 'setTimeout');
    // 40 deliveries over 1 s, the measured state rate, frequency unchanged.
    for (let i = 0; i < 40; i += 1) {
      activeFrequencyProbe.deliver();
      flushSync();
      vi.advanceTimersByTime(25);
    }
    const identifyArms = armed.mock.calls.filter(([, delay]) => delay === 800);
    armed.mockRestore();
    expect(identifyArms).toHaveLength(0);
    // The debounce armed at mount fired 800 ms in, while deliveries kept coming.
    expect(identifyFrequency).toHaveBeenCalledTimes(1);
    expect(identifyFrequency).toHaveBeenCalledWith(14_074_000);
  });

  it('a frequency change still restarts the debounce', () => {
    mountBar();
    vi.advanceTimersByTime(400);
    activeFrequencyProbe.frequencyHz = 7_074_000;
    activeFrequencyProbe.deliver();
    flushSync();
    vi.advanceTimersByTime(799);
    expect(identifyFrequency).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(identifyFrequency).toHaveBeenCalledTimes(1);
    expect(identifyFrequency).toHaveBeenCalledWith(7_074_000);
  });

  it('a lookup skipped while the popup is open runs for the current frequency once it closes', async () => {
    const station = (name: string) => ({
      station: name, city: '', state: '', language_name: 'English', country: 'XX', target: 'XX',
      on_air: false, freq_khz: '0', time_str: 'local', days: '', band: '40m', remarks: '', source: '',
    });
    identifyFrequency.mockResolvedValueOnce({ stations: [station('Station A')] })
      .mockResolvedValueOnce({ stations: [station('Station B')] });
    mountBar();
    await vi.advanceTimersByTimeAsync(800);
    flushSync();
    expect(identifyFrequency).toHaveBeenCalledTimes(1);
    target!.querySelector<HTMLButtonElement>('button.now-playing')!.click();
    flushSync();
    expect(target!.querySelector('.np-detail')).not.toBeNull();

    // Tuned elsewhere while the popup is open: the debounce expires, deliveries keep coming.
    activeFrequencyProbe.frequencyHz = 7_074_000;
    activeFrequencyProbe.deliver();
    flushSync();
    for (let i = 0; i < 40; i += 1) {
      await vi.advanceTimersByTimeAsync(25);
      activeFrequencyProbe.deliver();
      flushSync();
    }
    expect(identifyFrequency).toHaveBeenCalledTimes(1);

    target!.querySelector<HTMLButtonElement>('button.np-close')!.click();
    flushSync();
    for (let i = 0; i < 40; i += 1) {
      await vi.advanceTimersByTimeAsync(25);
      activeFrequencyProbe.deliver();
      flushSync();
    }
    expect(identifyFrequency).toHaveBeenCalledTimes(2);
    expect(identifyFrequency).toHaveBeenLastCalledWith(7_074_000);
    expect(target!.querySelector('.np-station')?.textContent).toBe('Station B');
  });
});
