import type { ServerState } from '../types/state';
import type { Capabilities } from '../types/capabilities';
import type { InfoResponse } from '../types/protocol';
import { markStateUpdated, setHttpConnected, setRadioHealth, setRadioStatus, setReconnecting } from '../stores/connection.svelte';

const BASE = '/api/v1';

let pollingMultiplier = 1;

export function setPollingMultiplier(m: number): void {
  pollingMultiplier = Math.max(1, Math.round(m));
}

let lastStateEtag: string | null = null;

function getStoredToken(): string | null {
  const storage = globalThis.localStorage;
  if (!storage || typeof storage.getItem !== 'function') {
    return null;
  }
  return storage.getItem('rigplane-auth-token');
}

function getAuthHeaders(): Record<string, string> {
  const token = getStoredToken();
  if (token) return { Authorization: `Bearer ${token}` };
  return {};
}

function handleUnauthorized(): void {
  const token = prompt('Enter auth token:');
  if (token) {
    localStorage.setItem('rigplane-auth-token', token);
    location.reload();
  }
}

export async function fetchState(): Promise<ServerState | null> {
  const headers: Record<string, string> = { ...getAuthHeaders() };
  if (lastStateEtag) {
    headers['If-None-Match'] = lastStateEtag;
  }

  const res = await fetch(`${BASE}/state`, { headers });
  if (res.status === 401) {
    handleUnauthorized();
    throw new Error('Unauthorized');
  }

  if (res.status === 304) {
    return null;
  }

  if (!res.ok) throw new Error(`fetchState: ${res.status}`);

  lastStateEtag = res.headers.get('ETag');
  return res.json() as Promise<ServerState>;
}

export async function fetchCapabilities(): Promise<Capabilities> {
  const res = await fetch(`${BASE}/capabilities`, { headers: getAuthHeaders() });
  if (res.status === 401) { handleUnauthorized(); throw new Error('Unauthorized'); }
  if (!res.ok) throw new Error(`fetchCapabilities: ${res.status}`);
  return res.json() as Promise<Capabilities>;
}

/** Fetch server info (version, uptime). Used by StatusBar component (Sprint 2). */
export async function fetchInfo(): Promise<InfoResponse> {
  const res = await fetch(`${BASE}/info`, { headers: getAuthHeaders() });
  if (res.status === 401) { handleUnauthorized(); throw new Error('Unauthorized'); }
  if (!res.ok) throw new Error(`fetchInfo: ${res.status}`);
  return res.json() as Promise<InfoResponse>;
}

/**
 * Poll `/api/v1/state` at the given interval, calling `callback` only when
 * the revision advances. Skips the poll if the previous one hasn't returned.
 *
 * @returns A stop function.
 */
const HTTP_ERROR_THRESHOLD = 3;

/** Clear the cached ETag so the next poll forces a fresh 200 response. */
export function clearEtag(): void {
  lastStateEtag = null;
}

export function startPolling(
  callback: (state: ServerState) => void,
  intervalMs = 200,
): () => void {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let running = true;
  let inflight = false;
  let lastRevision = -1;
  let lastHealthRevision = -1;
  let consecutiveErrors = 0;

  async function tick() {
    if (!running) return;
    if (!inflight) {
      inflight = true;
      try {
        const state = await fetchState();
        consecutiveErrors = 0;
        setReconnecting(false);
        setHttpConnected(true);
        markStateUpdated();

        // A 304 response maps to `null` here. It's still a successful poll.
        if (state?.radioDetail?.status) {
          setRadioStatus(state.radioDetail.status);
        }
        const healthRevision = state?.healthRevision ?? 0;
        if (
          state
          && (state.revision > lastRevision || healthRevision > lastHealthRevision)
        ) {
          lastRevision = state.revision;
          lastHealthRevision = healthRevision;
          callback(state);
        }
      } catch {
        consecutiveErrors++;
        setReconnecting(true);
        // Force a fresh 200 after transient errors.
        lastStateEtag = null;
        if (consecutiveErrors >= HTTP_ERROR_THRESHOLD) {
          setHttpConnected(false);
          setRadioHealth({
            serverReachable: false,
            radioLink: 'unknown',
            readiness: 'stalled',
            likelyCause: 'server_unreachable',
            sinceMs: 0,
            lastError: null,
          });
        }
      } finally {
        inflight = false;
      }
    }
    if (running) {
      timer = setTimeout(tick, intervalMs * pollingMultiplier);
    }
  }

  void tick();

  return () => {
    running = false;
    setReconnecting(false);
    if (timer) clearTimeout(timer);
  };
}
