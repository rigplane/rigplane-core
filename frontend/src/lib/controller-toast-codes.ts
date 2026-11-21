/**
 * Controller wire-code → toast-code boundary table (MOR-4250).
 *
 * The wire codes stay snake_case: they cross the HTTP API and are emitted
 * by the Python server too, so they are a protocol contract. Toast codes
 * are camelCase because `messageFromReasonCode` resolves
 * `core.toast.<code>` and validates the code shape.
 *
 * This table lives outside `$lib/transport` on purpose: presentation
 * entry points (notably `src/App.svelte`) are banned from importing
 * transport modules (ADR 2026-04-12, `no-restricted-imports`), but still
 * need to recognise a coded controller failure. Transport keeps
 * re-exporting the table, so runtime consumers are unaffected.
 */
export const CONTROLLER_TOAST_CODES = {
  controller_busy: 'controllerBusy',
  controller_invalid: 'controllerInvalid',
  controller_lost: 'controllerLost',
  controller_not_ready: 'controllerNotReady',
  controller_unsupported: 'controllerUnsupported',
  controller_local_required: 'controllerLocalRequired',
  remote_disabled: 'remoteDisabled',
} as const;

export type ControllerWireCode = keyof typeof CONTROLLER_TOAST_CODES;

/**
 * Resolve the toast code carried by an unknown bootstrap/init failure.
 * Structural check (a string `code` present in the table) rather than
 * `instanceof`, so callers never import the transport error class.
 * Returns `null` when the failure carries no controller wire code.
 */
export function controllerToastCodeOf(err: unknown): string | null {
  if (typeof err !== 'object' || err === null) return null;
  const code = (err as { code?: unknown }).code;
  if (typeof code !== 'string' || !(code in CONTROLLER_TOAST_CODES)) return null;
  return CONTROLLER_TOAST_CODES[code as ControllerWireCode];
}
