import type { FieldAvailability, FieldStatus, ServerState } from '$lib/types/state';
import { t } from '$lib/i18n';
import { fieldStatusMessageKey } from '$lib/i18n/field-status-messages';

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
 * When the parent is `missing`, the child must inherit that — otherwise an
 * unobserved leaf would resolve to `available` and the UI would present a
 * default (CTR / MID / …) as confirmed. A `stale` parent that has been
 * observed carries a last value (MOR-2425/R29: a control greys only on a
 * real disconnect or structural absence, not on staleness alone), so it
 * resolves to `available` instead of blocking the child. Returns `undefined`
 * when no ancestor carries a status.
 */
function parentAvailability(
  state: ServerState | null,
  publicPath: string,
): { availability: FieldAvailability; status: FieldStatus } | undefined {
  const fieldStatus = state?.fieldStatus;
  if (!fieldStatus) return undefined;
  let prefix = publicPath;
  let dot = prefix.lastIndexOf('.');
  while (dot > 0) {
    prefix = prefix.slice(0, dot);
    const status = fieldStatus[prefix];
    if (status) {
      return { availability: status.freshness === 'stale' && status.observed ? 'available' : status.availability, status };
    }
    dot = prefix.lastIndexOf('.');
  }
  return undefined;
}

function resolveFieldAvailability(
  state: ServerState | null,
  publicPath: string,
): { availability: FieldAvailability; status?: FieldStatus } {
  if (!state) return { availability: 'missing' };
  const status = getFieldStatus(state, publicPath);
  if (!status) {
    // No own entry: inherit from the nearest ancestor that has one. A
    // `missing` parent makes the child unavailable; only when no ancestor
    // carries a status do we treat the leaf as available.
    return parentAvailability(state, publicPath) ?? { availability: 'available' };
  }
  if (status.freshness === 'stale') {
    // A stale-but-observed entry still carries its last value (R29), so it
    // resolves to `available` rather than being blocked.
    if (status.observed) return { availability: 'available', status };
    return { availability: status.availability, status };
  }
  if (status.availability === 'available') {
    // Own entry says available, but a `missing` parent still wins — an
    // unobserved group must not be confirmed via one leaf.
    const parent = parentAvailability(state, publicPath);
    if (parent && parent.availability !== 'available') return parent;
  }
  return { availability: status.availability, status };
}

export function getFieldAvailability(
  state: ServerState | null,
  publicPath: string,
): FieldAvailability {
  return resolveFieldAvailability(state, publicPath).availability;
}

export function getFieldAvailabilityMessage(
  state: ServerState | null,
  publicPath: string,
): FieldStatus['message'] {
  const { availability, status } = resolveFieldAvailability(state, publicPath);
  if (availability === 'available') return undefined;
  // A refusal sentence belongs to an `unavailable` row: the producer
  // contract projects a refused read as `unavailable`, and the recovered
  // row drops its message. Any other non-available row keeps the legacy
  // raw `message` read, so a stale key lingering on a missing row cannot
  // render as a refusal (field-reason e2e: 'Not yet observed').
  if (availability !== 'unavailable') return status?.message;
  return resolveFieldStatusMessage(status);
}

/**
 * The operator-legible sentence for one field-status row (MOR-4828).
 *
 * Rows that carry a `messageKey` the catalog knows render through the
 * locale table (`t(key, params)`); every other row — an unknown key, a
 * missing key from an older sender, or a row with no key at all — renders
 * the English `message` the server always sends alongside. Receivers
 * tolerate unknown/missing keys by design: the fallback is the full
 * sentence, never a bare code or a `[missing:…]` marker.
 */
export function resolveFieldStatusMessage(
  status: FieldStatus | null | undefined,
): string | undefined {
  if (!status) return undefined;
  const key = fieldStatusMessageKey(status.messageKey);
  if (key !== undefined) {
    const rendered = t(key, status.messageParams ?? undefined);
    // resolve() falls back to en-US silently; when even en-US lacks the
    // key it returns the key itself (prod) or `[missing:key]` (dev) —
    // neither is operator text, so the English wire sentence wins.
    if (rendered !== key && !rendered.startsWith('[missing:')) return rendered;
  }
  return status.message ?? undefined;
}

/** One refusal row the health area names: the refused field and its reason. */
export interface RefusedFieldEntry {
  storePath: string;
  publicPath: string;
  text: string;
}

/**
 * Every refused field-status row as operator-legible text (MOR-4828).
 *
 * Only `unavailable` rows with a resolvable sentence qualify — a refused
 * read is projected as `unavailable` by the producer contract, and the
 * recovered row drops its message, so it disappears here on recovery.
 * Public-path aliases of one store path (e.g. `micGain` / `micGainAlias`)
 * collapse to a single entry, first path wins, mirroring the status-bar
 * tooltip loop this feeds.
 */
export function refusedFieldEntries(state: ServerState | null): RefusedFieldEntry[] {
  const fieldStatus = state?.fieldStatus;
  if (!fieldStatus) return [];
  const byStore = new Map<string, RefusedFieldEntry>();
  for (const [publicPath, status] of Object.entries(fieldStatus)) {
    if (!status || status.availability !== 'unavailable') continue;
    const text = resolveFieldStatusMessage(status);
    if (!text?.trim()) continue;
    if (!byStore.has(status.storePath)) {
      byStore.set(status.storePath, { storePath: status.storePath, publicPath, text });
    }
  }
  return [...byStore.values()];
}

export function isFieldAvailable(
  state: ServerState | null,
  publicPath: string,
): boolean {
  return getFieldAvailability(state, publicPath) === 'available';
}

/**
 * Whether the field carries a freshly observed reading (MOR-4434).
 *
 * The narrow-state table selection must not ride a stale reading: the
 * server already treats a stale narrow observation as unknown
 * (`_observed_receiver_narrow` in `src/rigplane/web/handlers/control.py`
 * returns `None` unless freshness is FRESH), so a frontend that validated
 * against a held stale value would disagree with the backend's admission
 * hint. Same fresh-only predicate `panel-commands.ts`:
 * `observedAvailableField` already uses for dispatch identity — this is
 * its neutral, cross-layer home (`props`, `adapters` and `commands`
 * all read narrow through it; none may import the command layer).
 * Display facts keep the R29 stale-showing `isFieldAvailable` gate;
 * only table selection uses this one.
 */
export function isFieldFreshlyObserved(
  state: ServerState | null,
  publicPath: string,
): boolean {
  const status = getFieldStatus(state, publicPath);
  return status?.observed === true && status.freshness === 'fresh'
    && status.availability === 'available';
}

/**
 * Whether resolved availability is `available` or `stale`.
 *
 * `missing`, `unavailable` and `undeclared` all resolve to false. This
 * preserves `getFieldAvailability`'s legacy no-entry fallback, so a true
 * result is compatibility eligibility, not proof that the backend observed
 * the field. Callers that need observation evidence must inspect
 * `fieldStatus.observed`.
 */
export function isFieldRead(
  state: ServerState | null,
  publicPath: string,
): boolean {
  const availability = getFieldAvailability(state, publicPath);
  return availability === 'available' || availability === 'stale';
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
