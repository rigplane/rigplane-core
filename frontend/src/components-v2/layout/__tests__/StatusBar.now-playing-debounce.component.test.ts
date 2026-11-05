/**
 * MOR-4213 — the Now Playing lookup debounce is armed by a frequency change,
 * not by every state delivery.
 *
 * The status bar's identification effect read the active frequency straight
 * from the radio state, so every delivery (tens per second) re-ran it; its
 * cleanup cleared the 800 ms debounce and it armed a new one. While the radio
 * streamed state the timer never fired (QA trace: 69 timers armed per second,
 * none fired) and the lookup never ran.
 *
 * The lookup goes through the REAL `SystemController` and its HTTP request;
 * only the network is faked. Its replies are the producer's own documents:
 * Core's `EiBiStation.to_dict()` wrapped the way the `/api/v1/eibi/identify`
 * route wraps them (`web_routing.py`).
 */
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { EibiResult } from '$lib/runtime/system-controller';

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

vi.mock('$lib/runtime', async () => {
  const { SystemController } = await import('$lib/runtime/system-controller');
  return {
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
      system: new SystemController(),
    },
  };
});

import StatusBar from '../StatusBar.svelte';
import { activeFrequencyProbe } from './support/active-frequency-probe.svelte';

const STATION_A_HZ = 14_074_000;
const STATION_B_HZ = 7_074_000;

/** `{"stations": [...], "freq_hz": f}` per frequency, built by Core itself. */
let identifyReplies: Record<number, EibiResult>;

beforeAll(() => {
  const script = [
    'import json',
    'from rigplane.web.eibi import EiBiStation',
    'def station(khz, name):',
    '    return EiBiStation(freq_khz=khz, time_start=0, time_end=2400, days="", country="USA",',
    '        station=name, language="E", target="NAm", remarks="", persistence=1,',
    '        start_date="", end_date="").to_dict()',
    'replies = {',
    `    ${STATION_A_HZ}: {"stations": [station(${STATION_A_HZ / 1000}, "Station A")], "freq_hz": ${STATION_A_HZ}},`,
    `    ${STATION_B_HZ}: {"stations": [station(${STATION_B_HZ / 1000}, "Station B")], "freq_hz": ${STATION_B_HZ}},`,
    '}',
    'print(json.dumps(replies, separators=(",", ":")))',
  ].join('\n');
  identifyReplies = JSON.parse(execFileSync('uv', ['run', 'python', '-c', script], {
    cwd: resolve(import.meta.dirname, '../../../../..'), encoding: 'utf8', timeout: 30_000,
  })) as Record<number, EibiResult>;
}, 30_000);

const fetchMock = vi.fn(async (input: string | URL | Request): Promise<Response> => {
  const url = new URL(String(input), 'http://station.test');
  const reply = url.pathname === '/api/v1/eibi/identify'
    ? identifyReplies[Number(url.searchParams.get('freq'))] : undefined;
  return reply === undefined
    ? new Response('not found', { status: 404 })
    : new Response(JSON.stringify(reply), { status: 200, headers: { 'Content-Type': 'application/json' } });
});

/** The identification requests the status bar sent, in order. */
const identifyRequests = () => fetchMock.mock.calls
  .map(([input]) => String(input))
  .filter((url) => url.startsWith('/api/v1/eibi/identify'));

describe('StatusBar Now Playing debounce (MOR-4213)', () => {
  let target: HTMLElement | null = null;
  let instance: object | null = null;

  beforeEach(() => {
    vi.useFakeTimers();
    fetchMock.mockClear();
    vi.stubGlobal('fetch', fetchMock);
    activeFrequencyProbe.frequencyHz = STATION_A_HZ;
  });

  afterEach(() => {
    if (instance) unmount(instance);
    instance = null;
    target?.remove();
    target = null;
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  function mountBar(): void {
    target = document.createElement('div');
    document.body.appendChild(target);
    instance = mount(StatusBar, { target }) as object;
    flushSync();
  }

  /** `count` deliveries 25 ms apart (the measured state rate), frequency unchanged. */
  async function deliveries(count: number): Promise<void> {
    for (let i = 0; i < count; i += 1) {
      await vi.advanceTimersByTimeAsync(25);
      activeFrequencyProbe.deliver();
      flushSync();
    }
  }

  it('state deliveries with an unchanged frequency do not re-arm the debounce', async () => {
    mountBar();
    const armed = vi.spyOn(globalThis, 'setTimeout');
    await deliveries(40);
    const identifyArms = armed.mock.calls.filter(([, delay]) => delay === 800);
    armed.mockRestore();
    expect(identifyArms).toHaveLength(0);
    // The debounce armed at mount fired 800 ms in, while deliveries kept coming.
    expect(identifyRequests()).toEqual([`/api/v1/eibi/identify?freq=${STATION_A_HZ}`]);
    flushSync();
    expect(target!.querySelector('.np-station')?.textContent).toBe('Station A');
  });

  it('a frequency change still restarts the debounce', async () => {
    mountBar();
    await vi.advanceTimersByTimeAsync(400);
    activeFrequencyProbe.frequencyHz = STATION_B_HZ;
    activeFrequencyProbe.deliver();
    flushSync();
    await vi.advanceTimersByTimeAsync(799);
    expect(identifyRequests()).toEqual([]);
    await vi.advanceTimersByTimeAsync(1);
    expect(identifyRequests()).toEqual([`/api/v1/eibi/identify?freq=${STATION_B_HZ}`]);
  });

  it('a lookup skipped while the popup is open runs for the current frequency once it closes', async () => {
    mountBar();
    await vi.advanceTimersByTimeAsync(800);
    flushSync();
    expect(target!.querySelector('.np-station')?.textContent).toBe('Station A');
    target!.querySelector<HTMLButtonElement>('button.now-playing')!.click();
    flushSync();
    expect(target!.querySelector('.np-detail')).not.toBeNull();

    // Tuned elsewhere while the popup is open: the debounce expires, deliveries keep coming.
    activeFrequencyProbe.frequencyHz = STATION_B_HZ;
    activeFrequencyProbe.deliver();
    flushSync();
    await deliveries(40);
    expect(identifyRequests()).toEqual([`/api/v1/eibi/identify?freq=${STATION_A_HZ}`]);

    target!.querySelector<HTMLButtonElement>('button.np-close')!.click();
    flushSync();
    await deliveries(40);
    flushSync();
    expect(identifyRequests()).toEqual([
      `/api/v1/eibi/identify?freq=${STATION_A_HZ}`,
      `/api/v1/eibi/identify?freq=${STATION_B_HZ}`,
    ]);
    expect(target!.querySelector('.np-station')?.textContent).toBe('Station B');
  });
});
