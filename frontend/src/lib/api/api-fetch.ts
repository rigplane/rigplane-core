/**
 * REST seam (C4): every `/api/v1/...` call goes through `apiFetch`.
 *
 * Core mutations carry the current controller credential. Acquisition and
 * release supply their own lifecycle headers in ControllerClient.
 *
 * Lives in `lib/api/` (a peer of `lib/transport/`) rather than in the
 * transport layer so presentation components outside the transport import
 * boundary (e.g. `components/spectrum/*`) can use it.
 */
import { controllerClient } from '../transport/controller-client';

export function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  if (path.startsWith('/api/v1/') && path !== '/api/v1/controller'
    && ['POST', 'PUT', 'PATCH', 'DELETE'].includes(init?.method?.toUpperCase() ?? 'GET')) {
    const credential = controllerClient.headers();
    if (Object.keys(credential).length > 0) {
      const headers = new Headers(init?.headers);
      for (const [name, value] of Object.entries(credential)) headers.set(name, value);
      return fetch(path, { ...init, headers });
    }
  }
  return fetch(path, init);
}
