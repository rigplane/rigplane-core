import type { FieldAvailability, FieldStatus, ServerState } from '$lib/types/state';

export function getFieldStatus(
  state: ServerState | null,
  publicPath: string,
): FieldStatus | undefined {
  return state?.fieldStatus?.[publicPath];
}

/**
 * Resolve the effective availability of a child path from the most specific
 * ancestor that carries an explicit field-status entry.
 *
 * A grouped family (e.g. `scopeControls`) may be observed/seeded as a whole
 * while its individual leaves (`scopeControls.mode`, …) have no own entry.
 * When the parent is `missing`/`stale`, the child must inherit that — otherwise
 * an unobserved leaf would resolve to `available` and the UI would present a
 * default (CTR / MID / …) as confirmed. Returns `undefined` when no ancestor
 * carries a status.
 */
function parentAvailability(
  state: ServerState | null,
  publicPath: string,
): FieldAvailability | undefined {
  const fieldStatus = state?.fieldStatus;
  if (!fieldStatus) return undefined;
  let prefix = publicPath;
  let dot = prefix.lastIndexOf('.');
  while (dot > 0) {
    prefix = prefix.slice(0, dot);
    const status = fieldStatus[prefix];
    if (status) {
      if (status.freshness === 'stale') return 'stale';
      return status.availability;
    }
    dot = prefix.lastIndexOf('.');
  }
  return undefined;
}

export function getFieldAvailability(
  state: ServerState | null,
  publicPath: string,
): FieldAvailability {
  if (!state) return 'missing';
  const status = getFieldStatus(state, publicPath);
  if (!status) {
    // No own entry: inherit from the nearest ancestor that has one. A
    // `missing`/`stale` parent makes the child unavailable; only when no
    // ancestor carries a status do we treat the leaf as available.
    return parentAvailability(state, publicPath) ?? 'available';
  }
  if (status.freshness === 'stale') return 'stale';
  if (status.availability === 'available') {
    // Own entry says available, but a `missing`/`stale` parent still wins —
    // a stale/unobserved group must not be confirmed via one leaf.
    const parent = parentAvailability(state, publicPath);
    if (parent && parent !== 'available') return parent;
  }
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
