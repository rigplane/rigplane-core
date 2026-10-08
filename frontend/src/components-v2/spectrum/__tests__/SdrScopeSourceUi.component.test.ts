/**
 * MOR-3158 — component tests for the SDR scope-source UI trio under
 * `components-v2/spectrum/`: the RIG/AUDIO/SDR source badge (per source
 * and per SDR lifecycle state), the display-only source selector, and
 * the TX-frozen waterfall indicator. Pure-props components — no runtime
 * is mounted or mocked; the spectrum region's own routing tests (the
 * SDR describe in `components/spectrum/__tests__/SpectrumPanel.component.test.ts`)
 * pin the mount and the payload pass-through.
 *
 * Mutation probes this file exists to satisfy: flip any source->label
 * mapping, any SDR-state->tone mapping, the tooltip composition, the
 * null-source gate, the selector's single-source gate, or the TX
 * indicator's active gate — and a test below dies.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount, type ComponentProps } from 'svelte';
import { SvelteMap } from 'svelte/reactivity';
import ScopeSourceBadge, {
  SCOPE_SOURCE_LABELS,
  scopeSourceBadgeText,
  scopeSourceLabel,
  sdrStateTone,
} from '../ScopeSourceBadge.svelte';
import ScopeSourceSelector from '../ScopeSourceSelector.svelte';
import SdrTxFrozenIndicator from '../SdrTxFrozenIndicator.svelte';
import type { ScopeSourceId } from '../ScopeSourceBadge.svelte';
import type { SdrStatusPublic } from '$lib/types/state';

function sdr(overrides: Partial<SdrStatusPublic> = {}): SdrStatusPublic {
  return {
    state: 'streaming',
    device: 'rtlsdr=0',
    sampleRateHz: 2_400_000,
    spanHz: 2_000_000,
    txFrozen: false,
    overflowCount: 0,
    lastError: null,
    ...overrides,
  };
}

// SvelteMap-backed so a write re-runs the component's `$derived` chain —
// the same reactive-seam idiom the SpectrumPanel harness uses (MOR-2464).
function badgeProps() {
  const state = new SvelteMap<string, unknown>([['source', 'sdr'], ['sdr', sdr()]]);
  return {
    state,
    props: {
      get source() { return state.get('source') as ScopeSourceId | null; },
      get sdr() { return state.get('sdr') as SdrStatusPublic | null; },
    },
  };
}

let mounted: ReturnType<typeof mount>[] = [];

// Per-component mounts typed through `ComponentProps` (the harness idiom of
// `controls/__tests__/AttenuatorControl.test.ts`) so literal props keep
// their contract types instead of widening to `string`.
function mountBadge(props: ComponentProps<typeof ScopeSourceBadge>): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  mounted.push(mount(ScopeSourceBadge, { target, props }));
  flushSync();
  return target;
}

function mountSelector(props: ComponentProps<typeof ScopeSourceSelector>): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  mounted.push(mount(ScopeSourceSelector, { target, props }));
  flushSync();
  return target;
}

function mountFrozen(props: ComponentProps<typeof SdrTxFrozenIndicator>): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  mounted.push(mount(SdrTxFrozenIndicator, { target, props }));
  flushSync();
  return target;
}

afterEach(() => {
  mounted.forEach((component) => unmount(component));
  mounted = [];
  document.body.innerHTML = '';
});

describe('ScopeSourceBadge (MOR-3158)', () => {
  it('renders nothing without a scope source', () => {
    const target = mountBadge({ source: null });
    expect(target.querySelector('[data-testid="scope-source-badge"]')).toBeNull();
    expect(target.textContent).toBe('');
  });

  it.each([
    ['hardware', 'RIG'],
    ['audio_fft', 'AUDIO'],
    ['sdr', 'SDR'],
  ] as const)('labels the %s source as %s', (source, label) => {
    const target = mountBadge({ source });
    const badge = target.querySelector<HTMLElement>('[data-testid="scope-source-badge"]')!;
    expect(badge).not.toBeNull();
    expect(badge.textContent).toBe(label);
    expect(badge.dataset.source).toBe(source);
    expect(badge.dataset.tone).toBe('neutral');
    expect(badge.getAttribute('aria-label')).toBe(`Scope source: ${label}`);
    expect(badge.title).toBe(`Scope source: ${label}`);
  });

  it('keeps every known source labelled — a new contract member fails loudly', () => {
    expect(Object.keys(SCOPE_SOURCE_LABELS).sort()).toEqual(['audio_fft', 'hardware', 'sdr']);
  });

  it.each([
    ['streaming', 'green'],
    ['starting', 'yellow'],
    ['reconnecting', 'yellow'],
    ['error', 'red'],
    ['disabled', 'neutral'],
  ] as const)('colours the SDR %s state %s', (state, tone) => {
    const target = mountBadge({ source: 'sdr', sdr: sdr({ state }) });
    const badge = target.querySelector<HTMLElement>('[data-testid="scope-source-badge"]')!;
    expect(badge.dataset.tone).toBe(tone);
    expect(badge.dataset.sdrState).toBe(state);
    expect(badge.title).toBe(`Scope source: SDR ${state}`);
  });

  it('carries lastError in the tooltip when the SDR state reports one', () => {
    const target = mountBadge({
      source: 'sdr', sdr: sdr({ state: 'error', lastError: 'Device or resource busy' }),
    });
    const badge = target.querySelector<HTMLElement>('[data-testid="scope-source-badge"]')!;
    expect(badge.title).toBe('Scope source: SDR error — Device or resource busy');
    expect(badge.getAttribute('aria-label')).toBe(badge.title);
  });

  it('omits the error separator when lastError is null', () => {
    const target = mountBadge({
      source: 'sdr', sdr: sdr({ state: 'error', lastError: null }),
    });
    expect(target.querySelector<HTMLElement>('[data-testid="scope-source-badge"]')!.title)
      .toBe('Scope source: SDR error');
  });

  it('treats an unknown SDR leaf as an uncoloured, stateless badge', () => {
    const target = mountBadge({ source: 'sdr', sdr: null });
    const badge = target.querySelector<HTMLElement>('[data-testid="scope-source-badge"]')!;
    expect(badge.textContent).toBe('SDR');
    expect(badge.dataset.tone).toBe('neutral');
    expect(badge.dataset.sdrState).toBeUndefined();
    expect(badge.title).toBe('Scope source: SDR');
  });

  it('recategorizes the tone reactively when the SDR state changes', () => {
    const { state, props } = badgeProps();
    const target = mountBadge(props);
    const badge = target.querySelector<HTMLElement>('[data-testid="scope-source-badge"]')!;
    expect(badge.dataset.tone).toBe('green');
    state.set('sdr', sdr({ state: 'reconnecting' }));
    flushSync();
    expect(badge.dataset.sdrState).toBe('reconnecting');
    expect(badge.dataset.tone).toBe('yellow');
  });

  it('exhaustively maps every SDR state (mutation probe: a flipped mapping dies)', () => {
    expect(sdrStateTone('streaming')).toBe('green');
    expect(sdrStateTone('starting')).toBe('yellow');
    expect(sdrStateTone('reconnecting')).toBe('yellow');
    expect(sdrStateTone('error')).toBe('red');
    expect(sdrStateTone('disabled')).toBe('neutral');
    expect(scopeSourceLabel('hardware')).toBe('RIG');
    expect(scopeSourceLabel('audio_fft')).toBe('AUDIO');
    expect(scopeSourceLabel('sdr')).toBe('SDR');
    expect(scopeSourceBadgeText('sdr', sdr({ state: 'error', lastError: 'x' })))
      .toBe('Scope source: SDR error — x');
    expect(scopeSourceBadgeText('hardware', null)).toBe('Scope source: RIG');
  });
});

describe('ScopeSourceSelector (MOR-3158)', () => {
  it('renders nothing for a single available source', () => {
    const target = mountSelector({ sources: ['sdr'], active: 'sdr' });
    expect(target.querySelector('[data-testid="scope-source-selector"]')).toBeNull();
    expect(target.textContent).toBe('');
  });

  it('renders nothing for no available sources', () => {
    const target = mountSelector({ sources: [], active: null });
    expect(target.querySelector('[data-testid="scope-source-selector"]')).toBeNull();
  });

  it('names every available source and marks the active one', () => {
    const target = mountSelector({
      sources: ['hardware', 'sdr'], active: 'sdr',
    });
    const selector = target.querySelector<HTMLElement>('[data-testid="scope-source-selector"]')!;
    expect(selector).not.toBeNull();
    const keys = [...selector.querySelectorAll<HTMLElement>('.scope-source-key')];
    expect(keys.map((key) => key.textContent)).toEqual(['RIG', 'SDR']);
    expect(keys.map((key) => key.dataset.source)).toEqual(['hardware', 'sdr']);
    expect(keys[0]!.dataset.active).toBe('false');
    expect(keys[0]!.getAttribute('aria-current')).toBeNull();
    expect(keys[1]!.dataset.active).toBe('true');
    expect(keys[1]!.getAttribute('aria-current')).toBe('true');
    expect(selector.getAttribute('aria-label')).toBe('Scope sources: RIG, SDR (active)');
  });

  it('renders no active key when the active source is unknown', () => {
    const target = mountSelector({
      sources: ['hardware', 'sdr'], active: null,
    });
    const selector = target.querySelector<HTMLElement>('[data-testid="scope-source-selector"]')!;
    expect(selector.querySelector('[data-active="true"]')).toBeNull();
    expect(selector.getAttribute('aria-label')).toBe('Scope sources: RIG, SDR');
  });

  it('is display-only — no focusable element survives a mutation', () => {
    const target = mountSelector({
      sources: ['hardware', 'audio_fft', 'sdr'], active: 'audio_fft',
    });
    expect(target.querySelector('button, input, select, a[href], [tabindex]')).toBeNull();
    const keys = [...target.querySelectorAll<HTMLElement>('.scope-source-key')];
    expect(keys.map((key) => key.textContent)).toEqual(['RIG', 'AUDIO', 'SDR']);
    expect(keys[1]!.dataset.active).toBe('true');
  });
});

describe('SdrTxFrozenIndicator (MOR-3158)', () => {
  it('renders nothing while the source is not TX-frozen', () => {
    const target = mountFrozen({ active: false });
    expect(target.querySelector('[data-testid="sdr-tx-frozen"]')).toBeNull();
    expect(target.textContent).toBe('');
  });

  it('shows the TX marker over the held waterfall while TX-frozen', () => {
    const target = mountFrozen({ active: true });
    const marker = target.querySelector<HTMLElement>('[data-testid="sdr-tx-frozen"]')!;
    expect(marker).not.toBeNull();
    expect(marker.textContent).toContain('TX');
    expect(marker.getAttribute('aria-label')).toBe('Waterfall held during transmit');
    expect(marker.querySelector('.sdr-tx-frozen-marker')!.textContent).toBe('TX');
  });

  it('flips with the frozen flag reactively', () => {
    const state = new SvelteMap<string, boolean>([['active', false]]);
    const target = mountFrozen({
      get active() { return state.get('active')!; },
    });
    expect(target.querySelector('[data-testid="sdr-tx-frozen"]')).toBeNull();
    state.set('active', true);
    flushSync();
    expect(target.querySelector('[data-testid="sdr-tx-frozen"]')).not.toBeNull();
    state.set('active', false);
    flushSync();
    expect(target.querySelector('[data-testid="sdr-tx-frozen"]')).toBeNull();
  });
});
