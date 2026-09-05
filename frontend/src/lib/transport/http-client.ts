import { validateCapabilities, type Capabilities } from '../types/capabilities';
import type { InfoResponse } from '../types/protocol';
import { getAuthHeaders, getAuthToken } from '../auth';

export { getAuthHeaders, getAuthToken };

const BASE = '/api/v1';

export class AuthenticationError extends Error {
  override name = 'AuthenticationError';
}

async function authenticatedFetch(path: string, signal?: AbortSignal): Promise<Response> {
  signal?.throwIfAborted();
  const res = await fetch(path, { headers: getAuthHeaders(), signal, redirect: 'error' });
  signal?.throwIfAborted();
  if (res.status !== 401) return res;

  const token = prompt('Enter auth token:');
  if (!token) {
    throw new AuthenticationError('Authentication cancelled. Reload the page to try again.');
  }
  signal?.throwIfAborted();
  const retry = await fetch(path, {
    headers: { Authorization: `Bearer ${token}` }, signal, redirect: 'error',
  });
  signal?.throwIfAborted();
  if (retry.status === 401) {
    throw new AuthenticationError('Auth token rejected. Reload the page to try again.');
  }
  if (retry.ok) localStorage.setItem('rigplane-auth-token', token);
  return retry;
}

export async function fetchCapabilities(): Promise<Capabilities> {
  const res = await authenticatedFetch(`${BASE}/capabilities`);
  if (!res.ok) throw new Error(`fetchCapabilities: ${res.status}`);
  return validateCapabilities(await res.json());
}

export async function fetchInfo(signal?: AbortSignal): Promise<InfoResponse> {
  const res = await authenticatedFetch(`${BASE}/info`, signal);
  if (!res.ok) throw new Error(`fetchInfo: ${res.status}`);
  return res.json() as Promise<InfoResponse>;
}
