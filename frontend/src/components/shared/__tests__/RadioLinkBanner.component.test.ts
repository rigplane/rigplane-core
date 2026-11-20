/**
 * MOR-2241 — persistent "radio disconnected, reconnecting…" banner.
 *
 * The banner reads the REAL connection store (no mock): the transition under
 * test is the rigConnected=true -> false edge that a state_update syncs when
 * the rig link drops (radio.svelte.ts: setRigConnected), and the false ->
 * true edge on reconnect.
 *
 * Pinned behaviour:
 *  - hidden while rigConnected=true (the connected steady state);
 *  - visible while rigConnected=false on a live transport session, and
 *    persistent — it does not auto-dismiss (advancing the clock keeps it);
 *  - cleared on reconnect (rigConnected back to true);
 *  - hidden on a cold start before facts are observed on the session (no
 *    startup flash), and hidden while the control link itself is down (the
 *    StatusBar control-link-lost case, not this banner's).
 *
 * The store module starts a `setInterval` on load (staleness clock), so it is
 * dynamically imported AFTER `vi.useFakeTimers()` — the same pattern as
 * StatusBar.bad-link-chip.component.test.ts.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { setLocale } from '$lib/i18n';
import { _resetLocale } from '$lib/i18n/store.svelte';
import type RadioLinkBannerType from '../RadioLinkBanner.svelte';

let RadioLinkBanner: typeof RadioLinkBannerType;
let setWsConnected: (v: boolean) => void;
let setRigConnected: (v: boolean) => void;

let host: HTMLDivElement;
let app: ReturnType<typeof mount> | null = null;

describe('RadioLinkBanner — rigConnected=false transition (MOR-2241)', () => {
  beforeEach(async () => {
    vi.useFakeTimers();
    if (!RadioLinkBanner) {
      RadioLinkBanner = (await import('../RadioLinkBanner.svelte')).default;
      const store = await import('$lib/stores/connection.svelte');
      setWsConnected = store.setWsConnected;
      setRigConnected = store.setRigConnected;
    }
    // Reset to the cold-start baseline: no transport, no facts observed.
    // setWsConnected(false) clears factsObservedThisSession (MOR-1526 F2).
    setRigConnected(false);
    setWsConnected(false);

    host = document.createElement('div');
    document.body.appendChild(host);
    localStorage.clear();
    _resetLocale();
    setLocale('en-US');

    app = mount(RadioLinkBanner, { target: host });
    flushSync();
  });

  afterEach(() => {
    if (app) {
      unmount(app);
      app = null;
    }
    host.remove();
    localStorage.clear();
    vi.useRealTimers();
  });

  function banner(): HTMLElement | null {
    return host.querySelector('[data-testid="radio-link-banner"]');
  }

  it('is hidden while the rig link is connected', () => {
    setWsConnected(true);
    setRigConnected(true);
    flushSync();

    expect(banner()).toBeNull();
  });

  it('shows a persistent banner on the rigConnected=true -> false transition and never auto-dismisses', () => {
    setWsConnected(true);
    setRigConnected(true);
    flushSync();
    expect(banner()).toBeNull();

    // The link-loss edge: a state_update reports rigConnected=false.
    setRigConnected(false);
    flushSync();

    const el = banner();
    expect(el).not.toBeNull();
    expect(el?.textContent?.trim()).toBe('Radio disconnected — reconnecting…');

    // Persistent: unlike a toast it survives any amount of elapsed time.
    vi.advanceTimersByTime(60_000);
    flushSync();
    expect(banner()).not.toBeNull();
  });

  it('clears the banner on reconnect (rigConnected=false -> true)', () => {
    setWsConnected(true);
    setRigConnected(true);
    flushSync();

    setRigConnected(false);
    flushSync();
    expect(banner()).not.toBeNull();

    setRigConnected(true);
    flushSync();
    expect(banner()).toBeNull();
  });

  it('stays hidden on a cold start before facts are observed on the session', () => {
    // Transport up, but no state_update has landed yet: rigConnected is its
    // initial false and must not flash the banner.
    setWsConnected(true);
    flushSync();

    expect(banner()).toBeNull();
  });

  it('stays hidden while the control link itself is down', () => {
    // Facts observed earlier, then the WS transport dropped: the StatusBar
    // control-link-lost row owns that presentation, not this banner.
    setWsConnected(true);
    setRigConnected(true);
    flushSync();
    setWsConnected(false);
    flushSync();

    expect(banner()).toBeNull();
  });
});
