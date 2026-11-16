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
 * bundle → transport, never a direct transport constructor. The
 * data-channel rows ride an injected `RemoteLink` whose `createChannel`
 * mints a FRESH native channel per `openChannel`: a data channel behaves
 * like a WebSocket, so `close()` really closes it and a later open is a
 * new channel, never a reopened one.
 *
 * The `bootRemote` rows cover boot.ts's own handle coordination only
 * (shutdown unmounts; a start still in flight never mounts after it). The
 * real remote boot seam — production App, runtime, channels, commands —
 * is e2e row R-A1 (`frontend/tests/e2e/remote/`), not jsdom; the App and
 * `mount` below are fakes of externals, never evidence about production
 * bootstrap.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve, sep } from 'node:path';
import { runInNewContext } from 'node:vm';
import {
  installBundle, webSocketBundle, dataChannelBundle,
  type ChannelLabel, type RemoteLink, type TransportBundle,
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
  });

  it('reads the transport kind nowhere outside transport-bundle.ts and boot.ts', () => {
    // C5b pin: the transport choice is the bundle's alone, so a second
    // decider cannot appear above the seam. Pinned shapes: the
    // `datachannel` kind literal, a `bundle.kind` read, and any comparison
    // of a kind against the transport literals. Test files may name the
    // kind (these fixtures assert it); product code may not.
    const offenders: string[] = [];
    for (const full of files) {
      const rel = projectPath(full);
      if (rel === 'lib/transport/transport-bundle.ts' || rel === 'boot.ts' || rel.includes('__tests__/')) continue;
      const content = readFileSync(full, 'utf8');
      const pattern = /'datachannel'|"datachannel"|bundle\.kind|kind\s*===\s*'(?:websocket|datachannel)'/;
      for (const line of matchingLines(content, pattern)) {
        offenders.push(`${rel}: ${line.trim()}`);
      }
    }
    expect(offenders, 'transport kind read outside the bundle').toEqual([]);
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

/** The fake `RemoteLink` the unit rows inject: `createChannel` mints a
 * fresh `FakeDataChannel` per call — exactly the contract the shell's
 * link implements — and records them so a row can act as the peer. */
class FakeRemoteLink implements RemoteLink {
  readonly api = new FakeDataChannel('api');
  readonly created: FakeDataChannel[] = [];
  createChannel(label: ChannelLabel): FakeDataChannel {
    const channel = new FakeDataChannel(label);
    this.created.push(channel);
    return channel;
  }
  createdFor(label: ChannelLabel): FakeDataChannel[] {
    return this.created.filter((channel) => channel.label === label);
  }
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

function dataChannelHarness(link = new FakeRemoteLink()): BundleHarness {
  return {
    bundle: dataChannelBundle(link),
    // The control hello proves the attach and is also an app frame.
    handshakeTexts: [goldenDocs.control_hello.text as string],
    openControl: (transport) => {
      transport.open('ws://ignored/', { protocols: ['rigplane-controller-v1.KEY'], role: 'primary' });
      const control = link.createdFor('control').at(-1) as FakeDataChannel;
      control.simulateOpen();
      control.simulateMessage(goldenDocs.control_hello.text as string);
    },
    deliverText: (text) => (link.createdFor('control').at(-1) as FakeDataChannel).simulateMessage(text),
    deliverBinary: (data) => (link.createdFor('control').at(-1) as FakeDataChannel).simulateMessage(data),
    sentToPeer: () => (link.createdFor('control').at(-1) as FakeDataChannel).sent.slice(),
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
    const link = new FakeRemoteLink();
    installBundle(dataChannelBundle(link));
    link.api.simulateOpen();
    const pending = apiFetch('/api/v1/info');
    await vi.advanceTimersByTimeAsync(0);
    expect(link.api.sent.filter((frame) => typeof frame !== 'string')).toHaveLength(2); // HEAD + END
    for (const frame of goldenDocs.api_ok.reply ?? []) link.api.simulateMessage(goldenFrame(frame.hex, 1));
    const res = await pending;
    expect(res.status).toBe(200);
    expect(stub, 'the global fetch stayed untouched').toHaveBeenCalledTimes(1);
  });

  it('a request made while the api channel is connecting waits for its open, then succeeds', async () => {
    // The api channel is still connecting when the mounted App asks for
    // /api/v1/info: the request must wait for the channel's own open
    // event — never throw "send while not open" — and then travel.
    const link = new FakeRemoteLink();
    installBundle(dataChannelBundle(link));
    const pending = apiFetch('/api/v1/info');
    await vi.advanceTimersByTimeAsync(0);
    expect(link.api.sent, 'nothing left while the channel connects').toEqual([]);
    link.api.simulateOpen();
    await vi.advanceTimersByTimeAsync(0);
    expect(link.api.sent.filter((frame) => typeof frame !== 'string')).toHaveLength(2); // HEAD + END
    for (const frame of goldenDocs.api_ok.reply ?? []) link.api.simulateMessage(goldenFrame(frame.hex, 1));
    const res = await pending;
    expect(res.status).toBe(200);
  });

  it('a request made while the api channel is connecting rejects when the channel closes first', async () => {
    const link = new FakeRemoteLink();
    installBundle(dataChannelBundle(link));
    const pending = apiFetch('/api/v1/info');
    link.api.close();
    await expect(pending).rejects.toThrow(TypeError);
    expect(link.api.sent).toEqual([]);
  });

  it('a request waiting for the api channel open can be aborted while it waits', async () => {
    const link = new FakeRemoteLink();
    installBundle(dataChannelBundle(link));
    const controller = new AbortController();
    const pending = apiFetch('/api/v1/info', { signal: controller.signal });
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    expect(link.api.sent, 'no request ever started').toEqual([]);
  });

  it('each label opens on its own channel', () => {
    const link = new FakeRemoteLink();
    installBundle(dataChannelBundle(link));
    const cases = [
      ['scope', goldenAttachText('attach_scope', 'KEY'), goldenDocs.attach_ack_scope.text as string],
      ['audio', goldenAttachText('attach_audio', 'KEY'), goldenDocs.attach_ack_audio.text as string],
    ] as const;
    for (const [label, attachText, ackText] of cases) {
      const transport = createMessageTransport(label);
      transport.open('ws://ignored/', { protocols: ['rigplane-controller-v1.KEY'], role: 'primary' });
      const channel = link.createdFor(label)[0];
      channel.simulateOpen();
      expect(JSON.parse(channel.textFrames()[0]), `${label} attach frame`).toEqual(JSON.parse(attachText));
      channel.simulateMessage(ackText);
      expect(transport.state, `${label} opens on its ack`).toBe('open');
    }
  });

  it('every openChannel gets a fresh native channel; close() closes that channel only', () => {
    // The RX-off/RX-on shape: an owner closes its transport when its
    // demand ends, and the next open rides a NEW native channel — the
    // closed one is never resurrected.
    const link = new FakeRemoteLink();
    const bundle = dataChannelBundle(link);
    const attach = { protocols: ['rigplane-controller-v1.KEY'], role: 'primary' } as const;

    const first = bundle.openChannel('audio');
    first.open('ws://ignored/', attach);
    const firstChannel = link.createdFor('audio')[0];
    firstChannel.simulateOpen();
    firstChannel.simulateMessage(goldenDocs.attach_ack_audio.text as string);
    expect(first.state).toBe('open');
    first.close();
    expect(firstChannel.readyState, 'close() really closed the native channel').toBe('closed');

    const second = bundle.openChannel('audio');
    second.open('ws://ignored/', attach);
    const secondChannel = link.createdFor('audio')[1];
    expect(secondChannel, 'a fresh native channel per open').not.toBe(firstChannel);
    secondChannel.simulateOpen();
    expect(JSON.parse(secondChannel.textFrames()[0]), 'the attach went out on the fresh channel')
      .toEqual(JSON.parse(goldenAttachText('attach_audio', 'KEY')));
    secondChannel.simulateMessage(goldenDocs.attach_ack_audio.text as string);
    expect(second.state, 'the reopened channel serves again').toBe('open');
  });

  it('link loss closes every transport the bundle still tracks and reports the loss once', () => {
    const link = new FakeRemoteLink();
    const losses: string[] = [];
    const bundle = dataChannelBundle(link, (reason) => losses.push(reason));
    const attach = { protocols: ['rigplane-controller-v1.KEY'], role: 'primary' } as const;

    const control = bundle.openChannel('control');
    control.open('ws://ignored/', attach);
    link.createdFor('control')[0].simulateOpen();
    const scope = bundle.openChannel('scope');
    const scopeCloses: string[] = [];
    scope.onClose((_info) => scopeCloses.push('scope'));
    scope.open('ws://ignored/', attach);
    const audio = bundle.openChannel('audio');
    const audioCloses: string[] = [];
    audio.onClose((_info) => audioCloses.push('audio'));
    audio.open('ws://ignored/', attach);
    link.api.simulateOpen();

    // The control channel dies: the link is lost — once — and everything
    // the bundle still tracks closes with it (the owners see the close,
    // and the server's cleanup runs on the closed channels).
    link.createdFor('control')[0].close();
    expect(losses).toEqual(['channel_closed']);
    expect(scopeCloses, 'the scope owner saw its transport close').toEqual(['scope']);
    expect(audioCloses, 'the audio owner saw its transport close').toEqual(['audio']);
    expect(link.createdFor('scope')[0].readyState).toBe('closed');
    expect(link.createdFor('audio')[0].readyState).toBe('closed');
    expect(link.api.readyState).toBe('closed');
  });

  it('shutdown closes every tracked transport and the api channel, rejects REST, reports no loss', async () => {
    const link = new FakeRemoteLink();
    const losses: string[] = [];
    const bundle = dataChannelBundle(link, (reason) => losses.push(reason));
    const attach = { protocols: ['rigplane-controller-v1.KEY'], role: 'primary' } as const;

    link.api.simulateOpen();
    const control = bundle.openChannel('control');
    control.open('ws://ignored/', attach);
    link.createdFor('control')[0].simulateOpen();
    const scope = bundle.openChannel('scope');
    scope.open('ws://ignored/', attach);

    bundle.shutdown();
    expect(losses, 'intentional retirement reports no loss').toEqual([]);
    expect(link.createdFor('control')[0].readyState).toBe('closed');
    expect(link.createdFor('scope')[0].readyState).toBe('closed');
    expect(link.api.readyState).toBe('closed');
    await expect(bundle.apiFetch('/api/v1/info'), 'REST is gone after shutdown').rejects.toThrow(TypeError);
    const later = bundle.openChannel('control');
    const laterCloses: { reason: string }[] = [];
    later.onClose((info) => laterCloses.push(info));
    expect(() => later.open('ws://ignored/'), 'no throw on a dead bundle').not.toThrow();
    await vi.advanceTimersByTimeAsync(0);
    expect(laterCloses).toHaveLength(1);
  });

  it('after a loss, openChannel closes at once with the reason and creates no native channel', async () => {
    const link = new FakeRemoteLink();
    const losses: string[] = [];
    const bundle = dataChannelBundle(link, (reason) => losses.push(reason));
    // An attach-refused control channel loses the link with the classified
    // reason; a non-default reason proves the wiring below.
    const control = bundle.openChannel('control');
    control.open('ws://ignored/', { protocols: [], role: 'primary' });
    await vi.advanceTimersByTimeAsync(0);
    expect(losses).toEqual(['attach_refused']);

    const created = link.created.length;
    const scope = bundle.openChannel('scope');
    const closes: { reason: string }[] = [];
    scope.onClose((info) => closes.push(info));
    expect(() => scope.open('ws://ignored/'), 'no throw on a dead link').not.toThrow();
    await vi.advanceTimersByTimeAsync(0);
    expect(closes).toEqual([{ code: 1006, reason: 'attach_refused', wasClean: false }]);
    expect(link.created.length, 'no native channel is created on a dead link').toBe(created);
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
      const link = new FakeRemoteLink();
      const losses: string[] = [];
      const bundle = dataChannelBundle(link, (reason) => losses.push(reason));
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
    const link = new FakeRemoteLink();
    const losses: string[] = [];
    const bundle = dataChannelBundle(link, (reason) => losses.push(reason));
    bundle.openChannel('control').open('ws://ignored/', { protocols: ['rigplane-controller-v1.KEY'], role: 'primary' });
    link.createdFor('control')[0].close();
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
    const link = new FakeRemoteLink();
    installBundle(dataChannelBundle(link));

    const transport = createMessageTransport('scope');
    const bins: ArrayBuffer[] = [];
    transport.onBinary((data) => bins.push(data));
    transport.open('ws://ignored/', { protocols: ['rigplane-controller-v1.KEY'], role: 'primary' });
    link.createdFor('scope')[0].simulateOpen();
    link.createdFor('scope')[0].simulateMessage(goldenDocs.attach_ack_scope.text as string);
    link.createdFor('scope')[0].simulateMessage(foreign);
    expect(bins[0], 'the foreign-realm buffer itself is delivered').toBe(foreign);
    expect(new Uint8Array(bins[0])).toEqual(Uint8Array.of(7, 8, 9));
  });
});

// ----------------------------------------------------------------------
// bootRemote's handle (C5b, PR-A): the shell-facing coordination only —
// `shutdown()` drops the link AND unmounts the App, and a startApp still
// in flight (async kit activation/imports) never mounts after it. The
// production remote boot seam is e2e row R-A1; here the App, `mount` and
// the kit activation are fakes of externals, and the rows assert only
// boot.ts's own coordination.

const events: string[] = [];
const mount = vi.fn((_component: unknown, _options: { target: HTMLElement }) => ({ mounted: true }));
const unmount = vi.fn((_app: unknown) => { events.push('unmount'); });
let releaseActivation: (() => void) | null = null;

function installMocks(): void {
  vi.doMock('../../../../component-kits.config', () => ({
    default: {
      kits: [async () => {
        events.push('kit');
        await new Promise<void>((resolve) => { releaseActivation = resolve; });
        return { default: { apiVersion: 1, id: 'boot-kit' } };
      }],
    },
  }));
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
    unmount: (app: unknown) => unmount(app),
  }));
}

describe('bootRemote handle (C5b)', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    events.length = 0;
    releaseActivation = null;
    document.body.innerHTML = '<main id="app"></main>';
  });
  afterEach(() => { installBundle(webSocketBundle()); });

  it('shutdown unmounts the mounted App and drops the link: no loss, REST is gone', async () => {
    installMocks();
    const { bootRemote, REMOTE_BOOT_API } = await import('../../../boot');
    expect(REMOTE_BOOT_API).toBe(1);
    const link = new FakeRemoteLink();
    const losses: string[] = [];
    const handle = bootRemote(link, { onLinkLost: (reason) => losses.push(reason) });
    await vi.waitFor(() => expect(releaseActivation).not.toBeNull());
    releaseActivation?.();
    await vi.waitFor(() => expect(events).toContain('mount'));
    link.api.simulateOpen();

    handle.shutdown();
    // bootRemote unmounts through its own svelte import: one microtask later.
    await vi.waitFor(() => expect(unmount, 'the mounted App is unmounted').toHaveBeenCalledOnce());
    expect(unmount.mock.calls[0][0]).toBe(mount.mock.results[0].value);
    expect(losses, 'shutdown reports no loss').toEqual([]);
    expect(link.api.readyState, 'the api channel is closed').toBe('closed');
    await expect(apiFetch('/api/v1/info'), 'REST is gone after shutdown').rejects.toThrow(TypeError);
  });

  it('a startApp still in flight never mounts after shutdown', async () => {
    installMocks();
    const { bootRemote } = await import('../../../boot');
    const link = new FakeRemoteLink();
    const losses: string[] = [];
    const handle = bootRemote(link, { onLinkLost: (reason) => losses.push(reason) });
    // Shutdown lands while the kit activation is still pending — before
    // any App import or mount has happened.
    await vi.waitFor(() => expect(releaseActivation).not.toBeNull());
    handle.shutdown();
    releaseActivation?.();
    await vi.waitFor(() => expect(events).toContain('activation'));
    await vi.waitFor(() => expect(events).toContain('app'));
    expect(mount, 'the App is never mounted after shutdown').not.toHaveBeenCalled();
    expect(unmount, 'nothing was mounted, so nothing is unmounted').not.toHaveBeenCalled();
    expect(losses).toEqual([]);
  });
});
