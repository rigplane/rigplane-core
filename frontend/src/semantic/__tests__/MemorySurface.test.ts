/**
 * MOR-2425 phase A — the semantic memory-channel surface.
 *
 * `MemorySurface` is not wired into any zone yet (no `memory` entry exists
 * in `SEMANTIC_SURFACE_NAMES` — see the phase A handoff report for why).
 * This file pins the surface in isolation: facts/callbacks in, the shared
 * `memory-channels.ts` module as the one storage owner, no legacy
 * `data-panel-id` markers.
 *
 * Every gate is pinned by a REAL click event dispatched past the `disabled`
 * attribute (`forceClick`), not `.click()` — `.click()` is a documented
 * no-op on a disabled button in this codebase's test suite
 * (`AntennaSurface.test.ts`) and would make the handler-level guard pin
 * vacuous.
 */
import { readFileSync } from 'node:fs';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import MemorySurface, { MAX_MEMORY_CHANNELS, UNKNOWN_TEXT } from '../MemorySurface.svelte';
import { MEMORY_STORAGE_KEY, type MemoryEntry } from '../memory-channels';
import type { MemoryPanelProps } from '../../lib/runtime/props/panel-props';

const SOURCE = readFileSync('src/semantic/MemorySurface.svelte', 'utf8');
/** Comments stripped, so the file's own doctrine prose can never be what a
 *  source-scanning test matches (same idiom as `AntennaSurface.test.ts`). */
const CODE = SOURCE.replace(/<!--[\s\S]*?-->/g, '');

const KNOWN: MemoryPanelProps = { activeFreqHz: 14_074_000, activeMode: 'USB', vfoIdentityKnown: true };
const UNKNOWN_IDENTITY: MemoryPanelProps = { activeFreqHz: 7_100_000, activeMode: 'LSB', vfoIdentityKnown: false };

function seed(entries: Record<number, MemoryEntry>): void {
  const obj: Record<string, MemoryEntry> = {};
  for (const [k, v] of Object.entries(entries)) obj[k] = v;
  localStorage.setItem(MEMORY_STORAGE_KEY, JSON.stringify(obj));
}

function render(facts: MemoryPanelProps, handlers: Record<string, unknown> = {}) {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(MemorySurface, { target, props: { facts, ...handlers } });
  flushSync();
  const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
  return {
    target,
    dispose: () => { unmount(component); target.remove(); },
    row: (ch: number) => q<HTMLElement>(`[data-channel="${ch}"]`),
    btn: (ch: number, suffix: string) => q<HTMLButtonElement>(`[data-testid="memory-channel-${ch}-${suffix}"]`),
  };
}

/** The bypass pattern from `AntennaSurface.test.ts`: a REAL click event past
 *  `disabled`, proving the handler's own guard rather than the attribute. */
function forceClick(node: HTMLElement): void {
  node.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  flushSync();
}

afterEach(() => { localStorage.clear(); });

describe('the memory surface owns no radio state and duplicates no storage logic', () => {
  it('imports only facts, display formatting and the shared storage module', () => {
    const specifiers = [...CODE.matchAll(/from\s+'([^']+)'/g)].map((m) => m[1]);
    expect(specifiers.length).toBeGreaterThan(0);
    expect([...new Set(specifiers)].sort()).toEqual([
      '../components-v2/display/frequency-format',
      '../lib/runtime/props/panel-props',
      './memory-channels',
    ]);
  });

  // Kills: reintroducing a second copy of the localStorage key/persist logic
  // inside the surface instead of routing through `memory-channels.ts`.
  it('never spells out the storage key itself — it is owned once, by memory-channels.ts', () => {
    expect(CODE).not.toContain(MEMORY_STORAGE_KEY);
  });

  it('declares no lifecycle hook and no effect', () => {
    for (const forbidden of ['onMount', 'onDestroy', '$effect', 'import(']) {
      expect(CODE).not.toContain(forbidden);
    }
  });

  it('renders no legacy data-panel-id marker', () => {
    const r = render(KNOWN);
    expect(r.target.querySelector('[data-panel-id]')).toBeNull();
    r.dispose();
  });
});

describe('the channel list', () => {
  it('renders all 99 channels once "All" is shown', () => {
    const r = render(KNOWN);
    const checkbox = r.target.querySelector('input[type="checkbox"]') as HTMLInputElement;
    checkbox.click();
    flushSync();
    expect(r.target.querySelectorAll('[role="listitem"]')).toHaveLength(MAX_MEMORY_CHANNELS);
    expect(MAX_MEMORY_CHANNELS).toBe(99);
    r.dispose();
  });

  it('renders only populated channels by default', () => {
    seed({ 4: { freq: 7_074_000, mode: 'USB', name: 'net' }, 40: { freq: 3_573_000, mode: 'LSB', name: '' } });
    const r = render(KNOWN);
    expect(r.target.querySelectorAll('[role="listitem"]')).toHaveLength(2);
    expect(r.row(4)).not.toBeNull();
    expect(r.row(40)).not.toBeNull();
    r.dispose();
  });
});

describe('recall — per-channel wiring (MUTATION: wiring every button to channel 0)', () => {
  it.each([2, 5, 9])('channel %d recalls itself, not channel 0 or another channel', (ch) => {
    seed({ 2: { freq: 1, mode: 'USB', name: '' }, 5: { freq: 2, mode: 'USB', name: '' }, 9: { freq: 3, mode: 'USB', name: '' } });
    const onRecall = vi.fn(() => true);
    const r = render(KNOWN, { onRecall });
    r.btn(ch, 'recall')!.click();
    flushSync();
    expect(onRecall).toHaveBeenCalledExactlyOnceWith(ch);
    r.dispose();
  });

  it('does not recall when the button reports refusal', () => {
    seed({ 2: { freq: 1, mode: 'USB', name: '' } });
    const onRecall = vi.fn(() => false);
    const r = render(KNOWN, { onRecall });
    r.btn(2, 'recall')!.click();
    flushSync();
    expect(r.row(2)?.classList.contains('selected')).toBe(false);
    r.dispose();
  });
});

describe('store', () => {
  it('calls onStore exactly once with the target channel, active frequency and mode', () => {
    const onStore = vi.fn(() => true);
    const r = render(KNOWN, { onStore });
    const checkbox = r.target.querySelector('input[type="checkbox"]') as HTMLInputElement;
    checkbox.click();
    flushSync();
    r.btn(7, 'store')!.click();
    flushSync();
    expect(onStore).toHaveBeenCalledExactlyOnceWith(7, 14_074_000, 'USB');
    r.dispose();
  });

  // MUTATION: removing the `!facts.vfoIdentityKnown` guard inside
  // `storeVfoToChannel` must be caught even though the button is ALSO
  // `disabled` — bypass the attribute with a real dispatched click.
  it('is refused — onStore never fires — when vfoIdentityKnown is false, even past the disabled attribute', () => {
    const onStore = vi.fn(() => true);
    const r = render(UNKNOWN_IDENTITY, { onStore });
    const checkbox = r.target.querySelector('input[type="checkbox"]') as HTMLInputElement;
    checkbox.click();
    flushSync();
    const button = r.btn(11, 'store')!;
    expect(button.disabled).toBe(true);
    forceClick(button);
    expect(onStore).not.toHaveBeenCalled();
    r.dispose();
  });
});

describe('clear', () => {
  it('calls onClear exactly once with the channel being cleared', () => {
    seed({ 12: { freq: 1, mode: 'USB', name: '' }, 20: { freq: 2, mode: 'USB', name: '' } });
    const onClear = vi.fn(() => true);
    const r = render(KNOWN, { onClear });
    r.btn(12, 'clear')!.click();
    flushSync();
    (r.row(12)!.querySelector('.clear-confirm') as HTMLButtonElement).click();
    flushSync();
    expect(onClear).toHaveBeenCalledExactlyOnceWith(12);
    r.dispose();
  });
});

describe('rename', () => {
  it('calls onRename once with the channel and the new name, and persists through memory-channels.ts', () => {
    seed({ 3: { freq: 14_200_000, mode: 'USB', name: '' } });
    const onRename = vi.fn();
    const r = render(KNOWN, { onRename });
    r.btn(3, 'name')!.click();
    flushSync();
    const input = r.btn(3, 'name-input') as unknown as HTMLInputElement;
    input.value = 'CLUB NET';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    flushSync();
    expect(onRename).toHaveBeenCalledExactlyOnceWith(3, 'CLUB NET');
    r.dispose();

    // Survives remount — proof the write went through the shared module,
    // not into this component instance's own memory.
    const r2 = render(KNOWN);
    expect(r2.btn(3, 'name')!.textContent?.trim()).toBe('CLUB NET');
    r2.dispose();
  });
});

describe('unknown VFO identity (WRONG-VFO GUARD)', () => {
  // MUTATION: gating the readout on `Number.isFinite(activeFreqHz)` instead
  // of `vfoIdentityKnown` — `UNKNOWN_IDENTITY` carries a real finite
  // frequency, so that reversion would render a number here.
  it('never renders a number for the active-VFO readout, even though the raw frequency is a real finite value', () => {
    const r = render(UNKNOWN_IDENTITY);
    const readout = r.target.querySelector('[data-testid="memory-active-vfo"]')!;
    expect(readout.textContent).not.toMatch(/\d/);
    expect(readout.textContent?.trim()).toBe(`${UNKNOWN_TEXT} ${UNKNOWN_TEXT}`);
    expect(readout.getAttribute('data-observed')).toBe('false');
    r.dispose();
  });

  it('renders the real frequency and mode once identity is known', () => {
    const r = render(KNOWN);
    const readout = r.target.querySelector('[data-testid="memory-active-vfo"]')!;
    expect(readout.textContent).toContain('14.074');
    expect(readout.textContent).toContain('USB');
    r.dispose();
  });
});
