/**
 * MOR-1070 — mutable holders the aliased fixture stubs read.
 *
 * VERIFICATION-ONLY TOOLING. Nothing under `src/` imports this file; it lives
 * outside `src/` on purpose so `eslint src/` and `vitest` (include:
 * `src/ ** /*.test.ts`) both ignore it. The production tree is byte-unchanged.
 *
 * The cockpit reaches live state through exactly four seams
 * (`$lib/runtime`, `$lib/runtime/tx-controller/managed-app-host`,
 * `$lib/runtime/adapters/mod-input-tx-guard.svelte` and the wiring's
 * `panel-adapters`). `vite.fixtures.config.ts` re-points those four at
 * `fixtures/stubs/*`, which read the server-shaped holders below. Everything else — the real
 * view-model adapter, the real presentation-capability derivation, the real
 * semantic surfaces, the real i18n catalog, the real CSS — is the shipped code.
 */
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';

export interface TxSnapshot {
  phase: 'idle' | 'key-confirm-pending' | 'active' | 'releasing' | 'failed';
  intent: 'momentary' | 'latched' | null;
  radioTx: 'off' | 'on' | 'unknown';
  txRisk: 'none' | 'uncertain' | 'confirmed-on';
  fault: string | null;
  faultDetail: null;
  fresh: boolean;
  releaseRequired: boolean;
  remainingMs: number | null;
  lastOperation: 'ptt_on' | 'transmit_on' | 'force_receive' | null;
}

export interface ModGuardProps {
  visible: boolean;
  sourceLabel: string | null;
}

export interface AudioRuntimeState {
  rxEnabled: boolean;
  muted: boolean;
  volume: number;
  connectionAudio: boolean;
}

export const DEFAULT_AUDIO_RUNTIME: AudioRuntimeState = {
  rxEnabled: false,
  muted: false,
  volume: 50,
  connectionAudio: false,
};

export const IDLE_TX: TxSnapshot = {
  phase: 'idle', intent: null, radioTx: 'off', txRisk: 'none', fault: null,
  faultDetail: null, fresh: true, releaseRequired: false, remainingMs: null, lastOperation: null,
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
  audioRuntime: { ...DEFAULT_AUDIO_RUNTIME } as AudioRuntimeState,
  calls: [] as HarnessCall[],
  listeners: new Set<(next: TxSnapshot) => void>(),
};

export function record(fn: string, args: unknown[]): void {
  harness.calls.push({ fn, args });
}
