import type { FieldAvailability, FieldStatus, ServerState } from '$lib/types/state';

export function getFieldStatus(
  state: ServerState | null,
  publicPath: string,
): FieldStatus | undefined {
  return state?.fieldStatus?.[publicPath];
}

export function getFieldAvailability(
  state: ServerState | null,
  publicPath: string,
): FieldAvailability {
  if (!state) return 'missing';
  const status = getFieldStatus(state, publicPath);
  if (!status) return 'available';
  if (status.freshness === 'stale') return 'stale';
  return status.availability;
}

export function isFieldAvailable(
  state: ServerState | null,
  publicPath: string,
): boolean {
  return getFieldAvailability(state, publicPath) === 'available';
}

export function areFieldsAvailable(
  state: ServerState | null,
  publicPaths: readonly string[],
): boolean {
  return publicPaths.every((path) => isFieldAvailable(state, path));
}

export function isAnyFieldAvailable(
  state: ServerState | null,
  publicPaths: readonly string[],
): boolean {
  return publicPaths.some((path) => isFieldAvailable(state, path));
}
