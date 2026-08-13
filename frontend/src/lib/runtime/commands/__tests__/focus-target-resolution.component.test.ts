/**
 * MOR-1456 — `focus_target` keyboard-shortcut resolution ("Go to X",
 * `g <key>` in `rigs/_keyboard-default.toml`).
 *
 * Red-first history: on `main` before this fix, EVERY selector in
 * `panel-commands.ts`'s `focus_target` dispatch pointed at legacy
 * `[data-panel="..."]`/`[data-control="..."]` markup that the v3-rework
 * (`desktop-declarations.ts`'s `filter`/`rfFrontEnd`/`rx-audio` zones)
 * retired in favour of the semantic surfaces below — every one of these
 * `it()`s failed with `document.activeElement` unchanged (`document.body`)
 * against the OLD selectors, most visibly `vfo` (`g v`, "Go to VFO"), the
 * dead shortcut the ticket named.
 *
 * This file mounts the REAL production semantic surfaces the desktop-v2
 * composition actually renders (`RxAudioSurface`/`RfFrontEndSurface`/
 * `FilterSurface`/`VfoSurface`), fully observed via the SAME topology
 * fixtures each surface's own unit test (`semantic/__tests__/*.test.ts`)
 * uses, and drives them through the REAL `makeKeyboardHandlers().dispatch`
 * from `panel-commands.ts` — never a stand-in selector map. Every advertised
 * `g <key>` binding is proven here except `waterfall`, which needs
 * `SpectrumPanel`'s heavier canvas/runtime mocking and is instead pinned
 * alongside that component's own suite
 * (`components/spectrum/__tests__/SpectrumPanel.component.test.ts`).
 *
 * `*.component.test.ts` naming puts this in the ISOLATED pool (MOR-1272
 * doctrine) — required because this file `vi.mock`s several of
 * `panel-commands.ts`'s own store/transport dependencies so the REAL module
 * imports cleanly with no live radio behind it. `focus_target` itself never
 * touches any of them (`dispatchKeyboardRadioAction` returns immediately —
 * it is not one of the `KEYBOARD_RADIO_ACTIONS`), so every mock below is a
 * safe, inert stand-in, not a behavior the tests below depend on.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mount, unmount, flushSync, type Component } from 'svelte';

// jsdom does not implement `Element.scrollIntoView` at all; the production
// `focus_target` dispatch calls it unconditionally after `.focus()`.
Element.prototype.scrollIntoView = vi.fn();

const h = vi.hoisted(() => ({ setAudioConfig: vi.fn() }));

vi.mock('$lib/transport/ws-client', () => ({
  getControlSession: vi.fn(() => ({ state: 'connected', epoch: 1 })),
  onCommandDelivery: vi.fn(() => () => undefined),
  onControlSessionTransition: vi.fn(() => () => undefined),
  sendCommand: vi.fn(() => true),
}));
vi.mock('$lib/stores/radio.svelte', () => ({
  getActiveReceiver: vi.fn(() => null),
  getRadioState: vi.fn(() => null),
  isRadioFieldAvailable: vi.fn(() => false),
  patchActiveReceiver: vi.fn(),
  patchRadioState: vi.fn(),
  patchReceiver: vi.fn(),
}));
vi.mock('$lib/state/field-status', () => ({
  getFieldStatus: vi.fn(() => undefined),
  isFieldAvailable: vi.fn(() => false),
}));
vi.mock('$lib/stores/capabilities.svelte', () => ({
  getCapabilities: vi.fn(() => null),
  capabilitiesMatchGeneration: vi.fn(() => false),
  getControlRange: vi.fn(() => null),
}));
vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: {
    get rxEnabled() { return false; },
    setMuted: vi.fn(), setRxLive: vi.fn(), setRxVolume: vi.fn(), setVolume: vi.fn(),
  },
}));
vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: { setAudioConfig: h.setAudioConfig },
}));
vi.mock('$lib/stores/tuning.svelte', () => ({
  getTuningStep: vi.fn(() => 1_000),
  adjustTuningStep: vi.fn(),
}));

import { makeKeyboardHandlers } from '../panel-commands';
import RxAudioSurface from '../../../../semantic/RxAudioSurface.svelte';
import RfFrontEndSurface from '../../../../semantic/RfFrontEndSurface.svelte';
import FilterSurface from '../../../../semantic/FilterSurface.svelte';
import VfoSurface from '../../../../semantic/VfoSurface.svelte';
import {
  topologyFixtures, withRxAudio, withModeFilter, withFilterPassband, withRfFrontEnd,
} from '../../../../semantic/fixtures/topologies';

let mounted: ReturnType<typeof mount>[] = [];

function render<P extends Record<string, unknown>>(component: Component<P>, props: P): void {
  const target = document.createElement('div');
  document.body.appendChild(target);
  mounted.push(mount(component, { target, props }));
  flushSync();
}

function dispatchFocusTarget(target: string): void {
  makeKeyboardHandlers().dispatch({ action: 'focus_target', params: { target } });
}

beforeEach(() => {
  mounted = [];
});

afterEach(() => {
  mounted.forEach((c) => unmount(c));
  mounted = [];
  document.body.innerHTML = '';
  vi.restoreAllMocks();
});

describe('focus_target dispatch resolves to a real, focusable anchor in the production DOM (MOR-1456)', () => {
  it('"af" focuses the AF-gain slider in RxAudioSurface (rx-audio zone)', () => {
    render(RxAudioSurface, { view: withRxAudio(topologyFixtures['1/single']) });
    const input = document.querySelector('[data-testid="rx-audio-af"] input');
    expect(input).not.toBeNull();

    dispatchFocusTarget('af');

    expect(document.activeElement).toBe(input);
  });

  it('"rf" focuses the RF-gain slider in RfFrontEndSurface (rfFrontEnd zone)', () => {
    render(RfFrontEndSurface, { view: withRfFrontEnd(topologyFixtures['1/single']) });
    const input = document.querySelector('[data-testid="rf-front-end-rfGain"] input');
    expect(input).not.toBeNull();

    dispatchFocusTarget('rf');

    expect(document.activeElement).toBe(input);
  });

  it('"mode" focuses the first mode choice in FilterSurface (filter zone)', () => {
    render(FilterSurface, { view: withFilterPassband(withModeFilter(topologyFixtures['1/single'])) });
    const button = document.querySelector('[data-testid="filter-mode"] button');
    expect(button).not.toBeNull();

    dispatchFocusTarget('mode');

    expect(document.activeElement).toBe(button);
  });

  it('"filter" focuses the first filter choice in FilterSurface (filter zone)', () => {
    render(FilterSurface, { view: withFilterPassband(withModeFilter(topologyFixtures['1/single'])) });
    const button = document.querySelector('[data-testid="filter-select"] button');
    expect(button).not.toBeNull();

    dispatchFocusTarget('filter');

    expect(document.activeElement).toBe(button);
  });

  it('"pbt" focuses the PBT-inner slider in FilterSurface (filter zone)', () => {
    render(FilterSurface, { view: withFilterPassband(withModeFilter(topologyFixtures['1/single'])) });
    const input = document.querySelector('[data-testid="filter-pbtInner"] input');
    expect(input).not.toBeNull();

    dispatchFocusTarget('pbt');

    expect(document.activeElement).toBe(input);
  });

  it('"vfo" focuses the active receiver\'s tunable frequency display in VfoSurface (receiver-deck zone)', () => {
    render(VfoSurface, { viewModel: topologyFixtures['1/single'], onTuneFrequency: vi.fn() });
    const tile = document.querySelector('[data-vfo-tile][data-vfo-active="true"]');
    const anchor = tile?.querySelector('[data-vfo-freq] [tabindex]');
    expect(anchor).not.toBeNull();

    dispatchFocusTarget('vfo');

    expect(document.activeElement).toBe(anchor);
  });
});

describe('focus_target resolution failure surfaces honestly, never a silent no-op (MOR-1456)', () => {
  it('warns and leaves focus untouched when the target has no focusable anchor in the current layout', () => {
    // Nothing mounted this describe's afterEach already cleared the DOM, so
    // "vfo" (a real, mapped target) resolves to no element — the shape a
    // dial-locked/disabled active receiver produces in production.
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const before = document.activeElement;

    dispatchFocusTarget('vfo');

    expect(document.activeElement).toBe(before);
    expect(warn).toHaveBeenCalledExactlyOnceWith(
      expect.stringContaining('focus_target "vfo" has no focusable anchor'),
    );
  });

  it('warns for a target string with no known selector at all', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});

    dispatchFocusTarget('nonexistent');

    expect(warn).toHaveBeenCalledExactlyOnceWith(
      expect.stringContaining('focus_target "nonexistent" has no focusable anchor'),
    );
  });
});
