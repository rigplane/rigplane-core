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
import { createMessageTransport } from '../transport-factory';
import { apiFetch } from '../../api/api-fetch';
import type { MessageTransport } from '../message-transport';
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
    expect(offenders).toEqual([]);
  });
});

/** Per-producer peer plumbing for the parametrised scenario: one way to
 * open the control channel, deliver text/binary, and read what left. */
interface BundleHarness {
  readonly bundle: TransportBundle;
  openControl(transport: MessageTransport): void;
  deliverText(text: string): void;
  deliverBinary(data: ArrayBuffer): void;
  sentToPeer(): Array<string | ArrayBuffer>;
}

function fakeChannels(api = new FakeDataChannel('api')): RemoteChannels {
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
    openControl: (transport) => {
      transport.open('ws://ignored/', { protocols: ['rigplane-controller-v1.KEY'], role: 'primary' });
      instances[0].simulateOpen();
    },
    deliverText: (text) => instances[0].simulateMessage(text),
    deliverBinary: (data) => instances[0].simulateMessage(data),
    sentToPeer: () => instances[0].sent.slice(),
  };
}

function dataChannelHarness(channels: RemoteChannels = fakeChannels()): BundleHarness {
  return {
    bundle: dataChannelBundle(channels),
    openControl: (transport) => {
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
    expect(texts).toEqual(['{"type":"hello"}']);
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
      expect(channel.textFrames()[0], `${label} attach frame`).toBe(attachText);
      channel.simulateMessage(ackText);
      expect(transport.state, `${label} opens on its ack`).toBe('open');
    }
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
