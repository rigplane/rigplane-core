import { test, expect, type Locator, type Page } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
import { mockCapabilities, mockInfo, mockState } from './fixtures';

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
      fields[path] = { observed: true, freshness: 'fresh', availability: 'available', storePath: path };
      if (value && typeof value === 'object') observe(value, path);
    }
  }
  if (known) observe(state);
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

async function boot(page: Page, layout: string, width: number, known: boolean) {
  const { state, caps } = fixture(known);
  await page.setViewportSize({ width, height: width === 900 ? 900 : 1000 });
  await page.addInitScript(({ state, layout, width }) => {
    localStorage.setItem('rigplane:workspace', JSON.stringify({ version: 1, layout,
      designLanguage: 'studioline', theme: width === 900 ? 'github-light' : 'nord' }));
    localStorage.setItem('rigplane.i18n.locale', width === 900 ? 'ru-RU' : 'en-US');
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
            const e = new MessageEvent('message', { data: JSON.stringify({ type: 'state_update',
              data: { type: 'full', data: state, revision: state.revision,
                stateRevision: state.stateRevision, freshnessRevision: state.freshnessRevision,
                observationSeq: state.observationSeq, stateContractVersion: state.stateContractVersion,
                providerGeneration: state.providerGeneration } }) });
            this.dispatchEvent(e); this.onmessage?.(e);
          }
        });
      }
      send(data: string) { commands.push(JSON.parse(data)); }
      close() { this.readyState = 3; const e = new Event('close'); this.dispatchEvent(e); this.onclose?.(e); }
    }
    Object.assign(window, { WebSocket: Socket });
  }, { state, layout, width });
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
  await page.goto(`/?locale=${width === 900 ? 'ru-RU' : 'en-US'}`, { waitUntil: 'networkidle' });
  await expect(page.locator(layout.startsWith('lcd') ? '.lcd-layout' : '.desktop-control-face')).toBeVisible();
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

for (const layout of ['standard', 'sdr-test', 'lcd-scope', 'lcd-cockpit']) {
  for (const width of [900, 1440]) for (const known of [true, false]) {
    test(`${layout} ${width} ${known ? 'observed IC' : 'unknown'} geometry`, async ({ page }, info) => {
      const errors: string[] = []; page.on('pageerror', e => errors.push(String(e)));
      await boot(page, layout, width, known);
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
        const meters = await page.locator('[data-zone-id="meters"]').boundingBox();
        expect.soft(center!.height, 'scope uses available space or its existing 280px floor')
          .toBeLessThanOrEqual(Math.max(280, page.viewportSize()!.height - center!.y - meters!.height));
        expect.soft(center!.y + center!.height, 'scope ends before station meters').toBeLessThanOrEqual(meters!.y + 1);
        await page.locator('[data-zone-id="meters"]').scrollIntoViewIfNeeded();
        await expect(page.locator('[data-zone-id="meters"]')).toBeInViewport();
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
