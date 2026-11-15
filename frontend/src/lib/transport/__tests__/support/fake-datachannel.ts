// Shared fake `RTCDataChannel` for the C5a tests: records sends, lets
// the test act as the peer, counts bufferedAmount, and decodes the
// committed golden documents' frames (`api-channel-golden.json`).
import golden from '../../api-channel-golden.json';
import type { DataChannelLike } from '../../datachannel-transport';

export interface GoldenFrameDoc {
  readonly hex: string;
  readonly payload?: unknown;
}
export interface GoldenDoc {
  readonly text?: string;
  readonly decoded?: Record<string, unknown> | null;
  readonly request?: readonly GoldenFrameDoc[];
  readonly reply?: readonly GoldenFrameDoc[];
  readonly frames?: readonly GoldenFrameDoc[];
  readonly response_body_b64?: string | null;
}
export const goldenDocs = golden.documents as Record<string, GoldenDoc>;

export class FakeDataChannel implements DataChannelLike {
  readyState: RTCDataChannelState = 'connecting';
  binaryType: BinaryType = 'blob';
  readonly label: string;
  bufferedAmount = 0;
  sent: Array<string | ArrayBuffer> = [];
  // This-less callbacks: assignable to the DOM handler types (a function
  // without a this parameter fits any this), and callable on the fake.
  onopen: ((ev: Event) => unknown) | null = null;
  onmessage: ((ev: MessageEvent) => unknown) | null = null;
  onclose: ((ev: Event) => unknown) | null = null;
  onerror: ((ev: Event) => unknown) | null = null;
  constructor(label = 'control') {
    this.label = label;
  }
  send(data: string | ArrayBuffer | ArrayBufferView | Blob): void {
    this.sent.push(data as string | ArrayBuffer);
  }
  close(): void {
    if (this.readyState === 'closed') return;
    this.readyState = 'closed';
    this.onclose?.(new Event('close'));
  }
  simulateOpen(): void {
    this.readyState = 'open';
    this.onopen?.(new Event('open'));
  }
  simulateMessage(data: unknown): void {
    this.onmessage?.({ data } as unknown as MessageEvent);
  }
  simulateError(): void {
    this.onerror?.(new Event('error'));
  }
  textFrames(): string[] {
    return this.sent.filter((f): f is string => typeof f === 'string');
  }
}

/** One committed golden frame, decoded; `id` rewrites the envelope's
 * request id (the payload bytes stay exactly the committed ones). */
export function goldenFrame(hex: string, id = -1): ArrayBuffer {
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < bytes.length; i++) bytes[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  if (id >= 0) new DataView(bytes.buffer).setUint32(2, id, false);
  return bytes.buffer as ArrayBuffer;
}

/** The golden attach frame's text with a live session key substituted
 * for the committed `<SESSION-KEY>` template. */
export function goldenAttachText(name: string, key: string): string {
  const text = goldenDocs[name]?.text;
  if (text === undefined) throw new Error(`golden document ${name} has no text`);
  return text.replace('<SESSION-KEY>', key);
}

/** The committed ERROR frame for one of `payloads.error.codes`. */
export function goldenErrorFrame(code: string): GoldenFrameDoc {
  const found = (goldenDocs.api_errors.frames ?? []).find(
    (f) => (f.payload as { code?: string } | null)?.code === code,
  );
  if (found === undefined) throw new Error(`golden api_errors document has no ${code} frame`);
  return found;
}
