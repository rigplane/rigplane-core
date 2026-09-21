/**
 * MOR-2509 slice 1 — structural pins for the Standard-skin VFO panel
 * skeleton: three content-sized rows per panel (identity, main, indicators)
 * and a compact two-column bridge grid.
 *
 * jsdom cannot measure pixels, so the layout is pinned the way the repo's
 * stylesheet tests pin cascades (see
 * `presentation/languages/fieldline/__tests__/stylesheet.test.ts`): the DOM
 * tests below pin row membership and order for the real `VfoSurface` mount,
 * and the source tests pin the declarations that keep those rows
 * content-sized (wrapping, no fixed row heights, no deck clipping). Pixel
 * behaviour at the ticket's viewport matrix is measured separately.
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
import { topologyFixtures, type TopologyFixtureId } from '../fixtures/topologies';

const indicatorField = <T>(value: T) => ({
  reading: { status: 'known' as const, value },
  availability: { structural: true, operational: true },
});

const absentAction = { structural: false, operational: false };
const availableAction = { structural: true, operational: true };

function receiverIndicator(receiver: ReceiverId): ReceiverIndicatorViewModel {
  return {
    receiver,
    availability: { structural: true, operational: true },
    sMeter: {
      ...indicatorField(receiver === 'MAIN' ? 0 : -31),
      source: {
        providerGeneration: 1, scope: 'receiver', receiver,
        path: receiver === 'MAIN' ? 'main.sMeter' : 'sub.sMeter',
      },
    },
    bandwidthHz: indicatorField(receiver === 'MAIN' ? 2400 : 500),
    agcMode: indicatorField(receiver === 'MAIN' ? 0 : 2),
    nbActive: indicatorField(receiver === 'SUB'),
    nrActive: indicatorField(receiver === 'MAIN'),
    notchMode: indicatorField<'off' | 'auto' | 'manual'>(receiver === 'MAIN' ? 'off' : 'auto'),
    attenuator: indicatorField(receiver === 'MAIN' ? 0 : 12),
    preamp: indicatorField(receiver === 'MAIN' ? 0 : 2),
    rfGain: indicatorField(receiver === 'MAIN' ? 0 : 0.75),
    digiSel: indicatorField(receiver === 'SUB'),
    ipPlus: indicatorField(receiver === 'MAIN'),
  };
}

/** Fixtures with receiver indicators plus a full radio-wide action block
 * (single-receiver schemes get no MAIN/SUB selector actions). */
function standardFixture(id: TopologyFixtureId): RadioViewModel {
  const base = topologyFixtures[id];
  const dual = id.startsWith('2/');
  return validateRadioViewModel({
    ...base,
    receiverIndicators: (dual ? (['MAIN', 'SUB'] as const) : (['MAIN'] as const))
      .map(receiverIndicator),
    radioWideIndicators: {
      rfState: 'receiving',
      antenna: indicatorField(1),
      atu: indicatorField('off'),
      ritActive: indicatorField(false),
      ritOffset: indicatorField(0),
      xitActive: indicatorField(false),
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

function statusTexts(row: Element): string[] {
  return Array.from(row.querySelectorAll('.v2-status-indicator'))
    .map((el) => el.textContent?.trim() ?? '');
}

const FOLLOWS = Node.DOCUMENT_POSITION_FOLLOWING;

describe.each(['2/main_sub', '1/ab'] as const)('standard panel rows (%s)', (id) => {
  const fixture = () => standardFixture(id);
  const dominantMode = id === '2/main_sub' ? 'USB' : 'LSB';
  const dominantFilter = id === '2/main_sub' ? 'WIDE' : 'NARROW';

  it('the frequency readout and the S-meter are descendants of the same main row, frequency first', () => {
    const root = mountSurface({ viewModel: fixture(), appearance: 'standard' });
    expect(panels(root).length).toBeGreaterThan(0);
    for (const panel of panels(root)) {
      const mainRow = panel.querySelector('.display-row');
      expect(mainRow, 'panel has a main row').not.toBeNull();
      const freq = mainRow!.querySelector('[data-vfo-freq]');
      expect(freq, 'frequency readout inside the main row').not.toBeNull();
      const meter = panel.querySelector('[data-testid="receiver-s-meter"]');
      if (meter !== null) {
        expect(mainRow!.contains(meter), 'meter inside the same main row').toBe(true);
        expect(freq!.compareDocumentPosition(meter) & FOLLOWS,
          'meter after the frequency readout in DOM order').toBeTruthy();
      }
    }
  });

  it('the identity row carries name, mode and filter, ahead of the main row', () => {
    const root = mountSurface({ viewModel: fixture(), appearance: 'standard' });
    for (const panel of panels(root)) {
      const identity = panel.querySelector('.panel-identity');
      expect(identity, 'panel has an identity row').not.toBeNull();
      expect(identity!.querySelector('.vfo-label')?.textContent?.trim()).toBeTruthy();
      const texts = statusTexts(identity!);
      expect(texts).toContain(dominantMode);
      expect(texts).toContain(dominantFilter);
      const mainRow = panel.querySelector('.display-row')!;
      expect(identity!.compareDocumentPosition(mainRow) & FOLLOWS,
        'identity row precedes the main row').toBeTruthy();
    }
  });

  it('the indicator row follows the main row and no longer carries mode or filter', () => {
    const root = mountSurface({ viewModel: fixture(), appearance: 'standard' });
    for (const panel of panels(root)) {
      const mainRow = panel.querySelector('.display-row')!;
      const indicatorRow = panel.querySelector('.control-strip');
      expect(indicatorRow, 'panel has an indicator row').not.toBeNull();
      expect(mainRow.compareDocumentPosition(indicatorRow!) & FOLLOWS,
        'indicator row follows the main row').toBeTruthy();
      const texts = statusTexts(indicatorRow!);
      expect(texts, 'mode lives in the identity row, not the indicator row')
        .not.toContain(dominantMode);
      expect(texts, 'filter lives in the identity row, not the indicator row')
        .not.toContain(dominantFilter);
    }
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

function styleBlock(path: string): string {
  const source = readFileSync(path, 'utf8');
  const match = source.match(/<style>([\s\S]*)<\/style>/);
  expect(match, `${path} has a style block`).toBeTruthy();
  // Comments are stripped first, the way `fieldline/__tests__/stylesheet.test.ts`
  // strips them: pins are about declarations, not prose.
  return match![1].replace(/\/\*[\s\S]*?\*\//g, '');
}

/** Bodies of every rule whose selector list contains `selector` verbatim
 * (media blocks included; a comma member must match exactly, so
 * `.x > .receiver-deck` never satisfies a query for `.receiver-deck`). */
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

describe('source pins: content-sized rows (MOR-2509 slice 1)', () => {
  const panelCss = styleBlock('src/components-v2/vfo/VfoPanel.svelte');
  const surfaceCss = styleBlock('src/semantic/VfoSurface.svelte');
  const layoutCss = styleBlock('src/components-v2/layout/RadioLayout.svelte');

  it('the VfoPanel control strip declares wrapping and never hides overflow', () => {
    const strips = rulesFor(panelCss, '.control-strip');
    expect(strips.join('\n')).toMatch(/flex-wrap:\s*wrap/);
    for (const strip of strips) {
      expect(strip, 'no clipping on any control-strip rule').not.toMatch(/overflow:\s*hidden/);
      expect(strip).not.toMatch(/white-space:\s*nowrap/);
    }
  });

  it('the VfoPanel rows are content-sized: no fixed row-height token sizes the panel grid', () => {
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
