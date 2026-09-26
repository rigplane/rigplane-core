/**
 * MOR-1703 — one Filter Width lifecycle DTO, three live presentations.
 *
 * Desktop (`desktop-v2`), narrow mobile (`mobile`) and a workspace that
 * keeps the filter zone selected all mount through the same composition
 * root. A pending `set_filter_width` must land as the same phase, busy,
 * target and live status on each, and keyboard/pointer must dispatch the
 * same intent. Canonical ARIA stays the confirmed width.
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
  commands: vi.fn(() => true),
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
import { setLocale } from '$lib/i18n';

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
  target = document.createElement('div');
  document.body.appendChild(target);
  const context = new Map<unknown, unknown>([[SURFACE_PLAN_CONTEXT_KEY, () => planFor(kind)]]);
  component = mount(HostedRadioLayoutFixture, {
    target,
    props: { skinId: skinFor(kind) },
    context,
  });
  flushSync();
}

function widthControl(): HTMLElement | null {
  return target.querySelector<HTMLElement>(
    '[data-testid="filter-width"] input, [data-filter-width-lifecycle] [role="slider"]',
  );
}

function snapshot() {
  const control = widthControl();
  const live = target.querySelector('[data-testid="filter-width"] [data-control-feedback-status]');
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
      expect(seen.live).toBe(
        'Filter width 3000 requested; not yet confirmed by the radio.',
      );
    },
  );

  it('keeps keyboard and pointer intents identical across the three mounts', () => {
    const dispatched: unknown[] = [];
    for (const kind of ['desktop', 'narrow-mobile', 'workspace-selected'] as const) {
      render(kind);
      const control = widthControl();
      expect(control, kind).not.toBeNull();
      h.commands.mockClear();
      control!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
      flushSync();
      dispatched.push(h.commands.mock.calls.map((call) => [call[0], call[1]]));
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
    expect(seen.live).toBe('Запрошена ширина фильтра 3000; радио ещё не подтвердило её.');
  });

  it('keeps forced-colors and reduced-motion as structural rules beside the phase attribute', () => {
    const source = readFileSync('src/semantic/FilterSurface.svelte', 'utf8');
    const widthRule = source.slice(source.indexOf('[data-testid="filter-width"]'));
    expect(widthRule).toContain('@media (forced-colors: active)');
    expect(widthRule).toContain('@media (prefers-reduced-motion: reduce)');
    expect(widthRule).toContain('data-command-phase');
    expect(widthRule).not.toContain('font-style: italic');
  });
});
