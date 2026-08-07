/** MOR-1070 stub for `$lib/runtime` — fixture state/caps, no transport. */
import { harness } from '../harness-state';

/**
 * MOR-1320: `SemanticRadioSurfaces.svelte` gained a `runtime.audio` /
 * `runtime.connectionAudio` read in the MOR-1279 rxAudio slice
 * (`rxAudioSnapshot`'s `muted`/`rxEnabled`/`volume`/`connected`) and this stub
 * had no such fields — a `TypeError: Cannot read properties of undefined
 * (reading 'muted')` at first render, one seam over from the command-bus
 * export gaps this ticket also fixes. MOR-1392 adds the fixture-level audio
 * axis, so this stub now reads the selected fixture's merged runtime facts
 * from `harness`; fixtures without overrides retain the same construction-
 * time defaults through `DEFAULT_AUDIO_RUNTIME` in `harness-state.ts`.
 */
/**
 * MOR-1312 (slice 12B): `SemanticRadioSurfaces` gained a
 * `runtime.defaultScopeStatus` / `runtime.scope.hardwareScopeConnected` /
 * `runtime.radioPowerOn` read (`scopeDisplaySnapshot`) — the same class of
 * gap MOR-1320 fixed for `runtime.audio` above. No fixture varies
 * scope-runtime state (`fixtures/catalog.ts` has no such override), so these
 * are fixed, honest "never observed" defaults, not reads from `harness`.
 */
const DEFAULT_SCOPE_STATUS = {
  source: null, available: false, resourceSelected: false, demand: 0,
  lifecycle: 'inactive' as const, transport: 'disconnected' as const, frameSeen: false,
};

export const runtime = {
  get state() { return harness.state; },
  get caps() { return harness.caps; },
  get audio() {
    const { rxEnabled, volume, muted } = harness.audioRuntime;
    return { rxEnabled, volume, muted };
  },
  get connectionAudio() { return harness.audioRuntime.connectionAudio; },
  get defaultScopeStatus() { return DEFAULT_SCOPE_STATUS; },
  get radioPowerOn() { return null; },
  get scope() { return { hardwareScopeConnected: false }; },
};
