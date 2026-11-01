/**
 * MOR-3677 — the FilterSurface WIDTH ROW renders one honest state per
 * `FilterWidthDisplay` (design §3 rendering table): text for the value
 * cell, a reason on the row's `title`/`aria-describedby` channel, a
 * per-state `data-width-state`, and the slider disabled everywhere except
 * `known`. The unobserved row keeps the MOR-2688 empty box (never a
 * placeholder) and the existing `field-not-observed` disabled-reason code.
 *
 * The MOR-2535 double-click reset gesture is pinned AGAIN in the
 * radio-default state: the row may say "no Hz" and still dispatch exactly
 * ONE `reset_filter_width` — the reset is the gesture that leaves the
 * radio-default state, so it must never be disabled by the display state.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRawSnippet, flushSync, mount, unmount } from 'svelte';
import { setLocale } from '$lib/i18n';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { ControlSessionSnapshot } from '$lib/runtime/frontend-runtime';
import type { FilterWidthDisplay, ModeFilterViewModel, RadioViewModel } from '../radio-view-model';
import type { FilterInstrumentHandles } from '../filter-instruments';

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
import { topologyFixtures, withModeFilter } from '../fixtures/topologies';

const handles: FilterInstrumentHandles = {
  mode: createRawSnippet(() => ({ render: () => '<span></span>' })),
  filter: createRawSnippet(() => ({ render: () => '<span></span>' })),
  shape: createRawSnippet(() => ({ render: () => '<span></span>' })),
  dataMode: createRawSnippet(() => ({ render: () => '<span></span>' })),
};

const AVAILABILITY = { structural: true, operational: true } as const;
const OPERATIONAL_OFF = { structural: true, operational: false } as const;
const KNOWN_WIDTH = { reading: { status: 'known' as const, value: 3000 }, availability: AVAILABILITY };
const NULL_WIDTH = { reading: { status: 'unknown' as const }, availability: AVAILABILITY };
const WITHHELD_WIDTH = { reading: { status: 'unknown' as const }, availability: OPERATIONAL_OFF };

/** A `modeFilter` whose width field matches the display state the row is
 *  driven with — the shape the adapter emits for that state. */
function widthView(
  display: FilterWidthDisplay,
  field: ModeFilterViewModel['filterWidth'],
): RadioViewModel {
  const fixture = withModeFilter(topologyFixtures['1/single']);
  return {
    ...fixture,
    modeFilter: { ...fixture.modeFilter!, filterWidthDisplay: display, filterWidth: field },
  };
}

describe('MOR-3677 FilterSurface width row renders the FilterWidthDisplay states', () => {
  let target: HTMLDivElement;
  let component: ReturnType<typeof mount> | null;

  beforeEach(() => {
    vi.useFakeTimers();
    setLocale('en-US');
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

  function mountRow(view: RadioViewModel, props: Record<string, unknown> = {}): HTMLElement {
    component = mount(FilterSurface, {
      target,
      props: { view, handles, part: 'filter', ...props },
    });
    flushSync();
    return target.querySelector<HTMLElement>('[data-testid="filter-width"]')!;
  }

  it('known: shows the confirmed Hz, stays enabled, and carries no reason', () => {
    const row = mountRow(widthView({ state: 'known', hz: 3000, stale: false }, KNOWN_WIDTH));
    expect(row.dataset.widthState).toBe('known');
    expect(row.dataset.disabledReason).toBeUndefined();
    expect(row.getAttribute('title')).toBeNull();
    expect(row.querySelector<HTMLElement>('output')!.textContent).toBe('3000');
    expect(row.querySelector<HTMLInputElement>('input')!.disabled).toBe(false);
  });

  it('known but stale: still the Hz value — staleness never blanks a held reading', () => {
    const row = mountRow(widthView({ state: 'known', hz: 3000, stale: true }, KNOWN_WIDTH));
    expect(row.dataset.widthState).toBe('known');
    expect(row.querySelector<HTMLElement>('output')!.textContent).toBe('3000');
  });

  it('radio-default: the value cell names the radio default, the row explains why, the slider is disabled', () => {
    const row = mountRow(widthView({ state: 'radio-default' }, NULL_WIDTH));
    expect(row.dataset.widthState).toBe('radio-default');
    expect(row.dataset.disabledReason).toBe('radio-default');
    expect(row.getAttribute('title')).toBe(
      'The radio is using its own default width for this mode and does not report it in Hz.',
    );
    expect(row.querySelector<HTMLElement>('output')!.textContent).toBe('Radio default');
    expect(row.querySelector<HTMLInputElement>('input')!.disabled).toBe(true);
    expect(row.querySelector('[aria-describedby]')?.getAttribute('aria-describedby')).toContain('width-reason');
  });

  it('narrow: the value cell names NARROW, the row explains why, the slider is disabled', () => {
    const row = mountRow(widthView({ state: 'narrow' }, WITHHELD_WIDTH));
    expect(row.dataset.widthState).toBe('narrow');
    expect(row.dataset.disabledReason).toBe('narrow');
    expect(row.getAttribute('title')).toBe(
      'NARROW is on, so the radio uses its own narrow width, which is not reported in Hz.',
    );
    expect(row.querySelector<HTMLElement>('output')!.textContent).toBe('Narrow (radio)');
    expect(row.querySelector<HTMLInputElement>('input')!.disabled).toBe(true);
  });

  it('not-reported: an empty value cell (MOR-2688) with the not-reported reason on the row', () => {
    const row = mountRow(widthView({ state: 'unknown', reason: 'not-reported' }, NULL_WIDTH));
    expect(row.dataset.widthState).toBe('not-reported');
    expect(row.dataset.disabledReason).toBe('not-reported');
    expect(row.getAttribute('title')).toBe(
      'The radio does not report a filter width for this mode.',
    );
    expect(row.querySelector<HTMLElement>('output')!.textContent).toBe('');
    expect(row.querySelector<HTMLInputElement>('input')!.disabled).toBe(true);
  });

  it('not-observed: the empty unlit box keeps the existing field-not-observed code and shared reason text', () => {
    const row = mountRow(widthView({ state: 'unknown', reason: 'not-observed' }, WITHHELD_WIDTH));
    expect(row.dataset.widthState).toBe('not-observed');
    expect(row.dataset.disabledReason).toBe('field-not-observed');
    expect(row.getAttribute('title')).toBe('Not yet observed');
    expect(row.querySelector<HTMLElement>('output')!.textContent).toBe('');
    expect(row.querySelector<HTMLInputElement>('input')!.disabled).toBe(true);
  });

  it('the reset hint names the radio\'s own default width, not a bare "default"', () => {
    h.caps = {
      ...FTX1_CAPABILITIES,
      capabilities: [...FTX1_CAPABILITIES.capabilities, 'filter_width_radio_default'],
      providerGeneration: 31,
    };
    const onWidthReset = vi.fn(makeFilterHandlers().onFilterWidthReset);
    const row = mountRow(widthView({ state: 'radio-default' }, NULL_WIDTH), { onWidthReset });
    expect(row.querySelector<HTMLElement>('.filter-level-name')!.getAttribute('title')).toBe(
      "Double-click: return to the radio's own default width (can be narrow in CW and data modes).",
    );
  });

  it('radio-default: the double-click still sends exactly ONE reset_filter_width', () => {
    h.caps = {
      ...FTX1_CAPABILITIES,
      capabilities: [...FTX1_CAPABILITIES.capabilities, 'filter_width_radio_default'],
      providerGeneration: 31,
    };
    const onWidthReset = vi.fn(makeFilterHandlers().onFilterWidthReset);
    const row = mountRow(widthView({ state: 'radio-default' }, NULL_WIDTH), { onWidthReset });

    row.querySelector<HTMLElement>('.filter-level-name')!
      .dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
    flushSync();

    expect(onWidthReset).toHaveBeenCalledTimes(1);
    expect(h.sendCommand.mock.calls.map(([name, params]) => [name, params])).toEqual([
      ['reset_filter_width', { receiver: 0 }],
    ]);
  });
});
