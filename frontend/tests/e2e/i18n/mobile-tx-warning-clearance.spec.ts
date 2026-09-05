import { expect, test, type Locator, type Page } from '@playwright/test';
import { writeFileSync } from 'node:fs';
import { mockCapabilities, mockInfo, mockState } from './fixtures';
import type { Capabilities } from '../../../src/lib/types/capabilities';
import type { ServerState } from '../../../src/lib/types/state';

const workspace = {
  version: 1,
  layout: 'standard',
  designLanguage: 'studioline',
  theme: 'github-light',
};

const state = {
  ...mockState,
  active: 'MAIN',
  main: { ...mockState.main, dataMode: 0 },
  dataOffModInput: 3,
} satisfies ServerState;

const capabilities = {
  ...mockCapabilities,
  capabilities: [...new Set([...mockCapabilities.capabilities, 'data_mode', 'mod_input_routing'])],
  dataModeCount: 3,
  audioTx: true,
  audioTxRoute: 'lan',
  audioTxRequiredModInputSource: 5,
} satisfies Capabilities;

const managedTransmit = {
  schemaVersion: 1,
  sampledAt: '2026-09-05T04:00:00.000Z',
  managedTransmit: {
    status: 'available',
    intent: { kind: 'rx' },
    releaseRequired: false,
    lastError: null,
    lastActuation: null,
    abortErrors: [],
    tot: { configuredSeconds: 180, active: false, remainingMs: null, expiresAt: null },
  },
  txObservation: { observedPtt: 'off' },
};

interface ControlCommand {
  id: string;
  name: string;
}

test.use({ hasTouch: true, isMobile: true });

async function prepare(page: Page) {
  const controlCommands: ControlCommand[] = [];
  const managedCommands: string[] = [];

  await page.addInitScript((value) => {
    localStorage.setItem('rigplane:workspace', JSON.stringify(value));
    localStorage.setItem('rigplane.i18n.locale', 'en-US');

    const browser = window as Window & {
      __txMedia?: { context: AudioContext; oscillator: OscillatorNode; stream: MediaStream };
      __trustedPointerTargets?: Array<{ event: string; trusted: boolean; target: string }>;
    };
    browser.__trustedPointerTargets = [];
    Reflect.defineProperty(window, 'AudioEncoder', { configurable: true, value: undefined });
    Reflect.defineProperty(window, 'MediaStreamTrackProcessor', { configurable: true, value: undefined });
    for (const eventName of ['pointerdown', 'pointerup', 'pointercancel', 'lostpointercapture']) {
      document.addEventListener(eventName, (event) => {
        const target = event.target instanceof Element ? event.target : null;
      browser.__trustedPointerTargets?.push({
          event: event.type,
          trusted: event.isTrusted,
          target: target?.closest('button')?.getAttribute('aria-label')
            ?? target?.getAttribute('data-testid')
            ?? target?.tagName
            ?? 'unknown',
        });
      }, true);
    }

    if (!navigator.mediaDevices) {
      Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {} });
    }
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {
      configurable: true,
      value: async () => {
        if (!browser.__txMedia) {
          const context = new AudioContext();
          const destination = context.createMediaStreamDestination();
          const oscillator = context.createOscillator();
          oscillator.connect(destination);
          oscillator.start();
          browser.__txMedia = { context, oscillator, stream: destination.stream };
        }
        return browser.__txMedia.stream.clone();
      },
    });
  }, workspace);

  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (request.method() === 'POST' && pathname === '/api/v1/managed-transmit/command') {
      const body = request.postDataJSON() as { operation: string };
      managedCommands.push(body.operation);
      await route.fulfill({ status: 202, contentType: 'application/json', body: '{}' });
      return;
    }
    if (request.method() !== 'GET') {
      await route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
      return;
    }
    const responses: Record<string, unknown> = {
      '/api/v1/state': state,
      '/api/v1/capabilities': capabilities,
      '/api/v1/managed-transmit': managedTransmit,
      '/api/v1/info': mockInfo,
    };
    await route.fulfill({
      status: pathname.startsWith('/api/local/') ? 404 : 200,
      contentType: 'application/json',
      body: JSON.stringify(responses[pathname] ?? {}),
    });
  });

  await page.routeWebSocket(/.*/, (socket) => {
    const pathname = new URL(socket.url()).pathname;
    socket.onMessage((message) => {
      if (pathname !== '/api/v1/ws' || typeof message !== 'string') return;
      const frame = JSON.parse(message) as { id?: string; name?: string; type?: string };
      if ((frame.type !== 'cmd' && frame.type !== 'command') || !frame.id || !frame.name) return;
      controlCommands.push({ id: frame.id, name: frame.name });
      setTimeout(() => socket.send(JSON.stringify({ type: 'response', id: frame.id, ok: true })), 0);
    });
    if (pathname === '/api/v1/ws') {
      socket.send(JSON.stringify({ type: 'state_update', data: { ...state, type: 'full', data: state } }));
    }
  });

  return { controlCommands, managedCommands };
}

async function settled(page: Page) {
  await expect(page.locator('.m-layout, .m-landscape')).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
}

async function hitAtCenter(locator: Locator) {
  return locator.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    const hit = document.elementFromPoint(x, y);
    return {
      x,
      y,
      hit: hit instanceof Element
        ? hit.closest('button')?.getAttribute('aria-label')
          ?? hit.getAttribute('data-testid')
          ?? hit.getAttribute('aria-label')
          ?? hit.tagName
        : null,
      contained: hit !== null && element.contains(hit),
    };
  });
}

async function holdAndRelease(page: Page, locator: Locator, whileHeld: () => Promise<void>) {
  const box = await locator.boundingBox();
  expect(box).not.toBeNull();
  const x = box!.x + box!.width / 2;
  const y = box!.y + box!.height / 2;
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Input.dispatchTouchEvent', {
    type: 'touchStart',
    touchPoints: [{ x, y }],
  });
  await page.waitForTimeout(90);
  await whileHeld();
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await cdp.detach();
}

test('persistent LAN MOD warning leaves mobile TX recovery controls reachable', async ({ page }, info) => {
  const commands = await prepare(page);
  await page.setViewportSize({ width: 430, height: 932 });
  await page.goto('/');
  await settled(page);

  const fab = page.getByRole('button', { name: 'Push to talk' });
  const floatingWarning = page.locator('.m-mod-input-warning').getByTestId('mod-input-tx-warning');
  await holdAndRelease(page, fab, async () => {
    await expect.poll(() => commands.controlCommands.map(({ name }) => name)).toEqual(['ptt_on']);
  });
  await expect(floatingWarning).toBeVisible();
  await page.waitForTimeout(400);

  const secondPressHit = await hitAtCenter(fab);
  await info.attach('portrait-warning-hit-test', {
    body: JSON.stringify(secondPressHit, null, 2),
    contentType: 'application/json',
  });
  await page.screenshot({ path: info.outputPath('portrait-warning.png') });
  expect(secondPressHit.contained).toBe(true);
  expect(secondPressHit.hit).toBe('Push to talk');

  await holdAndRelease(page, fab, async () => {
    await expect.poll(() => commands.controlCommands.filter(({ name }) => name === 'ptt_on'))
      .toHaveLength(2);
  });
  await expect.poll(() => commands.controlCommands.map(({ name }) => name), { timeout: 2_000 })
    .toEqual(['ptt_on', 'ptt_off', 'ptt_on', 'ptt_off']);

  const trustedPointers = await page.evaluate(() => (
    window as Window & { __trustedPointerTargets?: Array<{ event: string; trusted: boolean; target: string }> }
  ).__trustedPointerTargets ?? []);
  expect(trustedPointers.filter(({ event, target }) =>
    (event === 'pointerdown' || event === 'pointerup') && target === 'Push to talk'))
    .toEqual([
      { event: 'pointerdown', trusted: true, target: 'Push to talk' },
      { event: 'pointerup', trusted: true, target: 'Push to talk' },
      { event: 'pointerdown', trusted: true, target: 'Push to talk' },
      { event: 'pointerup', trusted: true, target: 'Push to talk' },
    ]);

  await page.setViewportSize({ width: 932, height: 430 });
  await expect(page.locator('.m-landscape')).toBeVisible();
  await expect(floatingWarning).toBeVisible();
  const landscapePtt = page.locator('.m-ls-ptt');
  const unkey = page.locator('.m-ls-unkey');
  expect((await hitAtCenter(landscapePtt)).contained).toBe(true);
  expect((await hitAtCenter(unkey)).contained).toBe(true);
  const forceOffsBeforeClick = commands.managedCommands.filter((name) => name === 'force_off').length;
  expect(forceOffsBeforeClick).toBe(0);
  await unkey.click();
  await expect.poll(() => commands.managedCommands.filter((name) => name === 'force_off'))
    .toHaveLength(forceOffsBeforeClick + 1);

  await page.evaluate(async () => {
    if (document.fullscreenElement) await document.exitFullscreen();
  });
  await page.setViewportSize({ width: 430, height: 932 });
  await expect(page.locator('.m-layout')).toBeVisible();
  await page.locator('.m-content').evaluate((element) => { element.scrollTop = element.scrollHeight; });
  await expect(floatingWarning).toBeVisible();
  expect((await hitAtCenter(fab)).contained).toBe(true);
  await floatingWarning.getByTestId('mod-input-dismiss').click();
  await expect(page.getByTestId('mod-input-tx-warning')).toHaveCount(0);
  writeFileSync(info.outputPath('evidence.json'), JSON.stringify({
    secondPressHit,
    controlCommands: commands.controlCommands,
    managedCommands: commands.managedCommands,
    trustedPointers,
  }, null, 2));
});
