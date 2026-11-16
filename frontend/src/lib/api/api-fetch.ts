/**
 * REST seam (C4): every `/api/v1/...` call goes through `apiFetch`.
 *
 * Same URLs, headers, redirect options and error handling as the direct
 * `fetch` calls it replaces — the default implementation IS `fetch`, so
 * behaviour is unchanged. A future WebRTC-backed UI routes the `api` data
 * channel through this same function.
 *
 * Lives in `lib/api/` (a peer of `lib/transport/`) rather than in the
 * transport layer so presentation components outside the transport import
 * boundary (e.g. `components/spectrum/*`) can use it.
 */
export function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(path, init);
}
