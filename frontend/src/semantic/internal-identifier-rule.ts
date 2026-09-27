/**
 * MOR-2716 — the internal-identifier rule for the whole-page placeholder
 * guard (MOR-2677 part 2's second check). A PURE module: no DOM, no Svelte,
 * no runtime callers — its only callers are this module's vitest suite and
 * the Playwright page scan (`tests/e2e/placeholder-guard/page-scan.spec.ts`).
 *
 * Vocabulary: built at run time from the view-model contract's own constant
 * tables — the parser's accepted disabled-reason codes
 * (`DISABLED_REASON_CODES`), the VFO slot kinds (`VFO_SLOT_KINDS`) and the
 * TX-target unknown reason codes (`TX_TARGET_UNKNOWN_REASONS`), all in
 * `radio-view-model.ts`. Never a hand-written second list: adding a code to
 * one of those tables puts it on screen-alert with no other edit.
 *
 * The `KeyBlockedReason` codes and `FAULT_REASON_CODES` live only in
 * `rx-tx-surface.ts` (owned by #3774) behind an i18n import chain the
 * Node-side scan cannot load; they are out of this vocabulary until that
 * file offers an importable runtime table.
 *
 * Matching: whole identifiers only. An identifier run is a maximal
 * `[A-Za-z0-9_-]` sequence, so `-` inside a code is part of the code, and a
 * longer or shorter variant (`field-not-observed-yet`,
 * `prefixed-field-not-observed`) does not match. Ordinary English words in
 * catalog sentences cannot trip the rule unless they are exactly the
 * identifier. Unlike the token rule, there is no title allowance: a raw
 * code is wrong in a title too.
 */
import {
  DISABLED_REASON_CODES,
  TX_TARGET_UNKNOWN_REASONS,
  VFO_SLOT_KINDS,
} from './radio-view-model';

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
