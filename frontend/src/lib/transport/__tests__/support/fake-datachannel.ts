// Shared fake `RTCDataChannel` for the C5a tests: records sends, lets the
// test act as the peer, and exposes the committed golden documents' texts.
import golden from '../../api-channel-golden.json';
import type { DataChannelLike } from '../../datachannel-transport';

/** One committed golden document, as far as these tests read it. */
interface GoldenDoc {
  readonly text?: string;
  readonly decoded?: Record<string, unknown> | null;
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

/** The golden attach frame's text, `<SESSION-KEY>` substituted live. */
export function goldenAttachText(name: string, key: string): string {
  const text = goldenDocs[name]?.text;
  if (text === undefined) throw new Error(`golden document ${name} has no text`);
  return text.replace('<SESSION-KEY>', key);
}

