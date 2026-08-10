import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';

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

const mocks = vi.hoisted(() => {
  const binaryHandlers = new Set<(data: ArrayBuffer) => void>();
  const stateHandlers = new Set<(state: MockConnectionState) => void>();
  let state: MockConnectionState = 'disconnected';
  const setState = (next: MockConnectionState) => {
    state = next;
    for (const handler of stateHandlers) handler(next);
  };
  const channel = {
    get state() { return state; },
    connect: vi.fn(() => {
      setState('connecting');
      setState('connected');
    }),
    disconnect: vi.fn(() => { setState('disconnected'); }),
    onBinary: vi.fn((handler: (data: ArrayBuffer) => void) => {
      binaryHandlers.add(handler);
      return () => { binaryHandlers.delete(handler); };
    }),
    onStateChange: vi.fn((handler: (value: MockConnectionState) => void) => {
      stateHandlers.add(handler);
      return () => { stateHandlers.delete(handler); };
    }),
    binaryHandlerCount: () => binaryHandlers.size,
    stateHandlerCount: () => stateHandlers.size,
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
    txController: Object.freeze({
      snapshot: vi.fn(() => Object.freeze({
        phase: 'idle', intent: null, sourceId: null, leaseId: null, guard: null,
        fault: null, radioTx: 'off', txRisk: 'none', mayOwnKey: false,
      })),
      subscribe: vi.fn(() => () => {}),
      start: vi.fn(),
      setIntent: vi.fn(),
      release: vi.fn(),
      resetFault: vi.fn(),
    }),
  };
});

vi.mock('$lib/transport/http-client', () => ({
  fetchCapabilities: mocks.fetchCapabilities,
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
vi.mock('$lib/runtime/tx-controller/app-host', () => ({
  getAppTxController: () => mocks.txController,
}));

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

function radioState(revision: number) {
  const receiver = {
    freqHz: 14_074_000 + revision, mode: 'USB', filter: 1, filterWidth: 2400,
    dataMode: 0, sMeter: 0, att: 0, preamp: 0, nb: false, nr: false,
    afLevel: 128, rfGain: 255, squelch: 0, agc: 2,
  };
  return {
    revision, stateRevision: revision, active: 'MAIN',
    stateContractVersion: 1, providerGeneration: 0,
    main: receiver, sub: { ...receiver },
  } as never;
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

      setRadioState(radioState(1));
      for (const target of targets) document.body.appendChild(target);
      cockpit = mount(AmberCockpit, { target: targets[0] });
      flushSync();
      expect(presentationResources.snapshot('audio-fft').demand).toBe(1);
      scope = mount(AmberScope, { target: targets[1] });
      flushSync();
      await vi.waitFor(() => expect(mocks.channel.connect).toHaveBeenCalledTimes(1));
      expect(presentationResources.snapshot('audio-fft').demand).toBe(2);
      expect(mocks.getChannel).toHaveBeenCalledExactlyOnceWith('audio-scope');
      expect(mocks.channel.connect).toHaveBeenCalledExactlyOnceWith('/api/v1/audio-scope');
      expect(mocks.channel.onBinary).toHaveBeenCalledTimes(1);
      expect(mocks.channel.onStateChange).toHaveBeenCalledTimes(1);
      expect(mocks.channel.binaryHandlerCount()).toBe(1);
      expect(mocks.channel.stateHandlerCount()).toBe(1);
      expect(acquire.mock.calls.map(([resource, consumer]) => [resource, consumer])).toEqual([
        ['audio-fft', 'AmberCockpit'],
        ['audio-fft', 'AmberScope'],
      ]);
      expect(presentationResources.snapshot('hardware-scope').demand).toBe(0);
      if (hasDefaultScopeStatus(runtime)) expect(runtime.defaultScopeStatus.demand).toBe(0);

      for (let revision = 2; revision < 7; revision += 1) {
        setRadioState(radioState(revision));
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
      expect(mocks.stopPolling).toHaveBeenCalledTimes(1);
    } finally {
      if (cockpit) await unmount(cockpit);
      if (scope) await unmount(scope);
      if (cleanup) await cleanup();
      clearCapabilities();
      for (const target of targets) target.remove();
      vi.doUnmock('$lib/transport/http-client');
      vi.doUnmock('$lib/transport/ws-client');
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
