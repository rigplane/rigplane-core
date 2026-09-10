import { test, expect, type Locator, type Page } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
import { fixtureById } from '../../../fixtures/catalog';
import { mockCapabilities, mockInfo, mockState } from './fixtures';

type TopologyId = 'topology-1-single' | 'topology-2-main-sub';

const METER_PATHS = ['main.sMeter', 'sub.sMeter', 'powerMeter', 'swrMeter',
  'alcMeter', 'compMeter', 'vdMeter', 'idMeter'] as const;
const unobservedMeters = (paths: readonly string[]) => Object.fromEntries(paths.map(path => [path, {
  storePath: path, observed: false, freshness: 'unknown' as const, availability: 'missing' as const,
}]));

function catalogFixture(id: TopologyId, known = true) {
  const fixture = fixtureById(id);
  if (!fixture) throw new Error(`Missing catalog fixture: ${id}`);
  const topologyState = fixture.state();
  const topologyCaps = fixture.caps();
  if (!topologyState || !topologyCaps) throw new Error(`Incomplete catalog fixture: ${id}`);
  const topologyRecord = topologyState as unknown as Record<string, unknown>;
  const state = {
    ...structuredClone(mockState),
    ...structuredClone(topologyState),
    main: {
      ...structuredClone(mockState.main),
      ...structuredClone(topologyState.main),
    },
  };
  if (Object.prototype.hasOwnProperty.call(topologyRecord, 'sub') && topologyState.sub) {
    state.sub = { ...structuredClone(mockState.sub), ...structuredClone(topologyState.sub) };
  } else {
    delete (state as unknown as { sub?: unknown }).sub;
  }
  if (!known) state.fieldStatus = unobservedMeters(METER_PATHS.filter(path =>
    Object.prototype.hasOwnProperty.call(topologyState.fieldStatus, path)));
  return {
    state,
    caps: { ...structuredClone(mockCapabilities), ...structuredClone(topologyCaps) },
  };
}

// Geometry-bearing values from the MOR-1413 IC-7300 observation (18f7e459).
// Keep provider/session metadata out of this portable fixture. Unknown cases
// retain raw values but withhold observation evidence, as the production smoke does.
function fixture(known: boolean) {
  const state = structuredClone(mockState);
  Object.assign(state, { powerLevel: 0, powerMeter: 0, swrMeter: 0, alcMeter: 0,
    compMeter: 0, vdMeter: 138, idMeter: 0, vfoSelect: 'A',
    sub: null,
    scopeControls: { receiver: 0, dual: false, mode: 0, span: 1, edge: 1,
      speed: 0, refDb: 0, hold: false, duringTx: true, centerType: 2,
      vbwNarrow: false, rbw: 0,
      fixedEdge: { rangeIndex: 1, edge: 1, startHz: 500000, endHz: 1500000 } } });
  Object.assign(state.main, { freqHz: 14035720, mode: 'CW', filter: 3, filterWidth: 150,
    sMeter: -54, afLevel: 0.2, rfGain: 0.6705882352941176, preamp: 2, nb: true,
    nrLevel: 72, nbLevel: 137, activeSlot: 'A',
    unselectedVfo: { freqHz: 14332000, mode: 'USB', filterNum: 1, dataMode: 0 } });
  state.updatedAt = new Date().toISOString();
  const fields: Record<string, unknown> = {};
  function observe(obj: object, prefix = '') {
    for (const [key, value] of Object.entries(obj)) {
      const path = prefix ? `${prefix}.${key}` : key;
      // Synthetic observation provenance for this fixture, not a radio measurement.
      fields[path] = { observed: true, freshness: 'fresh', availability: 'available', storePath: path, lastObservedMonotonic: 0 };
      if (value && typeof value === 'object') observe(value, path);
    }
  }
  if (known) observe(state);
  else Object.assign(fields, unobservedMeters(METER_PATHS.filter(path => path !== 'sub.sMeter')));
  // IC-7300 reports selected/unselected readback without proving A/B identity.
  delete fields['main.activeSlot'];
  delete fields.active;
  state.fieldStatus = fields as typeof state.fieldStatus;
  const caps = { ...structuredClone(mockCapabilities), model: 'IC-7300', receivers: 1,
    vfoScheme: 'ab', vfoReadback: 'selected_unselected',
    audioFftAvailable: true,
    capabilities: ['af_level', 'agc', 'attenuator', 'audio', 'band_edge', 'break_in',
      'bsr', 'compressor', 'cw', 'data_mode', 'dial_lock', 'filter_shape', 'filter_width',
      'ip_plus', 'meters', 'monitor', 'nb', 'notch', 'nr', 'pbt', 'power_control',
      'preamp', 'repeater_tone', 'rf_gain', 'rit', 'scan', 'scope', 'speech', 'split',
      'squelch', 'ssb_tx_bw', 'system_settings', 'tsql', 'tuner', 'tuning_step',
      'twin_peak', 'tx', 'vfo_equalize', 'vfo_swap', 'vox', 'xfc', 'xit'],
    meterCalibrations: { s_meter: [
      { raw: 0, actual: -54, label: 'S0' }, { raw: 120, actual: 0, label: 'S9' },
      { raw: 241, actual: 60, label: 'S9+60' },
    ] } };
  return { state, caps };
}

interface BootOptions {
  height?: number;
  theme?: 'nord' | 'github-light';
  locale?: 'en-US' | 'ru-RU';
  extraCapabilities?: string[];
  absoluteVfoPair?: boolean;
  /** The QA-only skin `?layout=flagship-probe` selects
   *  (`lib/stores/qa-cockpit-override.ts`). It is not a `CanonicalLayoutMode`,
   *  so the workspace `layout` this helper writes cannot carry it. */
  qaLayout?: 'flagship-probe';
}

async function boot(page: Page, layout: string, width: number, known: boolean, language = 'studioline', productionUnknown = false, topology?: TopologyId, options: BootOptions = {}) {
  const { state, caps } = topology ? catalogFixture(topology, known) : productionUnknown
    ? { state: structuredClone(mockState), caps: structuredClone(mockCapabilities) } : fixture(known);
  if (options.absoluteVfoPair) {
    const observed = (storePath: string) => ({ storePath, observed: true as const,
      freshness: 'fresh' as const, availability: 'available' as const, lastObservedMonotonic: 0 });
    state.main.vfoA = { freqHz: state.main.freqHz, mode: state.main.mode,
      filterNum: state.main.filter, dataMode: 0 };
    state.main.vfoB = structuredClone(state.main.unselectedVfo!);
    Object.assign(state, { txAntenna: 1, ritOn: false, ritTx: false, ritFreq: 0 });
    Object.assign(caps, { antennas: 1 });
    Object.assign(state.fieldStatus!, {
      'main.activeSlot': observed('main.activeSlot'),
      ...Object.fromEntries(['txAntenna', 'tunerStatus', 'ritOn', 'ritTx', 'ritFreq']
        .map(path => [path, observed(path)])),
      ...Object.fromEntries(['freqHz', 'mode', 'filterNum', 'dataMode'].flatMap(leaf =>
        (['vfoA', 'vfoB'] as const).map(slot => {
          const path = `main.${slot}.${leaf}`;
          return [path, observed(path)];
        }))),
    });
  }
  if (options.extraCapabilities) {
    const tags = (caps as unknown as { capabilities: string[] }).capabilities;
    (caps as unknown as { capabilities: string[] }).capabilities = [...new Set([...tags, ...options.extraCapabilities])];
  }
  const height = options.height ?? (width === 900 ? 900 : 1000);
  const theme = options.theme ?? (width === 900 ? 'github-light' : 'nord');
  const locale = options.locale ?? (width === 900 ? 'ru-RU' : 'en-US');
  await page.setViewportSize({ width, height });
  await page.addInitScript(({ state, layout, language, theme, locale }) => {
    localStorage.setItem('rigplane:workspace', JSON.stringify({ version: 1, layout,
      designLanguage: language, theme }));
    localStorage.setItem('rigplane.i18n.locale', locale);
    const commands: unknown[] = [];
    Object.assign(window, { geometryCommands: commands });
    // State registration only: no outgoing radio/HTTP command is forwarded.
    class Socket extends EventTarget {
      static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
      readyState = 0; binaryType = 'arraybuffer'; bufferedAmount = 0;
      onopen: ((e: Event) => void) | null = null;
      onmessage: ((e: MessageEvent) => void) | null = null;
      onclose: ((e: Event) => void) | null = null;
      constructor(public url: string) {
        super();
        queueMicrotask(() => {
          this.readyState = 1;
          const open = new Event('open'); this.dispatchEvent(open); this.onopen?.(open);
          if (new URL(url, location.href).pathname === '/api/v1/ws') {
            const emit = (next: typeof state) => {
              const e = new MessageEvent('message', { data: JSON.stringify({ type: 'state_update',
                data: { type: 'full', data: next, revision: next.revision,
                  stateRevision: next.stateRevision, freshnessRevision: next.freshnessRevision,
                  observationSeq: next.observationSeq, stateContractVersion: next.stateContractVersion,
                  providerGeneration: next.providerGeneration } }) });
              this.dispatchEvent(e); this.onmessage?.(e);
            };
            emit(state);
            window.addEventListener('geometry-state', e => emit((e as CustomEvent<typeof state>).detail));
          }
        });
      }
      send(data: string) { commands.push(JSON.parse(data)); }
      close() { this.readyState = 3; const e = new Event('close'); this.dispatchEvent(e); this.onclose?.(e); }
    }
    Object.assign(window, { WebSocket: Socket });
  }, { state, layout, language, theme, locale });
  await page.route('**/api/**', route => {
    const name = new URL(route.request().url()).pathname.split('/').pop();
    const body = name === 'state' ? state : name === 'capabilities' ? caps : name === 'info' ? mockInfo
      : name === 'managed-transmit' ? { schemaVersion: 1, sampledAt: new Date().toISOString(),
        managedTransmit: { status: 'available', intent: { kind: 'rx' }, releaseRequired: false,
          lastError: null, lastActuation: null, abortErrors: [],
          tot: { configuredSeconds: 180, active: false, remainingMs: null, expiresAt: null } },
        txObservation: { observedPtt: 'off' } } : {};
    return route.fulfill({ json: body });
  });
  const qaLayout = options.qaLayout;
  await page.goto(`/?locale=${locale}${qaLayout ? `&layout=${qaLayout}` : ''}`, { waitUntil: 'networkidle' });
  const shell = qaLayout ? '[data-testid="flagship-geometry-probe"]'
    : width <= 640 ? '.m-layout, .m-landscape'
    : layout.startsWith('lcd') ? '.lcd-layout' : '.desktop-control-face';
  await expect(page.locator(shell).first()).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
}

async function focusWithoutActivation(page: Page, control: Locator) {
  // Focusing a disabled last button would leave focus on Unkey; re-focusing
  // the already-focused element would then fail to exercise scroll recovery.
  await page.evaluate(() => { if (document.activeElement instanceof HTMLElement) document.activeElement.blur(); });
  await control.focus();
  await expect(control).toBeInViewport();
  expect(await control.evaluate(e => {
    const b = e.getBoundingClientRect();
    const hit = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2);
    return document.activeElement === e && (hit === e || e.contains(hit));
  })).toBe(true);
}

const STANDARD_WIDTHS = [1440, 1200, 1024, 900] as const;
const STANDARD_TOPOLOGIES = ['topology-1-single', 'topology-2-main-sub'] as const;
const STANDARD_SHORT_CASES = [
  ['studioline', 'nord'], ['studioline', 'github-light'],
  ['fieldline', 'nord'], ['fieldline', 'github-light'],
] as const;
const ALL_STRUCTURAL_ACTION_CAPS = ['vfo_equalize', 'vfo_swap', 'speech'];

async function standardGeometry(page: Page) {
  const selectors = {
    root: '.desktop-control-face.standard-face',
    status: '.desktop-control-face.standard-face > .status-bar',
    receiver: '.desktop-control-face.standard-face [data-zone-id="receiver-deck"]',
    left: '.desktop-control-face.standard-face .desktop-controls-left',
    center: '.desktop-control-face.standard-face .desktop-controls-center',
    right: '.desktop-control-face.standard-face .desktop-controls-right',
    bottom: '.desktop-control-face.standard-face .standard-bottom-dock',
    meters: '.desktop-control-face.standard-face [data-panel-id="semantic-meters"]',
  } as const;
  const entries = await Promise.all(Object.entries(selectors).map(async ([name, selector]) => {
    const target = page.locator(selector).first();
    await expect(target, `${name} is painted`).toBeVisible();
    return [name, await target.evaluate(element => element.getBoundingClientRect().toJSON())] as const;
  }));
  const instruments = await page.locator('.standard-face .receiver-instrument').evaluateAll(elements =>
    elements.map(instrument => {
      const box = instrument.getBoundingClientRect();
      const meter = instrument.querySelector('[data-testid="receiver-s-meter"]')?.getBoundingClientRect();
      const primary = instrument.querySelector('.panel-body [data-vfo-freq]');
      const secondary = instrument.querySelector('.slot-choice .vfo-freq');
      const textBox = (element: Element | null) => {
        if (!element) return null;
        const range = document.createRange();
        range.selectNodeContents(element);
        const rects = [...range.getClientRects()];
        const bounds = range.getBoundingClientRect();
        return {
          width: rects.reduce((sum, rect) => sum + rect.width, 0),
          height: Math.max(0, ...rects.map(rect => rect.height)),
          fontSize: Number.parseFloat(getComputedStyle(element).fontSize), rect: bounds.toJSON(),
        };
      };
      return {
        instrument: box.toJSON(), meter: meter?.toJSON() ?? null,
        primary: textBox(primary), secondary: textBox(secondary),
      };
    }));
  const horizontalClips = await page.locator(
    '.standard-face .receiver-instrument, .standard-face [data-testid="receiver-s-meter"], '
      + '.standard-face [data-vfo-freq], .standard-face .desktop-controls-left > *, '
      + '.standard-face .desktop-controls-center > *, .standard-face .desktop-controls-right > *, '
      + '.standard-face [data-zone-id="meters"] > *',
  ).evaluateAll(elements => {
    const viewportRight = document.documentElement.clientWidth;
    return elements.flatMap((element, index) => {
      const box = element.getBoundingClientRect();
      return box.width > 0 && (box.left < -1 || box.right > viewportRight + 1)
        ? [{ index, className: element.className, left: box.left, right: box.right, viewportRight }]
        : [];
    });
  });
  const receiverIntegrity = await page.locator(selectors.receiver).first().evaluate(receiver => {
    const zone = receiver.getBoundingClientRect();
    const tolerance = 1;
    const clippedOverflow = /(hidden|clip|auto|scroll)/;
    const descendants = [...receiver.querySelectorAll<HTMLElement>(
      '.receiver-instrument, [data-testid="receiver-s-meter"], [data-vfo-freq], '
        + '[data-instrument-bridge], [data-instrument-bridge] .active-receiver, '
        + '[data-instrument-bridge] [data-testid="vfo-shared-indicators"], '
        + '[data-instrument-bridge] [data-indicator-fact], [data-instrument-bridge] button, '
        + '[data-instrument-bridge] [data-testid="vfo-split-digest"], '
        + '[data-instrument-bridge] [data-split-rx], [data-instrument-bridge] [data-split-tx]',
    )].filter(element => {
      if (element.closest('.sr-only')) return false;
      const style = getComputedStyle(element);
      const box = element.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && box.width > 0 && box.height > 0;
    });
    const describe = (element: HTMLElement) => element.getAttribute('data-dual-action')
      ?? element.getAttribute('data-testid') ?? element.getAttribute('data-indicator-fact')
      ?? element.className ?? element.tagName;
    const outsideZone = descendants.flatMap(element => {
      const box = element.getBoundingClientRect();
      return box.left < zone.left - tolerance || box.right > zone.right + tolerance
        || box.top < zone.top - tolerance || box.bottom > zone.bottom + tolerance
        ? [{ target: describe(element), rect: box.toJSON(), zone: zone.toJSON() }] : [];
    });
    const ancestorClips = descendants.flatMap(element => {
      const box = element.getBoundingClientRect();
      for (let parent = element.parentElement; parent; parent = parent.parentElement) {
        const style = getComputedStyle(parent);
        const parentBox = parent.getBoundingClientRect();
        if (style.display === 'contents' || parentBox.width === 0 || parentBox.height === 0) continue;
        const clippedX = clippedOverflow.test(style.overflowX)
          && (box.left < parentBox.left - tolerance || box.right > parentBox.right + tolerance);
        const clippedY = clippedOverflow.test(style.overflowY)
          && (box.top < parentBox.top - tolerance || box.bottom > parentBox.bottom + tolerance);
        if (clippedX || clippedY) {
          return [{ target: describe(element), ancestor: describe(parent), axis: `${clippedX ? 'x' : ''}${clippedY ? 'y' : ''}` }];
        }
      }
      return [];
    });
    const textTargets = descendants.filter(element => element.matches(
      '[data-instrument-bridge] button, [data-instrument-bridge] .active-receiver, '
        + '[data-instrument-bridge] [data-indicator-fact], [data-instrument-bridge] [data-split-rx], '
        + '[data-instrument-bridge] [data-split-tx]',
    ));
    const textFailures = textTargets.flatMap(element => {
      if (element.matches('[data-indicator-fact="rf-authority"]')
        && ['receiving', 'unknown'].includes(element.dataset.indicatorRf ?? '')) return [];
      const range = document.createRange(); range.selectNodeContents(element);
      const text = range.getBoundingClientRect(); const owner = element.getBoundingClientRect();
      return text.width <= 0 || text.height <= 0 || text.left < owner.left - tolerance
        || text.right > owner.right + tolerance || text.top < owner.top - tolerance
        || text.bottom > owner.bottom + tolerance || text.top < zone.top - tolerance
        || text.bottom > zone.bottom + tolerance
        ? [{ target: describe(element), text: text.toJSON(), owner: owner.toJSON() }] : [];
    });
    const rfAuthorityFailures = [...receiver.querySelectorAll<HTMLElement>('[data-indicator-fact="rf-authority"]')]
      .flatMap(element => {
        const state = element.dataset.indicatorRf;
        const expected = state === 'transmitting' ? 'TX' : state === 'uncertain' ? 'TX?'
          : state === 'receiving' || state === 'unknown' ? '' : null;
        const owner = element.getBoundingClientRect();
        const actual = element.textContent?.trim() ?? '';
        return expected !== null && actual === expected && owner.width > 0 && owner.height > 0
          ? [] : [{ state, expected, actual, owner: owner.toJSON() }];
      });
    const hitFailures = descendants.filter(element => element.matches('[data-instrument-bridge] button'))
      .flatMap(element => {
        const box = element.getBoundingClientRect();
        const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
        return hit === element || (hit !== null && element.contains(hit)) ? []
          : [{ target: describe(element), hit: hit instanceof HTMLElement ? describe(hit) : null }];
      });
    const actionNames = descendants.filter(element => element.matches('[data-dual-action]'))
      .map(element => element.getAttribute('data-dual-action'));
    return {
      outsideZone, ancestorClips, textFailures, rfAuthorityFailures, hitFailures, actionNames,
      maxBottom: Math.max(zone.top, ...descendants.map(element => element.getBoundingClientRect().bottom)),
    };
  });
  return { boxes: Object.fromEntries(entries), instruments, horizontalClips, receiverIntegrity };
}

function expectStandardReceiverIntegrity(
  geometry: Awaited<ReturnType<typeof standardGeometry>>, bodyTop?: number,
) {
  expect.soft(geometry.receiverIntegrity.outsideZone, 'painted receiver descendants stay in their zone').toEqual([]);
  expect.soft(geometry.receiverIntegrity.ancestorClips, 'receiver descendants survive clipping ancestors').toEqual([]);
  expect.soft(geometry.receiverIntegrity.textFailures, 'bridge text ranges are painted and contained').toEqual([]);
  expect.soft(geometry.receiverIntegrity.rfAuthorityFailures, 'RF authority slots reserve space and show only TX states')
    .toEqual([]);
  expect.soft(geometry.receiverIntegrity.hitFailures, 'bridge controls are center-point hit-testable').toEqual([]);
  if (bodyTop !== undefined) {
    expect.soft(geometry.receiverIntegrity.maxBottom, 'receiver paint ends before the body row')
      .toBeLessThanOrEqual(bodyTop + 1);
  }
}

test.describe('MOR-2424 Standard v2.11.1 outer grid', () => {
  for (const width of [900, 1024, 1200, 1700] as const) {
    test(`Standard ${width} compact absolute VFO pair keeps every bridge control`, async ({ page }, info) => {
      await boot(page, 'standard', width, true, 'studioline', false, undefined, {
        height: 1000, extraCapabilities: ALL_STRUCTURAL_ACTION_CAPS, absoluteVfoPair: true,
      });
      const panel = page.getByTestId('vfo-instrument-panel');
      const cards = page.locator('[data-standard-vfo-slot]');
      await expect(cards).toHaveCount(2);
      const geometry = await panel.evaluate(element => {
        const rect = (target: Element) => target.getBoundingClientRect().toJSON();
        const cardRects = [...element.querySelectorAll('[data-standard-vfo-slot]')].map(rect);
        return {
          panel: rect(element), cards: cardRects,
          overflow: element.scrollWidth > element.clientWidth + 1,
          cardOverflow: [...element.querySelectorAll<HTMLElement>('[data-standard-vfo-slot]')]
            .map(card => {
              const cardBox = card.getBoundingClientRect();
              return [...card.querySelectorAll<HTMLElement>('.panel-header, .panel-meter, .vfo-freq, .control-strip, .control-strip *')]
              .filter(target => {
                const style = getComputedStyle(target);
                const box = target.getBoundingClientRect();
                return style.display !== 'none' && style.visibility !== 'hidden'
                  && (target.scrollWidth > target.clientWidth + 1
                    || box.left < cardBox.left - 1 || box.right > cardBox.right + 1
                    || box.top < cardBox.top - 1 || box.bottom > cardBox.bottom + 1);
              }).map(target => ({ target: target.className ?? target.tagName,
                scrollWidth: target.scrollWidth, clientWidth: target.clientWidth,
                rect: target.getBoundingClientRect().toJSON(), card: cardBox.toJSON() }));
            }),
          bridgeOverflow: [...element.querySelectorAll<HTMLElement>('[data-instrument-bridge] *')]
            .filter(target => target.scrollWidth > target.clientWidth + 1)
            .map(target => target.getAttribute('data-dual-action') ?? target.className ?? target.tagName),
        };
      });
      await info.attach('compact-vfo-pair', { body: JSON.stringify(geometry), contentType: 'application/json' });
      if (width > 1050) {
        expect.soft(geometry.panel.height, 'the full Standard VFO row stays compact').toBeLessThanOrEqual(190);
        expect.soft(geometry.cards[0].top, 'A and B cards start together').toBeCloseTo(geometry.cards[1].top, 0);
        expect.soft(geometry.cards[0].bottom, 'A and B cards end together').toBeCloseTo(geometry.cards[1].bottom, 0);
      }
      expect.soft(geometry.overflow, 'the VFO pair does not overflow its row').toBe(false);
      expect.soft(geometry.cardOverflow, 'both full card strips fit without clipped descendants').toEqual([[], []]);
      expect.soft(geometry.bridgeOverflow, 'every bridge item fits its assigned cell').toEqual([]);
      await expect(page.locator('[data-standard-select-vfo]')).toHaveCount(2);
      await expect(page.locator('[data-vfo-split]')).toBeVisible();
      for (const action of ['equalize', 'swap', 'speak']) {
        await expect(page.locator(`[data-dual-action="${action}"]`)).toBeVisible();
      }
      await expect(page.getByTestId('vfo-split-digest')).toBeVisible();
      const strips = cards.locator('.control-strip');
      await expect(strips).toHaveCount(2);
      await expect(strips.nth(0).locator('.mode-badge-wrapper')).toHaveAttribute('data-vfo-controls-disabled', 'false');
      await expect(strips.nth(1).locator('.mode-badge-wrapper')).toHaveAttribute('data-vfo-controls-disabled', 'true');
      await expect(strips.nth(0)).toContainText(/CW.*FIL3/);
      await expect(strips.nth(1)).toContainText(/USB.*FIL1/);
      for (const fact of ['antenna', 'atu', 'rit', 'xit']) {
        await expect(page.locator(`[data-indicator-fact="${fact}"]`)).toBeVisible();
      }
      expect(await page.evaluate(() => (window as unknown as { geometryCommands: { type: string }[] })
        .geometryCommands.filter(c => c.type === 'cmd'))).toEqual([]);
      const screenshot = info.outputPath(`compact-vfo-pair-${width}.png`);
      await page.screenshot({ path: screenshot, fullPage: true });
      await info.attach('compact-vfo-pair-screenshot', { path: screenshot, contentType: 'image/png' });
    });
  }

  for (const width of STANDARD_WIDTHS) for (const topology of STANDARD_TOPOLOGIES) {
    test(`standard ${width} ${topology} painted grid`, async ({ page }, info) => {
      await boot(page, 'standard', width, true, 'studioline', false, topology);
      const root = page.locator('.desktop-control-face');
      await expect(root).toHaveClass(/standard-face/);
      await expect(root).not.toHaveClass(/sdr-test/);
      await expect(page.locator('[data-vfo-appearance]').first()).toHaveAttribute('data-vfo-appearance', 'standard');
      await expect(page.locator('[data-vfo-tile]')).toHaveCount(topology === 'topology-1-single' ? 1 : 4);
      const geometry = await standardGeometry(page);
      const boxes = geometry.boxes as Record<string, DOMRect>;
      expect.soft(boxes.receiver.y, 'receiver deck follows status').toBeGreaterThanOrEqual(boxes.status.y + boxes.status.height - 1);
      const bodyTop = Math.min(boxes.left.y, boxes.center.y, boxes.right.y);
      const bodyBottom = Math.max(
        boxes.left.y + boxes.left.height, boxes.center.y + boxes.center.height,
        boxes.right.y + boxes.right.height,
      );
      expect.soft(bodyTop, 'outer-grid body follows the receiver deck').toBeGreaterThanOrEqual(boxes.receiver.y + boxes.receiver.height - 1);
      expect.soft(boxes.meters.y, 'meters follow the outer-grid body').toBeGreaterThanOrEqual(bodyBottom - 1);
      expect.soft(boxes.meters.x, 'meters start at the full-width bottom dock').toBeCloseTo(boxes.bottom.x, 0);
      expect.soft(boxes.meters.width, 'meters span the full-width bottom dock').toBeCloseTo(boxes.bottom.width, 0);
      if (width > 1024) {
        const sideWidth = width <= 1200 ? 208 : 228;
        expect.soft(boxes.receiver.height, 'wide Standard receiver row keeps its 200px floor').toBeGreaterThanOrEqual(199);
        expect.soft(boxes.left.width).toBeCloseTo(sideWidth, 0);
        expect.soft(boxes.right.width).toBeCloseTo(sideWidth, 0);
        expect.soft(boxes.left.y).toBeCloseTo(boxes.center.y, 0);
        expect.soft(boxes.center.y).toBeCloseTo(boxes.right.y, 0);
        expect.soft(boxes.left.x + boxes.left.width).toBeLessThan(boxes.center.x);
        expect.soft(boxes.center.x + boxes.center.width).toBeLessThan(boxes.right.x);
      } else {
        expect.soft(boxes.left.x).toBeCloseTo(boxes.center.x, 0);
        expect.soft(boxes.center.x).toBeCloseTo(boxes.right.x, 0);
        expect.soft(boxes.left.y + boxes.left.height).toBeLessThanOrEqual(boxes.center.y + 1);
        expect.soft(boxes.center.y + boxes.center.height).toBeLessThanOrEqual(boxes.right.y + 1);
        expect.soft(boxes.right.y + boxes.right.height).toBeLessThanOrEqual(boxes.meters.y + 1);
      }
      expect.soft(geometry.horizontalClips, 'painted Standard descendants fit horizontally').toEqual([]);
      expectStandardReceiverIntegrity(geometry, bodyTop);
      for (const instrument of geometry.instruments) {
        expect.soft(instrument.meter?.width ?? 0, 'receiver meter is painted').toBeGreaterThan(0);
        expect.soft(instrument.meter?.height ?? 0, 'receiver meter is painted').toBeGreaterThan(0);
        expect.soft(instrument.primary?.width ?? 0, 'primary frequency glyphs are painted').toBeGreaterThan(0);
        expect.soft(instrument.primary?.height ?? 0, 'primary frequency glyphs are painted').toBeGreaterThan(0);
        expect.soft(instrument.meter!.left).toBeGreaterThanOrEqual(instrument.instrument.left - 1);
        expect.soft(instrument.meter!.right).toBeLessThanOrEqual(instrument.instrument.right + 1);
        expect.soft(instrument.primary!.rect.left).toBeGreaterThanOrEqual(instrument.instrument.left - 1);
        expect.soft(instrument.primary!.rect.right).toBeLessThanOrEqual(instrument.instrument.right + 1);
        if (instrument.secondary) expect.soft(instrument.primary!.fontSize).toBeGreaterThan(instrument.secondary.fontSize);
      }
      if (topology === 'topology-2-main-sub') await expect(page.locator('.standard-face .spectrum-panel')).toBeVisible();
      const unkey = page.getByTestId('rx-tx-unkey');
      await expect(unkey).toHaveCount(1);
      await focusWithoutActivation(page, unkey);
      expect(await page.evaluate(() => (window as unknown as { geometryCommands: { type: string }[] }).geometryCommands.filter(c => c.type === 'cmd'))).toEqual([]);
      await info.attach('standard-grid-bounds', { body: JSON.stringify(geometry, null, 2), contentType: 'application/json' });
      const screenshot = info.outputPath('standard-grid.png');
      await page.screenshot({ path: screenshot, fullPage: true });
      await info.attach('standard-grid', { path: screenshot, contentType: 'image/png' });
    });
  }

  test('crosses the 1200 and 1024 Standard thresholds without changing routes', async ({ page }) => {
    await boot(page, 'standard', 1201, true, 'studioline', false, 'topology-1-single');
    const root = page.locator('.desktop-control-face.standard-face');
    const left = page.locator('.standard-face .desktop-controls-left');
    const center = page.locator('.standard-face .desktop-controls-center');
    expect((await left.boundingBox())!.width).toBeCloseTo(228, 0);
    expectStandardReceiverIntegrity(await standardGeometry(page));
    await page.setViewportSize({ width: 1200, height: 1000 });
    expect((await left.boundingBox())!.width).toBeCloseTo(208, 0);
    expectStandardReceiverIntegrity(await standardGeometry(page));
    await page.setViewportSize({ width: 1025, height: 1000 });
    expect((await left.boundingBox())!.y).toBeCloseTo((await center.boundingBox())!.y, 0);
    expectStandardReceiverIntegrity(await standardGeometry(page));
    await page.setViewportSize({ width: 1024, height: 1000 });
    expect((await left.boundingBox())!.y + (await left.boundingBox())!.height)
      .toBeLessThanOrEqual((await center.boundingBox())!.y + 1);
    expectStandardReceiverIntegrity(await standardGeometry(page));
    await expect(root).toHaveClass(/standard-face/);
  });

  for (const [language, theme] of STANDARD_SHORT_CASES) for (const locale of ['en-US', 'ru-RU'] as const) {
    test(`standard 1280x800 ${language} ${theme} ${locale} contains the full bridge`, async ({ page }) => {
      const known = locale === 'en-US';
      await boot(page, 'standard', 1280, known, language, false, 'topology-2-main-sub', {
        height: 800, theme, locale, extraCapabilities: ALL_STRUCTURAL_ACTION_CAPS,
      });
      const geometry = await standardGeometry(page);
      const boxes = geometry.boxes as Record<string, DOMRect>;
      const bodyTop = Math.min(boxes.left.y, boxes.center.y, boxes.right.y);
      expectStandardReceiverIntegrity(geometry, bodyTop);
      expect(geometry.receiverIntegrity.actionNames).toEqual(['main', 'sub', 'equalize', 'swap', 'speak']);
      expect(await page.evaluate(() => (window as unknown as { geometryCommands: { type: string }[] })
        .geometryCommands.filter(c => c.type === 'cmd'))).toEqual([]);
    });
  }

  for (const width of [1440, 1024] as const) {
    test(`SDR ${width} keeps its current desktop grid`, async ({ page }) => {
      await boot(page, 'sdr-test', width, true, 'studioline', false, 'topology-2-main-sub');
      const root = page.locator('.desktop-control-face');
      await expect(root).toHaveClass(/sdr-test/);
      await expect(root).not.toHaveClass(/standard-face/);
      await expect(page.locator('[data-vfo-appearance]').first()).toHaveAttribute('data-vfo-appearance', 'sdr');
      const left = await page.locator('.desktop-controls-left').boundingBox();
      const center = await page.locator('.desktop-controls-center').boundingBox();
      const right = await page.locator('.desktop-controls-right').boundingBox();
      expect(left!.y).toBeCloseTo(center!.y, 0);
      expect(center!.y).toBeCloseTo(right!.y, 0);
      expect(left!.x + left!.width).toBeLessThan(center!.x);
      expect(center!.x + center!.width).toBeLessThan(right!.x);
      expect(await page.evaluate(() => (window as unknown as { geometryCommands: { type: string }[] }).geometryCommands.filter(c => c.type === 'cmd'))).toEqual([]);
    });
  }

  test('mobile keeps the mobile shell at 390px', async ({ page }) => {
    await boot(page, 'standard', 390, true, 'studioline', false, 'topology-1-single');
    await expect(page.locator('.m-layout, .m-landscape').first()).toBeVisible();
    await expect(page.locator('.desktop-control-face')).toHaveCount(0);
    expect(await page.evaluate(() => (window as unknown as { geometryCommands: { type: string }[] }).geometryCommands.filter(c => c.type === 'cmd'))).toEqual([]);
  });
});

for (const layout of ['standard', 'sdr-test', 'lcd-scope', 'lcd-cockpit']) {
  for (const width of [900, 1440]) for (const known of [true, false]) {
    test(`${layout} ${width} ${known ? 'observed IC' : 'unknown'} geometry`, async ({ page }, info) => {
      const errors: string[] = []; page.on('pageerror', e => errors.push(String(e)));
      await boot(page, layout, width, known);
      if (layout === 'standard' && width === 900) {
        const stripFailures = await page.locator('.receiver-instrument .control-strip').evaluateAll(strips =>
          strips.flatMap(strip => {
            const card = strip.closest('.receiver-instrument')!.getBoundingClientRect();
            const box = strip.getBoundingClientRect();
            return strip.scrollWidth > strip.clientWidth + 1 || box.left < card.left - 1
              || box.right > card.right + 1 || box.top < card.top - 1 || box.bottom > card.bottom + 1
              ? [{ scrollWidth: strip.scrollWidth, clientWidth: strip.clientWidth,
                strip: box.toJSON(), card: card.toJSON() }] : [];
          }));
        expect.soft(stripFailures, '900px Standard cards keep every status chip contained').toEqual([]);
      }
      const unkey = page.getByTestId('rx-tx-unkey');
      await expect(unkey).toHaveCount(1);
      await expect(page.getByTestId('rx-tx-key')).toHaveCount(1);
      const boundsPath = info.outputPath('bounds.json');
      await writeFile(boundsPath, JSON.stringify(
        await page.locator('.desktop-controls-center,.desktop-controls-center .content-row,[data-zone-id="meters"],.lcd-slot,.lcd-scope,.lcd-filter-row,.lcd-vfo-row,.lcd-vfo-row .vfo-freq').evaluateAll(es => es.map(e => ({
          className: e.className, rect: e.getBoundingClientRect().toJSON(),
        }))), null, 2));
      await info.attach('bounds', { contentType: 'application/json', path: boundsPath });
      const semanticFrequency = page.locator('[data-vfo-freq]').first();
      if (known) await expect(semanticFrequency).toContainText('035');
      else await expect(semanticFrequency).toHaveText('—');
      if (layout.startsWith('lcd')) {
        const scope = await page.locator('.lcd-frame .lcd-scope,.lcd-frame .lcd-filter-row').boundingBox();
        expect.soft(scope!.height, 'LCD keeps space for its scope').toBeGreaterThanOrEqual(80);
        const clips = await page.locator('.lcd-layout .semantic-slot').evaluate(slot => {
          const outer = slot.getBoundingClientRect();
          return [...slot.querySelectorAll('.vfo-tile,.receiver-indicators,.rx-tx-actions button')]
            .filter(e => { const r = e.getBoundingClientRect(); return r.left < outer.left - 1 || r.right > outer.right + 1 || e.scrollWidth > e.clientWidth + 1; })
            .map(e => e.className);
        });
        expect.soft(clips, 'LCD facts and actions fit the actual column').toEqual([]);
        const digits = page.locator('.lcd-frame .freq-active').first();
        await expect(digits).toContainText('035'); await expect(digits).toContainText('720');
        const clippedDigits = await digits.evaluate(group => [...group.children].filter(digit => {
          const r = digit.getBoundingClientRect();
          for (let p = digit.parentElement; p; p = p.parentElement) {
            const c = getComputedStyle(p); const b = p.getBoundingClientRect();
            if (/(hidden|clip|auto|scroll)/.test(c.overflowX) && (r.left < b.left - 1 || r.right > b.right + 1)) return true;
            if (/(hidden|clip|auto|scroll)/.test(c.overflowY) && (r.top < b.top - 1 || r.bottom > b.bottom + 1)) return true;
          }
          return false;
        }).map(e => e.textContent));
        expect.soft(clippedDigits, 'every frequency group survives clipping ancestors').toEqual([]);
        await page.locator('.lcd-layout .content-right').evaluate(e => { e.scrollTop = e.scrollHeight; });
      } else {
        const center = await page.locator('.desktop-controls-center .content-row').boundingBox();
        const metersLocator = layout === 'standard'
          ? page.locator('[data-panel-id="semantic-meters"]')
          : page.locator('[data-zone-id="meters"]');
        const meters = await metersLocator.boundingBox();
        expect.soft(center!.height, 'scope uses available space or its existing 280px floor')
          .toBeLessThanOrEqual(Math.max(280, page.viewportSize()!.height - center!.y - meters!.height));
        expect.soft(center!.y + center!.height, 'scope ends before station meters').toBeLessThanOrEqual(meters!.y + 1);
        await metersLocator.scrollIntoViewIfNeeded();
        await expect(metersLocator).toBeInViewport();
        await page.locator('.desktop-controls-right').evaluate(e => { e.scrollTop = e.scrollHeight; });
      }
      await focusWithoutActivation(page, unkey);
      expect(errors).toEqual([]);
      expect(await page.evaluate(() => (window as unknown as { geometryCommands: { type: string }[] }).geometryCommands.filter(c => c.type === 'cmd'))).toEqual([]);
      const screenshot = info.outputPath('geometry.png');
      await page.screenshot({ path: screenshot, fullPage: true });
      await info.attach('geometry', { path: screenshot, contentType: 'image/png' });
    });
  }
}

// MOR-2425/R41: the freshness cue is gone, so the claim is now the stronger
// one it used to approximate — the instrument's geometry does not move at all
// through a current → stale → current recovery, and no cue comes back.
for (const layout of ['standard', 'sdr-test']) for (const language of ['studioline', 'fieldline']) {
  test(`${layout} ${language} instrument geometry is fixed through recovery`, async ({ page }, info) => {
    await boot(page, layout, 1440, false, language, true);
    // SDR opts out of the selected design language; assert the actual production state.
    if (layout === 'standard') await expect(page.locator('html')).toHaveAttribute('data-design-language', language);
    else expect(await page.locator('html').getAttribute('data-design-language')).toBeNull();
    await expect(page.locator('[data-vfo-appearance]').first()).toHaveAttribute('data-vfo-appearance', layout === 'standard' ? 'standard' : 'sdr');
    const frequency = page.locator('.receiver-instrument [data-vfo-freq]').first();
    await expect(frequency).toHaveText('—');
    const paint = await frequency.evaluate(e => {
      const read = (e: Element) => { const s = getComputedStyle(e); return [s.fontFamily, s.fontSize, s.fontWeight, s.lineHeight, s.color, s.textShadow, s.letterSpacing]; };
      return { outer: read(e), inner: read(e.querySelector('.freq')!) };
    });
    expect(paint.inner).toEqual(paint.outer);
    const measure = () => page.locator('.receiver-instrument').evaluateAll(instruments => {
      const rect = (e: Element) => e.getBoundingClientRect().toJSON();
      return instruments.map(instrument =>
        [...instrument.querySelectorAll('.vfo-tile,.vfo-role,.vfo-freq,.vfo-mode,.vfo-select')].map(rect));
    });
    const cueCount = () => page.locator('.receiver-instrument [data-vfo-stale-cue]').count();
    const unknown = await measure();
    expect(unknown.length).toBeGreaterThan(0);
    expect(await cueCount()).toBe(0);
    await info.attach('unknown-bounds', { body: JSON.stringify(unknown), contentType: 'application/json' });
    // The shared production unknown fixture remains evidence-free; recovery uses the IC fixture.
    await boot(page, layout, 1440, false, language);
    let current: Awaited<ReturnType<typeof measure>> | undefined;
    for (const [index, stateName] of ['current', 'stale', 'current'].entries()) {
      const state = fixture(true).state;
      Object.assign(state, { revision: index + 2, stateRevision: index + 2,
        freshnessRevision: index + 2, observationSeq: index + 2 });
      if (stateName === 'stale') for (const [path, field] of Object.entries(state.fieldStatus ?? {})) {
        if (!/^main\.(?:unselectedVfo\.)?(?:freqHz|mode|filter|filterNum)$/.test(path)) continue;
        Object.assign(field, { freshness: 'stale', availability: 'stale' });
      }
      await page.evaluate(state => window.dispatchEvent(new CustomEvent('geometry-state', { detail: state })), state);
      await expect(frequency).toHaveAttribute('data-display-state', stateName);
      await expect(frequency).toContainText('035');
      const boxes = await measure();
      await info.attach(`${stateName}-${index}-bounds`, { body: JSON.stringify(boxes), contentType: 'application/json' });
      if (current) expect(boxes).toEqual(current); else current = boxes;
      expect(await cueCount()).toBe(0);
      if (stateName === 'stale') {
        await page.screenshot({ path: info.outputPath('instrument-stale.png'), fullPage: true });
      }
    }
    expect(await page.evaluate(() => (window as unknown as { geometryCommands: { type: string }[] }).geometryCommands.filter(c => c.type === 'cmd'))).toEqual([]);
  });
}

// T185 — the transmit key pair wraps when its column is narrower than the pair.
// Measured in the flagship probe because it leaves `semantic/RxTxSurface.svelte`'s
// `.rx-tx-actions` row as that surface declares it, while
// `skins/desktop-v2/semantic-controls.css`, `components-v2/layout/LcdLayout.svelte`
// and `presentation/languages/fieldline/fieldline.css` each replace that row with a
// single column, where there is no line for a second button to share.
test.describe('T185 transmit key pair', () => {
  async function measurePair(page: Page) {
    const zone = page.locator('[data-zone-id="rx-tx"]').first();
    await expect(zone, 'the probe paints a transmit zone').toBeVisible();
    return zone.evaluate(element => {
      const actions = element.querySelector<HTMLElement>('.rx-tx-actions')!;
      const rect = (selector: string) =>
        element.querySelector(selector)!.getBoundingClientRect().toJSON();
      return {
        zone: element.getBoundingClientRect().toJSON(),
        actions: actions.getBoundingClientRect().toJSON(),
        key: rect('[data-testid="rx-tx-key"]'),
        unkey: rect('[data-testid="rx-tx-unkey"]'),
        gap: Number.parseFloat(getComputedStyle(actions).columnGap),
      };
    });
  }

  type Box = { left: number; right: number };
  const contained = (box: Box, zone: Box) => box.left >= zone.left - 1 && box.right <= zone.right + 1;

  test('stacks when its column is narrower than the pair', async ({ page }, info) => {
    // `layout` is the workspace value; `qaLayout` is what actually selects the skin.
    await boot(page, 'standard', 1440, true, 'studioline', false, undefined,
      { qaLayout: 'flagship-probe' });
    // Both cases set the rail column they measure in, rather than inheriting
    // whatever the skin declares: the two custom properties its grid templates
    // size a rail track from are the `minmax()` floor and its width, and BOTH
    // have to be pinned or the one left alone decides the track. 200px is
    // narrower than the pair, which is the column this case needs.
    // `!important` because the skin's own declarations are Svelte-scoped, and
    // a plain `.flagship-probe` selector loses to that class.
    await page.addStyleTag({ content: '.flagship-probe { --flagship-probe-rail-floor: 200px !important;'
      + ' --flagship-probe-rail-width: 200px !important; }' });
    const geometry = await measurePair(page);
    await info.attach('narrow', { body: JSON.stringify(geometry), contentType: 'application/json' });
    expect(geometry.unkey.top, 'the unkey button starts below the key button')
      .toBeGreaterThan(geometry.key.bottom - 1);
    expect(contained(geometry.key, geometry.zone), 'the key stays inside its column').toBe(true);
    expect(contained(geometry.unkey, geometry.zone), 'the unkey stays inside its column').toBe(true);
    // With each button on its own line, the widths measured are the pair's own,
    // and their sum exceeds the line the two were given.
    expect(geometry.key.width + geometry.gap + geometry.unkey.width,
      'the column under test is narrower than the pair').toBeGreaterThan(geometry.actions.width);
  });

  test('stays on one line when its column is wider than the pair', async ({ page }, info) => {
    await boot(page, 'standard', 1440, true, 'studioline', false, undefined,
      { qaLayout: 'flagship-probe' });
    // The mirror of the case above: 400px is wider than the pair. The skin's
    // own rail is one key wide, so this column has to be set here too.
    await page.addStyleTag({ content: '.flagship-probe { --flagship-probe-rail-floor: 400px !important;'
      + ' --flagship-probe-rail-width: 400px !important; }' });
    const geometry = await measurePair(page);
    await info.attach('wide', { body: JSON.stringify(geometry), contentType: 'application/json' });
    expect(geometry.key.width + geometry.gap + geometry.unkey.width,
      'the column under test is wider than the pair').toBeLessThanOrEqual(geometry.actions.width);
    expect(geometry.unkey.top, 'both buttons share one line').toBe(geometry.key.top);
    expect(contained(geometry.key, geometry.zone), 'the key stays inside its column').toBe(true);
    expect(contained(geometry.unkey, geometry.zone), 'the unkey stays inside its column').toBe(true);
  });
});
