/**
 * Component-level tests for Toast.svelte (RP-ML-005).
 *
 * Verifies the additive `code`/`params` wire schema:
 *   - notifications with `code` resolve to `core.toast.<code>` via the i18n runtime
 *     and re-render on locale change;
 *   - notifications without `code` keep showing the legacy English `message`
 *     verbatim (backward-compat path for any out-of-tree producer);
 *   - unknown codes fall back to `core.toast.unknown`;
 *   - placeholder substitution flows from `params` into the resolved string.
 *
 * The transport layer is mocked: we capture the registered handler and
 * dispatch synthetic `notification` payloads, since hand-rolling a WebSocket
 * in jsdom would obscure the actual contract under test.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import Toast from '../Toast.svelte';
import { setLocale } from '$lib/i18n';
import { _resetLocale } from '$lib/i18n/store.svelte';

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

function dispatchNotification(payload: Record<string, unknown>): void {
  for (const h of registered) {
    h({ type: 'notification', ...payload });
  }
}

let host: HTMLDivElement;
let app: ReturnType<typeof mount> | null = null;

// jsdom lacks Web Animations API; Svelte's `fly` transition calls
// `element.animate(...)`. Stub with a noop animation so the toast mounts.
if (typeof (Element.prototype as any).animate !== 'function') {
  (Element.prototype as any).animate = function animate(): Animation {
    return {
      cancel() {},
      finish() {},
      onfinish: null,
      oncancel: null,
      addEventListener() {},
      removeEventListener() {},
    } as unknown as Animation;
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

function getToastText(): string {
  const msg = host.querySelector('.toast-msg');
  return msg?.textContent?.trim() ?? '';
}

describe('Toast — reason-code resolution', () => {
  it('renders the localized message for a known reason code', () => {
    app = mount(Toast, { target: host });
    flushSync();

    dispatchNotification({
      level: 'success',
      message: 'Radio connected',
      code: 'radioConnected',
    });
    flushSync();

    expect(getToastText()).toBe('Radio connected');
  });

  it('falls back to core.toast.unknown for an unknown reason code', () => {
    app = mount(Toast, { target: host });
    flushSync();

    dispatchNotification({
      level: 'info',
      message: 'Whatever the server typed',
      code: 'completelyUnknownCode',
    });
    flushSync();

    expect(getToastText()).toBe('Something went wrong. Try again later.');
  });

  it('threads params into the resolved message', () => {
    app = mount(Toast, { target: host });
    flushSync();

    dispatchNotification({
      level: 'info',
      message: 'An update is available: 2.1.0.',
      code: 'updateAvailable',
      params: { version: '2.1.0' },
    });
    flushSync();

    expect(getToastText()).toBe('An update is available: 2.1.0.');
  });

  it('keeps the legacy English message verbatim when no code is provided', () => {
    app = mount(Toast, { target: host });
    flushSync();

    dispatchNotification({
      level: 'warning',
      message: 'Free-form English from a legacy producer',
    });
    flushSync();

    expect(getToastText()).toBe('Free-form English from a legacy producer');
  });

  it('resolves Japanese for a known reason code when locale=ja-JP and a translation exists', () => {
    setLocale('ja-JP');
    app = mount(Toast, { target: host });
    flushSync();

    // ja-JP is now a complete pilot translation, so `radioConnected`
    // resolves to its Japanese value rather than falling back to en-US.
    dispatchNotification({
      level: 'success',
      message: 'Radio connected',
      code: 'radioConnected',
    });
    flushSync();

    expect(getToastText()).toBe('トランシーバーを接続しました');
  });

  it('wraps the resolved message in pseudo-locale brackets under qps-ploc', () => {
    setLocale('qps-ploc');
    app = mount(Toast, { target: host });
    flushSync();

    dispatchNotification({
      level: 'success',
      message: 'Audio bridge started',
      code: 'audioBridgeStarted',
    });
    flushSync();

    const txt = getToastText();
    expect(txt.startsWith('⟦')).toBe(true);
    expect(txt.endsWith('⟧')).toBe(true);
  });
});
