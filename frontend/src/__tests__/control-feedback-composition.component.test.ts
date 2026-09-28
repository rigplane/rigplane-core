/**
 * MOR-1703 — one Filter Width lifecycle DTO, three live presentations.
 *
 * Desktop (`desktop-v2`) and a workspace that keeps the filter zone
 * selected mount the semantic `FilterSurface`; the narrow mobile (`mobile`)
 * phone mounts its REAL width control since MOR-2816 removed the bare
 * FilterSurface seat — the chip FilterPanel (`MobileRadioLayout`'s filter
 * sheet, opened from the essentials chip's "More…" trigger), whose table
 * catalog mounts the same feedback-integrated `ValueControl` hbar the
 * desktop row's input mirrors. A pending `set_filter_width` must land as
 * the same phase, busy, target and live status on each, and keyboard or
 * pointer input must dispatch the same intent. Canonical ARIA stays the
 * confirmed width. The live STATUS sentence is each mount's own emitter
 * (the desktop row prints the projector status, the phone row prints the
 * catalog announcement), so the expectation is per-kind while the phase,
 * busy and canonical ARIA stay byte-identical.
 *
 * Isolated pool by name (`*.component.test.ts`).
 */
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';
import type { ManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';
import type { SkinId } from '../skins/registry';

const h = vi.hoisted(() => ({
  session: { state: 'connected', epoch: 1 },
  listeners: new Set<(next: { state: string; epoch: number }) => void>(),
  commands: vi.fn<(name: string, params?: Record<string, unknown>, id?: string) => boolean>(() => true),
  txController: null as ManagedAppTxController | null,
}));
vi.mock('$lib/transport/ws-client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/transport/ws-client')>();
  return {
    ...actual,
    sendCommand: h.commands,
    getControlSession: () => h.session,
    onControlSessionTransition: (listener: (next: { state: string; epoch: number }) => void) => {
      h.listeners.add(listener);
      return () => h.listeners.delete(listener);
    },
    onCommandDelivery: () => () => undefined,
  };
});
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => {
    if (!h.txController) throw new Error('managed TX harness is not installed');
    return h.txController;
  },
}));
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => ({ visible: false, sourceLabel: null }),
  getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
}));

import SemanticRadioSurfaces from '../components-v2/wiring/SemanticRadioSurfaces.svelte';
import HostedRadioLayoutFixture from '../components-v2/layout/__tests__/fixtures/HostedRadioLayoutFixture.svelte';
import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';
import { resetRadioState, setRadioState } from '$lib/stores/radio.svelte';
import { getCommandLifecycles, resetCommandLifecycle } from '$lib/stores/commands.svelte';
import { dispatchRadioIntent } from '$lib/runtime/commands/radio-intents';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';
import { desktopV2Layout, mobileLayout } from '../presentation/layouts/declarations';
import { readWorkspace } from '../presentation/workspace/contract';
import {
  resolveSurfacePlan, SURFACE_PLAN_CONTEXT_KEY, type SurfacePlan,
} from '../presentation/workspace/resolution';
import { getLocale, setLocale } from '$lib/i18n';

const PROVIDER_GENERATION = 1;
const CONFIRMED_HZ = 2400;
const TARGET_HZ = 3000;
const fresh: FieldStatus = {
  storePath: 'x', observed: true, freshness: 'fresh',
  availability: 'available', lastObservedMonotonic: 500,
};

function liveCaps(): Capabilities {
  return {
    stateContractVersion: 1, providerGeneration: PROVIDER_GENERATION,
    model: 'fixture', scope: false, audio: false, tx: false,
    capabilities: ['filter_width'],
    receivers: 1, vfoScheme: 'single',
    freqRanges: [], modes: ['USB'], filters: ['FIL1'],
    filterWidthMin: 50, filterWidthMax: 3600,
    filterConfig: { USB: { defaults: [2400], fixed: false, minHz: 50, maxHz: 3600, stepHz: 50 } },
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
    webrtc: { available: false, enabled: false },
    txBands: [], scopeSource: null, audioFftAvailable: false,
  } as unknown as Capabilities;
}

// The phone's real width control is the chip FilterPanel's TABLE-mode hbar,
// so the phone mount pins a table catalog (the FTX-1-style capability
// shape). The table keeps the confirmed 2400, the keyboard step target 2450
// and the pending target 3000 as genuine catalog choices, and minHz/maxHz
// match the table ends the width rule validates.
function phoneCaps(): Capabilities {
  return {
    ...liveCaps(),
    filterConfig: {
      USB: { defaults: [2400], fixed: false, minHz: 1800, maxHz: 3000, stepHz: 1, table: [1800, 2400, 2450, 3000] },
    },
  } as unknown as Capabilities;
}

function liveState(seq = 3): ServerState {
  const slot = { freqHz: 14250000, mode: 'USB', filterNum: 1, dataMode: 0 };
  const receiver = {
    ...slot, vfoA: slot, vfoB: { ...slot, freqHz: 14300000 }, activeSlot: 'A', filter: 1,
    filterWidth: CONFIRMED_HZ,
    sMeter: -12, att: 0, preamp: 0, nb: false, nr: false,
    afLevel: 0.4, rfGain: 0.75, squelch: 0.1,
  };
  return {
    stateContractVersion: 1, providerGeneration: PROVIDER_GENERATION,
    revision: seq, stateRevision: seq, freshnessRevision: seq, observationSeq: seq,
    updatedAt: '2026-09-26T00:00:00Z',
    active: 'MAIN', split: false, dualWatch: false, ptt: false, tunerStatus: 0,
    connection: { rigConnected: true, radioReady: true, controlConnected: true },
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: receiver, sub: { ...receiver },
    fieldStatus: {
      active: fresh,
      'main.mode': fresh,
      'main.filter': fresh,
      'main.filterWidth': fresh,
      'main.freqHz': fresh,
    },
  } as unknown as ServerState;
}

type Presentation = 'desktop' | 'narrow-mobile' | 'workspace-selected';

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;
let txHarness: ManagedAppTxHarness;

function planFor(kind: Presentation): SurfacePlan {
  if (kind === 'narrow-mobile') {
    return resolveSurfacePlan(mobileLayout, readWorkspace({ version: 1 }).workspace);
  }
  return resolveSurfacePlan(desktopV2Layout, readWorkspace({
    version: 1,
    visibleSurfaces: { filter: ['filter'] },
  }).workspace);
}

function skinFor(kind: Presentation): SkinId {
  return kind === 'narrow-mobile' ? 'mobile' : 'desktop-v2';
}

function render(kind: Presentation): void {
  // The phone mount needs its table catalog in place before the layout
  // resolves the FilterPanel's width rule.
  expect(setCapabilities(kind === 'narrow-mobile' ? phoneCaps() : liveCaps())).toBe(true);
  expect(setRadioState(liveState())).toBe(true);
  target = document.createElement('div');
  document.body.appendChild(target);
  const context = new Map<unknown, unknown>([[SURFACE_PLAN_CONTEXT_KEY, () => planFor(kind)]]);
  component = mount(HostedRadioLayoutFixture, {
    target,
    props: { skinId: skinFor(kind) },
    context,
  });
  flushSync();
  if (kind === 'narrow-mobile') openPhoneFilterSheet();
}

// MOR-2816: the phone's FilterPanel lives inside the filter BottomSheet,
// opened by the essentials chip's "More…" trigger — the sheet mounts
// nothing until it opens, so the lifecycle is proven on the OPENED sheet.
function openPhoneFilterSheet(): void {
  const more = Array.from(target.querySelectorAll<HTMLButtonElement>('button'))
    .find((button) => (button.textContent ?? '').trim() === 'More…');
  if (more === undefined) throw new Error('phone filter sheet trigger not found');
  more.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  flushSync();
}

function widthControl(): HTMLElement | null {
  // MOR-2816: the phone seat is the chip FilterPanel's table-mode width
  // row (`data-filter-width-lifecycle`); the desktop/workspace seat stays
  // the semantic FilterSurface's `filter-width` row.
  const phone = target.querySelector<HTMLElement>(
    '[data-filter-width-lifecycle] input, [data-filter-width-lifecycle] [role="slider"]',
  );
  if (phone !== null) return phone;
  const row = target.querySelector<HTMLElement>('[data-testid="filter-width"]');
  return row?.querySelector<HTMLElement>('input, [role="slider"]') ?? null;
}

function snapshot() {
  const control = widthControl();
  const live = target.querySelector<HTMLElement>(
    '[data-testid="filter-width"] [data-control-feedback-status],'
    + ' [data-filter-width-lifecycle] [data-control-feedback-status]',
  );
  return {
    mounted: control !== null,
    phase: control?.getAttribute('data-command-phase') ?? null,
    busy: control?.getAttribute('aria-busy'),
    canonical: control?.getAttribute('aria-valuenow'),
    live: live?.textContent?.replace(/\s+/g, ' ').trim() ?? null,
  };
}

beforeEach(() => {
  txHarness = new ManagedAppTxHarness();
  h.txController = txHarness.controller;
  h.listeners.clear();
  h.commands.mockClear();
  setLocale('en-US');
  resetRadioState();
  clearCapabilities();
  resetCommandLifecycle();
  expect(setCapabilities(liveCaps())).toBe(true);
  expect(setRadioState(liveState())).toBe(true);
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
  resetRadioState();
  clearCapabilities();
  resetCommandLifecycle();
  setLocale('en-US');
});

describe('one Filter Width lifecycle is equivalent on desktop, narrow mobile and a workspace-selected mount (MOR-1703)', () => {
  // The live STATUS sentence is each mount's own emitter: the desktop row
  // prints the projector status ('Submitting: <target>'), the phone's chip
  // FilterPanel prints the catalog announcement with its kHz-formatted
  // target. The phase, busy and canonical ARIA above stay identical.
  const LIVE_STATUS: Readonly<Record<Presentation, string>> = {
    desktop: 'Submitting: 3000',
    'narrow-mobile': 'Filter width 3kHz requested; not yet confirmed by the radio.',
    'workspace-selected': 'Submitting: 3000',
  };

  it.each(['desktop', 'narrow-mobile', 'workspace-selected'] as const)(
    '%s: pending phase, busy, confirmed ARIA and live status match the shared DTO',
    (kind) => {
      render(kind);
      dispatchRadioIntent({ name: 'set_filter_width', params: { width: TARGET_HZ, receiver: 0 } });
      flushSync();

      const command = getCommandLifecycles().find((candidate) => candidate.name === 'set_filter_width');
      expect(command?.status).toBe('pending');
      expect(command?.params).toMatchObject({ width: TARGET_HZ });

      const seen = snapshot();
      expect(seen.mounted).toBe(true);
      expect(seen.phase).toBe('submitted');
      expect(seen.busy).toBe('true');
      expect(seen.canonical).toBe(String(CONFIRMED_HZ));
      // beforeEach pins en-US. That locale keeps the projector sentence the
      // width tests already accepted ("Submitting: 3000"), not the catalog
      // sentence. A non-English locale is pinned separately below.
      expect(getLocale()).toBe('en-US');
      expect(seen.live).toBe(LIVE_STATUS[kind]);
    },
  );

  it('keeps the width input intent identical across the three mounts', () => {
    vi.useFakeTimers();
    const dispatched: unknown[] = [];
    for (const kind of ['desktop', 'narrow-mobile', 'workspace-selected'] as const) {
      render(kind);
      const control = widthControl();
      expect(control, kind).not.toBeNull();
      control!.focus();
      h.commands.mockClear();
      if (control instanceof HTMLInputElement) {
        control.value = '2450';
        control.dispatchEvent(new Event('input', { bubbles: true }));
      } else {
        // MOR-2816: the phone's table-mode hbar has no native input — the
        // catalog's next choice above the confirmed 2400 is 2450, the same
        // target the desktop row types, so the three mounts still dispatch
        // one identical intent.
        control!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
      }
      // 500ms covers the phone path's stacked debounces (the scalar's 50ms
      // keyboard debounce, then the handler's own 200ms write debounce).
      vi.advanceTimersByTime(500);
      flushSync();
      dispatched.push(h.commands.mock.calls.map(([name, params]) => [name, params]));
      unmount(component!);
      component = null;
      document.body.innerHTML = '';
      resetCommandLifecycle();
      resetRadioState();
      expect(setRadioState(liveState(kind === 'desktop' ? 4 : kind === 'narrow-mobile' ? 5 : 6))).toBe(true);
    }
    expect(dispatched[0]).toEqual(dispatched[1]);
    expect(dispatched[1]).toEqual(dispatched[2]);
    expect(dispatched[0]).toEqual([['set_filter_width', { width: 2450, receiver: 0 }]]);
    vi.useRealTimers();
  });
});

describe('structural feedback survives locale, forced-colors and reduced-motion (MOR-1703)', () => {
  it('announces the same pending width in Russian without making color or motion the only signal', () => {
    setLocale('ru-RU');
    render('narrow-mobile');
    dispatchRadioIntent({ name: 'set_filter_width', params: { width: TARGET_HZ, receiver: 0 } });
    flushSync();
    const seen = snapshot();
    expect(seen.phase).toBe('submitted');
    expect(seen.busy).toBe('true');
    expect(seen.canonical).toBe(String(CONFIRMED_HZ));
    // The phone emitter formats the target through its kHz formatter
    // ('3kHz'), where the removed FilterSurface seat printed the raw
    // number — same pending width, same sentence family.
    expect(seen.live).toBe('Запрошена ширина фильтра 3kHz; радио ещё не подтвердило её.');
  });

  it('keeps forced-colors and reduced-motion as structural rules beside the phase attribute', () => {
    const source = readFileSync('src/semantic/FilterSurface.svelte', 'utf8');
    const style = source.slice(source.lastIndexOf('<style>'));
    const widthRule = style.slice(0, style.indexOf('.filter-toggle'));
    expect(source).toContain('data-command-phase={filterWidthView.phase');
    expect(widthRule).toContain('@media (forced-colors: active)');
    expect(widthRule).toContain('@media (prefers-reduced-motion: reduce)');
    expect(widthRule).not.toContain('font-style');
  });
});
