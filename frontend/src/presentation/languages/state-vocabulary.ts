/**
 * MOR-2036 — the data-* visual-state vocabulary a stylesheet keys off.
 *
 * `RxTxSurface.svelte` and `MetersSurface.svelte` are the two semantic-
 * vertical files whose `data-*` attributes a SHIPPED design-language
 * stylesheet actually branches on: `fieldline.css` and `studioline.css`
 * both key repeated rules off `[data-session=...]`/`[data-rf=...]`. This
 * module exists so a THIRD stylesheet author reads these exports instead
 * of reverse-engineering the value sets from those two files, the way the
 * first two families' authors had to.
 *
 * `VfoSurface.svelte` is the third file the MOR-2036 owner ruling scopes
 * this contract to, but neither shipped stylesheet selects on any
 * `data-vfo-*`/`data-active-receiver`/`data-split-*` attribute today — that
 * family carries none of the reverse-engineering burden this module
 * removes, so it is not exported here. Likewise `RxTxSurface.svelte`'s own
 * `data-origin`/`data-intent`/`data-fault-legs`/`data-receiver`/`data-slot`/
 * `data-field` and `MetersSurface.svelte`'s `data-relevant`/`data-observed`/
 * `data-meter` are real vocabularies (documented in each file's own source)
 * that no stylesheet keys off yet either; only the two collisions below
 * (`data-fault`, `data-reason`) are exported without a stylesheet consumer,
 * because they are contract defects in their own right.
 *
 * NOT part of `presentation/languages/contract.ts`: that module is a member
 * of the MOR-1077/1078/1079 workspace-purity closures
 * (`presentation/workspace/__tests__/purity.isolated.test.ts` and its two
 * siblings), which walk the closure's SOURCE TEXT and reject any member
 * naming `semantic/` in an import specifier — type-only or not, since the
 * walker does not erase `import type`. This module genuinely needs the real
 * `semantic/` unions (a second, hand-copied set would drift the moment
 * either side changed), so it lives here instead, outside that closure —
 * the same `import type` convention `projection.ts` (this directory)
 * already uses for the same one-way `presentation` → `semantic` direction:
 * accepted per the v3 ADR (`docs/plans/2026-07-25-ui-composition-
 * architecture-v3.md`) and the April ADR's "each layer depends only on the
 * layer below; never upward" rule (`docs/plans/2026-04-12-target-frontend-
 * architecture.md`). `contract.ts` itself keeps zero `semantic/`-naming
 * imports.
 */
import type {
  KeyBlockedReason, RfState, TxOrigin, TxSessionState, TxTargetUnknownReason,
} from '../../semantic/rx-tx-surface';
import type { DisabledReasonCode } from '../../semantic/radio-view-model';

export type { KeyBlockedReason, RfState, TxOrigin, TxSessionState };

/**
 * `RxTxSurface.svelte`'s `[data-testid="rx-tx-state"] data-rf` — also
 * `MetersSurface.svelte`'s `data-rf-state` (a separately-declared but
 * member-for-member identical vocabulary, `MeterRfState`). A THIRD,
 * unrelated `data-rf` lives on `components-v2/panels/TxPanel.svelte`
 * (`'on' | 'off' | 'unknown'`, disjoint from `RfState` except both include
 * `'unknown'`) — not exported here; out of scope by owner ruling.
 */
export const RF_STATES: readonly RfState[] = ['receiving', 'transmitting', 'uncertain', 'unknown'];
/** `RxTxSurface.svelte`'s `[data-testid="rx-tx-state"] data-session`. */
export const TX_SESSION_STATES: readonly TxSessionState[] = ['idle', 'pending', 'keyed', 'releasing', 'failed'];
/** `RxTxSurface.svelte`'s `[data-testid="rx-tx-state"] data-origin`. */
export const TX_ORIGINS: readonly TxOrigin[] = ['local', 'external'];
/** `RxTxSurface.svelte`'s `[data-testid="rx-tx-target"] data-target`. */
export const TX_TARGET_STATUSES = ['known', 'unknown'] as const;
export type TxTargetStatus = (typeof TX_TARGET_STATUSES)[number];

/**
 * `data-fault` COLLISION (MOR-2036) — the same attribute name renders two
 * unrelated grammars on two different elements of the semantic vertical:
 *  - `RxTxSurface.svelte`'s `.rx-tx-fault` paragraph: the App TX authority's
 *    own fault CODE (`TxAuthoritySnapshot.fault: string | null`), open-ended
 *    by design — a code this surface has never heard of must still show, so
 *    this is deliberately not a closed union.
 *  - `MetersSurface.svelte`'s `.meter-tile`: a plain over-threshold boolean,
 *    stringified the same way `data-relevant`/`data-observed` are.
 * Left unrenamed here: both attributes have existing test assertions keyed
 * to this exact name (e.g. `MetersSurface.test.ts`,
 * `rx-tx-surface.component.test.ts`) that a rename would break, for no
 * benefit to this ticket.
 */
export type RxTxFaultValue = string;
export type MetersFaultValue = 'true' | 'false';

/**
 * `data-reason` COLLISION (MOR-2036) — THREE unrelated unions share this one
 * attribute name, all in `RxTxSurface.svelte`, depending on which element
 * renders it:
 *  - the unknown-TX-target paragraph (`[data-testid="rx-tx-target"]`):
 *    `TxTargetUnknownReason` — four transient/permanent target facts.
 *  - a blocked-key-reason list item (`.rx-tx-blocked li`, no sibling
 *    `data-field`): `KeyBlockedReason` — this surface's own key-gate
 *    reasons.
 *  - a disabled-reason list item (same list, WITH a sibling `data-field`):
 *    `DisabledReasonCode` (`radio-view-model.ts`'s generic disabled-control
 *    vocabulary) — reused here as-is, not narrowed to TX-only members.
 * Left unrenamed here: existing test assertions are keyed to this exact
 * name (e.g. `rx-tx-surface.component.test.ts`'s
 * `[data-reason="radio-transmitting"]` selector) that a rename would break,
 * for no benefit to this ticket.
 */
export type { DisabledReasonCode, TxTargetUnknownReason };
