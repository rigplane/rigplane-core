/**
 * MOR-2111 PR2 — the repeater strip in the standard deck (real browser).
 *
 * Boots the standard face with MAIN on 14.250 MHz (HF) and SUB on 144.700 MHz
 * (a 2 m repeater frequency), patched with the repeater-band freqRanges,
 * `ctcssTones` and the repeater capability tags. Asserts the strip is drawn
 * under SUB's frequency and not under MAIN's, that it does not overlap the
 * RIT/XIT/SPLIT chips, and — the placement-B no-layout-shift guarantee — that
 * the deck height is byte-identical when SUB moves off 144.700 back to HF.
 *
 * The height re-emit drives the same mock WebSocket the boot installs; it is
 * not a radio or a live backend (no TX, no server).
 */
import { test, expect, type Page } from '@playwright/test';
import { fixtureById } from '../../../fixtures/catalog';
import { mockCapabilities, mockInfo, mockState } from './fixtures';
import type { Capabilities } from '../../../src/lib/types/capabilities';
import type { ServerState } from '../../../src/lib/types/state';

function observed(storePath: string) {
  return { storePath, observed: true, freshness: 'fresh' as const, availability: 'available' as const, lastObservedMonotonic: 0 };
}

function repeaterFixture(): { state: ServerState; caps: Capabilities } {
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
  state.main.freqHz = 14_250_000;
  state.sub!.freqHz = 144_700_000;
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
  caps.ctcssTones = [6700, 6930, 7190, 7440, 7700, 7970, 8250, 8540, 8850, 9150, 9450, 9750, 10000, 10350, 10720, 11090, 11480, 11880, 12300, 12730];
  caps.freqRanges = [
    ...(caps.freqRanges ?? []),
    { start: 144_000_000, end: 148_000_000, label: '2m', repeater: true },
    { start: 430_000_000, end: 450_000_000, label: '70cm', repeater: true },
  ];
  return { state, caps };
}

async function boot(page: Page, state: ServerState, caps: Capabilities): Promise<void> {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.addInitScript(({ state }) => {
    localStorage.setItem('rigplane:workspace', JSON.stringify({ version: 1, layout: 'standard', designLanguage: 'studioline', theme: 'nord' }));
    localStorage.setItem('rigplane.i18n.locale', 'en-US');
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
      send(_data: string) { /* recorded nowhere — no radio */ }
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

const DECK = '.desktop-control-face.standard-face [data-zone-id="receiver-deck"]';
const SUB_PANEL = '[data-receiver-instrument="SUB"]';
const MAIN_PANEL = '[data-receiver-instrument="MAIN"]';
const STRIP = '[data-vfo-row="repeater"]';

test('the repeater strip is per-receiver and keeps the deck height fixed', async ({ page }) => {
  const { state, caps } = repeaterFixture();
  await boot(page, state, caps);

  // SUB on 144.700 draws the strip; MAIN on HF does not.
  await expect(page.locator(`${SUB_PANEL} ${STRIP}`)).toBeVisible();
  await expect(page.locator(`${MAIN_PANEL} ${STRIP}`)).toHaveCount(0);

  // No overlap with the RIT/XIT/SPLIT chips (drawn only on the active panel).
  const stripBox = await page.locator(`${SUB_PANEL} ${STRIP}`).boundingBox();
  for (const chip of await page.locator('.chip-amber').all()) {
    const box = await chip.boundingBox();
    if (!box || !stripBox) continue;
    const overlap = stripBox.x < box.x + box.width && stripBox.x + stripBox.width > box.x
      && stripBox.y < box.y + box.height && stripBox.y + stripBox.height > box.y;
    expect(overlap).toBe(false);
  }

  const withStrip = (await page.locator(DECK).boundingBox())?.height ?? 0;
  expect(withStrip).toBeGreaterThan(0);

  // Move SUB to HF (7.185 MHz) — the strip must vanish and the deck height
  // must not move (placement B's measured 0 px). Bump the revision counters
  // so the mock WebSocket applies the frame as a fresh observation.
  const hf = {
    ...state,
    revision: (state.revision ?? 0) + 1,
    stateRevision: (state.stateRevision ?? 0) + 1,
    freshnessRevision: (state.freshnessRevision ?? 0) + 1,
    observationSeq: (state.observationSeq ?? 0) + 1,
    updatedAt: new Date().toISOString(),
    sub: { ...state.sub!, freqHz: 7_185_000 },
  };
  await page.evaluate((next) => {
    window.dispatchEvent(new CustomEvent('geometry-state', { detail: next }));
  }, hf);
  await expect(page.locator(`${SUB_PANEL} ${STRIP}`)).toHaveCount(0);

  const withoutStrip = (await page.locator(DECK).boundingBox())?.height ?? 0;
  expect(withoutStrip).toBe(withStrip);
});
