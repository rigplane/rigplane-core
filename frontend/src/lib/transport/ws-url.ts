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
