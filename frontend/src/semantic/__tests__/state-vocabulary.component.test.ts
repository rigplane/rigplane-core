/**
 * MOR-2036 — the data-* visual-state vocabulary contract
 * (`presentation/languages/contract.ts`) pinned against the real semantic
 * vertical.
 *
 * The contract module cannot value-import from `semantic/` (the v3 ADR's
 * one-way dependency — `presentation/` reads `semantic/` types only, never
 * runtime values), so its `RF_STATES`/`TX_SESSION_STATES`/`TX_ORIGINS` value
 * arrays are member-for-member COPIES of `rx-tx-surface.ts`'s own
 * `RF_LABEL`/`SESSION_LABEL` keys and `TxOrigin`'s members, not imports of
 * them — the same layering reason `radio-view-model.ts` re-declares
 * `MeterRfState` instead of importing `RfState` (see that file's doc
 * comment, and `meters.test.ts`'s "not a fork" pin, which this file mirrors
 * for the contract module).
 *
 * The second half proves the two name collisions the contract documents
 * (`data-fault`: an open-ended fault CODE on RxTxSurface vs. a plain
 * boolean on MetersSurface; `data-reason`: three unrelated unions on
 * RxTxSurface alone) are real, by mounting the actual components — a type
 * assertion alone cannot show that the DOM disagrees about one name.
 *
 * `*.component.test.ts` mounts real Svelte components and routes to the
 * isolated vitest project (`vite.config.ts`).
 */
import { describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import MetersSurface from '../MetersSurface.svelte';
import RxTxSurface from '../RxTxSurface.svelte';
import { topologyFixtures, withMeters } from '../fixtures/topologies';
import { BLOCKED_LABEL, RF_LABEL, SESSION_LABEL, type TxAuthoritySnapshot } from '../rx-tx-surface';
import {
  RF_STATES, TX_ORIGINS, TX_SESSION_STATES, TX_TARGET_STATUSES,
  type DisabledReasonCode, type KeyBlockedReason, type MetersFaultValue, type RxTxFaultValue,
  type TxOrigin, type TxTargetStatus, type TxTargetUnknownReason,
} from '../../presentation/languages/contract';

/** Mounts `component`, runs `fn` over its DOM, always unmounts (mirrors `design-language-wiring.component.test.ts`). */
function withMounted<P extends Record<string, unknown>>(
  component: unknown, props: P, fn: (root: HTMLElement) => void,
): void {
  const target = document.createElement('div');
  document.body.appendChild(target);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const instance = mount(component as any, { target, props });
  flushSync();
  try { fn(target); } finally { unmount(instance); target.remove(); }
}

describe('contract.ts vocabulary arrays are not a fork of the real unions (MOR-2036)', () => {
  it('RF_STATES is exactly RfState — RF_LABEL\'s own exhaustive keys', () => {
    expect(new Set(RF_STATES)).toEqual(new Set(Object.keys(RF_LABEL)));
  });

  it('TX_SESSION_STATES is exactly TxSessionState — SESSION_LABEL\'s own exhaustive keys', () => {
    expect(new Set(TX_SESSION_STATES)).toEqual(new Set(Object.keys(SESSION_LABEL)));
  });

  it('TX_ORIGINS has exactly TxOrigin\'s two members', () => {
    // No existing exported Record to diff against (txOrigin() has no label
    // map), so this is pinned directly against the TYPE via a self-map:
    // a member added to `TxOrigin` without a matching key here fails
    // `npm run check` (missing property), and a bogus extra key fails it
    // too (excess property) — same mechanism as `meters.test.ts`'s
    // `surfaceToMeter`/`meterToSurface` pair.
    const originSelf: Record<TxOrigin, true> = { local: true, external: true };
    expect(new Set(TX_ORIGINS)).toEqual(new Set(Object.keys(originSelf)));
  });
});

const IDLE: TxAuthoritySnapshot = {
  phase: 'idle', intent: null, radioTx: 'off', txRisk: 'none', mayOwnKey: false, fault: null,
};
const FAULT: TxAuthoritySnapshot = {
  phase: 'failed', intent: null, radioTx: 'off', txRisk: 'none', mayOwnKey: false, fault: 'audio-failed',
};

describe('RxTxSurface renders exactly the contract\'s vocabulary (MOR-2036)', () => {
  it('data-rf/data-session/data-origin/data-target are members of the exported vocabulary, on a known-target idle fixture', () => {
    withMounted(RxTxSurface, { view: topologyFixtures['1/single'], tx: IDLE }, (root) => {
      const state = root.querySelector('[data-testid="rx-tx-state"]')!;
      expect(RF_STATES).toContain(state.getAttribute('data-rf'));
      expect(TX_SESSION_STATES).toContain(state.getAttribute('data-session'));
      expect(TX_ORIGINS).toContain(state.getAttribute('data-origin'));
      const targetEl = root.querySelector('[data-testid="rx-tx-target"]')!;
      expect(TX_TARGET_STATUSES).toContain(targetEl.getAttribute('data-target'));
      const expectedTargetStatus: TxTargetStatus = 'known';
      expect(targetEl.getAttribute('data-target')).toBe(expectedTargetStatus);
    });
  });

  it('data-reason\'s THREE collision cases and data-fault\'s fault-code case, on a faulted unknown-target fixture', () => {
    withMounted(RxTxSurface, { view: topologyFixtures['1/ab'], tx: FAULT }, (root) => {
      const targetEl = root.querySelector('[data-testid="rx-tx-target"]')!;
      const expectedTargetStatus: TxTargetStatus = 'unknown';
      expect(targetEl.getAttribute('data-target')).toBe(expectedTargetStatus);

      // 1. the unknown-target paragraph's own data-reason: TxTargetUnknownReason.
      // The array is TYPED, not bare — a typo'd member here fails `npm run check`.
      const TARGET_UNKNOWN_REASONS: readonly TxTargetUnknownReason[] = [
        'not-observed', 'stale', 'unsupported', 'contradiction',
      ];
      const targetReason = targetEl.getAttribute('data-reason');
      expect(TARGET_UNKNOWN_REASONS).toContain(targetReason);
      expect(targetReason).toBe('not-observed'); // the '1/ab' fixture's own declared reason

      // 2. a blocked-key list item's data-reason: KeyBlockedReason (no sibling data-field).
      // `BLOCKED_LABEL: Record<KeyBlockedReason, string>` (rx-tx-surface.ts)
      // already guarantees its keys are exactly `KeyBlockedReason`'s members;
      // `Object.keys` itself is only ever typed `string[]`, so recovering
      // that guarantee needs the cast below.
      const blockedReasonKeys = Object.keys(BLOCKED_LABEL) as readonly KeyBlockedReason[];
      const blockedItems = [...root.querySelectorAll('[data-testid="rx-tx-blocked"] li[data-reason]:not([data-field])')];
      expect(blockedItems.length).toBeGreaterThan(0);
      for (const li of blockedItems) expect(blockedReasonKeys).toContain(li.getAttribute('data-reason'));

      // 3. a disabled-reason list item's data-reason: DisabledReasonCode, WITH a sibling data-field.
      const disabledItems = [...root.querySelectorAll('[data-testid="rx-tx-blocked"] li[data-field]')];
      expect(disabledItems).toHaveLength(1);
      expect(disabledItems[0].getAttribute('data-field')).toBe('txTarget');
      const expectedDisabledReason: DisabledReasonCode = 'field-not-observed';
      expect(disabledItems[0].getAttribute('data-reason')).toBe(expectedDisabledReason);

      // 4. data-fault is the OPEN-ENDED fault CODE here, never a boolean —
      // the other half of the MOR-2036 collision proof is MetersSurface,
      // below.
      const faultEl = root.querySelector('[data-testid="rx-tx-fault"]')!;
      const faultValue: RxTxFaultValue = faultEl.getAttribute('data-fault') ?? '';
      expect(faultValue).toBe('audio-failed');
      expect(faultValue).not.toBe('true');
      expect(faultValue).not.toBe('false');
    });
  });
});

describe('MetersSurface\'s data-fault is the OTHER collision half: a plain boolean (MOR-2036)', () => {
  it('every rendered meter tile\'s data-fault is exactly "true" or "false", never a fault code', () => {
    const view = withMeters(topologyFixtures['1/single'], 'transmitting');
    withMounted(MetersSurface, { view }, (root) => {
      const tiles = [...root.querySelectorAll<HTMLElement>('[data-meter-tile][data-fault]')];
      expect(tiles.length).toBeGreaterThan(0);
      for (const tile of tiles) {
        const value = tile.dataset.fault as MetersFaultValue;
        expect(['true', 'false']).toContain(value);
      }
    });
  });
});
