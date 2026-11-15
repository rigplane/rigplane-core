/**
 * `api` channel client tests (C5a): the C3 wire format driven through
 * a real `DataChannelTransport` over the shared fake channel, frames
 * hand-built from the pinned contract (an independent oracle, like
 * `tests/test_webrtc_api_channel.py`).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { FakeDataChannel } from './support/fake-datachannel';
import { DataChannelTransport } from '../datachannel-transport';
import { createApiChannelFetch } from '../api-channel-client';
import wire from '../api-channel-wire.json';

const REQ_HEAD = 1;
const REQ_BODY = 2;
const RES_HEAD = 3;
const RES_BODY = 4;
const END = 5;
const ERROR = 6;
const CANCEL = 7;
const utf8 = new TextEncoder();

function frame(type: number, id: number, payload: Uint8Array = new Uint8Array(0)) {
  const out = new Uint8Array(7 + payload.length);
  const view = new DataView(out.buffer);
  view.setUint8(0, 1);
  view.setUint8(1, type);
  view.setUint32(2, id, false);
  view.setUint8(6, 0);
  out.set(payload, 7);
  return out;
}

/** One attached `api` channel: the fake channel, its transport, fetch. */
function openApiChannel() {
  const ch = new FakeDataChannel('api');
  const transport = new DataChannelTransport(ch);
  transport.open('ignored://');
  ch.simulateOpen();
  return { ch, api: createApiChannelFetch(transport) };
}

function reply(ch: FakeDataChannel, type: number, id: number, payload: Uint8Array = new Uint8Array(0)) {
  ch.simulateMessage(frame(type, id, payload).slice().buffer);
}


function frames(ch: FakeDataChannel) {
  return ch.sent
    .filter((f): f is ArrayBuffer => f instanceof ArrayBuffer)
    .map((raw) => {
      const bytes = new Uint8Array(raw);
      return { type: bytes[1], id: new DataView(raw).getUint32(2, false), payload: bytes.slice(7) };
    });
}

/** Complete one server response (RES_HEAD + <=16 KB chunks + END). */
function respond(ch: FakeDataChannel, id: number, status: number, body: Uint8Array) {
  reply(ch, RES_HEAD, id, utf8.encode(JSON.stringify({ status })));
  const chunk = wire.limits.response_chunk_bytes;
  for (let off = 0; off < body.length; off += chunk) {
    reply(ch, RES_BODY, id, body.slice(off, off + chunk));
  }
  reply(ch, END, id);
}

describe('createApiChannelFetch', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());
  it('sends one REQ_HEAD + END and reassembles a small response', async () => {
    const { ch, api } = openApiChannel();
    const pending = api('/api/v1/info?a=1&b=2&b=3', {
      headers: { 'X-RigPlane-Controller': 'k' },
    });
    await vi.advanceTimersByTimeAsync(0);
    const sent = frames(ch);
    expect(sent.map((f) => f.type)).toEqual([REQ_HEAD, END]);
    const head = JSON.parse(new TextDecoder().decode(sent[0].payload));
    expect(head.method).toBe('GET');
    expect(head.path).toBe('/api/v1/info');
    expect(head.query).toEqual({ a: ['1'], b: ['2', '3'] });
    expect(head.headers).toEqual({ 'x-rigplane-controller': 'k' });
    respond(ch, 1, 200, utf8.encode('{"ok":true}'));
    const res = await pending;
    expect(res.status).toBe(200);
    expect(await res.text()).toBe('{"ok":true}');
  });
  it('chunks a request body at the wire chunk size', async () => {
    const { ch, api } = openApiChannel();
    const chunk = wire.limits.response_chunk_bytes;
    const body = new Uint8Array(chunk * 2 + 100);
    const pending = api('/api/v1/controller', { method: 'POST', body: body.buffer });
    await vi.advanceTimersByTimeAsync(0);
    const sent = frames(ch);
    expect(sent.map((f) => f.type)).toEqual([REQ_HEAD, REQ_BODY, REQ_BODY, REQ_BODY, END]);
    const bodies = sent.filter((f) => f.type === REQ_BODY);
    expect(bodies.map((f) => f.payload.length)).toEqual([chunk, chunk, 100]);
    expect(new Uint8Array(bodies.flatMap((f) => [...f.payload]))).toEqual(body);
    respond(ch, 1, 204, new Uint8Array(0));
    expect((await pending).status).toBe(204);
  });
  it('reassembles a 40 KB response from 16 KB chunks', async () => {
    const { ch, api } = openApiChannel();
    const body = new Uint8Array(40 * 1024);
    for (let i = 0; i < body.length; i++) body[i] = i % 251;
    const pending = api('/api/v1/info');
    await vi.advanceTimersByTimeAsync(0);
    respond(ch, 1, 200, body);
    const res = await pending;
    expect(new Uint8Array(await res.arrayBuffer())).toEqual(body);
  });
  it('maps a terminal ERROR frame to an error Response', async () => {
    const { ch, api } = openApiChannel();
    const pending = api('/api/v1/info');
    await vi.advanceTimersByTimeAsync(0);
    reply(ch, ERROR, 1, utf8.encode(JSON.stringify({ code: 'timeout', message: 'nope' })));
    const res = await pending;
    expect(res.status).toBe(502);
    expect(res.headers.get('X-RigPlane-Api-Error')).toBe('timeout');
    expect(await res.json()).toEqual({ error: 'timeout', message: 'nope' });
  });
  it('sends CANCEL and rejects with AbortError on AbortSignal', async () => {
    const { ch, api } = openApiChannel();
    const ctrl = new AbortController();
    const pending = api('/api/v1/info', { signal: ctrl.signal });
    await vi.advanceTimersByTimeAsync(0);
    ctrl.abort();
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    expect(frames(ch).find((f) => f.type === CANCEL)?.id).toBe(1);
  });
  it('rejects with a timeout and sends CANCEL at the wire deadline', async () => {
    const { ch, api } = openApiChannel();
    let failure: unknown;
    const pending = api('/api/v1/info').catch((err: unknown) => {
      failure = err;
    });
    await vi.advanceTimersByTimeAsync(wire.limits.request_deadline_s * 1000 + 1);
    await pending;
    expect(failure).toMatchObject({ name: 'TimeoutError' });
    expect(frames(ch).some((f) => f.type === CANCEL)).toBe(true);
  });
  it('waits while bufferedAmount is above the high-water mark', async () => {
    const { ch, api } = openApiChannel();
    ch.bufferedAmount = wire.limits.send_high_water_bytes + 1;
    const pending = api('/api/v1/info');
    await vi.advanceTimersByTimeAsync(1000);
    expect(frames(ch)).toEqual([]);
    ch.bufferedAmount = 0;
    await vi.advanceTimersByTimeAsync(1000);
    expect(frames(ch)[0]?.type).toBe(REQ_HEAD);
  });
  it('uses strictly increasing request ids across requests', async () => {
    const { ch, api } = openApiChannel();
    const first = api('/api/v1/info');
    await vi.advanceTimersByTimeAsync(0);
    respond(ch, 1, 200, utf8.encode('"first"'));
    await first;
    const second = api('/api/v1/info');
    await vi.advanceTimersByTimeAsync(0);
    const ids = frames(ch).filter((f) => f.type === REQ_HEAD).map((f) => f.id);
    expect(ids).toEqual([1, 2]);
    respond(ch, 2, 200, utf8.encode('"second"'));
    expect(await second).toMatchObject({ status: 200 });
  });
});
