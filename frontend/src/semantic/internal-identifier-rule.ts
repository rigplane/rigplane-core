/**
 * MOR-2716 — the internal-identifier rule for the whole-page placeholder
 * guard (MOR-2677 part 2's second check). A PURE module: no DOM, no Svelte,
 * no runtime callers — its only callers are this module's vitest suite and
 * the Playwright page scan (`tests/e2e/placeholder-guard/page-scan.spec.ts`).
 *
 * Vocabulary: built at run time from the contract's own constant tables —
 * the parser's accepted disabled-reason codes (`DISABLED_REASON_CODES`),
 * the VFO slot kinds (`VFO_SLOT_KINDS`) and the TX-target unknown reason
 * codes (`TX_TARGET_UNKNOWN_REASONS`), all in `radio-view-model.ts` — and
 * the RX/TX surface's key-blocked reason codes
 * (`KEY_BLOCKED_REASON_CODES`) and fault reason codes
 * (`FAULT_REASON_CODES`), both in `rx-tx-codes.ts`. Never a hand-written
 * second list: adding a code to one of those tables puts it on
 * screen-alert with no other edit.
 *
 * Matching: whole identifiers only. An identifier run is a maximal
 * `[A-Za-z0-9_-]` sequence, so `-` inside a code is part of the code, and a
 * longer or shorter variant (`field-not-observed-yet`,
 * `prefixed-field-not-observed`) does not match. Unlike the token rule,
 * there is no title allowance: a raw code is wrong in a title too.
 */
import {
  DISABLED_REASON_CODES,
  TX_TARGET_UNKNOWN_REASONS,
  VFO_SLOT_KINDS,
} from './radio-view-model';
import { FAULT_REASON_CODES, KEY_BLOCKED_REASON_CODES } from './rx-tx-codes';

export type InternalIdentifierTokenKind = 'internal-identifier';

export interface InternalIdentifierToken {
  readonly kind: InternalIdentifierTokenKind;
  /** The offending identifier, exactly as it appeared. */
  readonly token: string;
}

export const INTERNAL_IDENTIFIER_VOCABULARY: readonly string[] = [
  ...new Set([
    ...DISABLED_REASON_CODES,
    ...VFO_SLOT_KINDS,
    ...TX_TARGET_UNKNOWN_REASONS,
    ...KEY_BLOCKED_REASON_CODES,
    ...FAULT_REASON_CODES,
  ]),
].sort();

const VOCABULARY: ReadonlySet<string> = new Set(INTERNAL_IDENTIFIER_VOCABULARY);
const NON_IDENTIFIER_CHARS = /[^A-Za-z0-9_-]+/;

export function findInternalIdentifiers(text: string): InternalIdentifierToken[] {
  const hits: InternalIdentifierToken[] = [];
  for (const run of text.split(NON_IDENTIFIER_CHARS)) {
    if (VOCABULARY.has(run)) {
      hits.push({ kind: 'internal-identifier', token: run });
    }
  }
  return hits;
}
