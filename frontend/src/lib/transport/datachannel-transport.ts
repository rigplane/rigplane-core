/**
 * `DataChannelTransport`: the `MessageTransport` over injected native
 * `RTCDataChannel`s (C5a). The channel set is the committed wire contract's
 * `data_channels` (K5): `control` and `api` are one reliable channel each;
 * `scope` and `audio` are a split pair — the lossy binary channel plus its
 * reliable ordered `*-text` companion, passed as `textChannel` — merged into
 * this one transport: text goes out on the companion, binary on the lossy
 * channel, and frames from both arrive here. The first text frame sent is
 * the attach frame (on the text-carrying channel), built from the committed
 * wire contract: keyed labels take their key from the offered
 * `rigplane-controller-v1.<key>` token; every label, the keyless api acquire
 * included, takes its role from the caller's attach (no attach or no role is
 * refused, never defaulted). The attach rides a reliable channel and is
 * never resent. The transport opens only once the attach is acknowledged
 * (scope/audio: the `attach_ack`, with both channels open; control: the
 * contract-validated `control_hello`; api: on send — reliable), so no client
 * frame can precede the attach on the wire; `attachedProtocol` is the token.
 */
import wire from './api-channel-wire.json';
import type {
  MessageTransport, MessageTransportState, TransportAttach, TransportBinaryHandler,
  TransportCloseHandler, TransportCloseInfo, TransportErrorHandler, TransportOpenHandler, TransportTextHandler,
} from './message-transport';

/** The `RTCDataChannel` surface this transport consumes. */
export type DataChannelLike = Pick<RTCDataChannel, 'label' | 'readyState' | 'bufferedAmount' | 'binaryType' | 'onopen' | 'onmessage' | 'onclose' | 'onerror' | 'send' | 'close'>;

/** Realm-safe `ArrayBuffer` check: a channel created in a parent window hands a
 * UI running in an iframe an `ArrayBuffer` of the other realm, for which
 * `instanceof ArrayBuffer` is false. */
export function isArrayBuffer(value: unknown): value is ArrayBuffer {
  return Object.prototype.toString.call(value) === '[object ArrayBuffer]';
}

/** A wire payload contract: its `fields` rows (name, kind, required, note). */
export interface PayloadContract { readonly fields: readonly (string | boolean)[][] }
/** The typed, contract-validated `attach_ack` payload. */
interface AttachAck { readonly type: 'attached'; readonly v: number; readonly channel: string }
const CONTROLLER_PROTOCOL_PREFIX = 'rigplane-controller-v1.';

/** One row of the committed K5 channel set (`data_channels`). */
export interface DataChannelSpec {
  readonly label: string;
  readonly kind: string;
  readonly ordered: boolean;
  readonly max_retransmits: number | null;
  readonly carries: string;
}
/** The committed K5 channel set, never re-typed here. */
export const DATA_CHANNELS: readonly DataChannelSpec[] = wire.data_channels;
/** A split pair's two labels share one pair id after this separator
 * (`scope/7f3a` + `scope-text/7f3a`); the answerer pairs them by it. */
export const PAIR_ID_SEPARATOR: string = wire.data_channel_pair_separator;

/** The `*-text` companion label a lossy binary label needs (same pair id);
 * null for a label that carries every frame itself (control, api). */
export function textCompanionLabel(label: string): string | null {
  const cut = label.indexOf(PAIR_ID_SEPARATOR);
  const base = cut < 0 ? label : label.slice(0, cut);
  const pairSuffix = cut < 0 ? '' : label.slice(cut);
  const spec = DATA_CHANNELS.find((row) => row.label === base);
  if (spec?.carries !== 'binary') return null;
  const companion = DATA_CHANNELS.find((row) => row.kind === spec.kind && row.carries === 'text');
  return companion === undefined ? null : companion.label + pairSuffix;
}

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
export function buildContractPayload(contract: PayloadContract, parts: Record<string, unknown>): string {
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
export function decodeContractPayload<T>(contract: PayloadContract, text: string): T | null {
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
  private openNotified = false;
  private live = false;
  private readonly text: DataChannelLike;
  /**
   * @param channel the label's channel; for scope/audio the lossy binary one
   * @param textChannel the reliable `*-text` companion of a scope/audio
   *   channel (required there, refused elsewhere)
   */
  constructor(private readonly channel: DataChannelLike, private readonly textChannel: DataChannelLike | null = null) {
    this.text = textChannel ?? channel;
  }
  private channels(): DataChannelLike[] {
    return this.text === this.channel ? [this.channel] : [this.channel, this.text];
  }
  private allOpen(): boolean {
    return this.channels().every((c) => c.readyState === 'open');
  }
  get state(): MessageTransportState {
    if (!this.live || this.channels().some((c) => c.readyState === 'closing' || c.readyState === 'closed')) return 'closed';
    // Open only after the attach step is acknowledged (and both halves of a
    // pair are open), never on a native channel's readyState: a client frame
    // sent earlier would precede the attach on the wire.
    return this.acked && this.allOpen() ? 'open' : 'connecting';
  }
  get attachedProtocol(): string { return this.acked ? this.acceptedToken : ''; }
  get bufferedAmount(): number { return this.channel.bufferedAmount; }
  open(url: string, attach?: TransportAttach): void {
    void url; // unused: the injected channel's label names the connection
    this.live = true;
    this.acked = false;
    this.openNotified = false;
    const companion = textCompanionLabel(this.channel.label);
    if ((this.textChannel?.label ?? null) !== companion) {
      // A split label needs exactly its committed text companion, and a
      // label that carries every frame takes none.
      this.refuseAttach('text channel does not match the channel set');
      return;
    }
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
    for (const channel of this.channels()) {
      channel.binaryType = 'arraybuffer';
      channel.onmessage = (event) => this.onChannelMessage(event.data);
      channel.onclose = () => this.finish({ code: 1006, reason: '', wasClean: false });
      channel.onerror = () => {
        for (const handler of [...this.errorHandlers]) handler();
      };
    }
    // The attach goes out when the text-carrying channel opens; a lossy
    // binary half opening later may complete an already-acked attach.
    this.text.onopen = () => this.onChannelOpen();
    if (this.text !== this.channel) this.channel.onopen = () => this.notifyOpenWhenReady();
    if (this.text.readyState === 'open') {
      // Already open: still notify listeners registered right after open().
      queueMicrotask(() => {
        if (this.live && this.text.readyState === 'open') this.onChannelOpen();
      });
    }
  }
  send(data: string | ArrayBuffer): void {
    if (this.state !== 'open') throw new Error('DataChannelTransport: send while not open');
    // Text on the reliable (companion) channel, binary on the label's own.
    // Narrow per call: the native send overloads take one payload kind each.
    if (typeof data === 'string') this.text.send(data);
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
    // Sent once on a reliable channel: never resent. A peer that never
    // answers closes the channel itself (the server's attach bound).
    this.text.send(this.attachFrame as string);
    if (this.channel.label === 'api') {
      this.resolveAttach(); // reliable: nothing to wait for
      return;
    }
    this.awaitingAck = true;
  }
  private resolveAttach(): void {
    this.awaitingAck = false;
    this.acked = true;
    this.acceptedToken = this.offeredToken ?? '';
    this.notifyOpenWhenReady();
  }
  private notifyOpenWhenReady(): void {
    if (!this.live || !this.acked || this.openNotified || !this.allOpen()) return;
    this.openNotified = true;
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
    } else if (isArrayBuffer(data)) {
      for (const handler of [...this.binaryHandlers]) handler(data);
    }
  }
  private finish(info: TransportCloseInfo): void {
    if (!this.live) return;
    this.live = false;
    this.awaitingAck = false;
    this.acked = false;
    for (const channel of this.channels()) {
      channel.onopen = null;
      channel.onmessage = null;
      channel.onclose = null;
      channel.onerror = null;
      try {
        channel.close();
      } catch {
        // Already gone — the close event (if any) carries the outcome.
      }
    }
    for (const handler of [...this.closeHandlers]) handler(info);
  }
}
