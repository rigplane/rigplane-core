/**
 * Message-transport seam (C4).
 *
 * One typed interface for a single message channel: open/close, text and
 * binary send, open/text/binary/close/error events, channel state, and the
 * controller-session semantics the code uses today expressed in
 * transport-neutral terms.
 *
 * Today the controller session rides the `Sec-WebSocket-Protocol` handshake:
 * the client offers the session token as a subprotocol and the server echoes
 * the accepted one back. That is modelled here as an `attach` step —
 * offer `protocols`, read back `attachedProtocol` — which the WebSocket
 * implementation performs via `Sec-WebSocket-Protocol` and a future WebRTC
 * data-channel transport will perform its own way. No WebRTC code in this PR.
 *
 * Reconnect/backoff policy stays with the owners (`WsChannel`,
 * `AudioManager`); this interface only moves raw-socket handling behind the
 * seam so a second transport is a drop-in later.
 */

import type { ControllerClient } from './controller-client';

export type MessageTransportState = 'connecting' | 'open' | 'closed';

/**
 * The controller-session role, typed from the producer's own getter so this
 * stays the one definition source.
 */
export type ControllerRole = ControllerClient['role'];

/**
 * Controller-session attachment, transport-neutral.
 *
 * `protocols` are the session subprotocol tokens to offer (empty when
 * sessionless). The peer echoes the accepted one, visible as
 * `attachedProtocol` once open. `role` is the caller's controller-session
 * role, named by the caller on every attach — the transport never
 * defaults it.
 */
export interface TransportAttach {
  readonly protocols: readonly string[];
  readonly role: ControllerRole;
}

export interface TransportCloseInfo {
  code: number;
  reason: string;
  wasClean: boolean;
}

export type TransportOpenHandler = () => void;
export type TransportTextHandler = (text: string) => void;
export type TransportBinaryHandler = (data: ArrayBuffer) => void;
export type TransportCloseHandler = (info: TransportCloseInfo) => void;
export type TransportErrorHandler = () => void;

/** One message channel. */
export interface MessageTransport {
  /** Live channel state, derived from the underlying socket. */
  readonly state: MessageTransportState;
  /** Token the peer accepted on attach ('' when sessionless or not open). */
  readonly attachedProtocol: string;
  open(url: string, attach?: TransportAttach): void;
  send(data: string | ArrayBuffer): void;
  close(): void;
  onOpen(handler: TransportOpenHandler): () => void;
  onText(handler: TransportTextHandler): () => void;
  onBinary(handler: TransportBinaryHandler): () => void;
  onClose(handler: TransportCloseHandler): () => void;
  onError(handler: TransportErrorHandler): () => void;
}
