/**
 * REST seam (C4/C5b): every `/api/v1/...` call goes through `apiFetch`.
 *
 * The call sites are unchanged; where a request travels is the installed
 * transport bundle's decision (`transport-bundle.ts`): the WebSocket
 * bundle's `apiFetch` is the global `fetch` this body used to be — same
 * URLs, headers, redirect options and error handling — and a data-channel
 * bundle serves it from the attached `api` channel. The direct `fetch`
 * body is replaced, not kept beside.
 *
 * Lives in `lib/api/` (a peer of `lib/transport/`) rather than in the
 * transport layer so presentation components outside the transport import
 * boundary (e.g. `components/spectrum/*`) can use it.
 */
import { currentBundle } from '../transport/transport-bundle';

export function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  return currentBundle().apiFetch(path, init);
}
