import { beforeEach, describe, expect, it, vi } from 'vitest';
import wire from '../lib/transport/api-channel-wire.json';
import { FakeDataChannel, goldenDocs, goldenFrame } from '../lib/transport/__tests__/support/fake-datachannel';

const events: string[] = [];
type MountOptions = { target: HTMLElement; props?: { onRemoteTeardown(finished: Promise<void>): void } };
const mount = vi.fn((_component: unknown, _options: MountOptions) => ({ mounted: true }));
const unmount = vi.fn(async (_app: unknown) => {});

function installMocks(options: { activationError?: Error; activationGate?: Promise<void> } = {}): void {
  vi.doMock('../lib/migrate-legacy-storage', () => {
    events.push('migration');
    return {};
  });
  vi.doMock('../../component-kits.config', () => {
    events.push('config');
    return {
      default: {
        kits: [async () => {
          events.push('kit');
          return { default: { apiVersion: 1, id: 'boot-kit' } };
        }],
      },
    };
  });
  vi.doMock('../component-kits/activation', () => ({
    activateComponentKits: async (config: { kits: readonly (() => Promise<unknown>)[] }) => {
      await Promise.all(config.kits.map((load) => load()));
      events.push('activation');
      await options.activationGate;
      if (options.activationError) throw options.activationError;
    },
  }));
  vi.doMock('../App.svelte', () => {
    events.push('app');
    return { default: function App() {} };
  });
  vi.doMock('../app.css', () => {
    events.push('css');
    return {};
  });
  vi.doMock('svelte', () => ({
    unmount,
    mount: (component: unknown, options: MountOptions) => {
      events.push('mount');
      return mount(component, options);
    },
  }));
}

function remoteLink() {
  const channels = new Map<string, FakeDataChannel>();
  const createChannel = (label: string) => {
    expect(wire.data_channels.some((channel) => channel.label === label)).toBe(true);
    const channel = new FakeDataChannel(label);
    channel.simulateOpen();
    vi.spyOn(channel, 'close');
    channels.set(label, channel);
    return channel;
  };
  return { api: createChannel('api'), createChannel, channels };
}

beforeEach(() => {
  vi.resetModules();
  vi.resetAllMocks();
  events.length = 0;
  document.body.innerHTML = '<main id="app"></main>';
});

describe('component-kit application bootstrap', () => {
  it('migrates, loads configuration, activates kits, then evaluates and mounts App once', async () => {
    installMocks();

    const { default: started } = await import('../main');
    await started;

    expect(events[0]).toBe('migration');
    expect(events.indexOf('config')).toBeGreaterThan(events.indexOf('migration'));
    expect(events.indexOf('kit')).toBeGreaterThan(events.indexOf('config'));
    expect(events.indexOf('app')).toBeGreaterThan(events.indexOf('activation'));
    expect(events.indexOf('css')).toBeGreaterThan(events.indexOf('activation'));
    expect(events.at(-1)).toBe('mount');
    expect(mount).toHaveBeenCalledOnce();
    expect(mount.mock.calls[0][1]).toEqual({ target: document.getElementById('app') });
  });

  it('renders one host-owned failure surface and never evaluates or mounts App after activation fails', async () => {
    installMocks({ activationError: new Error('bad component kit') });
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});

    const { default: started } = await import('../main');
    await expect(started).rejects.toThrow('bad component kit');

    expect(events).not.toContain('app');
    expect(events).not.toContain('css');
    expect(events).not.toContain('mount');
    expect(mount).not.toHaveBeenCalled();
    expect(document.querySelectorAll('[data-component-kit-startup-error]')).toHaveLength(1);
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('RigPlane could not start');
    expect(consoleError).toHaveBeenCalledWith('[rigplane] startup failed:', expect.any(Error));
  });

  it('retires a remote start during kit activation and mounts its replacement once', async () => {
    let finishActivation!: () => void;
    installMocks({ activationGate: new Promise<void>((resolve) => { finishActivation = resolve; }) });
    const { bootRemote } = await import('../boot');
    const first = remoteLink();
    const onLinkLost = vi.fn();
    const old = bootRemote(first, { onLinkLost });
    await vi.waitFor(() => expect(events).toContain('activation'));
    old.shutdown();

    const next = remoteLink();
    const live = bootRemote(next, { onLinkLost });
    finishActivation();
    await vi.waitFor(() => expect(mount).toHaveBeenCalledOnce());
    expect(events.filter((event) => event === 'activation')).toHaveLength(1);
    expect(first.api.close).toHaveBeenCalledOnce();
    expect(first.api.onmessage).toBeNull();
    expect(onLinkLost).not.toHaveBeenCalled();

    // A retired handle must not affect the app that replaced it.
    old.shutdown();
    expect(next.api.close).not.toHaveBeenCalled();
    live.shutdown();
    await vi.waitFor(() => expect(unmount).toHaveBeenCalledOnce());
  });

  it('waits for the mounted runtime to retire before replacing it', async () => {
    installMocks();
    const { bootRemote } = await import('../boot');
    const { controllerClient } = await import('../lib/transport/controller-client');
    const { currentBundle } = await import('../lib/transport/transport-bundle');
    const options = { onLinkLost: vi.fn() };
    bootRemote(remoteLink(), options);
    await vi.waitFor(() => expect(mount).toHaveBeenCalledOnce());
    const control = currentBundle().openChannel('control');
    control.open('', { protocols: ['rigplane-controller-v1.' + 'a'.repeat(64)], role: 'primary' });
    const stopClosingOnLoss = controllerClient.onLoss(() => control.close());
    let finishTeardown!: () => void;
    const teardown = new Promise<void>((resolve) => { finishTeardown = resolve; });
    unmount.mockImplementationOnce(async () => {
      mount.mock.calls[0][1].props!.onRemoteTeardown(teardown);
    });
    const live = bootRemote(remoteLink(), options);
    stopClosingOnLoss();
    expect(options.onLinkLost).not.toHaveBeenCalled();
    await vi.waitFor(() => expect(unmount).toHaveBeenCalledOnce());
    expect(mount).toHaveBeenCalledOnce();
    finishTeardown();
    await vi.waitFor(() => expect(mount).toHaveBeenCalledTimes(2));
    live.shutdown();
    await vi.waitFor(() => expect(unmount).toHaveBeenCalledTimes(2));
  });

  it('uses both halves of a media pair and reports controller loss once after closing the link', async () => {
    installMocks();
    const { bootRemote } = await import('../boot');
    const { currentBundle } = await import('../lib/transport/transport-bundle');
    const { controllerClient } = await import('../lib/transport/controller-client');
    const link = remoteLink();
    const onLinkLost = vi.fn(() => {
      expect(link.api.onmessage).toBeNull();
      expect(link.channels.get('control')?.close).toHaveBeenCalledOnce();
    });
    const handle = bootRemote(link, { onLinkLost });
    await vi.waitFor(() => expect(mount).toHaveBeenCalledOnce());
    const attach = { protocols: ['rigplane-controller-v1.' + 'a'.repeat(64)], role: 'primary' } as const;
    const control = currentBundle().openChannel('control');
    const scope = currentBundle().openChannel('scope');
    const audio = currentBundle().openChannel('audio');
    for (const channel of [control, scope, audio]) channel.open('', attach);
    await Promise.resolve();
    for (const label of ['scope', 'audio']) {
      const binary = link.channels.get(label)!;
      const text = link.channels.get(label + '-text')!;
      expect(binary.sent).toEqual([]);
      expect(text.sent).toHaveLength(1);
      text.close();
      expect(binary.close).toHaveBeenCalledOnce();
    }
    expect(link.channels.get('control')?.close).not.toHaveBeenCalled();
    expect(onLinkLost).not.toHaveBeenCalled();
    controllerClient.lose();
    expect(onLinkLost).toHaveBeenCalledExactlyOnceWith('session_lost');
    handle.shutdown();
    await vi.waitFor(() => expect(unmount).toHaveBeenCalledOnce());
  });

  it('uses one API attach and skips an unknown incoming type during a fetch', async () => {
    installMocks();
    const { bootRemote } = await import('../boot');
    const { apiFetch } = await import('../lib/api/api-fetch');
    const link = remoteLink();
    const handle = bootRemote(link, { onLinkLost: vi.fn() });
    try {
      await vi.waitFor(() => expect(mount).toHaveBeenCalledOnce());
      const reply = apiFetch('/api/v1/band-plan/layers');
      await vi.waitFor(() => expect(link.api.sent).toHaveLength(3));
      expect(link.api.textFrames().map((text) => JSON.parse(text))).toEqual([goldenDocs.attach_api.decoded]);
      const [, request] = link.api.sent;
      const id = new DataView(request as ArrayBuffer).getUint32(2);
      const [head, body, end] = goldenDocs.api_ok.reply!;
      link.api.simulateMessage(goldenFrame(head.hex, id));
      const unknown = goldenFrame(end.hex, id);
      new DataView(unknown).setUint8(1, 255);
      link.api.simulateMessage(unknown);
      link.api.simulateMessage(goldenFrame(body.hex, id));
      link.api.simulateMessage(goldenFrame(end.hex, id));
      const response = await reply;
      expect(response.status).toBe(200);
      expect(response.headers.get('content-length')).toBe('16');
      expect((await response.arrayBuffer()).byteLength).toBe(16);
    } finally {
      handle.shutdown();
      await vi.waitFor(() => expect(unmount).toHaveBeenCalledOnce());
    }
  });
});
