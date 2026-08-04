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
import type {
  Availability, MeterField, MeterRfState, MetersViewModel,
  RadioViewModel, TxAuxField, TxAuxViewModel,
} from '../radio-view-model';

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

/**
 * Applies a fully-capable `txAux` group (MOR-1244) to any topology fixture —
 * every control structurally present and operationally observed. Composable
 * the same way `withAudioOnlyScope` is: TX-adjacent capability is an axis
 * orthogonal to VFO topology, not a property of any one scheme, and "only
 * where the radio genuinely has the capability" (governing scope) is left to
 * the caller — this fixture models a radio that has all of them, it does not
 * force txAux onto the four base topologies.
 */
export function withTxAux(fixture: RadioViewModel): RadioViewModel {
  const avail: Availability = { structural: true, operational: true };
  const known = <T>(value: T): TxAuxField<T> => ({ reading: { status: 'known', value }, availability: avail });
  const txAux: TxAuxViewModel = {
    atu: known('off'),
    vox: known(false),
    voxGain: known(50),
    antiVoxGain: known(30),
    voxDelay: known(20),
    compressor: known(false),
    compressorLevel: known(10),
    monitor: known(false),
    monitorLevel: known(128),
    rfPower: known(0.8),
    micGain: known(128),
    driveGain: known(128),
  };
  return { ...fixture, txAux };
}

/**
 * Applies a fully-observed `meters` group (MOR-1262 slice 2A) to any topology
 * fixture, read against the RF state the caller names — meters are an axis
 * orthogonal to VFO topology, same as `withTxAux` above. `rfState` is a
 * PARAMETER rather than a constant precisely because TX relevance is the
 * property under test: a fixture that hard-coded 'receiving' would make the
 * MOR-1235 disagreement unobservable.
 */
export function withMeters(
  fixture: RadioViewModel, rfState: MeterRfState = 'receiving',
): RadioViewModel {
  const avail: Availability = { structural: true, operational: true };
  const tx = rfState !== 'receiving';
  const field = (value: number, relevant: boolean): MeterField =>
    ({ reading: { status: 'known', value }, availability: avail, relevant });
  const meters: MetersViewModel = {
    rfState,
    signal: field(120, !tx),
    power: field(0.6, tx),
    swr: field(20, tx),
    alc: field(40, tx),
    compression: field(10, tx),
    drainVoltage: field(200, true),
    drainCurrent: field(80, tx),
  };
  return { ...fixture, meters };
}
