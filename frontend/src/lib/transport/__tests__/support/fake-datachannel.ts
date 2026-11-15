// Shared fake `RTCDataChannel` for the C5a tests: records sends, lets
// the test act as the peer, counts bufferedAmount.
export class FakeDataChannel {
  readyState: 'connecting' | 'open' | 'closing' | 'closed' = 'connecting';
  binaryType = 'blob';
  readonly label: string;
  bufferedAmount = 0;
  sent: Array<string | ArrayBuffer> = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(label = 'control') {
    this.label = label;
  }
  send(data: string | ArrayBuffer): void {
    this.sent.push(data);
  }
  close(): void {
    if (this.readyState === 'closed') return;
    this.readyState = 'closed';
    this.onclose?.();
  }
  simulateOpen(): void {
    this.readyState = 'open';
    this.onopen?.();
  }
  simulateMessage(data: unknown): void {
    this.onmessage?.({ data });
  }
  simulateError(): void {
    this.onerror?.();
  }
  textFrames(): string[] {
    return this.sent.filter((f): f is string => typeof f === 'string');
  }
}
