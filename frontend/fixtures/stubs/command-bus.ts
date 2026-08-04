/**
 * MOR-1070 stub for `components-v2/wiring/command-bus`.
 *
 * Stubbed so a capture run opens no WebSocket and constructs no AudioContext
 * (the real module pulls in `$lib/transport/ws-client` and
 * `$lib/audio/audio-manager` at module scope) — the harness must be
 * deterministic and offline.
 *
 * MOR-1271: this stub went stale. `SemanticRadioSurfaces.svelte` gained a
 * `txAuxIntents` bundle in the MOR-1244/MOR-1265 vocabulary slices and now
 * imports `makeTxHandlers` and `makeVoxHandlers` alongside `makeVfoHandlers`;
 * the stub exported only the last of the three. A stub that omits a named
 * export fails at MODULE RESOLUTION rather than at call time, so the harness
 * could not build its own entry and NO fixture loaded at all — the whole
 * MOR-1070 verification surface was dark, not merely degraded.
 *
 * The rule this file therefore has to keep: mirror every factory the
 * cockpit's tree imports, not only the ones a given fixture exercises. Every
 * handler records rather than commands — the harness asserts that an intent
 * reached the bus, and must never reach a radio.
 */
import { record } from '../harness-state';

/** Records `<channel>.<handler>` with the call's arguments, for a list of handler names. */
function recorders<K extends string>(
  channel: string, names: readonly K[],
): Record<K, (...args: unknown[]) => void> {
  return Object.fromEntries(
    names.map((name) => [name, (...args: unknown[]) => record(`${channel}.${name}`, args)]),
  ) as Record<K, (...args: unknown[]) => void>;
}

export function makeVfoHandlers() {
  return {
    onVfoSelect: (...args: unknown[]): void => record('vfo.select', args),
    onSplitToggle: (...args: unknown[]): void => record('vfo.split', args),
    onDualWatchToggle: (...args: unknown[]): void => record('vfo.dualWatch', args),
  };
}

export function makeVoxHandlers() {
  return recorders('vox', [
    'onVoxToggle', 'onVoxGainChange', 'onAntiVoxGainChange', 'onVoxDelayChange',
  ] as const);
}

export function makeTxHandlers() {
  return recorders('tx', [
    'onRfPowerChange', 'onMicGainChange', 'onAtuToggle', 'onAtuTune', 'onVoxToggle',
    'onCompToggle', 'onCompLevelChange', 'onMonToggle', 'onMonLevelChange', 'onDriveGainChange',
  ] as const);
}
