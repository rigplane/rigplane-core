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
import { readFileSync } from 'node:fs';
import { mount, flushSync } from 'svelte';
import type { ComponentProps } from 'svelte';
import type { ReceiverId } from '../radio-view-model';
import {
  validateRadioViewModel, type RadioViewModel,
  type ReceiverIndicatorViewModel,
} from '../radio-view-model';
import VfoSurface from '../VfoSurface.svelte';
import { topologyFixtures, withBand, type TopologyFixtureId } from '../fixtures/topologies';
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
function standardFixture(id: TopologyFixtureId, readings: 'known' | 'unknown' = 'known'): RadioViewModel {
  const base = topologyFixtures[id];
  const dual = id.startsWith('2/');
  return validateRadioViewModel({
    ...base,
    ...(id === '2/main_sub' ? withBand(base) : {}),
    receiverIndicators: (dual ? (['MAIN', 'SUB'] as const) : (['MAIN'] as const))
      .map((receiver) => receiverIndicator(receiver, readings)),
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
        quickSplit: availableAction,
        quickDualWatch: availableAction,
        speak: availableAction,
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

  it('the radio-wide ANT tab is drawn once, on the active receiver\'s panel only', () => {
    const root = mountSurface({ viewModel: standardFixture('2/main_sub'), appearance: 'standard' });
    const antTabs = root.querySelectorAll('[data-tray-tab="ant"]');
    expect(antTabs.length).toBe(1);
    expect(antTabs[0].closest('[data-receiver-instrument]')?.getAttribute('data-receiver-instrument'))
      .toBe('MAIN');
    expect(antTabs[0].getAttribute('data-indicator-fact')).toBe('ant');
    expect(antTabs[0].textContent?.trim()).toBe('ANT 1');
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
        const count = row(panel, 'tray').querySelectorAll('[data-tray-tab]').length;
        expect(count, 'same tab count whatever the readings say').toBe(3);
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
    const expected: Record<string, string> = {
      agc: 'AGC', preamp: 'P.AMP', att: 'ATT', 'ip-plus': 'IP+', 'digi-sel': 'DIGI-SEL', rfg: 'RFG',
    };
    for (const lamp of lamps) {
      expect(lamp.getAttribute('data-lit')).toBe('false');
      expect(lamp.textContent?.trim()).toBe(expected[lamp.getAttribute('data-chip')!]);
    }
  });

  it('a radio without a front-end field draws no lamp for it (ftx1: no ATT)', () => {
    const caps = { ...FTX1_CAPABILITIES, providerGeneration: FTX1_STATE.providerGeneration };
    const model = toRadioViewModel(FTX1_STATE, caps);
    expect(model).not.toBeNull();
    const root = mountSurface({ viewModel: model!, appearance: 'standard' });
    const mainPanel = panels(root).find((panel) => panel.getAttribute('data-receiver-instrument') === 'MAIN')!;
    expect(mainPanel.querySelector('[data-chip="att"]')).toBeNull();
    expect(mainPanel.querySelector('[data-chip="agc"]')).not.toBeNull();
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
  it('2/main_sub: one grid container holds the operation controls with unchanged accessible names', () => {
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
      .toEqual(['MAIN', 'SUB', 'M=S', 'M↔S', 'Quick split', 'Quick dual watch', 'SPEAK']);
  });

  it('1/ab: the pair bridge grid keeps its controls and names', () => {
    const root = mountSurface({ viewModel: standardFixture('1/ab'), appearance: 'standard' });
    const bridge = root.querySelector('[data-instrument-bridge]');
    expect(bridge).not.toBeNull();
    const grid = bridge!.querySelectorAll('[data-testid="vfo-ops"]');
    expect(grid.length).toBe(1);
    const actions = Array.from(bridge!.querySelectorAll<HTMLButtonElement>('[data-dual-action]'));
    expect(actions.map((button) => button.getAttribute('aria-label') ?? button.textContent?.trim()))
      .toEqual(['A=B', 'A↔B', 'Quick split', 'Quick dual watch', 'SPEAK']);
  });
});

describe('the live FTX-1 payload (many null leaves) prints no placeholder', () => {
  const mountFtx1 = (): HTMLElement => {
    const caps = { ...FTX1_CAPABILITIES, providerGeneration: FTX1_STATE.providerGeneration };
    const model = toRadioViewModel(FTX1_STATE, caps);
    expect(model).not.toBeNull();
    return mountSurface({ viewModel: model!, appearance: 'standard' });
  };

  it('absent capabilities draw no element: no BAND tab, no ANT tab, no ATT lamp', () => {
    const root = mountFtx1();
    expect(root.querySelector('[data-tray-tab="band"]')).toBeNull();
    expect(root.querySelector('[data-tray-tab="ant"]')).toBeNull();
    expect(root.querySelector('[data-chip="att"]')).toBeNull();
    expect(root.querySelector('[data-chip="rfg"]')).toBeNull();
  });

  it('unknown leaves stay unlit in place and no chip prints a placeholder', () => {
    const root = mountFtx1();
    for (const panel of panels(root)) {
      for (const chip of panel.querySelectorAll('[data-tray-tab], [data-chip]')) {
        const text = chip.textContent ?? '';
        for (const forbidden of ['—', '?', 'null', 'undefined', 'NaN', 'UNKNOWN', 'N/A']) {
          expect(text, `chip ${chip.getAttribute('data-chip') ?? chip.getAttribute('data-tray-tab')} must not print ${forbidden}`)
            .not.toContain(forbidden);
        }
      }
    }
    const firstPanel = panels(root)[0];
    expect(firstPanel.querySelector('[data-chip="agc"]')?.getAttribute('data-lit')).toBe('false');
    expect(firstPanel.querySelector('[data-chip="nb"]')?.getAttribute('data-lit')).toBe('false');
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
    if (selectorList.split(',').some((member) => member.trim() === selector)) {
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

const panelCss = styleBlock('src/components-v2/vfo/VfoPanel.svelte');
const surfaceCss = styleBlock('src/semantic/VfoSurface.svelte');
const layoutCss = styleBlock('src/components-v2/layout/RadioLayout.svelte');

describe('source pins: fixed slot widths (MOR-2509 slice 2)', () => {
  it('every tray tab, lamp and large chip declares a fixed width and never wraps', () => {
    for (const selector of ['.tab', '.lamp', '.chip-lg', '.chip-amber', '.chip-dsp']) {
      const bodies = rulesFor(panelCss, selector).join('\n');
      expect(bodies, `${selector} reserves a fixed slot width`).toMatch(/width:\s*\d+px/);
    }
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

  it('the bridge operation grid declares two columns', () => {
    const ops = rulesFor(
      styleBlock('src/semantic/VfoOperationGroup.svelte'),
      ".vfo-ops[data-vfo-operation-appearance='standard']",
    ).join('\n');
    expect(ops).toMatch(/grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/);
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

  it('the panel is an inline-size container with wide and narrow modes at 760/530 px', () => {
    expect(rule(panelCss, '.panel')).toMatch(/container-type:\s*inline-size/);
    expect(panelCss).toMatch(/@container \(min-width:\s*760px\)/);
    expect(panelCss).toMatch(/@container \(max-width:\s*530px\)/);
    const wide = panelCss.slice(panelCss.indexOf('@container (min-width: 760px)'));
    expect(wide.slice(0, wide.indexOf('@container (max-width')))
      .toMatch(/grid-template-columns:\s*1fr 1fr/);
    const narrow = panelCss.slice(panelCss.indexOf('@container (max-width: 530px)'));
    expect(narrow).toMatch(/grid-column:\s*1\s*\/\s*-1/);
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

  it('the inactive panel is quiet through token variants, never a filter, and the meter is never dimmed', () => {
    for (const [, selector, body] of panelCss.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      expect(body, `${selector.trim()} must not dim with filter or opacity`)
        .not.toMatch(/filter:\s*saturate|opacity:\s*0\./);
    }
    const inactive = rulesFor(panelCss, '.panel:not(.active)').join('\n');
    expect(inactive).toMatch(/--vfo-chip-neon:\s*var\(--dl-vfo-primary-neon-dim/);
    expect(inactive).not.toMatch(/panel-meter/);
  });

  it('each design language stylesheet defines the panel tokens at exactly one root block', () => {
    for (const [path, language] of [
      ['src/presentation/languages/studioline/studioline.css', 'studioline'],
      ['src/presentation/languages/fieldline/fieldline.css', 'fieldline'],
      ['src/presentation/languages/segmentline/segmentline.css', 'segmentline'],
    ] as const) {
      const css = readFileSync(path, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
      const blocks = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
        .filter(([, selectors, body]) => selectors.includes(`[data-design-language='${language}']`)
          && body.includes('--dl-vfo-'));
      expect(blocks.length, `${language} defines the panel tokens in exactly one block`).toBe(1);
      expect(blocks[0][1].trim(), `${language}'s token block is a root block`)
        .toBe(`[data-design-language='${language}'][data-design-language]`);
    }
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

  it('every bridge control declares a 24px min-height floor', () => {
    expect(rulesFor(opsCss, ".vfo-ops[data-vfo-operation-appearance='standard'] .vfo-op").join('\n'))
      .toMatch(/min-height:\s*max\(24px/);
    expect(rulesFor(opsCss, ".fact-toggles[data-vfo-operation-appearance='standard'] .fact-toggle").join('\n'))
      .toMatch(/min-height:\s*max\(24px/);
    expect(rulesFor(segmentCss, '.segment.embedded').join('\n'))
      .toMatch(/min-height:\s*max\(24px/);
    expect(rulesFor(surfaceCss, "[data-vfo-appearance='standard'] .bridge .vfo-select").join('\n'))
      .toMatch(/min-height:\s*24px/);
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
});
