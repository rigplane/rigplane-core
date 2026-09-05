import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { ManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';

const h = vi.hoisted(() => ({ tx: null as ManagedAppTxController | null }));
vi.mock('$lib/transport/http-client', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/transport/http-client')>(),
  fetchInfo: vi.fn().mockResolvedValue({}),
}));
vi.mock('$lib/transport/ws-client', () => ({
  connect: vi.fn(), sendRaw: vi.fn(), sendCommand: vi.fn(),
  getControlSession: () => ({ state: 'connected', epoch: 1 }),
  onControlSessionTransition: () => () => undefined,
  onCommandDelivery: () => () => undefined,
  onMessage: () => () => undefined,
  getChannel: vi.fn(),
}));
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => h.tx,
}));

class FakeWebSocket {
  static OPEN = 1;
  static CONNECTING = 0;
  static instances: FakeWebSocket[] = [];
  readyState = FakeWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  send = vi.fn();
  constructor(readonly url: string) { FakeWebSocket.instances.push(this); }
  close() { this.readyState = 3; }
  open() { this.readyState = FakeWebSocket.OPEN; this.onopen?.(); }
}

const dualCaps = {
  stateContractVersion: 1, providerGeneration: 1,
  model: 'routing-fixture', scope: false, audio: true, tx: false,
  capabilities: ['audio', 'dual_rx', 'af_level'],
  receivers: 2, vfoScheme: 'main_sub', freqRanges: [], modes: [], filters: [],
  audioConfig: { sampleRate: 48000, channels: 2, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false }, txBands: [],
  scopeSource: null, audioFftAvailable: false,
} as unknown as Capabilities;

let svelte: typeof import('svelte');
let runtime: typeof import('$lib/runtime/frontend-runtime')['runtime'];
let audioManager: typeof import('$lib/audio/audio-manager')['audioManager'];
let SemanticRadioSurfaces: typeof import('../SemanticRadioSurfaces.svelte')['default'];
let AudioRoutingControl: typeof import('../../panels/AudioRoutingControl.svelte')['default'];
let setCapabilities: typeof import('$lib/stores/capabilities.svelte')['setCapabilities'];
let cleanup: (() => void) | undefined;
let components: ReturnType<typeof import('svelte')['mount']>[];
let target: HTMLDivElement;
let startRx: ReturnType<typeof vi.spyOn>;
let setConfig: ReturnType<typeof vi.spyOn>;

beforeEach(async () => {
  vi.resetModules();
  vi.stubGlobal('WebSocket', FakeWebSocket);
  vi.stubGlobal('AudioContext', vi.fn(() => { throw new Error('Unexpected audio startup'); }));
  FakeWebSocket.instances = [];
  localStorage.clear();
  svelte = await import('svelte');
  const { ManagedAppTxHarness } = await import('$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness');
  h.tx = new ManagedAppTxHarness().controller;
  ({ runtime } = await import('$lib/runtime/frontend-runtime'));
  ({ audioManager } = await import('$lib/audio/audio-manager'));
  ({ setCapabilities } = await import('$lib/stores/capabilities.svelte'));
  ({ default: SemanticRadioSurfaces } = await import('../SemanticRadioSurfaces.svelte'));
  ({ default: AudioRoutingControl } = await import('../../panels/AudioRoutingControl.svelte'));
  startRx = vi.spyOn(audioManager, 'startRx');
  setConfig = vi.spyOn(audioManager, 'setAudioConfig');
  setCapabilities(dualCaps);
  target = document.createElement('div');
  document.body.appendChild(target);
  components = [];
  cleanup = undefined;
});

afterEach(async () => {
  for (const component of components) await svelte.unmount(component);
  await cleanup?.();
  audioManager?.destroy();
  document.body.innerHTML = '';
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function renderSemantic() {
  components.push(svelte.mount(SemanticRadioSurfaces, { target }));
  svelte.flushSync();
}
function el(id: string) {
  return target.querySelector<HTMLElement>(`[data-testid="rx-audio-${id}"]`)!;
}
function checked(id: string) { return el(id).getAttribute('aria-checked'); }
function click(id: string) { el(id).click(); svelte.flushSync(); }

async function clearPresentation() {
  for (const component of components) await svelte.unmount(component);
  components = [];
  target.replaceChildren();
}

describe('MOR-2333 App routing restoration reaches mounted controls', () => {
  it('restores and applies saved routing once, confirms commands, and survives presentation replacement', async () => {
    localStorage.setItem('icom.audio.focus', 'sub');
    localStorage.setItem('icom.audio.split_stereo', '1');
    localStorage.setItem('icom.audio.main_gain_db', '-6');
    localStorage.setItem('icom.audio.sub_gain_db', '2');
    renderSemantic();
    expect(el('focus').dataset.observed).toBe('false');
    expect(el('split').dataset.observed).toBe('false');
    expect(setConfig).not.toHaveBeenCalled();
    expect(FakeWebSocket.instances).toHaveLength(0);

    cleanup = await runtime.bootstrap();
    svelte.flushSync();
    expect(checked('focus-sub')).toBe('true');
    expect(checked('split-on')).toBe('true');
    expect(audioManager.getAudioConfig()).toEqual({
      focus: 'sub', split_stereo: true, main_gain_db: -6, sub_gain_db: 2,
    });
    expect(setConfig).toHaveBeenCalledExactlyOnceWith({
      focus: 'sub', split_stereo: true, main_gain_db: -6, sub_gain_db: 2,
    });
    const ws = FakeWebSocket.instances[0];
    expect(ws.url).toContain('/api/v1/audio');
    ws.open();
    expect(ws.send).toHaveBeenCalledWith(JSON.stringify({ type: 'audio_config', focus: 'sub', split_stereo: true }));

    click('focus-main');
    click('split-off');
    expect(checked('focus-main')).toBe('true');
    expect(checked('focus-sub')).toBe('false');
    expect(checked('split-off')).toBe('true');
    expect(audioManager.getAudioConfig()).toMatchObject({ focus: 'main', split_stereo: false });
    expect(localStorage.getItem('icom.audio.focus')).toBe('main');
    expect(localStorage.getItem('icom.audio.split_stereo')).toBe('0');
    expect(ws.send).toHaveBeenLastCalledWith(JSON.stringify({ type: 'audio_config', focus: 'main', split_stereo: false }));

    const calls = setConfig.mock.calls.length;
    const storageRead = vi.spyOn(localStorage, 'getItem');
    await clearPresentation();
    components.push(svelte.mount(AudioRoutingControl, { target }));
    svelte.flushSync();
    expect(target.querySelectorAll('[role="radio"]')[0].getAttribute('aria-checked')).toBe('true');
    expect(target.querySelector<HTMLInputElement>('[aria-label="MAIN gain in decibels"]')!.value).toBe('-6');
    await clearPresentation();
    renderSemantic();
    expect(checked('focus-main')).toBe('true');
    expect(checked('split-off')).toBe('true');
    expect(await runtime.bootstrap()).toBe(cleanup);
    expect(setConfig).toHaveBeenCalledTimes(calls);
    expect(storageRead.mock.calls.filter(([key]) => String(key).startsWith('icom.audio.'))).toEqual([]);
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(startRx).not.toHaveBeenCalled();
    expect(AudioContext).not.toHaveBeenCalled();

    await clearPresentation();
    setCapabilities({ ...dualCaps, receivers: 1, vfoScheme: 'ab', capabilities: ['audio', 'af_level'] });
    renderSemantic();
    expect(el('focus')).toBeNull();
    expect(el('split')).toBeNull();
  });

  it('keeps missing fields unknown through partial restore and confirms each explicit application', async () => {
    localStorage.setItem('icom.audio.focus', 'sub');
    renderSemantic();
    cleanup = await runtime.bootstrap();
    svelte.flushSync();
    expect(checked('focus-sub')).toBe('true');
    expect(el('split').dataset.observed).toBe('false');
    expect(checked('split-off')).toBe('false');
    click('split-off');
    expect(checked('split-off')).toBe('true');
    expect(checked('focus-sub')).toBe('true');
    expect(startRx).not.toHaveBeenCalled();
  });

  it('does not turn player defaults into observed routing when storage is empty', async () => {
    cleanup = await runtime.bootstrap();
    renderSemantic();
    expect(el('focus').dataset.observed).toBe('false');
    expect(el('split').dataset.observed).toBe('false');
    expect(FakeWebSocket.instances).toHaveLength(0);
    click('focus-main');
    expect(checked('focus-main')).toBe('true');
    expect(el('split').dataset.observed).toBe('false');
    expect(startRx).not.toHaveBeenCalled();
  });
});
