/**
 * MOR-1243: RadioViewModel → RendererViewModel projection.
 *
 * Four requirement groups from the ticket's Outcome, one `describe` each:
 *   1. fixture-driven coverage — all four MOR-1062 topology fixtures plus a
 *      withAudioOnlyScope variant project to output the REAL
 *      `isRendererViewModel` gate (not a copy) accepts.
 *   2. unknownness preserved — every unknown/not-observed branch surfaces as
 *      the literal `'unknown'` primitive, never a fabricated look-alike
 *      default. Each test isolates exactly one field so a "collapse
 *      unknown→default" mutation in `projection.ts` fails only that test.
 *   3. capability objects and module paths cannot pass through — the
 *      projection itself is the mechanism (strips what it never reads,
 *      throws on what it does read but cannot coerce to a primitive), not
 *      just a input the gate happens to reject.
 *   4. determinism / purity — same input → deep-equal output, no input
 *      mutation, and the module imports only the two contracts' types.
 */
import { describe, it, expect } from 'vitest';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { readFileSync } from 'node:fs';
import { projectRadioViewModel, ProjectionError } from '../projection';
import { isRendererViewModel } from '../contract';
import { topologyFixtures, withAudioOnlyScope, type TopologyFixtureId } from '../../../semantic/fixtures/topologies';
import type { RadioViewModel } from '../../../semantic/radio-view-model';

const TOPOLOGY_IDS: readonly TopologyFixtureId[] = ['1/single', '1/ab', '2/ab_shared', '2/main_sub'];

describe('fixture-driven coverage: validator-clean input produces gate-clean output', () => {
  it.each(TOPOLOGY_IDS)('projects topology %s through the REAL isRendererViewModel gate', (id) => {
    const output = projectRadioViewModel(topologyFixtures[id]);
    expect(isRendererViewModel(output)).toBe(true);
  });

  it.each(TOPOLOGY_IDS)('projects the withAudioOnlyScope variant of %s through the REAL gate too', (id) => {
    const output = projectRadioViewModel(withAudioOnlyScope(topologyFixtures[id]));
    expect(isRendererViewModel(output)).toBe(true);
  });
});

describe('unknownness preserved: every unknown branch is an explicit primitive, never a fabricated default', () => {
  const base = topologyFixtures['1/single'];

  it('activeReceiver: unknown surfaces as "unknown", never a receiver id like "MAIN"', () => {
    const model: RadioViewModel = { ...base, activeReceiver: { status: 'unknown' } };
    expect(projectRadioViewModel(model).fields.activeReceiver).toBe('unknown');
  });

  it('a VFO slot: unknown surfaces as "unknown", never a fabricated slot id like "A"', () => {
    const model: RadioViewModel = { ...base, vfos: [{ ...base.vfos[0], slot: { kind: 'unknown' } }] };
    expect(projectRadioViewModel(model).fields.vfo0Slot).toBe('unknown');
  });

  it('split: unknown surfaces as "unknown", never collapses to a boolean default', () => {
    const model: RadioViewModel = { ...base, split: { status: 'unknown' } };
    expect(projectRadioViewModel(model).fields.split).toBe('unknown');
  });

  it('dualWatch: unknown surfaces as "unknown", never collapses to a boolean default', () => {
    const model: RadioViewModel = { ...base, dualWatch: { status: 'unknown' } };
    expect(projectRadioViewModel(model).fields.dualWatch).toBe('unknown');
  });

  it('txTarget: unknown surfaces its own status and does not fabricate a receiver, slot, or frequency', () => {
    const model: RadioViewModel = {
      ...base,
      txTarget: { status: 'unknown', reason: 'stale' },
      txPermit: { status: 'unknown', reason: 'tx-target-unknown' },
      vfos: [{ ...base.vfos[0], isTxTarget: false }],
    };
    const { fields } = projectRadioViewModel(model);
    expect(fields.txTargetStatus).toBe('unknown');
    expect(fields.txTargetReceiver).toBe('unknown');
    expect(fields.txTargetSlot).toBe('unknown');
    expect(fields.txTargetFrequencyHz).toBe(null);
    expect(fields.txTargetUnknownReason).toBe('stale');
  });

  it('txPermit: the tri-state "unknown" status passes through, never silently becomes "allowed" or "denied"', () => {
    const model: RadioViewModel = {
      ...base,
      txTarget: { status: 'unknown', reason: 'not-observed' },
      txPermit: { status: 'unknown', reason: 'ranges-unconfigured' },
      vfos: [{ ...base.vfos[0], isTxTarget: false }],
    };
    expect(projectRadioViewModel(model).fields.txPermitStatus).toBe('unknown');
  });
});

// Review cycle 1: the blocks above pin "unknown never becomes a default" but
// nothing pinned the other direction (known → 'unknown', or known values
// silently corrupted/dropped) — {kind:'radio', fields:{}} satisfies every
// isRendererViewModel-only assertion above. Exact `toEqual` field maps below
// close that gap; each was hand-derived from the fixture source in
// `../../../semantic/fixtures/topologies` (not captured from the function
// under test) and cross-checked against the independent reviewer's dumps for
// `1/single` and `2/main_sub`.
describe('known-value pinning: exact field maps (review cycle 1)', () => {
  it('1/single projects the full, exact field map', () => {
    expect(projectRadioViewModel(topologyFixtures['1/single']).fields).toEqual({
      topologyId: '1/single', vfoScheme: 'single', activeReceiver: 'MAIN', split: false, dualWatch: false,
      vfoCount: 1, disabledReasonsCount: 0,
      vfo0Receiver: 'MAIN', vfo0Slot: 'unslotted', vfo0Label: 'MAIN', vfo0FrequencyHz: 14195000,
      vfo0Mode: 'USB', vfo0Filter: 'WIDE', vfo0Active: true, vfo0TxTarget: true,
      txTargetStatus: 'known', txTargetReceiver: 'MAIN', txTargetSlot: 'unslotted', txTargetFrequencyHz: 14195000,
      txTargetUnknownReason: null, txPermitStatus: 'allowed', txPermitBand: '20m', txPermitReason: null,
      hardwareScopeStructural: true, hardwareScopeOperational: true,
      audioFftScopeStructural: false, audioFftScopeOperational: false,
    });
  });

  it('1/ab projects the full, exact field map (unknown dualWatch/txTarget/txPermit alongside known split)', () => {
    expect(projectRadioViewModel(topologyFixtures['1/ab']).fields).toEqual({
      topologyId: '1/ab', vfoScheme: 'ab', activeReceiver: 'MAIN', split: true, dualWatch: 'unknown',
      vfoCount: 2, disabledReasonsCount: 1,
      vfo0Receiver: 'MAIN', vfo0Slot: 'A', vfo0Label: 'VFO A', vfo0FrequencyHz: 7100000,
      vfo0Mode: 'LSB', vfo0Filter: 'NARROW', vfo0Active: true, vfo0TxTarget: false,
      vfo1Receiver: 'MAIN', vfo1Slot: 'B', vfo1Label: 'VFO B', vfo1FrequencyHz: 7150000,
      vfo1Mode: 'LSB', vfo1Filter: 'NARROW', vfo1Active: false, vfo1TxTarget: false,
      txTargetStatus: 'unknown', txTargetReceiver: 'unknown', txTargetSlot: 'unknown', txTargetFrequencyHz: null,
      txTargetUnknownReason: 'not-observed',
      txPermitStatus: 'unknown', txPermitBand: null, txPermitReason: 'tx-target-unknown',
      hardwareScopeStructural: false, hardwareScopeOperational: false,
      audioFftScopeStructural: false, audioFftScopeOperational: false,
      disabledReason0Field: 'txTarget', disabledReason0Code: 'field-not-observed',
    });
  });

  it('2/ab_shared projects the full, exact field map (denied permit, active SUB receiver)', () => {
    expect(projectRadioViewModel(topologyFixtures['2/ab_shared']).fields).toEqual({
      topologyId: '2/ab_shared', vfoScheme: 'ab_shared', activeReceiver: 'SUB', split: false, dualWatch: true,
      vfoCount: 2, disabledReasonsCount: 1,
      vfo0Receiver: 'MAIN', vfo0Slot: 'unslotted', vfo0Label: 'MAIN', vfo0FrequencyHz: 3573000,
      vfo0Mode: 'CW', vfo0Filter: 'NARROW', vfo0Active: false, vfo0TxTarget: false,
      vfo1Receiver: 'SUB', vfo1Slot: 'unslotted', vfo1Label: 'SUB', vfo1FrequencyHz: 3573000,
      vfo1Mode: 'CW', vfo1Filter: 'NARROW', vfo1Active: true, vfo1TxTarget: true,
      txTargetStatus: 'known', txTargetReceiver: 'SUB', txTargetSlot: 'unslotted', txTargetFrequencyHz: 3573000,
      txTargetUnknownReason: null,
      txPermitStatus: 'denied', txPermitBand: null, txPermitReason: 'outside-configured-ranges',
      hardwareScopeStructural: true, hardwareScopeOperational: false,
      audioFftScopeStructural: false, audioFftScopeOperational: false,
      disabledReason0Field: 'scope.hardwareScope', disabledReason0Code: 'field-not-observed',
    });
  });

  it('2/main_sub projects the full, exact field map (4 VFOs, both split and dualWatch true)', () => {
    expect(projectRadioViewModel(topologyFixtures['2/main_sub']).fields).toEqual({
      topologyId: '2/main_sub', vfoScheme: 'main_sub', activeReceiver: 'MAIN', split: true, dualWatch: true,
      vfoCount: 4, disabledReasonsCount: 1,
      vfo0Receiver: 'MAIN', vfo0Slot: 'A', vfo0Label: 'M-A', vfo0FrequencyHz: 14250000,
      vfo0Mode: 'USB', vfo0Filter: 'WIDE', vfo0Active: true, vfo0TxTarget: true,
      vfo1Receiver: 'MAIN', vfo1Slot: 'B', vfo1Label: 'M-B', vfo1FrequencyHz: 14280000,
      vfo1Mode: 'USB', vfo1Filter: 'WIDE', vfo1Active: false, vfo1TxTarget: false,
      vfo2Receiver: 'SUB', vfo2Slot: 'A', vfo2Label: 'S-A', vfo2FrequencyHz: 21295000,
      vfo2Mode: 'USB', vfo2Filter: 'WIDE', vfo2Active: false, vfo2TxTarget: false,
      vfo3Receiver: 'SUB', vfo3Slot: 'B', vfo3Label: 'S-B', vfo3FrequencyHz: 21330000,
      vfo3Mode: 'USB', vfo3Filter: 'WIDE', vfo3Active: false, vfo3TxTarget: false,
      txTargetStatus: 'known', txTargetReceiver: 'MAIN', txTargetSlot: 'A', txTargetFrequencyHz: 14250000,
      txTargetUnknownReason: null,
      txPermitStatus: 'allowed', txPermitBand: '20m', txPermitReason: null,
      hardwareScopeStructural: true, hardwareScopeOperational: true,
      audioFftScopeStructural: true, audioFftScopeOperational: false,
      disabledReason0Field: 'scope.audioFftScope', disabledReason0Code: 'capability-unavailable',
    });
  });

  it('withAudioOnlyScope(1/single) projects the exact field map with only the scope block flipped', () => {
    expect(projectRadioViewModel(withAudioOnlyScope(topologyFixtures['1/single'])).fields).toEqual({
      topologyId: '1/single', vfoScheme: 'single', activeReceiver: 'MAIN', split: false, dualWatch: false,
      vfoCount: 1, disabledReasonsCount: 0,
      vfo0Receiver: 'MAIN', vfo0Slot: 'unslotted', vfo0Label: 'MAIN', vfo0FrequencyHz: 14195000,
      vfo0Mode: 'USB', vfo0Filter: 'WIDE', vfo0Active: true, vfo0TxTarget: true,
      txTargetStatus: 'known', txTargetReceiver: 'MAIN', txTargetSlot: 'unslotted', txTargetFrequencyHz: 14195000,
      txTargetUnknownReason: null, txPermitStatus: 'allowed', txPermitBand: '20m', txPermitReason: null,
      hardwareScopeStructural: false, hardwareScopeOperational: false,
      audioFftScopeStructural: true, audioFftScopeOperational: true,
    });
  });

  // Contract-legal (validateRadioViewModel accepts it) but no fixture exercises it: a
  // KNOWN txTarget whose slot is itself {kind:'unknown'} — review cycle 1, F1.
  it('a KNOWN txTarget whose slot is {kind:"unknown"} projects the slot as "unknown", not a fabricated id (F1)', () => {
    const base = topologyFixtures['1/single'];
    const model: RadioViewModel = {
      ...base,
      txTarget: { status: 'known', receiver: 'MAIN', slot: { kind: 'unknown' }, frequencyHz: 14195000 },
    };
    const { fields } = projectRadioViewModel(model);
    expect(fields.txTargetStatus).toBe('known');
    expect(fields.txTargetSlot).toBe('unknown');
  });
});

describe('capability objects and module paths cannot pass through (the projection is the mechanism)', () => {
  it('an extra top-level key never read by the projection is dropped, not copied into fields — output stays gate-clean', () => {
    const smuggled = {
      ...topologyFixtures['1/single'],
      capabilities: { modes: ['USB', 'LSB', 'CW'], model: 'IC-7610' },
    } as unknown as RadioViewModel;
    const output = projectRadioViewModel(smuggled);
    expect(Object.keys(output.fields)).not.toContain('capabilities');
    expect(JSON.stringify(output)).not.toContain('IC-7610');
    expect(isRendererViewModel(output)).toBe(true);
  });

  it('a capability object smuggled into a primitive-typed slot (VFO mode) throws ProjectionError instead of forwarding it', () => {
    const base = topologyFixtures['1/single'];
    const capabilityShaped = { modes: ['USB', 'LSB'], model: 'IC-7610' };
    const smuggled: RadioViewModel = {
      ...base,
      vfos: [{ ...base.vfos[0], mode: capabilityShaped as unknown as string }],
    };
    expect(() => projectRadioViewModel(smuggled)).toThrow(ProjectionError);
  });

  it('a live module reference (e.g. a component constructor) in a primitive-typed slot throws, never passes through as inert data', () => {
    const base = topologyFixtures['1/single'];
    function FakeSkinComponent(): void {
      /* stands in for an imported component/module reference */
    }
    const smuggled: RadioViewModel = {
      ...base,
      vfos: [{ ...base.vfos[0], filter: FakeSkinComponent as unknown as string }],
    };
    expect(() => projectRadioViewModel(smuggled)).toThrow(ProjectionError);
  });
});

describe('determinism / purity', () => {
  it('same input produces a deep-equal output on repeated calls', () => {
    const model = topologyFixtures['2/main_sub'];
    expect(projectRadioViewModel(model)).toEqual(projectRadioViewModel(model));
  });

  it('does not mutate its input', () => {
    const model = topologyFixtures['2/main_sub'];
    const snapshot = JSON.parse(JSON.stringify(model));
    projectRadioViewModel(model);
    expect(model).toEqual(snapshot);
  });

  it('imports only types, and only from the two contracts — no runtime coupling beyond the seam', () => {
    const source = readFileSync(path.resolve(fileURLToPath(import.meta.url), '../../projection.ts'), 'utf-8');
    const importLines = source.split('\n').filter((line) => /^\s*import\b/.test(line));
    expect(importLines.length).toBeGreaterThan(0);
    for (const line of importLines) {
      expect(line).toMatch(/^\s*import type /);
      expect(line).toMatch(/from '(\.\.\/\.\.\/semantic\/radio-view-model|\.\/contract)';?\s*$/);
    }
  });
});
