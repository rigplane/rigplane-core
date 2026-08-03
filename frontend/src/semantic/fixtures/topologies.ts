/**
 * The four canonical topology fixtures (MOR-1062).
 *
 * Each is a complete, valid `RadioViewModel` instance for one of the
 * backend's four VFO schemes (`single | ab | ab_shared | main_sub`, see
 * `$lib/types/capabilities::VfoScheme`), paired with the structural receiver
 * count that scheme implies. MOR-1085's browser fixture matrix consumes
 * these by `topologyId`. Test assets — excluded from the MOR-1062 200-LOC
 * production guard (see the ticket's "Size guard" note).
 *
 * `withAudioOnlyScope` (review cycle 1, S1) is a composable scope-variant
 * helper: audio-only scope is a fifth, orthogonal condition in MOR-1085's
 * matrix ("4 topology pairs + audio-only scope"), not a property of any one
 * topology, so it can be applied to any of the four fixtures below.
 */
import type { RadioViewModel } from '../radio-view-model';

const singleReceiver: RadioViewModel = {
  topologyId: '1/single',
  vfoScheme: 'single',
  activeReceiver: { status: 'known', receiver: 'MAIN' },
  vfos: [
    {
      receiver: 'MAIN', slot: { kind: 'unslotted' }, label: 'MAIN', frequencyHz: 14195000,
      mode: 'USB', filter: 'WIDE', isActive: true, isTxTarget: true,
    },
  ],
  split: { status: 'known', value: false },
  dualWatch: { status: 'known', value: false },
  txTarget: {
    status: 'known', receiver: 'MAIN', slot: { kind: 'unslotted' }, frequencyHz: 14195000,
  },
  txPermit: { status: 'allowed', band: '20m' },
  scope: {
    hardwareScope: { structural: true, operational: true },
    audioFftScope: { structural: false, operational: false },
  },
  disabledReasons: [],
};

const singleAb: RadioViewModel = {
  topologyId: '1/ab',
  vfoScheme: 'ab',
  activeReceiver: { status: 'known', receiver: 'MAIN' },
  vfos: [
    {
      receiver: 'MAIN', slot: { kind: 'slotted', id: 'A' }, label: 'VFO A', frequencyHz: 7100000,
      mode: 'LSB', filter: 'NARROW', isActive: true, isTxTarget: false,
    },
    {
      receiver: 'MAIN', slot: { kind: 'slotted', id: 'B' }, label: 'VFO B', frequencyHz: 7150000,
      mode: 'LSB', filter: 'NARROW', isActive: false, isTxTarget: false,
    },
  ],
  // dualWatch deliberately unknown: this fixture models an early/partial
  // observation window, same story as its unobserved txTarget below.
  split: { status: 'known', value: true },
  dualWatch: { status: 'unknown' },
  txTarget: { status: 'unknown', reason: 'not-observed' },
  txPermit: { status: 'unknown', reason: 'tx-target-unknown' },
  scope: {
    hardwareScope: { structural: false, operational: false },
    audioFftScope: { structural: false, operational: false },
  },
  disabledReasons: [{ field: 'txTarget', code: 'field-not-observed' }],
};

const dualAbShared: RadioViewModel = {
  topologyId: '2/ab_shared',
  vfoScheme: 'ab_shared',
  activeReceiver: { status: 'known', receiver: 'SUB' },
  vfos: [
    {
      receiver: 'MAIN', slot: { kind: 'unslotted' }, label: 'MAIN', frequencyHz: 3573000,
      mode: 'CW', filter: 'NARROW', isActive: false, isTxTarget: false,
    },
    {
      receiver: 'SUB', slot: { kind: 'unslotted' }, label: 'SUB', frequencyHz: 3573000,
      mode: 'CW', filter: 'NARROW', isActive: true, isTxTarget: true,
    },
  ],
  split: { status: 'known', value: false },
  dualWatch: { status: 'known', value: true },
  txTarget: {
    status: 'known', receiver: 'SUB', slot: { kind: 'unslotted' }, frequencyHz: 3573000,
  },
  txPermit: { status: 'denied', reason: 'outside-configured-ranges' },
  scope: {
    hardwareScope: { structural: true, operational: false },
    audioFftScope: { structural: false, operational: false },
  },
  disabledReasons: [{ field: 'scope.hardwareScope', code: 'field-not-observed' }],
};

const dualMainSub: RadioViewModel = {
  topologyId: '2/main_sub',
  vfoScheme: 'main_sub',
  activeReceiver: { status: 'known', receiver: 'MAIN' },
  vfos: [
    {
      receiver: 'MAIN', slot: { kind: 'slotted', id: 'A' }, label: 'M-A', frequencyHz: 14250000,
      mode: 'USB', filter: 'WIDE', isActive: true, isTxTarget: true,
    },
    {
      receiver: 'MAIN', slot: { kind: 'slotted', id: 'B' }, label: 'M-B', frequencyHz: 14280000,
      mode: 'USB', filter: 'WIDE', isActive: false, isTxTarget: false,
    },
    {
      receiver: 'SUB', slot: { kind: 'slotted', id: 'A' }, label: 'S-A', frequencyHz: 21295000,
      mode: 'USB', filter: 'WIDE', isActive: false, isTxTarget: false,
    },
    {
      receiver: 'SUB', slot: { kind: 'slotted', id: 'B' }, label: 'S-B', frequencyHz: 21330000,
      mode: 'USB', filter: 'WIDE', isActive: false, isTxTarget: false,
    },
  ],
  // Both true at once, proving the orthogonal split/dualWatch representation
  // (review cycle 1, B2) actually admits the combination the old enum couldn't.
  split: { status: 'known', value: true },
  dualWatch: { status: 'known', value: true },
  txTarget: {
    status: 'known', receiver: 'MAIN', slot: { kind: 'slotted', id: 'A' }, frequencyHz: 14250000,
  },
  txPermit: { status: 'allowed', band: '20m' },
  scope: {
    hardwareScope: { structural: true, operational: true },
    audioFftScope: { structural: true, operational: false },
  },
  disabledReasons: [{ field: 'scope.audioFftScope', code: 'capability-unavailable' }],
};

export const topologyFixtures = {
  '1/single': singleReceiver,
  '1/ab': singleAb,
  '2/ab_shared': dualAbShared,
  '2/main_sub': dualMainSub,
} as const satisfies Record<string, RadioViewModel>;

export type TopologyFixtureId = keyof typeof topologyFixtures;

/**
 * Applies the "audio-only scope" condition (hardware scope absent, audio FFT
 * present and live) to any topology fixture, leaving every other fact
 * untouched. MOR-1085's browser matrix composes this onto whichever of the
 * four canonical topologies it needs — it is not baked into one fixture.
 */
export function withAudioOnlyScope(fixture: RadioViewModel): RadioViewModel {
  return {
    ...fixture,
    scope: {
      hardwareScope: { structural: false, operational: false },
      audioFftScope: { structural: true, operational: true },
    },
  };
}
