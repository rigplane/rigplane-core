import { scopeWire } from '$lib/runtime/__tests__/scope-wire-fixture';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';

const txHarness = new ManagedAppTxHarness();

type MockConnectionState = 'connecting' | 'connected' | 'disconnected';
type DefaultScopeStatusProbe = {
  source: 'hardware' | 'audio_fft' | null;
  available: boolean;
  resourceSelected: boolean;
  demand: number;
  lifecycle: string;
  transport: MockConnectionState;
  frameSeen: boolean;
};
type ScopeFrameProbe = import('$lib/runtime/adapters/scope-adapter').ScopeFrame;
type AmberAfScopePropsProbe = {
  data: Uint8Array | null;
  bandwidth?: number;
  onRegisterPush?: (push: (pixels: Uint8Array | null) => void) => void;
};

const scopeChildProbe = vi.hoisted(() => ({
  props: null as AmberAfScopePropsProbe | null,
  pushed: vi.fn<(pixels: Uint8Array | null) => void>(),
}));

const mocks = vi.hoisted(() => {
  const binaryHandlers = new Set<(data: ArrayBuffer) => void>();
  const stateHandlers = new Set<(state: MockConnectionState) => void>();
  let state: MockConnectionState = 'disconnected';
  let epoch = 0;
  const messageHandlers = new Set<(message: import('$lib/types/protocol').WsIncoming) => void>();
  const sessionHandlers = new Set<(value: { state: MockConnectionState; epoch: number }) => void>();
  const setState = (next: MockConnectionState) => {
    if (next === 'connected' && state !== 'connected') epoch += 1;
    state = next;
    for (const handler of stateHandlers) handler(next);
    for (const handler of sessionHandlers) handler({ state, epoch });
  };
  const channel = {
    get state() { return state; },
    get sessionEpoch() { return epoch; },
    connect: vi.fn(() => {
      setState('connecting');
      setState('connected');
    }),
    disconnect: vi.fn(() => { setState('disconnected'); }),
    sendRaw: vi.fn((_message: Record<string, unknown>) => true),
    onMessage(handler: (message: import('$lib/types/protocol').WsIncoming) => void) {
      messageHandlers.add(handler); return () => messageHandlers.delete(handler);
    },
    message(message: import('$lib/types/protocol').WsIncoming) { messageHandlers.forEach(handler => handler(message)); },
    onBinary: vi.fn((handler: (data: ArrayBuffer) => void) => {
      binaryHandlers.add(handler);
      return () => { binaryHandlers.delete(handler); };
    }),
    onStateChange: vi.fn((handler: (value: MockConnectionState) => void) => {
      stateHandlers.add(handler);
      return () => { stateHandlers.delete(handler); };
    }),
    onSessionTransition(handler: (value: { state: MockConnectionState; epoch: number }) => void) {
      sessionHandlers.add(handler); return () => { sessionHandlers.delete(handler); };
    },
    binaryHandlerCount: () => binaryHandlers.size,
    stateHandlerCount: () => stateHandlers.size,
    fire(buffer: ArrayBuffer) { binaryHandlers.forEach(handler => handler(buffer)); },
  };
  return {
    channel,
    fetchCapabilities: vi.fn(),
    startPolling: vi.fn(),
    stopPolling: vi.fn(),
    getChannel: vi.fn(() => channel),
    connect: vi.fn(),
    sendRaw: vi.fn(),
    sendCommand: vi.fn(() => true),
    onMessage: vi.fn(() => () => {}),
  };
});

vi.mock('$lib/transport/http-client', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/transport/http-client')>(),
  fetchCapabilities: mocks.fetchCapabilities,
  fetchInfo: vi.fn().mockResolvedValue({}),
  startPolling: mocks.startPolling,
  setPollingMultiplier: vi.fn(),
  clearEtag: vi.fn(),
}));

vi.mock('$lib/transport/ws-client', () => ({
  getChannel: mocks.getChannel,
  connect: mocks.connect,
  sendRaw: mocks.sendRaw,
  sendCommand: mocks.sendCommand,
  onMessage: mocks.onMessage,
  disconnectAll: vi.fn(),
  reconnectAll: vi.fn(),
}));
vi.mock('$lib/runtime/commands/radio-intents', async () => {
  const { sendCommand } = await import('$lib/transport/ws-client');
  return { dispatchRadioIntent: ({ name, params }: { name: string; params: Record<string, unknown> }) => sendCommand(name, params) };
});
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => txHarness.controller,
}));
vi.mock('../AmberAfScope.svelte', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../AmberAfScope.svelte')>();
  const RealAmberAfScope = actual.default as unknown as (
    anchor: Node,
    props: AmberAfScopePropsProbe,
  ) => unknown;
  return {
    default: (anchor: Node, props: AmberAfScopePropsProbe) => {
      const registerWithParent = props.onRegisterPush;
      props.onRegisterPush = (push) => {
        registerWithParent?.((pixels) => {
          scopeChildProbe.pushed(pixels);
          push(pixels);
        });
      };
      scopeChildProbe.props = props;
      return RealAmberAfScope(anchor, props);
    },
  };
});

const canonicalCapabilities: Capabilities = {
  model: 'test',
  scope: true,
  audio: true,
  audioFftAvailable: true,
  scopeSource: 'hardware',
  tx: false,
  capabilities: ['audio', 'scope'],
  receivers: 1,
  vfoScheme: 'single',
  freqRanges: [],
  modes: ['USB'],
  filters: ['FIL1'],
  audioConfig: { sampleRate: 48_000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: null,
  stateContractVersion: 1,
  providerGeneration: 0,
};

// A COMPLETE ServerState: ``isValidServerState`` rejects any missing counter
// or status field, and ``setRadioState`` then silently returns false — which
// would make the churn loop below vacuous. Mirror the canonical ``makeState``
// in ws-client-store.integration.test.ts.
function radioState(revision: number): ServerState {
  const receiver = {
    freqHz: 14_074_000 + revision, mode: 'USB', filter: 1, filterWidth: 2400,
    dataMode: 0, sMeter: 0, att: 0, preamp: 0, nb: false, nr: false,
    afLevel: 128, rfGain: 255, squelch: 0, agc: 2,
  };
  return {
    revision, stateRevision: revision,
    freshnessRevision: revision, observationSeq: revision,
    updatedAt: '2026-06-03T00:00:00Z',
    active: 'MAIN',
    ptt: false, split: false, dualWatch: false, tunerStatus: 0,
    stateContractVersion: 1, providerGeneration: 0,
    main: receiver, sub: { ...receiver },
    connection: { rigConnected: true, radioReady: true, controlConnected: true },
    txTarget: { status: 'unknown', reason: 'not-observed' },
  } as ServerState;
}

function hasDefaultScopeStatus(
  value: object,
): value is { readonly defaultScopeStatus: DefaultScopeStatusProbe } {
  return 'defaultScopeStatus' in value;
}

describe('LCD audio-FFT demand ownership', () => {
  it('boots canonical authority and shares only the two mounted panel leases', async () => {
    vi.resetModules();
    vi.clearAllMocks();
    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
      expect(url).toBe('/api/v1/controller');
      expect(init.method ?? 'GET').toBe('GET');
      expect(init.redirect).toBe('error');
      return { ok: true, status: 200, json: async () => ({ protocol_version: 1, mode: 'local' }) };
    }));
    mocks.startPolling.mockReturnValue(mocks.stopPolling);
    vi.stubGlobal('ResizeObserver', class {
      observe() {}
      disconnect() {}
    });

    const { flushSync, mount, unmount } = await import('svelte');
    const { default: AmberCockpit } = await import('../AmberCockpit.svelte');
    const { default: AmberScope } = await import('../AmberScope.svelte');
    const { presentationResources, runtime } = await import('$lib/runtime/frontend-runtime');
    const { setRadioState } = await import('$lib/stores/radio.svelte');
    const { clearCapabilities, setCapabilities } = await import('$lib/stores/capabilities.svelte');
    const configure = vi.spyOn(presentationResources, 'configure');
    const acquire = vi.spyOn(presentationResources, 'acquire');
    const release = vi.spyOn(presentationResources, 'release');
    const stream = runtime.scope.stream('audio_fft');
    const realScopeSubscribe = stream.subscribe.bind(stream);
    let cockpitFrameHandler: ((frame: ScopeFrameProbe, slot: string) => void) | undefined;
    let cockpitUnsubscribe: ReturnType<typeof vi.fn<() => void>> | undefined;
    const scopeSubscribe = vi.spyOn(stream, 'subscribe').mockImplementation((handler) => {
      const realUnsubscribe = realScopeSubscribe(handler);
      const exactUnsubscribe = vi.fn(() => realUnsubscribe());
      if (!cockpitFrameHandler) {
        cockpitFrameHandler = handler;
        cockpitUnsubscribe = exactUnsubscribe;
      }
      return exactUnsubscribe;
    });
    const targets = [document.createElement('div'), document.createElement('div')];
    let cockpit: ReturnType<typeof mount> | undefined;
    let scope: ReturnType<typeof mount> | undefined;
    let cleanup: (() => void) | undefined;

    try {
      expect(presentationResources.snapshot('audio-fft')).toMatchObject({
        available: false, selected: false, demand: 0, health: 'inactive',
      });
      expect(mocks.getChannel).not.toHaveBeenCalled();

      cleanup = await runtime.bootstrap();
      expect(mocks.fetchCapabilities).not.toHaveBeenCalled();
      expect(presentationResources.snapshot('hardware-scope')).toMatchObject({
        available: false, selected: false, demand: 0, health: 'inactive',
      });
      expect(presentationResources.snapshot('audio-fft')).toMatchObject({
        available: false, selected: false, demand: 0, health: 'inactive',
      });
      expect(presentationResources.snapshot('rx-audio')).toMatchObject({
        available: false, selected: true, demand: 0, health: 'inactive',
      });
      expect(mocks.getChannel).not.toHaveBeenCalled();
      expect(mocks.sendCommand).not.toHaveBeenCalled();

      expect(setCapabilities(canonicalCapabilities)).toBe(true);
      const audioConfigurations = configure.mock.calls.filter(
        ([resource]) => resource === 'audio-fft',
      );
      expect(audioConfigurations).toHaveLength(2);
      expect(audioConfigurations[0]?.[1]).toMatchObject({
        available: false, selected: false,
      });
      expect(audioConfigurations[1]?.[1]).toMatchObject({
        available: true, selected: true,
      });
      expect(presentationResources.snapshot('hardware-scope')).toMatchObject({
        available: true, selected: true, demand: 0, health: 'inactive',
      });
      expect(presentationResources.snapshot('audio-fft')).toMatchObject({
        available: true, selected: true, demand: 0, health: 'inactive',
      });
      expect(presentationResources.snapshot('rx-audio')).toMatchObject({
        available: true, selected: true, demand: 0, health: 'inactive',
      });
      if (hasDefaultScopeStatus(runtime)) {
        expect(runtime.defaultScopeStatus).toEqual({
          source: 'hardware',
          available: true,
          resourceSelected: true,
          demand: 0,
          lifecycle: 'inactive',
          transport: 'disconnected',
          frameSeen: false,
        });
      }
      expect(mocks.getChannel).not.toHaveBeenCalled();

      expect(setRadioState(radioState(1))).toBe(true);
      for (const target of targets) document.body.appendChild(target);
      cockpit = mount(AmberCockpit, { target: targets[0] });
      flushSync();
      expect(presentationResources.snapshot('audio-fft').demand).toBe(1);
      expect(scopeSubscribe).toHaveBeenCalledOnce();
      expect(cockpitFrameHandler).toBeTypeOf('function');
      expect(cockpitUnsubscribe).not.toHaveBeenCalled();

      await vi.waitFor(() => expect(mocks.channel.connect).toHaveBeenCalledTimes(1));
      const buffer = scopeWire({ source: 'audio_fft', sourceId: 'audio_fft', receiver: null, viewStartHz: 14_012_345, viewEndHz: 14_098_765, pixels: new Uint8Array([3, 17, 91, 205, 254]) });
      mocks.channel.fire(buffer);
      flushSync();
      const delivered = stream.latest('audio_fft:*')!.pixels!;
      expect(delivered.buffer).toBe(buffer);
      expect(scopeChildProbe.pushed).toHaveBeenCalledExactlyOnceWith(delivered);
      expect(scopeChildProbe.props?.data).toBeNull();
      expect(scopeChildProbe.props?.bandwidth).toBe(86_420);

      scope = mount(AmberScope, { target: targets[1] });
      flushSync();
      await vi.waitFor(() => expect(mocks.channel.connect).toHaveBeenCalledTimes(1));
      expect(presentationResources.snapshot('audio-fft').demand).toBe(2);
      expect(mocks.getChannel).toHaveBeenCalledExactlyOnceWith('scope');
      expect(mocks.channel.connect).toHaveBeenCalledExactlyOnceWith('/api/v1/scope');
      expect(mocks.channel.onBinary).toHaveBeenCalledTimes(1);
      expect(mocks.channel.onStateChange).toHaveBeenCalledTimes(1);
      expect(mocks.channel.binaryHandlerCount()).toBe(1);
      expect(mocks.channel.stateHandlerCount()).toBe(1);
      expect(scopeSubscribe).toHaveBeenCalledTimes(2);
      expect(acquire.mock.calls.map(([resource, consumer]) => [resource, consumer])).toEqual([
        ['audio-fft', 'AmberCockpit'],
        ['audio-fft', 'AmberScope'],
      ]);
      expect(presentationResources.snapshot('hardware-scope').demand).toBe(0);
      if (hasDefaultScopeStatus(runtime)) expect(runtime.defaultScopeStatus.demand).toBe(0);

      for (let revision = 2; revision < 7; revision += 1) {
        // Prove the churn stimulus actually enters the store — a rejected
        // state would make the no-re-acquire assertions below vacuous.
        expect(setRadioState(radioState(revision))).toBe(true);
        flushSync();
      }
      await Promise.resolve();
      expect(acquire).toHaveBeenCalledTimes(2);
      expect(release).not.toHaveBeenCalled();
      expect(configure.mock.calls.filter(([resource]) => resource === 'audio-fft')).toHaveLength(2);
      expect(mocks.getChannel).toHaveBeenCalledTimes(1);
      expect(mocks.channel.connect).toHaveBeenCalledTimes(1);
      expect(mocks.sendCommand).not.toHaveBeenCalled();

      await unmount(cockpit);
      cockpit = undefined;
      expect(cockpitUnsubscribe).toHaveBeenCalledOnce();
      expect(presentationResources.snapshot('audio-fft').demand).toBe(1);
      expect(mocks.channel.disconnect).not.toHaveBeenCalled();
      expect(mocks.channel.binaryHandlerCount()).toBe(1);
      expect(mocks.channel.stateHandlerCount()).toBe(1);

      await unmount(scope);
      scope = undefined;
      await vi.waitFor(() => expect(mocks.channel.disconnect).toHaveBeenCalledTimes(1));
      expect(presentationResources.snapshot('audio-fft').demand).toBe(0);
      expect(mocks.channel.binaryHandlerCount()).toBe(0);
      expect(mocks.channel.stateHandlerCount()).toBe(0);
      expect(release).toHaveBeenCalledTimes(2);

      await cleanup();
      const configureCountAfterCleanup = configure.mock.calls.length;
      clearCapabilities();
      expect(configure).toHaveBeenCalledTimes(configureCountAfterCleanup);
      await cleanup();
      // MOR-1409 A09b: the HTTP polling writer is gone; WS is the sole state
      // writer, so cleanup never has polling to stop.
      expect(mocks.stopPolling).not.toHaveBeenCalled();
      expect(txHarness.trace()).toEqual([]);
    } finally {
      if (cockpit) await unmount(cockpit);
      if (scope) await unmount(scope);
      if (cleanup) await cleanup();
      clearCapabilities();
      for (const target of targets) target.remove();
      vi.unstubAllGlobals();
      vi.resetModules();
    }
  });

  it('contains no direct transport or hardware-scope fallback', () => {
    for (const name of ['AmberCockpit', 'AmberScope']) {
      const source = readFileSync(resolve(`src/components-v2/panels/lcd/${name}.svelte`), 'utf8');
      expect(source).not.toMatch(/\$lib\/transport|hardware-scope/);
    }
  });
});


it.each(['AudioSpectrumPanel', 'AmberScope', 'AmberCockpit'] as const)(
  '%s retries a refused audio mapping when observed facts arrive without resize or VFO qualification', async name => {
    vi.resetModules(); vi.clearAllMocks();
    vi.doMock('../AmberAfScope.svelte', async importOriginal => {
      const actual = await importOriginal<typeof import('../AmberAfScope.svelte')>();
      const Child = actual.default as unknown as (anchor: Node, props: AmberAfScopePropsProbe) => unknown;
      return { default: (anchor: Node, props: AmberAfScopePropsProbe) => {
        const register = props.onRegisterPush;
        props.onRegisterPush = push => register?.(pixels => { scopeChildProbe.pushed(pixels); push(pixels); });
        return Child(anchor, props);
      } };
    });
    vi.doMock('../../audio-scope/AudioSpectrumCanvas.svelte', async importOriginal => {
      const actual = await importOriginal<typeof import('../../audio-scope/AudioSpectrumCanvas.svelte')>();
      const Child = actual.default as unknown as (anchor: Node, props: AmberAfScopePropsProbe) => unknown;
      return { default: (anchor: Node, props: AmberAfScopePropsProbe) => {
        const register = props.onRegisterPush;
        props.onRegisterPush = push => register?.(pixels => { scopeChildProbe.pushed(pixels); push(pixels); });
        return Child(anchor, props);
      } };
    });
    vi.stubGlobal('fetch' , vi.fn(async () => ({ ok: true, status: 200,
      json: async () => ({ protocol_version: 1, mode: 'local' }) })));
    let resizes = 0;
    vi.stubGlobal('ResizeObserver', class {
      constructor(private callback: (entries: { contentRect: { width: number; height: number } }[]) => void) {}
      observe() { resizes++; this.callback([{ contentRect: { width: 400, height: 100 } }]); }
      disconnect() {}
    });
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(false);
    const { flushSync, mount, unmount } = await import('svelte');
    const { runtime } = await import('$lib/runtime/frontend-runtime');
    const { setRadioState } = await import('$lib/stores/radio.svelte');
    const { setCapabilities, clearCapabilities } = await import('$lib/stores/capabilities.svelte');
    const { toSpectrumAuthority } = await import('$lib/runtime/adapters/scope-adapter');
    const Component = name === 'AudioSpectrumPanel'
      ? (await import('../../audio-scope/AudioSpectrumPanel.svelte')).default
      : name === 'AmberScope' ? (await import('../AmberScope.svelte')).default
        : (await import('../AmberCockpit.svelte')).default;
    const target = document.createElement('div'); document.body.append(target);
    const cleanup = await runtime.bootstrap();
    const caps = { ...canonicalCapabilities, vfoScheme: 'ab' as const, vfoReadback: 'none' as const };
    setCapabilities(caps);
    const initial = radioState(1); initial.active = null; initial.main!.mode = null; initial.main!.filterWidth = null;
    setRadioState(initial);
    const component = mount(Component, { target });
    const subscriptions = () => mocks.channel.sendRaw.mock.calls.filter(([message]) => message.type === 'subscribe');
    try {
      flushSync(); await Promise.resolve(); flushSync();
      expect(subscriptions()).toHaveLength(1);
      mocks.channel.message({ type: 'error', sourceId: 'audio_fft', receiver: null, message: 'mode/filter unavailable' });
      flushSync(); expect(scopeChildProbe.pushed).not.toHaveBeenCalled();
      const resizeCount = resizes;
      const known = radioState(2); known.active = null;
      known.fieldStatus = Object.fromEntries(['main.freqHz', 'main.mode', 'main.filterWidth', 'main.dataMode'].map(path => [path,
        { storePath: path, observed: true, freshness: 'fresh' as const, availability: 'available' as const, lastObservedMonotonic: 2 }]));
      expect(toSpectrumAuthority(known, caps)).toBeNull();
      expect(setRadioState(known)).toBe(true);
      flushSync(); await Promise.resolve(); flushSync();
      expect(resizes).toBe(resizeCount);
      expect(subscriptions()).toHaveLength(2);
      mocks.channel.message({ type: 'granted', sourceId: 'audio_fft', receiver: null,
        view: { startHz: 14000000, endHz: 14100000 }, coverage: { startHz: 14000000, endHz: 14100000 }, width: 4, fps: 20 });
      mocks.channel.fire(scopeWire({ source: 'audio_fft', sourceId: 'audio_fft', receiver: null }));
      flushSync(); expect(scopeChildProbe.pushed).toHaveBeenCalledTimes(1);
      const unrelated = { ...known, revision: 3, stateRevision: 3, observationSeq: 3,
        main: { ...known.main!, sMeter: 7 } };
      setRadioState(unrelated); flushSync();
      expect(subscriptions()).toHaveLength(2);
      const pixels = scopeChildProbe.pushed.mock.lastCall?.[0];
      expect(pixels).toBeInstanceOf(Uint8Array);
      mocks.channel.fire(scopeWire({ source: 'audio_fft', sourceId: 'audio_fft', receiver: null, pixels: null }));
      flushSync(); expect(scopeChildProbe.pushed).toHaveBeenLastCalledWith(null);
      mocks.channel.fire(scopeWire({ source: 'audio_fft', sourceId: 'audio_fft', receiver: null }));
      flushSync(); expect(scopeChildProbe.pushed.mock.lastCall?.[0]).toBeInstanceOf(Uint8Array);
      mocks.channel.message({ type: 'error', sourceId: 'audio_fft', receiver: null, message: 'source stopped' });
      flushSync(); expect(scopeChildProbe.pushed).toHaveBeenLastCalledWith(null);
      mocks.channel.fire(scopeWire({ source: 'audio_fft', sourceId: 'audio_fft', receiver: null }));
      flushSync(); expect(scopeChildProbe.pushed.mock.lastCall?.[0]).toBeInstanceOf(Uint8Array);
      mocks.channel.disconnect();
      flushSync(); expect(scopeChildProbe.pushed).toHaveBeenLastCalledWith(null);
    } finally {
      await unmount(component); await cleanup(); clearCapabilities(); target.remove();
      vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.resetModules();
    }
  },
);
