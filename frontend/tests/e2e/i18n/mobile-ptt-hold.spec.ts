import { expect, test, type CDPSession, type Locator, type Page } from '@playwright/test';
import { fulfillControllerRoute, mockCapabilities, mockControllerHeartbeatAck, mockInfo, mockLocalControllerStatus, mockState } from './fixtures';
import type { Capabilities } from '../../../src/lib/types/capabilities';
import type { ServerState } from '../../../src/lib/types/state';

/**
 * MOR-3817 — a held phone PTT keeps transmitting when the phone rotates or
 * the finger slides, and stops when the finger lifts or the page is hidden.
 * Touches are real CDP touch input; PTT ON/OFF are read from the WS
 * commands the page sends, and the rendered PTT button's held class is
 * checked against them.
 *
 * Mocked exchange. The Python tests that pin the shapes this fake speaks:
 * - WS `ptt_on` / `ptt_off` admission and result `{}`:
 *   tests/test_web_managed_ptt_rejection.py
 *   (test_first_client_ptt_admits_through_the_shared_queue,
 *   test_the_holder_releases_with_ptt_off);
 * - the WS success envelope `{type: 'response', id, ok: true, result}`:
 *   `web/handlers/control.py`, whose refusal envelope is pinned by
 *   test_second_client_ws_envelope_carries_the_conflict_message;
 * - the GET /api/v1/managed-transmit document:
 *   tests/test_managed_tx_http_route.py
 *   (test_get_projects_one_authority_snapshot_and_separate_observation).
 * This spec does not replay its own documents against the real handlers.
 */

const workspace = { version: 1, layout: 'standard', designLanguage: 'studioline', theme: 'github-light' };
const state = mockState satisfies ServerState;
const capabilities = mockCapabilities satisfies Capabilities;
const PORTRAIT = { width: 430, height: 932 };
const LANDSCAPE = { width: 932, height: 430 };

type Intent = 'rx' | 'ptt' | 'transmit';

function managedTransmitDocument(intent: Intent) {
  return {
    schemaVersion: 1,
    sampledAt: new Date().toISOString(),
    managedTransmit: {
      status: 'available',
      intent: intent === 'ptt' ? { kind: 'ptt', owner: 'mobile-e2e-session' }
        : intent === 'transmit' ? { kind: 'transmit' } : { kind: 'rx' },
      releaseRequired: intent !== 'rx',
      lastError: null,
      lastActuation: null,
      abortErrors: [],
      tot: { configuredSeconds: 180, active: false, remainingMs: null, expiresAt: null },
    },
    txObservation: { observedPtt: intent === 'rx' ? 'off' : 'on' },
  };
}

function admitted(command: string): Intent | null {
  if (command === 'ptt_on') return 'ptt';
  if (command === 'transmit_on') return 'transmit';
  if (command === 'ptt_off' || command === 'force_off') return 'rx';
  return null;
}

test.use({ hasTouch: true, isMobile: true });

async function prepare(page: Page) {
  const commands: string[] = [];
  let intent: Intent = 'rx';
  await page.addInitScript((value) => {
    localStorage.setItem('rigplane:workspace', JSON.stringify(value));
    localStorage.setItem('rigplane.i18n.locale', 'en-US');
    const browser = window as Window & { __txStream?: MediaStream };
    Reflect.defineProperty(window, 'AudioEncoder', { configurable: true, value: undefined });
    Reflect.defineProperty(window, 'MediaStreamTrackProcessor', { configurable: true, value: undefined });
    if (!navigator.mediaDevices) {
      Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {} });
    }
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {
      configurable: true,
      value: async () => {
        if (!browser.__txStream) {
          const context = new AudioContext();
          const destination = context.createMediaStreamDestination();
          const oscillator = context.createOscillator();
          oscillator.connect(destination);
          oscillator.start();
          browser.__txStream = destination.stream;
        }
        return browser.__txStream.clone();
      },
    });
  }, workspace);

  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (request.method() === 'POST' && pathname === '/api/v1/managed-transmit/command') {
      const { operation } = request.postDataJSON() as { operation: string };
      commands.push(operation);
      intent = admitted(operation) ?? intent;
      await route.fulfill({ status: 202, contentType: 'application/json', body: '{}' });
      return;
    }
    if (request.method() !== 'GET') {
      if (pathname === '/api/v1/controller') return fulfillControllerRoute(route);
      await route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
      return;
    }
    const responses: Record<string, unknown> = {
      '/api/v1/state': state,
      '/api/v1/capabilities': capabilities,
      '/api/v1/managed-transmit': managedTransmitDocument(intent),
      '/api/v1/info': mockInfo,
      '/api/v1/controller': mockLocalControllerStatus,
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
      if (frame.type === 'controller_heartbeat') {
        socket.send(JSON.stringify(mockControllerHeartbeatAck));
        return;
      }
      if ((frame.type !== 'cmd' && frame.type !== 'command') || !frame.id || !frame.name) return;
      commands.push(frame.name);
      intent = admitted(frame.name) ?? intent;
      setTimeout(() => socket.send(JSON.stringify({ type: 'response', id: frame.id, ok: true, result: {} })), 0);
    });
    if (pathname === '/api/v1/ws') {
      socket.send(JSON.stringify({ type: 'state_update', data: { ...state, type: 'full', data: state } }));
    }
  });
  return commands;
}

async function open(page: Page, viewport: { width: number; height: number }) {
  await page.setViewportSize(viewport);
  await page.goto('/');
  await expect(page.locator('.m-layout, .m-landscape')).toBeVisible();
}

class Finger {
  private constructor(private readonly cdp: CDPSession, public x: number, public y: number) {}

  static async down(page: Page, button: Locator) {
    const box = await button.boundingBox();
    expect(box).not.toBeNull();
    const cdp = await page.context().newCDPSession(page);
    const finger = new Finger(cdp, box!.x + box!.width / 2, box!.y + box!.height / 2);
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: finger.x, y: finger.y }] });
    return finger;
  }

  async slide(dx: number, dy: number, steps = 8) {
    for (let step = 1; step <= steps; step += 1) {
      await this.cdp.send('Input.dispatchTouchEvent', {
        type: 'touchMove',
        touchPoints: [{ x: this.x + (dx * step) / steps, y: this.y + (dy * step) / steps }],
      });
    }
    this.x += dx;
    this.y += dy;
  }

  async lift() {
    await this.cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await this.cdp.detach();
  }
}

const settle = (page: Page) => page.waitForTimeout(700);

/** The one mounted PTT button (FAB in portrait, strip button in landscape) shows held or idle. */
async function expectRenderedHeld(page: Page, held: boolean) {
  const button = page.locator('.ptt-fab, .m-ls-ptt');
  if (held) await expect(button).toHaveClass(/ptt-fab-held|m-ptt-held/);
  else await expect(button).not.toHaveClass(/ptt-fab-held|m-ptt-held/);
}

/** Landscape entry requests fullscreen; the browser resizes only a normal window. */
async function rotate(page: Page, viewport: { width: number; height: number }) {
  await page.evaluate(async () => {
    if (document.fullscreenElement) await document.exitFullscreen();
  });
  await page.setViewportSize(viewport);
  await expect(page.locator(viewport === LANDSCAPE ? '.m-landscape' : '.m-layout')).toBeVisible();
}

test('a held FAB keeps transmitting through rotation and stops when the finger lifts', async ({ page }) => {
  const commands = await prepare(page);
  await open(page, PORTRAIT);
  const finger = await Finger.down(page, page.locator('.ptt-fab'));
  await expect.poll(() => commands).toEqual(['ptt_on']);
  await expectRenderedHeld(page, true);

  await rotate(page, LANDSCAPE);
  await settle(page);
  expect(commands).toEqual(['ptt_on']);
  await expectRenderedHeld(page, true);

  await rotate(page, PORTRAIT);
  await settle(page);
  expect(commands).toEqual(['ptt_on']);
  await expectRenderedHeld(page, true);

  await finger.lift();
  await expect.poll(() => commands).toEqual(['ptt_on', 'ptt_off']);
  await expectRenderedHeld(page, false);
});

test('a held landscape PTT keeps transmitting through rotation to portrait', async ({ page }) => {
  const commands = await prepare(page);
  await open(page, LANDSCAPE);
  const finger = await Finger.down(page, page.locator('.m-ls-ptt'));
  await expect.poll(() => commands).toEqual(['ptt_on']);
  await expectRenderedHeld(page, true);

  await rotate(page, PORTRAIT);
  await settle(page);
  expect(commands).toEqual(['ptt_on']);
  await expectRenderedHeld(page, true);

  await finger.lift();
  await expect.poll(() => commands).toEqual(['ptt_on', 'ptt_off']);
  await expectRenderedHeld(page, false);
});

for (const [name, viewport, selector] of [
  ['FAB', PORTRAIT, '.ptt-fab'],
  ['landscape PTT', LANDSCAPE, '.m-ls-ptt'],
] as const) {
  test(`a held ${name} keeps transmitting while the finger slides off it`, async ({ page }) => {
    const commands = await prepare(page);
    await open(page, viewport);
    const button = page.locator(selector);
    const finger = await Finger.down(page, button);
    await expect.poll(() => commands).toEqual(['ptt_on']);
    await expectRenderedHeld(page, true);

    await finger.slide(-90, -90);
    const box = await button.boundingBox();
    expect(finger.x < box!.x || finger.y < box!.y, 'the finger ends outside the button').toBe(true);
    await settle(page);
    expect(commands).toEqual(['ptt_on']);
    await expectRenderedHeld(page, true);

    await finger.lift();
    await expect.poll(() => commands).toEqual(['ptt_on', 'ptt_off']);
    await expectRenderedHeld(page, false);
  });
}

test('lifting the finger ends a held FAB press', async ({ page }) => {
  const commands = await prepare(page);
  await open(page, PORTRAIT);
  const finger = await Finger.down(page, page.locator('.ptt-fab'));
  await expect.poll(() => commands).toEqual(['ptt_on']);
  await expectRenderedHeld(page, true);
  await finger.lift();
  await expect.poll(() => commands).toEqual(['ptt_on', 'ptt_off']);
  await expectRenderedHeld(page, false);
});

test('the page becoming hidden ends a held FAB press before the finger lifts', async ({ page }) => {
  const commands = await prepare(page);
  await open(page, PORTRAIT);
  const finger = await Finger.down(page, page.locator('.ptt-fab'));
  await expect.poll(() => commands).toEqual(['ptt_on']);
  await expectRenderedHeld(page, true);

  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect.poll(() => commands).toEqual(['ptt_on', 'ptt_off']);
  await expectRenderedHeld(page, false);

  await finger.lift();
  await settle(page);
  expect(commands).toEqual(['ptt_on', 'ptt_off']);
});
