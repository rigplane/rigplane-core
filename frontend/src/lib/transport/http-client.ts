import { validateCapabilities, type Capabilities } from '../types/capabilities';
import type { InfoResponse } from '../types/protocol';
import { getAuthHeaders, getAuthToken } from '../auth';

export { getAuthHeaders, getAuthToken };

const BASE = '/api/v1';

/** @deprecated Retained for source compatibility; HTTP requests do not throw this error. */
export class AuthenticationError extends Error {
  override name = 'AuthenticationError';
}

async function fetchApiResponse(path: string, signal?: AbortSignal): Promise<Response> {
  signal?.throwIfAborted();
  const res = await fetch(path, { headers: getAuthHeaders(), signal, redirect: 'error' });
  signal?.throwIfAborted();
  return res;
}

export async function fetchCapabilities(): Promise<Capabilities> {
  const res = await fetchApiResponse(`${BASE}/capabilities`);
  if (!res.ok) throw new Error(`fetchCapabilities: ${res.status}`);
  return validateCapabilities(await res.json());
}

export async function fetchInfo(signal?: AbortSignal): Promise<InfoResponse> {
  const res = await fetchApiResponse(`${BASE}/info`, signal);
  if (!res.ok) throw new Error(`fetchInfo: ${res.status}`);
  return res.json() as Promise<InfoResponse>;
}
