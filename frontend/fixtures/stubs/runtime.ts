/** MOR-1070 stub for `$lib/runtime` — fixture state/caps, no transport. */
import { harness } from '../harness-state';

/**
 * MOR-1320: `SemanticRadioSurfaces.svelte` gained a `runtime.audio` /
 * `runtime.connectionAudio` read in the MOR-1279 rxAudio slice
 * (`rxAudioSnapshot`'s `muted`/`rxEnabled`/`volume`/`connected`) and this stub
 * had no such fields — a `TypeError: Cannot read properties of undefined
 * (reading 'muted')` at first render, one seam over from the command-bus
 * export gaps this ticket also fixes. No fixture varies audio-runtime state
 * (`fixtures/catalog.ts` has no such override), so the fixed defaults below
 * mirror the real module's construction-time values
 * (`$lib/stores/audio.svelte.ts`'s initial `audioState`) rather than reading
 * from `harness` — there is nothing yet for a fixture to set.
 */
export const runtime = {
  get state() { return harness.state; },
  get caps() { return harness.caps; },
  get audio() { return { rxEnabled: false, volume: 50, muted: false }; },
  get connectionAudio() { return false; },
};
