/** Shared wire documents are verified against real producers/handlers by:
 * tests/test_host_api_v3_wire_contract.py::test_controller_documents
 * tests/test_host_api_v3_wire_contract.py::test_power_handler_and_observed_publication
 * tests/test_host_api_v3_wire_contract.py::test_af_handler_dispatch_and_observed_publication
 * ACKs acknowledge dispatch; only a subsequent provider observation changes readings.
 */
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import hostWire from '../../../../../tests/fixtures/host-api-v3-wire.json';
import { flushSync, mount, unmount } from 'svelte';
import App from '../../../App.svelte';
import { MockWebSocket, instances } from '$lib/transport/__tests__/support/fake-ws-backend';
import { disconnectAll } from '$lib/transport/ws-client';
import { validateCapabilities, type Capabilities } from '$lib/types/capabilities';
import { mockCapabilities, mockState, mockLocalControllerStatus, mockControllerGrant } from '../../../../tests/e2e/i18n/fixtures';
import type { InfoResponse } from '$lib/types/protocol';
import { audioManager } from '$lib/audio/audio-manager';
import { setTxCodecFallback } from '$lib/stores/audio.svelte';
import { markStateUpdated, setWsConnected } from '$lib/stores/connection.svelte';
import { runtime } from '$lib/runtime';
import type { EibiResult } from '$lib/runtime/system-controller';
import { LOCAL_SUPERVISOR_META_NAME } from '../manifest';
import { parseHostDeclaration, readHostDeclaration, getLayoutOptions, type LocalExtensionHostDeclaration } from '../host-ui';
import type { ServerState } from '$lib/types/state';
import type { LocalExtensionHostApiV3, LocalExtensionHostWindow } from '../host-api';
import { CANONICAL_LAYOUT_MODES } from '../../../presentation/layout-mode';

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
let station: EibiResult;
let frequencyFields: ServerState['fieldStatus'];
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
  const document: Capabilities = { ...mockCapabilities, receivers, vfoScheme: receivers === 2 ? 'main_sub' : 'single',
    capabilities: receivers === 2 ? ['audio', 'af_level', 'power_control', 'dual_rx'] : ['audio', 'af_level', 'power_control'] };
  caps = validateCapabilities(document);
  app = mount(App, { target: globalThis.document.body });
  await vi.waitFor(() => expect(instances.length).toBeGreaterThan(0));
  const socket = instances[0];
  socket.simulateOpen();
  const state: ServerState = { ...mockState, active: receivers === 2 ? 'SUB' : 'MAIN',
    fieldStatus: { ...hostWire.fieldStatus, ...frequencyFields } as ServerState['fieldStatus'],
    main: { ...mockState.main, afLevel: 0.4 }, sub: { ...mockState.main, afLevel: 0.6 } };
  socket.simulateMessage(JSON.stringify({ type: 'state_update', data: { type: 'full',
    stateContractVersion: 1, providerGeneration: 0, data: state } }));
  await settled();
  await vi.waitFor(() => { flushSync(); expect(host()?.getListening().enabled).toBe(true); });
}

beforeAll(() => {
  // Use the HTTP producer's serializer, including its real display fields.
  const documents = JSON.parse(execFileSync('uv', ['run', 'python', '-c', [
    'import json, sys',
    'sys.path.insert(0, "tests")',
    'from rigplane.web.eibi import EiBiStation',
    'from test_host_api_v3_wire_contract import _observe, FieldPath, WebServer, WebConfig, _capable_radio',
    'server = WebServer(_capable_radio(), WebConfig(radio_model="IC-7610"))',
    '_observe(server, FieldPath.receiver("0", "freq_mode", "freq_hz"), 14205000)',
    'station = EiBiStation(freq_khz=14074, time_start=0, time_end=2400, days="", country="USA", station="Host Test Station", language="E", target="NAm", remarks="", persistence=1, start_date="", end_date="")',
    'fields = server.build_public_state()["fieldStatus"]',
    'print(json.dumps({"station": {"stations": [station.to_dict()]}, "fields": {"main.freqHz": fields["main.freqHz"]}}))',
  ].join('\n')], { cwd: resolve(import.meta.dirname, '../../../../..'), encoding: 'utf8', timeout: 30_000 })) as { station: EibiResult; fields: ServerState['fieldStatus'] };
  station = documents.station; frequencyFields = documents.fields;
}, 30_000);

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
    const body = url.startsWith('/api/v1/eibi/identify?') ? station : url.endsWith('/capabilities') ? caps : url.endsWith('/controller')
      ? init?.method === 'POST' ? mockControllerGrant : mockLocalControllerStatus
      : url.endsWith('/info') ? info : url.endsWith('/radio/power')
        ? powerFails ? { error: 'power unavailable' } : hostWire.power.response : {};
    const status = url.endsWith('/controller') && init?.method === 'POST' ? 201
      : url.endsWith('/radio/power') && powerFails ? 503 : 200;
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
  }));
});
afterEach(async () => {
  if (app) { await unmount(app); app = undefined; }
  disconnectAll(); document.body.replaceChildren();
  vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs();
  resize(viewport.width, viewport.height);
  document.querySelector(`meta[name="${LOCAL_SUPERVISOR_META_NAME}"]`)?.remove();
});

describe('installed host API v3 with production UI handlers', () => {
  it.each([undefined, {}, { health: false }, { health: true }, { health: 'true' }])(
    'respects host health ownership %j without hiding independent status controls', async (flags: { health?: unknown } | undefined) => {
      if (flags) {
        const declaration = { version: 1, report: false, language: false, ...flags };
        const meta = document.createElement('meta'); meta.name = LOCAL_SUPERVISOR_META_NAME;
        meta.content = JSON.stringify(declaration); document.head.append(meta);
      }
      await start(1);
      const owned = flags?.health === true;
      const bar = () => document.querySelector('.status-bar')!;
      const indicator = (name: string) => bar().querySelector(`[title^="${name}"]`);
      expect(indicator('Radio ↔ Server:') === null).toBe(owned);
      expect(indicator('Control WebSocket:') === null).toBe(owned);
      expect(indicator('State HTTP:') === null).toBe(owned);
      expect(bar().querySelector('[data-testid="audio-chip"]') === null).toBe(owned);
      const connection = () => Array.from(bar().querySelectorAll('button'))
        .find(button => /^(Connect|Disconnect)$/.test(button.textContent?.trim() ?? ''));
      expect(connection() === undefined).toBe(owned);
      if (!owned) expect(connection()?.textContent?.trim()).toBe('Disconnect');
      expect(bar().querySelector('.power-toggle-btn')).not.toBeNull();
      expect(bar().querySelector('.skin-select')).not.toBeNull();
      expect(bar().querySelector('.settings-btn')).not.toBeNull();
      expect(bar().querySelector('[data-testid="managed-tot-status"]')).not.toBeNull();
      await vi.waitFor(() => expect(bar().querySelector('.now-playing')?.textContent).toContain('Host Test Station'), { timeout: 3_000 });

      setTxCodecFallback(true); flushSync();
      expect(bar().querySelector('[data-testid="tx-codec-fallback"]')).not.toBeNull();
      // Drive the real staleness interval from its production state-update clock.
      const clock = vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 7_000);
      await vi.waitFor(() => { flushSync(); expect(runtime.connectionStale).toBe(true); }, { timeout: 2_000 });
      expect(bar().querySelector('[data-testid="bad-link-chip"]') === null).toBe(owned);
      clock.mockRestore(); markStateUpdated();

      setWsConnected(false); flushSync();
      expect(document.querySelector('.control-link-lost') === null).toBe(owned);
      expect(bar().querySelector('.http-lost-label') === null).toBe(owned);
      expect(connection() === undefined).toBe(owned);
      if (!owned) expect(connection()?.textContent?.trim()).toBe('Connect');
      setWsConnected(true); flushSync();

      // LCD's legacy scope indicator has no declared semantic replacement.
      expect(host().setLayout('lcd-cockpit')).toBe(true);
      await vi.waitFor(() => { flushSync(); expect(document.querySelector('.lcd-layout')).not.toBeNull(); });
      expect(indicator('Scope WebSocket:')).not.toBeNull();
      expect(bar().querySelector('[data-testid="managed-tot-status"]')).not.toBeNull();
      setTxCodecFallback(false);
    }, 30_000,
  );

  it('publishes AF and power only after producer-verified provider readback', async () => {
    await start(1);
    const socket = instances[0];
    expect(host().setListeningLevel(hostWire.af.request.params.level)).toBe(true);
    expect(commands()).toContainEqual(expect.objectContaining({
      name: hostWire.af.request.name, params: hostWire.af.request.params,
    }));
    expect(host().getListening().level).toBe(0.4);
    let revision = 1;
    const publish = (powerOn: boolean, afLevel: number) => {
      revision += 1;
      const state: ServerState = { ...mockState, powerOn, revision,
        stateRevision: revision, observationSeq: revision,
        main: { ...mockState.main, afLevel },
        fieldStatus: hostWire.fieldStatus as ServerState['fieldStatus'],
      };
      socket.simulateMessage(JSON.stringify({ type: 'state_update', data: { type: 'full',
        stateContractVersion: 1, providerGeneration: 0, data: state } }));
    };
    publish(true, hostWire.af.observed);
    await vi.waitFor(() => { flushSync(); expect(host().getListening().level).toBe(hostWire.af.observed); });
    expect(host().setRadioPower(false)).toBe(true); await confirm();
    expect(requests.at(-1)?.init?.body).toBe(JSON.stringify(hostWire.power.request));
    expect(host().getHostSnapshot().power).toBe(true);
    publish(hostWire.power.observed, hostWire.af.observed);
    await vi.waitFor(() => { flushSync(); expect(host().getHostSnapshot().power).toBe(false); });
  });

  it.each([true, false])('keeps mounted pickers and the host catalogue synchronized with DEV=%s', async development => {
    vi.stubEnv('DEV', development);
    await start(2);
    const expected = new Set([...CANONICAL_LAYOUT_MODES].filter(mode => development || mode !== 'sdr-test'));
    expect(new Set(host().getLayouts().map(option => option.value))).toEqual(expected);
    for (const layout of expected) {
      expect(host().setLayout(layout)).toBe(true);
      await vi.waitFor(() => {
        flushSync();
        const selector = document.querySelector<HTMLSelectElement>('select.skin-select');
        expect(selector?.value).toBe(layout);
        expect(new Set(Array.from(selector!.options, option => option.value))).toEqual(expected);
      });
    }
  }, 30000);

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

  it.each([{ version: 1, report: true, language: 'true', health: true }, { version: 1, language: true, health: true }])('preserves standalone controls for invalid declaration %j', async declaration => {
    const meta = document.createElement('meta'); meta.name = LOCAL_SUPERVISOR_META_NAME;
    meta.content = JSON.stringify(declaration); document.head.append(meta);
    await start(1);
    expect(document.querySelector('.report-btn')).not.toBeNull();
    expect(document.querySelector('[data-testid="audio-chip"]')).not.toBeNull();
    host().openRadioControls(); flushSync();
    expect(document.querySelector('.language-selector')).not.toBeNull();
  });

  it.each([true, false])('respects declared host language ownership=%s in mounted controls', async language => {
    const declaration: LocalExtensionHostDeclaration = { version: 1, report: false, language };
    const meta = document.createElement('meta'); meta.name = LOCAL_SUPERVISOR_META_NAME;
    meta.content = JSON.stringify(declaration); document.head.append(meta);
    await start(1);
    expect(host().openRadioControls()).toBe(true); flushSync();
    const controls = document.querySelector('.settings-modal');
    expect(controls).not.toBeNull();
    expect(controls!.querySelector('[data-panel-id="desktop-workspace"]')).not.toBeNull();
    expect(controls!.querySelector('[data-panel-id="desktop-language"]') === null).toBe(language);
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
    expect(installed.setTheme('dracula')).toBe(true); flushSync();
    document.querySelector<HTMLButtonElement>('.theme-button')!.click(); flushSync();
    const themeOptions = Array.from(document.querySelectorAll<HTMLButtonElement>('.theme-option'));
    const dracula = themeOptions.find(button => button.querySelector('.theme-name')?.textContent === 'Dracula');
    expect(dracula).toBeDefined();
    expect(themeOptions.filter(button => button.classList.contains('active'))).toEqual([dracula]);
    expect(themeOptions.filter(button => button.querySelector('.theme-check'))).toEqual([dracula]);
    document.querySelector<HTMLButtonElement>('.theme-button')!.click(); flushSync();
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
    expect(parseHostDeclaration(null)).toEqual({ version: 1, report: false, language: false, health: false });
    expect(parseHostDeclaration({ version: 2, report: true }).report).toBe(false);
    expect(parseHostDeclaration({ version: 1, report: 'true' }).report).toBe(false);
    const declaration: LocalExtensionHostDeclaration = { version: 1, report: true, language: true, health: true };
    expect(parseHostDeclaration({ version: 1, report: true, language: true })).toEqual({ ...declaration, health: false });
    for (const health of [false, 'true', 1, null, {}, []]) {
      expect(parseHostDeclaration({ ...declaration, health })).toEqual({ ...declaration, health: false });
    }
    expect(parseHostDeclaration({ ...declaration, version: 2 }).health).toBe(false);
    expect(parseHostDeclaration({ ...declaration, language: 'true' }).health).toBe(false);
    const meta = document.createElement('meta'); meta.name = LOCAL_SUPERVISOR_META_NAME;
    meta.content = JSON.stringify(declaration); document.head.append(meta);
    expect(readHostDeclaration()).toEqual(declaration); meta.remove();
    expect(getLayoutOptions(false).map(x => x.value)).not.toContain('sdr-test');
    expect(getLayoutOptions(true).map(x => x.value)).toContain('sdr-test');
  });
});
