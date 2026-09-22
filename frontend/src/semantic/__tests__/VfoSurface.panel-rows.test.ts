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
import { topologyFixtures, withTxAux, type TopologyFixtureId } from '../fixtures/topologies';

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
      dialLock: indicatorField(false),
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
      const mainRow = panel.querySelector('[data-vfo-row="main"]');
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
      const identity = panel.querySelector('[data-vfo-row="identity"]');
      expect(identity, 'panel has an identity row').not.toBeNull();
      expect(identity!.querySelector('.vfo-label')?.textContent?.trim()).toBeTruthy();
      const texts = statusTexts(identity!);
      expect(texts).toContain(dominantMode);
      expect(texts).toContain(dominantFilter);
      const mainRow = panel.querySelector('[data-vfo-row="main"]')!;
      expect(identity!.compareDocumentPosition(mainRow) & FOLLOWS,
        'identity row precedes the main row').toBeTruthy();
    }
  });

  it('the indicator row follows the main row and no longer carries mode or filter', () => {
    const root = mountSurface({ viewModel: fixture(), appearance: 'standard' });
    for (const panel of panels(root)) {
      const mainRow = panel.querySelector('[data-vfo-row="main"]')!;
      const indicatorRow = panel.querySelector('[data-vfo-row="chips"]');
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
      .toEqual(['MAIN', 'SUB', 'M↔S', 'M=S', 'Quick split', 'Quick dual watch', 'SPEAK']);
  });

  it('1/ab: the pair bridge grid keeps its controls and names', () => {
    const root = mountSurface({ viewModel: standardFixture('1/ab'), appearance: 'standard' });
    const bridge = root.querySelector('[data-instrument-bridge]');
    expect(bridge).not.toBeNull();
    const grid = bridge!.querySelectorAll('[data-testid="vfo-ops"]');
    expect(grid.length).toBe(1);
    const actions = Array.from(bridge!.querySelectorAll<HTMLButtonElement>('[data-dual-action]'));
    expect(actions.map((button) => button.getAttribute('aria-label') ?? button.textContent?.trim()))
      .toEqual(['A↔B', 'A=B', 'Quick split', 'Quick dual watch', 'SPEAK']);
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

const panelCss = styleBlock('src/components-v2/vfo/VfoPanel.svelte');
const surfaceCss = styleBlock('src/semantic/VfoSurface.svelte');
const layoutCss = styleBlock('src/components-v2/layout/RadioLayout.svelte');

describe('source pins: content-sized rows (MOR-2509 slice 1)', () => {
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

  it('the bridge operation column stacks fixed two-column group rows', () => {
    const ops = rulesFor(
      styleBlock('src/semantic/VfoOperationGroup.svelte'),
      ".vfo-ops[data-vfo-operation-appearance='standard']",
    ).join('\n');
    expect(ops).toMatch(/display:\s*flex;\s*flex-direction:\s*column/);
    expect(ops).toMatch(/gap:\s*var\(--vfo-ops-gap/);
    expect(rulesFor(
      styleBlock('src/semantic/VfoOperationGroup.svelte'),
      ".ops-row",
    ).join('\n')).toMatch(/grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/);
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

  it('every bridge key declares the 28px height floor and 12px text', () => {
    const ops = rulesFor(opsCss, ".vfo-ops[data-vfo-operation-appearance='standard']").join('\n');
    expect(ops).toMatch(/--btn-min-height:\s*28px/);
    expect(ops).toMatch(/--btn-font-size:\s*12px/);
    expect(rulesFor(segmentCss, '.active-receiver-toggle.embedded').join('\n'))
      .toMatch(/--btn-min-height:\s*28px/);
    expect(rulesFor(surfaceCss, "[data-vfo-appearance='standard'] .bridge .vfo-select").join('\n'))
      .toMatch(/min-height:\s*28px/);
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
      const value = css.match(new RegExp(`${declaration}:\\\\s*([^;]*);`))
        ?? css.match(new RegExp(`${declaration}:[ \\n]*([^;]*);`));
      expect(value, `${label} declares ${declaration}`).toBeTruthy();
      expect(value![1], `${label} reads the inset without a fallback literal`)
        .toMatch(FALLBACK_FREE);
    }
  });
});

describe('bridge hardware keys (MOR-2509 package C)', () => {
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
      // Selector keys are the family's flat fill look; every hardware key
      // must declare the hardware surface the family paints.
      if (button.closest('[data-standard-select-vfo], [role="radiogroup"]') === null) {
        expect(button.getAttribute('data-surface'), button.textContent ?? '').toBe('hardware');
      }
    }
  });

  it('orders the groups selector, momentary actions, latching toggles, divider, radio functions', () => {
    const root = mountSurface({ viewModel: functionsFixture(), appearance: 'standard' });
    const order = Array.from(root.querySelector('[data-instrument-bridge]')!.querySelectorAll<HTMLElement>(
      '[data-active-receiver-segment], [data-dual-action], [data-vfo-split], [data-vfo-dual-watch],'
        + ' .bridge-divider, [data-vfo-tuner], [data-vfo-vox], [data-vfo-lock]',
    )).map(keyName);
    expect(order).toEqual([
      'main', 'sub', 'swap', 'equalize', 'quick-split', 'quick-dual-watch', 'speak',
      'split', 'dual-watch', 'divider', 'tuner', 'vox', 'lock',
    ]);
  });

  it('keeps the SPLIT and DW latching keys as dot-lamp switches with unchanged semantics', () => {
    const root = mountSurface({ viewModel: functionsFixture(), appearance: 'standard' });
    const split = root.querySelector<HTMLButtonElement>('[data-vfo-split]')!;
    const dualWatch = root.querySelector<HTMLButtonElement>('[data-vfo-dual-watch]')!;
    for (const button of [split, dualWatch]) {
      expect(button.getAttribute('role')).toBe('switch');
      expect(button.getAttribute('data-indicator-style')).toBe('dot');
      expect(button.classList).toContain('v2-control-button');
    }
    expect(split.getAttribute('data-indicator-color')).toBe('cyan');
    expect(dualWatch.getAttribute('data-indicator-color')).toBe('green');
    expect(split.getAttribute('aria-label')).toBe('Split: on');
    expect(dualWatch.getAttribute('aria-label')).toBe('Dual watch: on');
  });

  it('renders TUNER, VOX and LOCK as dot-lamp switches with lamps lit only when on', () => {
    const root = mountSurface({ viewModel: functionsFixture({
      atu: 'on', vox: true, dialLock: true,
    }), appearance: 'standard' });
    const tuner = root.querySelector<HTMLButtonElement>('[data-vfo-tuner]')!;
    const vox = root.querySelector<HTMLButtonElement>('[data-vfo-vox]')!;
    const lock = root.querySelector<HTMLButtonElement>('[data-vfo-lock]')!;
    for (const [button, label] of [[tuner, 'Tuner'], [vox, 'VOX'], [lock, 'Lock']] as const) {
      expect(button, label).not.toBeNull();
      expect(button.getAttribute('role')).toBe('switch');
      expect(button.getAttribute('data-indicator-style')).toBe('dot');
      expect(button.getAttribute('aria-checked')).toBe('true');
      expect(button.getAttribute('data-active')).toBe('true');
      expect(button.disabled).toBe(false);
    }
    expect(tuner.getAttribute('data-indicator-color')).toBe('red');
    expect(vox.getAttribute('data-indicator-color')).toBe('amber');
    expect(lock.getAttribute('data-indicator-color')).toBe('cyan');
    expect(tuner.getAttribute('aria-label')).toBe('Tuner: on');
  });

  it('distinguishes TUNER tuning from on by lamp treatment, not by blinking', () => {
    const root = mountSurface({ viewModel: functionsFixture({ atu: 'tuning' }), appearance: 'standard' });
    const tuner = root.querySelector<HTMLButtonElement>('[data-vfo-tuner]')!;
    expect(tuner.getAttribute('aria-checked')).toBe('mixed');
    expect(tuner.getAttribute('data-active')).toBe('true');
    expect(tuner.getAttribute('data-indicator-color')).toBe('orange');
    expect(tuner.getAttribute('aria-label')).toBe('Tuner: tuning');
    const onRoot = mountSurface({ viewModel: functionsFixture({ atu: 'on' }), appearance: 'standard' });
    expect(onRoot.querySelector('[data-vfo-tuner]')!.getAttribute('data-indicator-color')).toBe('red');
  });

  it('unknown function readings stay unlit and disabled, never fabricated', () => {
    const root = mountSurface({ viewModel: functionsFixture({
      atu: 'unknown', vox: 'unknown', dialLock: 'unknown',
    }), appearance: 'standard' });
    for (const selector of ['[data-vfo-tuner]', '[data-vfo-vox]', '[data-vfo-lock]']) {
      const button = root.querySelector<HTMLButtonElement>(selector)!;
      expect(button.getAttribute('data-active')).toBe('false');
      expect(button.getAttribute('aria-checked')).toBe('mixed');
      expect(button.disabled).toBe(true);
      expect(button.getAttribute('aria-describedby')).toBeTruthy();
      expect(root.querySelector(`#${button.getAttribute('aria-describedby')}`)?.textContent)
        .toBe('Not yet observed');
    }
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

  it('replaces the ACTIVE RECEIVER caption with the filled selector key announcement', () => {
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

  it('keeps the A/B pair selector keys filled, named and addressable', () => {
    const root = mountSurface({ viewModel: standardFixture('1/ab'), appearance: 'standard' });
    const keys = Array.from(root.querySelectorAll<HTMLButtonElement>('[data-standard-select-vfo]'));
    expect(keys.map((key) => key.getAttribute('data-standard-select-vfo'))).toEqual(['A', 'B']);
    for (const key of keys) {
      expect(key.classList).toContain('v2-control-button');
      expect(key.getAttribute('aria-label')).toBe(`SELECT ${key.getAttribute('data-standard-select-vfo')}`);
    }
    expect(keys[0].getAttribute('data-active')).toBe('true');
    expect(keys[0].disabled).toBe(true);
    expect(keys[1].getAttribute('data-active')).toBe('false');
    expect(keys[1].disabled).toBe(false);
  });

  it('reserves the lamp slot on lampless keys: the shared sheet aligns both paddings', () => {
    const sheet = readFileSync('src/components-v2/controls/control-button.css', 'utf8');
    const dotRule = sheet.match(
      /\.v2-control-button\[data-indicator-style='dot'\],\s*\n?\.v2-control-button\[data-reserve-indicator='true'\]\s*\{[^}]*\}/,
    );
    expect(dotRule, "the dot padding rule also selects [data-reserve-indicator='true']").toBeTruthy();
    expect(dotRule![0]).toMatch(/padding-left:\s*calc\(var\(--indicator-dot-reserve\)/);
  });
});
