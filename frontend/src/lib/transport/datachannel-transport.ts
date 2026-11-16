/**
 * `DataChannelTransport`: the `MessageTransport` over one injected native
 * `RTCDataChannel` (C5a). The first text frame sent is the attach frame,
 * built from the committed wire contract: keyed labels take their key from
 * the offered `rigplane-controller-v1.<key>` token; every label, the
 * keyless api acquire included, takes its role from the caller's attach
 * (no attach or no role is refused, never defaulted). The transport
 * opens only once the attach is acknowledged (scope/audio: the
 * `attach_ack`; control: the contract-validated `control_hello`; api: on
 * send — reliable), so no client
 * frame can precede the attach on the wire; lossy labels resend until the
 * ack or error+close after 5 s; `attachedProtocol` is the token.
 */
import wire from './api-channel-wire.json';
import type {
  MessageTransport, MessageTransportState, TransportAttach, TransportBinaryHandler,
  TransportCloseHandler, TransportCloseInfo, TransportErrorHandler, TransportOpenHandler, TransportTextHandler,
} from './message-transport';

export const ATTACH_RESEND_MS = 250; // resend until the ack arrives
export const ATTACH_TIMEOUT_MS = 5000; // give-up bound for the attach step

/** The `RTCDataChannel` surface this transport consumes. */
export type DataChannelLike = Pick<RTCDataChannel, 'label' | 'readyState' | 'bufferedAmount' | 'binaryType' | 'onopen' | 'onmessage' | 'onclose' | 'onerror' | 'send' | 'close'>;

/** A wire payload contract: its `fields` rows (name, kind, required, note). */
interface PayloadContract { readonly fields: readonly (string | boolean)[][] }
/** The typed, contract-validated `attach_ack` payload. */
interface AttachAck { readonly type: 'attached'; readonly v: number; readonly channel: string }
const CONTROLLER_PROTOCOL_PREFIX = 'rigplane-controller-v1.';

/** The session key offered as a `rigplane-controller-v1.<key>` token — the
 * shape `ControllerClient.protocols()` offers; null when no token is offered. */
function controllerKeyFromProtocols(protocols: readonly string[] | undefined): string | null {
  const token = (protocols ?? []).find(
    (p) => p.startsWith(CONTROLLER_PROTOCOL_PREFIX) && p.length > CONTROLLER_PROTOCOL_PREFIX.length,
  );
  return token === undefined ? null : token.slice(CONTROLLER_PROTOCOL_PREFIX.length);
}
function fieldOk(kind: string, value: unknown): boolean {
  const map = (check: (entry: unknown) => boolean): boolean =>
    typeof value === 'object' && value !== null && !Array.isArray(value) && Object.values(value).every(check);
  if (kind === 'string') return typeof value === 'string';
  if (kind === 'integer') return typeof value === 'number' && Number.isInteger(value);
  if (kind === 'boolean') return typeof value === 'boolean';
  if (kind === 'array of strings') return Array.isArray(value) && value.every((x) => typeof x === 'string');
  if (kind === 'string | null') return value === null || typeof value === 'string';
  if (kind === 'object: name -> [values]') return map((v) => Array.isArray(v) && v.every((x) => typeof x === 'string'));
  if (kind === 'object: name -> value') return map((v) => typeof v === 'string');
  return kind.length > 2 && kind.startsWith('"') && kind.endsWith('"') && value === kind.slice(1, -1);
}
/** Validate a parsed payload: every required field present, every present
 * field of its contract kind. */
export function payloadMatchesContract(contract: PayloadContract, value: unknown): boolean {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  return contract.fields.every((row) => {
    const [name, kind] = row;
    if (typeof name !== 'string' || typeof kind !== 'string') return false;
    return Object.hasOwn(record, name) ? fieldOk(kind, record[name]) : row[2] !== true;
  });
}

/** Build a payload carrying exactly the contract's fields. */
function buildContractPayload(contract: PayloadContract, parts: Record<string, unknown>): string {
  const payload: Record<string, unknown> = {};
  for (const row of contract.fields) {
    const [name, kind] = row;
    if (typeof name !== 'string' || typeof kind !== 'string') throw new Error('payload contract: bad fields row');
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
/** Decode a payload text against a contract; null when it violates it. */
function decodeContractPayload<T>(contract: PayloadContract, text: string): T | null {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    return null;
  }
  return payloadMatchesContract(contract, value) ? (value as T) : null;
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
    const ready = this.channel.readyState;
    if (!this.live || ready === 'closing' || ready === 'closed') return 'closed';
    // Open only after the attach step is acknowledged, never on the native
    // channel's readyState: a client frame sent earlier would precede the
    // attach on the wire.
    return this.acked ? 'open' : 'connecting';
  }
  get attachedProtocol(): string { return this.acked ? this.acceptedToken : ''; }
  get bufferedAmount(): number { return this.channel.bufferedAmount; }
  open(url: string, attach?: TransportAttach): void {
    void url; // unused: the injected channel's label names the connection
    this.live = true;
    this.acked = false;
    const key = controllerKeyFromProtocols(attach?.protocols);
    this.offeredToken = key === null ? null : CONTROLLER_PROTOCOL_PREFIX + key;
    this.acceptedToken = '';
    if (key === null && this.channel.label !== 'api') {
      // Keyless is only the api label's acquire path; the server refuses a
      // null key on a keyed label, and an attach-less open offers none.
      this.refuseAttach('keyless attach refused');
      return;
    }
    // Every label — the keyless api acquire included — carries the caller's
    // controller role; the transport never defaults one (a defaulted primary
    // would steal the session), so a missing attach or role is refused.
    const role = attach?.role;
    if (role !== 'primary' && role !== 'auxiliary') {
      this.refuseAttach('roleless attach refused');
      return;
    }
    this.attachFrame = buildContractPayload(wire.payloads.attach_frame, { type: 'attach', controller_key: key, role, state_delta: 2 });
    this.channel.binaryType = 'arraybuffer';
    this.channel.onopen = () => this.onChannelOpen();
    this.channel.onmessage = (event) => this.onChannelMessage(event.data);
    this.channel.onclose = () => this.finish({ code: 1006, reason: '', wasClean: false });
    this.channel.onerror = () => {
      for (const handler of [...this.errorHandlers]) handler();
    };
    if (this.channel.readyState === 'open') {
      // Already open: still notify listeners registered right after open().
      queueMicrotask(() => {
        if (this.live && this.channel.readyState === 'open') this.onChannelOpen();
      });
    }
  }
  send(data: string | ArrayBuffer): void {
    if (this.state !== 'open') throw new Error('DataChannelTransport: send while not open');
    // Narrow per call: the native send overloads take one payload kind each.
    if (typeof data === 'string') this.channel.send(data);
    else this.channel.send(data);
  }
  close(): void { this.finish({ code: 1000, reason: '', wasClean: true }); }
  private subscribe<T>(handlers: Set<T>, handler: T): () => void {
    handlers.add(handler);
    return () => handlers.delete(handler);
  }
  onOpen(handler: TransportOpenHandler): () => void { return this.subscribe(this.openHandlers, handler); }
  onText(handler: TransportTextHandler): () => void { return this.subscribe(this.textHandlers, handler); }
  onBinary(handler: TransportBinaryHandler): () => void { return this.subscribe(this.binaryHandlers, handler); }
  onClose(handler: TransportCloseHandler): () => void { return this.subscribe(this.closeHandlers, handler); }
  onError(handler: TransportErrorHandler): () => void { return this.subscribe(this.errorHandlers, handler); }
  /** Fail the attach before anything reaches the wire: error, then close. */
  private refuseAttach(reason: string): void {
    queueMicrotask(() => {
      for (const handler of [...this.errorHandlers]) handler();
      this.finish({ code: 1006, reason, wasClean: false });
    });
  }
  private onChannelOpen(): void {
    // open() builds the frame before wiring this handler, so it is set here.
    this.channel.send(this.attachFrame as string);
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
        // Only a text frame that passes the committed control_hello contract
        // proves the attach; one that says hello but violates the contract is
        // delivered below like any other frame and the attach times out.
        if (decodeContractPayload(wire.payloads.control_hello, data) !== null) this.resolveAttach();
      } else {
        const ack = decodeContractPayload<AttachAck>(wire.payloads.attach_ack, data);
        // Only the committed attach_ack version completes the attach; any
        // other frame stays an ordinary text frame.
        if (ack !== null && ack.v === wire.payloads.attach_ack.version) {
          this.resolveAttach();
          return; // the ack is transport-level; clients never see it
        }
      }
    }
    if (typeof data === 'string') {
      for (const handler of [...this.textHandlers]) handler(data);
    } else if (data instanceof ArrayBuffer) {
      for (const handler of [...this.binaryHandlers]) handler(data);
    }
  }
  private clearAttachTimers(): void {
    if (this.resendTimer !== null) { clearInterval(this.resendTimer); this.resendTimer = null; }
    if (this.deadlineTimer !== null) { clearTimeout(this.deadlineTimer); this.deadlineTimer = null; }
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
