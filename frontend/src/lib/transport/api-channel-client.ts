/**
 * `api` data-channel client (C5a): the C3 wire format over one attached
 * `api` channel, as a `fetch`-compatible function `apiFetch` can be
 * pointed at later (boot wiring is C5b). Every wire value comes from
 * the committed single source `api-channel-wire.json`; one fetch per
 * channel, ids are per-client. A terminal ERROR frame resolves as an
 * error Response (502, code in `X-RigPlane-Api-Error`); abort and the
 * deadline reject; sends wait above the JSON high-water mark.
 */
import wire from './api-channel-wire.json';
import type { DataChannelTransport } from './datachannel-transport';

const HEADER_SIZE = wire.header.size_bytes;
const TYPES = wire.message_types;
const LIMITS = wire.limits;
const FIELD_WIDTHS: Record<string, number> = { u8: 1, u32be: 4 };
const OFFSETS: Record<string, number> = (() => {
  const offsets: Record<string, number> = {};
  let offset = 0;
  for (const field of wire.header.fields) {
    const width = FIELD_WIDTHS[field.type];
    if (width === undefined) throw new Error(`api channel wire: bad field ${field.type}`);
    offsets[field.name] = offset;
    offset += width;
  }
  if (offset !== HEADER_SIZE) throw new Error('api channel wire: bad size_bytes');
  return offsets;
})();

export type ApiChannelFetch = (path: string, init?: RequestInit) => Promise<Response>;

interface PendingRequest {
  id: number;
  status: number | null;
  headers: Record<string, string>;
  chunks: Uint8Array[];
  settle: (response: Response) => void;
  fail: (error: Error) => void;
  deadline: ReturnType<typeof setTimeout>;
  onAbort: (() => void) | null;
}

const utf8 = new TextEncoder();
const decoder = new TextDecoder();

function encodeFrame(type: number, id: number, payload: Uint8Array): ArrayBuffer {
  const frame = new Uint8Array(HEADER_SIZE + payload.length);
  const view = new DataView(frame.buffer);
  view.setUint8(OFFSETS.version, wire.version);
  view.setUint8(OFFSETS.type, type);
  view.setUint32(OFFSETS.request_id, id, false);
  view.setUint8(OFFSETS.flags, 0);
  frame.set(payload, HEADER_SIZE);
  return frame.buffer;
}

function bodyBytes(body: BodyInit | null | undefined): Uint8Array {
  if (body === null || body === undefined) return new Uint8Array(0);
  if (typeof body === 'string') return utf8.encode(body);
  if (body instanceof ArrayBuffer) return new Uint8Array(body.slice(0));
  if (ArrayBuffer.isView(body)) return new Uint8Array(body.buffer.slice(body.byteOffset, body.byteOffset + body.byteLength));
  throw new TypeError('api channel: unsupported request body type');
}

function concat(chunks: Uint8Array[]): Uint8Array<ArrayBuffer> {
  // A fresh copy backed by its own ArrayBuffer: a proper BodyInit for
  // Response (a Uint8Array view over a shared/pooled buffer is not).
  const bytes = new Uint8Array(chunks.reduce((size, chunk) => size + chunk.length, 0));
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  return bytes;
}
function errorResponse(code: string, message: string): Response {
  return new Response(JSON.stringify({ error: code, message }), {
    status: 502,
    headers: { 'Content-Type': 'application/json', 'X-RigPlane-Api-Error': code },
  });
}

export function createApiChannelFetch(
  transport: Pick<DataChannelTransport, 'send' | 'onBinary' | 'bufferedAmount'>
): ApiChannelFetch {
  const pending = new Map<number, PendingRequest>();
  let nextId = 0;
  const cleanup = (request: PendingRequest): void => {
    pending.delete(request.id);
    clearTimeout(request.deadline);
    request.onAbort?.();
  };
  const settleWith = (request: PendingRequest, response: Response): void => {
    request.settle(response);
    cleanup(request);
  };
  const fail = (request: PendingRequest, error: Error): void => {
    request.fail(error);
    cleanup(request);
  };
  const send = (r: PendingRequest, type: number, payload: Uint8Array): void => {
    transport.send(encodeFrame(type, r.id, payload));
  };
  const cancel = (r: PendingRequest): void => {
    try {
      send(r, TYPES.CANCEL, new Uint8Array(0));
    } catch {
      /* channel gone; the failure already settled the request */
    }
  };
  const routeFrame = (request: PendingRequest, frame: Uint8Array): void => {
    const view = new DataView(frame.buffer, frame.byteOffset, frame.byteLength);
    const payload = frame.slice(HEADER_SIZE);
    switch (view.getUint8(OFFSETS.type)) {
      case TYPES.RES_HEAD: {
        const head = JSON.parse(decoder.decode(payload)) as Record<string, unknown>;
        request.status = typeof head.status === 'number' ? head.status : 0;
        request.headers =
          head.headers && typeof head.headers === 'object'
            ? (head.headers as Record<string, string>)
            : {};
        return;
      }
      case TYPES.RES_BODY:
        request.chunks.push(payload);
        return;
      case TYPES.END: {
        const body = concat(request.chunks);
        settleWith(
          request,
          new Response(body.length === 0 ? null : body.buffer, {
            status: request.status ?? 200,
            headers: new Headers(request.headers),
          })
        );
        return;
      }
      case TYPES.ERROR: {
        const error = JSON.parse(decoder.decode(payload)) as Record<string, unknown>;
        settleWith(
          request,
          errorResponse(
            typeof error.code === 'string' ? error.code : 'internal',
            typeof error.message === 'string' ? error.message : 'api channel error'
          )
        );
        return;
      }
    }
  };
  transport.onBinary((data) => {
    const frame = new Uint8Array(data);
    if (frame.length < HEADER_SIZE) return;
    const view = new DataView(frame.buffer, frame.byteOffset, frame.byteLength);
    const request = pending.get(view.getUint32(OFFSETS.request_id, false));
    if (request === undefined) return;
    try {
      routeFrame(request, frame);
    } catch {
      settleWith(request, errorResponse('malformed', 'undecodable api channel frame'));
    }
  });
  const waitForDrain = async (): Promise<void> => {
    if (transport.bufferedAmount <= LIMITS.send_high_water_bytes) return;
    while (transport.bufferedAmount > LIMITS.send_low_water_bytes) {
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
  };
  return (path, init) =>
    new Promise<Response>((resolve, reject) => {
      const id = ++nextId;
      const url = new URL(path, 'http://rigplane.api.channel');
      const query: Record<string, string[]> = {};
      for (const key of url.searchParams.keys()) query[key] = url.searchParams.getAll(key);
      const headers: Record<string, string> = {};
      new Headers(init?.headers).forEach((value, key) => {
        headers[key] = value;
      });
      const head = utf8.encode(
        JSON.stringify({
          method: (init?.method ?? 'GET').toUpperCase(),
          path: url.pathname,
          query,
          headers,
        })
      );
      const body = bodyBytes(init?.body);
      const request: PendingRequest = {
        id,
        status: null,
        headers: {},
        chunks: [],
        settle: resolve,
        fail: reject,
        deadline: setTimeout(
          () => {
            fail(request, Object.assign(new Error('deadline exceeded'), { name: 'TimeoutError' }));
            cancel(request);
          },
          LIMITS.request_deadline_s * 1000
        ),
        onAbort: null,
      };
      const signal = init?.signal;
      if (signal) {
        const onAbortEvent = () => {
          fail(request, Object.assign(new Error('The api channel request was aborted'), { name: 'AbortError' }));
          cancel(request);
        };
        request.onAbort = () => signal.removeEventListener('abort', onAbortEvent);
        if (signal.aborted) {
          onAbortEvent();
          return;
        }
        signal.addEventListener('abort', onAbortEvent);
      }
      pending.set(id, request);
      void (async () => {
        try {
          await waitForDrain();
          send(request, TYPES.REQ_HEAD, head);
          const chunkSize = LIMITS.response_chunk_bytes;
          for (let offset = 0; offset < body.length; offset += chunkSize) {
            await waitForDrain();
            if (!pending.has(id)) return; // aborted or timed out mid-body
            send(request, TYPES.REQ_BODY, body.slice(offset, offset + chunkSize));
          }
          await waitForDrain();
          send(request, TYPES.END, new Uint8Array(0));
        } catch (error) {
          fail(request, error instanceof Error ? error : new Error('send failed'));
        }
      })();
    });
}
