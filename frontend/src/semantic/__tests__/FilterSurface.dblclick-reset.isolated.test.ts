/**
 * MOR-2535 (correction round 1) — the filter-slider reset gesture is a
 * DOUBLE-CLICK on the row's LABEL or VALUE cell, never on the range track
 * (a track double-click would fire native `input` events — position
 * commands — before `dblclick` ran). These tests exercise the MECHANISM:
 * a mounted `FilterSurface`, a real `dblclick` `MouseEvent` dispatched on
 * the label element, the REAL `makeFilterHandlers()` factory behind the
 * surface's callbacks, and a spied `sendCommand` at the transport seam
 * (same wiring shape as `FilterSurface.width-command.isolated.test.ts`).
 * Every assertion counts the exact commands on the wire — since the
 * gesture target is the label, no native `input` event fires, and the
 * exact counts pin that no `input` path ran.
 *
 * Pinned here:
 *  1. IF shift label double-click → exactly ONE `set_if_shift {offset: 0}`.
 *  2. Width label double-click with a mode-keyed factory default for the
 *     current filter selection → exactly ONE `set_filter_width` with it.
 *  3. Width label double-click on a radio WITHOUT a filter selector (the
 *     post-MOR-2530 FTX-1 shape: no `activeFilterConfiguration`, no
 *     `currentFilter`) is a DOCUMENTED no-op — the owner has not ruled
 *     which `defaults[]` element is the singular no-selector default, so
 *     no default is invented and no command is sent.
 *  4. PBT label double-click → the ATOMIC `onPbtReset` dispatch site:
 *     exactly `set_pbt_inner` then `set_pbt_outer`, both with the lattice
 *     centre raw, in that order.
 *  5. The PBT reset button rides the same dispatch site.
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
 *  reading of 1 (FIL1 → 2400 Hz) — the shape a radio WITH a filter selector
 *  publishes. */
const SSB_FACTORY_CONFIGURATION: ActiveFilterConfiguration = {
  slots: [2400, 1800, 300].map((factoryWidthHz, i) =>
    ({ filter: i + 1, label: `FIL${i + 1}`, factoryWidthHz })),
  fixed: false, minHz: null, maxHz: null, stepHz: null, segments: [], table: [],
};

function dblclick(element: Element): void {
  element.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
  flushSync();
}

/** `[name, params]` of every command that reached the transport, in order. */
function sentCommands(): [string, Record<string, unknown>][] {
  return h.sendCommand.mock.calls.map(([name, params]) => [name, params]);
}

describe('MOR-2535 double-click on a filter row label resets it to its default', () => {
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

  it('IF shift: double-click on the label dispatches exactly one set_if_shift {offset: 0}', () => {
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

    const row = target.querySelector<HTMLElement>('[data-testid="filter-ifShift"]')!;
    const input = row.querySelector<HTMLInputElement>('input')!;
    expect(input.disabled).toBe(false);
    expect(input.value).toBe('500');

    // The TRACK is not the gesture target: a double-click there is inert.
    dblclick(input);
    expect(onIfShiftChange).not.toHaveBeenCalled();

    dblclick(row.querySelector<HTMLElement>('.filter-level-name')!);

    expect(onIfShiftChange).toHaveBeenCalledExactlyOnceWith(0);
    expect(sentCommands()).toEqual([
      ['set_if_shift', { offset: 0, receiver: 0 }],
    ]);
  });

  it('width: double-click on the label with a mode default dispatches one set_filter_width with that default', () => {
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

    const row = target.querySelector<HTMLElement>('[data-testid="filter-width"]')!;
    const input = row.querySelector<HTMLInputElement>('input')!;
    expect(input.disabled).toBe(false);
    expect(input.value).toBe('3000');

    // The TRACK is not the gesture target: a double-click there is inert.
    dblclick(input);
    expect(onFilterWidthChange).not.toHaveBeenCalled();

    dblclick(row.querySelector<HTMLElement>('.filter-level-name')!);

    // currentFilter reads 1 (FIL1) → factory default 2400 Hz.
    expect(onFilterWidthChange).toHaveBeenCalledExactlyOnceWith(2400);
    vi.advanceTimersByTime(200);
    expect(sentCommands()).toEqual([
      ['set_filter_width', { width: 2400, receiver: 0 }],
    ]);
  });

  it('width: double-click on a radio WITHOUT a filter selector (the FTX-1 shape) is a documented no-op — no default, no command', () => {
    const fixture = withModeFilter(topologyFixtures['1/single']);
    const view = {
      ...fixture,
      modeFilter: {
        ...fixture.modeFilter!,
        // The post-MOR-2530 FTX-1 publishes NO filter selector and NO
        // mode-keyed configuration, so no factory width default exists to
        // reset to. Which `defaults[]` element should count as the singular
        // default on such radios is the owner's open decision — until then
        // the gesture is a documented no-op and nothing is invented.
        activeFilterConfiguration: null,
        currentFilter: { reading: { status: 'unknown' as const }, availability: AVAILABILITY },
        filterWidth: { reading: { status: 'known' as const, value: 3000 }, availability: AVAILABILITY },
      },
    };
    const onFilterWidthChange = vi.fn(makeFilterHandlers().onFilterWidthChange);
    component = mount(FilterSurface, {
      target,
      props: { view, handles, part: 'filter', onFilterWidthChange },
    });
    flushSync();

    const row = target.querySelector<HTMLElement>('[data-testid="filter-width"]')!;
    expect(row.querySelector<HTMLInputElement>('input')!.disabled).toBe(false);
    dblclick(row.querySelector<HTMLElement>('.filter-level-name')!);
    dblclick(row.querySelector<HTMLOutputElement>('output')!);

    expect(onFilterWidthChange).not.toHaveBeenCalled();
    vi.advanceTimersByTime(500);
    expect(h.sendCommand).not.toHaveBeenCalled();
    // Nothing shown: the readout keeps the confirmed value and no feedback
    // announcement element appears.
    expect(row.querySelector<HTMLOutputElement>('output')!.textContent).toBe('3000');
    expect(target.querySelector('[data-control-feedback-status]')).toBeNull();
  });

  it('PBT: double-click on a row label dispatches the atomic inner+outer centre reset, in order', () => {
    // A PBT-capable radio on a measured lattice: USB, width 2400 Hz, mode
    // step 50 Hz, `pbt_inner` raw range 0..255 centred at 128 — the shape
    // `panel-commands.intent.isolated.test.ts`'s `resets both edges to the
    // lattice centre` pins at the handler level. No `fieldStatus` entries,
    // so every leaf resolves available.
    h.caps = {
      providerGeneration: 31,
      capabilities: ['pbt'],
      controls: {
        pbt_inner: { raw_min: 0, raw_max: 255, raw_center: 128, display_min: -1200, display_max: 1200 },
      },
      filterConfig: { USB: { pbtStepHz: 50 } },
    } as unknown as Capabilities;
    h.state = {
      ...structuredClone(FTX1_STATE),
      active: 'MAIN',
      main: {
        ...structuredClone(FTX1_STATE).main!,
        mode: 'USB', dataMode: 1, filterWidth: 2400, pbtInner: 100, pbtOuter: -100,
      },
      fieldStatus: {},
    } as unknown as ServerState;

    const fixture = withFilterPassband(topologyFixtures['1/single']);
    const view = {
      ...fixture,
      filterPassband: {
        ...fixture.filterPassband!,
        pbtInner: { reading: { status: 'known' as const, value: 100 }, availability: AVAILABILITY },
        pbtOuter: { reading: { status: 'known' as const, value: -100 }, availability: AVAILABILITY },
      },
    };
    const onPbtReset = vi.fn(makeFilterHandlers().onPbtReset);
    const onPbtInnerChange = vi.fn();
    const onPbtOuterChange = vi.fn();
    component = mount(FilterSurface, {
      target,
      props: { view, handles, onPbtReset, onPbtInnerChange, onPbtOuterChange },
    });
    flushSync();

    const row = target.querySelector<HTMLElement>('[data-testid="filter-pbtInner"]')!;
    const input = row.querySelector<HTMLInputElement>('input')!;
    expect(input.disabled).toBe(false);
    expect(input.value).toBe('100');

    // The TRACK is not the gesture target: a double-click there is inert.
    dblclick(input);
    expect(onPbtReset).not.toHaveBeenCalled();

    dblclick(row.querySelector<HTMLElement>('.filter-level-name')!);

    // ONE dispatch site, raw centre conversion, inner then outer — and the
    // per-row change callbacks are NOT the reset path.
    expect(onPbtReset).toHaveBeenCalledTimes(1);
    expect(onPbtReset).toHaveBeenCalledWith();
    expect(onPbtInnerChange).not.toHaveBeenCalled();
    expect(onPbtOuterChange).not.toHaveBeenCalled();
    expect(sentCommands()).toEqual([
      ['set_pbt_inner', { value: 128, receiver: 0 }],
      ['set_pbt_outer', { value: 128, receiver: 0 }],
    ]);
  });

  it('PBT: the reset button rides the same onPbtReset dispatch site, inner then outer', () => {
    h.caps = {
      providerGeneration: 31,
      capabilities: ['pbt'],
      controls: {
        pbt_inner: { raw_min: 0, raw_max: 255, raw_center: 128, display_min: -1200, display_max: 1200 },
      },
      filterConfig: { USB: { pbtStepHz: 50 } },
    } as unknown as Capabilities;
    h.state = {
      ...structuredClone(FTX1_STATE),
      active: 'MAIN',
      main: {
        ...structuredClone(FTX1_STATE).main!,
        mode: 'USB', dataMode: 1, filterWidth: 2400, pbtInner: 100, pbtOuter: -100,
      },
      fieldStatus: {},
    } as unknown as ServerState;

    const view = withFilterPassband(topologyFixtures['1/single']);
    const onPbtReset = vi.fn(makeFilterHandlers().onPbtReset);
    const onPbtInnerChange = vi.fn();
    const onPbtOuterChange = vi.fn();
    component = mount(FilterSurface, {
      target,
      props: { view, handles, onPbtReset, onPbtInnerChange, onPbtOuterChange },
    });
    flushSync();

    target.querySelector<HTMLButtonElement>('[data-testid="filter-pbt-reset"]')!.click();
    flushSync();

    expect(onPbtReset).toHaveBeenCalledTimes(1);
    expect(onPbtReset).toHaveBeenCalledWith();
    expect(onPbtInnerChange).not.toHaveBeenCalled();
    expect(onPbtOuterChange).not.toHaveBeenCalled();
    expect(sentCommands()).toEqual([
      ['set_pbt_inner', { value: 128, receiver: 0 }],
      ['set_pbt_outer', { value: 128, receiver: 0 }],
    ]);
  });
});
