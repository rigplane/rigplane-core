/**
 * `api` data-channel client (C5a): the C3 wire format over one attached
 * `api` channel, exposed as a `fetch`-compatible function so `apiFetch`
 * can be pointed at it later (the boot wiring is C5b).
 *
 * Every wire value comes from the committed single source
 * `api-channel-wire.json` (kept honest against
 * `rigplane.web.api_channel.api_channel_wire_definition` by
 * `tests/test_api_channel_wire_json.py`) — no re-typed constants here.
 * One fetch per channel: request ids are per-client.
 *
 * Outcomes: a terminal ERROR frame resolves as an error `Response`
 * (status 502, code in `X-RigPlane-Api-Error`), while local failures —
 * abort and the per-request deadline — reject like a network failure.
 * Sends wait while the channel buffers above the JSON high-water mark
 * and resume below the low-water mark.
 */
import wire from './api-channel-wire.json';

/** Backpressure poll step while the channel buffer is above high water. */
const DRAIN_POLL_MS = 10;

const HEADER_SIZE = wire.header.size_bytes;
const TYPES = wire.message_types;
const LIMITS = wire.limits;

/** Byte width of each header field type the layout may name. */
const FIELD_WIDTHS: Record<string, number> = { u8: 1, u16be: 2, u32be: 4 };

/** Header field offsets, computed from the committed layout. */
const OFFSETS: Record<string, number> = (() => {
  const offsets: Record<string, number> = {};
  let offset = 0;
  for (const field of wire.header.fields) {
    const width = FIELD_WIDTHS[field.type];
    if (width === undefined) {
      throw new Error(`api channel wire: unknown header field type ${field.type}`);
    }
    offsets[field.name] = offset;
    offset += width;
  }
  if (offset !== HEADER_SIZE) {
    throw new Error('api channel wire: header fields exceed size_bytes');
  }
  return offsets;
})();

/** The channel surface the client needs (a `DataChannelTransport` fits). */
export interface ApiChannelTransport {
  send(data: string | ArrayBuffer): void;
  onBinary(handler: (data: ArrayBuffer) => void): () => void;
  readonly bufferedAmount: number;
}

/** `fetch`-compatible request function over the `api` channel. */
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

function encodeFrame(type: number, requestId: number, payload: Uint8Array): ArrayBuffer {
  const frame = new Uint8Array(HEADER_SIZE + payload.length);
  const view = new DataView(frame.buffer);
  view.setUint8(OFFSETS.version, wire.version);
  view.setUint8(OFFSETS.type, type);
  view.setUint32(OFFSETS.request_id, requestId, false);
  view.setUint8(OFFSETS.flags, 0);
  frame.set(payload, HEADER_SIZE);
  return frame.buffer;
}

/** One fetch-compatible function over the attached `api` channel. */
export function createApiChannelFetch(transport: ApiChannelTransport): ApiChannelFetch {
  const pending = new Map<number, PendingRequest>();
  let nextId = 0;
  transport.onBinary((data) => {
    const frame = new Uint8Array(data);
    if (frame.length < HEADER_SIZE) return;
    const request = pending.get(
      new DataView(frame.buffer, frame.byteOffset, frame.byteLength).getUint32(
        OFFSETS.request_id,
        false
      )
    );
    if (request === undefined) return;
    try {
      routeFrame(request, frame);
    } catch {
      request.settle(errorResponse('malformed', 'undecodable api channel frame'));
      cleanup(request);
    }
  });

  function routeFrame(request: PendingRequest, frame: Uint8Array): void {
    const view = new DataView(frame.buffer, frame.byteOffset, frame.byteLength);
    const type = view.getUint8(OFFSETS.type);
    const payload = frame.slice(HEADER_SIZE);
    if (view.getUint8(OFFSETS.version) !== wire.version) {
      request.settle(errorResponse('bad_version', 'unsupported wire version'));
      cleanup(request);
      return;
    }
    if (type === TYPES.RES_HEAD) {
      const head = JSON.parse(decoder.decode(payload)) as {
        status?: unknown;
        headers?: unknown;
      };
      request.status = typeof head.status === 'number' ? head.status : 0;
      request.headers =
        head.headers && typeof head.headers === 'object'
          ? (head.headers as Record<string, string>)
          : {};
    } else if (type === TYPES.RES_BODY) {
      request.chunks.push(payload);
    } else if (type === TYPES.END) {
      const body = concatChunks(request.chunks);
      request.settle(
        new Response(body.length === 0 ? null : body, {
          status: request.status ?? 200,
          headers: new Headers(request.headers),
        })
      );
      cleanup(request);
    } else if (type === TYPES.ERROR) {
      const error = JSON.parse(decoder.decode(payload)) as {
        code?: unknown;
        message?: unknown;
      };
      request.settle(
        errorResponse(
          typeof error.code === 'string' ? error.code : 'internal',
          typeof error.message === 'string' ? error.message : 'api channel error'
        )
      );
      cleanup(request);
    }
  }

  const waitForDrain = async (): Promise<void> => {
    if (transport.bufferedAmount <= LIMITS.send_high_water_bytes) return;
    while (transport.bufferedAmount > LIMITS.send_low_water_bytes) {
      await new Promise((resolve) => setTimeout(resolve, DRAIN_POLL_MS));
    }
  };

  const send = (request: PendingRequest, type: number, payload: Uint8Array) => {
    transport.send(encodeFrame(type, request.id, payload));
  };

  const cancel = (request: PendingRequest) => {
    try {
      send(request, TYPES.CANCEL, new Uint8Array(0));
    } catch {
      // The channel is gone; the failure above already settles the request.
    }
  };

  const cleanup = (request: PendingRequest): void => {
    pending.delete(request.id);
    clearTimeout(request.deadline);
    if (request.onAbort !== null) request.onAbort();
  };

  const fail = (request: PendingRequest, error: Error): void => {
    request.fail(error);
    cleanup(request);
  };

  return (path, init) =>
    new Promise<Response>((resolve, reject) => {
      const id = ++nextId;
      const url = new URL(path, 'http://rigplane.api.channel');
      const query: Record<string, string[]> = {};
      for (const key of url.searchParams.keys()) {
        query[key] = url.searchParams.getAll(key);
      }
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
        deadline: setTimeout(() => {
          const error = new Error('api channel request deadline exceeded');
          error.name = 'TimeoutError';
          fail(request, error);
          cancel(request);
        }, LIMITS.request_deadline_s * 1000),
        onAbort: null,
      };
      const signal = init?.signal;
      if (signal !== undefined) {
        const onAbortEvent = () => {
          const error = new Error('The api channel request was aborted');
          error.name = 'AbortError';
          fail(request, error);
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
          if (!pending.has(id)) return;
          send(request, TYPES.END, new Uint8Array(0));
        } catch (error) {
          fail(
            request,
            error instanceof Error ? error : new Error('api channel send failed')
          );
        }
      })();
    });
}

function bodyBytes(body: BodyInit | null | undefined): Uint8Array {
  if (body === null || body === undefined) return new Uint8Array(0);
  if (typeof body === 'string') return utf8.encode(body);
  if (body instanceof ArrayBuffer) return new Uint8Array(body.slice(0));
  if (ArrayBuffer.isView(body)) {
    return new Uint8Array(body.buffer.slice(body.byteOffset, body.byteOffset + body.byteLength));
  }
  throw new TypeError('api channel: unsupported request body type');
}

function concatChunks(chunks: Uint8Array[]): Uint8Array {
  const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const out = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.length;
  }
  return out;
}

/** A terminal ERROR frame as an error `Response` (code surfaced twice). */
function errorResponse(code: string, message: string): Response {
  return new Response(JSON.stringify({ error: code, message }), {
    status: 502,
    headers: { 'Content-Type': 'application/json', 'X-RigPlane-Api-Error': code },
  });
}
