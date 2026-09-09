/**
 * MOR-2425 / owner ruling R58 (2026-09-09) — `slotsOf` / `forSlot`, the
 * DECK-SLOT counterpart of `receiversOf` / `forReceiver`.
 *
 * Every view model below is built by the REAL adapter (`toRadioViewModel`)
 * from a ServerState/Capabilities pair, never by hand: the thing under test
 * is which positions the adapter's own `vfos` flatMap produces per topology,
 * and a hand-built model would be a restatement of the expectation rather
 * than evidence for it. Each test's doc line names the mutation it kills.
 */
import { describe, expect, it } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';
import { toRadioViewModel } from '$lib/runtime/adapters/radio-view-model-adapter';
import { forSlot, slotsOf } from '../dual-receiver-strips';
import type { RadioViewModel, VfoSlot } from '../../../semantic/radio-view-model';

const fresh: FieldStatus = {
  storePath: 'x', observed: true, freshness: 'fresh', availability: 'available',
};

function caps(over: Partial<Capabilities>): Capabilities {
  return {
    model: 'fixture', scope: false, audio: true, tx: true,
    capabilities: ['audio', 'tx'],
    receivers: 1, vfoScheme: 'ab',
    freqRanges: [], modes: [], filters: [],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
    webrtc: { available: false, enabled: false },
    txBands: [], scopeSource: null, audioFftAvailable: false,
    ...over,
  } as unknown as Capabilities;
}

const dualCaps = (over: Partial<Capabilities>) =>
  caps({ receivers: 2, capabilities: ['audio', 'tx', 'dual_rx'], ...over });

function state(payload: Record<string, unknown>, paths: readonly string[]): ServerState {
  return {
    split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'unknown', reason: 'not-observed' },
    ...payload,
    fieldStatus: Object.fromEntries(paths.map((path) => [path, fresh])),
  } as unknown as ServerState;
}

const leaf = (freqHz: number) => ({ freqHz, mode: 'USB', filterNum: 1, dataMode: 0 });
const rx = (freqHz: number) => ({ freqHz, mode: 'USB', filter: 1, dataMode: 0 });
const leafPaths = (base: string) => [`${base}.freqHz`, `${base}.mode`, `${base}.filterNum`];
const rxPaths = (base: string) => [`${base}.freqHz`, `${base}.mode`, `${base}.filter`];

function view(s: ServerState, c: Capabilities): RadioViewModel {
  const model = toRadioViewModel(s, c);
  expect(model).not.toBeNull();
  return model!;
}

/** 1/single — one receiver, one unslotted VFO. */
const single = () => view(
  state({ active: 'MAIN', main: rx(14250000) }, ['active', ...rxPaths('main')]),
  caps({ vfoScheme: 'single' }),
);

/** 1/ab, IC-7300 shaped: `activeSlot` never observed and the readback is
 *  relative, so the adapter emits selected + unselected RELATIVE positions. */
const abRelative = () => view(
  state(
    { active: 'MAIN', main: { ...rx(14250000), unselectedVfo: leaf(14300000) } },
    ['active', ...rxPaths('main'), ...leafPaths('main.unselectedVfo')],
  ),
  caps({ vfoScheme: 'ab', vfoReadback: 'selected_unselected' }),
);

/** 1/ab with the slot view observed and B selected: two SLOTTED positions,
 *  emitted by the adapter in declaration order A, B. */
const abSlottedActiveB = () => view(
  state(
    {
      active: 'MAIN',
      main: { ...rx(14250000), activeSlot: 'B', vfoA: leaf(14250000), vfoB: leaf(14300000) },
    },
    [
      'active', ...rxPaths('main'), 'main.activeSlot',
      ...leafPaths('main.vfoA'), ...leafPaths('main.vfoB'),
    ],
  ),
  caps({ vfoScheme: 'ab' }),
);

/** 1/ab whose slot B payload is absent: ONE position of unknown identity. */
const abSlotUnknown = () => view(
  state(
    { active: 'MAIN', main: { ...rx(14250000), activeSlot: 'A', vfoA: leaf(14250000) } },
    ['active', ...rxPaths('main'), 'main.activeSlot', ...leafPaths('main.vfoA')],
  ),
  caps({ vfoScheme: 'ab' }),
);

/** 2/ab_shared, FTX-1 shaped: one unslotted VFO per receiver, SUB active. */
const abShared = () => view(
  state(
    { active: 'SUB', main: rx(14250000), sub: rx(7100000) },
    ['active', ...rxPaths('main'), ...rxPaths('sub')],
  ),
  dualCaps({ vfoScheme: 'ab_shared' }),
);

/** 2/main_sub: A/B slots on BOTH receivers — four `vfos` entries. */
const mainSub = () => view(
  state(
    {
      active: 'MAIN',
      main: { ...rx(14250000), activeSlot: 'A', vfoA: leaf(14250000), vfoB: leaf(14280000) },
      sub: { ...rx(7100000), activeSlot: 'A', vfoA: leaf(7100000), vfoB: leaf(7150000) },
    },
    [
      'active', ...rxPaths('main'), ...rxPaths('sub'), 'main.activeSlot', 'sub.activeSlot',
      ...leafPaths('main.vfoA'), ...leafPaths('main.vfoB'),
      ...leafPaths('sub.vfoA'), ...leafPaths('sub.vfoB'),
    ],
  ),
  dualCaps({ vfoScheme: 'main_sub' }),
);

const identity = (slot: VfoSlot): string =>
  slot.kind === 'slotted' ? slot.id : slot.kind === 'relative' ? slot.role : slot.kind;

/** What each deck slot is: its receiver, the `vfos` positions it holds (by
 *  slot identity, read back through `forSlot`), and whether it owns its
 *  receiver's instruments. */
const deck = (model: RadioViewModel) => slotsOf(model).map((slot) => ({
  key: slot.key,
  receiver: slot.receiver,
  holds: forSlot(model, slot).vfos.map((vfo) => identity(vfo.slot)),
  owns: slot.ownsReceiverInstruments,
}));

describe('what the adapter actually produces, per topology', () => {
  // The premise every expectation below rests on. Kills: a fixture that
  // silently stops exercising the branch it was written for — a 1/ab state
  // that resolves to slotted positions instead of relative ones, say.
  it.each([
    ['1/single', single, [['MAIN', 'unslotted']]],
    ['1/ab relative', abRelative, [['MAIN', 'selected'], ['MAIN', 'unselected']]],
    ['1/ab slotted', abSlottedActiveB, [['MAIN', 'A'], ['MAIN', 'B']]],
    ['1/ab slot unknown', abSlotUnknown, [['MAIN', 'unknown']]],
    ['2/ab_shared', abShared, [['MAIN', 'unslotted'], ['SUB', 'unslotted']]],
    ['2/main_sub', mainSub,
      [['MAIN', 'A'], ['MAIN', 'B'], ['SUB', 'A'], ['SUB', 'B']]],
  ])('%s', (_name, build, expected) => {
    expect((build as () => RadioViewModel)().vfos.map((vfo) => [vfo.receiver, identity(vfo.slot)]))
      .toEqual(expected);
  });
});

describe('slotsOf', () => {
  // R58: on a one-receiver radio the second deck slot is the unselected VFO.
  // Kills: slicing by receiver (`receiversOf`) — which yields ONE slot here,
  // the defect this ticket exists to remove.
  it('gives a 1/ab radio two slots: the selected VFO, then the unselected one', () => {
    expect(deck(abRelative())).toEqual([
      { key: 'MAIN-selected', receiver: 'MAIN', holds: ['selected'], owns: true },
      { key: 'MAIN-unselected', receiver: 'MAIN', holds: ['unselected'], owns: false },
    ]);
  });

  // R58: the left column is the ACTIVE slot. Kills: emitting the adapter's
  // declaration order A, B when B is the observed selected slot.
  it('puts the observed active slot first on a slotted single-receiver radio', () => {
    expect(deck(abSlottedActiveB())).toEqual([
      { key: 'MAIN-B', receiver: 'MAIN', holds: ['B'], owns: true },
      { key: 'MAIN-A', receiver: 'MAIN', holds: ['A'], owns: false },
    ]);
  });

  // R58: on a two-receiver radio the second slot IS the SUB receiver, whose
  // own instruments it keeps. Kills: reordering the two receivers behind the
  // active one, which would move SUB into the left column here (this fixture
  // observes SUB as active) and out of the deck's `rx-sub` area.
  it('gives a 2/ab_shared radio one slot per receiver, MAIN then SUB', () => {
    expect(deck(abShared())).toEqual([
      { key: 'MAIN', receiver: 'MAIN', holds: ['unslotted'], owns: true },
      { key: 'SUB', receiver: 'SUB', holds: ['unslotted'], owns: true },
    ]);
  });

  // Kills: one slot per `vfos` entry on a two-receiver radio, which would
  // give 2/main_sub four columns for a two-column deck.
  it('gives a 2/main_sub radio two slots, each holding its receiver\'s A and B', () => {
    expect(deck(mainSub())).toEqual([
      { key: 'MAIN', receiver: 'MAIN', holds: ['A', 'B'], owns: true },
      { key: 'SUB', receiver: 'SUB', holds: ['A', 'B'], owns: true },
    ]);
  });

  // R58 art direction: a never-produced fact is not drawn. Kills: padding a
  // one-VFO radio out to two columns with an empty second slot.
  it.each([
    ['1/single', single],
    ['1/ab whose slot identity was never observed', abSlotUnknown],
  ])('never fabricates a second slot for %s', (_name, build) => {
    expect(slotsOf((build as () => RadioViewModel)())).toHaveLength(1);
  });

  it('reports no slots for an empty vfos array', () => {
    expect(slotsOf({ ...mainSub(), vfos: [] })).toEqual([]);
  });
});

describe('forSlot', () => {
  // Kills: slicing by receiver instead of by slot — both MAIN positions
  // would then land in every MAIN column.
  it('keeps only the entries its slot names', () => {
    const model = abRelative();
    const [primary, secondary] = slotsOf(model);
    expect(forSlot(model, primary).vfos).toHaveLength(1);
    expect(forSlot(model, primary).vfos[0].slot).toEqual({ kind: 'relative', role: 'selected' });
    expect(forSlot(model, secondary).vfos[0].slot).toEqual({ kind: 'relative', role: 'unselected' });
  });

  // R58: the 7300's second slot is not a receiver, so it carries no receiver
  // instruments. `VfoSurface` reads `receiverIndicators`, and its
  // `indicatorReceiver` prop cannot express "none" — an omitted prop means
  // "every indicator" there (`semantic/VfoSurface.svelte`, the
  // `receiverIndicators` $derived) — so the SLICE is what withholds them.
  // Kills: passing the receiver's indicators through to every slot.
  it('withholds the receiver indicators from a slot that does not own them', () => {
    const model = abRelative();
    const [primary, secondary] = slotsOf(model);
    expect(model.receiverIndicators?.map((item) => item.receiver)).toEqual(['MAIN']);
    expect(forSlot(model, primary).receiverIndicators?.map((item) => item.receiver))
      .toEqual(['MAIN']);
    expect(forSlot(model, secondary).receiverIndicators).toEqual([]);
  });

  // Kills: the SUB receiver losing its own S-meter row when the deck strips
  // by slot — the FTX-1 half of R58.
  it('keeps each receiver\'s own indicators on a two-receiver deck', () => {
    const model = abShared();
    for (const slot of slotsOf(model)) {
      expect(forSlot(model, slot).receiverIndicators?.map((item) => item.receiver))
        .toEqual([slot.receiver]);
    }
  });

  // Kills: the slice also touching shared/global facts instead of passing
  // them through verbatim — the same invariant `forReceiver` is held to.
  it('leaves every field other than vfos and receiverIndicators identical', () => {
    const model = mainSub();
    const sliced = forSlot(model, slotsOf(model)[0]);
    const strip = (m: RadioViewModel) => {
      const { vfos: _vfos, receiverIndicators: _indicators, ...rest } = m;
      return rest;
    };
    expect(strip(sliced)).toEqual(strip(model));
  });
});
