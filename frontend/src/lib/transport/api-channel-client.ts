/**
 * `api` data-channel client (C5a): the C3 wire format over one attached
 * `api` channel, as a `fetch`-compatible function. Every wire value
 * comes from the committed single source `api-channel-wire.json`; frame
 * payloads are built and validated against its `payloads` contracts (a
 * violating payload is a protocol error), one fetch per channel. A wire
 * id is allocated when the HEAD is emitted, so ids leave in increasing
 * order under backpressure; drain waits end when the request settles, so
 * a cancelled or timed-out request sends nothing further. A terminal
 * ERROR frame resolves as an error Response (502, code in
 * `X-RigPlane-Api-Error`); abort and the deadline reject.
 */
import wire from './api-channel-wire.json';
import {
  buildContractPayload,
  payloadMatchesContract,
  type DataChannelTransport,
  type PayloadContract,
} from './datachannel-transport';

const HEADER_SIZE = wire.header.size_bytes;
const TYPES = wire.message_types;
const LIMITS = wire.limits;
const REQUEST_HEAD_CONTRACT: PayloadContract = wire.payloads.request_head;
const RESPONSE_HEAD_CONTRACT: PayloadContract = wire.payloads.response_head;
const ERROR_CONTRACT: PayloadContract = wire.payloads.error;
const ERROR_CODES = new Set<string>(wire.payloads.error.codes);
const DRAIN_POLL_MS = 10;
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

/** The `payloads.response_head` contract's frame payload. */
export interface ApiResponseHead {
  readonly status: number;
  readonly headers: Record<string, string>;
}
/** The `payloads.error` contract's frame payload. */
export interface ApiErrorFrame {
  readonly code: string;
  readonly message: string;
}

interface PendingRequest {
  id: number | null; // the wire id, allocated when the HEAD is emitted
  settled: boolean;
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

function parseJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

/** A parsed payload checked against its contract — throws on violation. */
function contractPayload(contract: PayloadContract, text: string): Record<string, unknown> {
  const value = parseJson(text);
  if (!payloadMatchesContract(contract, value)) {
    throw new Error('api channel: payload violates its wire contract');
  }
  return value as Record<string, unknown>;
}

function decodeResponseHead(text: string): ApiResponseHead {
  const value = contractPayload(RESPONSE_HEAD_CONTRACT, text);
  return { status: value.status as number, headers: value.headers as Record<string, string> };
}

function decodeErrorFrame(text: string): ApiErrorFrame {
  const value = contractPayload(ERROR_CONTRACT, text);
  const frame: ApiErrorFrame = { code: value.code as string, message: value.message as string };
  if (!ERROR_CODES.has(frame.code)) {
    throw new Error(`api channel: error code ${frame.code} outside the wire contract`);
  }
  return frame;
}

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
    request.settled = true;
    if (request.id !== null) pending.delete(request.id);
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
    transport.send(encodeFrame(type, r.id as number, payload));
  };
  const cancel = (r: PendingRequest): void => {
    if (r.id === null) return; // the HEAD never left: the server has nothing to drop
    try {
      send(r, TYPES.CANCEL, new Uint8Array(0));
    } catch {
      /* channel gone; the failure already settled the request */
    }
  };
  /** Wait for the send buffer, but end as soon as the request settles.
   * False means: stop sending — the request is no longer live. */
  const waitForDrain = async (request: PendingRequest): Promise<boolean> => {
    if (transport.bufferedAmount <= LIMITS.send_high_water_bytes) return !request.settled;
    while (transport.bufferedAmount > LIMITS.send_low_water_bytes) {
      if (request.settled) return false;
      await new Promise((resolve) => setTimeout(resolve, DRAIN_POLL_MS));
    }
    return !request.settled;
  };
  const routeFrame = (request: PendingRequest, frame: Uint8Array): void => {
    const view = new DataView(frame.buffer, frame.byteOffset, frame.byteLength);
    const payload = frame.slice(HEADER_SIZE);
    switch (view.getUint8(OFFSETS.type)) {
      case TYPES.RES_HEAD: {
        const head = decodeResponseHead(decoder.decode(payload));
        request.status = head.status;
        request.headers = head.headers;
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
        const error = decodeErrorFrame(decoder.decode(payload));
        settleWith(request, errorResponse(error.code, error.message));
        return;
      }
    }
  };
  transport.onBinary((data) => {
    const frame = new Uint8Array(data);
    if (frame.length < HEADER_SIZE) return;
    const view = new DataView(frame.buffer, frame.byteOffset, frame.byteLength);
    const request = pending.get(view.getUint32(OFFSETS.request_id, false));
    if (request === undefined) return; // an unknown id is a no-op
    try {
      routeFrame(request, frame);
    } catch {
      settleWith(request, errorResponse('malformed', 'undecodable api channel frame'));
    }
  });
  return (path, init) =>
    new Promise<Response>((resolve, reject) => {
      const url = new URL(path, 'http://rigplane.api.channel');
      const query: Record<string, string[]> = {};
      for (const key of url.searchParams.keys()) query[key] = url.searchParams.getAll(key);
      const headers: Record<string, string> = {};
      new Headers(init?.headers).forEach((value, key) => {
        headers[key] = value;
      });
      const head = utf8.encode(
        buildContractPayload(REQUEST_HEAD_CONTRACT, {
          method: (init?.method ?? 'GET').toUpperCase(),
          path: url.pathname,
          query,
          headers,
        })
      );
      const body = bodyBytes(init?.body);
      const request: PendingRequest = {
        id: null,
        settled: false,
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
      void (async () => {
        try {
          await waitForDrain(request);
          if (request.settled) return;
          request.id = ++nextId; // allocated at emission: ids leave in order
          pending.set(request.id, request);
          send(request, TYPES.REQ_HEAD, head);
          const chunkSize = LIMITS.response_chunk_bytes;
          for (let offset = 0; offset < body.length; offset += chunkSize) {
            await waitForDrain(request);
            if (request.settled) return; // aborted or timed out mid-body
            send(request, TYPES.REQ_BODY, body.slice(offset, offset + chunkSize));
          }
          await waitForDrain(request);
          if (request.settled) return;
          send(request, TYPES.END, new Uint8Array(0));
        } catch (error) {
          if (!request.settled) fail(request, error instanceof Error ? error : new Error('send failed'));
        }
      })();
    });
}
