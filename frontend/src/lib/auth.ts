/** Read the current application authentication token for one request. */
export function getAuthToken(): string | null {
  const storage = globalThis.localStorage;
  if (!storage || typeof storage.getItem !== 'function') {
    return null;
  }
  return storage.getItem('rigplane-auth-token');
}

/** Build the authorization headers for one request from current storage. */
export function getAuthHeaders(): Record<string, string> {
  const token = getAuthToken();
  if (token) return { Authorization: `Bearer ${token}` };
  return {};
}
