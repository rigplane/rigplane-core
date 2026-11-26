/**
 * REST seam (C4): every `/api/v1/...` call goes through `apiFetch`.
 *
 * Remote Core mutations carry the current controller credential. A caller that
 * already supplies its own `X-RigPlane-Controller` header (managed-transmit and
 * controller clients, acquisition and release) wins: the request is passed to
 * `fetch` unchanged.
 *
 * Lives in `lib/api/` (a peer of `lib/transport/`) rather than in the
 * transport layer so presentation components outside the transport import
 * boundary (e.g. `components/spectrum/*`) can use it.
 */
import { controllerClient } from '../transport/controller-client';

const CONTROLLER_HEADER = 'x-rigplane-controller';

function hasControllerHeader(headers: HeadersInit | undefined): boolean {
  if (!headers) return false;
  if (headers instanceof Headers) return headers.has(CONTROLLER_HEADER);
  if (Array.isArray(headers)) return headers.some(([name]) => name.toLowerCase() === CONTROLLER_HEADER);
  return Object.keys(headers).some((name) => name.toLowerCase() === CONTROLLER_HEADER);
}

export function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  if (controllerClient.remote && path.startsWith('/api/v1/') && path !== '/api/v1/controller'
    && ['POST', 'PUT', 'PATCH', 'DELETE'].includes(init?.method?.toUpperCase() ?? 'GET')
    && !hasControllerHeader(init?.headers)) {
    const credential = controllerClient.headers();
    if (Object.keys(credential).length > 0) {
      const callerHeaders = init?.headers;
      if (callerHeaders && !(callerHeaders instanceof Headers) && !Array.isArray(callerHeaders)) {
        return fetch(path, { ...init, headers: { ...callerHeaders, ...credential } });
      }
      const headers = new Headers(callerHeaders);
      for (const [name, value] of Object.entries(credential)) headers.set(name, value);
      return fetch(path, { ...init, headers });
    }
  }
  return fetch(path, init);
}
