/**
 * `api` channel client tests (C5a): the C3 wire format driven against a
 * fake channel, with the frame bytes built by hand from the pinned
 * contract (an independent oracle, mirroring the server-side constants
 * like `tests/test_webrtc_api_channel.py` does, not a re-read of the
 * client).
 *
 * Header: `[u8 version=1][u8 type][u32 request_id BE][u8 flags]` (7 B).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createApiChannelFetch } from '../api-channel-client';
import wire from '../api-channel-wire.json';

const H = 7;
const REQ_HEAD = 1;
const REQ_BODY = 2;
const RES_HEAD = 3;
const RES_BODY = 4;
const END = 5;
const ERROR = 6;
const CANCEL = 7;

function frame(type: number, id: number, payload: Uint8Array = new Uint8Array(0)) {
  const out = new Uint8Array(H + payload.length);
  const view = new DataView(out.buffer);
  view.setUint8(0, 1);
  view.setUint8(1, type);
  view.setUint32(2, id, false);
  view.setUint8(6, 0);
  out.set(payload, H);
  return out;
}

interface ParsedFrame {
  type: number;
  id: number;
  payload: Uint8Array;
}

function parseFrames(sent: Uint8Array[]): ParsedFrame[] {
  return sent.map((raw) => ({
    type: raw[1],
    id: new DataView(raw.buffer, raw.byteOffset).getUint32(2, false),
    payload: raw.slice(H),
  }));
}

function utf8(text: string): Uint8Array {
  return new TextEncoder().encode(text);
}

/** Channel-shaped stub: captures sends, lets the test act as the server. */
class FakeApiChannel {
  sent: Uint8Array[] = [];
  bufferedAmount = 0;
  private handler: ((data: ArrayBuffer) => void) | null = null;

  send(data: string | ArrayBuffer): void {
    this.sent.push(typeof data === 'string' ? utf8(data) : new Uint8Array(data));
  }

  onBinary(handler: (data: ArrayBuffer) => void): () => void {
    this.handler = handler;
    return () => {
      this.handler = null;
    };
  }

  /** Server → client: deliver one binary frame. */
  reply(f: ParsedFrame | Uint8Array): void {
    const bytes = f instanceof Uint8Array ? f : frame(f.type, f.id, f.payload);
    this.handler?.(bytes.slice().buffer);
  }

  frames(): ParsedFrame[] {
    return parseFrames(this.sent);
  }
}

function respond(
  ch: FakeApiChannel,
  id: number,
  status: number,
  headers: Record<string, string>,
  body: Uint8Array
) {
  const chunk = wire.limits.response_chunk_bytes;
  ch.reply(frame(RES_HEAD, id, utf8(JSON.stringify({ status, headers }))));
  for (let off = 0; off < body.length; off += chunk) {
    ch.reply(frame(RES_BODY, id, body.slice(off, off + chunk)));
  }
  ch.reply(frame(END, id));
}

describe('createApiChannelFetch', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('sends one REQ_HEAD + END and reassembles a small response', async () => {
    const ch = new FakeApiChannel();
    const api = createApiChannelFetch(ch);
    const pending = api('/api/v1/info', { headers: { 'X-RigPlane-Controller': 'k' } });
    await vi.advanceTimersByTimeAsync(0);
    const frames = ch.frames();
    expect(frames.map((f) => f.type)).toEqual([REQ_HEAD, END]);
    expect(frames[0].id).toBe(1);
    expect(JSON.parse(new TextDecoder().decode(frames[0].payload))).toEqual({
      method: 'GET',
      path: '/api/v1/info',
      query: {},
      headers: { 'x-rigplane-controller': 'k' },
    });
    respond(ch, 1, 200, { 'Content-Type': 'application/json' }, utf8('{"ok":true}'));
    const res = await pending;
    expect(res.status).toBe(200);
    expect(res.headers.get('Content-Type')).toBe('application/json');
    expect(await res.text()).toBe('{"ok":true}');
  });

  it('splits the path query into multi-value arrays', async () => {
    const ch = new FakeApiChannel();
    const api = createApiChannelFetch(ch);
    const pending = api('/api/v1/things?b=2&b=3&a=1', { method: 'DELETE' });
    await vi.advanceTimersByTimeAsync(0);
    const head = JSON.parse(new TextDecoder().decode(ch.frames()[0].payload));
    expect(head).toEqual({
      method: 'DELETE',
      path: '/api/v1/things',
      query: { a: ['1'], b: ['2', '3'] },
      headers: {},
    });
    respond(ch, 1, 204, {}, new Uint8Array(0));
    const res = await pending;
    expect(res.status).toBe(204);
  });

  it('chunks a request body at the wire chunk size', async () => {
    const ch = new FakeApiChannel();
    const api = createApiChannelFetch(ch);
    const chunk = wire.limits.response_chunk_bytes;
    const body = new Uint8Array(chunk * 2 + 100);
    const pending = api('/api/v1/controller', { method: 'POST', body: body.buffer });
    await vi.advanceTimersByTimeAsync(0);
    const frames = ch.frames();
    expect(frames.map((f) => f.type)).toEqual([
      REQ_HEAD,
      REQ_BODY,
      REQ_BODY,
      REQ_BODY,
      END,
    ]);
    const bodies = frames.filter((f) => f.type === REQ_BODY);
    expect(bodies.map((f) => f.payload.length)).toEqual([chunk, chunk, 100]);
    expect(new Uint8Array([...bodies.map((f) => [...f.payload])].flat())).toEqual(body);
    respond(ch, 1, 201, {}, utf8('{}'));
    expect((await pending).status).toBe(201);
  });

  it('reassembles a 40 KB response from 16 KB chunks', async () => {
    const ch = new FakeApiChannel();
    const api = createApiChannelFetch(ch);
    const body = new Uint8Array(40 * 1024);
    for (let i = 0; i < body.length; i++) body[i] = i % 251;
    const pending = api('/api/v1/info');
    await vi.advanceTimersByTimeAsync(0);
    respond(ch, 1, 200, {}, body);
    const res = await pending;
    expect(res.status).toBe(200);
    expect(new Uint8Array(await res.arrayBuffer())).toEqual(body);
  });

  it('maps a terminal ERROR frame to an error Response', async () => {
    const ch = new FakeApiChannel();
    const api = createApiChannelFetch(ch);
    const pending = api('/api/v1/info');
    await vi.advanceTimersByTimeAsync(0);
    ch.reply(frame(ERROR, 1, utf8(JSON.stringify({ code: 'timeout', message: 'nope' }))));
    const res = await pending;
    expect(res.status).toBe(502);
    expect(res.headers.get('X-RigPlane-Api-Error')).toBe('timeout');
    expect(await res.json()).toEqual({ error: 'timeout', message: 'nope' });
  });

  it('sends CANCEL and rejects with AbortError on AbortSignal', async () => {
    const ch = new FakeApiChannel();
    const api = createApiChannelFetch(ch);
    const ctrl = new AbortController();
    const pending = api('/api/v1/info', { signal: ctrl.signal });
    await vi.advanceTimersByTimeAsync(0);
    ctrl.abort();
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    expect(ch.frames().map((f) => f.type)).toContain(CANCEL);
    expect(ch.frames().find((f) => f.type === CANCEL)?.id).toBe(1);
  });

  it('rejects with a timeout and sends CANCEL at the wire deadline', async () => {
    const ch = new FakeApiChannel();
    const api = createApiChannelFetch(ch);
    let failure: unknown;
    const pending = api('/api/v1/info').catch((err: unknown) => {
      failure = err;
    });
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(wire.limits.request_deadline_s * 1000 + 1);
    await pending;
    expect(failure).toMatchObject({ name: 'TimeoutError' });
    expect(ch.frames().map((f) => f.type)).toContain(CANCEL);
  });

  it('waits while bufferedAmount is above the high-water mark', async () => {
    const ch = new FakeApiChannel();
    const api = createApiChannelFetch(ch);
    ch.bufferedAmount = wire.limits.send_high_water_bytes + 1;
    const pending = api('/api/v1/info');
    await vi.advanceTimersByTimeAsync(1000);
    expect(ch.frames()).toEqual([]);
    ch.bufferedAmount = 0;
    await vi.advanceTimersByTimeAsync(1000);
    expect(ch.frames()[0]?.type).toBe(REQ_HEAD);
    respond(ch, 1, 200, {}, new Uint8Array(0));
    expect((await pending).status).toBe(200);
  });

  it('uses strictly increasing request ids, multiplexed out of order', async () => {
    const ch = new FakeApiChannel();
    const api = createApiChannelFetch(ch);
    const first = api('/api/v1/info');
    const second = api('/api/v1/info');
    await vi.advanceTimersByTimeAsync(0);
    const ids = ch.frames().filter((f) => f.type === REQ_HEAD).map((f) => f.id);
    expect(ids).toEqual([1, 2]);
    respond(ch, 2, 200, {}, utf8('"second"'));
    respond(ch, 1, 200, {}, utf8('"first"'));
    const resSecond = await second;
    const resFirst = await first;
    expect(resSecond.status).toBe(200);
    expect(await resSecond.text()).toBe('"second"');
    expect(await resFirst.text()).toBe('"first"');
  });
});
