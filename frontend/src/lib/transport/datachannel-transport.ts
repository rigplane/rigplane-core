/**
 * `DataChannelTransport`: the `MessageTransport` over one
 * `RTCDataChannel`-like channel (C5a).
 *
 * The channel is injected already tied to its peer — no
 * `RTCPeerConnection` is created here. The channel's attach step is the
 * data-channel one: the first text frame sent is the attach frame
 * (`{"type":"attach","controller_key":...,"state_delta":...,"role":...}`),
 * RESENT every 250 ms until the server's
 * `{"type":"attached","v":1,"channel":...}` acknowledgement arrives,
 * because the scope/audio channels are lossy and may drop it; with no
 * ack within 5 s the transport errors and closes. Text/binary frames
 * that arrive before the ack and are not the ack are delivered
 * normally (an unordered channel can overtake the ack with data).
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

/** Attach-frame resend interval (the ack may be lost on a lossy channel). */
export const ATTACH_RESEND_MS = 250;
/** Give-up bound for the attach step, counted from the first send. */
export const ATTACH_TIMEOUT_MS = 5000;

/** The `RTCDataChannel` surface this transport needs (test-injectable). */
export interface DataChannelLike {
  readonly readyState: 'connecting' | 'open' | 'closing' | 'closed';
  readonly label: string;
  readonly bufferedAmount: number;
  binaryType: string;
  onopen: (() => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  onclose: (() => void) | null;
  onerror: (() => void) | null;
  send(data: string | ArrayBuffer): void;
  close(): void;
}

interface AckFrame {
  readonly type: 'attached';
  readonly v?: number;
  readonly channel?: unknown;
}

export class DataChannelTransport implements MessageTransport {
  private readonly channel: DataChannelLike;
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

  constructor(channel: DataChannelLike) {
    this.channel = channel;
  }

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

  /** Unsent bytes buffered by the channel (send backpressure source). */
  get bufferedAmount(): number {
    return this.channel.bufferedAmount;
  }

  open(url: string, attach?: TransportAttach): void {
    // The URL is ignored: the injected channel already names its peer.
    // A previous wiring, if any, is abandoned untouched, like
    // WebSocketTransport.open.
    void url;
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
    this.channel.onclose = () =>
      this.finish({ code: 1006, reason: '', wasClean: false });
    this.channel.onerror = () => {
      // The close event that follows a channel error carries the outcome.
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

  onOpen(handler: TransportOpenHandler): () => void {
    this.openHandlers.add(handler);
    return () => this.openHandlers.delete(handler);
  }

  onText(handler: TransportTextHandler): () => void {
    this.textHandlers.add(handler);
    return () => this.textHandlers.delete(handler);
  }

  onBinary(handler: TransportBinaryHandler): () => void {
    this.binaryHandlers.add(handler);
    return () => this.binaryHandlers.delete(handler);
  }

  onClose(handler: TransportCloseHandler): () => void {
    this.closeHandlers.add(handler);
    return () => this.closeHandlers.delete(handler);
  }

  onError(handler: TransportErrorHandler): () => void {
    this.errorHandlers.add(handler);
    return () => this.errorHandlers.delete(handler);
  }

  private onChannelOpen(): void {
    if (this.attachFrame === null) {
      this.acked = true;
      for (const handler of [...this.openHandlers]) handler();
      return;
    }
    this.awaitingAck = true;
    this.channel.send(this.attachFrame);
    this.resendTimer = setInterval(() => {
      if (this.awaitingAck) this.channel.send(this.attachFrame as string);
    }, ATTACH_RESEND_MS);
    this.deadlineTimer = setTimeout(() => {
      for (const handler of [...this.errorHandlers]) handler();
      this.finish({ code: 1006, reason: 'attach timeout', wasClean: false });
    }, ATTACH_TIMEOUT_MS);
  }

  private onChannelMessage(data: unknown): void {
    if (this.awaitingAck && typeof data === 'string') {
      let frame: AckFrame | null = null;
      try {
        frame = JSON.parse(data) as AckFrame;
      } catch {
        frame = null;
      }
      if (frame !== null && frame.type === 'attached' && frame.v === 1) {
        this.awaitingAck = false;
        this.clearAttachTimers();
        this.acked = true;
        this.ackedChannel = typeof frame.channel === 'string' ? frame.channel : '';
        for (const handler of [...this.openHandlers]) handler();
        return;
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

  /** Tear everything down exactly once and report one close event. */
  private finish(info: TransportCloseInfo): void {
    this.clearAttachTimers();
    const wasLive = this.live;
    const channel = this.channel;
    this.live = false;
    this.awaitingAck = false;
    if (!wasLive) return;
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
