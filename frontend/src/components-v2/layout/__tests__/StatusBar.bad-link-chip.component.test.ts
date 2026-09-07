/**
 * MOR-2425 R29(3) — `connection.svelte.ts: isStale()` (exposed as
 * `frontend-runtime.ts: get connectionStale()`) has computed WS-update
 * staleness from real timing since MOR-1419, but had zero consumers
 * anywhere in `frontend/src` until this chip. This test drives the REAL
 * staleness mechanism (real `setInterval`, real `markStateUpdated()`) under
 * fake timers, rather than stubbing `connectionStale` directly, so a
 * regression in the wiring between the store and `StatusBar.svelte` cannot
 * hide behind a mock that always returns the value the test wants.
 *
 * All app modules are imported dynamically, once, in `beforeAll`, AFTER
 * `vi.useFakeTimers()` — `connection.svelte.ts` registers its staleness
 * `setInterval` at module load, and only a timer scheduled after fake
 * timers are installed can be driven by `vi.advanceTimersByTime`. This
 * file deliberately does NOT call `vi.resetModules()` (unlike
 * `connection.test.ts`'s per-test reset): that would also invalidate the
 * already-loaded `svelte` runtime module, producing a `mount`/component
 * pair from two different module instances and an `effect_orphan` error.
 * Instead, each test resets the store's own state via its real setters.
 *
 * `$lib/runtime` is still mocked (as in the sibling radio-link-chip test)
 * for the properties `StatusBar.svelte` needs unconditionally on every
 * render (`defaultScopeStatus`, `system.*`), but its `connectionStale`
 * getter forwards to the real, unmocked `$lib/stores/connection.svelte`
 * module — the same module `StatusBar.svelte` reads `getWsConnected()` from
 * directly — so both paths observe one real, shared staleness state.
 */
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

vi.mock('$lib/stores/capabilities.svelte', () => ({
  hasAnyScope: vi.fn(() => false),
  hasAudio: vi.fn(() => false),
  hasSpectrum: vi.fn(() => false),
}));

vi.mock('$lib/stores/layout.svelte', () => ({
  getLayoutMode: vi.fn(() => 'standard'),
  setLayoutMode: vi.fn(),
}));

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  getActiveFrequencyHz: vi.fn(() => null),
}));

vi.mock('$lib/runtime', async () => {
  const conn = await import('$lib/stores/connection.svelte');
  return {
    runtime: {
      defaultScopeStatus: {
        source: null,
        available: false,
        resourceSelected: false,
        demand: 0,
        lifecycle: 'inactive',
        transport: 'disconnected',
        frameSeen: false,
      },
      system: {
        disconnect: vi.fn(),
        connect: vi.fn(),
        powerOn: vi.fn(async () => {}),
        powerOff: vi.fn(async () => {}),
        identifyFrequency: vi.fn(async () => null),
      },
      // Forwards to the real, unmocked store — see file header.
      get connectionStale() {
        return conn.isStale();
      },
    },
  };
});

describe('StatusBar bad-link chip (MOR-2425 R29(3))', () => {
  let target: HTMLElement | null = null;
  let instance: object | null = null;
  let conn: typeof import('$lib/stores/connection.svelte');
  let t: typeof import('$lib/i18n')['t'];
  let mount: typeof import('svelte')['mount'];
  let unmount: typeof import('svelte')['unmount'];
  let flushSync: typeof import('svelte')['flushSync'];
  let StatusBar: (typeof import('../StatusBar.svelte'))['default'];

  beforeAll(async () => {
    vi.useFakeTimers();
    conn = await import('$lib/stores/connection.svelte');
    ({ t } = await import('$lib/i18n'));
    ({ mount, unmount, flushSync } = await import('svelte'));
    ({ default: StatusBar } = await import('../StatusBar.svelte'));
  });

  afterAll(() => {
    vi.useRealTimers();
  });

  afterEach(() => {
    if (instance) unmount(instance);
    instance = null;
    target?.remove();
    target = null;
    // Resync the shared store for the next test: a healthy WS plus a
    // just-arrived update never leaves the chip showing carryover
    // staleness from this test's timer advance.
    conn.setWsConnected(false);
    conn.markStateUpdated();
  });

  function mountBar(): void {
    target = document.createElement('div');
    document.body.appendChild(target);
    instance = mount(StatusBar, { target }) as object;
    flushSync();
  }

  function badLinkChip(): Element | null {
    return target!.querySelector('[data-testid="bad-link-chip"]');
  }

  it('appears once state updates stall past the threshold while the WS stays connected', () => {
    conn.setWsConnected(true);
    conn.markStateUpdated();
    mountBar();
    expect(badLinkChip(), 'must not appear before the threshold').toBeNull();

    vi.advanceTimersByTime(6000);
    flushSync();

    const chip = badLinkChip();
    expect(chip).not.toBeNull();
    // i18n key's rendered text, not a hardcoded string copy (per spec).
    expect(chip!.textContent).toContain(t('core.statusbar.badLink.label'));
  });

  it('clears within one tick when a state_update lands', () => {
    conn.setWsConnected(true);
    conn.markStateUpdated();
    mountBar();
    vi.advanceTimersByTime(6000);
    flushSync();
    expect(badLinkChip(), 'precondition: chip must be showing').not.toBeNull();

    conn.markStateUpdated();
    flushSync();

    expect(badLinkChip()).toBeNull();
  });

  it('does not double up with the disconnected indicator when the WS itself is down', () => {
    conn.setWsConnected(true);
    conn.markStateUpdated();
    mountBar();
    vi.advanceTimersByTime(6000);
    flushSync();
    expect(badLinkChip(), 'precondition: staleness alone must show the chip').not.toBeNull();

    conn.setWsConnected(false);
    flushSync();

    // Exactly one indicator for the WS-down root cause: the existing
    // disconnected banner, never the bad-link chip alongside it.
    expect(target!.querySelector('.control-link-lost')).not.toBeNull();
    expect(badLinkChip()).toBeNull();
  });
});
