/**
 * `api` channel client tests (C5a): the committed producer-built golden
 * documents (`api-channel-golden.json`) drive the fake peer — every reply
 * decodes the committed hex, with only the envelope's request id rewritten to
 * the client's allocated id. The same documents are replayed through the real
 * server stack by the Python counterpart
 * `tests/test_api_channel_golden.py::test_golden_documents_replay_through_real_handlers`.
 */
import { runInNewContext } from 'node:vm';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { FakeDataChannel, goldenFrame, goldenDocs } from './support/fake-datachannel';
import { DataChannelTransport } from '../datachannel-transport';
import { createApiChannelFetch } from '../api-channel-client';
import wire from '../api-channel-wire.json';

const T = wire.message_types;
const H = wire.header.size_bytes;
const utf8 = new TextEncoder();
/** A hand-built frame — negative tests only; well-formed replays decode
 * the committed golden hex. */
function frame(type: number, id: number, payload: Uint8Array = new Uint8Array(0)) {
  const out = new Uint8Array(H + payload.length);
  const view = new DataView(out.buffer);
  view.setUint8(0, wire.version);
  view.setUint8(1, type);
  view.setUint32(2, id, false);
  out.set(payload, H);
  return out;
}
function frames(ch: FakeDataChannel) {
  return ch.sent
    .filter((f): f is ArrayBuffer => f instanceof ArrayBuffer)
    .map((raw) => {
      const bytes = new Uint8Array(raw);
      return { type: bytes[1], id: new DataView(raw).getUint32(2, false), payload: bytes.slice(H) };
    });
}
/** Answer with committed golden frames under the live request id. */
function replay(ch: FakeDataChannel, doc: readonly { hex: string }[], id: number) {
  for (const f of doc) ch.simulateMessage(goldenFrame(f.hex, id));
}
function b64(value: string): Uint8Array {
  return Uint8Array.from(atob(value), (c) => c.charCodeAt(0));
}
/** One attached `api` channel: the golden attach_api shape goes first,
 * opened as the real caller would — keyless acquire, its role named. */
function openApiChannel() {
  const ch = new FakeDataChannel('api');
  const transport = new DataChannelTransport(ch);
  transport.open('ignored://', { protocols: [], role: 'primary' });
  ch.simulateOpen();
  expect(ch.textFrames().map((f) => JSON.parse(f as string))).toEqual([goldenDocs.attach_api.decoded]);
  return { ch, api: createApiChannelFetch(transport) };
}

/** An ArrayBuffer of another realm: what a channel created in a parent window
 * delivers when the UI runs inside an iframe (`instanceof ArrayBuffer` is false). */
function foreignBuffer(bytes: Uint8Array): ArrayBuffer {
  const Foreign = runInNewContext('ArrayBuffer') as ArrayBufferConstructor;
  const out = new Foreign(bytes.length);
  new Uint8Array(out).set(bytes);
  return out;
}

describe('createApiChannelFetch', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());
  it('replays api_ok, api_chunked and api_not_found with complete heads', async () => {
    const { ch, api } = openApiChannel();
    const ok = api('/api/v1/band-plan/layers');
    await vi.advanceTimersByTimeAsync(0);
    const sent = frames(ch);
    expect(sent.map((f) => f.type)).toEqual([T.REQ_HEAD, T.END]);
    // The request head is exactly the golden document's payload.
    expect(JSON.parse(new TextDecoder().decode(sent[0].payload))).toEqual(goldenDocs.api_ok.request?.[0]?.payload);
    replay(ch, goldenDocs.api_ok.reply ?? [], 1);
    const res = await ok;
    expect(res.status).toBe(200);
    expect(res.headers.get('content-length')).toBe('16');
    expect(res.headers.get('x-frame-options')).toBe('DENY');
    expect(new Uint8Array(await res.arrayBuffer())).toEqual(b64(goldenDocs.api_ok.response_body_b64 ?? ''));
    const chunked = api('/api/v1/band-plan/layers');
    await vi.advanceTimersByTimeAsync(0);
    replay(ch, goldenDocs.api_chunked.reply ?? [], 2);
    const big = await chunked;
    expect(big.headers.get('content-length')).toBe('16385');
    expect(new Uint8Array(await big.arrayBuffer())).toEqual(b64(goldenDocs.api_chunked.response_body_b64 ?? ''));
    const missing = api('/api/v1/nonexistent');
    await vi.advanceTimersByTimeAsync(0);
    replay(ch, goldenDocs.api_not_found.reply ?? [], 3);
    const notFound = await missing;
    expect(notFound.status).toBe(404);
    expect(await notFound.text()).toBe('404 Not Found');
    expect(frames(ch).filter((f) => f.type === T.REQ_HEAD).map((f) => f.id)).toEqual([1, 2, 3]);
  });
  it('settles golden ERROR frames as 502 responses; contract violations are protocol errors', async () => {
    const { ch, api } = openApiChannel();
    const first = api('/api/v1/info');
    await vi.advanceTimersByTimeAsync(0);
    const timeout = (goldenDocs.api_errors.frames ?? []).find(
      (f) => (f.payload as { code?: string } | null)?.code === 'timeout',
    );
    expect(timeout).toBeDefined(); // payloads.error.codes, committed hex
    replay(ch, [timeout ?? { hex: '' }], 1);
    const res = await first;
    expect(res.status).toBe(502);
    expect(res.headers.get('X-RigPlane-Api-Error')).toBe('timeout');
    expect(await res.json()).toEqual({ error: 'timeout', message: 'request timed out' });
    // An ERROR code outside payloads.error.codes is a protocol error.
    const second = api('/api/v1/info');
    await vi.advanceTimersByTimeAsync(0);
    ch.simulateMessage(frame(T.ERROR, 2, utf8.encode('{"code":"nope","message":"x"}')).buffer);
    expect((await second).headers.get('X-RigPlane-Api-Error')).toBe('malformed');
    // So is a RES_HEAD missing the contract-required headers field.
    const third = api('/api/v1/info');
    await vi.advanceTimersByTimeAsync(0);
    ch.simulateMessage(frame(T.RES_HEAD, 3, utf8.encode('{"status":200}')).buffer);
    expect((await third).headers.get('X-RigPlane-Api-Error')).toBe('malformed');
    // Golden frames for an unknown id (api_unknown_id_ignored) are a no-op;
    // the live request still completes.
    const live = api('/api/v1/info');
    await vi.advanceTimersByTimeAsync(0);
    for (const f of goldenDocs.api_unknown_id_ignored.request ?? []) ch.simulateMessage(goldenFrame(f.hex));
    replay(ch, goldenDocs.api_ok.reply ?? [], 4);
    expect((await live).status).toBe(200);
  });
  it('fails outstanding requests with a connection error when the channel closes', async () => {
    const { ch, api } = openApiChannel();
    const base = vi.getTimerCount();
    // Stalled before its HEAD (above the high-water mark) and awaiting a
    // reply: both must fail on close, not on their later deadlines.
    ch.bufferedAmount = wire.limits.send_high_water_bytes + 1;
    const stalled = api('/api/v1/info').catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(100);
    ch.bufferedAmount = 0;
    const waiting = api('/api/v1/info').catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(0);
    expect(frames(ch).map((f) => f.type)).toEqual([T.REQ_HEAD, T.END]);
    ch.close();
    await vi.advanceTimersByTimeAsync(wire.limits.request_deadline_s * 1000 + 1); // past every deadline
    await expect(stalled).resolves.toMatchObject({ name: 'TypeError' }); // a connection error
    await expect(waiting).resolves.toMatchObject({ name: 'TypeError' }); // not a TimeoutError
    expect(vi.getTimerCount()).toBe(base); // deadlines and drain polls cleared
    expect(frames(ch)).toHaveLength(2); // nothing more was sent on the dead channel
  });
  it('rejects golden frames whose version byte is not the committed one', async () => {
    const { ch, api } = openApiChannel();
    const reply = goldenDocs.api_ok.reply ?? [];
    // A wrong-version RES_HEAD is a protocol error, never the golden 200 head.
    const first = api('/api/v1/info');
    await vi.advanceTimersByTimeAsync(0);
    const badHead = new Uint8Array(goldenFrame(reply[0].hex, 1));
    badHead[0] = wire.version + 1;
    ch.simulateMessage(badHead.buffer);
    ch.simulateMessage(goldenFrame(reply[reply.length - 1].hex, 1)); // the golden END
    const res = await first;
    expect(res.status).toBe(502);
    expect(res.headers.get('X-RigPlane-Api-Error')).toBe('bad_version');
    // A wrong-version END never settles the request as a normal response.
    const second = api('/api/v1/info');
    await vi.advanceTimersByTimeAsync(0);
    ch.simulateMessage(goldenFrame(reply[0].hex, 2)); // routed: the golden 200 head
    const badEnd = new Uint8Array(goldenFrame(reply[reply.length - 1].hex, 2));
    badEnd[0] = 0;
    ch.simulateMessage(badEnd.buffer);
    const res2 = await second;
    expect(res2.status).toBe(502);
    expect(res2.headers.get('X-RigPlane-Api-Error')).toBe('bad_version');
  });
  it('aborts like api_cancel: HEAD then CANCEL, and nothing after', async () => {
    const { ch, api } = openApiChannel();
    const ctrl = new AbortController();
    const pending = api('/api/v1/info', { signal: ctrl.signal });
    await Promise.resolve(); // one microtask: the HEAD has left, the END has not
    ctrl.abort();
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    // The golden document's trailing END is server tolerance; the settled
    // client sends nothing after CANCEL.
    expect(frames(ch).map((f) => f.type)).toEqual([T.REQ_HEAD, T.CANCEL]);
  });
  it('sends nothing when a request settles before or during backpressure', async () => {
    const { ch, api } = openApiChannel();
    const base = vi.getTimerCount();
    // An already-aborted bodyless request sends nothing at all.
    const ctrl = new AbortController();
    ctrl.abort();
    await expect(api('/api/v1/controller', { method: 'DELETE', signal: ctrl.signal })).rejects.toMatchObject({
      name: 'AbortError',
    });
    expect(frames(ch)).toEqual([]);
    expect(vi.getTimerCount()).toBe(base);
    // Abort under backpressure: never sent, the drain poll stops with it.
    ch.bufferedAmount = wire.limits.send_high_water_bytes + 1;
    const stalledCtrl = new AbortController();
    const stalled = api('/api/v1/info', { signal: stalledCtrl.signal });
    await vi.advanceTimersByTimeAsync(200);
    stalledCtrl.abort();
    await expect(stalled).rejects.toMatchObject({ name: 'AbortError' });
    await vi.advanceTimersByTimeAsync(200);
    expect(frames(ch)).toEqual([]); // the id was never allocated
    expect(vi.getTimerCount()).toBe(base);
    // The deadline under backpressure behaves the same, without a signal.
    const timed = api('/api/v1/info').catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(wire.limits.request_deadline_s * 1000 + 1);
    expect(await timed).toMatchObject({ name: 'TimeoutError' });
    expect(frames(ch)).toEqual([]);
    expect(vi.getTimerCount()).toBe(base);
    ch.bufferedAmount = 0; // release the stall: nothing more is ever sent
    await vi.advanceTimersByTimeAsync(100);
    expect(frames(ch)).toEqual([]);
  });
  it('keeps HEAD ids in emission order under high→intermediate→low backpressure', async () => {
    const { ch, api } = openApiChannel();
    ch.bufferedAmount = wire.limits.send_high_water_bytes + 1;
    const early = api('/api/v1/band-plan/layers'); // above high-water: waits for low
    await vi.advanceTimersByTimeAsync(50);
    ch.bufferedAmount = 131072; // below high-water, above low-water
    const late = api('/api/v1/band-plan/layers'); // admitted at once
    await vi.advanceTimersByTimeAsync(50);
    // The later request's HEAD left first, so it must carry the lower id.
    expect(frames(ch).filter((f) => f.type === T.REQ_HEAD).map((f) => f.id)).toEqual([1]);
    ch.bufferedAmount = 0;
    await vi.advanceTimersByTimeAsync(50);
    expect(frames(ch).filter((f) => f.type === T.REQ_HEAD).map((f) => f.id)).toEqual([1, 2]);
    replay(ch, goldenDocs.api_ok.reply ?? [], 1);
    replay(ch, goldenDocs.api_ok.reply ?? [], 2);
    expect((await early).status).toBe(200);
    expect((await late).status).toBe(200);
  });
  it('chunks a request body at the wire chunk size; query and headers fold into the head', async () => {
    const { ch, api } = openApiChannel();
    const chunk = wire.limits.response_chunk_bytes;
    const body = new Uint8Array(chunk * 2 + 100);
    const pending = api('/api/v1/controller?a=1&b=2&b=3', {
      method: 'POST',
      body: body.buffer,
      headers: { 'X-RigPlane-Controller': 'k' },
    });
    await vi.advanceTimersByTimeAsync(0);
    const sent = frames(ch);
    expect(sent.map((f) => f.type)).toEqual([T.REQ_HEAD, T.REQ_BODY, T.REQ_BODY, T.REQ_BODY, T.END]);
    const bodies = sent.filter((f) => f.type === T.REQ_BODY);
    expect(bodies.map((f) => f.payload.length)).toEqual([chunk, chunk, 100]);
    expect(new Uint8Array(bodies.flatMap((f) => [...f.payload]))).toEqual(body);
    expect(JSON.parse(new TextDecoder().decode(sent[0].payload))).toEqual({
      method: 'POST',
      path: '/api/v1/controller',
      query: { a: ['1'], b: ['2', '3'] },
      headers: { 'x-rigplane-controller': 'k' },
    });
    replay(ch, goldenDocs.api_ok.reply ?? [], 1);
    expect((await pending).status).toBe(200);
  });
  it('handles binary frames and request bodies that come from another realm', async () => {
    const { ch, api } = openApiChannel();
    const body = foreignBuffer(utf8.encode('hello'));
    expect(body instanceof ArrayBuffer).toBe(false);
    const pending = api('/api/v1/controller', { method: 'POST', body });
    await vi.advanceTimersByTimeAsync(0);
    const sent = frames(ch);
    expect(sent.map((f) => f.type)).toEqual([T.REQ_HEAD, T.REQ_BODY, T.END]);
    expect(new TextDecoder().decode(sent[1].payload)).toBe('hello');
    for (const f of goldenDocs.api_ok.reply ?? []) {
      ch.simulateMessage(foreignBuffer(new Uint8Array(goldenFrame(f.hex, 1))));
    }
    expect((await pending).status).toBe(200);
  });
});
