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
import { getFilterWidthControlFeedback } from '$lib/runtime/adapters/panel-adapters';
import { resetCommandLifecycle } from '$lib/stores/commands.svelte';
import { FTX1_CAPABILITIES, FTX1_STATE } from '$lib/runtime/adapters/__tests__/fixtures/ftx1-profile';
import FilterSurface from '../FilterSurface.svelte';
import { topologyFixtures, withModeFilter } from '../fixtures/topologies';
import type { FilterInstrumentHandles } from '../filter-instruments';

const handles: FilterInstrumentHandles = {
  mode: createRawSnippet(() => ({ render: () => '<span></span>' })),
  filter: createRawSnippet(() => ({ render: () => '<span></span>' })),
  shape: createRawSnippet(() => ({ render: () => '<span></span>' })),
  dataMode: createRawSnippet(() => ({ render: () => '<span></span>' })),
};

describe('MOR-2529 native range input with unread filter selection', () => {
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

  it('routes the observed width through the browser handler when filter identity is unobserved', () => {
    const fixture = withModeFilter(topologyFixtures['1/single']);
    const availability = { structural: true, operational: true } as const;
    const filterWidthFeedback = getFilterWidthControlFeedback();
    expect(h.state?.main?.filter).toBeNull();
    expect(h.state?.fieldStatus?.['main.filter']?.availability).toBe('undeclared');
    expect(filterWidthFeedback).toMatchObject({
      confirmed: 3000,
      phase: 'idle',
      availability: 'available',
      scope: { control: 'filter-width', receiver: 0 },
    });
    const view = {
      ...fixture,
      modeFilter: {
        ...fixture.modeFilter!,
        filterWidth: { reading: { status: 'known' as const, value: 3000 }, availability },
        filterWidthMin: { reading: { status: 'known' as const, value: 300 }, availability },
        filterWidthMax: { reading: { status: 'known' as const, value: 4000 }, availability },
      },
    };
    const onFilterWidthChange = vi.fn(makeFilterHandlers().onFilterWidthChange);
    component = mount(FilterSurface, {
      target,
      props: {
        view,
        handles,
        part: 'filter',
        filterWidthFeedback,
        onFilterWidthChange,
      },
    });
    flushSync();

    const input = target.querySelector<HTMLInputElement>('[data-testid="filter-width"] input')!;
    expect(input.disabled).toBe(false);
    expect(input.value).toBe('3000');
    input.value = '3200';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();

    expect(onFilterWidthChange).toHaveBeenCalledExactlyOnceWith(3200);
    vi.advanceTimersByTime(200);
    expect(h.sendCommand).toHaveBeenCalledWith(
      'set_filter_width', { width: 3200, receiver: 0 }, expect.any(String),
    );
  });
});
