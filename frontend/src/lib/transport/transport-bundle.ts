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
 * an injected `RemoteLink`: every `openChannel(label)` asks the link for a
 * FRESH native channel, so a data channel behaves like a WebSocket —
 * `close()` really closes it and the next open is a new channel, never a
 * reopened one. The api channel (created by the shell before the offer) is
 * attached once per link and serves `apiFetch` through
 * `createApiChannelFetch`, each request waiting for its open.
 *
 * Link loss, minimal (PR-A): the control and api channels' own close
 * events — an attach refusal, an attach timeout, or a closed channel —
 * report one loss through `onLinkLost`, close every transport the bundle
 * still tracks (the link is dead for the UI, and the server's cleanup runs
 * on the closed channels), and reject `apiFetch` from then on with a
 * `TypeError`, like a failed `fetch`; `openChannel` then hands out
 * transports that close at once with the reason (never throw).
 * `shutdown()` is the same teardown without the loss report. A closed
 * `RTCDataChannel` cannot be reopened, so recovery is always new channels,
 * never transport-level reconnect. PTT release and the user-facing loss
 * notification above this seam are PR-B; a scope or audio channel dying
 * alone does not lose the link (PR-B owns that story).
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

/** One link-loss report per link. */
export type LinkLostHandler = (reason: LinkLostReason) => void;

/** What the shell injects: a live, already negotiated link. */
export interface RemoteLink {
  /** Created by the shell before the offer (it is what puts the data
   *  section into the SDP). One per link. */
  readonly api: DataChannelLike;
  /** A fresh native channel with this label on the same peer connection,
   *  configured from the `channels` table of api-channel-wire.json
   *  (ordered / maxRetransmits). Called once per `openChannel`. */
  createChannel(label: ChannelLabel): DataChannelLike;
}

export interface TransportBundle {
  readonly kind: TransportKind;
  openChannel(label: ChannelLabel): MessageTransport;
  apiFetch(path: string, init?: RequestInit): Promise<Response>;
}

/** A data-channel bundle's lifecycle surface (consumed by `bootRemote`). */
export interface DataChannelBundle extends TransportBundle {
  /** Drop the link and stop serving the UI; reports no loss. */
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

export function dataChannelBundle(link: RemoteLink, onLinkLost?: LinkLostHandler): DataChannelBundle {
  let lost = false;
  let reason: LinkLostReason = 'channel_closed';
  const gone = (): TypeError => new TypeError('api channel closed');

  // Every transport this bundle has handed out and not yet seen close.
  const tracked = new Set<MessageTransport>();

  // The api channel: attached once per link, before any request can
  // leave — keyless acquire, its role named (the wire contract defaults
  // an absent role to "primary"; the transport never defaults).
  const apiTransport = new DataChannelTransport(link.api);
  const channelFetch = createApiChannelFetch(apiTransport);
  apiTransport.open('rigplane://api', { protocols: [], role: 'primary' });

  // One open-wait per link: resolved by the transport's open, rejected by
  // its close — so a request made while the channel is still connecting
  // waits instead of throwing "send while not open".
  const apiOpened = new Promise<void>((resolve, reject) => {
    apiTransport.onOpen(() => resolve());
    apiTransport.onClose(() => reject(gone()));
  });
  void apiOpened.catch(() => {}); // no awaiter yet: not an unhandled rejection

  const closeEverything = (): void => {
    for (const transport of [...tracked]) transport.close();
    tracked.clear();
    apiTransport.close();
  };

  const reportLoss = (why: LinkLostReason): void => {
    if (lost) return;
    lost = true; // first: the close cascade below must not re-report
    reason = why;
    onLinkLost?.(why);
    closeEverything();
  };
  apiTransport.onClose((info) => reportLoss(linkLostReason(info)));

  /** Wait for the api channel's open, honouring the caller's abort. */
  const waitForOpen = (signal: AbortSignal | undefined): Promise<void> => {
    if (signal === undefined) return apiOpened;
    return new Promise<void>((resolve, reject) => {
      const abort = (): void =>
        reject(Object.assign(new Error('The api channel request was aborted'), { name: 'AbortError' }));
      if (signal.aborted) { abort(); return; }
      signal.addEventListener('abort', abort, { once: true });
      void apiOpened.then(
        () => { signal.removeEventListener('abort', abort); resolve(); },
        (error: unknown) => { signal.removeEventListener('abort', abort); reject(error); },
      );
    });
  };

  return {
    kind: 'datachannel',
    openChannel: (label) => {
      if (lost) return new LostChannelTransport(reason);
      const transport = new DataChannelTransport(link.createChannel(label));
      tracked.add(transport);
      transport.onClose(() => { tracked.delete(transport); });
      if (label === 'control') {
        // Only the control and api channels' death loses the link: a scope
        // or audio channel dying alone must leave control (and PTT)
        // untouched.
        transport.onClose((info) => reportLoss(linkLostReason(info)));
      }
      return transport;
    },
    apiFetch: (path, init) => {
      if (lost) return Promise.reject(gone());
      return waitForOpen(init?.signal).then(() => {
        if (lost) throw gone();
        return channelFetch(path, init);
      });
    },
    shutdown: () => {
      if (lost) return;
      lost = true; // intentional retirement: reports no loss
      closeEverything();
    },
  };
}
