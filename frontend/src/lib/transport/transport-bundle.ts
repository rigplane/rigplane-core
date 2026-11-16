/**
 * Transport bundle (C5b): the one place that owns the transport choice for
 * a running UI — which `MessageTransport` each channel label gets, and how
 * REST travels. The seams above (`transport-factory.ts`, `api-fetch.ts`)
 * read the installed bundle; nothing else decides on the transport, and
 * `kind` is read nowhere outside this module and `boot.ts` (pinned in
 * transport-seam.isolated.test.ts).
 *
 * `webSocketBundle()` is today's behaviour exactly: a `WebSocketTransport`
 * per channel and the global `fetch` for REST. `dataChannelBundle()` rides
 * four injected native channels: control/scope/audio get a
 * `DataChannelTransport` per `openChannel` call over their own channel, and
 * the api channel is attached once per channel generation, its
 * `createApiChannelFetch` serving `apiFetch`.
 *
 * Link loss, minimal (PR-A): the control and api channels' own close
 * events — an attach refusal, an attach timeout, or a closed channel —
 * report one loss per generation through `onLinkLost` and empty the
 * bundle; `openChannel` then hands out transports that close at once with
 * the reason (never throw) until `replaceChannels` installs a fresh
 * channel set. A closed `RTCDataChannel` cannot be reopened, so recovery
 * is always new channels, never transport-level reconnect. PTT release and
 * the user-facing loss notification above this seam are PR-B; a scope or
 * audio channel dying alone does not lose the link (PR-B owns that story).
 */
import type { MessageTransport, MessageTransportState, TransportCloseInfo } from './message-transport';
import { WebSocketTransport } from './websocket-transport';
import { ATTACH_CLOSE_REASON, DataChannelTransport, type DataChannelLike } from './datachannel-transport';
import { createApiChannelFetch } from './api-channel-client';

/** The transport choice. Never "remote": that word already means the host
 * runs controller sessions in remote mode (`ControllerClient.remote`). */
export type TransportKind = 'websocket' | 'datachannel';

/** The channel labels a bundle serves (the api label is bundle-internal). */
export type ChannelLabel = 'control' | 'scope' | 'audio';

export type LinkLostReason =
  | 'channel_closed'
  | 'attach_refused'
  | 'attach_timeout'
  | 'wire_version'
  | 'session_lost';

/** One link-loss report per channel generation. */
export type LinkLostHandler = (reason: LinkLostReason) => void;

/** The four injected native channels a data-channel bundle rides. */
export interface RemoteChannels {
  readonly control: DataChannelLike;
  readonly scope: DataChannelLike;
  readonly audio: DataChannelLike;
  readonly api: DataChannelLike;
}

export interface TransportBundle {
  readonly kind: TransportKind;
  openChannel(label: ChannelLabel): MessageTransport;
  apiFetch(path: string, init?: RequestInit): Promise<Response>;
}

/** A data-channel bundle's recovery surface (consumed by `bootRemote`). */
export interface DataChannelBundle extends TransportBundle {
  /** Install a fresh channel set; a closed `RTCDataChannel` cannot reopen. */
  replaceChannels(channels: RemoteChannels): void;
  /** Drop the channels and stop serving the UI; reports no loss. */
  shutdown(): void;
}

/** Map a transport's own close info onto a link-loss reason: the attach
 * refusals and timeout the transport itself reports, everything else a
 * closed channel. */
function linkLostReason(info: TransportCloseInfo): LinkLostReason {
  if (info.reason === ATTACH_CLOSE_REASON.keyless || info.reason === ATTACH_CLOSE_REASON.roleless) return 'attach_refused';
  if (info.reason === ATTACH_CLOSE_REASON.timeout) return 'attach_timeout';
  return 'channel_closed';
}

/** A channel over a link that is already gone: `open()` closes at once
 * with the loss reason. The owners' reconnect backoff keeps calling
 * `openChannel`; it must see a close, not an exception. */
class LostChannelTransport implements MessageTransport {
  private readonly closeHandlers = new Set<(info: TransportCloseInfo) => void>();
  constructor(private readonly reason: LinkLostReason) {}
  get state(): MessageTransportState { return 'closed'; }
  get attachedProtocol(): string { return ''; }
  open(_url: string): void {
    queueMicrotask(() => {
      for (const handler of [...this.closeHandlers]) handler({ code: 1006, reason: this.reason, wasClean: false });
    });
  }
  send(_data: string | ArrayBuffer): void {
    throw new Error('LostChannelTransport: send while not open');
  }
  close(): void {
    // Nothing was ever open.
  }
  onOpen(): () => void { return () => undefined; }
  onText(): () => void { return () => undefined; }
  onBinary(): () => void { return () => undefined; }
  onError(): () => void { return () => undefined; }
  onClose(handler: (info: TransportCloseInfo) => void): () => void {
    this.closeHandlers.add(handler);
    return () => this.closeHandlers.delete(handler);
  }
}

let installed: TransportBundle = webSocketBundle();

/** Install the bundle the UI runs on. `startApp` (boot.ts) is the
 * production installer; the seam tests install through the same function. */
export function installBundle(bundle: TransportBundle): void {
  installed = bundle;
}

/** The installed bundle, read by `transport-factory.ts` and `api-fetch.ts`. */
export function currentBundle(): TransportBundle {
  return installed;
}

export function webSocketBundle(): TransportBundle {
  return {
    kind: 'websocket',
    openChannel: () => new WebSocketTransport(),
    apiFetch: (path, init) => fetch(path, init),
  };
}

export function dataChannelBundle(channels: RemoteChannels, onLinkLost?: LinkLostHandler): DataChannelBundle {
  let current: RemoteChannels | null = channels;
  let lost = false;
  let reason: LinkLostReason = 'channel_closed';
  let generation = 0;

  const reportLoss = (why: LinkLostReason): void => {
    if (lost || current === null) return;
    lost = true;
    reason = why;
    current = null;
    onLinkLost?.(why);
  };

  // The api channel: attached once per channel generation, before any
  // request can leave — keyless acquire, its role named (the wire contract
  // defaults an absent role to "primary"; the transport never defaults).
  const attachApiChannel = (
    live: RemoteChannels,
  ): ((path: string, init?: RequestInit) => Promise<Response>) => {
    const mine = generation;
    const transport = new DataChannelTransport(live.api);
    const channelFetch = createApiChannelFetch(transport);
    transport.onClose((info) => {
      if (mine === generation) reportLoss(linkLostReason(info));
    });
    transport.open('rigplane://api', { protocols: [], role: 'primary' });
    return channelFetch;
  };

  let rest = attachApiChannel(channels);

  return {
    kind: 'datachannel',
    openChannel: (label) => {
      const live = current;
      if (live === null) return new LostChannelTransport(reason);
      const transport = new DataChannelTransport(live[label]);
      if (label === 'control') {
        // Only the control channel's death loses the link: a scope or audio
        // channel dying alone must leave control (and PTT) untouched.
        const mine = generation;
        transport.onClose((info) => {
          if (mine === generation) reportLoss(linkLostReason(info));
        });
      }
      return transport;
    },
    apiFetch: (path, init) => rest(path, init),
    replaceChannels: (channels) => {
      generation += 1;
      current = channels;
      lost = false;
      reason = 'channel_closed';
      rest = attachApiChannel(channels);
    },
    shutdown: () => {
      generation += 1;
      current = null;
      lost = true;
    },
  };
}
