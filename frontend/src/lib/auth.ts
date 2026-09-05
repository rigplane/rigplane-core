export function getAuthToken(): string | null {
  return null;
}

/** @deprecated Application authentication headers are no longer produced here. */
export function getAuthHeaders(): Record<string, string> {
  return {};
}
