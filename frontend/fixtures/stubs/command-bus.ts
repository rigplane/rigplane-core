/**
 * MOR-1070 stub for `components-v2/wiring/command-bus`.
 *
 * Only `makeVfoHandlers` is reachable from the cockpit's tree. Stubbed so a
 * capture run opens no WebSocket and constructs no AudioContext (the real
 * module pulls in `$lib/transport/ws-client` and `$lib/audio/audio-manager` at
 * module scope) — the harness must be deterministic and offline.
 */
import { record } from '../harness-state';

export function makeVfoHandlers() {
  return {
    onVfoSelect: (...args: unknown[]): void => record('vfo.select', args),
    onSplitToggle: (...args: unknown[]): void => record('vfo.split', args),
    onDualWatchToggle: (...args: unknown[]): void => record('vfo.dualWatch', args),
  };
}
