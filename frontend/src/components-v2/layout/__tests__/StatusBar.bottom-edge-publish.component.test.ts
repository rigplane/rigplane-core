/**
 * MOR-1240 — StatusBar publishes its own bottom edge.
 *
 * The powered-off overlay no longer measures the DOM from AppGlobalHost:
 * that host-side lookup raced the lazily loaded presentation (power-off
 * could be known before the skin chunk mounted the bar) and missed moves
 * that resize nothing (the link-lost row above the bar shifts it without
 * changing its box). The dependency is inverted: while mounted,
 * StatusBar writes the ceil of its bar element's `getBoundingClientRect()
 * .bottom` to the document-level `--rp-status-bar-bottom` custom property,
 * and the overlay only consumes it in CSS.
 *
 * These tests pin the publish contract with the REAL connection store
 * driving the link-lost row (the same module choice as the bad-link-chip
 * suite) and NO fake ResizeObserver callbacks: the only re-publish
 * triggers exercised here are mount, unmount and a real reactive state
 * flip, which is exactly what the host-side design could not guarantee.
 * jsdom does no layout, so `Element.prototype.getBoundingClientRect` is
 * stubbed with a mutable rect — the same idiom the host-side tests used
 * to use, now pointed at the element that owns the measurement.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

vi.mock('$lib/stores/capabilities.svelte', () => ({
  hasAnyScope: vi.fn(() => false),
  hasAudio: vi.fn(() => false),
  hasSpectrum: vi.fn(() => false),
  hasCapability: vi.fn(() => false),
}));

vi.mock('$lib/stores/layout.svelte', () => ({
  getLayoutMode: vi.fn(() => 'standard'),
  setLayoutMode: vi.fn(),
}));

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  getActiveFrequencyHz: vi.fn(() => null),
}));

vi.mock('$lib/i18n', () => ({
  t: (key: string, params?: Record<string, string>) =>
    params?.detail !== undefined ? `${key}: ${params.detail}` : key,
  messageFromReasonCode: (code: string) => code,
}));

// AppGlobalHost (mounted by the load-order test below) reads the
// App-owned TX controller; a stub controller keeps the lamp dark.
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => ({
    snapshot: () => ({ radioTx: 'off', txRisk: 'none' }),
    subscribe: () => () => {},
  }),
}));

vi.mock('$lib/transport/ws-client', () => ({
  onMessage: vi.fn(() => vi.fn()),
  onCommandDelivery: vi.fn(() => vi.fn()),
}));

const power = vi.hoisted(() => ({
  radioPowerOn: null as boolean | null,
  powerOn: vi.fn(async () => {}),
}));

vi.mock('$lib/runtime', () => ({
  runtime: {
    get radioPowerOn() { return power.radioPowerOn; },
    get system() {
      return {
        powerOn: power.powerOn,
        disconnect: vi.fn(),
        connect: vi.fn(),
        identifyFrequency: vi.fn(async () => null),
      };
    },
    defaultScopeStatus: {
      source: null,
      available: false,
      resourceSelected: false,
      demand: 0,
      lifecycle: 'inactive',
      transport: 'disconnected',
      frameSeen: false,
    },
  },
}));

import { setWsConnected } from '$lib/stores/connection.svelte';
import StatusBar from '../StatusBar.svelte';
import AppGlobalHost from '../../../AppGlobalHost.svelte';

const BOTTOM_EDGE = '--rp-status-bar-bottom';
const bottomEdge = () => document.documentElement.style.getPropertyValue(BOTTOM_EDGE);

// jsdom does no layout: every element reports the same mutable rect, and
// only StatusBar's publish path reads it.
let rect: { bottom: number };
const rectOf = () =>
  ({ bottom: rect.bottom, top: 0, left: 0, right: 0, width: 0, height: 0, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect;

describe('MOR-1240 — StatusBar publishes its bottom edge (--rp-status-bar-bottom)', () => {
  let targets: HTMLElement[] = [];
  let instances: object[] = [];

  function render(component: typeof StatusBar | typeof AppGlobalHost): HTMLElement {
    const target = document.createElement('div');
    document.body.appendChild(target);
    targets.push(target);
    const instance = mount(component, { target }) as object;
    instances.push(instance);
    flushSync();
    return target;
  }

  beforeEach(() => {
    vi.clearAllMocks();
    document.documentElement.style.removeProperty(BOTTOM_EDGE);
    document.body.innerHTML = '';
    power.radioPowerOn = null;
    rect = { bottom: 33.4 };
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
      return rectOf();
    });
    // The real store starts disconnected (link-lost row visible); tests
    // that need it hidden flip it with the real setter.
    setWsConnected(false);
  });

  afterEach(() => {
    for (const instance of instances) unmount(instance);
    instances = [];
    for (const target of targets) target.remove();
    targets = [];
    document.documentElement.style.removeProperty(BOTTOM_EDGE);
    vi.restoreAllMocks();
  });

  it('publishes the ceil of the bar bottom on mount and removes it on unmount', () => {
    const target = render(StatusBar);
    expect(target.querySelector('.status-bar')).not.toBeNull();
    expect(bottomEdge()).toBe('34px');

    unmount(instances.pop()!);
    targets.pop()!.remove();
    expect(bottomEdge()).toBe('');
  });

  it('keeps a newer instance\'s value: an older bar\'s cleanup must not wipe it', () => {
    // Two bars are never mounted together in-tree, but a skin switch
    // destroys the old StatusBar only after the new one has mounted.
    render(StatusBar);
    expect(bottomEdge()).toBe('34px');

    rect = { bottom: 50.2 };
    render(StatusBar);
    expect(bottomEdge()).toBe('51px');

    // Unmount the FIRST instance: its cleanup must leave the second
    // instance's value alone.
    const first = instances.shift()!;
    unmount(first);
    targets.shift()!.remove();
    expect(bottomEdge()).toBe('51px');

    const second = instances.shift()!;
    unmount(second);
    targets.shift()!.remove();
    expect(bottomEdge()).toBe('');
  });

  it('republishes when the link-lost row appears — a real state flip, no observer callback', () => {
    setWsConnected(true);
    flushSync();
    const target = render(StatusBar);
    expect(target.querySelector('.control-link-lost')).toBeNull();
    expect(bottomEdge()).toBe('34px');

    // The link-lost row appears ABOVE the bar and moves it down without
    // resizing the bar's own box — the exact move the host-side
    // ResizeObserver design missed. Flip the real state; never call an
    // observer callback by hand.
    rect = { bottom: 47.6 };
    setWsConnected(false);
    flushSync();
    expect(target.querySelector('.control-link-lost')).not.toBeNull();
    expect(bottomEdge()).toBe('48px');
  });

  // The load-order race the review blocked on: power-off is known before
  // the lazily loaded skin chunk mounts a StatusBar. The overlay must be
  // full-screen until a bar actually publishes, and a bar mounting later
  // must be enough — jsdom lays nothing out, so the variable's presence
  // is the assertable fact.
  it('a StatusBar mounted after power-off makes the edge variable present', () => {
    power.radioPowerOn = false;
    const host = render(AppGlobalHost);
    const overlay = host.querySelector<HTMLElement>('[data-testid="global-power-off"]');
    expect(overlay).not.toBeNull();
    // No StatusBar yet: no property anywhere, overlay full-screen via the
    // CSS fallback.
    expect(bottomEdge()).toBe('');
    expect(overlay!.style.top).toBe(`var(${BOTTOM_EDGE}, 0px)`);

    // The skin chunk finishes loading and mounts the bar afterwards.
    const bar = render(StatusBar);
    expect(bar.querySelector('.status-bar')).not.toBeNull();
    expect(bottomEdge()).toBe('34px');
  });
});
