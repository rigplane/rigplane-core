/**
 * Transport-seam guards (C4, no behaviour change; C5b adds the bundle).
 *
 * The UI must run over WebSockets today and over WebRTC data channels from
 * one bundle. Both seams live in exactly one place each:
 *   - message channels: `lib/transport/` (`message-transport.ts`,
 *     `websocket-transport.ts`, `transport-bundle.ts`,
 *     `transport-factory.ts`);
 *   - REST: `lib/api/api-fetch.ts` (`apiFetch`), delegating to the
 *     installed transport bundle.
 *
 * Each test names the mutation it exists to kill: reaching past the seam
 * with a raw `WebSocket` or a raw `/api/v1` fetch; deciding on the
 * transport kind outside `transport-bundle.ts`/`boot.ts`.
 *
 * The bundle rows (C5b) run the same scenario over both producers through
 * the production path — `createMessageTransport` / `apiFetch` → installed
 * bundle → transport, never a direct transport constructor.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve, sep } from 'node:path';
import { runInNewContext } from 'node:vm';
import {
  installBundle, webSocketBundle, dataChannelBundle,
  type RemoteChannels, type TransportBundle,
} from '../transport-bundle';
import { ATTACH_CLOSE_REASON } from '../datachannel-transport';
import { createMessageTransport } from '../transport-factory';
import { apiFetch } from '../../api/api-fetch';
import type { MessageTransport, TransportAttach } from '../message-transport';
import { FakeDataChannel, goldenFrame, goldenDocs, goldenAttachText } from './support/fake-datachannel';
import { MockWebSocket, instances } from './support/fake-ws-backend';

// `process.cwd()` is `frontend/` under this project's vitest config.
const SRC_ROOT = resolve(process.cwd(), 'src');

function collectSourceFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      collectSourceFiles(full, out);
    } else if (full.endsWith('.ts') || full.endsWith('.svelte')) {
      out.push(full);
    }
  }
  return out;
}

function projectPath(full: string): string {
  return relative(SRC_ROOT, full).split(sep).join('/');
}

function isCommentLine(line: string): boolean {
  const trimmed = line.trim();
  return trimmed.startsWith('//') || trimmed.startsWith('*') || trimmed.startsWith('/*') || trimmed.startsWith('<!--');
}

function matchingLines(content: string, pattern: RegExp): string[] {
  return content.split('\n').filter((line) => !isCommentLine(line) && pattern.test(line));
}

describe('transport seam guards', () => {
  const files = collectSourceFiles(SRC_ROOT);

  it('no `new WebSocket(` outside lib/transport/', () => {
    const offenders: string[] = [];
    for (const full of files) {
      const rel = projectPath(full);
      if (rel.startsWith('lib/transport/')) continue;
      const hits = matchingLines(readFileSync(full, 'utf8'), /new WebSocket\(/);
      if (hits.length > 0) offenders.push(`${rel}: ${hits[0].trim()}`);
    }
    expect(offenders).toEqual([]);

    // C5b pin: the transport choice is the bundle's alone, so a second
    // decider cannot appear above the seam. Pinned shapes: the
    // `datachannel` kind literal, a `bundle.kind` read, and any comparison
    // of a kind against the transport literals. Test files may name the
    // kind (these fixtures assert it); product code may not.
    const kindOffenders: string[] = [];
    for (const full of files) {
      const rel = projectPath(full);
      if (rel === 'lib/transport/transport-bundle.ts' || rel === 'boot.ts' || rel.includes('__tests__/')) continue;
      const content = readFileSync(full, 'utf8');
      const pattern = /'datachannel'|"datachannel"|bundle\.kind|kind\s*===\s*'(?:websocket|datachannel)'/;
      for (const line of matchingLines(content, pattern)) {
        kindOffenders.push(`${rel}: ${line.trim()}`);
      }
    }
    expect(kindOffenders, 'transport kind read outside the bundle').toEqual([]);
  });

  it('every /api/v1 fetch goes through apiFetch', () => {
    // `lib/api/api-fetch.ts` owns the seam; since C5b the REST call itself
    // lives in the installed bundle — `webSocketBundle().apiFetch` IS the
    // old `fetch` body — so `lib/transport/transport-bundle.ts` is the one
    // other file allowed a `fetch(`. `tuning.svelte.ts` talks to the Pro
    // supervisor companion surface (`/api/local/...`), which is not part
    // of the /api/v1 seam and keeps its own fetch.
    const offenders: string[] = [];
    for (const full of files) {
      const rel = projectPath(full);
      if (rel === 'lib/api/api-fetch.ts' || rel === 'lib/transport/transport-bundle.ts') continue;
      const content = readFileSync(full, 'utf8');
      for (const line of matchingLines(content, /fetch\(/)) {
        if (rel === 'lib/stores/tuning.svelte.ts' && line.includes('/api/local')) continue;
        offenders.push(`${rel}: ${line.trim()}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});

/** Per-producer peer plumbing for the parametrised scenario: one way to
 * open the control channel, deliver text/binary, and read what left. */
interface BundleHarness {
  readonly bundle: TransportBundle;
  openControl(transport: MessageTransport): void;
  /** Text frames the producer itself delivers to the app during open. */
  readonly handshakeTexts: readonly string[];
  deliverText(text: string): void;
  deliverBinary(data: ArrayBuffer): void;
  sentToPeer(): Array<string | ArrayBuffer>;
}

function fakeChannels(api = new FakeDataChannel('api')): { [K in keyof RemoteChannels]: FakeDataChannel } {
  return {
    control: new FakeDataChannel('control'),
    scope: new FakeDataChannel('scope'),
    audio: new FakeDataChannel('audio'),
    api,
  };
}

function webSocketHarness(): BundleHarness {
  instances.length = 0;
  vi.stubGlobal('WebSocket', MockWebSocket);
  return {
    bundle: webSocketBundle(),
    handshakeTexts: [],
    openControl: (transport) => {
      transport.open('ws://ignored/', { protocols: ['rigplane-controller-v1.KEY'], role: 'primary' });
      instances[0].simulateOpen();
    },
    deliverText: (text) => instances[0].simulateMessage(text),
    deliverBinary: (data) => instances[0].simulateMessage(data),
    sentToPeer: () => instances[0].sent.slice(),
  };
}

function dataChannelHarness(channels = fakeChannels()): BundleHarness {
  return {
    bundle: dataChannelBundle(channels),
    // The control hello proves the attach and is also an app frame.
    handshakeTexts: [goldenDocs.control_hello.text as string],
    openControl: (transport) => {
      transport.open('ws://ignored/', { protocols: ['rigplane-controller-v1.KEY'], role: 'primary' });
      channels.control.simulateOpen();
      channels.control.simulateMessage(goldenDocs.control_hello.text as string);
    },
    deliverText: (text) => channels.control.simulateMessage(text),
    deliverBinary: (data) => channels.control.simulateMessage(data),
    sentToPeer: () => channels.control.sent.slice(),
  };
}

describe('transport bundle seam (C5b)', () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    installBundle(webSocketBundle());
  });

  it.each([
    ['websocket', webSocketHarness],
    ['datachannel', () => dataChannelHarness()],
  ])('%s bundle: the factory reads the installed bundle; text and binary travel both ways', (_kind, make) => {
    const harness = make();
    installBundle(harness.bundle);

    const transport = createMessageTransport('control');
    const texts: string[] = [];
    const bins: ArrayBuffer[] = [];
    let opened = 0;
    transport.onText((text) => texts.push(text));
    transport.onBinary((data) => bins.push(data));
    transport.onOpen(() => { opened += 1; });

    harness.openControl(transport);
    expect(opened, 'open fires once').toBe(1);
    expect(transport.state).toBe('open');
    harness.deliverText('{"type":"hello"}');
    const outgoing = Uint8Array.of(1, 2, 3).buffer;
    harness.deliverBinary(outgoing);
    expect(texts).toEqual([...harness.handshakeTexts, '{"type":"hello"}']);
    expect(bins).toEqual([outgoing]);
    transport.send('{"type":"command"}');
    expect(harness.sentToPeer()).toContain('{"type":"command"}');
  });

  it('apiFetch is fetch on the websocket bundle and the attached api channel on a datachannel bundle', async () => {
    // WebSocket leg: today's behaviour — the global fetch, unchanged.
    const response = new Response(JSON.stringify({ ok: true }), { status: 200 });
    const stub = vi.fn(async () => response);
    vi.stubGlobal('fetch', stub);
    installBundle(webSocketBundle());
    await expect(apiFetch('/api/v1/info')).resolves.toBe(response);
    expect(stub).toHaveBeenCalledTimes(1);
    expect(stub).toHaveBeenCalledWith('/api/v1/info', undefined);

    // Data-channel leg: the same apiFetch call travels the api transport;
    // the reply is the committed golden api_ok frames.
    const api = new FakeDataChannel('api');
    installBundle(dataChannelBundle(fakeChannels(api)));
    api.simulateOpen();
    const pending = apiFetch('/api/v1/info');
    await vi.advanceTimersByTimeAsync(0);
    expect(api.sent.filter((frame) => typeof frame !== 'string')).toHaveLength(2); // HEAD + END
    for (const frame of goldenDocs.api_ok.reply ?? []) api.simulateMessage(goldenFrame(frame.hex, 1));
    const res = await pending;
    expect(res.status).toBe(200);
    expect(stub, 'the global fetch stayed untouched').toHaveBeenCalledTimes(1);
  });

  it('each label opens on its own channel', () => {
    const channels = fakeChannels();
    installBundle(dataChannelBundle(channels));
    const cases = [
      ['scope', channels.scope, goldenAttachText('attach_scope', 'KEY'), goldenDocs.attach_ack_scope.text as string],
      ['audio', channels.audio, goldenAttachText('attach_audio', 'KEY'), goldenDocs.attach_ack_audio.text as string],
    ] as const;
    for (const [label, channel, attachText, ackText] of cases) {
      const transport = createMessageTransport(label);
      transport.open('ws://ignored/', { protocols: ['rigplane-controller-v1.KEY'], role: 'primary' });
      channel.simulateOpen();
      expect(JSON.parse(channel.textFrames()[0]), `${label} attach frame`).toEqual(JSON.parse(attachText));
      channel.simulateMessage(ackText);
      expect(transport.state, `${label} opens on its ack`).toBe('open');
    }
  });

  it('classifies the transport\'s own close reasons: an attach refusal is reported as attach_refused', async () => {
    // Drive the real transport's refusals through the bundle; the reason
    // strings come from the transport's exported constants, so a drift in
    // either file changes this outcome.
    const cases: ReadonlyArray<readonly ['keyless' | 'roleless', TransportAttach]> = [
      ['keyless', { protocols: [], role: 'primary' }],
      // A caller that omits the role (the type forbids it; the transport refuses it anyway).
      ['roleless', { protocols: ['rigplane-controller-v1.KEY'] } as unknown as TransportAttach],
    ];
    for (const [name, attach] of cases) {
      const losses: string[] = [];
      const bundle = dataChannelBundle(fakeChannels(), (reason) => losses.push(reason));
      const closes: string[] = [];
      const transport = bundle.openChannel('control');
      transport.onClose((info) => closes.push(info.reason));
      transport.open('ws://ignored/', attach);
      await vi.advanceTimersByTimeAsync(0);
      expect(closes, name).toEqual([ATTACH_CLOSE_REASON[name]]);
      expect(losses, name).toEqual(['attach_refused']);
    }
  });

  it('a clean channel close (empty reason) is reported as channel_closed', () => {
    const channels = fakeChannels();
    const losses: string[] = [];
    const bundle = dataChannelBundle(channels, (reason) => losses.push(reason));
    bundle.openChannel('control').open('ws://ignored/', { protocols: ['rigplane-controller-v1.KEY'], role: 'primary' });
    channels.control.close();
    expect(losses).toEqual(['channel_closed']);
    expect(ATTACH_CLOSE_REASON.none).toBe('');
  });

  it('delivers binary frames from another realm (a channel created in the parent window) through the bundle', () => {
    // D3: `instanceof ArrayBuffer` is false for a buffer of another realm,
    // so scope/audio frames handed across windows would be dropped silently
    // while same-realm unit tests pass. This asserts the fix through the
    // bundle path, not only the `isArrayBuffer` unit.
    const Foreign = runInNewContext('ArrayBuffer') as ArrayBufferConstructor;
    const foreign = new Foreign(3);
    new Uint8Array(foreign).set([7, 8, 9]);
    const channels = fakeChannels();
    installBundle(dataChannelBundle(channels));

    const transport = createMessageTransport('scope');
    const bins: ArrayBuffer[] = [];
    transport.onBinary((data) => bins.push(data));
    transport.open('ws://ignored/', { protocols: ['rigplane-controller-v1.KEY'], role: 'primary' });
    channels.scope.simulateOpen();
    channels.scope.simulateMessage(goldenDocs.attach_ack_scope.text as string);
    channels.scope.simulateMessage(foreign);
    expect(bins[0], 'the foreign-realm buffer itself is delivered').toBe(foreign);
    expect(new Uint8Array(bins[0])).toEqual(Uint8Array.of(7, 8, 9));
  });
});

// ----------------------------------------------------------------------
// One boot path (C5b, PR-A): both entries — `main.ts` (the station-served
// page) and `bootRemote` (the remote-link shell) — run the one `startApp`.
//
// What is compared: the ordered construction trace (config → kit →
// activation → App/app.css evaluation → one `mount` on `#app`) and the
// installed transport bundle each entry leaves behind. The only accepted
// differences are the bundle itself and `main.ts`'s own storage-migration
// preamble, which runs before `startApp` and has no equivalent in a fresh
// remote context.

const events: string[] = [];
const mount = vi.fn((_component: unknown, _options: { target: HTMLElement }) => ({ mounted: true }));

function installMocks(): void {
  vi.doMock('../../migrate-legacy-storage', () => {
    events.push('migration');
    return {};
  });
  vi.doMock('../../../../component-kits.config', () => {
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
  vi.doMock('../../../component-kits/activation', () => ({
    activateComponentKits: async (config: { kits: readonly (() => Promise<unknown>)[] }) => {
      await Promise.all(config.kits.map((load) => load()));
      events.push('activation');
    },
  }));
  vi.doMock('../../../App.svelte', () => {
    events.push('app');
    return { default: function App() {} };
  });
  vi.doMock('../../../app.css', () => {
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

/** The station page's own preamble (the migration side-effect import) —
 * absent from the remote entry by design. */
const STATION_ONLY = 'migration';

describe('one boot path (C5b)', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    events.length = 0;
    document.body.innerHTML = '<main id="app"></main>';
  });

  it('both entries run the one startApp: identical construction trace, only the bundle differs', async () => {
    installMocks();
    const { default: started } = await import('../../../main');
    await started;
    expect(events[0], 'main.ts still migrates storage first').toBe(STATION_ONLY);
    const stationTrace = events.filter((event) => event !== STATION_ONLY);
    expect(mount).toHaveBeenCalledOnce();
    const { currentBundle } = await import('../transport-bundle');
    expect(currentBundle().kind).toBe('websocket');

    vi.resetModules();
    vi.clearAllMocks();
    events.length = 0;
    document.body.innerHTML = '<main id="app"></main>';
    installMocks();
    const { bootRemote, REMOTE_BOOT_API } = await import('../../../boot');
    expect(REMOTE_BOOT_API).toBe(1);
    bootRemote(fakeChannels(), { onLinkLost: () => {} });
    await vi.waitFor(() => expect(events.at(-1)).toBe('mount'));
    expect(events.filter((event) => event !== STATION_ONLY)).toEqual(stationTrace);
    expect(mount).toHaveBeenCalledOnce();
    expect(mount.mock.calls[0][1]).toEqual({ target: document.getElementById('app') });
    const { currentBundle: currentAfterBoot } = await import('../transport-bundle');
    expect(currentAfterBoot().kind).toBe('datachannel');
  });

  it('bootRemote serves the factory from the injected channels; loss empties the bundle until replaceChannels', async () => {
    installMocks();
    const { bootRemote } = await import('../../../boot');
    const first = fakeChannels();
    const losses: string[] = [];
    const handle = bootRemote(first, { onLinkLost: (reason) => losses.push(reason) });
    await vi.waitFor(() => expect(events).toContain('mount'));

    const { createMessageTransport } = await import('../transport-factory');
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
    // JSON spacing is not contractual: compare the decoded documents.
    expect(JSON.parse(next.control.textFrames()[0])).toEqual(JSON.parse(goldenAttachText('attach_control', 'KEY')));
    next.control.simulateMessage(goldenDocs.control_hello.text as string);
    expect(opened2).toBe(1);
    expect(losses, 'no second loss from the swap').toEqual(['channel_closed']);
  });

  it('shutdown stops serving channels without reporting a loss', async () => {
    installMocks();
    const { bootRemote } = await import('../../../boot');
    const losses: string[] = [];
    const handle = bootRemote(fakeChannels(), { onLinkLost: (reason) => losses.push(reason) });
    await vi.waitFor(() => expect(events).toContain('mount'));
    handle.shutdown();

    const { createMessageTransport } = await import('../transport-factory');
    const transport = createMessageTransport('control');
    const closes: { reason: string }[] = [];
    transport.onClose((info) => closes.push(info));
    transport.open('ws://ignored/');
    await vi.waitFor(() => expect(closes).toHaveLength(1));
    expect(losses).toEqual([]);
  });
});
