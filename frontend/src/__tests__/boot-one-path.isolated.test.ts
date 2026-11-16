/**
 * One boot path (C5b, PR-A): both entries — `main.ts` (the station-served
 * page) and `bootRemote` (the remote-link shell) — run the one `startApp`.
 *
 * What is compared: the ordered construction trace (config → kit →
 * activation → App/app.css evaluation → one `mount` on `#app`) and the
 * installed transport bundle each entry leaves behind. The only accepted
 * differences are the bundle itself and `main.ts`'s own storage-migration
 * preamble, which runs before `startApp` and has no equivalent in a fresh
 * remote context.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { FakeDataChannel, goldenAttachText, goldenDocs } from '../lib/transport/__tests__/support/fake-datachannel';
import type { RemoteChannels } from '../lib/transport/transport-bundle';

const events: string[] = [];
const mount = vi.fn((_component: unknown, _options: { target: HTMLElement }) => ({ mounted: true }));

function installMocks(): void {
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
    mount: (component: unknown, options: { target: HTMLElement }) => {
      events.push('mount');
      return mount(component, options);
    },
  }));
}

function fakeChannels(): RemoteChannels {
  return {
    control: new FakeDataChannel('control'),
    scope: new FakeDataChannel('scope'),
    audio: new FakeDataChannel('audio'),
    api: new FakeDataChannel('api'),
  };
}

/** The station page's own preamble (the migration side-effect import) —
 * absent from the remote entry by design. */
const STATION_ONLY = 'migration';

beforeEach(() => {
  vi.resetModules();
  vi.clearAllMocks();
  events.length = 0;
  document.body.innerHTML = '<main id="app"></main>';
});

describe('one boot path (C5b)', () => {
  it('both entries run the one startApp: identical construction trace, only the bundle differs', async () => {
    installMocks();
    const { default: started } = await import('../main');
    await started;
    expect(events[0], 'main.ts still migrates storage first').toBe(STATION_ONLY);
    const stationTrace = events.filter((event) => event !== STATION_ONLY);
    expect(mount).toHaveBeenCalledOnce();
    const { currentBundle } = await import('../lib/transport/transport-bundle');
    expect(currentBundle().kind).toBe('websocket');

    vi.resetModules();
    vi.clearAllMocks();
    events.length = 0;
    document.body.innerHTML = '<main id="app"></main>';
    installMocks();
    const { bootRemote, REMOTE_BOOT_API } = await import('../boot');
    expect(REMOTE_BOOT_API).toBe(1);
    bootRemote(fakeChannels(), { onLinkLost: () => {} });
    await vi.waitFor(() => expect(events.at(-1)).toBe('mount'));
    expect(events.filter((event) => event !== STATION_ONLY)).toEqual(stationTrace);
    expect(mount).toHaveBeenCalledOnce();
    expect(mount.mock.calls[0][1]).toEqual({ target: document.getElementById('app') });
    const { currentBundle: currentAfterBoot } = await import('../lib/transport/transport-bundle');
    expect(currentAfterBoot().kind).toBe('datachannel');
  });

  it('bootRemote serves the factory from the injected channels; loss empties the bundle until replaceChannels', async () => {
    installMocks();
    const { bootRemote } = await import('../boot');
    const first = fakeChannels();
    const losses: string[] = [];
    const handle = bootRemote(first, { onLinkLost: (reason) => losses.push(reason) });
    await vi.waitFor(() => expect(events).toContain('mount'));

    const { createMessageTransport } = await import('../lib/transport/transport-factory');
    // A live control transport over the injected channel: the channel dying
    // under it loses the link, once.
    const control = createMessageTransport('control');
    control.open('ws://ignored/', { protocols: ['rigplane-controller-v1.KEY'], role: 'primary' });
    first.control.simulateOpen();
    first.control.simulateMessage(goldenDocs.control_hello.text as string);
    expect(control.state).toBe('open');
    first.control.close();
    expect(losses).toEqual(['channel_closed']);

    // Until replaceChannels, openChannel hands out transports that close at
    // once with the reason — no throw, so the owners' backoff survives.
    const scope = createMessageTransport('scope');
    const closes: { reason: string }[] = [];
    scope.onClose((info) => closes.push(info));
    scope.open('ws://ignored/');
    await vi.waitFor(() => expect(closes).toHaveLength(1));
    expect(closes[0].reason).toBe('channel_closed');

    // replaceChannels: the bundle rides the fresh channels.
    const next = fakeChannels();
    handle.replaceChannels(next);
    const control2 = createMessageTransport('control');
    let opened2 = 0;
    control2.onOpen(() => { opened2 += 1; });
    control2.open('ws://ignored/', { protocols: ['rigplane-controller-v1.KEY'], role: 'primary' });
    next.control.simulateOpen();
    expect(next.control.textFrames()[0]).toBe(goldenAttachText('attach_control', 'KEY'));
    next.control.simulateMessage(goldenDocs.control_hello.text as string);
    expect(opened2).toBe(1);
    expect(losses, 'no second loss from the swap').toEqual(['channel_closed']);
  });

  it('shutdown stops serving channels without reporting a loss', async () => {
    installMocks();
    const { bootRemote } = await import('../boot');
    const losses: string[] = [];
    const handle = bootRemote(fakeChannels(), { onLinkLost: (reason) => losses.push(reason) });
    await vi.waitFor(() => expect(events).toContain('mount'));
    handle.shutdown();

    const { createMessageTransport } = await import('../lib/transport/transport-factory');
    const transport = createMessageTransport('control');
    const closes: { reason: string }[] = [];
    transport.onClose((info) => closes.push(info));
    transport.open('ws://ignored/');
    await vi.waitFor(() => expect(closes).toHaveLength(1));
    expect(losses).toEqual([]);
  });
});
