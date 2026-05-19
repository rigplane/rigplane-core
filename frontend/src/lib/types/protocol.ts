// WebSocket protocol types

// Outgoing: client → server (envelope format)
export interface WsCommand {
  type: 'cmd';
  name: string;
  id: string;
  params: Record<string, unknown>;
}

// DX cluster spot
export interface DxSpot {
  spotter: string;
  freq: number;
  call: string;
  comment: string;
  time_utc: string;
  timestamp: number;
}

// Incoming JSON message union (scope_data is binary — handled via onBinary, not here)
export type WsIncoming =
  | { type: 'dx_spot'; spot: DxSpot }
  | { type: 'dx_spots'; spots: DxSpot[] }
  | {
      type: 'notification';
      level: string;
      message: string;
      category?: string;
      // RP-ML-005: optional reason code + params for localized resolution.
      // When `code` is present, the frontend resolves `core.toast.<code>`
      // via the i18n runtime; `message` remains the legacy English fallback.
      code?: string;
      params?: Record<string, string | number>;
    }
  | { type: 'ack'; id: string }
  | { type: 'error'; id: string; message: string }
  | { type: 'response'; id: string; ok: boolean; result?: Record<string, unknown>; error?: string; message?: string }
  | { type: 'hello'; proto: number; server: string; version: string; radio: string; connected: boolean; capabilities: string[] }
  | { type: 'state'; data: Record<string, unknown> }
  | { type: 'state_update'; data: Record<string, unknown> }
  | { type: 'companion_state'; tuning_step_hz?: number; [key: string]: unknown }
  | { type: 'event'; name?: string; event?: string; data?: Record<string, unknown>; connected?: boolean; radio_ready?: boolean };

// Incoming: server → client (base interface for typed sub-interfaces)
export interface WsMessage {
  type: 'dx_spot' | 'dx_spots' | 'notification' | 'ack' | 'error' | 'response' | 'hello' | 'state' | 'state_update' | 'companion_state' | 'event';
  [key: string]: unknown;
}

export interface AckMessage extends WsMessage {
  type: 'ack';
  id: string;
}

export interface ErrorMessage extends WsMessage {
  type: 'error';
  id: string;
  message: string;
}

export interface NotificationMessage extends WsMessage {
  type: 'notification';
  level: 'info' | 'warning' | 'error';
  message: string;
}

// /api/v1/info response
export interface InfoResponse {
  version: string;
  revision: number;
  updatedAt: string;
  uptime: number;
}

// Command type constants
export const CMD_SET_FREQ = 'set_freq';
export const CMD_SET_MODE = 'set_mode';
export const CMD_SET_FILTER = 'set_filter';

/** Generate a unique command ID (works in non-secure contexts too). */
export function makeCommandId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  // Fallback for HTTP (non-secure) contexts
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const h = [...bytes].map(b => b.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}`;
}
