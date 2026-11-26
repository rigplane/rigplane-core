/**
 * REST seam (C4): every `/api/v1/...` call goes through `apiFetch`.
 *
 * The active bundle supplies either browser fetch or the K5 API channel.
 *
 * Lives in `lib/api/` (a peer of `lib/transport/`) rather than in the
 * transport layer so presentation components outside the transport import
 * boundary (e.g. `components/spectrum/*`) can use it.
 */
import { currentBundle } from '../transport/transport-bundle';

export function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const bundle = currentBundle();
  return bundle.kind === 'datachannel' ? bundle.apiFetch(path, init) : fetch(path, init);
}
