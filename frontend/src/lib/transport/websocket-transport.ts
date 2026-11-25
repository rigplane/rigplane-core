/**
 * `WebSocketTransport`: the `MessageTransport` over a raw `WebSocket`.
 *
 * Behaviour is exactly what `WsChannel` and `AudioManager` did with the
 * socket directly: URL as given, session subprotocols offered via
 * `Sec-WebSocket-Protocol`, `binaryType = 'arraybuffer'`, text/binary split
 * on incoming frames, an error closes the socket (so the close path — and
 * the reconnect policy above — runs), and a close while still connecting is
 * deferred silently like the old disconnect-CONNECTING branch. The
 * controller-session attach step is the subprotocol offer plus the
 * `protocol` echo read back as `attachedProtocol`.
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

export class WebSocketTransport implements MessageTransport {
  private socket: WebSocket | null = null;
  private openHandlers = new Set<TransportOpenHandler>();
  private textHandlers = new Set<TransportTextHandler>();
  private binaryHandlers = new Set<TransportBinaryHandler>();
  private closeHandlers = new Set<TransportCloseHandler>();
  private errorHandlers = new Set<TransportErrorHandler>();

  get state(): MessageTransportState {
    const socket = this.socket;
    if (!socket) return 'closed';
    if (socket.readyState === WebSocket.OPEN) return 'open';
    if (socket.readyState === WebSocket.CONNECTING) return 'connecting';
    return 'closed';
  }

  get attachedProtocol(): string {
    return this.socket?.protocol ?? '';
  }

  open(url: string, attach?: TransportAttach): void {
    // A previous socket, if any, is abandoned untouched — the owners never
    // open over a live channel, and its late events stay deliverable so
    // their record-then-guard handling matches the raw-socket wiring.
    const protocols = attach && attach.protocols.length > 0 ? [...attach.protocols] : undefined;
    const socket = protocols ? new WebSocket(url, protocols) : new WebSocket(url);
    socket.binaryType = 'arraybuffer';
    this.socket = socket;
    socket.onopen = () => {
      for (const handler of [...this.openHandlers]) handler();
    };
    socket.onmessage = (event: MessageEvent) => {
      const data: unknown = event?.data;
      if (typeof data === 'string') {
        for (const handler of [...this.textHandlers]) handler(data);
      } else if (data instanceof ArrayBuffer) {
        for (const handler of [...this.binaryHandlers]) handler(data);
      }
    };
    socket.onerror = () => {
      // Today's wiring closed the socket on error; the close path (and the
      // reconnect policy above it) is what recovers.
      this.closeSocket(socket);
      for (const handler of [...this.errorHandlers]) handler();
    };
    socket.onclose = (event: CloseEvent) => {
      // Detach only the current socket; a superseded socket's late close
      // must not blank a newer channel. The event itself is still delivered
      // so record-then-guard subscribers see every close, as before.
      if (this.socket === socket) this.socket = null;
      const info: TransportCloseInfo = {
        code: event?.code ?? 0,
        reason: event?.reason ?? '',
        wasClean: event?.wasClean ?? false,
      };
      for (const handler of [...this.closeHandlers]) handler(info);
    };
  }

  send(data: string | ArrayBuffer): void {
    const socket = this.socket;
    if (!socket) throw new Error('WebSocketTransport: send on a closed transport');
    socket.send(data);
  }

  close(): void {
    const socket = this.socket;
    this.socket = null;
    if (!socket) return;
    if (socket.readyState === WebSocket.CONNECTING) {
      // MOR-4469: cancel the handshake itself instead of letting it finish
      // (an audio stop must not keep the connection attempt alive until it
      // opens). A deliberate cancel is not a transport error, so onerror is
      // silenced; onclose stays mapped so the eventual close still reports.
      socket.onopen = () => {};
      socket.onerror = () => {};
    }
    this.closeSocket(socket);
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

  private closeSocket(socket: WebSocket): void {
    try {
      socket.close();
    } catch {
      // Already gone — the close event (if any) carries the outcome.
    }
  }
}
