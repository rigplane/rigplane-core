/**
 * MOR-1070 — mutable holders the aliased fixture stubs read.
 *
 * VERIFICATION-ONLY TOOLING. Nothing under `src/` imports this file; it lives
 * outside `src/` on purpose so `eslint src/`, `svelte-check --tsconfig
 * tsconfig.app.json` (include: `src/**`) and `vitest` (include:
 * `src/ ** /*.test.ts`) all ignore it. The production tree is byte-unchanged.
 *
 * The cockpit reaches live state through exactly four seams
 * (`$lib/runtime`, `$lib/runtime/tx-controller/app-host`,
 * `$lib/runtime/adapters/mod-input-tx-guard.svelte` and the wiring's
 * `command-bus`). `vite.fixtures.config.ts` re-points those four at
 * `fixtures/stubs/*`, which read the holders below. Everything else — the real
 * view-model adapter, the real presentation-capability derivation, the real
 * semantic surfaces, the real i18n catalog, the real CSS — is the shipped code.
 */
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';

export interface TxSnapshot {
  phase: 'idle' | 'audio-start-pending' | 'key-confirm-pending' | 'active' | 'releasing' | 'failed';
  intent: 'momentary' | 'latched' | null;
  guard: { leaseId: string } | null;
  radioTx: 'off' | 'on' | 'unknown';
  txRisk: 'none' | 'uncertain' | 'confirmed-on';
  mayOwnKey: boolean;
  fault: string | null;
}

export interface ModGuardProps {
  visible: boolean;
  sourceLabel: string | null;
}

export const IDLE_TX: TxSnapshot = {
  phase: 'idle', intent: null, guard: null,
  radioTx: 'off', txRisk: 'none', mayOwnKey: false, fault: null,
};

export interface HarnessCall {
  fn: string;
  args: unknown[];
}

export const harness = {
  state: null as ServerState | null,
  caps: null as Capabilities | null,
  tx: { ...IDLE_TX } as TxSnapshot,
  modGuard: { visible: false, sourceLabel: null } as ModGuardProps,
  calls: [] as HarnessCall[],
  listeners: new Set<(next: TxSnapshot) => void>(),
};

export function record(fn: string, args: unknown[]): void {
  harness.calls.push({ fn, args });
}
