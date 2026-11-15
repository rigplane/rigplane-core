/**
 * Message-transport factory (C4): the one place that decides which
 * `MessageTransport` implementation a channel gets.
 *
 * `ws-client.ts` (control + scope) and `audio-manager.ts` create their
 * channel through here instead of `new WebSocket`. The default is the
 * WebSocket implementation, preserving today's behaviour; a future change
 * adds a second transport behind this same function.
 */
import type { MessageTransport } from './message-transport';
import { WebSocketTransport } from './websocket-transport';

export type MessageTransportFactory = () => MessageTransport;

export function createMessageTransport(): MessageTransport {
  return new WebSocketTransport();
}
