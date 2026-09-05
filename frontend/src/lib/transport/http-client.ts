import { validateCapabilities, type Capabilities } from '../types/capabilities';
import type { InfoResponse } from '../types/protocol';
import { getAuthHeaders, getAuthToken } from '../auth';

export { getAuthHeaders, getAuthToken };

const BASE = '/api/v1';

function handleUnauthorized(): void {
  const token = prompt('Enter auth token:');
  if (token) {
    localStorage.setItem('rigplane-auth-token', token);
    location.reload();
  }
}

export async function fetchCapabilities(): Promise<Capabilities> {
  const res = await fetch(`${BASE}/capabilities`, { headers: getAuthHeaders() });
  if (res.status === 401) { handleUnauthorized(); throw new Error('Unauthorized'); }
  if (!res.ok) throw new Error(`fetchCapabilities: ${res.status}`);
  return validateCapabilities(await res.json());
}

/** Fetch server info (version, uptime). Used by StatusBar component (Sprint 2). */
export async function fetchInfo(): Promise<InfoResponse> {
  const res = await fetch(`${BASE}/info`, { headers: getAuthHeaders() });
  if (res.status === 401) { handleUnauthorized(); throw new Error('Unauthorized'); }
  if (!res.ok) throw new Error(`fetchInfo: ${res.status}`);
  return res.json() as Promise<InfoResponse>;
}
