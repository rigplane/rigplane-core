/**
 * MOR-1555 — profile-parameterized conformance harness.
 *
 * Extracted from MOR-1428's `mor1428-ic7300-conformance.isolated.test.ts`
 * (the first consumer, migrated onto this harness) so a second profile or
 * radio family is a declarative table entry (see `profiles.ts`), not a
 * copy-pasted ~170 LOC of mock scaffolding.
 *
 * ISOLATED POOL (MOR-1272 naming convention): this module-scope-mocks six
 * store/transport modules the same way
 * `panel-commands.intent.isolated.test.ts` does, for the identical reason —
 * under the `fast` project's `isolate: false`, a sibling file importing the
 * real modules later in the same worker could inherit a mocked instance from
 * the shared module cache. Any `*.isolated.test.ts` file that imports this
 * harness inherits the same six mocks; `vi.mock` factories are hoisted to
 * the top of THIS file, and ESM import ordering guarantees this module is
 * fully evaluated (registering the mocks) before a consuming test file's
 * subsequent imports of the real mocked modules resolve.
 *
 * Deliberately holds nothing beyond: the six mocks, the deep-clone fixture
 * loaders, and three assertion primitives (`expectFrames`, `expectRefusal`,
 * `expectIntentTransport`). No profile-specific literal belongs here — see
 * `profiles.ts` for per-radio fixture data.
 *
 * MOR-1562 (C8) extension: `panel-adapters.ts`'s `derive*Props` seams read
 * `runtime.state`/`runtime.caps` (the `FrontendRuntime` singleton,
 * `../frontend-runtime`), not the `getRadioState()`/`getCapabilities()`
 * store accessors the six mocks above already cover — the pre-C8 harness
 * stubbed only `rxEnabled`/`setMuted`/`setRxLive`/`setRxVolume`/`setVolume`
 * on that mock (audio-panel seam needs), so `deriveXxxProps()` calls would
 * have thrown on `undefined.state`. `get state()`/`get caps()` are added
 * below, mirroring the real `FrontendRuntime` getters
 * (`radio.current`/`getCapabilities()`) exactly but sourced from `h.state`/
 * `h.caps` like every other mock here. Also adds `getMeterCalibration`/
 * `getMeterRedline` to the capabilities-store mock — `capabilities-adapter.ts`
 * imports both alongside the already-mocked `getControlRange`, and omitting
 * them would leave two of that adapter's four exports uncallable.
 */
import { expect, vi } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import { getCommandLifecycles } from '$lib/stores/commands.svelte';

const h = vi.hoisted(() => ({
  state: null as ServerState | null,
  caps: null as Capabilities | null,
  sendCommand: vi.fn((
    _name: string,
    _params: Record<string, unknown>,
    _id?: string,
  ) => true),
  patchActiveReceiver: vi.fn(),
  patchRadioState: vi.fn(),
  patchReceiver: vi.fn(),
  rxEnabled: false,
  setMuted: vi.fn(),
  setRxLive: vi.fn(),
  setRxVolume: vi.fn(),
  setVolume: vi.fn(),
  setAudioConfig: vi.fn(),
}));

export { h };

vi.mock('$lib/transport/ws-client', () => ({
  getControlSession: vi.fn(() => ({ state: 'connected', epoch: 1 })),
  onCommandDelivery: vi.fn(() => () => undefined),
  onControlSessionTransition: vi.fn(() => () => undefined),
  sendCommand: h.sendCommand,
}));

vi.mock('$lib/stores/radio.svelte', () => ({
  getActiveReceiver: vi.fn(() => {
    if (!h.state) return null;
    return h.state.active === 'SUB' ? h.state.sub ?? null : h.state.main ?? null;
  }),
  getRadioState: vi.fn(() => h.state),
  // Unused by panel-commands.ts (it reads `$lib/state/field-status`'s
  // `isFieldAvailable` instead, which this harness leaves REAL) — kept for
  // mock-module shape completeness only.
  isRadioFieldAvailable: vi.fn(() => false),
  patchActiveReceiver: h.patchActiveReceiver,
  patchRadioState: h.patchRadioState,
  patchReceiver: h.patchReceiver,
}));

vi.mock('$lib/stores/capabilities.svelte', () => ({
  getCapabilities: vi.fn(() => h.caps),
  capabilitiesMatchGeneration: vi.fn((providerGeneration: unknown) =>
    Number.isSafeInteger(providerGeneration)
    && h.caps?.stateContractVersion === 1
    && h.caps?.providerGeneration === providerGeneration),
  getControlRange: vi.fn((name: string) =>
    (h.caps?.controls as Record<string, unknown> | undefined)?.[name] ?? null),
  // MOR-1562: mirrors the real `capabilities.svelte.ts` implementations
  // (`capabilities?.meterCalibrations?.[x] ?? null` / `...meterRedlines...`)
  // exactly — `capabilities-adapter.ts`'s `getMeterCalibration`/
  // `getMeterRedline` are thin passthroughs to these two.
  getMeterCalibration: vi.fn((meterType: string) =>
    (h.caps?.meterCalibrations as Record<string, unknown> | undefined)?.[meterType] ?? null),
  getMeterRedline: vi.fn((meterType: string) =>
    (h.caps?.meterRedlines as Record<string, unknown> | undefined)?.[meterType] ?? null),
}));

vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: {
    get rxEnabled() { return h.rxEnabled; },
    setMuted: h.setMuted,
    setRxLive: h.setRxLive,
    setRxVolume: h.setRxVolume,
    setVolume: h.setVolume,
    // MOR-1562: `panel-adapters.ts`'s `derive*Props` seams read these two
    // directly off the runtime singleton — sourced from the same `h.state`/
    // `h.caps` every other seam in this harness reads, so a test setting
    // `h.state`/`h.caps` (the existing `fixtureState`/`fixtureCaps` pattern)
    // covers this seam with no separate setup.
    get state() { return h.state; },
    get caps() { return h.caps; },
  },
}));

vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: { setAudioConfig: h.setAudioConfig },
}));

vi.mock('$lib/stores/tuning.svelte', () => ({
  getTuningStep: vi.fn(() => 1_000),
}));

/** A profile's fixture pair, as registered in `profiles.ts`. */
export interface FixturePair {
  state: ServerState;
  caps: Capabilities;
}

/** Deep clone so a test that mutates `h.state` never leaks into a sibling. */
export function fixtureState(profile: FixturePair): ServerState {
  return structuredClone(profile.state);
}

/** Deep clone so a test that mutates `h.caps` never leaks into a sibling. */
export function fixtureCaps(profile: FixturePair): Capabilities {
  return structuredClone(profile.caps);
}

function exactCalls(): Array<[string, Record<string, unknown>]> {
  return h.sendCommand.mock.calls.map(([name, params]) => [name, params]);
}

/**
 * Asserts every dispatched WS frame carries the intent-transport shape: a
 * 3-arg `sendCommand(name, params, id)` call with a string id, and exactly
 * one command-lifecycle entry per dispatched call.
 */
export function expectIntentTransport(): void {
  for (const call of h.sendCommand.mock.calls) {
    expect(call[2]).toEqual(expect.any(String));
    expect(call).toHaveLength(3);
  }
  expect(getCommandLifecycles()).toHaveLength(h.sendCommand.mock.calls.length);
}

/**
 * Runs `fn`, asserts it dispatched exactly `frames` (name + params pairs, in
 * order) via `sendCommand`, and asserts intent-transport shape on every
 * call. Returns `fn`'s return value so callers can still assert it.
 */
export function expectFrames<T>(
  fn: () => T,
  frames: Array<[string, Record<string, unknown>]>,
): T {
  const result = fn();
  expect(exactCalls()).toEqual(frames);
  expectIntentTransport();
  return result;
}

/** Runs `fn` and asserts it refused to dispatch anything at all. */
export function expectRefusal(fn: () => void): void {
  fn();
  expect(h.sendCommand).not.toHaveBeenCalled();
  expect(getCommandLifecycles()).toHaveLength(0);
}
