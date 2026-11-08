import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import App from '../../../App.svelte';
import { MockWebSocket, instances } from '$lib/transport/__tests__/support/fake-ws-backend';
import { disconnectAll } from '$lib/transport/ws-client';
import { validateCapabilities, type Capabilities } from '$lib/types/capabilities';
import { mockCapabilities, mockState, mockLocalControllerStatus, mockControllerGrant } from '../../../../tests/e2e/i18n/fixtures';
import type { InfoResponse } from '$lib/types/protocol';
import { audioManager } from '$lib/audio/audio-manager';
import { LOCAL_SUPERVISOR_META_NAME } from '../manifest';
import { parseHostDeclaration, readHostDeclaration, getLayoutOptions, type LocalExtensionHostDeclaration } from '../host-ui';
import type { ServerState } from '$lib/types/state';
import type { LocalExtensionHostApiV3, LocalExtensionHostWindow } from '../host-api';

vi.mock('$lib/media/media-session', () => ({ initMediaSession() {}, destroyMediaSession() {} } satisfies Pick<typeof import('$lib/media/media-session'), 'initMediaSession' | 'destroyMediaSession'>));
const audioBoundary = vi.hoisted(() => ({ deferStop: false, finishStop: undefined as (() => void) | undefined }));
vi.mock('$lib/audio/audio-manager', async () => {
  const { setRxEnabled } = await import('$lib/stores/audio.svelte');
  type BrowserAudio = typeof import('$lib/audio/audio-manager').audioManager;
  let rxEnabled = false;
  const setEnabled = (enabled: boolean) => { rxEnabled = enabled; setRxEnabled(enabled); };
  const audioManager = {
  onChange: () => () => {}, onTxAudioDied: () => () => {}, get rxEnabled() { return rxEnabled; },
  setOperatorNotifier: vi.fn<BrowserAudio['setOperatorNotifier']>(), setControllerBinding: vi.fn<BrowserAudio['setControllerBinding']>(), destroy: vi.fn<BrowserAudio['destroy']>(),
  startRx: () => setEnabled(true), stopRx: () => {
    if (audioBoundary.deferStop) audioBoundary.finishStop = () => setEnabled(false); else setEnabled(false);
  }, stopTx() {},
  getAppliedAudioConfig: () => null, setRxVolume: vi.fn<BrowserAudio['setRxVolume']>(),
  };
  return { audioManager: audioManager satisfies Pick<BrowserAudio, keyof typeof audioManager> };
});

let app: ReturnType<typeof mount> | undefined;
let caps: Capabilities;
let powerFails = false;
const requests: Array<{ url: string; init?: RequestInit }> = [];
const info: InfoResponse = { version: 'test', revision: 1, updatedAt: '2026-01-01T00:00:00Z', uptime: 0 };
const viewport = { width: window.innerWidth, height: window.innerHeight };
const host = () => (window as LocalExtensionHostWindow).rigplaneExtensionHost as LocalExtensionHostApiV3;
const button = (id: string) => document.querySelector<HTMLButtonElement>(`[data-testid="confirm-dialog-${id}"]`)!;
const commands = () => instances.flatMap(socket => socket.sent.map(frame => JSON.parse(frame))).filter(frame => frame.type === 'cmd');
async function settled() { await vi.waitFor(() => { flushSync(); expect(document.querySelector('.radio-layout, .lcd-layout, .m-layout'), document.body.textContent).not.toBeNull(); }, { timeout: 10000 }); }
async function confirm() { flushSync(); button('confirm').click(); await vi.waitFor(() => { flushSync(); expect(document.querySelector('[role="alertdialog"]')).toBeNull(); }, { timeout: 8000 }); }
function resize(width: number, height = 900) {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: height });
  window.dispatchEvent(new Event('resize'));
}
async function start(receivers: 1 | 2) {
  const document: Capabilities = { ...mockCapabilities, receivers, vfoScheme: receivers === 2 ? 'main_sub' : 'ab', capabilities: ['audio', 'af_level', 'power_control'] };
  caps = validateCapabilities(document);
  app = mount(App, { target: globalThis.document.body });
  await vi.waitFor(() => expect(instances.length).toBeGreaterThan(0));
  const socket = instances[0];
  socket.simulateOpen();
  const state: ServerState = { ...mockState, active: receivers === 2 ? 'SUB' : 'MAIN',
    main: { ...mockState.main, afLevel: 0.4 }, sub: { ...mockState.main, afLevel: 0.6 } };
  socket.simulateMessage(JSON.stringify({ type: 'state_update', data: { type: 'full',
    stateContractVersion: 1, providerGeneration: 0, data: state } }));
  await settled();
  await vi.waitFor(() => { flushSync(); expect(host()?.getListening().enabled).toBe(true); });
}

beforeEach(() => {
  requests.length = 0; instances.length = 0; powerFails = false;
  audioBoundary.deferStop = false; audioBoundary.finishStop = undefined;
  vi.clearAllMocks();
  localStorage.clear(); resize(1400);
  vi.stubGlobal('WebSocket', MockWebSocket);
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement) {
    return new Proxy({ canvas: this, createImageData: (w: number, h: number) => ({ data: new Uint8ClampedArray(w * h * 4) }),
      measureText: () => ({ width: 0 }) }, { get: (target, key) => key in target ? Reflect.get(target, key) : () => {} }) as unknown as CanvasRenderingContext2D;
  });
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    requests.push({ url, init });
    const body = url.endsWith('/capabilities') ? caps : url.endsWith('/controller')
      ? init?.method === 'POST' ? mockControllerGrant : mockLocalControllerStatus
      : url.endsWith('/info') ? info : url.endsWith('/radio/power')
        ? powerFails ? { error: 'power unavailable' } : { status: 'ok', power: JSON.parse(init!.body as string).state } : {};
    const status = url.endsWith('/controller') && init?.method === 'POST' ? 201
      : url.endsWith('/radio/power') && powerFails ? 503 : 200;
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
  }));
});
afterEach(async () => {
  if (app) { await unmount(app); app = undefined; }
  disconnectAll(); document.body.replaceChildren();
  vi.restoreAllMocks(); vi.unstubAllGlobals();
  resize(viewport.width, viewport.height);
  document.querySelector(`meta[name="${LOCAL_SUPERVISOR_META_NAME}"]`)?.remove();
});

describe('installed host API v3 with production UI handlers', () => {
  it.each([1, 2] as const)('uses validated %i-receiver documents for LOCAL, MUTE and playback', async receivers => {
    await start(receivers);
    const listening = vi.fn();
    const stopListening = host().subscribeListening(listening);
    expect(host().version).toBe(3);
    expect(host().setListeningMode('local')).toBe(true);
    expect(host().setListeningLevel(0.42)).toBe(true);
    expect(commands()).toContainEqual(expect.objectContaining({ name: 'set_af_level', params: { level: 0.42, level_unit: 'normalized', receiver: receivers === 2 ? 1 : 0 } }));
    expect(host().setListeningMode('mute')).toBe(true);
    const before = commands().length;
    const muted = host().getListening();
    expect(muted.mode).toBe('mute');
    expect(host().setListeningLevel(0.7)).toBe(false);
    expect(commands()).toHaveLength(before);
    expect(host().getListening()).toEqual(muted);
    expect(host().setListeningMode('live')).toBe(true);
    expect(host().getListening()).toMatchObject({ mode: 'live', target: 'playback' });
    const radioCommands = commands().length;
    expect(host().setListeningLevel(0.35)).toBe(true);
    expect(audioManager.setRxVolume).toHaveBeenCalledWith(0.35);
    expect(commands()).toHaveLength(radioCommands);
    expect(host().getListening().level).toBe(0.35);
    flushSync();
    const mute = Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find(button => button.textContent?.trim() === 'MUTE');
    expect(mute).toBeDefined(); mute!.click(); flushSync();
    expect(listening.mock.lastCall?.[0].mode).toBe('mute');
    const count = listening.mock.calls.length; stopListening();
    host().setListeningMode('local'); flushSync(); expect(listening).toHaveBeenCalledTimes(count);
    expect(host().setListeningLevel(NaN)).toBe(false);
    expect(host().setListeningLevel(-1)).toBe(false);
    expect(host().setListeningLevel(99)).toBe(false);
  }, 30000);

  it('republishes the radio target when deferred RX teardown completes while muted', async () => {
    await start(1);
    const listening = vi.fn(); const stop = host().subscribeListening(listening);
    host().setListeningMode('live'); flushSync();
    audioBoundary.deferStop = true;
    host().setListeningMode('mute'); flushSync();
    await vi.waitFor(() => expect(audioBoundary.finishStop).toBeDefined());
    expect(listening.mock.lastCall?.[0]).toMatchObject({ mode: 'mute', target: 'playback' });
    audioBoundary.finishStop!(); flushSync();
    expect(listening.mock.lastCall?.[0]).toMatchObject({ mode: 'mute', target: 'radio', maxLevel: 1 });
    stop();
  });

  it.each([{ version: 1, report: true, language: 'true' }, { version: 1, language: true }])('preserves standalone controls for invalid declaration %j', async declaration => {
    const meta = document.createElement('meta'); meta.name = LOCAL_SUPERVISOR_META_NAME;
    meta.content = JSON.stringify(declaration); document.head.append(meta);
    await start(1);
    expect(document.querySelector('.report-btn')).not.toBeNull();
    host().openRadioControls(); flushSync();
    expect(document.querySelector('.language-selector')).not.toBeNull();
  });

  it('shares confirmations with UI buttons and keeps actions through desktop, mobile and LCD', async () => {
    await start(1);
    const installed = host();
    const snapshots = vi.fn();
    const stop = installed.subscribeHostSnapshot(snapshots);
    document.querySelector<HTMLButtonElement>('.theme-button')!.click(); flushSync();
    Array.from(document.querySelectorAll<HTMLButtonElement>('.theme-option')).find(button => button.textContent?.includes('Nord'))!.click(); flushSync();
    expect(snapshots.mock.lastCall?.[0].theme).toBe('nord');
    expect(document.documentElement.dataset.theme).toBe('nord');
    expect(installed.openRadioControls()).toBe(true); flushSync();
    expect(document.querySelector('.settings-modal')).not.toBeNull();
    document.querySelector<HTMLButtonElement>('.settings-close')!.click();
    document.querySelector<HTMLSelectElement>('select.skin-select')!.value = 'lcd-cockpit';
    document.querySelector('select.skin-select')!.dispatchEvent(new Event('change', { bubbles: true }));
    await vi.waitFor(() => expect(document.querySelector('.lcd-layout')).not.toBeNull());
    expect(snapshots.mock.lastCall?.[0].layout).toBe('lcd-cockpit');
    expect(installed.openRadioControls()).toBe(true); flushSync();
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
    expect(installed.setLayout('standard')).toBe(true); await settled();
    resize(390, 844);
    await vi.waitFor(() => expect(document.querySelector('.m-layout')).not.toBeNull());
    expect(host()).toBe(installed);
    expect(installed.setRadioPower(false)).toBe(true); flushSync();
    expect(requests.filter(r => r.url.endsWith('/radio/power'))).toHaveLength(0);
    button('cancel').click(); flushSync();
    expect(requests.filter(r => r.url.endsWith('/radio/power'))).toHaveLength(0);
    expect(installed.setRadioPower(false)).toBe(true); await confirm();
    expect(requests.at(-1)).toMatchObject({ url: '/api/v1/radio/power', init: { body: '{"state":"off"}' } });
    powerFails = true;
    expect(installed.setRadioPower(false)).toBe(true); flushSync(); button('confirm').click();
    await vi.waitFor(() => expect(document.querySelector('[data-testid="confirm-dialog-error"]')?.textContent).toContain('power unavailable'));
    button('close').click(); flushSync();
    expect(installed.disconnect()).toBe(true); flushSync();
    expect(instances[0].readyState).toBe(MockWebSocket.OPEN);
    await confirm();
    expect(instances[0].readyState).toBe(MockWebSocket.CLOSED);
    expect(installed.setListeningLevel(0.5)).toBe(false);
    expect(installed.setListeningMode('local')).toBe(false);
    expect(snapshots.mock.lastCall?.[0].connected).toBe(false);
    const count = instances.length;
    expect(installed.connect()).toBe(true); flushSync(); expect(instances).toHaveLength(count);
    await confirm(); expect(instances.length).toBeGreaterThan(count);
    resize(1400); await vi.waitFor(() => expect(document.querySelector('.radio-layout')).not.toBeNull());
    expect(installed.setLayout('lcd-cockpit')).toBe(true);
    await vi.waitFor(() => expect(document.querySelector('.lcd-layout')).not.toBeNull());
    expect(installed.setTheme('dracula')).toBe(true); flushSync();
    expect(document.documentElement.dataset.theme).toBe('dracula');
    expect(JSON.parse(localStorage.getItem('rigplane:workspace')!)).toMatchObject({ layout: 'lcd-cockpit', theme: 'dracula' });
    expect(snapshots.mock.lastCall?.[0]).toMatchObject({ layout: 'lcd-cockpit', theme: 'dracula' });
    const calls = snapshots.mock.calls.length; stop();
    installed.setTheme('default'); flushSync(); expect(snapshots).toHaveBeenCalledTimes(calls);
    expect(installed.setLayout('invalid')).toBe(false); expect(installed.setTheme('invalid')).toBe(false);
  }, 30000);

  it('reads only the typed versioned supervisor declaration', () => {
    expect(parseHostDeclaration(null)).toEqual({ version: 1, report: false, language: false });
    expect(parseHostDeclaration({ version: 2, report: true }).report).toBe(false);
    expect(parseHostDeclaration({ version: 1, report: 'true' }).report).toBe(false);
    const declaration: LocalExtensionHostDeclaration = { version: 1, report: true, language: true };
    const meta = document.createElement('meta'); meta.name = LOCAL_SUPERVISOR_META_NAME;
    meta.content = JSON.stringify(declaration); document.head.append(meta);
    expect(readHostDeclaration()).toEqual(declaration); meta.remove();
    expect(getLayoutOptions(false).map(x => x.value)).not.toContain('sdr-test');
    expect(getLayoutOptions(true).map(x => x.value)).toContain('sdr-test');
  });
});
