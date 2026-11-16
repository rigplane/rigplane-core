/**
 * Message-transport factory (C5b): the one call-site seam between the
 * channel owners (`ws-client.ts`, `audio-manager.ts`) and the installed
 * transport bundle. `createMessageTransport(label)` asks the bundle for
 * the label's channel; the zero-argument always-WebSocket factory it
 * replaced is gone — the transport choice lives in `transport-bundle.ts`
 * and nowhere else.
 */
import type { MessageTransport } from './message-transport';
import type { ChannelLabel } from './transport-bundle';
import { currentBundle } from './transport-bundle';

export type MessageTransportFactory = (label: ChannelLabel) => MessageTransport;

export function createMessageTransport(label: ChannelLabel): MessageTransport {
  return currentBundle().openChannel(label);
}
