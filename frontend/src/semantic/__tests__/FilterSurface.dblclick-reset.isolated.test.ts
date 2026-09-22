/**
 * MOR-2535 — double-click on a filter slider resets it to its default,
 * through the scalar lease's `reset()` path, with the REAL
 * `makeFilterHandlers()` factory behind the surface's callbacks and a spied
 * `sendCommand` at the transport seam (same wiring shape as
 * `FilterSurface.width-command.isolated.test.ts`).
 *
 * Pinned here:
 *  1. IF shift double-click → exactly ONE `set_if_shift {offset: 0}`.
 *  2. Width double-click with a mode-keyed factory default for the current
 *     filter selection → exactly ONE `set_filter_width` with that default.
 *  3. Width double-click with NO declared default → nothing sent, nothing
 *     shown (no invented default).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRawSnippet, flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { ControlSessionSnapshot } from '$lib/runtime/frontend-runtime';

const h = vi.hoisted(() => ({
  state: null as ServerState | null,
  caps: null as Capabilities | null,
  sendCommand: vi.fn((_name: string, _params: Record<string, unknown>, _id?: string) => true),
}));

vi.mock('$lib/transport/ws-client', () => ({
  getControlSession: vi.fn(() => ({ state: 'connected', epoch: 31 })),
  onCommandDelivery: vi.fn(() => () => undefined),
  onControlSessionTransition: vi.fn(() => () => undefined),
  sendCommand: h.sendCommand,
}));

vi.mock('$lib/stores/radio.svelte', () => ({
  getActiveReceiver: vi.fn(() => h.state?.active === 'SUB' ? h.state.sub : h.state?.main ?? null),
  getRadioState: vi.fn(() => h.state),
  subscribeRadioState: vi.fn((listener: (state: ServerState | null) => void) => {
    listener(h.state);
    return () => undefined;
  }),
}));

vi.mock('$lib/stores/capabilities.svelte', () => ({
  capabilitiesMatchGeneration: vi.fn((generation: unknown) =>
    Number.isSafeInteger(generation) && h.caps?.providerGeneration === generation),
  getCapabilities: vi.fn(() => h.caps),
  getControlRange: vi.fn(() => null),
}));

vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: {
    get state() { return h.state; },
    get caps() { return h.caps; },
    get controlSession() {
      return { state: 'connected', epoch: 31 } satisfies ControlSessionSnapshot;
    },
    rxEnabled: false,
    setMuted: vi.fn(),
    setRxLive: vi.fn(),
    setRxVolume: vi.fn(),
    setVolume: vi.fn(),
  },
}));

vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: { setAudioConfig: vi.fn() },
}));

vi.mock('$lib/stores/tuning.svelte', () => ({
  adjustTuningStep: vi.fn(),
  getTuningStep: vi.fn(() => 1_000),
}));

import { makeFilterHandlers } from '$lib/runtime/commands/panel-commands';
import { resetCommandLifecycle } from '$lib/stores/commands.svelte';
import { FTX1_CAPABILITIES, FTX1_STATE } from '$lib/runtime/adapters/__tests__/fixtures/ftx1-profile';
import FilterSurface from '../FilterSurface.svelte';
import { topologyFixtures, withFilterPassband, withModeFilter } from '../fixtures/topologies';
import type { ActiveFilterConfiguration } from '../radio-view-model';
import type { FilterInstrumentHandles } from '../filter-instruments';

const handles: FilterInstrumentHandles = {
  mode: createRawSnippet(() => ({ render: () => '<span></span>' })),
  filter: createRawSnippet(() => ({ render: () => '<span></span>' })),
  shape: createRawSnippet(() => ({ render: () => '<span></span>' })),
  dataMode: createRawSnippet(() => ({ render: () => '<span></span>' })),
};

const AVAILABILITY = { structural: true, operational: true } as const;

/** The FTX-1 SSB-row factory defaults (`rigs/ftx1.toml`
 *  `[filters.width.SSB] defaults = [2400, 1800, 300]`), exposed through the
 *  view model's `activeFilterConfiguration` for the fixture's `currentFilter`
 *  reading of 1 (FIL1 → 2400 Hz). */
const SSB_FACTORY_CONFIGURATION: ActiveFilterConfiguration = {
  slots: [2400, 1800, 300].map((factoryWidthHz, i) =>
    ({ filter: i + 1, label: `FIL${i + 1}`, factoryWidthHz })),
  fixed: false, minHz: null, maxHz: null, stepHz: null, segments: [], table: [],
};

function dblclick(input: HTMLInputElement): void {
  input.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
  flushSync();
}

describe('MOR-2535 double-click resets a filter slider to its default', () => {
  let target: HTMLDivElement;
  let component: ReturnType<typeof mount> | null;

  beforeEach(() => {
    vi.useFakeTimers();
    h.state = structuredClone(FTX1_STATE);
    h.state.providerGeneration = 31;
    h.caps = { ...FTX1_CAPABILITIES, providerGeneration: 31 };
    h.sendCommand.mockClear();
    resetCommandLifecycle();
    target = document.createElement('div');
    document.body.appendChild(target);
    component = null;
  });

  afterEach(() => {
    if (component !== null) void unmount(component);
    resetCommandLifecycle();
    vi.useRealTimers();
    target.remove();
  });

  it('IF shift: double-click dispatches exactly one set_if_shift {offset: 0}', () => {
    const fixture = withFilterPassband(topologyFixtures['1/single']);
    const view = {
      ...fixture,
      filterPassband: {
        ...fixture.filterPassband!,
        ifShift: { reading: { status: 'known' as const, value: 500 }, availability: AVAILABILITY },
      },
    };
    const onIfShiftChange = vi.fn(makeFilterHandlers().onIfShiftChange);
    component = mount(FilterSurface, {
      target,
      props: { view, handles, onIfShiftChange },
    });
    flushSync();

    const input = target.querySelector<HTMLInputElement>('[data-testid="filter-ifShift"] input')!;
    expect(input.disabled).toBe(false);
    expect(input.value).toBe('500');
    dblclick(input);

    expect(onIfShiftChange).toHaveBeenCalledExactlyOnceWith(0);
    expect(h.sendCommand).toHaveBeenCalledTimes(1);
    expect(h.sendCommand).toHaveBeenCalledWith(
      'set_if_shift', { offset: 0, receiver: 0 }, expect.any(String),
    );
  });

  it('width: double-click with a mode default dispatches one set_filter_width with that default', () => {
    const fixture = withModeFilter(topologyFixtures['1/single']);
    const view = {
      ...fixture,
      modeFilter: {
        ...fixture.modeFilter!,
        activeFilterConfiguration: SSB_FACTORY_CONFIGURATION,
        filterWidth: { reading: { status: 'known' as const, value: 3000 }, availability: AVAILABILITY },
      },
    };
    const onFilterWidthChange = vi.fn(makeFilterHandlers().onFilterWidthChange);
    component = mount(FilterSurface, {
      target,
      props: { view, handles, part: 'filter', onFilterWidthChange },
    });
    flushSync();

    const input = target.querySelector<HTMLInputElement>('[data-testid="filter-width"] input')!;
    expect(input.disabled).toBe(false);
    expect(input.value).toBe('3000');
    dblclick(input);

    // currentFilter reads 1 (FIL1) → factory default 2400 Hz.
    expect(onFilterWidthChange).toHaveBeenCalledExactlyOnceWith(2400);
    vi.advanceTimersByTime(200);
    expect(h.sendCommand).toHaveBeenCalledTimes(1);
    expect(h.sendCommand).toHaveBeenCalledWith(
      'set_filter_width', { width: 2400, receiver: 0 }, expect.any(String),
    );
  });

  it('width: double-click with NO declared default sends nothing and shows nothing', () => {
    const fixture = withModeFilter(topologyFixtures['1/single']);
    const view = {
      ...fixture,
      modeFilter: {
        ...fixture.modeFilter!,
        // No mode-keyed configuration at all — the radio (or the unresolved
        // mode) declares no factory width, so there is nothing to reset to.
        activeFilterConfiguration: null,
        filterWidth: { reading: { status: 'known' as const, value: 3000 }, availability: AVAILABILITY },
      },
    };
    const onFilterWidthChange = vi.fn(makeFilterHandlers().onFilterWidthChange);
    component = mount(FilterSurface, {
      target,
      props: { view, handles, part: 'filter', onFilterWidthChange },
    });
    flushSync();

    const input = target.querySelector<HTMLInputElement>('[data-testid="filter-width"] input')!;
    expect(input.disabled).toBe(false);
    dblclick(input);

    expect(onFilterWidthChange).not.toHaveBeenCalled();
    vi.advanceTimersByTime(500);
    expect(h.sendCommand).not.toHaveBeenCalled();
    // Nothing shown: the readout keeps the confirmed value and no feedback
    // announcement element appears.
    const output = target.querySelector<HTMLOutputElement>('[data-testid="filter-width"] output')!;
    expect(output.textContent).toBe('3000');
    expect(target.querySelector('[data-control-feedback-status]')).toBeNull();
  });
});
