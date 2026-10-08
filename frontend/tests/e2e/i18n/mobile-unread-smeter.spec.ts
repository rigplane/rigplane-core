/**
 * MOR-2730 — the phone layout draws no S-meter reading until one is read:
 * the portrait S-meter bar and the TX chip's meter-dock S row, on a
 * calibrated radio.
 */
import { expect, test, type Page } from '@playwright/test';
import { writeFileSync } from 'node:fs';
import { mockCapabilities, mockInfo, mockLocalControllerStatus, mockState } from './fixtures';
import type { Capabilities } from '../../../src/lib/types/capabilities';
import type { ServerState } from '../../../src/lib/types/state';

const workspace = { version: 1, layout: 'standard', designLanguage: 'studioline', theme: 'github-light' };

// `rigs/ic7610.toml` `[meters.s_meter]`: the S-meter value is dB relative to S9.
const capabilities = {
  ...mockCapabilities,
  meterCalibrations: { s_meter: [
    { raw: 0, actual: -54, label: 'S0' }, { raw: 26, actual: -48, label: 'S1' },
    { raw: 52, actual: -36, label: 'S3' }, { raw: 78, actual: -24, label: 'S5' },
    { raw: 103, actual: -12, label: 'S7' }, { raw: 130, actual: 0, label: 'S9' },
    { raw: 165, actual: 10, label: 'S9+10' }, { raw: 200, actual: 20, label: 'S9+20' },
    { raw: 240, actual: 40, label: 'S9+40' },
  ] },
} satisfies Capabilities;

// The radio is transmitting (`observedPtt: 'on'` below) with its transmit
// meters read, so the TX chip mounts its meter dock; only the S-meter
// differs between the two cases.
function radioState(sMeter: number | null): ServerState {
  return {
    ...mockState,
    ptt: true,
    powerMeter: 50,
    swrMeter: 1.5,
    alcMeter: 0.5,
    main: { ...mockState.main, sMeter },
    sub: mockState.sub ? { ...mockState.sub, sMeter } : mockState.sub,
    // MOR-2816: the hoisted bar reads through the semantic receiver-host
    // path, which consumes the observation contract — not the raw leaf the
    // retired value-fed strip read. Without a `fieldStatus` entry the
    // adapter reports the field not-observed
    // (`display-observation.ts` qualifyEvidence) and an unqualified domain
    // (`radio-view-model-adapter.ts` meterValueDomain), so even a read
    // value renders as unread. The real backend always publishes these
    // entries (`web/state_schema.py` FieldStatusPublic), so the fixture
    // models one: observed, fresh, and quality-calibrated.
    fieldStatus: {
      'main.sMeter': sMeterFieldStatus('main.sMeter'),
      'sub.sMeter': sMeterFieldStatus('sub.sMeter'),
    },
  };
}

function sMeterFieldStatus(storePath: 'main.sMeter' | 'sub.sMeter') {
  return {
    storePath,
    observed: true,
    freshness: 'fresh' as const,
    availability: 'available' as const,
    lastObservedMonotonic: 0,
    quality: ['calibrated'],
  };
}

const managedTransmit = {
  schemaVersion: 1, sampledAt: '2026-09-27T00:00:00.000Z',
  managedTransmit: { status: 'available', intent: { kind: 'rx' }, releaseRequired: false,
    lastError: null, lastActuation: null, abortErrors: [],
    tot: { configuredSeconds: 180, active: false, remainingMs: null, expiresAt: null } },
  txObservation: { observedPtt: 'on' },
};

test.use({ hasTouch: true, isMobile: true });

async function prepare(page: Page) {
  const server = { state: radioState(null) };
  await page.addInitScript((value) => {
    localStorage.setItem('rigplane:workspace', JSON.stringify(value));
    localStorage.setItem('rigplane.i18n.locale', 'en-US');
  }, workspace);
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (request.method() !== 'GET') {
      await route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
      return;
    }
    const responses: Record<string, unknown> = {
      '/api/v1/state': server.state,
      '/api/v1/capabilities': capabilities,
      '/api/v1/managed-transmit': managedTransmit,
      '/api/v1/info': mockInfo,
      '/api/v1/controller': mockLocalControllerStatus,
    };
    await route.fulfill({ status: pathname.startsWith('/api/local/') ? 404 : 200,
      contentType: 'application/json', body: JSON.stringify(responses[pathname] ?? {}) });
  });
  await page.routeWebSocket(/.*/, (socket) => {
    if (new URL(socket.url()).pathname === '/api/v1/ws') {
      socket.send(JSON.stringify({ type: 'state_update', data: {
        ...server.state, type: 'full', data: server.state,
      } }));
    }
  });
  return server;
}

async function observe(page: Page, name: string, outputPath: (file: string) => string) {
  await page.goto('/');
  await expect(page.locator('.m-layout')).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  // The meter's attack is 50 ms (`signal-meter-motion.svelte.ts`
  // ATTACK_SECONDS); a read value has lit its segments well before this.
  await page.waitForTimeout(1_000);
  const bar = page.locator('.m-smeter-bar');
  await bar.screenshot({ path: outputPath(`${name}-smeter.png`) });
  const smeter = await bar.evaluate((element) => ({
    label: element.querySelector('svg[role="img"]')?.getAttribute('aria-label') ?? null,
    sUnit: element.querySelector('[data-meter-reading]')?.textContent ?? null,
    dbm: element.querySelector('[data-meter-reading-secondary]')?.textContent ?? null,
    litSegments: element.querySelectorAll('[data-meter-fill][visibility="visible"]').length,
    box: (({ x, width, height }) => ({ x, width, height }))(element.getBoundingClientRect()),
  }));
  writeFileSync(outputPath(`${name}-smeter.json`), JSON.stringify(smeter, null, 2));

  await page.locator('.m-chip-bar').getByRole('tab', { name: 'TX', exact: true }).click();
  const sRow = page.locator('.m-tx-meter .dock-row')
    .filter({ has: page.locator('.dock-row-label', { hasText: /^S$/ }) });
  await expect(sRow).toHaveCount(1);
  await page.locator('.m-tx-meter').screenshot({ path: outputPath(`${name}-tx-dock.png`) });
  const txDockS = await sRow.evaluate((element) => ({
    value: element.querySelector('.dock-row-value')?.textContent ?? null,
    fill: element.querySelector<HTMLElement>('.dock-bar-fill')?.style.width ?? null,
    box: (({ x, width, height }) => ({ x, width, height }))(element.getBoundingClientRect()),
  }));
  writeFileSync(outputPath(`${name}-tx-dock.json`), JSON.stringify(txDockS, null, 2));
  return { smeter, txDockS };
}

test('an unread S-meter draws no reading on the phone S-meter or the TX dock S row', async ({ page }, info) => {
  const server = await prepare(page);
  await page.setViewportSize({ width: 390, height: 844 });
  const outputPath = (file: string) => info.outputPath(file);

  server.state = radioState(0);
  const read = await observe(page, 'read', outputPath);
  server.state = radioState(null);
  const unread = await observe(page, 'unread', outputPath);

  // A read S-meter at S9 (0 dB relative to S9) lights the bar, so the
  // lit-segment probe works in this setup. MOR-2816: the hoisted bar is the
  // compact `vfo-wide` face (the migration pin's ruling), whose readout is
  // ONE 13px line — the S-unit — with no dBm line (pinned by
  // `LinearSMeter.mockup-v8.test.ts`); its accessible name carries the
  // S-unit only, so `dbm` probes no node at all (null), not an empty one.
  expect(read.smeter).toMatchObject({ label: 'S meter S9', sUnit: 'S9', dbm: null });
  expect(read.smeter.litSegments).toBeGreaterThan(0);
  expect(read.txDockS.value).toBe('S9');
  expect(Number.parseFloat(read.txDockS.fill ?? '')).toBeGreaterThan(0);

  expect(unread.smeter).toMatchObject({ label: 'S meter', sUnit: '', dbm: null, litSegments: 0 });
  expect(unread.txDockS).toMatchObject({ value: '', fill: '0%' });

  // The first reading changes neither box's size or horizontal position.
  expect(unread.smeter.box).toEqual(read.smeter.box);
  expect(unread.txDockS.box).toEqual(read.txDockS.box);
});
