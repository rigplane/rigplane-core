/**
 * Tests for the semantic VFO surface (MOR-1063), built on the MOR-1062
 * `RadioViewModel` contract and its four topology fixtures.
 *
 * Discriminating-test goals (see ticket acceptance evidence):
 *   - render VFO facts faithfully per fixture, across all four topologies;
 *   - the four topologies must render structurally differently — a test
 *     that would pass on identical renders is tautological, so we assert a
 *     rendered-DOM signature is pairwise distinct, mirroring
 *     `topology-fixtures.test.ts`'s own non-vacuous-distinctness guard;
 *   - unknown facts (ActiveRx, VFO slot, split/dualWatch) must render as an
 *     explicit "unknown" state, never silently defaulted;
 *   - selection/toggle intents fire with the right payload, and never fire
 *     while the control is disabled (mutation-kill coverage);
 *   - accessible names and disabled/absent gating per the MOR-977 two-level
 *     doctrine (structural = absent, operational = disabled);
 *   - i18n coverage: no unresolved catalog key leaks into rendered text.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ComponentProps } from 'svelte';
import VfoSurface from '../VfoSurface.svelte';
import { validateRadioViewModel, type RadioViewModel, type VfoSlot } from '../radio-view-model';
import { topologyFixtures, withAudioOnlyScope, type TopologyFixtureId } from '../fixtures/topologies';

const ids: readonly TopologyFixtureId[] = ['1/single', '1/ab', '2/ab_shared', '2/main_sub'];

let components: ReturnType<typeof mount>[] = [];

function mountSurface(props: ComponentProps<typeof VfoSurface>): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(VfoSurface, { target, props });
  flushSync();
  components.push(component);
  return target;
}

beforeEach(() => {
  components = [];
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  while (document.body.firstChild) document.body.removeChild(document.body.firstChild);
});

function expectedSlotKey(slot: VfoSlot): string {
  return slot.kind === 'slotted' ? slot.id : slot.kind;
}
function expectedFreq(hz: number | null): string {
  return hz === null ? '—' : `${(hz / 1_000_000).toFixed(6)} MHz`;
}

/** Normalized rendered-DOM summary — only VFO-surface-owned facts. */
function renderSignature(target: HTMLElement): string {
  const tiles = Array.from(target.querySelectorAll<HTMLElement>('[data-vfo-tile]')).map((el) => ({
    receiver: el.dataset.vfoReceiver,
    slot: el.dataset.vfoSlot,
    active: el.dataset.vfoActive,
    txTarget: el.dataset.vfoTxTarget,
    freq: el.querySelector('.vfo-freq')?.textContent,
    mode: el.querySelector('.vfo-mode')?.textContent,
  }));
  return JSON.stringify({
    tiles,
    activeReceiver: target.querySelector('[data-testid="vfo-active-receiver"]')?.getAttribute('data-active-receiver'),
    split: target.querySelector('[data-vfo-split]')?.getAttribute('aria-checked'),
    dualWatch: target.querySelector('[data-vfo-dual-watch]')?.getAttribute('aria-checked'),
  });
}

// ── Faithful rendering per fixture ──────────────────────────────────────────

describe.each(ids)('topology %s', (id) => {
  it('renders every VFO fact faithfully', () => {
    const model = topologyFixtures[id];
    const target = mountSurface({ viewModel: model });
    const tiles = target.querySelectorAll<HTMLElement>('[data-vfo-tile]');
    expect(tiles.length).toBe(model.vfos.length);

    model.vfos.forEach((vfo, i) => {
      const tile = tiles[i];
      expect(tile.dataset.vfoReceiver).toBe(vfo.receiver);
      expect(tile.dataset.vfoSlot).toBe(expectedSlotKey(vfo.slot));
      expect(tile.dataset.vfoActive).toBe(String(vfo.isActive));
      expect(tile.dataset.vfoTxTarget).toBe(String(vfo.isTxTarget));
      expect(tile.querySelector('.vfo-freq')?.textContent).toBe(expectedFreq(vfo.frequencyHz));
      expect(tile.querySelector('.vfo-mode')?.textContent).toContain(vfo.mode ?? '—');
      if (vfo.filter) expect(tile.querySelector('.vfo-mode')?.textContent).toContain(vfo.filter);
      expect(tile.querySelector('[data-vfo-tx-badge]') !== null).toBe(vfo.isTxTarget);
      const shownLabel =
        tile.querySelector('[data-vfo-select]')?.textContent?.trim() ??
        tile.querySelector('[data-vfo-label]')?.textContent?.trim();
      expect(shownLabel).toBe(vfo.label);
    });
  });

  it('never leaks an unresolved i18n catalog key', () => {
    const target = mountSurface({ viewModel: topologyFixtures[id] });
    expect(target.textContent).not.toMatch(/\[missing:/);
  });
});

// ── Non-vacuous structural distinctness across topologies (V2-style) ───────

it('renders four structurally different DOM signatures across the four topologies', () => {
  const signatures = ids.map((id) => renderSignature(mountSurface({ viewModel: topologyFixtures[id] })));
  expect(new Set(signatures).size).toBe(ids.length);
});

// ── S1: audio-only scope is orthogonal to the VFO surface ──────────────────

it.each(ids)('withAudioOnlyScope on %s changes nothing this surface renders (scope is out of scope)', (id) => {
  const base = renderSignature(mountSurface({ viewModel: topologyFixtures[id] }));
  const variant = renderSignature(mountSurface({ viewModel: withAudioOnlyScope(topologyFixtures[id]) }));
  expect(variant).toBe(base);
});

// ── Roles: receiver + slot identity text ────────────────────────────────────

it('shows a distinct role per VFO across single/dual and slotted/unslotted schemes', () => {
  const single = mountSurface({ viewModel: topologyFixtures['1/single'] });
  expect(single.querySelector('.vfo-role')?.textContent).toBe('MAIN');

  const ab = mountSurface({ viewModel: topologyFixtures['1/ab'] });
  const abRoles = Array.from(ab.querySelectorAll('.vfo-role')).map((e) => e.textContent);
  expect(abRoles).toEqual(['MAIN A', 'MAIN B']);

  const mainSub = mountSurface({ viewModel: topologyFixtures['2/main_sub'] });
  const roles = Array.from(mainSub.querySelectorAll('.vfo-role')).map((e) => e.textContent);
  expect(roles).toEqual(['MAIN A', 'MAIN B', 'SUB A', 'SUB B']);
});

// ── UNCERTAINTY IS VISIBLE ───────────────────────────────────────────────────

describe('uncertainty is rendered explicitly, never defaulted', () => {
  it('unknown activeReceiver renders an explicit "unknown" state, never MAIN', () => {
    const model: RadioViewModel = validateRadioViewModel({
      ...topologyFixtures['1/single'],
      activeReceiver: { status: 'unknown' },
    });
    const target = mountSurface({ viewModel: model });
    const el = target.querySelector('[data-testid="vfo-active-receiver"]');
    expect(el?.getAttribute('data-active-receiver')).toBe('unknown');
    expect(el?.getAttribute('data-active-receiver')).not.toBe('MAIN');
    expect(el?.textContent).toContain('unknown');
  });

  it('unknown VFO slot renders an explicit "unknown" state, never defaults to A', () => {
    const base = topologyFixtures['1/ab'];
    const model: RadioViewModel = validateRadioViewModel({
      ...base,
      vfos: [base.vfos[0], { ...base.vfos[1], slot: { kind: 'unknown' } }],
    });
    const target = mountSurface({ viewModel: model });
    const tiles = target.querySelectorAll<HTMLElement>('[data-vfo-tile]');
    expect(tiles[1].dataset.vfoSlot).toBe('unknown');
    expect(tiles[1].dataset.vfoSlot).not.toBe('A');
    expect(tiles[1].querySelector('.vfo-role')?.textContent).toContain('unknown');
  });

  it('R2: two VFOs on the SAME receiver with BOTH slots unobserved render without crashing, ' +
    'each with its own explicit unknown-slot presentation', () => {
    // MOR-988 §3.2/§4: missing/stale slot projects as unknown per VFO,
    // independently — the contract validator accepts two same-receiver VFOs
    // that are each separately unobserved; the surface must never fabricate
    // a shared/synthesized slot id to disambiguate them, and must not use a
    // key derived from slot identity (which would collide here).
    const base = topologyFixtures['1/ab'];
    const model: RadioViewModel = validateRadioViewModel({
      ...base,
      vfos: [
        { ...base.vfos[0], slot: { kind: 'unknown' } },
        { ...base.vfos[1], slot: { kind: 'unknown' } },
      ],
    });
    let target!: HTMLElement;
    expect(() => {
      target = mountSurface({ viewModel: model });
    }).not.toThrow();
    const tiles = target.querySelectorAll<HTMLElement>('[data-vfo-tile]');
    expect(tiles.length).toBe(2);
    tiles.forEach((tile) => {
      expect(tile.dataset.vfoSlot).toBe('unknown');
      expect(tile.dataset.vfoSlot).not.toBe('A');
      expect(tile.querySelector('.vfo-role')?.textContent).toContain('unknown');
    });
  });

  it('unknown dualWatch renders an explicit "unknown" tri-state, never "off"', () => {
    // 1/ab fixture carries dualWatch: { status: 'unknown' } verbatim.
    const target = mountSurface({ viewModel: topologyFixtures['1/ab'] });
    const toggle = target.querySelector<HTMLButtonElement>('[data-vfo-dual-watch]')!;
    expect(toggle.getAttribute('aria-checked')).toBe('mixed');
    expect(toggle.textContent).toContain('unknown');
    expect(toggle.textContent).not.toContain('off');
  });

  it('unknown split renders an explicit "unknown" tri-state', () => {
    const base = topologyFixtures['1/ab'];
    const model: RadioViewModel = validateRadioViewModel({ ...base, split: { status: 'unknown' } });
    const target = mountSurface({ viewModel: model });
    const toggle = target.querySelector<HTMLButtonElement>('[data-vfo-split]')!;
    expect(toggle.getAttribute('aria-checked')).toBe('mixed');
    expect(toggle.textContent).toContain('unknown');
    // R1 (review cycle 1): pin the disabled attribute itself, not just the
    // aria-checked/text-content facts above — mutation M14 deleted
    // `disabled={viewModel.split.status === 'unknown'}` and every other
    // assertion in this file still passed (dualWatch's equivalent was
    // pinned; split's was not).
    expect(toggle.disabled).toBe(true);
  });

  it('null frequency renders as an explicit placeholder, not 0 or blank', () => {
    const base = topologyFixtures['1/single'];
    const model: RadioViewModel = validateRadioViewModel({
      ...base,
      vfos: [{ ...base.vfos[0], frequencyHz: null }],
    });
    const target = mountSurface({ viewModel: model });
    expect(target.querySelector('.vfo-freq')?.textContent).toBe('—');
  });
});

// ── Selection intents: payload correctness + disabled/absent gating ────────

describe('VFO selection intent', () => {
  it('clicking a selectable inactive VFO emits onSelectVfo with the exact receiver+slot payload', () => {
    const onSelectVfo = vi.fn();
    const target = mountSurface({ viewModel: topologyFixtures['2/main_sub'], onSelectVfo });
    target.querySelector<HTMLButtonElement>('[data-vfo-receiver="MAIN"][data-vfo-slot="B"] [data-vfo-select]')!.click();
    expect(onSelectVfo).toHaveBeenCalledOnce();
    expect(onSelectVfo).toHaveBeenCalledWith({ receiver: 'MAIN', slot: { kind: 'slotted', id: 'B' } });
  });

  it('the active VFO renders no select control at all (nothing to choose)', () => {
    const target = mountSurface({ viewModel: topologyFixtures['2/main_sub'] });
    const activeTile = target.querySelector<HTMLElement>('[data-vfo-active="true"]')!;
    expect(activeTile.querySelector('[data-vfo-select]')).toBeNull();
  });

  it('a single-VFO topology renders no select control — structurally nothing to choose', () => {
    const target = mountSurface({ viewModel: topologyFixtures['1/single'] });
    expect(target.querySelector('[data-vfo-select]')).toBeNull();
  });

  it('a lone inactive VFO (structural edge case) still renders no select control', () => {
    // Independent of `isActive`: with only one VFO in the model there is
    // nothing else to select between, regardless of its active flag.
    const base = topologyFixtures['1/single'];
    const model: RadioViewModel = validateRadioViewModel({
      ...base,
      vfos: [{ ...base.vfos[0], isActive: false }],
    });
    const target = mountSurface({ viewModel: model });
    expect(target.querySelector('[data-vfo-select]')).toBeNull();
  });

  it('a VFO with an unobserved slot renders its select control disabled', () => {
    const base = topologyFixtures['1/ab'];
    const model: RadioViewModel = validateRadioViewModel({
      ...base,
      vfos: [base.vfos[0], { ...base.vfos[1], slot: { kind: 'unknown' } }],
    });
    const target = mountSurface({ viewModel: model });
    const button = target.querySelectorAll<HTMLButtonElement>('[data-vfo-select]')[0];
    expect(button.disabled).toBe(true);
  });

  it('MUTATION KILL: clicking a disabled (unknown-slot) select control never emits the intent, ' +
    'even if the native disabled gate is bypassed', () => {
    const onSelectVfo = vi.fn();
    const base = topologyFixtures['1/ab'];
    const model: RadioViewModel = validateRadioViewModel({
      ...base,
      vfos: [base.vfos[0], { ...base.vfos[1], slot: { kind: 'unknown' } }],
    });
    const target = mountSurface({ viewModel: model, onSelectVfo });
    const button = target.querySelectorAll<HTMLButtonElement>('[data-vfo-select]')[0];
    // A native `disabled` button already refuses to dispatch `click` in jsdom
    // and real browsers alike. Force it enabled first so this test proves
    // the component's OWN handler guard — not just DOM disabled semantics —
    // refuses to fabricate an A/B id and emit the intent.
    button.disabled = false;
    button.click();
    expect(onSelectVfo).not.toHaveBeenCalled();
  });
});

// ── Split / dual-watch toggle intents ───────────────────────────────────────

describe('split / dual-watch toggle intents', () => {
  it('clicking a known split toggle emits onToggleSplit', () => {
    const onToggleSplit = vi.fn();
    const target = mountSurface({ viewModel: topologyFixtures['1/single'], onToggleSplit });
    target.querySelector<HTMLButtonElement>('[data-vfo-split]')!.click();
    expect(onToggleSplit).toHaveBeenCalledOnce();
  });

  it('clicking a known dualWatch toggle emits onToggleDualWatch', () => {
    const onToggleDualWatch = vi.fn();
    const target = mountSurface({ viewModel: topologyFixtures['2/main_sub'], onToggleDualWatch });
    target.querySelector<HTMLButtonElement>('[data-vfo-dual-watch]')!.click();
    expect(onToggleDualWatch).toHaveBeenCalledOnce();
  });

  it('MUTATION KILL: clicking a disabled (unknown) dualWatch toggle never emits the intent, ' +
    'even if the native disabled gate is bypassed', () => {
    const onToggleDualWatch = vi.fn();
    // 1/ab carries dualWatch: unknown verbatim.
    const target = mountSurface({ viewModel: topologyFixtures['1/ab'], onToggleDualWatch });
    const toggle = target.querySelector<HTMLButtonElement>('[data-vfo-dual-watch]')!;
    toggle.disabled = false;
    toggle.click();
    expect(onToggleDualWatch).not.toHaveBeenCalled();
  });

  it('MUTATION KILL: clicking a disabled (unknown) split toggle never emits the intent, ' +
    'even if the native disabled gate is bypassed', () => {
    const onToggleSplit = vi.fn();
    const base = topologyFixtures['1/ab'];
    const model: RadioViewModel = validateRadioViewModel({ ...base, split: { status: 'unknown' } });
    const target = mountSurface({ viewModel: model, onToggleSplit });
    const toggle = target.querySelector<HTMLButtonElement>('[data-vfo-split]')!;
    toggle.disabled = false;
    toggle.click();
    expect(onToggleSplit).not.toHaveBeenCalled();
  });

  it('both split and dualWatch can be independently true at once (2/main_sub)', () => {
    const target = mountSurface({ viewModel: topologyFixtures['2/main_sub'] });
    expect(target.querySelector('[data-vfo-split]')?.getAttribute('aria-checked')).toBe('true');
    expect(target.querySelector('[data-vfo-dual-watch]')?.getAttribute('aria-checked')).toBe('true');
  });
});

// ── Accessibility basics ─────────────────────────────────────────────────────

describe('accessibility basics', () => {
  it('the surface exposes an accessible group name', () => {
    const target = mountSurface({ viewModel: topologyFixtures['2/main_sub'] });
    const region = target.querySelector('[data-testid="vfo-surface"]');
    expect(region?.getAttribute('role')).toBe('group');
    expect(region?.getAttribute('aria-label')?.length).toBeGreaterThan(0);
  });

  it('every enabled select control has a non-empty accessible name', () => {
    const target = mountSurface({ viewModel: topologyFixtures['2/main_sub'] });
    const buttons = target.querySelectorAll<HTMLButtonElement>('[data-vfo-select]');
    expect(buttons.length).toBeGreaterThan(0);
    buttons.forEach((b) => expect(b.getAttribute('aria-label')?.length).toBeGreaterThan(0));
  });

  it('split/dualWatch toggles have accessible names and role="switch"', () => {
    const target = mountSurface({ viewModel: topologyFixtures['2/main_sub'] });
    for (const sel of ['[data-vfo-split]', '[data-vfo-dual-watch]']) {
      const btn = target.querySelector<HTMLButtonElement>(sel)!;
      expect(btn.getAttribute('role')).toBe('switch');
      expect(btn.getAttribute('aria-label')?.length).toBeGreaterThan(0);
    }
  });

  it('disabled controls are excluded from the tab order (native disabled semantics)', () => {
    const base = topologyFixtures['1/ab'];
    const model: RadioViewModel = validateRadioViewModel({
      ...base,
      vfos: [base.vfos[0], { ...base.vfos[1], slot: { kind: 'unknown' } }],
    });
    const target = mountSurface({ viewModel: model });
    const disabledSelect = target.querySelectorAll<HTMLButtonElement>('[data-vfo-select]')[0];
    const disabledDualWatch = target.querySelector<HTMLButtonElement>('[data-vfo-dual-watch]')!;
    expect(disabledSelect.disabled).toBe(true);
    expect(disabledDualWatch.disabled).toBe(true);
    // jsdom mirrors the browser: a disabled element cannot become activeElement.
    disabledSelect.focus();
    expect(document.activeElement).not.toBe(disabledSelect);
  });

  it('focus order follows DOM order: enabled buttons appear in source-array order', () => {
    const target = mountSurface({ viewModel: topologyFixtures['2/main_sub'] });
    const focusable = Array.from(target.querySelectorAll<HTMLButtonElement>('button:not([disabled])'));
    // MAIN A is active (no select button); expect MAIN B, SUB A, SUB B selects,
    // then the split and dualWatch toggles, in that DOM order.
    const order = focusable.map(
      (el) => el.dataset.vfoSelect !== undefined
        ? `select:${el.closest('[data-vfo-tile]')?.getAttribute('data-vfo-receiver')}:${el.closest('[data-vfo-tile]')?.getAttribute('data-vfo-slot')}`
        : el.hasAttribute('data-vfo-split') ? 'split' : 'dualWatch',
    );
    expect(order).toEqual(['select:MAIN:B', 'select:SUB:A', 'select:SUB:B', 'split', 'dualWatch']);
  });
});

// ── MOR-1068: the three composition props a per-receiver cockpit needs ───────
// `selectionPoolSize` and `showRadioWideFacts` shipped with MOR-1067;
// `showVfoList` and `groupLabel` land with the radio-wide row's move out of
// the strips. Every default path below must stay byte-identical for the
// unsliced callers (sdr-test / LCD / mobile).

describe('selectionPoolSize (MOR-1067) — the ?? vs || distinction', () => {
  // MOR-1067 verification, gap (c): `??` -> `||` in the fallback survived
  // mutation because every existing caller passes a positive pool. An
  // EXPLICIT pool of 0 is the only value that separates them: `??` keeps 0
  // (nothing to choose -> control ABSENT), `||` treats 0 as "unset" and
  // silently falls back to the slice's own length, resurrecting the control.
  it('an explicit pool size of 0 means "nothing to choose", not "unset"', () => {
    const target = mountSurface({
      viewModel: topologyFixtures['2/main_sub'],
      selectionPoolSize: 0,
    });
    expect(target.querySelectorAll('[data-vfo-select]')).toHaveLength(0);
    // Same model with no pool override still offers the controls, so the
    // assertion above cannot pass for the trivial reason.
    const unsliced = mountSurface({ viewModel: topologyFixtures['2/main_sub'] });
    expect(unsliced.querySelectorAll('[data-vfo-select]').length).toBeGreaterThan(0);
  });

  it('a pool larger than the slice keeps the control present (MOR-1067 F1)', () => {
    const sliced: RadioViewModel = {
      ...topologyFixtures['2/ab_shared'],
      vfos: topologyFixtures['2/ab_shared'].vfos.filter((v) => v.receiver === 'MAIN'),
    };
    expect(mountSurface({ viewModel: sliced }).querySelectorAll('[data-vfo-select]')).toHaveLength(0);
    expect(
      mountSurface({ viewModel: sliced, selectionPoolSize: 2 }).querySelectorAll('[data-vfo-select]'),
    ).toHaveLength(1);
  });
});

describe('showVfoList (MOR-1068) — the radio-wide half, placeable on its own', () => {
  // Kills: ignoring the prop (the cockpit's global row would duplicate every
  // VFO tile already rendered in the strips), or letting it also suppress the
  // radio-wide facts (the row would render nothing at all).
  it('false renders the radio-wide facts with no VFO list', () => {
    const target = mountSurface({ viewModel: topologyFixtures['2/main_sub'], showVfoList: false });
    expect(target.querySelectorAll('[data-vfo-tile]')).toHaveLength(0);
    expect(target.querySelector('[data-testid="vfo-list"]')).toBeNull();
    expect(target.querySelectorAll('[data-vfo-split]')).toHaveLength(1);
    expect(target.querySelectorAll('[data-vfo-dual-watch]')).toHaveLength(1);
    expect(target.querySelectorAll('[data-testid="vfo-active-receiver"]')).toHaveLength(1);
  });

  // Kills: defaulting the new prop to false — every unsliced caller would
  // lose its VFO tiles.
  it('defaults to true: the unsliced surface is unchanged', () => {
    const target = mountSurface({ viewModel: topologyFixtures['2/main_sub'] });
    expect(target.querySelectorAll('[data-vfo-tile]')).toHaveLength(4);
    expect(target.querySelector('[data-testid="vfo-list"]')).not.toBeNull();
  });
});

describe('groupLabel (MOR-1068) — distinct accessible names per mounted surface', () => {
  // Kills: ignoring the prop. A cockpit mounts three of these surfaces at
  // once (MAIN strip, SUB strip, radio-wide row); with one shared generic
  // name assistive tech sees three identical groups and cannot tell which
  // receiver it is in.
  it('overrides the group accessible name when supplied', () => {
    const target = mountSurface({
      viewModel: topologyFixtures['2/main_sub'], groupLabel: 'Receiver SUB',
    });
    expect(target.querySelector('[data-testid="vfo-surface"]')?.getAttribute('aria-label'))
      .toBe('Receiver SUB');
  });

  it('falls back to the generic catalog label when absent', () => {
    const target = mountSurface({ viewModel: topologyFixtures['2/main_sub'] });
    const label = target.querySelector('[data-testid="vfo-surface"]')?.getAttribute('aria-label');
    expect(label).toBeTruthy();
    expect(label).not.toBe('Receiver SUB');
    // i18n coverage: never an unresolved catalog key.
    expect(label).not.toContain('core.vfo.');
  });
});
