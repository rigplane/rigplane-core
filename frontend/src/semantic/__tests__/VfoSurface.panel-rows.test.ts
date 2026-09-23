/**
 * MOR-2509 slice 2 — structural pins for the Standard-skin VFO panel
 * "screen": four fixed rows per panel (tray, receiver, main, under) plus the
 * DSP chip group, the tray tabs, the annunciator lamps, and the
 * under-frequency RIT/XIT/SPLIT chips.
 *
 * jsdom cannot measure pixels, so geometry is pinned the way the repo's
 * stylesheet tests pin cascades (see
 * `presentation/languages/studioline/__tests__/stylesheet.test.ts`): the DOM
 * tests below pin membership, order and lit/unlit state for the real
 * `VfoSurface` mount, and the source tests pin the fixed-slot width rules,
 * the vertical-rhythm custom property and the container queries that keep
 * those rows stable. Pixel behaviour at the ticket's viewport matrix is
 * measured separately by `tests/e2e/i18n/desktop-geometry.spec.ts`.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { mount, flushSync } from 'svelte';
import type { ComponentProps } from 'svelte';
import type { ReceiverId } from '../radio-view-model';
import {
  validateRadioViewModel, type RadioViewModel,
  type ReceiverIndicatorViewModel,
} from '../radio-view-model';
import VfoSurface from '../VfoSurface.svelte';
import { topologyFixtures, withBand, withTxAux, type TopologyFixtureId } from '../fixtures/topologies';
import { toRadioViewModel } from '$lib/runtime/adapters/radio-view-model-adapter';
import {
  FTX1_CAPABILITIES, FTX1_STATE,
} from '$lib/runtime/adapters/__tests__/fixtures/ftx1-profile';

const indicatorField = <T>(value: T) => ({
  reading: { status: 'known' as const, value },
  availability: { structural: true, operational: true },
});

const unknownField = <T>() => ({
  reading: { status: 'unknown' as const },
  availability: { structural: true, operational: true },
});

const absentAction = { structural: false, operational: false };
const availableAction = { structural: true, operational: true };

function receiverIndicator(
  receiver: ReceiverId,
  readings: 'known' | 'unknown' = 'known',
): ReceiverIndicatorViewModel {
  const field = readings === 'known' ? indicatorField : unknownField;
  return {
    receiver,
    availability: { structural: true, operational: true },
    sMeter: {
      ...field(receiver === 'MAIN' ? 0 : -31),
      source: {
        providerGeneration: 1, scope: 'receiver', receiver,
        path: receiver === 'MAIN' ? 'main.sMeter' : 'sub.sMeter',
      },
    },
    bandwidthHz: field(receiver === 'MAIN' ? 2400 : 500),
    agcMode: field(receiver === 'MAIN' ? 'FAST' : 2),
    nbActive: field(receiver === 'SUB'),
    nrActive: field(receiver === 'MAIN'),
    notchMode: field<'off' | 'auto' | 'manual'>(receiver === 'MAIN' ? 'off' : 'auto'),
    attenuator: field(receiver === 'MAIN' ? 0 : 12),
    preamp: field(receiver === 'MAIN' ? 0 : 2),
    rfGain: field(receiver === 'MAIN' ? 0 : 0.75),
    digiSel: field(receiver === 'SUB'),
    ipPlus: field(receiver === 'MAIN'),
  };
}

/** Fixtures with receiver indicators plus a full radio-wide action block
 *  (single-receiver schemes get no MAIN/SUB selector actions). */
function standardFixture(
  id: TopologyFixtureId, readings: 'known' | 'unknown' = 'known', antennaPorts = 2,
): RadioViewModel {
  const base = topologyFixtures[id];
  const dual = id.startsWith('2/');
  const banded = id === '2/main_sub' ? withBand(base) : base;
  const band = banded.band
    ? {
      band: readings === 'known'
        ? banded.band
        : {
          ...banded.band,
          currentBand: {
            reading: { status: 'unknown' as const },
            availability: banded.band.currentBand.availability,
          },
          // MOR-2526: the unknown axis flips EVERY band reading. In a real
          // payload `currentBand` IS the active receiver's map entry (one
          // object), so a variant flipping one and not the other models a
          // shape the adapter cannot emit.
          receiverBands: {
            main: {
              reading: { status: 'unknown' as const },
              availability: banded.band.receiverBands.main.availability,
            },
            sub: {
              reading: { status: 'unknown' as const },
              availability: banded.band.receiverBands.sub.availability,
            },
          },
          currentBandTx: 'denied' as const,
        },
    }
    : {};
  return validateRadioViewModel({
    ...banded,
    ...band,
    receiverIndicators: (dual ? (['MAIN', 'SUB'] as const) : (['MAIN'] as const))
      .map((receiver) => receiverIndicator(receiver, readings)),
    antenna: {
      txAntenna: readings === 'known' ? indicatorField(1) : unknownField(),
      rxAnt: readings === 'known' ? indicatorField(false) : unknownField(),
      antennaCount: antennaPorts,
    },
    radioWideIndicators: {
      rfState: readings === 'known' ? 'receiving' : 'uncertain',
      antenna: readings === 'known' ? indicatorField(1) : unknownField(),
      atu: indicatorField('off'),
      dialLock: indicatorField(false),
      ritActive: readings === 'known' ? indicatorField(true) : unknownField(),
      ritOffset: readings === 'known' ? indicatorField(120) : unknownField(),
      xitActive: readings === 'known' ? indicatorField(false) : unknownField(),
      xitOffset: indicatorField(0),
      actions: {
        main: dual ? availableAction : absentAction,
        sub: dual ? availableAction : absentAction,
        equalize: availableAction,
        swap: availableAction,
      },
    },
  });
}

let targets: HTMLElement[] = [];

function mountSurface(props: ComponentProps<typeof VfoSurface>): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  targets.push(target);
  mount(VfoSurface, { target, props });
  flushSync();
  return target;
}

beforeEach(() => {
  targets = [];
});

afterEach(() => {
  while (document.body.firstChild) document.body.removeChild(document.body.firstChild);
});

function panels(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>('[data-receiver-instrument]'));
}

function row(panel: Element, name: string): HTMLElement {
  const found = panel.querySelector(`[data-vfo-row="${name}"]`);
  expect(found, `panel has a ${name} row`).not.toBeNull();
  return found as HTMLElement;
}

const FOLLOWS = Node.DOCUMENT_POSITION_FOLLOWING;

function expectBefore(earlier: Element, later: Element, how: string): void {
  expect(earlier.compareDocumentPosition(later) & FOLLOWS, how).toBeTruthy();
}

describe.each(['2/main_sub', '1/ab'] as const)('standard panel rows (%s)', (id) => {
  const fixture = (readings: 'known' | 'unknown' = 'known') => standardFixture(id, readings);
  const dominantMode = id === '2/main_sub' ? 'USB' : 'LSB';
  const dominantFilter = id === '2/main_sub' ? 'WIDE' : 'NARROW';

  it('the frequency readout and the S-meter are descendants of the same main row, frequency first', () => {
    const root = mountSurface({ viewModel: fixture(), appearance: 'standard' });
    expect(panels(root).length).toBeGreaterThan(0);
    for (const panel of panels(root)) {
      const mainRow = row(panel, 'main');
      const freq = mainRow.querySelector('[data-vfo-freq]');
      expect(freq, 'frequency readout inside the main row').not.toBeNull();
      const meter = panel.querySelector('[data-testid="receiver-s-meter"]');
      if (meter !== null) {
        expect(mainRow.contains(meter), 'meter inside the same main row').toBe(true);
        expectBefore(freq!, meter, 'meter after the frequency readout in DOM order');
      }
    }
  });

  it('the four fixed rows appear in order: tray, receiver, main, under', () => {
    const root = mountSurface({ viewModel: fixture(), appearance: 'standard' });
    for (const panel of panels(root)) {
      expectBefore(row(panel, 'tray'), row(panel, 'receiver'), 'tray precedes the receiver row');
      expectBefore(row(panel, 'receiver'), row(panel, 'main'), 'receiver row precedes the main row');
      expectBefore(row(panel, 'main'), row(panel, 'under'), 'main row precedes the under row');
    }
  });

  it('the receiver row carries the name once with mode and filter, and no tag chips', () => {
    const root = mountSurface({ viewModel: fixture(), appearance: 'standard' });
    for (const panel of panels(root)) {
      const receiverRow = row(panel, 'receiver');
      const names = receiverRow.querySelectorAll('.vfo-label');
      expect(names.length, 'the receiver name appears exactly once').toBe(1);
      expect(receiverRow.querySelector('.header-tag')).toBeNull();
      expect(receiverRow.textContent).toContain(dominantMode);
      expect(receiverRow.textContent).toContain(dominantFilter);
    }
    const first = panels(root)[0];
    expect(row(first, 'receiver').querySelector('.mode-badge-wrapper')).not.toBeNull();
  });

  it('the DSP chip group sits inside the main row and carries NB, NR and one NOTCH chip', () => {
    const root = mountSurface({ viewModel: fixture(), appearance: 'standard' });
    for (const panel of panels(root)) {
      const dsp = panel.querySelector('[data-vfo-row="dsp"]');
      expect(dsp, 'panel has a DSP group').not.toBeNull();
      expect(row(panel, 'main').contains(dsp!)).toBe(true);
      const keys = Array.from(dsp!.querySelectorAll('[data-chip]'))
        .map((chip) => chip.getAttribute('data-chip'));
      expect(keys).toEqual(['nb', 'nr', 'notch']);
    }
  });
});

describe('standard tray tabs (2/main_sub)', () => {
  it('BAND and BW tabs are drawn with lit text from known readings and label-only when unknown', () => {
    const root = mountSurface({ viewModel: standardFixture('2/main_sub'), appearance: 'standard' });
    const mainPanel = panels(root).find((panel) => panel.getAttribute('data-receiver-instrument') === 'MAIN')!;
    const band = mainPanel.querySelector('[data-tray-tab="band"]')!;
    expect(band.textContent?.trim()).toBe('20M');
    expect(band.getAttribute('data-lit')).toBe('true');
    expect(mainPanel.querySelector('[data-tray-tab="bw"]')?.textContent?.trim()).toBe('BW 2400');
  });

  // ── MOR-2526 (owner ruling 2026-09-22): the tray band is per receiver ───
  it('the inactive panel\'s tray band shows its OWN receiver\'s band, lit', () => {
    const baseFixture = standardFixture('2/main_sub');
    // SUB parked on 7.100 MHz — inside this fixture's named 40m band — with
    // its own reading known; MAIN stays the active receiver on 20m.
    const viewModel = validateRadioViewModel({
      ...baseFixture,
      vfos: baseFixture.vfos.map(
        (vfo) => vfo.receiver === 'SUB' ? { ...vfo, frequencyHz: 7100000 } : vfo,
      ),
      band: {
        ...baseFixture.band!,
        receiverBands: {
          ...baseFixture.band!.receiverBands,
          sub: {
            reading: { status: 'known', value: '40m' },
            availability: { structural: true, operational: true },
          },
        },
      },
    });
    const root = mountSurface({ viewModel, appearance: 'standard' });
    const subPanel = panels(root).find((panel) => panel.getAttribute('data-receiver-instrument') === 'SUB')!;
    const subBand = subPanel.querySelector('[data-tray-tab="band"]')!;
    expect(subBand.textContent?.trim()).toBe('40M');
    expect(subBand.getAttribute('data-lit')).toBe('true');
    // The active MAIN panel keeps its own 20m reading — two different bands
    // on one radio, which is the whole point of the per-receiver map.
    const mainPanel = panels(root).find((panel) => panel.getAttribute('data-receiver-instrument') === 'MAIN')!;
    expect(mainPanel.querySelector('[data-tray-tab="band"]')?.textContent?.trim()).toBe('20M');
  });

  it('an unread SUB frequency keeps the inactive panel\'s BAND tab unlit in place', () => {
    // withBand's SUB entry is unknown (its SUB sits outside the named bands):
    // the tab keeps its own label, never a placeholder and never MAIN's band.
    const root = mountSurface({ viewModel: standardFixture('2/main_sub'), appearance: 'standard' });
    const subPanel = panels(root).find((panel) => panel.getAttribute('data-receiver-instrument') === 'SUB')!;
    const subBand = subPanel.querySelector('[data-tray-tab="band"]')!;
    expect(subBand.textContent?.trim()).toBe('BAND');
    expect(subBand.getAttribute('data-lit')).toBe('false');
  });

  it('the radio-wide ANT tab is drawn once, on the active receiver\'s panel only', () => {
    const root = mountSurface({ viewModel: standardFixture('2/main_sub'), appearance: 'standard' });
    const antTabs = root.querySelectorAll('[data-tray-tab="ant"]');
    expect(antTabs.length).toBe(1);
    expect(antTabs[0].closest('[data-receiver-instrument]')?.getAttribute('data-receiver-instrument'))
      .toBe('MAIN');
    expect(antTabs[0].getAttribute('data-indicator-fact')).toBe('ant');
    expect(antTabs[0].textContent?.trim()).toBe('ANT 1');
  });

  it('draws no ANT tab on a single-port radio (owner ruling, 2026-09-21)', () => {
    const root = mountSurface({
      viewModel: standardFixture('2/main_sub', 'known', 1), appearance: 'standard',
    });
    expect(root.querySelectorAll('[data-tray-tab="ant"]')).toHaveLength(0);
  });

  it('the ANT tab stays on the active-slot panel of a single-receiver A/B pair', () => {
    const root = mountSurface({ viewModel: standardFixture('1/ab'), appearance: 'standard' });
    const antTabs = root.querySelectorAll('[data-tray-tab="ant"]');
    expect(antTabs.length).toBe(1);
    expect(antTabs[0].closest('[data-standard-vfo-slot]')?.getAttribute('data-standard-vfo-slot'))
      .toBe('A');
  });

  it('tray tab count does not depend on reading values', () => {
    const known = mountSurface({ viewModel: standardFixture('2/main_sub'), appearance: 'standard' });
    const unknown = mountSurface({ viewModel: standardFixture('2/main_sub', 'unknown'), appearance: 'standard' });
    for (const root of [known, unknown]) {
      for (const panel of panels(root)) {
        // ANT is radio-wide, so the active receiver's panel owns 3 tabs and
        // the other 2; what must not change is the count per panel.
        const expected = panel.getAttribute('data-receiver-instrument') === 'MAIN' ? 3 : 2;
        const count = row(panel, 'tray').querySelectorAll('[data-tray-tab]').length;
        expect(count, 'same tab count whatever the readings say').toBe(expected);
      }
    }
    const unknownMain = panels(unknown).find((panel) => panel.getAttribute('data-receiver-instrument') === 'MAIN')!;
    expect(unknownMain.querySelector('[data-tray-tab="band"]')?.getAttribute('data-lit')).toBe('false');
    expect(unknownMain.querySelector('[data-tray-tab="ant"]')?.textContent?.trim()).toBe('ANT');
    expect(unknownMain.querySelector('[data-tray-tab="bw"]')?.textContent?.trim()).toBe('BW');
  });
});

describe('standard annunciator lamps (2/main_sub)', () => {
  it('AGC, P.AMP, ATT, IP+, DIGI-SEL and RFG keep their slots with no placeholder text', () => {
    const root = mountSurface({ viewModel: standardFixture('2/main_sub'), appearance: 'standard' });
    const mainPanel = panels(root).find((panel) => panel.getAttribute('data-receiver-instrument') === 'MAIN')!;
    const receiverRow = row(mainPanel, 'receiver');
    const keys = Array.from(receiverRow.querySelectorAll('.lamp'))
      .map((lamp) => lamp.getAttribute('data-chip'));
    expect(keys).toEqual(['agc', 'preamp', 'att', 'ip-plus', 'digi-sel', 'rfg']);
    const text = receiverRow.textContent ?? '';
    for (const forbidden of ['—', '?', 'null', 'undefined', 'NaN', 'UNKNOWN', 'N/A']) {
      expect(text, `receiver row must not print ${forbidden}`).not.toContain(forbidden);
    }
    expect(receiverRow.querySelector('[data-chip="rfg"]')?.getAttribute('data-indicator-fact')).toBe('rfg');
  });

  it('unknown readings leave every lamp unlit in place with its own label', () => {
    const root = mountSurface({ viewModel: standardFixture('2/main_sub', 'unknown'), appearance: 'standard' });
    const mainPanel = panels(root).find((panel) => panel.getAttribute('data-receiver-instrument') === 'MAIN')!;
    const lamps = row(mainPanel, 'receiver').querySelectorAll('.lamp');
    expect(lamps.length).toBe(6);
    // Owner ruling 2026-09-23: with the gain unread, RFG shows nothing at
    // all — an empty reserved slot — while the other lamps keep their labels.
    const expected: Record<string, string> = {
      agc: 'AGC', preamp: 'P.AMP', att: 'ATT', 'ip-plus': 'IP+', 'digi-sel': 'DIGI-SEL', rfg: '',
    };
    for (const lamp of lamps) {
      expect(lamp.getAttribute('data-lit')).toBe('false');
      expect(lamp.textContent?.trim()).toBe(expected[lamp.getAttribute('data-chip')!]);
    }
    expect(row(mainPanel, 'receiver').querySelector('[data-chip="rfg"]')?.getAttribute('aria-hidden')).toBe('true');
  });

  it('a radio without a front-end field draws no lamp for it (ftx1: no IP+, no DIGI-SEL)', () => {
    const caps = { ...FTX1_CAPABILITIES, providerGeneration: FTX1_STATE.providerGeneration };
    const model = toRadioViewModel(FTX1_STATE, caps);
    expect(model).not.toBeNull();
    const indicator = model!.receiverIndicators!.find((item) => item.receiver === 'MAIN')!;
    expect(indicator.ipPlus.availability.structural).toBe(false);
    expect(indicator.digiSel.availability.structural).toBe(false);
    const root = mountSurface({ viewModel: model!, appearance: 'standard' });
    const mainPanel = panels(root).find((panel) => panel.getAttribute('data-receiver-instrument') === 'MAIN')!;
    expect(mainPanel.querySelector('[data-chip="ip-plus"]')).toBeNull();
    expect(mainPanel.querySelector('[data-chip="digi-sel"]')).toBeNull();
    expect(mainPanel.querySelector('[data-chip="agc"]')).not.toBeNull();
  });
});

// Owner ruling 2026-09-23: the RFG lamp appears only while RF gain is
// reduced. Coordinator decision, same day: "reduced" is decided on the
// formatted percentage — a raw 254 of 255 is strictly below 1 yet displays
// as 100, so it shows nothing. At the maximum and while the reading is
// unknown the lamp stays in the flow as an empty reserved slot (fixed
// width by chip key), so the deck never shifts when the gain changes.
describe('standard RFG lamp appears only when RF gain is reduced (2/main_sub)', () => {
  const fixtureWithRfGain = (rfGain: number | 'unknown') => {
    const base = standardFixture('2/main_sub');
    const indicator = base.receiverIndicators![0];
    return validateRadioViewModel({
      ...base,
      receiverIndicators: [{
        ...indicator,
        rfGain: rfGain === 'unknown' ? unknownField<number>() : indicatorField(rfGain),
      }],
    });
  };
  const rfgLamp = (viewModel: RadioViewModel): HTMLElement => {
    const root = mountSurface({ viewModel, appearance: 'standard' });
    const mainPanel = panels(root).find((panel) => panel.getAttribute('data-receiver-instrument') === 'MAIN')!;
    return row(mainPanel, 'receiver').querySelector<HTMLElement>('[data-chip="rfg"]')!;
  };

  it('a reduced gain (0.6) is lit with its percentage', () => {
    const lamp = rfgLamp(fixtureWithRfGain(0.6));
    expect(lamp.textContent?.trim()).toBe('RFG 60%');
    expect(lamp.getAttribute('data-lit')).toBe('true');
    expect(lamp.getAttribute('aria-hidden')).toBeNull();
  });

  it('a gain that rounds to 99 (raw 253 of 255) is lit', () => {
    const lamp = rfgLamp(fixtureWithRfGain(253 / 255));
    expect(lamp.textContent?.trim()).toBe('RFG 99%');
    expect(lamp.getAttribute('data-lit')).toBe('true');
    expect(lamp.getAttribute('aria-hidden')).toBeNull();
  });

  it.each([
    ['at the maximum', 1],
    ['at a raw 254 of 255 — below 1 yet displaying as 100', 254 / 255],
    ['with an unknown reading', 'unknown'],
  ] as const)('%s the lamp is an empty reserved slot, unlit and aria-hidden', (_name, rfGain) => {
    const lamp = rfgLamp(fixtureWithRfGain(rfGain));
    expect(lamp, 'the slot stays in the flow').not.toBeNull();
    expect(lamp.textContent).toBe('');
    expect(lamp.getAttribute('data-lit')).toBe('false');
    expect(lamp.getAttribute('aria-hidden')).toBe('true');
    expect(lamp.getAttribute('data-indicator-fact')).toBe('rfg');
  });

  it('the lamp inventory is identical across reduced, maximum and unknown gains', () => {
    for (const rfGain of [0.6, 253 / 255, 254 / 255, 1, 'unknown'] as const) {
      const root = mountSurface({ viewModel: fixtureWithRfGain(rfGain), appearance: 'standard' });
      const mainPanel = panels(root).find((panel) => panel.getAttribute('data-receiver-instrument') === 'MAIN')!;
      const keys = Array.from(row(mainPanel, 'receiver').querySelectorAll('.lamp'))
        .map((lamp) => lamp.getAttribute('data-chip'));
      expect(keys, 'same lamp slots whatever the gain says').toEqual(
        ['agc', 'preamp', 'att', 'ip-plus', 'digi-sel', 'rfg']);
    }
  });
});

describe('MODE/FIL three-way presence (2/main_sub)', () => {
  const absent = { reading: { status: 'unknown' as const }, availability: { structural: false, operational: false } };
  const modeFilterFixture = () => validateRadioViewModel({
    ...standardFixture('2/main_sub'),
    modeFilter: {
      activeFilterConfiguration: null,
      currentMode: indicatorField('USB'),
      modeChoices: ['USB'],
      currentFilter: absent,
      filterChoices: ['FIL1'],
      filterWidth: absent,
      filterWidthMin: absent,
      filterWidthMax: absent,
    },
  });

  it('a structurally absent filter draws no FIL chip; MODE keeps its chip', () => {
    const root = mountSurface({ viewModel: modeFilterFixture(), appearance: 'standard' });
    expect(root.querySelectorAll('[data-chip="filter"]')).toHaveLength(0);
    const mainPanel = panels(root).find((panel) => panel.getAttribute('data-receiver-instrument') === 'MAIN')!;
    expect(mainPanel.querySelector('[data-chip="mode"]')?.textContent?.trim()).toBe('USB');
  });

  it('an unsupported display observation draws no chip either (same mechanism)', () => {
    const base = standardFixture('2/main_sub');
    const viewModel = validateRadioViewModel({
      ...base,
      vfos: base.vfos.map((vfo) => ({
        ...vfo,
        display: {
          frequencyHz: { state: 'current' as const, value: vfo.frequencyHz },
          mode: { state: 'unsupported' as const },
          filter: { state: 'current' as const, value: vfo.filter },
        },
      })),
    });
    const root = mountSurface({ viewModel, appearance: 'standard' });
    expect(root.querySelectorAll('[data-chip="mode"]')).toHaveLength(0);
    expect(root.querySelectorAll('[data-chip="filter"]')[0]?.textContent?.trim()).toBe('WIDE');
  });

  it('an unread filter keeps its chip unlit with the dim FIL label', () => {
    const base = standardFixture('2/main_sub');
    const viewModel = validateRadioViewModel({
      ...base,
      vfos: base.vfos.map((vfo) => ({ ...vfo, filter: null })),
    });
    const root = mountSurface({ viewModel, appearance: 'standard' });
    const chips = root.querySelectorAll('[data-chip="filter"]');
    expect(chips.length).toBeGreaterThan(0);
    for (const chip of Array.from(chips)) {
      expect(chip.getAttribute('data-lit')).toBe('false');
      expect(chip.textContent?.trim()).toBe('FIL');
    }
  });
});

describe('standard notch chip (one chip per notchMode value)', () => {
  const chipFor = (value: 'off' | 'auto' | 'manual') => {
    const base = standardFixture('2/main_sub');
    const indicator = base.receiverIndicators![0];
    const viewModel = validateRadioViewModel({
      ...base,
      receiverIndicators: [{
        ...indicator,
        notchMode: indicatorField<'off' | 'auto' | 'manual'>(value),
      }],
    });
    const root = mountSurface({ viewModel, appearance: 'standard' });
    const mainPanel = panels(root).find((panel) => panel.getAttribute('data-receiver-instrument') === 'MAIN')!;
    const chips = mainPanel.querySelectorAll('[data-chip="notch"]');
    return { chips, chip: chips[0] as HTMLElement };
  };

  it.each([
    ['off', 'NOTCH', 'false'],
    ['auto', 'NOTCH A', 'true'],
    ['manual', 'NOTCH M', 'true'],
  ] as const)('notchMode %s renders exactly one chip, %s, lit=%s', (value, text, lit) => {
    const { chips, chip } = chipFor(value);
    expect(chips.length).toBe(1);
    expect(chip.textContent?.trim()).toBe(text);
    expect(chip.getAttribute('data-lit')).toBe(lit);
  });
});

describe('standard TX chip (2/main_sub)', () => {
  const txChip = (rfState: 'receiving' | 'transmitting' | 'uncertain') => {
    const base = standardFixture('2/main_sub');
    const viewModel = validateRadioViewModel({
      ...base,
      radioWideIndicators: { ...base.radioWideIndicators!, rfState },
    });
    const root = mountSurface({ viewModel, appearance: 'standard' });
    const chips = Array.from(root.querySelectorAll('[data-chip="tx"]'));
    return {
      chips,
      main: chips.find((chip) => chip.closest('[data-receiver-instrument]')?.getAttribute('data-receiver-instrument') === 'MAIN'),
      sub: chips.find((chip) => chip.closest('[data-receiver-instrument]')?.getAttribute('data-receiver-instrument') === 'SUB'),
    };
  };

  it('lit only for the TX-target receiver while the radio transmits', () => {
    const transmitting = txChip('transmitting');
    expect(transmitting.chips.length).toBe(2);
    expect(transmitting.main!.getAttribute('data-lit')).toBe('true');
    expect(transmitting.main!.getAttribute('data-indicator-fact')).toBe('tx');
    expect(transmitting.main!.getAttribute('data-state')).toBe('transmitting');
    expect(transmitting.sub!.getAttribute('data-lit')).toBe('false');
  });

  it.each(['receiving', 'uncertain'] as const)('unlit everywhere while rfState is %s', (rfState) => {
    const state = txChip(rfState);
    for (const chip of state.chips) {
      expect(chip.getAttribute('data-lit')).toBe('false');
    }
  });
});

describe('standard under-frequency chips (2/main_sub)', () => {
  it('RIT shows its signed offset lit, XIT and SPLIT keep their slots unlit', () => {
    const root = mountSurface({ viewModel: standardFixture('2/main_sub'), appearance: 'standard' });
    const mainPanel = panels(root).find((panel) => panel.getAttribute('data-receiver-instrument') === 'MAIN')!;
    const under = row(mainPanel, 'under');
    const keys = Array.from(under.querySelectorAll('[data-chip]'))
      .map((chip) => chip.getAttribute('data-chip'));
    expect(keys).toEqual(['rit', 'xit', 'split']);
    // Radio-wide values appear on ONE panel: the active receiver's.
    expect(root.querySelectorAll('[data-chip="rit"]')).toHaveLength(1);
    expect(root.querySelectorAll('[data-chip="xit"]')).toHaveLength(1);
    expect(root.querySelectorAll('[data-chip="split"]')).toHaveLength(1);
    expect(under.querySelector('[data-chip="rit"]')?.textContent?.trim()).toBe('RIT +120');
    expect(under.querySelector('[data-chip="rit"]')?.getAttribute('data-lit')).toBe('true');
    expect(under.querySelector('[data-chip="rit"]')?.getAttribute('data-indicator-fact')).toBe('rit');
    expect(under.querySelector('[data-chip="xit"]')?.getAttribute('data-indicator-fact')).toBe('xit');
    expect(under.querySelector('[data-chip="split"]')?.textContent?.trim()).toBe('SPLIT');
    expect(under.querySelector('[data-chip="split"]')?.getAttribute('data-lit')).toBe('true');
  });

  it('unknown radio-wide offsets leave the RIT chip unlit with its label, no placeholder', () => {
    const root = mountSurface({ viewModel: standardFixture('2/main_sub', 'unknown'), appearance: 'standard' });
    const mainPanel = panels(root).find((panel) => panel.getAttribute('data-receiver-instrument') === 'MAIN')!;
    const rit = row(mainPanel, 'under').querySelector('[data-chip="rit"]')!;
    expect(rit.getAttribute('data-lit')).toBe('false');
    expect(rit.textContent?.trim()).toBe('RIT');
    expect(rit.textContent).not.toContain('—');
  });
});

describe('bridge operation grid (standard appearance)', () => {
  it('2/main_sub: one grid container holds the mock-up operation controls and accessible names', () => {
    const root = mountSurface({ viewModel: standardFixture('2/main_sub'), appearance: 'standard' });
    const bridge = root.querySelector('[data-instrument-bridge]');
    expect(bridge).not.toBeNull();
    const grid = bridge!.querySelectorAll('[data-testid="vfo-ops"]');
    expect(grid.length, 'exactly one operation grid in the bridge').toBe(1);
    const actions = Array.from(bridge!.querySelectorAll<HTMLButtonElement>('[data-dual-action]'));
    expect(actions.length).toBeGreaterThan(0);
    for (const action of actions) {
      expect(grid[0].contains(action), 'every operation control sits in the grid').toBe(true);
    }
    expect(actions.map((button) => button.getAttribute('aria-label') ?? button.textContent?.trim()))
      .toEqual(['MAIN', 'SUB', 'M⇄S', 'M=S']);
  });

  it('1/ab: the pair bridge grid keeps its controls and names', () => {
    const root = mountSurface({ viewModel: standardFixture('1/ab'), appearance: 'standard' });
    const bridge = root.querySelector('[data-instrument-bridge]');
    expect(bridge).not.toBeNull();
    const grid = bridge!.querySelectorAll('[data-testid="vfo-ops"]');
    expect(grid.length).toBe(1);
    const actions = Array.from(bridge!.querySelectorAll<HTMLButtonElement>('[data-dual-action]'));
    expect(actions.map((button) => button.getAttribute('aria-label') ?? button.textContent?.trim()))
      .toEqual(['A↔B', 'A=B']);
  });
});

describe('the live FTX-1 payload (many null leaves) prints no placeholder', () => {
  const mountFtx1 = (): HTMLElement => {
    const caps = { ...FTX1_CAPABILITIES, providerGeneration: FTX1_STATE.providerGeneration };
    const model = toRadioViewModel(FTX1_STATE, caps);
    expect(model).not.toBeNull();
    return mountSurface({ viewModel: model!, appearance: 'standard' });
  };

  it('absent capabilities draw no element; present groups keep their tabs (ftx1)', () => {
    const root = mountFtx1();
    // No antenna capability and no IP+ / DIGI-SEL: nothing drawn for them.
    expect(root.querySelector('[data-tray-tab="ant"]')).toBeNull();
    expect(root.querySelector('[data-chip="ip-plus"]')).toBeNull();
    expect(root.querySelector('[data-chip="digi-sel"]')).toBeNull();
    // The band group follows freqRanges and BW the filters group: both stay.
    // MOR-2526: each panel reads its OWN receiver's band — in this capture
    // MAIN (14.074 MHz) and SUB (14.073 MHz) are both observed inside the
    // named 20m band, so both tabs are lit with their own reading.
    const bandTabs = root.querySelectorAll('[data-tray-tab="band"]');
    expect(bandTabs.length).toBe(2);
    expect(bandTabs[0].textContent?.trim()).toBe('20M');
    expect(bandTabs[0].getAttribute('data-lit')).toBe('true');
    expect(bandTabs[1].textContent?.trim()).toBe('20M');
    expect(bandTabs[1].getAttribute('data-lit')).toBe('true');
    const bwTabs = root.querySelectorAll('[data-tray-tab="bw"]');
    expect(bwTabs.length).toBe(2);
    for (const tab of Array.from(bwTabs)) {
      expect(tab.getAttribute('data-lit')).toBe('true');
    }
    expect(bwTabs[0].textContent?.trim()).toMatch(/^BW \d+$/);
  });

  it('unknown leaves stay unlit while the absent filter selector reserves slots without FIL chips', () => {
    const root = mountFtx1();
    for (const panel of panels(root)) {
      for (const chip of panel.querySelectorAll('[data-tray-tab], [data-chip]')) {
        const text = chip.textContent ?? '';
        for (const forbidden of ['—', '?', 'null', 'undefined', 'NaN', 'UNKNOWN', 'N/A']) {
          expect(text, `chip ${chip.getAttribute('data-chip') ?? chip.getAttribute('data-tray-tab')} must not print ${forbidden}`)
            .not.toContain(forbidden);
        }
        // Owner ruling 2026-09-23: the captured radio reports RF gain at the
        // maximum, so its RFG lamp is deliberately an empty reserved slot —
        // the one unlit chip allowed to print nothing.
        if (chip.getAttribute('data-lit') === 'false' && chip.getAttribute('data-chip') !== 'rfg') {
          expect(chip.textContent?.trim(), 'an unlit chip keeps its label, never an empty frame')
            .not.toBe('');
        }
      }
      expect(panel.textContent, 'the whole panel text carries no dash').not.toContain('—');
    }
    // The captured radio has no filter selector, so each row reserves its
    // fixed-width slot without drawing or naming a filter chip.
    const filChips = root.querySelectorAll('[data-chip="filter"]');
    expect(filChips).toHaveLength(0);
    for (const panel of panels(root)) {
      const slots = panel.querySelectorAll('.chip-slot');
      expect(slots).toHaveLength(1);
      expect(slots[0].getAttribute('aria-hidden')).toBe('true');
      expect(slots[0].textContent).toBe('');
    }
    // Lit state is the model's own reading status, chip by chip.
    const caps = { ...FTX1_CAPABILITIES, providerGeneration: FTX1_STATE.providerGeneration };
    const model = toRadioViewModel(FTX1_STATE, caps)!;
    const firstPanel = panels(root)[0];
    const indicator = model.receiverIndicators!
      .find((item) => item.receiver === firstPanel.getAttribute('data-receiver-instrument'))!;
    const litOf = (field: { reading: { status: string } }) =>
      field.reading.status === 'known' ? 'true' : 'false';
    expect(firstPanel.querySelector('[data-chip="agc"]')?.getAttribute('data-lit'))
      .toBe(litOf(indicator.agcMode));
    expect(firstPanel.querySelector('[data-chip="nb"]')?.getAttribute('data-lit'))
      .toBe(indicator.nbActive.reading.status === 'known' && indicator.nbActive.reading.value === true
        ? 'true' : 'false');
  });
});

describe('slot stability in jsdom (fixed widths are source-pinned; jsdom measures no boxes)', () => {
  it('toggling reading values changes no chip inventory on any row', () => {
    const known = mountSurface({ viewModel: standardFixture('2/main_sub'), appearance: 'standard' });
    const unknown = mountSurface({ viewModel: standardFixture('2/main_sub', 'unknown'), appearance: 'standard' });
    const inventory = (root: HTMLElement) => panels(root).map((panel) =>
      Array.from(panel.querySelectorAll('[data-tray-tab], [data-chip]'))
        .map((chip) => `${chip.getAttribute('data-tray-tab') ?? chip.getAttribute('data-chip')}`)
        .sort().join('|'));
    expect(inventory(unknown)).toEqual(inventory(known));
  });
});

function styleBlock(path: string): string {
  const source = readFileSync(path, 'utf8');
  const match = source.match(/<style>([\s\S]*)<\/style>/);
  expect(match, `${path} has a style block`).toBeTruthy();
  // Comments are stripped first, the way `fieldline/__tests__/stylesheet.test.ts`
  // strips them: pins are about declarations, not prose.
  return match![1].replace(/\/\*[\s\S]*?\*\//g, '');
}

/** Bodies of every rule whose selector list contains `selector` verbatim
 *  (media blocks included; a comma member must match exactly, so
 *  `.x > .receiver-deck` never satisfies a query for `.receiver-deck`). */
function rulesFor(css: string, selector: string): string[] {
  const bodies: string[] = [];
  for (const [, selectorList, body] of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    // A selector written across lines (indented continuation) must match its
    // single-line spelling: collapse runs of whitespace before comparing.
    if (selectorList.split(',').some((member) => member.replace(/\s+/g, ' ').trim() === selector)) {
      bodies.push(body);
    }
  }
  expect(bodies.length, `rule "${selector}" exists`).toBeGreaterThan(0);
  return bodies;
}

/** Body of the sole rule whose whole selector list is exactly `[selector]`. */
function rule(css: string, selector: string): string {
  const standalone = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .filter(([, selectorList]) => selectorList.split(',').map((m) => m.trim()).join(',') === selector)
    .map(([, , body]) => body);
  expect(standalone, `standalone rule "${selector}" exists`).toHaveLength(1);
  return standalone[0];
}

function atRuleBlock(css: string, prelude: string): string {
  const start = css.indexOf(prelude);
  expect(start, `at-rule "${prelude}" exists`).toBeGreaterThanOrEqual(0);
  const open = css.indexOf('{', start);
  let depth = 0;
  for (let index = open; index < css.length; index += 1) {
    if (css[index] === '{') depth += 1;
    if (css[index] === '}') depth -= 1;
    if (depth === 0) return css.slice(open + 1, index);
  }
  throw new Error(`at-rule "${prelude}" is not closed`);
}

type Specificity = readonly [number, number, number];

function selectorList(selectorList: string): string[] {
  const selectors: string[] = [];
  let depth = 0;
  let start = 0;
  for (let index = 0; index < selectorList.length; index += 1) {
    if (selectorList[index] === '(') depth += 1;
    if (selectorList[index] === ')') depth -= 1;
    if (selectorList[index] === ',' && depth === 0) {
      selectors.push(selectorList.slice(start, index).trim());
      start = index + 1;
    }
  }
  selectors.push(selectorList.slice(start).trim());
  return selectors;
}

function compareSpecificity(left: Specificity, right: Specificity): number {
  return left[0] - right[0] || left[1] - right[1] || left[2] - right[2];
}

function specificity(selector: string): Specificity {
  let remainder = selector;
  const score = [0, 0, 0];
  for (const match of selector.matchAll(/:is\(([^()]*)\)/g)) {
    const strongest = selectorList(match[1])
      .map(specificity)
      .sort(compareSpecificity)
      .at(-1) ?? [0, 0, 0];
    score[0] += strongest[0];
    score[1] += strongest[1];
    score[2] += strongest[2];
    remainder = remainder.replace(match[0], '');
  }
  score[0] += remainder.match(/#[\w-]+/g)?.length ?? 0;
  score[1] += remainder.match(/\.[\w-]+|\[[^\]]+\]|:(?!:)[\w-]+/g)?.length ?? 0;
  score[2] += remainder.match(/::[\w-]+/g)?.length ?? 0;
  const types = remainder
    .replace(/#[\w-]+|\.[\w-]+|\[[^\]]+\]|::?[\w-]+|[>+~*]/g, ' ')
    .match(/[a-zA-Z][\w-]*/g);
  score[2] += types?.length ?? 0;
  return score as [number, number, number];
}

function winningDeclaration(
  css: string,
  target: Element,
  property: string,
  pseudoElement?: string,
): string | undefined {
  const candidates: Array<{ value: string; specificity: Specificity; order: number }> = [];
  for (const match of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const declaration = match[2].match(new RegExp(`(?:^|;)\\s*${property}\\s*:\\s*([^;]+)`));
    if (!declaration) continue;
    for (const selector of selectorList(match[1])) {
      const hasPseudoElement = selector.includes('::');
      if ((pseudoElement === undefined) === hasPseudoElement) continue;
      if (pseudoElement !== undefined && !selector.includes(pseudoElement)) continue;
      const elementSelector = pseudoElement === undefined ? selector : selector.replace(pseudoElement, '');
      try {
        if (!target.matches(elementSelector)) continue;
      } catch {
        continue;
      }
      candidates.push({
        value: declaration[1].trim(),
        specificity: specificity(selector),
        order: match.index,
      });
    }
  }
  return candidates.sort((left, right) =>
    compareSpecificity(left.specificity, right.specificity) || left.order - right.order,
  ).at(-1)?.value;
}

function winningInheritedDeclaration(
  css: string,
  target: Element,
  property: string,
): string | undefined {
  for (let current: Element | null = target; current !== null; current = current.parentElement) {
    const value = winningDeclaration(css, current, property);
    if (value !== undefined) return value;
  }
  return undefined;
}

const panelCss = styleBlock('src/components-v2/vfo/VfoPanel.svelte');
const surfaceCss = styleBlock('src/semantic/VfoSurface.svelte');
const layoutCss = styleBlock('src/components-v2/layout/RadioLayout.svelte');
const surfaceSource = readFileSync('src/semantic/VfoSurface.svelte', 'utf8');
const studiolineCss = readFileSync('src/presentation/languages/studioline/studioline.css', 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '');

describe('source pins: fixed slot widths (MOR-2509 slice 2)', () => {
  it('every tray tab, lamp and large chip declares a fixed width and never wraps', () => {
    for (const selector of ['.tab', '.lamp', '.chip-lg', '.chip-amber']) {
      const bodies = rulesFor(panelCss, selector).join('\n');
      expect(bodies, `${selector} reserves a fixed slot width`).toMatch(/width:\s*\d+px/);
    }
    // The DSP chip's fixed slot is its grid column; the chip fills it.
    expect(rulesFor(panelCss, '.chip-dsp').join('\n'))
      .toMatch(/width:\s*100%/);
    for (const selector of ['.tray', '.receiver-row', '.under-row', '.dsp']) {
      expect(rulesFor(panelCss, selector).join('\n'), `${selector} never wraps`)
        .not.toMatch(/flex-wrap:\s*wrap/);
    }
  });

  it('no panel rule hides overflow or clips the rows', () => {
    const panel = rule(panelCss, '.panel');
    expect(panel).toMatch(/min-width:\s*0/);
    expect(panel).not.toMatch(/overflow:\s*hidden/);
    expect(panel).toMatch(/grid-template-rows:\s*auto auto auto/);
    expect(panel).not.toMatch(/--vfo-panel-(header|meter|body)-height/);
  });

  it('the bridge operation column stacks fixed six-column group rows', () => {
    const ops = rulesFor(
      styleBlock('src/semantic/VfoOperationGroup.svelte'),
      ".vfo-ops[data-vfo-operation-appearance='standard']",
    ).join('\n');
    expect(ops).toMatch(/display:\s*flex;\s*flex-direction:\s*column/);
    expect(ops).toMatch(/gap:\s*0/);
    expect(rulesFor(
      styleBlock('src/semantic/VfoOperationGroup.svelte'),
      ".ops-row",
    ).join('\n')).toMatch(/grid-template-columns:\s*repeat\(6,\s*minmax\(0,\s*1fr\)\)/);
    expect(surfaceCss, 'no wider bridge override may exist')
      .not.toMatch(/\.vfo-ops[^}]*grid-template-columns:\s*repeat\(3/);
  });

  it('panels shrink before the bridge: receiver instruments declare min-width 0', () => {
    expect(rulesFor(surfaceCss, '.receiver-instrument').join('\n')).toMatch(/min-width:\s*0/);
  });

  it('no receiver-deck rule that can apply to the semantic deck hides overflow', () => {
    expect(rule(layoutCss, '.receiver-deck')).toMatch(/overflow:\s*visible/);
    for (const [, selector, body] of layoutCss.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      if (body.includes('overflow: hidden') && selector.includes('.receiver-deck')) {
        expect(selector, `deck clipping must stay scoped to the legacy root: ${selector.trim()}`)
          .toMatch(/:not\(\.semantic-deck\)/);
      }
    }
  });
});

describe('source pins: vertical rhythm, container queries, tokens (MOR-2509 slice 2)', () => {
  it('the five vertical gaps all read the one rhythm custom property', () => {
    expect(rulesFor(panelCss, '.tray').join('\n'))
      .toMatch(/margin-block-end:\s*calc\(17px \* var\(--vfo-deck-rhythm/);
    expect(rulesFor(panelCss, '.receiver-row').join('\n'))
      .toMatch(/margin-block-end:\s*calc\(16px \* var\(--vfo-deck-rhythm/);
    expect(rulesFor(panelCss, '.under-row').join('\n'))
      .toMatch(/margin-block-start:\s*calc\(14px \* var\(--vfo-deck-rhythm/);
    expect(rulesFor(panelCss, '.dsp').join('\n'))
      .toMatch(/gap:\s*calc\(9px \* var\(--vfo-deck-rhythm/);
    expect(rule(panelCss, '.panel'))
      .toMatch(/padding-block-end:\s*calc\(19px \* var\(--vfo-deck-rhythm/);
    expect(rule(panelCss, '.panel')).toMatch(/--vfo-deck-rhythm:\s*var\(--dl-vfo-rhythm,\s*1\)/);
  });

  it('the panel is an inline-size container with wide, compact, and narrow modes at 760/520/470 px', () => {
    expect(rule(panelCss, '.panel')).toMatch(/container-type:\s*inline-size/);
    const threshold = Number(rule(panelCss, '.panel')
      .match(/--vfo-panel-narrow-breakpoint:\s*(\d+)px/)?.[1]);
    expect(threshold).toBe(470);
    const compactThreshold = Number(rule(panelCss, '.panel')
      .match(/--vfo-panel-compact-breakpoint:\s*(\d+)px/)?.[1]);
    expect(compactThreshold).toBe(520);
    expect(panelCss).toMatch(/@container \(min-width:\s*760px\)/);
    // The container queries must hard-code the literal (custom properties are
    // not allowed there), so pin the literal EQUAL to its token.
    expect(panelCss).toMatch(new RegExp(`@container \\(max-width:\\s*${compactThreshold}px\\)`));
    expect(panelCss).toMatch(new RegExp(`@container \\(max-width:\\s*${threshold}px\\)`));
    expect(panelCss).not.toMatch(/@container \(max-width:\s*(?!520px|470px)\d+px\)/);
    const wide = panelCss.slice(panelCss.indexOf('@container (min-width: 760px)'));
    expect(wide.slice(0, wide.indexOf('@container (max-width')))
      .toMatch(/grid-template-columns:\s*1fr 1fr/);
    const narrow = panelCss.slice(panelCss.indexOf('@container (max-width: 470px)'));
    expect(narrow).toMatch(/grid-column:\s*1\s*\/\s*-1/);
  });

  it('the unsupported MODE/FIL slot and its chip read one width token in narrow mode', () => {
    const panel = rule(panelCss, '.panel');
    expect(panel).toMatch(/--vfo-large-chip-width:\s*70px/);
    for (const selector of ['.chip-slot', '.chip-lg']) {
      expect(rulesFor(panelCss, selector).join('\n'))
        .toMatch(/width:\s*var\(--vfo-large-chip-width\)/);
    }
    const compact = panelCss.slice(panelCss.indexOf('@container (max-width: 520px)'));
    expect(compact).toMatch(/--vfo-large-chip-width:\s*60px/);
    expect(compact).toMatch(/\.lamp\[data-chip='att'\]\s*\{\s*width:\s*46px/);
    expect(compact).toMatch(/\.lamp\[data-chip='rfg'\]\s*\{\s*width:\s*64px/);
  });

  it('the narrow under-row sets fixed chip widths only inside the meter-under-frequency query', () => {
    const prelude = '@container (max-width: 470px)';
    const narrow = atRuleBlock(panelCss, prelude);
    const narrowStart = panelCss.indexOf(prelude);
    const narrowEnd = panelCss.indexOf('{', narrowStart) + narrow.length + 2;
    const outsideNarrow = panelCss.slice(0, narrowStart) + panelCss.slice(narrowEnd);

    for (const [chip, width] of [['split', 72], ['rit', 88], ['xit', 60]] as const) {
      const declaration = new RegExp(
        `\\.chip-amber\\[data-chip='${chip}'\\]\\s*\\{[^}]*width:\\s*${width}px`,
      );
      expect(narrow, `${chip} uses its fixed narrow width`).toMatch(declaration);
      expect(outsideNarrow, `${chip} narrow width stays inside the 470px query`)
        .not.toMatch(declaration);
    }
    expect(panelCss).not.toContain('--dl-vfo-amber-chip-narrow-width-trim');
    expect(narrow).not.toMatch(/flex-wrap:\s*wrap/);
    expect(rulesFor(panelCss, '.under-row').some((body) => /gap:\s*6px/.test(body))).toBe(true);
  });

  it('chip colours consume the --dl-vfo-* language tokens', () => {
    expect(panelCss).toMatch(/var\(--dl-vfo-primary-neon,/);
    expect(panelCss).toMatch(/var\(--dl-vfo-red,/);
    expect(panelCss).toMatch(/var\(--dl-vfo-amber,/);
    expect(panelCss).toMatch(/var\(--dl-vfo-brown,/);
    expect(panelCss).toMatch(/var\(--dl-vfo-dsp,/);
    expect(panelCss).toMatch(/var\(--dl-vfo-slate,/);
    expect(panelCss).toMatch(/var\(--dl-vfo-unlit-text,/);
  });

  it('the frequency digits are bold and tabular', () => {
    const freq = rulesFor(panelCss, '.vfo-freq').join('\n');
    expect(freq).toMatch(/font-weight:\s*var\(--dl-vfo-frequency-weight,\s*800\)/);
    expect(freq).toMatch(/font-variant-numeric:\s*tabular-nums/);
  });

  it('the panel state owns the meter filter and the inactive state cancels it', () => {
    for (const [, selector, body] of panelCss.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      expect(body, `${selector.trim()} must not dim with a saturation filter`)
        .not.toMatch(/filter:\s*saturate/);
    }
    const inactive = rulesFor(panelCss, '.panel:not(.active)').join('\n');
    expect(inactive).toMatch(/--vfo-neon-fill:\s*var\(--dl-vfo-primary-neon-dim/);
    expect(inactive).not.toMatch(/(?:^|[;\s])(?:filter|opacity)\s*:/);
    expect(inactive).not.toMatch(/panel-meter/);
    expect(rule(panelCss, '.panel'))
      .toMatch(/--v2-meter-lit-filter:\s*var\(--dl-vfo-meter-lit-filter,\s*none\)/);
    expect(inactive).toMatch(/--v2-meter-lit-filter:\s*none/);
    const filterOwners = [...panelCss.matchAll(/([^{}]+)\{([^{}]*--v2-meter-lit-filter:\s*[^;}]+;[^{}]*)\}/g)]
      .map(([, selector]) => selector.trim());
    expect(filterOwners).toEqual(['.panel', '.panel:not(.active)']);
  });

  it('the inactive readout ink differs from the active ink in every mode that dims (R2-4)', () => {
    const inactive = rulesFor(panelCss, '.panel:not(.active)').join('\n');
    const base = rule(panelCss, '.panel');
    // The dim inks exist only on the inactive panel: the base wiring never
    // defines them, so the active readout keeps the inherited ink and the
    // secondary label tone.
    expect(inactive).toMatch(/--vfo-frequency-ink:\s*var\(--dl-vfo-frequency-ink-dim,\s*#a9b5c1\)/);
    expect(inactive).toMatch(/--vfo-name-ink:\s*var\(--dl-vfo-name-ink-dim,\s*#7f8d9b\)/);
    expect(base).not.toContain('--vfo-frequency-ink:');
    expect(base).not.toContain('--vfo-name-ink:');
    expect(rulesFor(panelCss, '.vfo-freq').join('\n'))
      .toMatch(/color:\s*var\(--vfo-frequency-ink,\s*currentcolor\)/);
    expect(rulesFor(panelCss, '.vfo-label').join('\n'))
      .toMatch(/color:\s*var\(--vfo-name-ink,\s*var\(--v2-text-secondary\)\)/);
    // An unread frequency stays an unlit label even on the inactive panel:
    // the (0,2,0) display-unknown rule outranks the (0,1,0) base rule.
    expect(rulesFor(panelCss, '.vfo-freq.display-unknown').join('\n'))
      .toMatch(/color:\s*var\(--vfo-unlit-text\)/);

    // jsdom in this harness substitutes no custom properties (the digit-
    // weight pin below records the same limit), so computed-style equality
    // cannot be asserted here. Instead each language's dim tokens are
    // resolved by hand along their var() chains, per mode, and compared
    // with the active ink each readout ACTUALLY renders:
    // - frequency: the language's root `color:` on the surface the panel
    //   inherits from (each language declares one — asserted below);
    // - name: --v2-text-secondary from .vfo-label's fallback. No language
    //   stylesheet sets that token (asserted below), so the single
    //   declaration in theme/tokens.css applies; a theme that overrides
    //   --v2-text-secondary to a language's dim value would render the two
    //   names alike — that variation is theme-owned and not covered here.
    const bodyOf = (css: string, selector: string, marker: string): string => {
      const found = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
        .find(([, selectors, body]) => selectors.trim() === selector && body.includes(marker))?.[2];
      expect(found, `${selector} declares ${marker}`).toBeTruthy();
      return found ?? '';
    };
    const hex = (body: string, token: string): string => {
      const m = body.match(new RegExp(`--${token}:\\s*(#[0-9a-fA-F]{6})`));
      expect(m, `token --${token} is a hex literal`).toBeTruthy();
      return (m?.[1] ?? '').toLowerCase();
    };

    const theme = readFileSync('src/components-v2/theme/tokens.css', 'utf8');
    const nameInk = hex(theme, 'v2-text-secondary');

    // studioline, dark and light. The active frequency ink is the language
    // root's `color: var(--dl-studioline-text)`, resolved from the palette
    // block per mode; the active name ink is the theme secondary.
    const stud = readFileSync('src/presentation/languages/studioline/studioline.css', 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '');
    const studRoot = "[data-design-language='studioline'][data-design-language]";
    const studLightSel = "[data-design-language='studioline'][data-design-language][data-language-mode='light']";
    expect(bodyOf(stud, studRoot, '--dl-studioline-text:'), 'studioline root paints the inherited surface ink')
      .toMatch(/color:\s*var\(--dl-studioline-text\)/);
    const studBase = bodyOf(stud, studRoot, '--dl-vfo-frequency-ink-dim:');
    const studLight = bodyOf(stud, studLightSel, '--dl-vfo-frequency-ink-dim:');
    const studDarkInk = hex(bodyOf(stud, studRoot, '--dl-studioline-text:'), 'dl-studioline-text');
    const studLightInk = hex(bodyOf(stud, studLightSel, '--dl-studioline-text:'), 'dl-studioline-text');
    for (const [mode, block, activeFreqInk] of [
      ['dark', studBase, studDarkInk],
      ['light', studLight, studLightInk],
    ] as const) {
      expect(hex(block, 'dl-vfo-frequency-ink-dim'), `studioline ${mode}: dim frequency ink differs from the inherited surface ink`)
        .not.toBe(activeFreqInk);
      expect(hex(block, 'dl-vfo-name-ink-dim'), `studioline ${mode}: dim name ink differs from the theme label tone`)
        .not.toBe(nameInk);
    }

    // fieldline. The dim inks are var(--dl-fieldline-muted) per mode. The
    // active frequency ink is the language root's `color:
    // var(--dl-fieldline-text)`; the active name ink is the theme
    // secondary, NOT the fieldline text token.
    const field = readFileSync('src/presentation/languages/fieldline/fieldline.css', 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '');
    const fieldRoot = "[data-design-language='fieldline'][data-design-language]";
    const fieldLightSel = "[data-design-language='fieldline'][data-design-language][data-language-mode='light']";
    expect(bodyOf(field, fieldRoot, '--dl-fieldline-text:'), 'fieldline root paints the inherited surface ink')
      .toMatch(/color:\s*var\(--dl-fieldline-text\)/);
    const fieldBase = bodyOf(field, fieldRoot, '--dl-fieldline-text:');
    const fieldLight = bodyOf(field, fieldLightSel, '--dl-fieldline-text:');
    const fieldHighContrast = bodyOf(field,
      "[data-design-language='fieldline'][data-design-language][data-theme='high-contrast']", '--dl-fieldline-text:');
    for (const [mode, block] of [['default', fieldBase], ['light', fieldLight]] as const) {
      expect(hex(block, 'dl-fieldline-muted'), `fieldline ${mode}: dim frequency ink differs from the inherited surface ink`)
        .not.toBe(hex(block, 'dl-fieldline-text'));
      expect(hex(block, 'dl-fieldline-muted'), `fieldline ${mode}: dim name ink differs from the theme label tone`)
        .not.toBe(nameInk);
    }
    // High contrast pins both palette tones to the same white, and the
    // high-contrast THEME pins --v2-text-secondary to that white too: the
    // inactive readout deliberately does not dim there — quieting yields
    // to legibility at the contrast rail (rule stated in fieldline.css).
    expect(hex(fieldHighContrast, 'dl-fieldline-muted'), 'fieldline high-contrast keeps the frequency undimmed')
      .toBe(hex(fieldHighContrast, 'dl-fieldline-text'));
    const highContrastTheme = readFileSync('src/components-v2/theme/themes/high-contrast.css', 'utf8');
    expect(highContrastTheme).toMatch(/--v2-text-secondary:\s*#ffffff/);

    // segmentline. The active frequency ink is the language root's
    // `color: var(--dl-segmentline-ink-strong)`; the dim inks are that ink
    // at lower alpha, so the numeral's paint differs structurally. The dim
    // name ink is a translucent ramp value — a different paint from any
    // opaque theme token, including the default compared here.
    const seg = readFileSync('src/presentation/languages/segmentline/segmentline.css', 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '');
    const segRoot = "[data-design-language='segmentline'][data-design-language]";
    expect(bodyOf(seg, segRoot, '--dl-segmentline-ink-strong:'), 'segmentline root paints the inherited surface ink')
      .toMatch(/color:\s*var\(--dl-segmentline-ink-strong\)/);
    const segBase = bodyOf(seg, segRoot, '--dl-vfo-frequency-ink-dim:');
    const segInk = bodyOf(seg, segRoot, '--dl-segmentline-ink-strong:');
    expect(segBase).toMatch(/--dl-vfo-frequency-ink-dim:\s*rgba\(var\(--dl-segmentline-ink\) \/ 0\.75\)/);
    expect(segBase).toMatch(/--dl-vfo-name-ink-dim:\s*rgba\(var\(--dl-segmentline-ink\) \/ 0\.6\)/);
    expect(segInk).toMatch(/--dl-segmentline-ink-strong:\s*rgba\(var\(--dl-segmentline-ink\) \/ 1\)/);

    // The name's active ink stays theme-owned: no language stylesheet may
    // set --v2-text-secondary, or the resolution above would be stale.
    for (const [language, css] of [['studioline', stud], ['fieldline', field], ['segmentline', seg]] as const) {
      expect(css, `${language} does not set the theme-owned --v2-text-secondary`)
        .not.toContain('--v2-text-secondary:');
    }
  });

  it('keeps filled chip ink mode-independent while light mode owns a light panel', () => {
    const base = [...studiolineCss.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
      .find(([, selectors, body]) => selectors.trim() === "[data-design-language='studioline'][data-design-language]"
        && body.includes('--dl-vfo-primary-neon:'))?.[2] ?? '';
    const light = rulesFor(
      studiolineCss,
      "[data-design-language='studioline'][data-design-language][data-language-mode='light']",
    ).join('\n');
    // Only the ACTIVE frames and ink stay white and mode-independent.
    for (const token of ['frame', 'tab-frame']) {
      expect(base, `--dl-vfo-${token} stays white`).toMatch(new RegExp(`--dl-vfo-${token}:\\s*#ffffff`));
      expect(light, `light mode does not replace --dl-vfo-${token}`).not.toContain(`--dl-vfo-${token}:`);
    }
    // The dim chrome follows the v8 mock-up: greyed frames, softened chip
    // ink, and the grey inactive readout of R2-4.
    expect(base).toMatch(/--dl-vfo-frame-dim:\s*#9fb0c4/);
    expect(base).toMatch(/--dl-vfo-tab-frame-dim:\s*#b9c4cf/);
    expect(base).toMatch(/--dl-vfo-dim-text:\s*#e3eaf2/);
    expect(base).toMatch(/--dl-vfo-frequency-ink-dim:\s*#a9b5c1/);
    expect(base).toMatch(/--dl-vfo-name-ink-dim:\s*#7f8d9b/);
    // Light mode owns its own quiet dim values — except the chip ink, which
    // sits on the dim fills and those stay dark in both modes.
    for (const token of ['frame-dim', 'tab-frame-dim', 'frequency-ink-dim', 'name-ink-dim']) {
      expect(light, `light mode owns --dl-vfo-${token}`).toContain(`--dl-vfo-${token}:`);
    }
    expect(light).not.toContain('--dl-vfo-dim-text:');
    expect(base).toMatch(/--dl-vfo-amber-chip-text:\s*#ffd47a/);
    expect(base).toMatch(/--dl-vfo-amber-chip-text-dim:\s*#b89a5e/);
    expect(light).toMatch(/--dl-vfo-red-text:\s*#a33228/);
    expect(light).toMatch(/--dl-vfo-amber-text:\s*#7a6220/);
    expect(light).toMatch(/--dl-vfo-brown-text:\s*#7a5210/);
    expect(light).toMatch(/--dl-vfo-panel-background:\s*linear-gradient\([^;]*#f9f6f0[^;]*#e9e4db/);
    expect(light).toMatch(/--dl-vfo-meter-well-background:\s*#151b22/);
    expect(light).toMatch(/--dl-vfo-panel-sheen:\s*none/);
    expect(light).toMatch(/--dl-vfo-panel-sheen-under:\s*var\(--v2-vfo-panel-sheen-override,/);
  });

  it('the dark ground and glass match the v8 mock-up: #10161d top stop, scanlines in the sheen', () => {
    const base = [...studiolineCss.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
      .find(([, selectors, body]) => selectors.trim() === "[data-design-language='studioline'][data-design-language]"
        && body.includes('--dl-vfo-primary-neon:'))?.[2] ?? '';
    const light = rulesFor(
      studiolineCss,
      "[data-design-language='studioline'][data-design-language][data-language-mode='light']",
    ).join('\n');
    expect(base).toMatch(
      /--dl-vfo-panel-background:\s*linear-gradient\(180deg,\s*#10161d 0%,\s*#0a0e13 38%,\s*#070a0e 100%\)/,
    );
    // One token, two layers: the 115° sheen over the 3px scanline raster,
    // exactly the mock-up's .glass background. Both die together under
    // prefers-contrast: more through the override.
    expect(base).toMatch(/--dl-vfo-panel-sheen:\s*var\(--v2-vfo-panel-sheen-override,/);
    expect(base).toMatch(
      /--dl-vfo-panel-sheen:[^;]*rgba\(255, 255, 255, 0\.055\)[^;]*rgba\(255, 255, 255, 0\.018\)[^;]*transparent 38%/,
    );
    expect(base).toMatch(
      /--dl-vfo-panel-sheen:[^;]*repeating-linear-gradient\(180deg,\s*rgba\(255, 255, 255, 0\.022\) 0 1px,\s*transparent 1px 3px\)/,
    );
    expect(base).toMatch(/--dl-vfo-panel-sheen-blend:\s*screen/);
    // Light mode owns no glass over content (block N1): the over token is
    // nulled there and the gloss lives UNDER the content via the under
    // token — no scanline raster on paper.
    expect(light).toMatch(/--dl-vfo-panel-sheen:\s*none/);
    expect(light).toMatch(
      /--dl-vfo-panel-sheen-under:\s*var\(--v2-vfo-panel-sheen-override,\s*linear-gradient\(115deg,\s*rgba\(255, 255, 255, 0\.72\)[^;]*rgba\(255, 255, 255, 0\.2\)[^;]*transparent 38%\)/,
    );
    expect(light).not.toMatch(/--dl-vfo-panel-sheen-under:[^;]*repeating-linear-gradient/);
  });

  it('every readout rule that sets a digit weight resolves it from the deck token', () => {
    const strip = (path: string) =>
      readFileSync(path, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    // Rules that can set the weight of a `.vfo-freq` wrapper or a `.freq`
    // readout. A source pin is the honest form here: jsdom substitutes no
    // custom properties, so a cascade loss (the language's 200 beating the
    // panel's token) is invisible to computed-style assertions.
    const weightDeclarations = (css: string): [string, string][] =>
      [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
        .flatMap(([selector, body]) => [...body.matchAll(/font-weight:\s*([^;]+);/g)]
          .map((match): [string, string] => [selector.trim(), match[1].trim()]));
    const resolvesFromDeckToken = (value: string): boolean =>
      /var\(--dl-vfo-frequency-weight/.test(value)
      || /var\(--v2-vfo-font-weight/.test(value)
      || /var\(--freq-font-weight/.test(value);
    for (const [path, css] of [
      ['src/presentation/languages/studioline/studioline.css', strip('src/presentation/languages/studioline/studioline.css')],
      ['src/components-v2/vfo/VfoPanel.svelte', styleBlock('src/components-v2/vfo/VfoPanel.svelte')],
      ['src/primitives/frequency/StandardFrequencyReadout.svelte',
        styleBlock('src/primitives/frequency/StandardFrequencyReadout.svelte')],
    ] as const) {
      for (const [selector, value] of weightDeclarations(css)) {
        // The interactive compound is the digits' own rule; the primitive's
        // passive `.freq` base (a literal 700 for non-interactive mounts) is
        // out of this package's lease and never wins against `.freq.interactive`.
        if (!/(^|[ ,])\.vfo-freq([ ,:.[]|$)/.test(selector)
          && !/(^|[ ,])\.freq\.interactive([ ,:.[]|$)/.test(selector)) continue;
        expect(value, `${path}: ${selector} must resolve font-weight from --dl-vfo-frequency-weight (directly or via --v2-vfo-font-weight/--freq-font-weight)`)
          .satisfies(resolvesFromDeckToken);
      }
    }
    // The language defines the token and routes the skin variable in one block.
    const studioline = strip('src/presentation/languages/studioline/studioline.css');
    expect(studioline).toMatch(/--dl-vfo-frequency-weight:\s*800/);
    expect(studioline).toMatch(/--v2-vfo-font-weight:\s*var\(--dl-vfo-frequency-weight\)/);
    const readout = readFileSync('src/primitives/frequency/StandardFrequencyReadout.svelte', 'utf8');
    expect(readout).toMatch(/'--freq-font-weight':\s*'var\(--v2-vfo-font-weight\)'/);
  });

  it('each design language stylesheet defines the panel tokens at exactly one root block', () => {
    for (const [path, language] of [
      ['src/presentation/languages/studioline/studioline.css', 'studioline'],
      ['src/presentation/languages/fieldline/fieldline.css', 'fieldline'],
      ['src/presentation/languages/segmentline/segmentline.css', 'segmentline'],
    ] as const) {
      const css = readFileSync(path, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
      const rootBlocks = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
        .filter(([, selectors, body]) => selectors.trim() === `[data-design-language='${language}'][data-design-language]`
          && body.includes('--dl-vfo-'));
      expect(rootBlocks.length, `${language} defines the panel tokens in exactly one root block`).toBe(1);
      for (const [, selectors, body] of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
        if (body.includes('--dl-vfo-')) {
          expect(selectors.trim(), `${language} touches the panel tokens only from its own scope`)
            .toContain(`[data-design-language='${language}']`);
        }
      }
    }
  });

  it('every --dl-vfo-key-* token the language bundles declare has a var() reader under src', () => {
    const declared = new Set<string>();
    for (const path of [
      'src/presentation/languages/studioline/studioline.css',
      'src/presentation/languages/fieldline/fieldline.css',
      'src/presentation/languages/segmentline/segmentline.css',
    ]) {
      const css = readFileSync(path, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
      for (const match of css.matchAll(/--dl-vfo-key-[a-z0-9-]+(?=\s*:)/g)) {
        declared.add(match[0]);
      }
    }
    // A declared token with no var() reader anywhere under src is an orphan:
    // re-adding one of the round-5 bridge key tokens turns this red. Today
    // only --dl-vfo-key-separator survives, read by VfoOperationGroup.svelte.
    const sourceFiles = (dir: string): string[] =>
      readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
        const path = `${dir}/${entry.name}`;
        if (entry.isDirectory()) return sourceFiles(path);
        return /\.(?:svelte|css|ts)$/.test(entry.name) ? [path] : [];
      });
    const contents = sourceFiles('src').map((path) => readFileSync(path, 'utf8'));
    const orphans = [...declared].filter((name) => {
      const reader = new RegExp(`var\\(\\s*${name}[\\s),]`);
      return !contents.some((content) => reader.test(content));
    });
    expect(orphans, 'declared --dl-vfo-key-* tokens with no var() reader under src').toEqual([]);
  });

  it('the alive layer is token-owned, standard-only and non-interactive', () => {
    const theme = readFileSync('src/components-v2/theme/tokens.css', 'utf8');
    expect(theme).toMatch(/@media \(prefers-contrast:\s*more\)[\s\S]*--v2-vfo-glow-override:\s*none/);
    expect(theme).toMatch(/--v2-vfo-meter-lit-filter-override:\s*none/);
    expect(theme).toMatch(/--v2-vfo-panel-sheen-override:\s*none/);

    for (const [path, language] of [
      ['src/presentation/languages/studioline/studioline.css', 'studioline'],
      ['src/presentation/languages/fieldline/fieldline.css', 'fieldline'],
      ['src/presentation/languages/segmentline/segmentline.css', 'segmentline'],
    ] as const) {
      const css = readFileSync(path, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
      const root = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
        .find(([, selectors, body]) => selectors.trim() === `[data-design-language='${language}'][data-design-language]`
          && body.includes('--dl-vfo-'))?.[2] ?? '';
      for (const token of ['panel-background', 'panel-shadow', 'panel-sheen', 'bridge-background',
        'bridge-shadow', 'meter-well-background', 'meter-well-shadow', 'meter-lit-filter',
        'frequency-glow', 'primary-glow', 'red-glow', 'amber-glow', 'dsp-glow',
        'frequency-ink-dim', 'name-ink-dim']) {
        expect(root, `${language} owns --dl-vfo-${token}`).toContain(`--dl-vfo-${token}:`);
      }
    }

    expect(rule(panelCss, '.panel')).toMatch(/background:\s*var\(--dl-vfo-panel-background/);
    // The glass is the panel's last box, ABOVE the content, never the old
    // z-index: -1 under-content placement.
    const sheen = rule(panelCss, '.panel::after');
    expect(sheen).toMatch(/pointer-events:\s*none/);
    expect(sheen).toMatch(/position:\s*absolute/);
    expect(sheen).not.toMatch(/z-index:\s*-/);
    expect(sheen).toMatch(/mix-blend-mode:\s*var\(--dl-vfo-panel-sheen-blend/);
    // Light mode's gloss is a separate UNDER layer: ::before, still no
    // negative z-index — the rows are positioned, so tree order paints them
    // above the first box.
    const underGlass = rule(panelCss, '.panel::before');
    expect(underGlass).toMatch(/pointer-events:\s*none/);
    expect(underGlass).not.toMatch(/z-index:\s*-/);
    expect(underGlass).toMatch(/background:\s*var\(--dl-vfo-panel-sheen-under,\s*none\)/);
    for (const row of ['.tray', '.receiver-row', '.display-row']) {
      expect(rulesFor(panelCss, row).join('\n'), `${row} paints above the under-glass`)
        .toMatch(/position:\s*relative/);
    }
    expect(rulesFor(panelCss, '.panel-meter').join('\n'))
      .toMatch(/background:\s*var\(--dl-vfo-meter-well-background/);
    expect(rulesFor(panelCss, '.panel-meter').join('\n'))
      .not.toMatch(/--v2-meter-lit-filter\s*:/);
    expect(rulesFor(surfaceCss, "[data-vfo-appearance='standard'] .bridge").join('\n'))
      .toMatch(/background:\s*var\(--dl-vfo-bridge-background/);
    expect(surfaceCss).not.toMatch(/\[data-vfo-appearance='(?:semantic|sdr)'\][^{}]*--dl-vfo-/);
  });

  it('imports and mounts VfoPanel only inside the Standard instrument branch', () => {
    expect(surfaceSource.match(/import VfoPanel\b/g)).toHaveLength(1);
    expect(surfaceSource.match(/<VfoPanel\b/g)).toHaveLength(1);
    const start = surfaceSource.indexOf('{#snippet standardInstrument');
    const end = surfaceSource.indexOf('\n  {#if appearance', start);
    expect(start).toBeGreaterThan(0);
    expect(end).toBeGreaterThan(start);
    const standardInstrument = surfaceSource.slice(start, end);
    expect(standardInstrument).toContain('<VfoPanel');
    expect(standardInstrument).not.toContain('slotTag=');
    expect(surfaceSource.slice(0, start) + surfaceSource.slice(end)).not.toContain('<VfoPanel');
    expect(surfaceSource).toMatch(/if \(appearance !== 'standard' \|\| instrumentReceivers\.length !== 1\) return null/);
    expect(surfaceSource.match(/\{#if appearance === 'standard'}\{@render standardInstrument/g)).toHaveLength(2);
  });

  it('glow belongs only to lit large elements and the inactive panel cancels it', () => {
    for (const selector of [
      ".chip-lg[data-lit='true']", ".tab[data-lit='true']", ".lamp[data-lit='true']",
      ".chip-amber[data-lit='true']", ".chip-dsp[data-lit='true']", ".chip-tx[data-lit='true']",
    ]) {
      expect(rulesFor(panelCss, selector).join('\n'), `${selector} consumes a glow token`)
        .toMatch(/(?:box-shadow|text-shadow):\s*var\(--vfo-/);
    }
    const inactive = rulesFor(panelCss, '.panel:not(.active)').join('\n');
    expect(inactive).toMatch(/--vfo-frequency-glow:\s*none/);
    expect(inactive).toMatch(/--vfo-(?:primary|red|amber|dsp)-glow:\s*none/);
    expect(inactive).toMatch(/--v2-meter-lit-filter:\s*none/);
    expect(panelCss).not.toMatch(/@keyframes|animation\s*:/);
  });
});

describe('source pins: the tile fills its wrapper (MOR-2509 correction 5)', () => {
  const indicatorCss = styleBlock('src/semantic/VfoIndicatorRow.svelte');

  it('the wrapper is a column so its stretched height reaches the tile, in every appearance', () => {
    expect(rulesFor(surfaceCss, '.receiver-instrument').join('\n'))
      .toMatch(/display:\s*flex;\s*flex-direction:\s*column/);
    expect(rulesFor(surfaceCss, "[data-vfo-appearance='standard'] .receiver-instrument > :where(.vfo-tile)").join('\n'))
      .toMatch(/flex:\s*1/);
    expect(rulesFor(surfaceCss, '.receiver-instrument > :global(.indicator-row)').join('\n'))
      .toMatch(/flex:\s*1/);
  });

  it('extra height lands below the chip row, not in stretched or centred rows', () => {
    expect(rule(panelCss, '.panel')).toMatch(/align-content:\s*start/);
    expect(rulesFor(indicatorCss, '.indicator-row').join('\n')).toMatch(/align-content:\s*start/);
  });
});

describe('source pins: bridge inset and hit targets (MOR-2509 correction 2)', () => {
  const opsCss = styleBlock('src/semantic/VfoOperationGroup.svelte');
  const segmentCss = styleBlock('src/components-v2/vfo/ActiveReceiverToggle.svelte');

  it('the caption wraps: no active-receiver rule declares nowrap', () => {
    for (const [, selector, body] of surfaceCss.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      if (selector.includes('.active-receiver')) {
        expect(body, `${selector.trim()} must not pin the caption to one line`)
          .not.toMatch(/white-space:\s*nowrap/);
      }
    }
  });

  it('nothing in the bridge blinks: the operation group declares no animation', () => {
    expect(opsCss).not.toMatch(/@keyframes/);
    expect(opsCss).not.toMatch(/animation\s*:/);
  });

  it('the bridge block inset and the panel wrapper padding read one custom property', () => {
    expect(rulesFor(surfaceCss, '.instrument-panel').join('\n'))
      .toMatch(/--vfo-instrument-inset-block:\s*6px/);
    const standardScope = rulesFor(surfaceCss,
      "[data-vfo-appearance='standard'] .instrument-panel:not(:has(> .standard-pair-bridge))").join('\n');
    expect(standardScope).toMatch(/--vfo-instrument-inset-block:\s*8px/);
    const FALLBACK_FREE = /var\(--vfo-instrument-inset-block\)(?![^;]*,)/;
    for (const [label, declaration, css] of [
      ['base wrapper', 'padding', rulesFor(surfaceCss, '.receiver-instrument').join('\n')],
      ['standard wrapper', 'padding', rulesFor(surfaceCss, "[data-vfo-appearance='standard'] .receiver-instrument").join('\n')],
      ['bridge', 'margin-block', rulesFor(surfaceCss, '.bridge').join('\n')],
    ] as const) {
      const value = css.match(new RegExp(`${declaration}:\\s*([^;]*);`))
        ?? css.match(new RegExp(`${declaration}:[ \\n]*([^;]*);`));
      expect(value, `${label} declares ${declaration}`).toBeTruthy();
      expect(value![1], `${label} reads the inset without a fallback literal`)
        .toMatch(FALLBACK_FREE);
    }
  });

  it('the bridge width is one custom property and the standard deck uses the mock-up column', () => {
    expect(rulesFor(surfaceCss, '.instrument-panel').join('\n'))
      .toMatch(/--vfo-bridge-width:\s*180px/);
    expect(rulesFor(surfaceCss, '.bridge').join('\n'))
      .toMatch(/flex:\s*0 0 var\(--vfo-bridge-width\)/);
    expect(rulesFor(surfaceCss, "[data-vfo-appearance='standard'] .bridge").join('\n'))
      .toMatch(/--vfo-bridge-width:\s*212px/);
    expect(rulesFor(surfaceCss, "[data-vfo-appearance='standard'] .standard-pair-bridge").join('\n'))
      .toMatch(/flex:\s*0 0 var\(--vfo-bridge-width\)/);
    // The qualified assertion pins the Standard 150 px rule below 1280 px;
    // the compact assertion pins the shared 150 px rule used by SDR at 1050 px.
    const narrowBlock = surfaceCss.slice(
      surfaceCss.indexOf('@media (max-width: 1279px)'),
      surfaceCss.indexOf('@media (max-width: 1050px)'),
    );
    expect(narrowBlock, 'the narrow bridge rule keeps the attribute-qualified selector')
      .toMatch(/\[data-vfo-appearance='standard'\] \.bridge\s*\{[^}]*--vfo-bridge-width:\s*150px/s);
    expect(narrowBlock, 'the Standard 1279px block keeps its attribute-qualified selector')
      .not.toMatch(/(?:^|[{}])\s*\.bridge\s*\{[^}]*--vfo-bridge-width/s);
    const compactBlock = surfaceCss.slice(
      surfaceCss.indexOf('@media (max-width: 1050px)'),
      surfaceCss.indexOf('@media (max-width: 760px)'),
    );
    expect(compactBlock, 'the SDR appearance keeps the shared 150px bridge at 1050px')
      .toMatch(/(?:^|[{}])\s*\.bridge\s*\{[^}]*--vfo-bridge-width:\s*150px/s);
    expect(surfaceCss.indexOf('@media (max-width: 1279px)'),
      'equal specificity with the desktop rule: the narrow rule wins by source order')
      .toBeGreaterThan(surfaceCss.indexOf('--vfo-bridge-width: 212px'));
  });

  it('stacks an absolute Standard pair at the 1024-class breakpoint', () => {
    const compact = surfaceCss.slice(surfaceCss.indexOf('@media (max-width: 1050px)'));
    expect(compact.slice(0, compact.indexOf('@media (max-width: 760px)')))
      .toMatch(/\.standard-receiver\[data-standard-vfo-slot\][^{]*\{[^}]*flex-basis:\s*calc\(100% - 192px\)/s);
  });
});

describe('mock-up v8 geometry with the owner 13:28 EDT bridge-key override', () => {
  it('keeps the mock-up dimensions and type while the Button family owns key paint', () => {
    const ops = styleBlock('src/semantic/VfoOperationGroup.svelte');
    const receiver = styleBlock('src/components-v2/vfo/ActiveReceiverToggle.svelte');
    const buttons = readFileSync('src/components-v2/controls/control-button.css', 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '');

    const operationGrid = rulesFor(ops, '.ops-row').join('\n');
    const standardBridge = rulesFor(surfaceCss,
      "[data-vfo-appearance='standard'] .standard-pair-bridge").join('\n');
    expect(rulesFor(surfaceCss, "[data-vfo-appearance='standard'] .bridge").join('\n'),
      'changing the mock-up 212px bridge column turns this red')
      .toMatch(/--vfo-bridge-width:\s*212px/);
    expect(standardBridge, 'changing the mock-up 12px/10px bridge padding turns this red')
      .toMatch(/padding:\s*12px 10px/);
    expect(operationGrid, 'changing the six-column bridge grid turns this red')
      .toMatch(/grid-template-columns:\s*repeat\(6,\s*minmax\(0,\s*1fr\)\)/);
    expect(operationGrid, 'changing the 6px key gap turns this red').toMatch(/gap:\s*6px/);
    expect(rulesFor(ops, '.ops-row > :global(.v2-control-button)').join('\n'),
      'changing a full key from three of six columns turns this red')
      .toMatch(/grid-column:\s*span 3/);
    expect(rulesFor(ops, '.functions-row > :global(.v2-control-button)').join('\n'),
      'changing a function key from two of six columns turns this red')
      .toMatch(/grid-column:\s*span 2/);
    expect(rulesFor(receiver, '.active-receiver-toggle.embedded.hardware').join('\n'),
      'changing the selector grid or its 6px gap turns this red')
      .toMatch(/grid-template-columns:\s*repeat\(6,\s*minmax\(0,\s*1fr\)\);\s*gap:\s*6px/);
    expect(rulesFor(receiver, '.active-receiver-toggle.embedded.hardware > :global(button)').join('\n'),
      'changing a receiver key from three of six columns turns this red')
      .toMatch(/grid-column:\s*span 3/);

    const keyRule = rulesFor(buttons,
      ".vfo-ops[data-vfo-operation-appearance='standard'] .v2-control-button").join('\n');
    for (const [claim, pattern] of [
      ['28px minimum key height', /min-height:\s*28px/],
      ['full row width', /width:\s*100%/],
      ['12px labels', /font-size:\s*12px/],
      ['700 label weight', /font-weight:\s*700/],
      ['0.06em label tracking', /letter-spacing:\s*0\.06em/],
    ] as const) {
      expect(keyRule, `changing the mock-up ${claim} turns this red`).toMatch(pattern);
    }
    expect(keyRule, '28px remains a minimum, not a fixed key height')
      .not.toMatch(/(?:^|[;{])\s*height:\s*28px/);
    expect(keyRule, 'the bridge does not replace the Button family paint')
      .not.toMatch(/(?:^|;)\s*(?:background|border|border-radius|color|box-shadow):/);

    expect(rulesFor(ops, ".vfo-ops[data-vfo-operation-appearance='standard']").join('\n'),
      'the operation stack consumes the bridge height')
      .toMatch(/flex:\s*1 1 auto/);
    expect(operationGrid, 'every present row receives an equal share above its 28px minimum')
      .toMatch(/flex:\s*1 1 0;\s*min-height:\s*28px/);
    expect(rulesFor(ops,
      ".vfo-ops[data-vfo-operation-appearance='standard'] .ops-row > :global(.v2-control-button)",
    ).join('\n'), 'every key stretches to its row')
      .toMatch(/height:\s*100%/);

    const divider = rulesFor(ops, '.bridge-divider').join('\n');
    expect(divider, 'changing the one-pixel separator turns this red').toMatch(/height:\s*1px/);
    expect(divider, 'changing the separator 8px vertical margin turns this red')
      .toMatch(/margin:\s*8px 0/);
    expect(buttons).not.toMatch(/vfo-operation-appearance='standard'[^}]*indicator-style='dot'/s);
    expect(buttons).not.toContain("data-appearance='selected'");
  });

  it('keeps the longest function label inside the 212px column', () => {
    const root = mountSurface({ viewModel: withTxAux(standardFixture('2/main_sub')), appearance: 'standard' });
    const bridge = root.querySelector<HTMLElement>('[data-instrument-bridge]')!;
    const keys = Array.from(bridge.querySelectorAll<HTMLButtonElement>('.v2-control-button'));
    const tuner = bridge.querySelector<HTMLButtonElement>('[data-vfo-tuner]')!;
    expect(tuner.textContent?.trim()).toBe('TUNER');

    const bridgeWidth = 212;
    const bridgeBorder = 2;
    const bridgePadding = 20;
    const trackGap = 6;
    const trackWidth = (bridgeWidth - bridgeBorder - bridgePadding - (5 * trackGap)) / 6;
    const functionKeyBorderBox = (2 * trackWidth) + trackGap;
    const keyClientWidth = functionKeyBorderBox - 2;
    const fontSize = 12;
    const labelWidth = (tuner.textContent!.trim().length * fontSize * 0.6)
      + ((tuner.textContent!.trim().length - 1) * fontSize * 0.06);

    for (const key of keys) {
      const isFunction = key.closest('.functions-row') !== null;
      const borderBox = isFunction ? functionKeyBorderBox : (3 * trackWidth) + (2 * trackGap);
      Object.defineProperties(key, {
        clientWidth: { configurable: true, value: borderBox - 2 },
        scrollWidth: { configurable: true, value: isFunction ? labelWidth : borderBox - 2 },
      });
      expect(key.scrollWidth, key.textContent?.trim()).toBeLessThanOrEqual(key.clientWidth);
    }
    expect(labelWidth).toBeLessThanOrEqual(keyClientWidth);
  });

  it('keeps the longest function label inside the 150px column below 1280px', () => {
    const narrowBlock = surfaceCss.slice(
      surfaceCss.indexOf('@media (max-width: 1279px)'),
      surfaceCss.indexOf('@media (max-width: 1050px)'),
    );
    const narrowRule = narrowBlock.match(
      /\[data-vfo-appearance='standard'\] \.bridge\s*\{([^}]*)\}/s);
    expect(narrowRule, 'the narrow bridge rule keeps the attribute-qualified selector').toBeTruthy();
    const bridgeWidth = Number(narrowRule![1].match(/--vfo-bridge-width:\s*(\d+)px/)?.[1]);
    expect(bridgeWidth).toBe(150);
    const paddingInline = Number(narrowRule![1].match(/padding:\s*12px\s+(\d+)px/)?.[1]);
    expect(Number.isFinite(paddingInline), 'the narrow rule keeps the 12px block padding').toBe(true);

    // Same measure as "keeps the longest function label inside the 212px
    // column": TUNER is the longest function label, Roboto Mono advances are
    // 0.6em, and the tracking adds 0.06em between glyphs.
    const trackGap = 6;
    const trackWidth = (bridgeWidth - 2 - (2 * paddingInline) - (5 * trackGap)) / 6;
    const keyClientWidth = (2 * trackWidth) + trackGap - 2;
    const labelWidth = (5 * 12 * 0.6) + (4 * 12 * 0.06);
    expect(keyClientWidth,
      `TUNER (${labelWidth}px) fits the narrow span-2 key client box (${keyClientWidth}px)`)
      .toBeGreaterThanOrEqual(labelWidth);
  });

  it('pins full-height row alignment for a 196px bridge through shared layout tokens', () => {
    const surface = rulesFor(surfaceCss,
      "[data-vfo-appearance='standard'] .standard-pair-bridge").join('\n');
    const ops = styleBlock('src/semantic/VfoOperationGroup.svelte');
    expect(surface).toMatch(/padding:\s*12px 10px/);
    expect(surface).toMatch(/justify-content:\s*stretch/);
    expect(rulesFor(ops, '.ops-row').join('\n'))
      .toMatch(/flex:\s*1 1 0;\s*min-height:\s*28px/);
    expect(rulesFor(ops, '.bridge-divider').join('\n'))
      .toMatch(/height:\s*1px;\s*margin:\s*8px 0/);

    const bridgeHeight = 196;
    const paddingBlock = 24;
    const dividerBlock = 17;
    const interRowGaps = 12;
    const rowHeight = (bridgeHeight - paddingBlock - dividerBlock - interRowGaps) / 4;
    const rowTops = [12, 12 + rowHeight + 6, 12 + (2 * (rowHeight + 6)), 148.25];
    expect(rowHeight).toBe(35.75);
    expect(rowTops).toEqual([12, 53.75, 95.5, 148.25]);
    expect(rowTops.at(-1)! + rowHeight).toBe(bridgeHeight - 12);
  });

  it('resolves family edge bars and one hardware background for selected and function keys', () => {
    const buttons = readFileSync('src/components-v2/controls/control-button.css', 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '');
    const host = document.createElement('div');
    host.dataset.vfoAppearance = 'standard';
    host.innerHTML = `
      <div data-instrument-bridge>
        <div class="vfo-ops" data-vfo-operation-appearance="standard">
          <button class="v2-control-button active" data-active="true" data-surface="hardware"
            data-active-receiver-segment="MAIN"
            data-indicator-style="edge-left" data-indicator-color="cyan"></button>
          <button class="v2-control-button active" data-active="true" data-surface="hardware"
            data-vfo-tuner
            data-indicator-style="edge-left" data-indicator-color="red"></button>
        </div>
      </div>
      <div class="bridge" data-instrument-bridge>
        <button class="vfo-select" data-vfo-select-absolute="A"></button>
      </div>
    `;
    const selected = host.querySelector<HTMLButtonElement>('[data-active-receiver-segment]')!;
    const tuner = host.querySelector<HTMLButtonElement>('[data-vfo-tuner]')!;
    expect(winningDeclaration(buttons, selected, '--indicator-color')).toBe('var(--indicator-cyan)');
    expect(winningDeclaration(buttons, tuner, '--indicator-color')).toBe('var(--indicator-red)');
    for (const key of [selected, tuner]) {
      expect(winningDeclaration(buttons, key, 'background', '::before'))
        .toMatch(/var\(--indicator-color\) 50%/);
      expect(winningDeclaration(buttons, key, 'opacity', '::before')).toBe('1');
    }
    const selectedBackground = winningDeclaration(buttons, selected, 'background');
    const tunerBackground = winningDeclaration(buttons, tuner, 'background');
    expect(selectedBackground).toBe(tunerBackground);
    expect(selectedBackground).toMatch(/var\(--hw-gradient-top-1\)/);
    const identity = host.querySelector<HTMLButtonElement>('[data-vfo-select-absolute]')!;
    expect(winningInheritedDeclaration(surfaceCss, identity, '--btn-font-size')).toBe('12px');
    expect(winningInheritedDeclaration(surfaceCss, identity, '--btn-min-height')).toBeUndefined();
  });
});

describe('bridge hardware keys (MOR-2509 package C)', () => {
  const opsCss = styleBlock('src/semantic/VfoOperationGroup.svelte');
  /** 2/main_sub with every radio function present and observed. */
  function functionsFixture(changes?: {
    atu?: 'off' | 'on' | 'tuning' | 'unknown';
    vox?: boolean | 'unknown';
    dialLock?: boolean | 'unknown';
  }): RadioViewModel {
    const field = <T>(value: T | 'unknown') => value === 'unknown'
      ? { reading: { status: 'unknown' as const }, availability: { structural: true, operational: true } }
      : { reading: { status: 'known' as const, value }, availability: { structural: true, operational: true } };
    const base = withTxAux(standardFixture('2/main_sub'));
    return validateRadioViewModel({
      ...base,
      ...(changes?.atu !== undefined || changes?.dialLock !== undefined ? {
        radioWideIndicators: {
          ...base.radioWideIndicators!,
          ...(changes?.atu !== undefined ? { atu: field(changes.atu) } : {}),
          ...(changes?.dialLock !== undefined ? { dialLock: field(changes.dialLock) } : {}),
        },
      } : {}),
      ...(changes?.vox !== undefined ? { txAux: { ...base.txAux!, vox: field(changes.vox) } } : {}),
    });
  }

  /** The bridge-local name of an ordered key element. */
  function keyName(element: Element): string {
    const action = element.getAttribute('data-dual-action');
    if (action !== null) return action;
    const segment = element.getAttribute('data-active-receiver-segment');
    if (segment !== null) return segment;
    if (element.hasAttribute('data-vfo-split')) return 'split';
    if (element.hasAttribute('data-vfo-dual-watch')) return 'dual-watch';
    if (element.classList.contains('bridge-divider')) return 'divider';
    if (element.hasAttribute('data-vfo-tuner')) return 'tuner';
    if (element.hasAttribute('data-vfo-vox')) return 'vox';
    return 'lock';
  }

  it('renders every bridge key through the shared button family', () => {
    const root = mountSurface({ viewModel: functionsFixture(), appearance: 'standard' });
    const bridge = root.querySelector('[data-instrument-bridge]')!;
    const buttons = Array.from(bridge.querySelectorAll<HTMLButtonElement>('button'));
    expect(buttons.length).toBeGreaterThan(0);
    for (const button of buttons) {
      expect(button.classList, button.textContent ?? '').toContain('v2-control-button');
      expect(button.getAttribute('data-surface'), button.textContent ?? '').toBe('hardware');
      expect(button.getAttribute('data-indicator-style'), button.textContent ?? '').toBe('edge-left');
      expect(button.getAttribute('data-appearance'), button.textContent ?? '').toBeNull();
    }
    const selected = bridge.querySelector<HTMLButtonElement>(
      '[data-active-receiver-segment="MAIN"]',
    )!;
    expect(selected.getAttribute('data-active')).toBe('true');
    expect(selected.getAttribute('data-indicator-color')).toBe('cyan');
    for (const [selector, color] of [
      ['[data-active-receiver-segment="SUB"]', 'cyan'],
      ['[data-vfo-swap]', 'cyan'],
      ['[data-vfo-equalize]', 'cyan'],
      ['[data-vfo-split]', 'cyan'],
      ['[data-vfo-dual-watch]', 'cyan'],
      ['[data-vfo-tuner]', 'red'],
      ['[data-vfo-vox]', 'violet'],
      ['[data-vfo-lock]', 'cyan'],
    ] as const) {
      expect(bridge.querySelector(selector)?.getAttribute('data-indicator-color'), selector).toBe(color);
    }
  });

  it('orders the groups selector, momentary actions, latching toggles, divider, radio functions', () => {
    const root = mountSurface({ viewModel: functionsFixture(), appearance: 'standard' });
    const order = Array.from(root.querySelector('[data-instrument-bridge]')!.querySelectorAll<HTMLElement>(
      '[data-active-receiver-segment], [data-dual-action], [data-vfo-split], [data-vfo-dual-watch],'
        + ' .bridge-divider, [data-vfo-tuner], [data-vfo-vox], [data-vfo-lock]',
    )).map(keyName);
    expect(order).toEqual([
      'main', 'sub', 'swap', 'equalize', 'split', 'dual-watch',
      'divider', 'tuner', 'vox', 'lock',
    ]);
    expect(root.querySelector('[data-vfo-tuner]')?.closest('.functions-row')).not.toBeNull();
    const functionKeys = rulesFor(
      opsCss, '.functions-row > :global(.v2-control-button)',
    ).join('\n');
    expect(functionKeys).toMatch(/grid-column:\s*span 2/);
    expect(rulesFor(opsCss, '.ops-row > :global(.v2-control-button)').join('\n'))
      .toMatch(/grid-column:\s*span 3;\s*min-width:\s*0;\s*width:\s*100%/);
    expect(opsCss).not.toContain('.ops-row > :global(*)');
  });

  it('keeps the SPLIT and DW latching keys as cyan edge-bar switches', () => {
    const root = mountSurface({ viewModel: functionsFixture(), appearance: 'standard' });
    const split = root.querySelector<HTMLButtonElement>('[data-vfo-split]')!;
    const dualWatch = root.querySelector<HTMLButtonElement>('[data-vfo-dual-watch]')!;
    for (const button of [split, dualWatch]) {
      expect(button.getAttribute('role')).toBe('switch');
      expect(button.getAttribute('data-indicator-style')).toBe('edge-left');
      expect(button.classList).toContain('v2-control-button');
    }
    expect(split.getAttribute('data-indicator-color')).toBe('cyan');
    expect(dualWatch.getAttribute('data-indicator-color')).toBe('cyan');
    expect(split.getAttribute('aria-label')).toBe('Split: on');
    expect(dualWatch.getAttribute('aria-label')).toBe('Dual watch: on');
  });

  it('renders TUNER, VOX and LOCK as edge-bar switches lit only when on', () => {
    const root = mountSurface({ viewModel: functionsFixture({
      atu: 'on', vox: true, dialLock: true,
    }), appearance: 'standard',
      onToggleTuner: () => {}, onToggleVox: () => {}, onToggleDialLock: () => {},
    });
    const tuner = root.querySelector<HTMLButtonElement>('[data-vfo-tuner]')!;
    const vox = root.querySelector<HTMLButtonElement>('[data-vfo-vox]')!;
    const lock = root.querySelector<HTMLButtonElement>('[data-vfo-lock]')!;
    for (const [button, label] of [[tuner, 'Tuner'], [vox, 'VOX'], [lock, 'Lock']] as const) {
      expect(button, label).not.toBeNull();
      expect(button.getAttribute('role')).toBe('switch');
      expect(button.getAttribute('data-indicator-style')).toBe('edge-left');
      expect(button.getAttribute('aria-checked')).toBe('true');
      expect(button.getAttribute('data-active')).toBe('true');
      expect(button.disabled).toBe(false);
    }
    expect(tuner.getAttribute('data-indicator-color')).toBe('red');
    expect(vox.getAttribute('data-indicator-color')).toBe('violet');
    expect(lock.getAttribute('data-indicator-color')).toBe('cyan');
    expect(tuner.getAttribute('aria-label')).toBe('Tuner: on');
  });

  it('keeps TUNER tuning known and checked with a distinct steady amber bar', () => {
    const root = mountSurface({ viewModel: functionsFixture({ atu: 'tuning' }), appearance: 'standard' });
    const tuner = root.querySelector<HTMLButtonElement>('[data-vfo-tuner]')!;
    expect(tuner.getAttribute('role')).toBe('switch');
    expect(tuner.getAttribute('aria-checked')).toBe('true');
    expect(tuner.getAttribute('data-active')).toBe('true');
    expect(tuner.getAttribute('data-indicator-color')).toBe('amber');
    expect(tuner.getAttribute('aria-label')).toBe('Tuner: tuning');
    const onRoot = mountSurface({ viewModel: functionsFixture({ atu: 'on' }), appearance: 'standard' });
    expect(onRoot.querySelector('[data-vfo-tuner]')!.getAttribute('data-indicator-color')).toBe('red');
  });

  it('unknown function readings stay unlit and disabled with bare names', () => {
    const root = mountSurface({ viewModel: functionsFixture({
      atu: 'unknown', vox: 'unknown', dialLock: 'unknown',
    }), appearance: 'standard' });
    const bareNames: Record<string, string> = {
      '[data-vfo-tuner]': 'Tuner', '[data-vfo-vox]': 'VOX', '[data-vfo-lock]': 'Lock',
    };
    for (const [selector, name] of Object.entries(bareNames)) {
      const button = root.querySelector<HTMLButtonElement>(selector)!;
      expect(button.getAttribute('data-active')).toBe('false');
      expect(button.getAttribute('role'), name).not.toBe('switch');
      expect(button.getAttribute('aria-checked')).toBeNull();
      expect(button.getAttribute('aria-label')).toBe(name);
      expect(button.disabled).toBe(true);
      expect(button.getAttribute('aria-describedby')).toBeTruthy();
      expect(root.querySelector(`#${button.getAttribute('aria-describedby')}`)?.textContent)
        .toBe('Not yet observed');
    }
    const bridge = root.querySelector('[data-instrument-bridge]')!;
    for (const button of Array.from(bridge.querySelectorAll<HTMLButtonElement>('button'))) {
      const name = button.getAttribute('aria-label') ?? button.textContent ?? '';
      expect(name, name).not.toContain('unknown');
    }
  });

  it('every bridge switch carries a valid aria-checked and no other key pretends to be one', () => {
    const lit = mountSurface({ viewModel: functionsFixture({
      atu: 'on', vox: true, dialLock: true,
    }), appearance: 'standard' });
    for (const button of Array.from(lit.querySelectorAll<HTMLButtonElement>('[data-instrument-bridge] button'))) {
      if (button.getAttribute('role') === 'switch') {
        expect(['true', 'false'], button.textContent ?? '').toContain(button.getAttribute('aria-checked'));
      } else if (button.getAttribute('role') !== 'radio') {
        expect(button.getAttribute('aria-checked'), button.textContent ?? '').toBeNull();
      }
    }
    const unknown = mountSurface({ viewModel: functionsFixture({
      atu: 'unknown', vox: 'unknown', dialLock: 'unknown',
    }), appearance: 'standard' });
    expect(unknown.querySelectorAll('[data-instrument-bridge] button[role="switch"]').length)
      .toBeLessThan(unknown.querySelectorAll('[data-instrument-bridge] button').length);
  });

  it('does not draw a function the profile lacks', () => {
    const base = topologyFixtures['2/main_sub'];
    const root = mountSurface({ viewModel: validateRadioViewModel({
      ...base,
      receiverIndicators: [receiverIndicator('MAIN'), receiverIndicator('SUB')],
    }), appearance: 'standard' });
    expect(root.querySelector('[data-vfo-tuner]')).toBeNull();
    expect(root.querySelector('[data-vfo-vox]')).toBeNull();
    expect(root.querySelector('[data-vfo-lock]')).toBeNull();
  });

  it('replaces the ACTIVE RECEIVER caption with the lit edge-bar key announcement', () => {
    const root = mountSurface({ viewModel: functionsFixture(), appearance: 'standard' });
    const bridge = root.querySelector('[data-instrument-bridge]')!;
    expect(bridge.querySelector('[data-testid="vfo-active-receiver"]')).toBeNull();
    const group = bridge.querySelector('[role="radiogroup"][aria-label="Active receiver"]')!;
    expect(group).not.toBeNull();
    const main = group.querySelector<HTMLButtonElement>('[data-active-receiver-segment="MAIN"]')!;
    expect(main.getAttribute('aria-checked')).toBe('true');
    expect(main.getAttribute('data-active')).toBe('true');
    expect(main.textContent?.trim()).toBe('MAIN');
  });

  it('keeps the A/B pair selector keys edge-lit, named and addressable', () => {
    const root = mountSurface({ viewModel: standardFixture('1/ab'), appearance: 'standard' });
    const keys = Array.from(root.querySelectorAll<HTMLButtonElement>('[data-standard-select-vfo]'));
    expect(keys.map((key) => key.getAttribute('data-standard-select-vfo'))).toEqual(['A', 'B']);
    for (const key of keys) {
      expect(key.classList).toContain('v2-control-button');
      expect(key.getAttribute('data-appearance')).toBeNull();
      expect(key.getAttribute('data-indicator-style')).toBe('edge-left');
      expect(key.getAttribute('data-indicator-color')).toBe('cyan');
      expect(key.getAttribute('aria-label')).toBe(`SELECT ${key.getAttribute('data-standard-select-vfo')}`);
    }
    expect(keys[0].getAttribute('data-active')).toBe('true');
    expect(keys[0].disabled).toBe(true);
    expect(keys[1].getAttribute('data-active')).toBe('false');
    expect(keys[1].disabled).toBe(false);
  });

  it('renders no SPLIT key when the split capability is absent', () => {
    const root = mountSurface({ viewModel: functionsFixture(), appearance: 'standard', hasSplit: false });
    expect(root.querySelector('[data-vfo-split]')).toBeNull();
    expect(root.querySelector('[data-vfo-dual-watch]')).not.toBeNull();
  });

  it('renders no fact-toggles container when both latching capabilities are absent', () => {
    const root = mountSurface({
      viewModel: functionsFixture(), hasSplit: false, hasDualWatch: false,
    });
    expect(root.querySelector('[data-vfo-split]')).toBeNull();
    expect(root.querySelector('[data-vfo-dual-watch]')).toBeNull();
    expect(root.querySelector('.fact-toggles')).toBeNull();
  });

  it('renders no DW key when the dual_watch capability is absent', () => {
    const root = mountSurface({ viewModel: functionsFixture(), appearance: 'standard', hasDualWatch: false });
    expect(root.querySelector('[data-vfo-dual-watch]')).toBeNull();
    expect(root.querySelector('[data-vfo-split]')).not.toBeNull();
  });

  it('removes the orphan selected appearance and bridge dot-lamp seats', () => {
    const sheet = readFileSync('src/components-v2/controls/control-button.css', 'utf8');
    const types = readFileSync('src/lib/Button/types.ts', 'utf8');
    const control = readFileSync('src/lib/Button/ControlButton.svelte', 'utf8');
    expect(sheet).not.toContain("data-appearance='selected'");
    expect(sheet).not.toContain('data-reserve-indicator');
    expect(types).not.toContain('ButtonAppearance');
    expect(control).not.toContain('data-appearance=');
    expect(control).not.toContain('data-reserve-indicator=');
    for (const path of [
      'src/semantic/VfoOperationGroup.svelte',
      'src/components-v2/vfo/ActiveReceiverToggle.svelte',
      'src/semantic/VfoSurface.svelte',
    ]) {
      const source = readFileSync(path, 'utf8');
      expect(source, path).toContain('<HardwareButton');
      expect(source, path).not.toContain('<ControlButton');
    }
  });
});
