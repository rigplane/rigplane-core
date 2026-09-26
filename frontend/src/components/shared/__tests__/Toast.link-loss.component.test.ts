/**
 * MOR-2241 — one link-loss notice instead of a flood of
 * "provider generation invalidated" toasts.
 *
 * Server path (structured, not message-text matching):
 *   `src/rigplane/web/server.py: _on_provider_generation` terminates
 *   in-flight commands with reason "provider generation invalidated";
 *   `_on_command_lifecycle_event` forwards each as a targeted notification
 *   with `code: 'commandExecutionFailed'` and
 *   `params: { reason: 'provider generation invalidated' }`.
 *
 * While the radio link is not connected
 * (`getRadioLinkState() !== 'connected'` — the same derivation that drives
 * the StatusBar link chip), those link-loss terminations must not become
 * per-command toasts. The chip is the persistent disconnected indicator.
 *
 * The transport layer is mocked: the registered `onMessage` handler is
 * captured and synthetic `notification` payloads are dispatched, mirroring
 * `Toast.component.test.ts`. The link state comes from a controllable mock
 * of `$lib/stores/connection.svelte` so the tests can drive the
 * connected -> disconnected -> connected transitions directly.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import Toast from '../Toast.svelte';
import { setLocale } from '$lib/i18n';
import { _resetLocale } from '$lib/i18n/store.svelte';
import { getRadioLinkState } from '$lib/stores/connection.svelte';

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

/** A server-sent link-loss termination, exactly as `_on_command_lifecycle_event` builds it. */
function linkLossTermination(): Record<string, unknown> {
  return {
    level: 'error',
    message: 'Command failed: provider generation invalidated',
    category: 'command',
    code: 'commandExecutionFailed',
    params: { reason: 'provider generation invalidated' },
  };
}

let host: HTMLDivElement;
let app: ReturnType<typeof mount> | null = null;

// Same jsdom Web Animations stub as Toast.component.test.ts: Svelte's `fly`
// transition needs `element.animate(...)` with an `onfinish` completion.
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
  vi.mocked(getRadioLinkState).mockReturnValue('connected');
});

afterEach(() => {
  if (app) {
    unmount(app);
    app = null;
  }
  host.remove();
  localStorage.clear();
});

function countToasts(): number {
  return host.querySelectorAll('.toast').length;
}

function errorMessages(): string[] {
  return Array.from(host.querySelectorAll('.toast.error .toast-msg')).map(
    (el) => el.textContent?.trim() ?? '',
  );
}

describe('Toast — link-loss termination suppression (MOR-2241)', () => {
  it('produces no per-command toast for N link-loss terminations while disconnected', () => {
    app = mount(Toast, { target: host });
    flushSync();

    // The link-loss transition: the StatusBar chip leaves 'connected'.
    vi.mocked(getRadioLinkState).mockReturnValue('disconnected');

    // The bench case: three in-flight commands terminated at once.
    for (let i = 0; i < 3; i++) {
      dispatchNotification(linkLossTermination());
      flushSync();
    }

    expect(countToasts()).toBe(0);
  });

  it('still toasts an unrelated command error while disconnected', () => {
    app = mount(Toast, { target: host });
    flushSync();

    vi.mocked(getRadioLinkState).mockReturnValue('disconnected');

    dispatchNotification({
      level: 'error',
      message: 'Command failed: radio did not respond',
      category: 'command',
      code: 'commandExecutionFailed',
      params: { reason: 'radio did not respond' },
    });
    flushSync();

    expect(errorMessages()).toEqual(['Command failed: radio did not respond']);
  });

  it('toasts a termination-type failure again after the link is restored', () => {
    app = mount(Toast, { target: host });
    flushSync();

    // Lost, then restored: the chip is back to 'connected'.
    vi.mocked(getRadioLinkState).mockReturnValue('disconnected');
    dispatchNotification(linkLossTermination());
    flushSync();
    expect(countToasts()).toBe(0);

    vi.mocked(getRadioLinkState).mockReturnValue('connected');
    dispatchNotification(linkLossTermination());
    flushSync();

    expect(errorMessages()).toEqual(['Command failed: provider generation invalidated']);
  });
});
