import { getAuthToken } from './http-client';

function resolveWsUrl(url: string): URL {
  return new URL(url, `${location.protocol}//${location.host}/`);
}

export function withoutWsAuthToken(url: string): string {
  const resolved = resolveWsUrl(url);
  if (!resolved.searchParams.has('token')) return url;
  resolved.searchParams.delete('token');
  return resolved.toString();
}

export function authenticatedWsUrl(url: string): string {
  const cleanUrl = withoutWsAuthToken(url);
  const resolved = resolveWsUrl(cleanUrl);
  const protocol = resolved.protocol.replace(/^ws/, 'http');
  if (protocol !== location.protocol || resolved.host !== location.host) return cleanUrl;
  const token = getAuthToken();
  if (!token) return cleanUrl;
  resolved.searchParams.set('token', token);
  return resolved.toString();
}

/**
 * Set one query parameter without losing the URL's form. A same-origin
 * relative URL (`/api/v1/ws`, the unauthenticated local default) stays
 * relative so the WebSocket constructor resolves its scheme; `new URL(url)`
 * on it would throw.
 */
export function withWsSearchParam(url: string, key: string, value: string): string {
  const resolved = resolveWsUrl(url);
  resolved.searchParams.set(key, value);
  if (/^wss?:/i.test(url)) return resolved.toString();
  return `${resolved.pathname}${resolved.search}${resolved.hash}`;
}
