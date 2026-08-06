/**
 * MOR-1368 (v3-rework S9) — the RIGHT sidebar's half of the MOR-1364
 * suppression channel, pinned at the component.
 *
 * WHY THIS FILE EXISTS. `rx-audio`, `dsp` and `cw` are the three panels that
 * live in BOTH sidebars, and `lib/drag-reorder.svelte.ts` lets the operator
 * move them across (a module-level registry performs the cross-sidebar
 * transfer). A suppression wired on the left only therefore does not retire
 * the twin — it relocates it to wherever the operator last dragged it, and for
 * `cw` that means a second break-in control on screen beside the
 * SAFETY-CRITICAL `CwKeyerSurface` (MOR-1310), disagreeing about one radio
 * setting. `LeftSidebar.test.ts` covers only the drag-order localStorage
 * logic and no sidebar had a suppression test of its own, so the right side
 * was pinned exclusively end-to-end through `RadioLayout`.
 *
 * Scope is the GATE, not the panels: every child is stubbed, so a failure here
 * is unambiguously about which `{#if}` decided to render.
 *
 * Isolated pool by name (`*.component.test.ts`), per the MOR-1272 doctrine.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { SemanticSurfaceName } from '../../../presentation/layouts/contract';

vi.mock('$lib/stores/capabilities.svelte', () => ({
  hasCapability: vi.fn(() => true),
  hasAudioFft: vi.fn(() => true),
}));

// Hoisted by vitest above the imports, so the factory must be inline rather
// than a shared const (which would not be initialised when the mock runs).
vi.mock('../../panels/RxAudioPanel.svelte', async () => ({ default: (await import('./SpectrumPanelStub.svelte')).default }));
vi.mock('../../panels/DspPanel.svelte', async () => ({ default: (await import('./SpectrumPanelStub.svelte')).default }));
vi.mock('../../panels/TxPanel.svelte', async () => ({ default: (await import('./SpectrumPanelStub.svelte')).default }));
vi.mock('../../panels/CwPanel.svelte', async () => ({ default: (await import('./SpectrumPanelStub.svelte')).default }));
vi.mock('../../panels/MemoryPanel.svelte', async () => ({ default: (await import('./SpectrumPanelStub.svelte')).default }));
vi.mock('../../panels/audio-scope/AudioSpectrumPanel.svelte', async () => ({ default: (await import('./SpectrumPanelStub.svelte')).default }));

import RightSidebar from '../RightSidebar.svelte';

/** Every panel the right sidebar can host, so no pin passes vacuously for the
 *  boring reason that the drag order never offered the panel. */
const RIGHT_ALL = ['rx-audio', 'audio-scope', 'dsp', 'tx', 'cw', 'memory'];

let mounted: ReturnType<typeof mount>[] = [];

function render(declared: SemanticSurfaceName[] = []): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  mounted.push(mount(RightSidebar, {
    target,
    props: { declared: new Set<SemanticSurfaceName>(declared) },
  }));
  flushSync();
  return target;
}

const panelIds = (t: HTMLElement) => [...t.querySelectorAll('[data-panel-id]')]
  .map((el) => el.getAttribute('data-panel-id')!)
  .sort();

beforeEach(() => {
  mounted = [];
  localStorage.setItem('rigplane:right-panel-order', JSON.stringify(RIGHT_ALL));
});

afterEach(() => {
  mounted.forEach((c) => unmount(c));
  document.body.innerHTML = '';
  localStorage.clear();
});

describe('the right sidebar carries the same suppression channel as the left (MOR-1368)', () => {
  // INERTNESS BASELINE: with no zone declared, every panel renders. Without
  // this the suppression pins below could be satisfied by a panel that never
  // rendered in the first place.
  it('renders every panel when the manifest declares nothing', () => {
    expect(panelIds(render())).toEqual(['audio-scope', 'cw', 'dsp', 'memory', 'rx-audio', 'tx']);
  });

  // ONE ROW PER CROSS-SIDEBAR TWIN, both directions in one assertion: the
  // declared family's panel is gone and every sibling survives. A predicate
  // wired to the wrong surface name dies on the survival half.
  it.each([
    ['rxAudio', 'rx-audio'],
    ['dsp', 'dsp'],
    ['cwKeyer', 'cw'],
  ] as const)('declaring %s retires the %s panel here, and nothing else', (surface, panelId) => {
    const ids = panelIds(render([surface]));
    expect(ids).not.toContain(panelId);
    expect(ids).toEqual(
      ['audio-scope', 'cw', 'dsp', 'memory', 'rx-audio', 'tx'].filter((id) => id !== panelId),
    );
  });

  // The S9 set together — the shape `desktop-v2` actually ships after this
  // slice. What is left is exactly the panels with no semantic twin at all
  // (`audio-scope`, `memory`) plus `tx`, which is R9's and follows
  // `hideTxPanel`, never `declared`.
  it('retires all three S9 twins at once and leaves only the untwinned panels', () => {
    expect(panelIds(render(['rxAudio', 'dsp', 'cwKeyer'])))
      .toEqual(['audio-scope', 'memory', 'tx']);
  });

  // R9, restated at this component: `rxTx` is NOT on this channel. Declaring
  // it must not touch the TX panel — that decision belongs to `hideTxPanel`,
  // which follows the semantic DECK (MOR-1313). Folding the two would strand
  // an operator with no unkey affordance on an `rxTx`-only manifest.
  it('declaring rxTx does not suppress the TX panel — that is hideTxPanel\'s job', () => {
    expect(panelIds(render(['rxTx']))).toContain('tx');
    const t = document.createElement('div');
    document.body.appendChild(t);
    mounted.push(mount(RightSidebar, {
      target: t,
      props: { hideTxPanel: true, declared: new Set<SemanticSurfaceName>() },
    }));
    flushSync();
    expect(panelIds(t)).not.toContain('tx');
  });

  // An omitting caller is a guaranteed no-op — the property that let the
  // S6-pre channel land inert, and the reason `LcdLayout` mounting this
  // component without `declared` cannot silently suppress anything.
  it('defaults to suppressing nothing when the prop is omitted', () => {
    const t = document.createElement('div');
    document.body.appendChild(t);
    mounted.push(mount(RightSidebar, { target: t, props: {} }));
    flushSync();
    expect(panelIds(t)).toEqual(['audio-scope', 'cw', 'dsp', 'memory', 'rx-audio', 'tx']);
  });
});
