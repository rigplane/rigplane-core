/**
 * MOR-4596, the complete settings-to-transmission path: the operator flips the
 * real control, the real AudioManager starts TX through the real TxMic, and the
 * constraints that reach getUserMedia are the ones the mode names. Reload is
 * a fresh module graph over the same localStorage.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { mount as Mount, unmount as Unmount, flushSync as FlushSync } from 'svelte';

vi.mock('../../../lib/audio/rx-player', () => ({
  RxPlayer: class {
    start = vi.fn(); stop = vi.fn(); flush = vi.fn(); setJitterBounds = vi.fn();
    stats = vi.fn(() => ({ underruns: 0, bufferDepthMs: 0, droppedFrames: 0 }));
    feed = vi.fn(); setFocus = vi.fn(); setSplitStereo = vi.fn(); setChannelGainDb = vi.fn();
    get focus() { return 'main'; }
    get splitStereo() { return false; }
    get mainGainDb() { return 0; }
    get subGainDb() { return 0; }
    set volume(_value: number) {}
  },
}));
vi.mock('../../../lib/stores/connection.svelte', () => ({ setAudioConnected: vi.fn() }));
vi.mock('../../../lib/stores/audio.svelte', () => ({
  setRxEnabled: vi.fn(), setTxEnabled: vi.fn(), setTxCodecFallback: vi.fn(),
  setRxError: vi.fn(), setTxError: vi.fn(),
}));
vi.mock('$lib/stores/capabilities.svelte', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/stores/capabilities.svelte')>(),
  getCapabilities: vi.fn(() => null),
}));

const KEY = 'rigplane:tx-mic-processing';
let target: HTMLElement;
let svelte: { mount: typeof Mount; unmount: typeof Unmount; flushSync: typeof FlushSync };
let mounted: ReturnType<typeof Mount> | null = null;
let getUserMedia: ReturnType<typeof vi.fn>;

class FakeWebSocket {
  static OPEN = 1;
  static CONNECTING = 0;
  readyState = 0; binaryType = '';
  onopen: (() => void) | null = null; onmessage: unknown = null;
  onerror: unknown = null; onclose: unknown = null;
  send() {}
  close() { this.readyState = 3; }
}

function installMedia(): void {
  const track = Object.assign(new EventTarget(), { stop: vi.fn(), kind: 'audio' });
  const stream = { getTracks: () => [track], getAudioTracks: () => [track] } as unknown as MediaStream;
  getUserMedia = vi.fn().mockResolvedValue(stream);
  const reader = { read: vi.fn(() => new Promise(() => {})), cancel: vi.fn().mockResolvedValue(undefined) };
  const encoder = { configure: vi.fn(), encode: vi.fn(), close: vi.fn(), state: 'configured' };
  (globalThis as any).AudioEncoder = function () { return encoder; };
  (globalThis as any).MediaStreamTrackProcessor = function () {
    return { readable: { getReader: () => reader } };
  };
  Object.defineProperty(globalThis, 'navigator', {
    value: { mediaDevices: { getUserMedia } }, writable: true, configurable: true,
  });
}

/** A page load: fresh modules over whatever localStorage holds. */
async function loadPage() {
  vi.resetModules();
  // Svelte itself is re-imported with the page: a component compiled against
  // one runtime cannot be mounted by another.
  svelte = await import('svelte');
  const { default: Control } = await import('../TxMicProcessingControl.svelte');
  const { audioManager } = await import('../../../lib/audio/audio-manager');
  mounted = svelte.mount(Control, { target });
  svelte.flushSync();
  return { audioManager };
}

const pick = (mode: 'raw' | 'browser') => {
  target.querySelector<HTMLInputElement>(`[data-testid="tx-mic-processing-${mode}"]`)!.click();
  svelte.flushSync();
};

async function transmit(audioManager: { startTx(): Promise<string | null>; stopTx(): void }) {
  expect(await audioManager.startTx()).toBeNull();
  const audio = getUserMedia.mock.calls.at(-1)![0].audio;
  audioManager.stopTx();
  return audio;
}

beforeEach(() => {
  localStorage.removeItem(KEY);
  vi.unstubAllGlobals();
  vi.stubGlobal('WebSocket', FakeWebSocket);
  vi.stubGlobal('location', { protocol: 'http:', host: 'localhost:5173' });
  installMedia();
  target = document.createElement('div');
  document.body.append(target);
});
afterEach(() => {
  if (mounted) svelte.unmount(mounted);
  mounted = null;
  target.remove();
  delete (globalThis as any).AudioEncoder;
  delete (globalThis as any).MediaStreamTrackProcessor;
});

describe('TX mic processing: control -> AudioManager -> getUserMedia (MOR-4596)', () => {
  it('starts raw by default and follows each mode picked in the control', async () => {
    const { audioManager } = await loadPage();
    expect(await transmit(audioManager)).toMatchObject({
      echoCancellation: false, noiseSuppression: false, autoGainControl: false,
    });

    pick('browser');
    expect(await transmit(audioManager)).toMatchObject({
      echoCancellation: true, noiseSuppression: true, autoGainControl: true,
    });

    pick('raw');
    expect(await transmit(audioManager)).toMatchObject({
      echoCancellation: false, noiseSuppression: false, autoGainControl: false,
    });
  });

  it('keeps the chosen mode across a reload, in the control and in the capture', async () => {
    const first = await loadPage();
    pick('browser');
    expect(localStorage.getItem(KEY)).toBe('browser');
    first.audioManager.stopTx();
    svelte.unmount(mounted!);
    mounted = null;

    const { audioManager } = await loadPage();
    expect(target.querySelector<HTMLInputElement>('[data-testid="tx-mic-processing-browser"]')!.checked)
      .toBe(true);
    expect(await transmit(audioManager)).toMatchObject({
      echoCancellation: true, noiseSuppression: true, autoGainControl: true,
    });
  });

  it('gives each mounted copy its own radio group (phone sheet + nested dialog)', async () => {
    await loadPage();
    const { default: Control } = await import('../TxMicProcessingControl.svelte');
    const second = document.createElement('div');
    document.body.append(second);
    const secondMount = svelte.mount(Control, { target: second });
    svelte.flushSync();

    const first = target.querySelector<HTMLInputElement>('[data-testid="tx-mic-processing-raw"]')!;
    const other = second.querySelector<HTMLInputElement>('[data-testid="tx-mic-processing-raw"]')!;
    // One shared name would couple the copies through native radio grouping.
    expect(first.name).not.toBe(other.name);
    // Re-clicking the already-selected mode in the second copy leaves the
    // first copy's selection rendered instead of silently unchecking it.
    other.click();
    svelte.flushSync();
    expect(first.checked).toBe(true);
    expect(other.checked).toBe(true);

    svelte.unmount(secondMount);
    second.remove();
  });
});
