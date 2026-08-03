/**
 * MOR-1065 slice b — the semantic VFO surface wired to live runtime state.
 *
 * Slice b owns the VFO half only: the adapter output actually reaching a
 * mounted surface, and the surface's selection/toggle intents reaching the
 * command bus with the real slot identity. Every TX assertion — authority
 * snapshot pass-through, owner identity, ungated unkey, teardown release,
 * fault recovery, and the MOR-617 preflight — arrives with the TX half in
 * slice c, together with the code it pins.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';

const h = vi.hoisted(() => ({
  state: null as unknown,
  caps: null as unknown,
  selectVfo: vi.fn(),
  splitToggle: vi.fn(),
  dualWatchToggle: vi.fn(),
}));

vi.mock('$lib/runtime', () => ({
  runtime: {
    get state() { return h.state; },
    get caps() { return h.caps; },
  },
}));
vi.mock('../command-bus', () => ({
  makeVfoHandlers: () => ({
    onVfoSelect: h.selectVfo,
    onSplitToggle: h.splitToggle,
    onDualWatchToggle: h.dualWatchToggle,
  }),
}));

import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';

const fresh = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };
const slot = (freqHz: number) => ({ freqHz, mode: 'USB', filterNum: 1, dataMode: 0 });

function liveState(): ServerState {
  const paths = ['active', 'split', 'dualWatch', 'txTarget'];
  for (const rx of ['main', 'sub']) {
    paths.push(`${rx}.freqHz`, `${rx}.mode`, `${rx}.filter`, `${rx}.activeSlot`);
    for (const v of ['vfoA', 'vfoB']) {
      paths.push(`${rx}.${v}.freqHz`, `${rx}.${v}.mode`, `${rx}.${v}.filterNum`);
    }
  }
  const receiver = (hz: number) => ({
    ...slot(hz), vfoA: slot(hz), vfoB: slot(hz + 50000), activeSlot: 'A', filter: 1,
  });
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: receiver(14250000), sub: receiver(14300000),
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}

const liveCaps = (): Capabilities => ({
  model: 'fixture', scope: false, audio: true, tx: true,
  capabilities: ['audio', 'tx', 'dual_rx'], receivers: 2, vfoScheme: 'main_sub',
  freqRanges: [], modes: [], filters: [],
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
  scopeSource: null, audioFftAvailable: false,
} as unknown as Capabilities);

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function render(): void {
  target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(SemanticRadioSurfaces, { target });
  flushSync();
}

const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;

beforeEach(() => {
  h.state = liveState();
  h.caps = liveCaps();
  h.selectVfo = vi.fn();
  h.splitToggle = vi.fn();
  h.dualWatchToggle = vi.fn();
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
});

describe('the VFO surface renders from the live adapter output', () => {
  it('mounts the semantic VFO surface with the derived view model', () => {
    render();
    expect(q('[data-testid="vfo-surface"]')).not.toBeNull();
    expect(target.querySelectorAll('[data-vfo-tile]')).toHaveLength(4);
    expect(q('[data-testid="vfo-active-receiver"]')?.dataset.activeReceiver).toBe('MAIN');
  });

  // MUTATION KILLED: rendering the surface against a fabricated fallback model
  // when capabilities have not loaded. There is no safe default topology.
  it('renders no surface at all rather than guessing when capabilities are absent', () => {
    h.caps = null;
    render();
    expect(q('[data-testid="vfo-surface"]')).toBeNull();
    expect(q('[data-testid="semantic-radio-surfaces"]')).not.toBeNull();
  });

  it('routes the VFO selection intent to the command bus with the real slot id', () => {
    render();
    const buttons = [...target.querySelectorAll<HTMLButtonElement>('[data-vfo-select]')];
    buttons[0].click();
    flushSync();
    expect(h.selectVfo).toHaveBeenCalledWith('MAIN', 'B');
  });

  it('routes the split toggle straight through to the command bus', () => {
    render();
    q<HTMLButtonElement>('[data-vfo-split]')!.click();
    flushSync();
    expect(h.splitToggle).toHaveBeenCalledTimes(1);
  });

  // MUTATION KILLED: sending a blind `true` (or the current value) for
  // dual-watch instead of its negation — the toggle would latch rather than
  // toggle.
  it('sends the negated dual-watch value', () => {
    render();
    q<HTMLButtonElement>('[data-vfo-dual-watch]')!.click();
    flushSync();
    expect(h.dualWatchToggle).toHaveBeenCalledWith(true);
  });

  // MUTATION KILLED: dropping the `status === 'known'` guard in
  // `toggleDualWatch` — an unobserved fact has no defined negation, so the
  // wiring would command a value it invented. (The surface also disables the
  // control; this pins the wiring's own half of the two-level gate.)
  it('commands nothing for an unobserved dual-watch fact', () => {
    h.state = { ...liveState(), fieldStatus: {} } as unknown as ServerState;
    render();
    const toggle = q<HTMLButtonElement>('[data-vfo-dual-watch]')!;
    expect(toggle.getAttribute('aria-checked')).toBe('mixed');
    toggle.click();
    flushSync();
    expect(h.dualWatchToggle).not.toHaveBeenCalled();
  });
});
