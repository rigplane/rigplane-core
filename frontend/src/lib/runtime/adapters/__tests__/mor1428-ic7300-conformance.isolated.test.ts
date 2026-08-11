/**
 * MOR-1428 — Tier 2 v1: IC-7300 profile conformance suite over the
 * live-captured fixture.
 *
 * MOR-1409's A07-A15 authority migration (closed at `c96ae052`, #2317) left
 * 6543+ green frontend tests coexisting with 12 dead control families on a
 * real single-receiver radio: `knownActiveReceiver()` and its four sibling
 * gates required the radio-wide `active` field to be positively observed
 * before routing ANY receiver-scoped write, and the IC-7300's CI-V link
 * never confirms that field (dual-RX-only echo) — every synthetic test
 * fixture built `active: 'MAIN'` as an OBSERVED fact and therefore never
 * caught it. The MOR-1418/1421/1423/1419 fix wave (this repo's
 * `origin/main`, PRs #2372/#2374/#2376/#2373) made the active receiver
 * resolve structurally from capabilities instead. This suite is the
 * regression guard for that class of bug: every assertion below runs
 * against `fixtures/ic7300-{state,capabilities}.json`, a byte-faithful
 * capture of the real bench stand's `/api/v1/state` +
 * `/api/v1/capabilities` (see `fixtures/ic7300-profile.ts` for full
 * provenance) — not a synthetic state where every field is conveniently
 * observed.
 *
 * Per-assertion mapping to the walkthrough findings this closes:
 *   - `describe('view model...')`      -> MOR-1421 (activeReceiverId /
 *                                          scope-adapter / VFO tile highlight)
 *   - `describe('dual-receiver chrome...')` -> MOR-1421 (`hasDualReceiver`
 *                                          gate, SemanticRadioSurfaces.svelte)
 *   - `describe('handler dispatch...')` -> MOR-1418 (mode/filter/band/RF/AGC/
 *                                          NB/NR/AF/RIT gate) and MOR-1423
 *                                          (memory/keyboard/VFO-select gate)
 *   - `describe('honest refusals...')`  -> MOR-988 §3.2 fail-closed doctrine:
 *                                          the fix wave bypasses ONLY the
 *                                          `active` gate — leaf fields the
 *                                          radio genuinely never confirmed
 *                                          (`ritOn`, `micGain`, `main.nbLevel`)
 *                                          must still refuse.
 *
 * ISOLATED POOL (MOR-1272 naming convention): this file module-scope-mocks
 * six store/transport modules the same way
 * `panel-commands.intent.isolated.test.ts` does, for the identical reason —
 * under the `fast` project's `isolate: false`, a sibling file importing the
 * real modules later in the same worker could inherit this file's mocked
 * instances from the shared module cache.
 *
 * Unlike `panel-commands.intent.isolated.test.ts`, this file does NOT mock
 * `$lib/state/field-status` — that module is pure (reads `state.fieldStatus`
 * directly, no store lookups), and the whole point of an over-the-fixture
 * suite is to exercise it against the REAL captured `fieldStatus` map rather
 * than a synthetic per-test override.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';

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
  // `isFieldAvailable` instead, which this file leaves REAL) — kept for
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
}));

vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: {
    get rxEnabled() { return h.rxEnabled; },
    setMuted: h.setMuted,
    setRxLive: h.setRxLive,
    setRxVolume: h.setRxVolume,
    setVolume: h.setVolume,
  },
}));

vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: { setAudioConfig: h.setAudioConfig },
}));

vi.mock('$lib/stores/tuning.svelte', () => ({
  getTuningStep: vi.fn(() => 1_000),
}));

import {
  makeAgcHandlers,
  makeBandHandlers,
  makeDspHandlers,
  makeFilterHandlers,
  makeMemoryHandlers,
  makeModeHandlers,
  makeRfFrontEndHandlers,
  makeRitXitHandlers,
  makeRxAudioHandlers,
  makeTxHandlers,
  makeVfoHandlers,
  dispatchKeyboardRadioAction,
} from '../../commands/panel-commands';
import { getCommandLifecycles, resetCommandLifecycle } from '$lib/stores/commands.svelte';
import { isFieldAvailable } from '$lib/state/field-status';
import { validateRadioViewModel } from '../../../../semantic/radio-view-model';
import { toRadioViewModel } from '../radio-view-model-adapter';
import { toSpectrumAuthority } from '../scope-adapter';
import { IC7300_CAPABILITIES, IC7300_STATE } from './fixtures/ic7300-profile';

/** Deep clone so a test that mutates `h.state`/`h.caps` never leaks into a sibling. */
function fixtureState(): ServerState {
  return structuredClone(IC7300_STATE);
}
function fixtureCaps(): Capabilities {
  return structuredClone(IC7300_CAPABILITIES);
}

function exactCalls(): Array<[string, Record<string, unknown>]> {
  return h.sendCommand.mock.calls.map(([name, params]) => [name, params]);
}

function expectIntentTransport(): void {
  for (const call of h.sendCommand.mock.calls) {
    expect(call[2]).toEqual(expect.any(String));
    expect(call).toHaveLength(3);
  }
  expect(getCommandLifecycles()).toHaveLength(h.sendCommand.mock.calls.length);
}

/* ── Section 1: real adapters over the raw fixture (no store mocking needed —
 * toRadioViewModel/toSpectrumAuthority take state+caps as plain arguments). */

describe('IC-7300 fixture — real toRadioViewModel/toSpectrumAuthority (MOR-1421)', () => {
  it('resolves activeReceiver to MAIN though the live stand never observed `active` (MOR-1421)', () => {
    // Confirms the fixture actually exercises the bug: the live radio's
    // `active` field reads observed:false — the structurally-unobservable
    // shape the fix targets, not a stand-in.
    expect(IC7300_STATE.fieldStatus?.active?.observed).toBe(false);
    expect(IC7300_CAPABILITIES.receivers).toBe(1);

    const view = validateRadioViewModel(toRadioViewModel(IC7300_STATE, IC7300_CAPABILITIES));
    expect(view.activeReceiver).toEqual({ status: 'known', receiver: 'MAIN' });
    expect(view.disabledReasons).not.toContainEqual({
      field: 'activeReceiver', code: 'field-not-observed',
    });
  });

  it('marks exactly one VFO tile isActiveSlot && isActive', () => {
    const view = validateRadioViewModel(toRadioViewModel(IC7300_STATE, IC7300_CAPABILITIES));
    const activeTiles = view.vfos.filter((vfo) => vfo.isActive && vfo.isActiveSlot);
    expect(activeTiles).toHaveLength(1);
    expect(activeTiles[0].receiver).toBe('MAIN');
  });

  it('revives a non-null spectrum authority for receiver 0 at the fixture\'s live freq/mode', () => {
    const authority = toSpectrumAuthority(IC7300_STATE, IC7300_CAPABILITIES);
    expect(authority).not.toBeNull();
    expect(authority).toMatchObject({
      receiver: 0,
      frequencyHz: IC7300_STATE.main!.freqHz,
      mode: IC7300_STATE.main!.mode,
    });
  });

  it('hides dual-watch / active-receiver readout chrome — hasDualReceiver gate is false (MOR-1421, #2376)', () => {
    // Same plain expression `SemanticRadioSurfaces.svelte` computes at its
    // seam: `(runtime.caps?.receivers ?? 1) > 1`. A single-receiver IC-7300
    // fixture must resolve this to false — VfoSurface's radio-wide
    // active-receiver readout and dual-watch toggle stay hidden.
    const hasDualReceiver = (IC7300_CAPABILITIES.receivers ?? 1) > 1;
    expect(hasDualReceiver).toBe(false);
  });

  it('carries the powerOn trap shape — raw true but never observed (MOR-1439)', () => {
    // Confirms the fixture actually exercises the MOR-1439 bug: like `active`
    // above, the IC-7300's serial link never confirms powerstat, so
    // `fieldStatus.powerOn` reads observed:false/availability:'missing' even
    // though the raw top-level `powerOn` happens to read `true` here. Ingestion
    // (radio.svelte.ts) must gate on this field-status entry via
    // `isFieldAvailable`, not trust the raw value — see
    // `stores/__tests__/radio.test.ts` for the ingestion-level RED/GREEN pins.
    expect(IC7300_STATE.powerOn).toBe(true);
    expect(IC7300_STATE.fieldStatus?.powerOn?.observed).toBe(false);
    expect(isFieldAvailable(IC7300_STATE, 'powerOn')).toBe(false);
  });
});

/* ── Section 2: handler dispatch through the REAL panel-commands.ts
 * factories, fed the fixture via the mocked store seams above. Every
 * expectation names the WS frame (command name + exact params shape) the
 * real factory must produce. */

describe('IC-7300 fixture — handler dispatch through real factories (MOR-1418/MOR-1423)', () => {
  beforeEach(() => {
    h.state = fixtureState();
    h.caps = fixtureCaps();
    h.sendCommand.mockClear();
    h.patchActiveReceiver.mockClear();
    h.patchRadioState.mockClear();
    h.patchReceiver.mockClear();
    h.setAudioConfig.mockClear();
    h.rxEnabled = false;
  });

  afterEach(() => {
    resetCommandLifecycle();
  });

  it('mode: dispatches set_mode on receiver 0 although `active` was never observed', () => {
    makeModeHandlers().onModeChange('CW');
    expect(exactCalls()).toEqual([['set_mode', { mode: 'CW', receiver: 0 }]]);
    expectIntentTransport();
  });

  it('filter: dispatches set_filter on receiver 0', () => {
    makeFilterHandlers().onFilterChange(2);
    expect(exactCalls()).toEqual([['set_filter', { filter: 2, receiver: 0 }]]);
    expectIntentTransport();
  });

  it('band: dispatches set_band from a BSR-coded band select', () => {
    makeBandHandlers().onBandSelect('20m', 14_225_000, 5);
    expect(exactCalls()).toEqual([['set_band', { band: 5 }]]);
    expectIntentTransport();
  });

  it('RF front end: dispatches set_attenuator / set_preamp / set_rf_gain on receiver 0', () => {
    makeRfFrontEndHandlers().onAttChange(0);
    makeRfFrontEndHandlers().onPreChange(1);
    makeRfFrontEndHandlers().onRfGainChange(200);
    expect(exactCalls()).toEqual([
      ['set_attenuator', { db: 0, receiver: 0 }],
      ['set_preamp', { level: 1, receiver: 0 }],
      ['set_rf_gain', { level: 200, receiver: 0 }],
    ]);
    expectIntentTransport();
  });

  it('AGC: dispatches set_agc on receiver 0', () => {
    makeAgcHandlers().onAgcModeChange(2);
    expect(exactCalls()).toEqual([['set_agc', { mode: 2, receiver: 0 }]]);
    expectIntentTransport();
  });

  it('NB: dispatches set_nb on receiver 0', () => {
    makeDspHandlers().onNbToggle(true);
    expect(exactCalls()).toEqual([['set_nb', { on: true, receiver: 0 }]]);
    expectIntentTransport();
  });

  it('NR: dispatches set_nr on receiver 0', () => {
    makeDspHandlers().onNrModeChange(1);
    expect(exactCalls()).toEqual([['set_nr', { on: true, receiver: 0 }]]);
    expectIntentTransport();
  });

  it('AF level: dispatches set_af_level on receiver 0', () => {
    makeRxAudioHandlers().onAfLevelChange(0.5);
    expect(exactCalls()).toEqual([['set_af_level', { level: 0.5, receiver: 0 }]]);
    expectIntentTransport();
  });

  it('memory recall: dispatches set_memory_mode + memory_to_vfo (relative A/B identity, MOR-1423)', () => {
    // The live stand's `main.activeSlot` is unobserved, but
    // `vfoReadback: 'selected_unselected'` makes `relativeVfoIdentityUnknown`
    // route the snapshot through `main.freqHz`/`main.mode` directly instead
    // of the absolute A/B slot — both genuinely observed on this fixture.
    const ok = makeMemoryHandlers().onRecall(7);
    expect(ok).toBe(true);
    expect(exactCalls()).toEqual([
      ['set_memory_mode', { channel: 7 }],
      ['memory_to_vfo', { channel: 7 }],
    ]);
    expectIntentTransport();
  });

  it('memory store: dispatches set_memory_mode + memory_write for the fixture\'s live freq/mode', () => {
    const ok = makeMemoryHandlers().onStore(5, IC7300_STATE.main!.freqHz, IC7300_STATE.main!.mode);
    expect(ok).toBe(true);
    expect(exactCalls()).toEqual([
      ['set_memory_mode', { channel: 5 }],
      ['memory_write', {}],
    ]);
    expectIntentTransport();
  });

  it('keyboard context: dispatches set_split via dispatchKeyboardRadioAction(toggle_split)', () => {
    expect(IC7300_STATE.split).toBe(false);
    const handled = dispatchKeyboardRadioAction({ action: 'toggle_split' });
    expect(handled).toBe(true);
    expect(exactCalls()).toEqual([['set_split', { on: true }]]);
    expectIntentTransport();
  });

  it('VFO A/B select: dispatches set_vfo for the target slot (no dependency on activeSlot)', () => {
    // `onVfoSelect` never reads `main.activeSlot` — it resolves the receiver
    // structurally (MOR-1423) and only checks the SLOT is reachable
    // (`vfoScheme: 'ab'` supports A/B). Works although activeSlot is
    // unobserved on this fixture.
    makeVfoHandlers().onVfoSelect('MAIN', 'B');
    expect(exactCalls()).toEqual([['set_vfo', { vfo: 'B' }]]);
    expectIntentTransport();
  });

  it('onMainFreqChange: dispatches set_freq on receiver 0', () => {
    makeVfoHandlers().onMainFreqChange(14_205_000);
    expect(exactCalls()).toEqual([['set_freq', { freq: 14_205_000, receiver: 0 }]]);
    expectIntentTransport();
  });

  it('RIT toggle: REFUSES — `ritOn` is genuinely unobserved on this fixture, not fabricated MAIN-bypassed (MOR-1418 scope boundary)', () => {
    // `rit` is a declared capability and the single-RX `active` bypass is
    // in effect, but `onRitToggle` ALSO gates on `knownTopLevelField('ritOn')`
    // — a DIFFERENT field the fix wave never touches. The live IC-7300
    // stand never confirmed ritOn in this session (nobody queried RIT), so
    // this must still refuse: the fix bypasses exactly the `active` gate,
    // nothing else.
    expect(IC7300_CAPABILITIES.capabilities).toContain('rit');
    expect(IC7300_STATE.fieldStatus?.ritOn?.observed).toBe(false);

    makeRitXitHandlers().onRitToggle();

    expect(h.sendCommand).not.toHaveBeenCalled();
    expect(getCommandLifecycles()).toHaveLength(0);
  });
});

/* ── Section 3: honest refusals pinned — genuinely-unobserved leaves on the
 * live fixture must still fail closed. RIT (above) is the first; two more
 * below. Each names the specific field the live radio never confirmed. */

describe('IC-7300 fixture — honest refusals pinned (MOR-988 §3.2 fail-closed)', () => {
  beforeEach(() => {
    h.state = fixtureState();
    h.caps = fixtureCaps();
    h.sendCommand.mockClear();
  });

  afterEach(() => {
    resetCommandLifecycle();
  });

  it('mic gain: REFUSES — micGain is unobserved on the live stand', () => {
    expect(IC7300_STATE.fieldStatus?.micGain?.observed).toBe(false);

    makeTxHandlers().onMicGainChange(100);

    expect(h.sendCommand).not.toHaveBeenCalled();
    expect(getCommandLifecycles()).toHaveLength(0);
  });

  it('NB level: REFUSES — main.nbLevel is unobserved on the live stand (main.nb itself IS observed)', () => {
    expect(IC7300_STATE.fieldStatus?.['main.nbLevel']?.observed).toBe(false);
    // Contrast: the boolean NB toggle IS observed and DOES dispatch (Section 2).
    expect(IC7300_STATE.fieldStatus?.['main.nb']?.observed).toBe(true);

    makeDspHandlers().onNbLevelChange(5);

    expect(h.sendCommand).not.toHaveBeenCalled();
    expect(getCommandLifecycles()).toHaveLength(0);
  });
});
