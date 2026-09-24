/**
 * MOR-2111 — the repeater side panel on the Standard face (real browser).
 * Boots the Standard face from the `topology-2-main-sub` fixture, patched with
 * repeater-band freqRanges, `ctcssTones` and the repeater capability tags.
 * Outgoing WebSocket frames are recorded, never forwarded: no radio, no TX,
 * no server.
 */
import { test, expect, type Page } from '@playwright/test';
import { fixtureById } from '../../../fixtures/catalog';
import { mockCapabilities, mockInfo, mockState } from './fixtures';
import type { Capabilities } from '../../../src/lib/types/capabilities';
import type { ServerState } from '../../../src/lib/types/state';

function observed(storePath: string) {
  return { storePath, observed: true, freshness: 'fresh' as const, availability: 'available' as const, lastObservedMonotonic: 0 };
}

function repeaterFixture(mainHz: number, subHz: number, active: 'MAIN' | 'SUB'): { state: ServerState; caps: Capabilities } {
  const fixture = fixtureById('topology-2-main-sub');
  if (!fixture) throw new Error('missing topology-2-main-sub fixture');
  const topologyState = fixture.state();
  const topologyCaps = fixture.caps();
  if (!topologyState || !topologyCaps) throw new Error('missing topology-2-main-sub fixture');
  const state = {
    ...structuredClone(mockState),
    ...structuredClone(topologyState),
    main: { ...structuredClone(mockState.main), ...structuredClone(topologyState.main) },
    sub: { ...structuredClone(mockState.sub), ...structuredClone(topologyState.sub) },
  } as ServerState;
  state.active = active;
  state.main.freqHz = mainHz;
  state.sub!.freqHz = subHz;
  state.sub!.mode = 'FM';
  Object.assign(state.main, { repeaterTone: false, repeaterTsql: false, toneFreq: 8850, repeaterShift: 0 });
  Object.assign(state.sub, { repeaterTone: false, repeaterTsql: false, toneFreq: 8850, repeaterShift: 0 });
  state.fieldStatus = { ...(state.fieldStatus ?? {}) };
  for (const receiver of ['main', 'sub']) {
    for (const leaf of ['repeaterTone', 'repeaterTsql', 'toneFreq', 'repeaterShift']) {
      const path = `${receiver}.${leaf}`;
      state.fieldStatus[path] = observed(path);
    }
  }
  const caps: Capabilities = { ...structuredClone(mockCapabilities), ...structuredClone(topologyCaps) };
  caps.capabilities = [...new Set([...(caps.capabilities ?? []), 'repeater_tone', 'tsql', 'repeater_shift'])];
  caps.ctcssTones = [6700, 7190, 8850, 10000];
  caps.freqRanges = [
    ...(caps.freqRanges ?? []),
    { start: 144_000_000, end: 148_000_000, label: '2m', repeater: true },
    { start: 430_000_000, end: 450_000_000, label: '70cm', repeater: true },
  ];
  return { state, caps };
}

async function boot(page: Page, state: ServerState, caps: Capabilities, width = 1440): Promise<void> {
  await page.setViewportSize({ width, height: 1000 });
  await page.addInitScript(({ state }) => {
    localStorage.setItem('rigplane:workspace', JSON.stringify({ version: 1, layout: 'standard', designLanguage: 'studioline', theme: 'nord' }));
    localStorage.setItem('rigplane.i18n.locale', 'en-US');
    const frames: unknown[] = [];
    Object.assign(window, { repeaterFrames: frames });
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
            const e = new MessageEvent('message', { data: JSON.stringify({ type: 'state_update',
              data: { type: 'full', data: state, revision: state.revision,
                stateRevision: state.stateRevision, freshnessRevision: state.freshnessRevision,
                observationSeq: state.observationSeq, stateContractVersion: state.stateContractVersion,
                providerGeneration: state.providerGeneration } }) });
            this.dispatchEvent(e); this.onmessage?.(e);
          }
        });
      }
      send(data: string) { frames.push(JSON.parse(data)); }
      close() { this.readyState = 3; const e = new Event('close'); this.dispatchEvent(e); this.onclose?.(e); }
    }
    Object.assign(window, { WebSocket: Socket });
  }, { state });
  await page.route('**/api/**', (route) => {
    const name = new URL(route.request().url()).pathname.split('/').pop();
    const body = name === 'state' ? state : name === 'capabilities' ? caps : name === 'info' ? mockInfo
      : name === 'managed-transmit' ? { managedTransmit: { status: 'available', intent: { kind: 'rx' }, releaseRequired: false, lastError: null, lastActuation: null, abortErrors: [], tot: { configuredSeconds: 180, active: false, remainingMs: null, expiresAt: null } }, txObservation: { observedPtt: 'off' } } : {};
    return route.fulfill({ json: body });
  });
  await page.goto('/?locale=en-US', { waitUntil: 'networkidle' });
  await expect(page.locator('.desktop-control-face').first()).toBeVisible();
}

const PANEL = '.desktop-control-face.standard-face .desktop-controls-left [data-panel-id="semantic-repeater"]';
const SURFACE = `${PANEL} [data-testid="repeater-surface"]`;

async function repeaterFrames(page: Page): Promise<Array<{ name: string; params: Record<string, unknown> }>> {
  return page.evaluate(() => (window as unknown as { repeaterFrames: Array<{ type: string; name: string; params: Record<string, unknown> }> })
    .repeaterFrames.filter((frame) => frame.type === 'cmd' && frame.name.startsWith('set_repeater'))
    .map(({ name, params }) => ({ name, params })));
}

test('SUB on 144.700 FM while MAIN is selected on 14.250: the panel controls SUB and a click sends receiver 1', async ({ page }) => {
  const { state, caps } = repeaterFixture(14_250_000, 144_700_000, 'MAIN');
  await boot(page, state, caps);
  await expect(page.locator(SURFACE)).toBeVisible();
  await expect(page.locator(SURFACE)).toHaveAttribute('data-receiver', 'SUB');
  await expect(page.locator(`${SURFACE} [data-testid="repeater-receiver"]`)).toHaveText('SUB');
  await expect(page.locator(`${SURFACE} [data-testid="repeater-tone-freq-value"]`)).toHaveText('88.5');

  await page.locator(`${SURFACE} [data-testid="repeater-tone-tone"]`).click();
  await expect.poll(() => repeaterFrames(page)).toEqual([
    { name: 'set_repeater_tone', params: { on: true, receiver: 1 } },
  ]);
});

test('both receivers on HF: no repeater panel', async ({ page }) => {
  const { state, caps } = repeaterFixture(14_250_000, 7_185_000, 'MAIN');
  await boot(page, state, caps);
  await expect(page.locator('.desktop-control-face.standard-face [data-panel-id="semantic-filter"]')).toBeVisible();
  await expect(page.locator('[data-panel-id="semantic-repeater"]')).toHaveCount(0);
  await expect(page.locator('[data-testid="repeater-surface"]')).toHaveCount(0);
});

for (const active of ['MAIN', 'SUB'] as const) {
  test(`both receivers on 144/430 with ${active} selected: the panel follows the selection`, async ({ page }) => {
    const { state, caps } = repeaterFixture(145_500_000, 433_000_000, active);
    await boot(page, state, caps);
    await expect(page.locator(SURFACE)).toHaveAttribute('data-receiver', active);
    await page.locator(`${SURFACE} [data-testid="repeater-shift-plus"]`).click();
    await expect.poll(() => repeaterFrames(page)).toEqual([
      { name: 'set_repeater_shift', params: { direction: 1, receiver: active === 'SUB' ? 1 : 0 } },
    ]);
  });
}

for (const width of [1280, 1440]) {
  test(`at ${width} px every repeater control sits inside the panel and is hit at its own centre`, async ({ page }) => {
    const { state, caps } = repeaterFixture(14_250_000, 144_700_000, 'MAIN');
    await boot(page, state, caps, width);
    const panel = page.locator(PANEL);
    await expect(panel).toBeVisible();
    // Scroll the PANEL once, never a control: scrolling each control into
    // view would scroll overflowing content back inside the panel.
    await panel.scrollIntoViewIfNeeded();
    const results = await page.evaluate(([panelSelector, surfaceSelector]) => {
      const host = document.querySelector(panelSelector)!.getBoundingClientRect();
      return [...document.querySelectorAll(`${surfaceSelector} button, ${surfaceSelector} output`)].map((element) => {
        const box = element.getBoundingClientRect();
        const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
        return {
          label: element.textContent?.trim() || element.getAttribute('aria-label'),
          inside: box.left >= host.left - 0.5 && box.right <= host.right + 0.5
            && box.top >= host.top - 0.5 && box.bottom <= host.bottom + 0.5,
          hit: hit === element || element.contains(hit),
        };
      });
    }, [PANEL, SURFACE]);
    expect(results).toHaveLength(9);
    for (const result of results) {
      expect(result, JSON.stringify(result)).toMatchObject({ inside: true, hit: true });
    }
  });
}
