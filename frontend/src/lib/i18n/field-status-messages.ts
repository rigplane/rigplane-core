/**
 * Field-status refusal wire-code → catalog-key boundary table (MOR-4828).
 *
 * The wire codes stay short and stable: they cross the HTTP/WS API and are
 * emitted by the Python server (`acquisition_scheduler.FieldRefusalWire`),
 * so they are a protocol contract. The catalog keys live here, frontend-side,
 * so translators rename copy without touching the wire.
 *
 * This table lives in `$lib/i18n` — with `blocked-reasons.ts` — and NOT in
 * `lib/state`: `field-status.ts` reads the table but must not own the
 * catalog layout, and renderers that already import the i18n layer resolve
 * through it without reaching into state.
 *
 * Wire codes are validated by shape (same rule `messageFromReasonCode`
 * applies to toast codes): a code that is not in this table resolves to
 * `undefined` so the caller falls back to the English `message` the server
 * always sends alongside the key.
 */
/** Wire code → catalog key. */
export const FIELD_STATUS_MESSAGE_KEYS: Record<string, string> = {
  readRefused: 'core.fieldStatus.readRefused',
  readMalformed: 'core.fieldStatus.readMalformed',
};

/**
 * Resolve a server-sent field-status message code to its catalog key —
 * `undefined` for anything not in the catalog, so an unrecognised code (or
 * a non-string payload) keeps its verbatim English fallback, and
 * prototype-junk strings like `'constructor'` never resolve either (guarded
 * hasOwnProperty lookup, same as the blocked-reason guard).
 */
export const fieldStatusMessageKey = (code: unknown): string | undefined =>
  typeof code === 'string' &&
  Object.prototype.hasOwnProperty.call(FIELD_STATUS_MESSAGE_KEYS, code)
    ? FIELD_STATUS_MESSAGE_KEYS[code] : undefined;
