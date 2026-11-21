/**
 * MOR-4250 — controller error toasts render from the i18n catalogs.
 *
 * Customer bug: `ControllerClientError` messages were hardcoded in Russian,
 * so the English UI showed "Backend error: Error: Удалённое управление
 * потеряно…". The errors now carry wire codes, `CONTROLLER_TOAST_CODES`
 * converts them to camelCase toast codes, and the Toast surface resolves
 * `core.toast.<code>` through `messageFromReasonCode` — one mechanism for
 * every locale.
 *
 * These tests pin the acceptance criteria: the same `controllerLost`
 * notification renders the en-US catalog text under en-US and the ru-RU
 * catalog text under ru-RU, and none of the controller codes falls back to
 * the English `message` field or to `core.toast.unknown`.
 *
 * The transport layer is mocked: the registered `onMessage` handler is
 * captured and a synthetic `notification` payload is dispatched, mirroring
 * `Toast.link-loss.component.test.ts`.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import Toast from '../Toast.svelte';
import { setLocale } from '$lib/i18n';
import { _resetLocale } from '$lib/i18n/store.svelte';
import { CONTROLLER_TOAST_CODES } from '$lib/transport/controller-client';

const CONTROLLER_LOST_EN = 'Remote control was lost. Reconnect.';
const CONTROLLER_LOST_RU = 'Удалённое управление потеряно. Подключитесь заново.';

type Handler = (msg: Record<string, unknown>) => void;
let registered: Handler[] = [];

vi.mock('../../../lib/transport/ws-client', () => ({
  onMessage: (h: Handler) => {
    registered.push(h);
    return () => {
      registered = registered.filter((x) => x !== h);
    };
  },
}));

vi.mock('$lib/stores/connection.svelte', () => ({
  getRadioLinkState: vi.fn(() => 'connected'),
}));

function dispatchNotification(payload: Record<string, unknown>): void {
  for (const h of registered) {
    h({ type: 'notification', ...payload });
  }
}

/** The frontend-synthesized controller-loss notice (`ws-client` onLoss). */
function controllerLostNotice(): Record<string, unknown> {
  return {
    level: 'error',
    message: CONTROLLER_LOST_EN,
    category: 'command',
    code: CONTROLLER_TOAST_CODES.controller_lost,
  };
}

let host: HTMLDivElement;
let app: ReturnType<typeof mount> | null = null;

// Same jsdom Web Animations stub as Toast.link-loss.component.test.ts:
// Svelte's `fly` transition needs `element.animate(...)` with an
// `onfinish` completion.
if (typeof (Element.prototype as any).animate !== 'function') {
  (Element.prototype as any).animate = function animate(
    _keyframes: unknown,
    options?: number | { duration?: number },
  ): Animation {
    const duration = typeof options === 'number' ? options : (options?.duration ?? 0);
    const fake = {
      playState: 'running' as AnimationPlayState,
      currentTime: 0,
      onfinish: null as (() => void) | null,
      oncancel: null,
      cancel() {
        fake.playState = 'idle';
      },
      finish() {
        fake.playState = 'finished';
        fake.onfinish?.();
      },
      addEventListener() {},
      removeEventListener() {},
    };
    setTimeout(() => {
      if (fake.playState === 'running') {
        fake.playState = 'finished';
        fake.onfinish?.();
      }
    }, duration);
    return fake as unknown as Animation;
  };
}

beforeEach(() => {
  registered = [];
  host = document.createElement('div');
  document.body.appendChild(host);
  localStorage.clear();
  _resetLocale();
  setLocale('en-US');
});

afterEach(() => {
  if (app) {
    unmount(app);
    app = null;
  }
  host.remove();
  localStorage.clear();
});

function errorMessages(): string[] {
  return Array.from(host.querySelectorAll('.toast.error .toast-msg')).map(
    (el) => el.textContent?.trim() ?? '',
  );
}

describe('Toast — MOR-4250 controller reason codes', () => {
  it('renders controllerLost from the en-US catalog under en-US', () => {
    app = mount(Toast, { target: host });
    flushSync();

    dispatchNotification(controllerLostNotice());
    flushSync();

    expect(errorMessages()).toEqual([CONTROLLER_LOST_EN]);
  });

  it('re-resolves the rendered toast when the locale changes to ru-RU', () => {
    app = mount(Toast, { target: host });
    flushSync();

    dispatchNotification(controllerLostNotice());
    flushSync();
    expect(errorMessages()).toEqual([CONTROLLER_LOST_EN]);

    setLocale('ru-RU');
    flushSync();

    // renderToast re-resolves catalog text on locale change: the already
    // shown toast follows the new locale instead of freezing the en text.
    expect(errorMessages()).toEqual([CONTROLLER_LOST_RU]);
  });

  it('renders every controller wire code as a localized catalog entry, never core.toast.unknown', () => {
    app = mount(Toast, { target: host });
    flushSync();

    const EXPECTED_EN: Record<string, string> = {
      controllerBusy: 'Remote control is held by another client. Connect once it is released.',
      controllerInvalid: 'The station denied access to remote control.',
      controllerLost: CONTROLLER_LOST_EN,
      controllerNotReady: 'The station is not ready for remote control yet.',
      controllerUnsupported: 'The station does not support this control protocol.',
      controllerLocalRequired: 'This action is only available on the local host.',
      remoteDisabled: 'Remote control is disabled on this host.',
    };
    for (const [wire, code] of Object.entries(CONTROLLER_TOAST_CODES)) {
      dispatchNotification({
        level: 'error', message: 'English fallback', category: 'command', code,
      });
      flushSync();
      const rendered = errorMessages().at(-1) ?? '';
      expect(rendered, `wire code ${wire}`).toBe(EXPECTED_EN[code]);
      expect(rendered).not.toBe('English fallback');
      expect(rendered).not.toBe('Something went wrong. Try again later.');
    }
  });
});
