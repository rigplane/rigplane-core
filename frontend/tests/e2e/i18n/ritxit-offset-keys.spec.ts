/**
 * MOR-2727 — after a click on the RIT/XIT offset slider, the arrow keys step
 * the offset and never tune the VFO (real browser). Boots the Standard face
 * from the `topology-2-main-sub` fixture with RIT on at 0 Hz and the FTX-1
 * RIT domain (`rigs/ftx1.toml` [controls.rit]: -9999..9999 Hz, 1 Hz step).
 * Outgoing WebSocket frames are recorded, never forwarded: no radio, no TX,
 * no rigplane server.
 */
import { test, expect, type Page } from '@playwright/test';
import { fixtureById } from '../../../fixtures/catalog';
import { mockCapabilities, mockInfo, mockState } from './fixtures';
import type { Capabilities } from '../../../src/lib/types/capabilities';
import type { ServerState } from '../../../src/lib/types/state';

function observed(storePath: string) {
  return { storePath, observed: true, freshness: 'fresh' as const, availability: 'available' as const, lastObservedMonotonic: 0 };
}

const FTX1_RIT_DOMAIN = {
  mapping: 'identity', raw_min: -9999, raw_max: 9999, raw_step: 1, raw_origin: 0,
  display_min: '-9999', display_max: '9999', display_step: '1', display_origin: '0',
  display_unit: 'Hz', quantization: 'reject', restoration: 'exact',
};

function ritFixture(): { state: ServerState; caps: Capabilities } {
  const fixture = fixtureById('topology-2-main-sub');
  const topologyState = fixture?.state();
  const topologyCaps = fixture?.caps();
  if (!topologyState || !topologyCaps) throw new Error('missing topology-2-main-sub fixture');
  const state = {
    ...structuredClone(mockState),
    ...structuredClone(topologyState),
    main: { ...structuredClone(mockState.main), ...structuredClone(topologyState.main) },
    sub: { ...structuredClone(mockState.sub), ...structuredClone(topologyState.sub) },
  } as ServerState;
  Object.assign(state, { ritOn: true, ritTx: false, ritFreq: 0 });
  state.fieldStatus = { ...(state.fieldStatus ?? {}) };
  for (const path of ['ritOn', 'ritTx', 'ritFreq']) state.fieldStatus[path] = observed(path);
  const caps = {
    ...structuredClone(mockCapabilities), ...structuredClone(topologyCaps),
    controls: { rit: FTX1_RIT_DOMAIN },
  } as unknown as Capabilities;
  return { state, caps };
}

async function boot(page: Page, state: ServerState, caps: Capabilities): Promise<void> {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.addInitScript(({ state }) => {
    localStorage.setItem('rigplane:workspace', JSON.stringify({ version: 1, layout: 'standard', theme: 'nord' }));
    localStorage.setItem('rigplane.i18n.locale', 'en-US');
    const frames: unknown[] = [];
    Object.assign(window, { ritFrames: frames });
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

const OFFSET = '.desktop-control-face.standard-face [data-panel-id="semantic-rit-xit"] [data-testid="ritxit-offset"]';

/** The RIT offset and VFO frequency commands the page sent, in order. */
async function tuningFrames(page: Page): Promise<Array<{ name: string; params: Record<string, unknown> }>> {
  return page.evaluate(() => (window as unknown as { ritFrames: Array<{ type: string; name: string; params: Record<string, unknown> }> })
    .ritFrames.filter((frame) => frame.type === 'cmd' && (frame.name === 'set_rit_frequency' || frame.name === 'set_freq'))
    .map(({ name, params }) => ({ name, params })));
}

async function clearFrames(page: Page): Promise<void> {
  await page.evaluate(() => { (window as unknown as { ritFrames: unknown[] }).ritFrames.length = 0; });
}

/** The click itself positions the offset where it lands, so the frames it
 *  sent are dropped; End then fixes the value the arrows step from. */
async function clickThenStep(page: Page): Promise<void> {
  await page.locator(OFFSET).getByRole('slider').click();
  await clearFrames(page);
  await page.keyboard.press('End');
  await page.keyboard.press('ArrowLeft');
  await page.keyboard.press('ArrowRight');
}

const ONE_HZ_STEPS = [
  { name: 'set_rit_frequency', params: { freq: 9999 } },
  { name: 'set_rit_frequency', params: { freq: 9998 } },
  { name: 'set_rit_frequency', params: { freq: 9999 } },
];

test('after a click on the RIT offset slider the arrows step it by one 1 Hz lattice step and send no set_freq', async ({ page }) => {
  const { state, caps } = ritFixture();
  await boot(page, state, caps);
  await clickThenStep(page);
  await expect.poll(() => tuningFrames(page)).toEqual(ONE_HZ_STEPS);
});

test('the slider keeps the arrows after a click even where the click does not move focus', async ({ page }) => {
  const { state, caps } = ritFixture();
  await boot(page, state, caps);
  // Cancelling mousedown's default action stops the browser from moving
  // focus to the clicked element; the slider must take focus itself.
  await page.evaluate(() => {
    window.addEventListener('mousedown', (event) => event.preventDefault(), { capture: true });
  });
  await clickThenStep(page);
  await expect.poll(() => tuningFrames(page)).toEqual(ONE_HZ_STEPS);
});
