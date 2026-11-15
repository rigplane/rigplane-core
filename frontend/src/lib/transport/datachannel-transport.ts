/**
 * `DataChannelTransport`: the `MessageTransport` over one injected
 * native `RTCDataChannel` (C5a). The first text frame sent is the attach
 * frame, built from the committed wire contract: keyed labels
 * (control/scope/audio) take their key from the offered
 * `rigplane-controller-v1.<key>` subprotocol token, the api label is the
 * keyless acquire path. Lossy labels (scope/audio) resend until the ack
 * or error+close after 5 s, control resolves on the handler's hello, api
 * opens at once, and `attachedProtocol` exposes the accepted token.
 */
import wire from './api-channel-wire.json';
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

/** The `RTCDataChannel` surface this transport consumes. */
export type DataChannelLike = Pick<
  RTCDataChannel,
  | 'label'
  | 'readyState'
  | 'bufferedAmount'
  | 'binaryType'
  | 'onopen'
  | 'onmessage'
  | 'onclose'
  | 'onerror'
  | 'send'
  | 'close'
>;

/** One `fields` row of a wire payload contract: name, kind, required, note. */
type PayloadFieldRow = readonly (string | boolean)[];
export interface PayloadContract {
  readonly fields: readonly PayloadFieldRow[];
  readonly codes: readonly string[];
}

/** The typed, contract-validated `attach_ack` payload. */
export interface AttachAck {
  readonly type: 'attached';
  readonly v: number;
  readonly channel: string;
}

export const CONTROLLER_PROTOCOL_PREFIX = 'rigplane-controller-v1.';

/** The session key offered as a `rigplane-controller-v1.<key>` token
 * (the shape `ControllerClient.protocols()` offers); null when no token
 * is offered. */
export function controllerKeyFromProtocols(protocols: readonly string[] | undefined): string | null {
  for (const token of protocols ?? []) {
    if (typeof token === 'string' && token.startsWith(CONTROLLER_PROTOCOL_PREFIX)) {
      const key = token.slice(CONTROLLER_PROTOCOL_PREFIX.length);
      if (key !== '') return key;
    }
  }
  return null;
}

function stringMap(value: unknown, values: (entry: unknown) => boolean): boolean {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
  return Object.values(value).every(values);
}

function fieldOk(kind: string, value: unknown): boolean {
  if (kind === 'string') return typeof value === 'string';
  if (kind === 'integer') return typeof value === 'number' && Number.isInteger(value);
  if (kind === 'string | null') return value === null || typeof value === 'string';
  if (kind === 'object: name -> [values]') {
    return stringMap(value, (v) => Array.isArray(v) && v.every((x) => typeof x === 'string'));
  }
  if (kind === 'object: name -> value') return stringMap(value, (v) => typeof v === 'string');
  return kind.length > 2 && kind.startsWith('"') && kind.endsWith('"') && value === kind.slice(1, -1);
}

/** Validate a parsed payload against the contract's `fields` rows: every
 * required field present, every present field of its contract kind. */
export function payloadMatchesContract(contract: PayloadContract, value: unknown): boolean {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  return contract.fields.every((row) => {
    const [name, kind] = row;
    if (typeof name !== 'string' || typeof kind !== 'string') return false;
    if (!Object.hasOwn(record, name)) return row[2] !== true;
    return fieldOk(kind, record[name]);
  });
}

/** Build a payload carrying exactly the contract's fields. */
export function buildContractPayload(contract: PayloadContract, parts: Record<string, unknown>): string {
  const payload: Record<string, unknown> = {};
  for (const row of contract.fields) {
    const [name, kind] = row;
    if (typeof name !== 'string' || typeof kind !== 'string') {
      throw new Error('payload contract: bad fields row');
    }
    const value = parts[name];
    if (value === undefined) {
      if (row[2] === true) throw new Error(`payload contract: ${name} is required`);
      continue; // the producer omits an absent optional field
    }
    if (!fieldOk(kind, value)) throw new Error(`payload contract: bad ${name}`);
    payload[name] = value;
  }
  return JSON.stringify(payload);
}

function decodeAttachAck(text: string): AttachAck | null {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    return null;
  }
  return payloadMatchesContract(wire.payloads.attach_ack, value) ? (value as AttachAck) : null;
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
  private offeredToken: string | null = null;
  private acceptedToken = '';
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
    return this.acked ? this.acceptedToken : '';
  }
  get bufferedAmount(): number {
    return this.channel.bufferedAmount;
  }
  open(url: string, attach?: TransportAttach): void {
    void url; // unused: the injected channel's label names the connection
    this.live = true;
    this.acked = false;
    const key = controllerKeyFromProtocols(attach?.protocols);
    this.offeredToken = key === null ? null : CONTROLLER_PROTOCOL_PREFIX + key;
    this.acceptedToken = '';
    if (attach !== undefined && key === null && this.channel.label !== 'api') {
      // Keyless attach is only the api label's acquire path; every keyed
      // label needs a session key (the server refuses a null key there).
      queueMicrotask(() => {
        for (const handler of [...this.errorHandlers]) handler();
        this.finish({ code: 1006, reason: 'keyless attach refused', wasClean: false });
      });
      return;
    }
    this.attachFrame =
      attach === undefined && this.channel.label !== 'api'
        ? null // attach-less channel: no attach step
        : buildContractPayload(wire.payloads.attach_frame, {
            type: 'attach',
            controller_key: key,
            role: 'primary',
            state_delta: 2,
          });
    this.channel.binaryType = 'arraybuffer';
    this.channel.onopen = () => this.onChannelOpen();
    this.channel.onmessage = (event) => this.onChannelMessage(event.data);
    this.channel.onclose = () => this.finish({ code: 1006, reason: '', wasClean: false });
    this.channel.onerror = () => {
      for (const handler of [...this.errorHandlers]) handler();
    };
    if (this.channel.readyState === 'open') {
      // An already-open channel still notifies listeners that register
      // right after open() returns.
      queueMicrotask(() => {
        if (this.live && this.channel.readyState === 'open') this.onChannelOpen();
      });
    }
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
      this.resolveAttach();
      return;
    }
    this.channel.send(this.attachFrame);
    if (this.channel.label === 'api') {
      this.resolveAttach(); // reliable: nothing to lose
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
  private resolveAttach(): void {
    this.awaitingAck = false;
    this.clearAttachTimers();
    this.acked = true;
    this.acceptedToken = this.offeredToken ?? '';
    for (const handler of [...this.openHandlers]) handler();
  }
  private onChannelMessage(data: unknown): void {
    if (this.awaitingAck && typeof data === 'string') {
      if (this.channel.label === 'control') {
        let hello: unknown;
        try {
          hello = JSON.parse(data);
        } catch {
          hello = null;
        }
        if (hello instanceof Object && (hello as Record<string, unknown>).type === 'hello') {
          this.resolveAttach(); // hello is still delivered below
        }
      } else if (decodeAttachAck(data) !== null) {
        this.resolveAttach();
        return; // the ack is transport-level; clients never see it
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
    this.acked = false;
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
