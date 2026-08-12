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
// `element.animate(...)` and then assigns `animation.onfinish` itself to
// know when the outro transition has completed and the element can be
// removed from the DOM (see svelte/internal/client/dom/elements/transitions).
// A stub that never invokes `onfinish` leaves dismissed toasts stuck in the
// DOM forever, which would falsely fail every dismissal assertion below.
// Schedule the completion via `setTimeout` (honoring the animation's own
// `duration`) so it plays nicely with `vi.useFakeTimers()`.
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

describe('Toast — dismissal timing (MOR-1489)', () => {
  // Operators reported error toasts (e.g. the command-failure toast from a
  // mode-error bench case) auto-dismissing before they could be read. Error
  // toasts must now stay on screen until the operator dismisses them; only
  // info/warning toasts keep the old timed auto-dismiss.
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function countToasts(): number {
    return host.querySelectorAll('.toast').length;
  }

  /**
   * Advances the fake clock by `ms` and flushes Svelte's effect queue, then
   * advances a bit further and flushes again so the outro (`fly`)
   * transition's own (polyfilled) animation timer — only *scheduled* once
   * the dismiss effect runs, which itself only happens on the flush after
   * the dismiss timer fires — gets a chance to complete too. A single
   * advance+flush stops short of detaching the node from the DOM.
   */
  function settle(ms: number): void {
    vi.advanceTimersByTime(ms);
    flushSync();
    vi.advanceTimersByTime(200);
    flushSync();
  }

  it('does not auto-dismiss an error toast, even long after the old 5s timeout', () => {
    app = mount(Toast, { target: host });
    flushSync();

    dispatchNotification({
      level: 'error',
      message: 'Command failed: invalid mode for this band',
    });
    flushSync();

    expect(countToasts()).toBe(1);

    settle(60_000);

    expect(countToasts()).toBe(1);
  });

  it('dismisses an error toast when the operator clicks it (existing close affordance)', () => {
    app = mount(Toast, { target: host });
    flushSync();

    dispatchNotification({
      level: 'error',
      message: 'Command failed: invalid mode for this band',
    });
    flushSync();
    expect(countToasts()).toBe(1);

    const toastEl = host.querySelector<HTMLButtonElement>('.toast.error');
    toastEl?.click();
    settle(0);

    expect(countToasts()).toBe(0);
  });

  it('still auto-dismisses a warning toast after the default timeout', () => {
    app = mount(Toast, { target: host });
    flushSync();

    dispatchNotification({
      level: 'warning',
      message: 'Link to the radio is degraded',
    });
    flushSync();
    expect(countToasts()).toBe(1);

    settle(5_000);

    expect(countToasts()).toBe(0);
  });

  it('still auto-dismisses an info toast after the default timeout', () => {
    app = mount(Toast, { target: host });
    flushSync();

    dispatchNotification({
      level: 'info',
      message: 'Radio connected',
      code: 'radioConnected',
    });
    flushSync();
    expect(countToasts()).toBe(1);

    settle(5_000);

    expect(countToasts()).toBe(0);
  });
});

describe('Toast — sticky error cap (MOR-1489 review R2)', () => {
  // Sticky errors removed the old 5s TTL, which doubled as a flood bound:
  // a reconnect-triggered sendQueue flush, a run of acknowledged-then-failed
  // commands, or a control that errors on every click can each emit many
  // `error` toasts back to back. Cap how many stay on screen at once,
  // evicting the oldest first, so a burst can't cover the cockpit in
  // click-intercepting nodes.
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function errorMessages(): string[] {
    return Array.from(host.querySelectorAll('.toast.error .toast-msg')).map(
      (el) => el.textContent?.trim() ?? '',
    );
  }

  it('holds at most MAX_STICKY_ERRORS error toasts, evicting the oldest first', () => {
    app = mount(Toast, { target: host });
    flushSync();

    const total = 5; // MAX_STICKY_ERRORS (3) + 2
    for (let i = 1; i <= total; i++) {
      dispatchNotification({ level: 'error', message: `Command failed #${i}` });
      flushSync();
    }
    // Let every evicted toast's outro transition finish detaching its node.
    vi.advanceTimersByTime(200);
    flushSync();

    const messages = errorMessages();
    expect(messages).toHaveLength(3);
    expect(messages).toEqual(['Command failed #3', 'Command failed #4', 'Command failed #5']);
  });
});
