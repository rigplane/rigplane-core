/**
 * `DataChannelTransport`: the `MessageTransport` over one injected
 * `RTCDataChannel`-like channel (C5a). The first text frame sent is the
 * attach frame, resolved per label: lossy labels (scope/audio) resend
 * until the ack or error+close after 5 s, control resolves on the
 * handler's `hello`, api opens at once.
 */
import type {
  MessageTransport,
  MessageTransportState,
  TransportAttach,
  TransportBinaryHandler,
  TransportCloseHandler,
  TransportCloseInfo,
  TransportErrorHandler,
  TransportOpenHandler,
  TransportTextHandler,
} from './message-transport';

export const ATTACH_RESEND_MS = 250; // resend until the ack arrives
export const ATTACH_TIMEOUT_MS = 5000; // give-up bound for the attach step

/** The `RTCDataChannel` surface this transport needs (test-injectable). */
export interface DataChannelLike {
  readonly label: string;
  readonly readyState: 'connecting' | 'open' | 'closing' | 'closed';
  readonly bufferedAmount: number;
  binaryType: string;
  onopen: (() => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  onclose: (() => void) | null;
  onerror: (() => void) | null;
  send(data: string | ArrayBuffer): void;
  close(): void;
}

export class DataChannelTransport implements MessageTransport {
  private readonly openHandlers = new Set<TransportOpenHandler>();
  private readonly textHandlers = new Set<TransportTextHandler>();
  private readonly binaryHandlers = new Set<TransportBinaryHandler>();
  private readonly closeHandlers = new Set<TransportCloseHandler>();
  private readonly errorHandlers = new Set<TransportErrorHandler>();
  private attachFrame: string | null = null;
  private awaitingAck = false;
  private acked = false;
  private ackedChannel: string | null = null;
  private resendTimer: ReturnType<typeof setInterval> | null = null;
  private deadlineTimer: ReturnType<typeof setTimeout> | null = null;
  private live = false;
  constructor(private readonly channel: DataChannelLike) {}
  get state(): MessageTransportState {
    if (!this.live) return 'closed';
    const ready = this.channel.readyState;
    if (ready === 'closing' || ready === 'closed') return 'closed';
    if (this.acked) return 'open';
    if (this.awaitingAck) return 'connecting';
    return ready === 'open' ? 'open' : 'connecting';
  }
  get attachedProtocol(): string {
    return this.ackedChannel ?? '';
  }
  get bufferedAmount(): number {
    return this.channel.bufferedAmount;
  }
  open(
    url: string,
    attach?: TransportAttach & { controllerKey?: string | null; stateDelta?: 1 | 2; role?: string }
  ): void {
    void url; // unused: the injected channel already names its peer
    this.attachFrame =
      attach === undefined
        ? null
        : JSON.stringify({
            type: 'attach',
            controller_key: attach.controllerKey ?? null,
            state_delta: attach.stateDelta ?? 2,
            role: attach.role ?? 'primary',
          });
    this.live = true;
    this.channel.binaryType = 'arraybuffer';
    this.channel.onopen = () => this.onChannelOpen();
    this.channel.onmessage = (event) => this.onChannelMessage(event.data);
    this.channel.onclose = () => this.finish({ code: 1006, reason: '', wasClean: false });
    this.channel.onerror = () => {
      for (const handler of [...this.errorHandlers]) handler();
    };
    if (this.channel.readyState === 'open') this.onChannelOpen();
  }
  send(data: string | ArrayBuffer): void {
    if (this.state !== 'open') {
      throw new Error('DataChannelTransport: send on a closed transport');
    }
    this.channel.send(data);
  }
  close(): void {
    this.finish({ code: 1000, reason: '', wasClean: true });
  }
  private subscribe<T>(handlers: Set<T>, handler: T): () => void {
    handlers.add(handler);
    return () => handlers.delete(handler);
  }
  onOpen(handler: TransportOpenHandler): () => void {
    return this.subscribe(this.openHandlers, handler);
  }
  onText(handler: TransportTextHandler): () => void {
    return this.subscribe(this.textHandlers, handler);
  }
  onBinary(handler: TransportBinaryHandler): () => void {
    return this.subscribe(this.binaryHandlers, handler);
  }
  onClose(handler: TransportCloseHandler): () => void {
    return this.subscribe(this.closeHandlers, handler);
  }
  onError(handler: TransportErrorHandler): () => void {
    return this.subscribe(this.errorHandlers, handler);
  }
  private onChannelOpen(): void {
    if (this.attachFrame === null) {
      this.resolveAttach(null);
      return;
    }
    this.channel.send(this.attachFrame);
    if (this.channel.label === 'api') {
      this.resolveAttach(this.channel.label); // reliable: nothing to lose
      return;
    }
    this.awaitingAck = true;
    if (this.channel.label !== 'control') {
      // Lossy labels only: a silent control/api channel is closed by the peer.
      this.resendTimer = setInterval(() => {
        if (this.awaitingAck) this.channel.send(this.attachFrame as string);
      }, ATTACH_RESEND_MS);
      this.deadlineTimer = setTimeout(() => {
        for (const handler of [...this.errorHandlers]) handler();
        this.finish({ code: 1006, reason: 'attach timeout', wasClean: false });
      }, ATTACH_TIMEOUT_MS);
    }
  }
  private resolveAttach(ackChannel: string | null): void {
    this.awaitingAck = false;
    this.clearAttachTimers();
    this.acked = true;
    this.ackedChannel = ackChannel;
    for (const handler of [...this.openHandlers]) handler();
  }
  private onChannelMessage(data: unknown): void {
    if (this.awaitingAck && typeof data === 'string') {
      let frame: { type?: unknown; v?: unknown; channel?: unknown } | null = null;
      try {
        frame = JSON.parse(data);
      } catch {
        frame = null;
      }
      if (frame?.type === 'attached' && frame.v === 1 && this.channel.label !== 'control') {
        this.resolveAttach(typeof frame.channel === 'string' ? frame.channel : '');
        return; // the ack is transport-level; clients never see it
      }
      if (frame?.type === 'hello' && this.channel.label === 'control') {
        this.resolveAttach(this.channel.label); // hello is still delivered below
      }
    }
    if (typeof data === 'string') {
      for (const handler of [...this.textHandlers]) handler(data);
    } else if (data instanceof ArrayBuffer) {
      for (const handler of [...this.binaryHandlers]) handler(data);
    }
  }
  private clearAttachTimers(): void {
    if (this.resendTimer !== null) {
      clearInterval(this.resendTimer);
      this.resendTimer = null;
    }
    if (this.deadlineTimer !== null) {
      clearTimeout(this.deadlineTimer);
      this.deadlineTimer = null;
    }
  }
  private finish(info: TransportCloseInfo): void {
    this.clearAttachTimers();
    if (!this.live) return;
    this.live = false;
    this.awaitingAck = false;
    const { channel } = this;
    channel.onopen = null;
    channel.onmessage = null;
    channel.onclose = null;
    channel.onerror = null;
    try {
      channel.close();
    } catch {
      // Already gone — the close event (if any) carries the outcome.
    }
    for (const handler of [...this.closeHandlers]) handler(info);
  }
}
