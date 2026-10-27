import { validateCapabilities, type Capabilities } from '../types/capabilities';
import type { MemoryPageDto } from '../types/memory-list';
import type { InfoResponse } from '../types/protocol';
import { getAuthHeaders, getAuthToken } from '../auth';

export { getAuthHeaders, getAuthToken };

const BASE = '/api/v1';

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

export interface MemoryPageRequest { group?: number; start?: number; count?: number }

/** One page of the radio's stored memory channels; omitted fields take the server's defaults. */
export async function fetchMemoryPage(request: MemoryPageRequest, signal?: AbortSignal): Promise<MemoryPageDto> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(request)) if (value !== undefined) query.set(key, String(value));
  const res = await fetchApiResponse(`${BASE}/memories?${query}`, signal);
  if (!res.ok) throw new Error(`fetchMemoryPage: ${res.status}`);
  return res.json() as Promise<MemoryPageDto>;
}
