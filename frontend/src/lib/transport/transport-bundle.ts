import type { MessageTransport, TransportCloseHandler } from './message-transport';
import { createMessageTransport } from './transport-factory';
import { DataChannelTransport, textCompanionLabel, type DataChannelLike } from './datachannel-transport';
import { createApiChannelFetch } from './api-channel-client';

export type ChannelLabel = 'control' | 'scope' | 'audio';
export type LinkLostReason = 'channel_closed' | 'attach_refused' | 'session_lost';

export interface RemoteLink {
  /** The native channel used to negotiate the data section of the offer. */
  readonly api: DataChannelLike;
  /** Supply an unused native channel per call, using Core's DATA_CHANNELS table. */
  createChannel(label: string): DataChannelLike;
}

export type TransportBundle = {
  openChannel(label: ChannelLabel): MessageTransport;
} & (
  | { readonly kind: 'websocket' }
  | { readonly kind: 'datachannel'; apiFetch(path: string, init?: RequestInit): Promise<Response> }
);

// A terminal channel still obeys the MessageTransport close-event contract.
class ClosedTransport implements MessageTransport {
  readonly state = 'closed';
  readonly attachedProtocol = '';
  private readonly handlers = new Set<TransportCloseHandler>();

  open(): void {
    queueMicrotask(() => {
      for (const handler of this.handlers) handler({ code: 1006, reason: '', wasClean: false });
      this.close();
    });
  }
  send(): never { throw new TypeError('data channel closed'); }
  close(): void { this.handlers.clear(); }
  onOpen(): () => void { return () => {}; }
  onText(): () => void { return () => {}; }
  onBinary(): () => void { return () => {}; }
  onError(): () => void { return () => {}; }
  onClose(handler: TransportCloseHandler): () => void {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }
}

export function webSocketBundle(): TransportBundle {
  return {
    kind: 'websocket',
    openChannel: () => createMessageTransport(),
  };
}

let installed = webSocketBundle();

export function currentBundle(): TransportBundle { return installed; }
export function installBundle(bundle: TransportBundle): void { installed = bundle; }

/** Negotiated links are replaced by their host. */
export function canReconnectChannel(): boolean { return installed.kind === 'websocket'; }

function isClosing(channel: DataChannelLike): boolean {
  return channel.readyState === 'closed' || channel.readyState === 'closing';
}

export function dataChannelBundle(link: RemoteLink, onLinkLost: (reason: LinkLostReason) => void) {
  let stopped = false;
  const channels = new Set<MessageTransport>();
  const api = new DataChannelTransport(link.api);
  const fetchOverApi = createApiChannelFetch(api);
  const apiClosed = () => new TypeError('api channel closed');

  function shutdown(): void {
    if (stopped) return;
    stopped = true;
    for (const channel of channels) channel.close();
    channels.clear();
    api.close();
  }

  function lose(reason: LinkLostReason): void {
    if (stopped) return;
    shutdown();
    onLinkLost(reason);
  }

  const ready = new Promise<void>((resolve, reject) => {
    api.onOpen(resolve);
    api.onClose((info) => {
      reject(apiClosed());
      lose(info.reason ? 'attach_refused' : 'channel_closed');
    });
    api.open('rigplane://api', { protocols: [], role: 'primary' });
    // RTC will not deliver an event to listeners installed after closure.
    if (isClosing(link.api)) queueMicrotask(() => lose('channel_closed'));
  });
  void ready.catch(() => {}); // The link can close before its first request.

  async function waitForApi(signal?: AbortSignal | null): Promise<void> {
    if (signal == null) return ready;
    signal.throwIfAborted();
    let abort!: () => void;
    const cancelled = new Promise<never>((_, reject) => {
      abort = () => reject(signal.reason);
      signal.addEventListener('abort', abort, { once: true });
    });
    try {
      await Promise.race([ready, cancelled]);
    } finally {
      signal.removeEventListener('abort', abort);
    }
  }

  return {
    kind: 'datachannel' as const,
    openChannel(label: ChannelLabel): MessageTransport {
      if (stopped) return new ClosedTransport();
      const companion = textCompanionLabel(label);
      let native: DataChannelLike | undefined;
      let text: DataChannelLike | null = null;
      try {
        native = link.createChannel(label);
        text = companion === null ? null : link.createChannel(companion);
        if (isClosing(native) || (text !== null && isClosing(text))) {
          throw new TypeError('data channel closed');
        }
      } catch {
        native?.close();
        text?.close();
        if (label === 'control') queueMicrotask(() => lose('channel_closed'));
        return new ClosedTransport();
      }
      const channel = new DataChannelTransport(native, text);
      channels.add(channel);
      channel.onClose((info) => {
        channels.delete(channel);
        if (label === 'control') lose(info.reason ? 'attach_refused' : 'channel_closed');
      });
      return channel;
    },
    async apiFetch(path: string, init?: RequestInit): Promise<Response> {
      if (stopped) throw apiClosed();
      await waitForApi(init?.signal);
      if (stopped) throw apiClosed();
      return fetchOverApi(path, init);
    },
    lose,
    shutdown,
  };
}
