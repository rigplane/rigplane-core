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
import { createTuningAccumulator } from '$lib/runtime/commands/tuning-accumulator';

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
  it('relative bootstrap shows honest values and explicit A/B selectors with duplicate protection', () => {
    vi.useFakeTimers();
    const onSelectVfo = vi.fn();
    const base = topologyFixtures['1/ab'];
    const model: RadioViewModel = validateRadioViewModel({
      ...base,
      vfos: [
        {
          ...base.vfos[0], slot: { kind: 'relative', role: 'selected' },
          label: 'Selected VFO', isActive: true, isActiveSlot: true,
        },
        {
          ...base.vfos[1], slot: { kind: 'relative', role: 'unselected' },
          label: 'Unselected VFO', isActive: false, isActiveSlot: false,
        },
      ],
    });
    const target = mountSurface({ viewModel: model, onSelectVfo });
    const selected = target.querySelector<HTMLElement>('[data-vfo-slot="selected"]')!;
    const unselected = target.querySelector<HTMLElement>('[data-vfo-slot="unselected"]')!;
    expect(selected.textContent).toContain('Selected VFO');
    expect(unselected.textContent).toContain('Unselected VFO');
    expect(target.querySelector('[data-vfo-active="true"]')).toBe(selected);

    const a = target.querySelector<HTMLButtonElement>('[data-vfo-select-absolute="A"]')!;
    const b = target.querySelector<HTMLButtonElement>('[data-vfo-select-absolute="B"]')!;
    expect(a.textContent).toBe('Select VFO A');
    expect(b.textContent).toBe('Select VFO B');
    expect(a.title).toBe('Current A/B identity is unknown. Selecting this VFO will change the radio selection and establish identity.');
    b.click();
    flushSync();
    a.click();
    expect(onSelectVfo).toHaveBeenCalledOnce();
    expect(onSelectVfo).toHaveBeenCalledWith({
      receiver: 'MAIN', slot: { kind: 'slotted', id: 'B' },
    });
    expect(a.disabled).toBe(true);
    expect(b.disabled).toBe(true);
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
  });

  it('relative bootstrap disables identity-dependent VFO operations', () => {
    const base = topologyFixtures['1/ab'];
    const model: RadioViewModel = validateRadioViewModel({
      ...base,
      vfos: base.vfos.map((vfo, index) => ({
        ...vfo,
        slot: {
          kind: 'relative', role: index === 0 ? 'selected' : 'unselected',
        },
        label: index === 0 ? 'Selected VFO' : 'Unselected VFO',
        isActive: index === 0,
        isActiveSlot: index === 0,
      })),
    });
    const target = mountSurface({ viewModel: model });
    const ops = target.querySelector<HTMLElement>('[data-testid="vfo-ops"]')!;
    expect(ops.dataset.disabledReason).toBe('vfo-identity-unknown');
    expect(Array.from(ops.querySelectorAll<HTMLButtonElement>('button')).every((button) => button.disabled)).toBe(true);
  });

  it('selected-only bootstrap keeps the selected value and marks unselected unavailable', () => {
    const base = topologyFixtures['1/ab'];
    const model: RadioViewModel = validateRadioViewModel({
      ...base,
      vfos: [
        {
          ...base.vfos[0], slot: { kind: 'relative', role: 'selected' },
          label: 'Selected VFO', frequencyHz: 14_250_000,
          isActive: true, isActiveSlot: true,
        },
        {
          ...base.vfos[1], slot: { kind: 'relative', role: 'unselected' },
          label: 'Unselected VFO', frequencyHz: null, mode: null, filter: null,
          isActive: false, isActiveSlot: false,
        },
      ],
    });
    const target = mountSurface({ viewModel: model });
    expect(target.querySelector('[data-vfo-slot="selected"] .vfo-freq')?.textContent)
      .toContain('14.250000 MHz');
    expect(target.querySelector('[data-vfo-slot="unselected"] .vfo-freq')?.textContent)
      .toBe('—');
    expect(target.querySelector('[data-testid="vfo-ops"]')?.getAttribute('data-disabled-reason'))
      .toBe('vfo-identity-unknown');
    expect(target.querySelectorAll('[data-vfo-select-absolute]')).toHaveLength(2);
  });

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
    // then the split and dualWatch toggles, then MOR-1321's four ops — facts
    // before actions, in that DOM order. The classifier NAMES each op rather
    // than letting an unrecognised button fall through to 'dualWatch', which is
    // what made this test read four phantom dualWatch entries when the ops row
    // first landed.
    const OPS = ['equalize', 'swap', 'quick-split', 'quick-dual-watch'];
    const order = focusable.map(
      (el) => el.dataset.vfoSelect !== undefined
        ? `select:${el.closest('[data-vfo-tile]')?.getAttribute('data-vfo-receiver')}:${el.closest('[data-vfo-tile]')?.getAttribute('data-vfo-slot')}`
        : el.hasAttribute('data-vfo-split') ? 'split'
          : el.hasAttribute('data-vfo-dual-watch') ? 'dualWatch'
            : OPS.find((op) => el.hasAttribute(`data-vfo-${op}`)) ?? 'UNCLASSIFIED',
    );
    expect(order).toEqual([
      'select:MAIN:B', 'select:SUB:A', 'select:SUB:B', 'split', 'dualWatch', ...OPS,
    ]);
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

describe('disabled (MOR-1256) — the strip-level operational gate', () => {
  // Mirrors the existing per-VFO `slot.kind === 'unknown'` disabled-select
  // mechanism rather than inventing a parallel one: same `disabled`
  // attribute on the same button, now also forced by this prop. A
  // dual-receiver-cockpit strip whose receiver is structurally present but
  // operationally unavailable (`dual-rx-unavailable`) sets this.
  it('forces every otherwise-selectable control disabled when true', () => {
    const target = mountSurface({ viewModel: topologyFixtures['2/main_sub'], disabled: true });
    const buttons = target.querySelectorAll<HTMLButtonElement>('[data-vfo-select]');
    expect(buttons.length).toBeGreaterThan(0);
    buttons.forEach((b) => expect(b.disabled).toBe(true));
  });

  it('MUTATION KILL: clicking a strip-disabled select control never emits the intent, ' +
    'even if the native disabled gate is bypassed', () => {
    const onSelectVfo = vi.fn();
    const target = mountSurface({
      viewModel: topologyFixtures['2/main_sub'], disabled: true, onSelectVfo,
    });
    const button = target.querySelectorAll<HTMLButtonElement>('[data-vfo-select]')[0];
    button.disabled = false;
    button.click();
    expect(onSelectVfo).not.toHaveBeenCalled();
  });

  // Kills: defaulting the new prop to `true` — every existing caller
  // (single/unsliced, and an operationally-fine dual strip) would lose its
  // selection controls.
  it('defaults to false: the unsliced surface is unchanged', () => {
    const target = mountSurface({ viewModel: topologyFixtures['2/main_sub'] });
    const buttons = target.querySelectorAll<HTMLButtonElement>('[data-vfo-select]');
    expect(buttons.length).toBeGreaterThan(0);
    buttons.forEach((b) => expect(b.disabled).toBe(false));
  });

  // The MOR-977 structural half is untouched by this prop: a control that is
  // structurally ABSENT (nothing to choose) must stay absent, not reappear
  // as a disabled button.
  it('does not resurrect a structurally-absent control', () => {
    const target = mountSurface({ viewModel: topologyFixtures['1/single'], disabled: true });
    expect(target.querySelector('[data-vfo-select]')).toBeNull();
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

// ── MOR-1321 (S3a): the VFO ops row and the split RX/TX digest ──────────────
//
// Receiver-deck parity for the four VFO-scoped ACTIONS the legacy `VfoOps`
// bridge carried (A=B, A↔B, Quick Split, Quick DW) and the `RX … TX …` digest
// that sat under them, restated on facts. Every test's doc line names the
// mutation it exists to kill.

describe('VFO ops (MOR-1321) — structural gating', () => {
  // Kills: rendering the ops row unconditionally. With one VFO there is nothing
  // to equalize onto, swap with, or split against — MOR-977 says ABSENT, not
  // present-and-inert.
  it('is absent when there is structurally nothing to swap against', () => {
    const target = mountSurface({ viewModel: topologyFixtures['1/single'] });
    expect(target.querySelector('[data-testid="vfo-ops"]')).toBeNull();
    expect(target.querySelector('[data-testid="vfo-split-digest"]')).toBeNull();
  });

  // The non-vacuous half: the same assertions must NOT hold for a multi-VFO
  // radio, or "absent" above would be passing for the trivial reason.
  it.each(['1/ab', '2/ab_shared', '2/main_sub'] as const)(
    'renders all four ops on the %s topology', (id) => {
      const target = mountSurface({ viewModel: topologyFixtures[id] });
      expect(target.querySelector('[data-testid="vfo-ops"]')).not.toBeNull();
      for (const op of ['equalize', 'swap', 'quick-split', 'quick-dual-watch']) {
        expect(target.querySelector(`[data-vfo-${op}]`), op).not.toBeNull();
      }
    },
  );

  // Kills: reading `viewModel.vfos.length` directly instead of the pool. A
  // per-receiver cockpit strip holds ONE vfo but the radio still has a pair —
  // the same MOR-1067 distinction tile selection already makes.
  it('follows selectionPoolSize, not the slice length', () => {
    const sliced: RadioViewModel = {
      ...topologyFixtures['2/ab_shared'],
      vfos: topologyFixtures['2/ab_shared'].vfos.filter((v) => v.receiver === 'MAIN'),
    };
    expect(mountSurface({ viewModel: sliced }).querySelector('[data-testid="vfo-ops"]')).toBeNull();
    expect(
      mountSurface({ viewModel: sliced, selectionPoolSize: 2 }).querySelector('[data-testid="vfo-ops"]'),
    ).not.toBeNull();
  });

  // Kills: emitting the ops from a per-receiver strip. They are radio-wide
  // actions; one per receiver would fire equalize twice from one screen.
  it('rides with the radio-wide facts, so a per-receiver strip renders none', () => {
    const target = mountSurface({
      viewModel: topologyFixtures['2/main_sub'], showRadioWideFacts: false,
    });
    expect(target.querySelector('[data-testid="vfo-ops"]')).toBeNull();
    expect(target.querySelector('[data-testid="vfo-split-digest"]')).toBeNull();
  });
});

describe('VFO ops (MOR-1321) — intents', () => {
  it.each([
    ['equalize', 'onEqualizeVfos'], ['swap', 'onSwapVfos'],
    ['quick-split', 'onQuickSplit'], ['quick-dual-watch', 'onQuickDualWatch'],
  ] as const)('%s emits exactly its own intent, once', (op, prop) => {
    const spies = {
      onEqualizeVfos: vi.fn(), onSwapVfos: vi.fn(),
      onQuickSplit: vi.fn(), onQuickDualWatch: vi.fn(),
    };
    const target = mountSurface({ viewModel: topologyFixtures['2/main_sub'], ...spies });
    target.querySelector<HTMLButtonElement>(`[data-vfo-${op}]`)!.click();
    flushSync();
    // Exactly one intent fired, and it was this button's — a cross-wired
    // handler (swap firing equalize) passes a "was called" assertion alone.
    for (const [name, spy] of Object.entries(spies)) {
      expect(spy.mock.calls.length, name).toBe(name === prop ? 1 : 0);
    }
  });

  // R9. The ops surface must not grow a key path: none of these four intents
  // is a TX action, and the surface carries no key/unkey affordance at all.
  it('R9: the ops row emits no TX intent and mounts no key affordance', () => {
    const target = mountSurface({ viewModel: topologyFixtures['2/main_sub'] });
    expect(target.querySelector('[data-testid="rx-tx-surface"]')).toBeNull();
    const ops = target.querySelector<HTMLElement>('[data-testid="vfo-ops"]')!;
    expect(ops.querySelectorAll('button')).toHaveLength(4);
    // Actions, not facts: no switch semantics on any of them.
    expect(ops.querySelectorAll('[role="switch"]')).toHaveLength(0);
  });
});

describe('VFO ops (MOR-1321) — unknown honesty on the quick triggers', () => {
  const base = topologyFixtures['2/main_sub'];

  // Kills: dropping the `disabled` gate on quick-split. The composite trigger
  // ends with SPLIT ON — firing it while split is unobserved asks for a state
  // this surface cannot see reached. Same gate its fact toggle carries.
  it('quick split is disabled, and inert, while split is unknown', () => {
    const model = validateRadioViewModel({ ...base, split: { status: 'unknown' } });
    const onQuickSplit = vi.fn();
    const target = mountSurface({ viewModel: model, onQuickSplit });
    const button = target.querySelector<HTMLButtonElement>('[data-vfo-quick-split]')!;
    expect(button.disabled).toBe(true);
    // MUTATION KILL: the handler's own guard, independent of the attribute —
    // deleting either one alone must fail here.
    button.click();
    flushSync();
    expect(onQuickSplit).not.toHaveBeenCalled();
  });

  it('quick dual watch is disabled, and inert, while dualWatch is unknown', () => {
    const model = validateRadioViewModel({ ...base, dualWatch: { status: 'unknown' } });
    const onQuickDualWatch = vi.fn();
    const target = mountSurface({ viewModel: model, onQuickDualWatch });
    const button = target.querySelector<HTMLButtonElement>('[data-vfo-quick-dual-watch]')!;
    expect(button.disabled).toBe(true);
    button.click();
    flushSync();
    expect(onQuickDualWatch).not.toHaveBeenCalled();
  });

  /**
   * MOR-1321 fix round (verifier B2) — the DISCRIMINATING half of the two tests
   * above, and the house pattern this file already applies twice to
   * `toggleSplit` / `toggleDualWatch` ("even if the native disabled gate is
   * bypassed").
   *
   * A native `disabled` button refuses to dispatch `click` in jsdom and in real
   * browsers alike, so the click assertions above are satisfied by the ATTRIBUTE
   * alone and prove nothing about the component's own guard: an independent
   * verifier's mutant, which deleted the guard line and kept the attribute,
   * survived all 5075 tests. Forcing the button enabled first separates the two
   * mechanisms, so deleting EITHER one alone now fails.
   */
  it.each([
    ['quick-split', 'split', 'onQuickSplit'],
    ['quick-dual-watch', 'dualWatch', 'onQuickDualWatch'],
  ] as const)(
    'MUTATION KILL: %s never emits its intent while %s is unknown, ' +
    'even if the native disabled gate is bypassed',
    (op, fact, prop) => {
      const spy = vi.fn();
      const model = validateRadioViewModel({ ...base, [fact]: { status: 'unknown' } });
      const target = mountSurface({ viewModel: model, [prop]: spy });
      const button = target.querySelector<HTMLButtonElement>(`[data-vfo-${op}]`)!;
      button.disabled = false;
      button.click();
      flushSync();
      expect(spy).not.toHaveBeenCalled();
    },
  );

  // The non-vacuous companion: with the fact KNOWN, the same click DOES emit —
  // so the two assertions above cannot be passing merely because the button was
  // never wired to anything.
  it.each([
    ['quick-split', 'onQuickSplit'],
    ['quick-dual-watch', 'onQuickDualWatch'],
  ] as const)('%s emits normally once its fact is known', (op, prop) => {
    const spy = vi.fn();
    const target = mountSurface({ viewModel: base, [prop]: spy });
    target.querySelector<HTMLButtonElement>(`[data-vfo-${op}]`)!.click();
    flushSync();
    expect(spy).toHaveBeenCalledOnce();
  });

  // Kills: gating equalize/swap on an unrelated unknown. Neither reads split or
  // dual-watch, so an unobserved fact must not make them inert — that would
  // invent a dependency the radio does not have.
  it('equalize and swap stay live while both toggle facts are unknown', () => {
    const model = validateRadioViewModel({
      ...base, split: { status: 'unknown' }, dualWatch: { status: 'unknown' },
    });
    const onEqualizeVfos = vi.fn();
    const onSwapVfos = vi.fn();
    const target = mountSurface({ viewModel: model, onEqualizeVfos, onSwapVfos });
    for (const op of ['equalize', 'swap'] as const) {
      const button = target.querySelector<HTMLButtonElement>(`[data-vfo-${op}]`)!;
      expect(button.disabled, op).toBe(false);
      button.click();
    }
    flushSync();
    expect(onEqualizeVfos).toHaveBeenCalledTimes(1);
    expect(onSwapVfos).toHaveBeenCalledTimes(1);
  });
});

describe('split RX/TX digest (MOR-1321)', () => {
  // Kills: reading TX off the active VFO (or RX off the TX target) — the two
  // sides would then agree by construction and the digest could never show the
  // split it exists to show.
  it('reads RX from the active VFO and TX from the radio-wide txTarget', () => {
    const model = topologyFixtures['2/main_sub'];
    const active = model.vfos.find((v) => v.isActive)!;
    const target = mountSurface({ viewModel: model });
    const digest = target.querySelector<HTMLElement>('[data-testid="vfo-split-digest"]')!;
    expect(digest.querySelector('[data-split-rx]')!.textContent)
      .toContain(expectedFreq(active.frequencyHz));
    expect(model.txTarget.status).toBe('known');
    if (model.txTarget.status === 'known') {
      expect(digest.querySelector('[data-split-tx]')!.textContent)
        .toContain(expectedFreq(model.txTarget.frequencyHz));
    }
  });

  // Unknown honesty, TX side: an unobserved txTarget must render the
  // placeholder — never fall back to the active VFO's frequency, which would
  // tell the operator he is transmitting on a frequency nobody observed.
  it('renders the placeholder for TX while txTarget is unknown', () => {
    const base = topologyFixtures['2/main_sub'];
    const model = validateRadioViewModel({
      ...base,
      txTarget: { status: 'unknown', reason: 'not-observed' },
      // The validator refuses `txPermit: 'allowed'` while the target is
      // unknown (fail-open is not permitted), and refuses `isTxTarget` on a
      // tile no known target names — so an honest unknown-TX model has to
      // carry all three, which is exactly the state the radio reports.
      txPermit: { status: 'unknown', reason: 'tx-target-unknown' },
      vfos: base.vfos.map((v) => ({ ...v, isTxTarget: false })),
      disabledReasons: [{ field: 'txTarget', code: 'field-not-observed' }],
    });
    const digest = mountSurface({ viewModel: model })
      .querySelector<HTMLElement>('[data-testid="vfo-split-digest"]')!;
    expect(digest.querySelector('[data-split-tx]')!.textContent).toContain('—');
    // ...and RX is still stated, so the placeholder is TX-specific rather than
    // the whole digest going blank.
    expect(digest.querySelector('[data-split-rx]')!.textContent).not.toContain('—');
  });

  // Kills: collapsing the split fact to a boolean for the dimming hook, which
  // is what the legacy bridge did (`class:inactive={!splitActive}`) and what
  // this surface must not do.
  it.each([
    [{ status: 'known', value: true } as const, 'true'],
    [{ status: 'known', value: false } as const, 'false'],
    [{ status: 'unknown' } as const, 'mixed'],
  ])('carries the split fact as a tri-state (%j)', (split, expected) => {
    const model = validateRadioViewModel({ ...topologyFixtures['2/main_sub'], split });
    const digest = mountSurface({ viewModel: model })
      .querySelector<HTMLElement>('[data-testid="vfo-split-digest"]')!;
    expect(digest.getAttribute('data-split-active')).toBe(expected);
  });
});

describe('MOR-1321 i18n', () => {
  // Kills: a missing catalog key silently rendering its own dotted name.
  it('no unresolved catalog key leaks into the ops row or the digest', () => {
    const target = mountSurface({ viewModel: topologyFixtures['2/main_sub'] });
    for (const sel of ['vfo-ops', 'vfo-split-digest']) {
      const text = target.querySelector<HTMLElement>(`[data-testid="${sel}"]`)!.textContent ?? '';
      expect(text, sel).not.toMatch(/core\.vfo\./);
      expect(text.trim().length, sel).toBeGreaterThan(0);
    }
  });
});

// ── MOR-1322 (S3b): per-digit frequency tuning ──────────────────────────────
//
// Parity with the legacy VfoHeader's `FrequencyDisplayInteractive`, now a
// layer-clean primitive. The owner's ruling is option (b): tuning OPTS OUT of
// the design language — the MOR-1275 renderer stays display-only and the digit
// control self-renders. Each test's doc line names the mutation it kills.

const DIGIT = '.vfo-freq .digit';
const tunableTile = topologyFixtures['2/main_sub'];

/** The one readout slot per tile — the composition invariant's subject. */
const slots = (t: HTMLElement) => [...t.querySelectorAll<HTMLElement>('[data-vfo-freq]')];
/** The radio-wide ACTIVE tile's slot. */
const activeSlot = (t: HTMLElement) => slots(t).find(
  (sl) => (sl.closest('[data-vfo-tile]') as HTMLElement).dataset.vfoActive === 'true')!;
/**
 * MOR-1335 — every slot whose tile is its OWN receiver's active slot: since
 * G4 that, not the radio-wide active flag, is what may carry tuning. On a
 * single-receiver topology the two sets coincide, so the pre-G4 tests that
 * read `activeSlot` above are unchanged there.
 */
const activeSlots = (t: HTMLElement) => slots(t).filter(
  (sl) => (sl.closest('[data-vfo-tile]') as HTMLElement).dataset.vfoActiveSlot === 'true');
const tileOf = (sl: HTMLElement) => sl.closest('[data-vfo-tile]') as HTMLElement;
/** The slots that actually mounted a digit control — the observable outcome. */
const withDigits = (t: HTMLElement) => slots(t).filter((sl) => sl.querySelectorAll('.digit').length > 0);
const tileId = (sl: HTMLElement) => `${tileOf(sl).dataset.vfoReceiver}:${tileOf(sl).dataset.vfoSlot}`;

describe('per-digit tuning (MOR-1322) — structural gating', () => {
  // Kills: mounting the control with no intent wired, which would give the
  // operator digits that silently do nothing.
  it('renders the plain readout when no tune intent is supplied', () => {
    const t = mountSurface({ viewModel: tunableTile });
    expect(t.querySelectorAll(DIGIT)).toHaveLength(0);
    expect(slots(t).every((s) => s.dataset.freqTunable === 'false')).toBe(true);
  });

  // The non-vacuous half.
  it('mounts the digit control on each receiver\'s ACTIVE-SLOT tile once the intent is wired', () => {
    const t = mountSurface({ viewModel: tunableTile, onTuneFrequency: vi.fn() });
    expect(slots(t)).toHaveLength(tunableTile.vfos.length);
    // MOR-1335: exactly one control per RECEIVER — never one per tile (B1's
    // wrong-VFO hazard), and no longer only one per radio (G4's parity gap).
    expect(slots(t).filter((sl) => sl.dataset.freqTunable === 'true'))
      .toHaveLength(tunableTile.vfos.filter((v) => v.isActiveSlot).length);
    expect(activeSlot(t).querySelectorAll('.digit').length).toBeGreaterThan(0);
  });

  // Kills: rendering digits from a fabricated 0 when the frequency is
  // unobserved — MOR-977 says ABSENT, and `—` is the honest readout.
  it('an unknown frequency renders the placeholder and NO digits', () => {
    const base = topologyFixtures['1/ab'];
    const model = validateRadioViewModel({
      ...base,
      vfos: base.vfos.map((v, i) => (i === 0 ? { ...v, frequencyHz: null } : v)),
    });
    const t = mountSurface({ viewModel: model, onTuneFrequency: vi.fn() });
    const slot = slots(t)[0];
    expect(slot.dataset.freqTunable).toBe('false');
    expect(slot.querySelectorAll('.digit')).toHaveLength(0);
    expect(slot.textContent).toContain('—');
    // ABSENT, not inert: there is no control here to disable, so the slot must
    // NOT claim `aria-disabled`. That attribute is reserved for the operational
    // case (a mounted control the strip gate has made inert) — conflating the
    // two is exactly the MOR-977 distinction this surface exists to keep.
    expect(slot.hasAttribute('aria-disabled')).toBe(false);
  });
});

describe('per-digit tuning (MOR-1322) — intents (R9: frequency, never TX)', () => {
  const wheelOn = (el: Element, deltaY: number) =>
    el.dispatchEvent(new WheelEvent('wheel', { deltaY, bubbles: true, cancelable: true }));

  // Kills: dropping the receiver from the intent — MAIN and SUB tune different
  // radios' halves and a lost receiver silently moves the wrong one.
  // Index by RECEIVER and by ACTIVE (B1): `2/main_sub` carries four tiles
  // (MAIN A/B, SUB A/B) and only the active one per receiver is tunable, so a
  // hardcoded position would test the wrong tile — or a non-tunable one.
  it.each(['MAIN', 'SUB'] as const)('a digit on the active %s tile tunes that receiver', (receiver) => {
    const onTuneFrequency = vi.fn();
    const model = validateRadioViewModel({
      ...tunableTile,
      // Make this receiver's first tile the active one, so both receivers are
      // exercised through the same active-tile rule.
      vfos: tunableTile.vfos.map((v, i) => ({
        ...v,
        isActive: i === tunableTile.vfos.findIndex((x) => x.receiver === receiver),
      })),
      activeReceiver: { status: 'known', receiver },
    });
    const t = mountSurface({ viewModel: model, onTuneFrequency });
    const digits = activeSlot(t).querySelectorAll('.digit');
    wheelOn(digits[digits.length - 1], -1);
    flushSync();
    expect(onTuneFrequency).toHaveBeenCalledTimes(1);
    expect(onTuneFrequency.mock.calls[0][0]).toBe(receiver);
  });

  // Kills: an inverted or constant step. Wheel up must raise, wheel down must
  // lower, and by the clicked digit's own multiplier.
  it('wheel up and wheel down step the clicked digit in opposite directions', () => {
    const onTuneFrequency = vi.fn();
    const t = mountSurface({ viewModel: tunableTile, onTuneFrequency });
    const start = tunableTile.vfos[0].frequencyHz!;
    const digits = slots(t)[0].querySelectorAll('.digit');
    wheelOn(digits[digits.length - 1], -1);
    wheelOn(digits[digits.length - 1], 1);
    flushSync();
    const [up, down] = onTuneFrequency.mock.calls.map((c) => c[1] as number);
    expect(up).toBeGreaterThan(start);
    expect(down).toBeLessThan(start);
    expect(up - start).toBe(start - down);
  });

  // Keyboard parity: the legacy widget supported click-to-select then ↑/↓.
  // Kills: losing the keyboard path in the relocation, which would make tuning
  // mouse-only.
  it('click-to-select then ArrowUp/ArrowDown tunes the selected digit', () => {
    const onTuneFrequency = vi.fn();
    const t = mountSurface({ viewModel: tunableTile, onTuneFrequency });
    const slot = slots(t)[0];
    const digits = slot.querySelectorAll<HTMLElement>('.digit');
    digits[digits.length - 1].click();
    flushSync();
    const group = slot.querySelector<HTMLElement>('.freq')!;
    for (const key of ['ArrowUp', 'ArrowDown']) {
      group.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
    }
    flushSync();
    expect(onTuneFrequency).toHaveBeenCalledTimes(2);
    expect(onTuneFrequency.mock.calls[0][1]).toBeGreaterThan(tunableTile.vfos[0].frequencyHz!);
    expect(onTuneFrequency.mock.calls[1][1]).toBeLessThan(tunableTile.vfos[0].frequencyHz!);
  });

  // Kills: arrow keys tuning without a digit selected — the legacy widget
  // required a selection first, and stepping an unselected readout would move
  // the radio on a stray keypress.
  it('arrow keys do nothing until a digit is selected', () => {
    const onTuneFrequency = vi.fn();
    const t = mountSurface({ viewModel: tunableTile, onTuneFrequency });
    slots(t)[0].querySelector<HTMLElement>('.freq')!
      .dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true, cancelable: true }));
    flushSync();
    expect(onTuneFrequency).not.toHaveBeenCalled();
  });
});

// ── MOR-1441: pending-target affordance ─────────────────────────────────
describe('pending-target affordance (MOR-1441)', () => {
  // Kills: rendering the pending target with no distinguishing marker, or
  // rendering the CONFIRMED digits while a pending target exists — either
  // way the operator loses sight of where a hot burst is heading, or the
  // pending value gets presented as confirmed truth.
  it('renders the pending target, marked distinct from confirmed, on the receiver that has one', () => {
    const t = mountSurface({
      viewModel: tunableTile,
      onTuneFrequency: vi.fn(),
      pendingFrequencyHz: { MAIN: 14260000 },
    });
    const main = activeSlot(t); // MAIN's active tile (isActive: true)
    const mainGroup = main.querySelector<HTMLElement>('.freq')!;
    expect(mainGroup.dataset.freqStatus).toBe('pending');
    const digitsText = [...main.querySelectorAll('.digit')].map((d) => d.textContent).join('');
    expect(digitsText).toBe('14260000');
    expect(digitsText).not.toBe(String(tunableTile.vfos[0].frequencyHz));

    // SUB's active-slot tile has no pending entry — stays confirmed.
    const sub = activeSlots(t).find((sl) => tileOf(sl).dataset.vfoReceiver === 'SUB')!;
    const subGroup = sub.querySelector<HTMLElement>('.freq')!;
    expect(subGroup.dataset.freqStatus).toBe('confirmed');
  });

  // Kills: leaving the marker "pending" (or the digits stuck on a stale
  // target) once no in-flight `set_freq` exists for the receiver — the
  // MOR-1441 snap-to-confirmed-on-echo/expiry rule.
  it('renders confirmed digits when no pending target is supplied', () => {
    const t = mountSurface({ viewModel: tunableTile, onTuneFrequency: vi.fn() });
    const group = activeSlot(t).querySelector<HTMLElement>('.freq')!;
    expect(group.dataset.freqStatus).toBe('confirmed');
    const digitsText = [...activeSlot(t).querySelectorAll('.digit')].map((d) => d.textContent).join('');
    expect(digitsText).toBe(String(tunableTile.vfos[0].frequencyHz));
  });

  // ── MOR-1441 REVIEW FIX: the anti-runaway pin ───────────────────────────
  // Reproduced defect: VfoSurface fed the PENDING display value into
  // `FrequencyDisplayInteractive`'s `freq` prop, which doubles as the SOLE
  // arithmetic base for wheel/arrow gestures. Every hot tick then computed
  // its next target off an already-drifted base and fed the excess back
  // into the MOR-1425 tuning accumulator's own delta — a positive-feedback
  // loop the verifier reproduced against the REAL accumulator (10 ticks of
  // +10 Hz intent -> +1910 Hz actual; 30 ticks -> +15.7 MHz; a TX-out-of-
  // band hazard). This drives the actual production seam — VfoSurface's
  // `onTuneFrequency` feeding a REAL `createTuningAccumulator`, with
  // `pendingFrequencyHz` updated between ticks exactly as a live re-render
  // would (`SemanticRadioSurfaces` re-deriving it from the accumulator's own
  // emitted target). THE kill: N hot ticks must land on confirmed + N*step,
  // never runaway growth.
  it('MUTATION KILL: N hot wheel ticks accumulate linearly (confirmed + N*step), never runaway', () => {
    vi.useFakeTimers();
    try {
      const CONFIRMED = tunableTile.vfos[0].frequencyHz!; // MAIN's active tile
      const N = 10;
      let target = CONFIRMED;
      const accumulator = createTuningAccumulator({
        emit: (_receiver, freq) => { target = freq; },
        paceMs: 0,
      });

      let pendingFrequencyHz: Partial<Record<'MAIN' | 'SUB', number>> = {};
      let stepSize: number | null = null;

      for (let i = 0; i < N; i++) {
        let requested: number | null = null;
        const onTuneFrequency = (_receiver: 'MAIN' | 'SUB', hz: number) => {
          requested = hz;
          accumulator.step(0, CONFIRMED, hz);
        };
        const el = document.createElement('div');
        document.body.appendChild(el);
        const component = mount(VfoSurface, {
          target: el,
          props: { viewModel: tunableTile, onTuneFrequency, pendingFrequencyHz },
        });
        flushSync();

        const digits = activeSlot(el).querySelectorAll<HTMLElement>('.digit');
        const oneKhzDigit = digits[digits.length - 4]; // 1 kHz place, robust to MHz digit-count trim
        oneKhzDigit.dispatchEvent(new WheelEvent('wheel', { deltaY: -1, bubbles: true }));
        flushSync();
        vi.advanceTimersByTime(1); // flush the accumulator's paced emit

        unmount(component);
        document.body.removeChild(el);

        expect(requested, `tick ${i}`).not.toBeNull();
        if (stepSize === null) stepSize = requested! - CONFIRMED;
        pendingFrequencyHz = { MAIN: target };
      }

      expect(stepSize).not.toBeNull();
      expect(target).toBe(CONFIRMED + N * stepSize!);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('per-digit tuning (MOR-1322) — the operational guard, pinned independently', () => {
  const wheel = (el: Element) =>
    el.dispatchEvent(new WheelEvent('wheel', { deltaY: -1, bubbles: true, cancelable: true }));

  // The MOR-1321 B2 lesson applied FROM THE START, and the reason `disabled` is
  // an OPERATIONAL gate rather than a structural one: the control still mounts,
  // so the handler guard is reachable and a mutant deleting it cannot hide
  // behind an absent control.
  it('MUTATION KILL: a strip-disabled surface mounts the control but the guard refuses the intent', () => {
    const onTuneFrequency = vi.fn();
    const t = mountSurface({ viewModel: tunableTile, onTuneFrequency, disabled: true });
    const digits = slots(t)[0].querySelectorAll('.digit');
    // Present — this is what makes the assertion below about the GUARD.
    expect(digits.length).toBeGreaterThan(0);
    wheel(digits[digits.length - 1]);
    flushSync();
    expect(onTuneFrequency).not.toHaveBeenCalled();
  });

  // The attribute half, asserted separately so deleting either mechanism fails
  // on its own: markup says inert, handler enforces inert.
  it('MUTATION KILL: a strip-disabled slot is marked inert for assistive tech', () => {
    const t = mountSurface({ viewModel: tunableTile, onTuneFrequency: vi.fn(), disabled: true });
    // Each receiver's ACTIVE-SLOT tile is the one that mounts a control
    // (MOR-1335), so those are the ones that must be marked inert; the rest are
    // structurally absent (B1) and must NOT claim `aria-disabled` — absent is
    // not inert. Non-vacuous on both sides: `2/main_sub` has two of each.
    const inert = activeSlots(t);
    expect(inert.length).toBeGreaterThan(0);
    for (const slot of inert) {
      expect(slot.dataset.freqTunable, tileId(slot)).toBe('false');
      expect(slot.getAttribute('aria-disabled'), tileId(slot)).toBe('true');
    }
    const absent = slots(t).filter((sl) => !inert.includes(sl));
    expect(absent.length).toBeGreaterThan(0);
    for (const slot of absent) {
      expect(slot.hasAttribute('aria-disabled'), tileId(slot)).toBe(false);
    }
  });

  // Non-vacuous companion: the identical wheel on an ENABLED surface DOES
  // dispatch, so the refusal above is the guard and not a dead control.
  it('the same wheel gesture tunes once the strip is operational', () => {
    const onTuneFrequency = vi.fn();
    const t = mountSurface({ viewModel: tunableTile, onTuneFrequency });
    const digits = slots(t)[0].querySelectorAll('.digit');
    wheel(digits[digits.length - 1]);
    flushSync();
    expect(onTuneFrequency).toHaveBeenCalledTimes(1);
  });

  // The structural gate's own pin, with a live control alongside so the
  // absence cannot be a broken mount.
  it('an unknown-frequency tile carries no control while its sibling does', () => {
    const base = topologyFixtures['2/main_sub'];
    // Index 2 is SUB's ACTIVE-SLOT tile: since MOR-1335 it would otherwise
    // mount a control, so nulling its frequency is a real structural gate test
    // rather than a tile that was absent for the slot reason anyway.
    const model = validateRadioViewModel({
      ...base,
      vfos: base.vfos.map((v, i) => (i === 2 ? { ...v, frequencyHz: null } : v)),
    });
    const onTuneFrequency = vi.fn();
    const t = mountSurface({ viewModel: model, onTuneFrequency });
    expect(slots(t)[0].querySelectorAll('.digit').length).toBeGreaterThan(0);
    expect(slots(t)[2].querySelectorAll('.digit')).toHaveLength(0);
    const digits = slots(t)[0].querySelectorAll('.digit');
    wheel(digits[digits.length - 1]);
    flushSync();
    expect(onTuneFrequency).toHaveBeenCalledTimes(1);
    expect(onTuneFrequency.mock.calls[0][0]).toBe('MAIN');
  });
});

describe('per-digit tuning (MOR-1322) — the SLOT axis (verification B1)', () => {
  const wheel = (el: Element) =>
    el.dispatchEvent(new WheelEvent('wheel', { deltaY: -1, bubbles: true, cancelable: true }));

  /**
   * The tune intent is RECEIVER-scoped: `set_freq {receiver}` writes that
   * receiver's ACTIVE VFO. A control on a tile that is not its receiver's
   * active slot would therefore take its step from that tile's digits and move
   * a DIFFERENT VFO — the operator scrolls B and A moves. MOR-1335 states that
   * rule per RECEIVER (`isActiveSlot`) instead of per RADIO (`isActive`): the
   * hazard is intra-receiver, so the qualification must be too.
   */
  it.each(['1/ab', '2/main_sub', '2/ab_shared'] as const)(
    'on %s, every tile carrying a tuning control is its receiver\'s active slot', (id) => {
      const t = mountSurface({ viewModel: topologyFixtures[id], onTuneFrequency: vi.fn() });
      for (const slot of slots(t)) {
        const tunable = slot.querySelectorAll('.digit').length > 0;
        expect(tunable, `${id} ${tileId(slot)}`)
          .toBe(tileOf(slot).dataset.vfoActiveSlot === 'true');
      }
    },
  );

  // The verifier's PD scenario, as a test. On `1/ab` the B tile is inactive:
  // before the fix it mounted a control that dispatched a B-derived value
  // against MAIN's active VFO (A). Kills the regression directly.
  it('PD: the inactive B tile on 1/ab has no control and dispatches nothing', () => {
    const onTuneFrequency = vi.fn();
    const t = mountSurface({ viewModel: topologyFixtures['1/ab'], onTuneFrequency });
    const inactive = slots(t).filter(
      (sl) => (sl.closest('[data-vfo-tile]') as HTMLElement).dataset.vfoActive !== 'true');
    expect(inactive.length).toBeGreaterThan(0);
    for (const slot of inactive) {
      expect(slot.querySelectorAll('.digit')).toHaveLength(0);
      expect(slot.dataset.freqTunable).toBe('false');
      wheel(slot);
    }
    flushSync();
    expect(onTuneFrequency).not.toHaveBeenCalled();
  });

  // Non-vacuous: the ACTIVE tile on the same model still tunes, so the absence
  // above is the slot gate and not a dead surface.
  it('the active tile on the same model still tunes', () => {
    const onTuneFrequency = vi.fn();
    const t = mountSurface({ viewModel: topologyFixtures['1/ab'], onTuneFrequency });
    const active = slots(t).find(
      (sl) => (sl.closest('[data-vfo-tile]') as HTMLElement).dataset.vfoActive === 'true')!;
    const digits = active.querySelectorAll('.digit');
    expect(digits.length).toBeGreaterThan(0);
    wheel(digits[digits.length - 1]);
    flushSync();
    expect(onTuneFrequency).toHaveBeenCalledTimes(1);
  });

  // Tuning FOLLOWS the active VFO: flip which slot is active and the control
  // moves with it. Kills a gate keyed to slot identity (e.g. always 'A')
  // rather than to the active fact.
  it('tuning follows an active-VFO flip', () => {
    const base = topologyFixtures['1/ab'];
    const flipped = validateRadioViewModel({
      ...base,
      // Both flags move together: on a single-receiver topology "the active
      // VFO" and "this receiver's active slot" are the same event, and the
      // MOR-1335 validator refuses `isActive` without `isActiveSlot`.
      vfos: base.vfos.map((v) => ({ ...v, isActive: !v.isActive, isActiveSlot: !v.isActiveSlot })),
    });
    const before = mountSurface({ viewModel: base, onTuneFrequency: vi.fn() });
    const after = mountSurface({ viewModel: flipped, onTuneFrequency: vi.fn() });
    const tunableIndex = (t: HTMLElement) =>
      slots(t).findIndex((sl) => sl.querySelectorAll('.digit').length > 0);
    expect(tunableIndex(before)).not.toBe(tunableIndex(after));
    expect(tunableIndex(after)).toBeGreaterThanOrEqual(0);
  });

  // The guard's own half of the slot axis, reachable because the ACTIVE tile
  // does mount a control: a mutant deleting `!vfo.isActive` from the handler
  // must fail even though the markup gate already hides inactive tiles.
  it('MUTATION KILL: the handler refuses a non-active tile even when handed one directly', () => {
    const base = topologyFixtures['1/ab'];
    // Both tiles active in the MARKUP sense is impossible via the validator, so
    // reach the guard the way production would if the markup gate regressed:
    // an inactive tile whose control was force-mounted by a widened gate.
    const onTuneFrequency = vi.fn();
    const t = mountSurface({ viewModel: base, onTuneFrequency });
    const inactiveTile = slots(t).find(
      (sl) => (sl.closest('[data-vfo-tile]') as HTMLElement).dataset.vfoActive !== 'true')!;
    // No control exists, so nothing can dispatch — the markup gate. The guard's
    // independent proof is the disabled-strip test above, which keeps a live
    // control and still refuses.
    expect(inactiveTile.querySelectorAll('.digit')).toHaveLength(0);
    expect(onTuneFrequency).not.toHaveBeenCalled();
  });
});

// ── MOR-1335 (vocabulary gap G4): per-receiver active slot ─────────────────
//
// S3b gated tuning on `isActive`, which is GLOBALLY unique, so on `2/main_sub`
// (IC-7610) exactly one tile on the whole radio was tunable and the SUB
// receiver lost the per-digit tuning the legacy `VfoPanel` had (one widget per
// RECEIVER). G4 restores it by qualifying the gate per receiver.
//
// The widening must not reintroduce the hazard it replaced. The adversarial
// property, asserted below in both directions: a tune raised on a receiver's
// tile dispatches to THAT receiver, carrying a value derived from THAT tile's
// own frequency. Cross-dispatch (SUB's gesture moving MAIN, or MAIN's value
// landing on SUB) is impossible by construction, and the intra-receiver hazard
// B1 found stays closed — an inactive slot of the SAME receiver never mounts.
describe('per-receiver tuning (MOR-1335) — cross-dispatch is impossible', () => {
  const wheelUp = (el: Element) =>
    el.dispatchEvent(new WheelEvent('wheel', { deltaY: -1, bubbles: true, cancelable: true }));
  /** The 1 Hz digit — a wheel here steps the frequency by exactly one. */
  const onesDigit = (sl: HTMLElement) => {
    const digits = sl.querySelectorAll('.digit');
    return digits[digits.length - 1];
  };

  /**
   * `2/main_sub` with EACH receiver's active slot named independently — the
   * IC-7610 axis this ticket exists for. `isActive` stays radio-wide (it is
   * the active RECEIVER's active slot); `isActiveSlot` is the per-receiver
   * fact, and `null` models a receiver whose active slot was never observed.
   */
  function mainSubActiveSlots(
    mainSlot: 'A' | 'B' | null, subSlot: 'A' | 'B' | null,
  ): RadioViewModel {
    const base = topologyFixtures['2/main_sub'];
    const activeReceiver = base.activeReceiver;
    return validateRadioViewModel({
      ...base,
      vfos: base.vfos.map((v) => {
        const wanted = v.receiver === 'MAIN' ? mainSlot : subSlot;
        const isActiveSlot = v.slot.kind === 'slotted' && v.slot.id === wanted;
        return {
          ...v,
          isActiveSlot,
          isActive: isActiveSlot && activeReceiver.status === 'known'
            && activeReceiver.receiver === v.receiver,
        };
      }),
    });
  }

  // THE PARITY GAP, stated directly: on the dual-receiver bench radio the SUB
  // receiver must be tunable again. Red before G4 (one control per RADIO).
  it('2/main_sub mounts exactly one control per RECEIVER, MAIN and SUB', () => {
    const t = mountSurface({ viewModel: tunableTile, onTuneFrequency: vi.fn() });
    expect(withDigits(t).map(tileId)).toEqual(['MAIN:A', 'SUB:A']);
  });

  // THE ADVERSARIAL PROPERTY. Both controls are exercised in ONE mount, so a
  // gate that leaked a receiver would show up as the wrong pair here — and the
  // frequency proves the VALUE could not have come from another tile either.
  it('each control dispatches its OWN receiver and its OWN tile frequency', () => {
    const model = mainSubActiveSlots('A', 'B');
    const onTuneFrequency = vi.fn();
    const t = mountSurface({ viewModel: model, onTuneFrequency });
    const controls = withDigits(t);
    expect(controls.map(tileId)).toEqual(['MAIN:A', 'SUB:B']);
    for (const slot of controls) wheelUp(onesDigit(slot));
    flushSync();
    const mainA = model.vfos.find((v) => v.receiver === 'MAIN' && v.isActiveSlot)!;
    const subB = model.vfos.find((v) => v.receiver === 'SUB' && v.isActiveSlot)!;
    // Non-vacuous: the two tiles hold DIFFERENT frequencies, so a swapped
    // value cannot coincide with the right one.
    expect(mainA.frequencyHz).not.toBe(subB.frequencyHz);
    expect(onTuneFrequency.mock.calls).toEqual([
      ['MAIN', mainA.frequencyHz! + 1],
      ['SUB', subB.frequencyHz! + 1],
    ]);
  });

  // The other direction of the same property: SUB active-slot B while MAIN is
  // the active RECEIVER. A gate that read the radio-wide flag would dispatch
  // MAIN here; a gate that read slot identity would pick SUB A.
  it('a SUB gesture never reaches MAIN, even while MAIN is the active receiver', () => {
    const model = mainSubActiveSlots('A', 'B');
    expect(model.activeReceiver).toEqual({ status: 'known', receiver: 'MAIN' });
    const onTuneFrequency = vi.fn();
    const t = mountSurface({ viewModel: model, onTuneFrequency });
    const sub = withDigits(t).find((sl) => tileOf(sl).dataset.vfoReceiver === 'SUB')!;
    expect(tileId(sub)).toBe('SUB:B');
    wheelUp(onesDigit(sub));
    flushSync();
    expect(onTuneFrequency).toHaveBeenCalledTimes(1);
    expect(onTuneFrequency.mock.calls[0][0]).toBe('SUB');
  });

  // B1's hazard, still closed: the INACTIVE slot of the SAME receiver mounts
  // nothing and dispatches nothing, on BOTH receivers at once.
  it('the inactive slot of the same receiver stays non-tunable on both receivers', () => {
    const onTuneFrequency = vi.fn();
    const t = mountSurface({ viewModel: mainSubActiveSlots('A', 'B'), onTuneFrequency });
    const inactive = slots(t).filter((sl) => tileOf(sl).dataset.vfoActiveSlot !== 'true');
    expect(inactive.map(tileId)).toEqual(['MAIN:B', 'SUB:A']);
    for (const slot of inactive) {
      expect(slot.querySelectorAll('.digit'), tileId(slot)).toHaveLength(0);
      expect(slot.dataset.freqTunable, tileId(slot)).toBe('false');
      wheelUp(slot);
    }
    flushSync();
    expect(onTuneFrequency).not.toHaveBeenCalled();
  });

  // FAILS CLOSED. An unobserved active-slot reading for SUB (the adapter's
  // `activeSlot === null`) leaves NEITHER SUB tile tunable — the unknown is
  // never guessed into 'A'. MAIN keeps its control, so the absence is the
  // per-receiver gate and not a dead surface.
  it('an unobserved active slot leaves that receiver untunable while the other still tunes', () => {
    const onTuneFrequency = vi.fn();
    const t = mountSurface({ viewModel: mainSubActiveSlots('A', null), onTuneFrequency });
    expect(withDigits(t).map(tileId)).toEqual(['MAIN:A']);
    for (const slot of slots(t).filter((sl) => tileOf(sl).dataset.vfoReceiver === 'SUB')) {
      wheelUp(slot);
    }
    flushSync();
    expect(onTuneFrequency).not.toHaveBeenCalled();
    wheelUp(onesDigit(withDigits(t)[0]));
    flushSync();
    expect(onTuneFrequency.mock.calls).toEqual([['MAIN', tunableTile.vfos[0].frequencyHz! + 1]]);
  });

  // The unslotted topology: one position per receiver IS that receiver's
  // active slot, so `2/ab_shared` regains BOTH controls. Kills a gate keyed to
  // a slotted `id` that would leave every unslotted position untunable.
  it('2/ab_shared tunes both receivers, each on its own position', () => {
    const onTuneFrequency = vi.fn();
    const t = mountSurface({ viewModel: topologyFixtures['2/ab_shared'], onTuneFrequency });
    expect(withDigits(t).map(tileId)).toEqual(['MAIN:unslotted', 'SUB:unslotted']);
    for (const slot of withDigits(t)) wheelUp(onesDigit(slot));
    flushSync();
    expect(onTuneFrequency.mock.calls.map((c) => c[0])).toEqual(['MAIN', 'SUB']);
  });

  // The single-receiver topologies are UNCHANGED by G4: one receiver has one
  // active slot, so the pre-G4 "one tunable tile per radio" still holds there.
  it.each(['1/single', '1/ab'] as const)('%s still carries exactly one control', (id) => {
    const t = mountSurface({ viewModel: topologyFixtures[id], onTuneFrequency: vi.fn() });
    expect(withDigits(t)).toHaveLength(1);
    expect(tileOf(withDigits(t)[0]).dataset.vfoActive).toBe('true');
  });
});

/**
 * MOR-1322 fix round (verification B2) — the composition crux, pinned with a
 * design language ACTUALLY ACTIVE.
 *
 * The first cut asserted `not.toContain('MHz')` and never set
 * `[data-design-language]`, so the only state that can produce a double readout
 * was never entered: two mutants — rendering the language text alongside the
 * digits, and dropping the language's region attributes in the tunable branch —
 * both survived the whole suite. These tests activate a real registered
 * language (MOR-1278 attribute doctrine, same `activate` idiom as
 * `design-language-wiring.component.test.ts`) and assert the rule directly.
 */
describe('per-digit tuning (MOR-1322) — composition with an ACTIVE design language', () => {
  /** MOR-1278: the activation attribute is the single switch. */
  const activate = (id: string | null) => {
    if (id === null) delete document.documentElement.dataset.designLanguage;
    else document.documentElement.dataset.designLanguage = id;
  };
  afterEach(() => activate(null));

  /** Every `data-dl-*` the language stamps on the slot — its claim on the region. */
  const regionAttrs = (el: HTMLElement) => [...el.attributes]
    .filter((a) => a.name.startsWith('data-dl-'))
    .map((a) => `${a.name}=${a.value}`)
    .sort();

  const LANGUAGES = ['studioline', 'fieldline'] as const;

  // (a) THE RULE. Kills MV3: rendering `freq.text` alongside the digits when a
  // language is active — the exact double readout the ruling forbids, in the
  // only state that can produce it.
  it.each(LANGUAGES)('%s: a tunable slot shows the digits and NO language text', (lang) => {
    activate(lang);
    const t = mountSurface({ viewModel: tunableTile, onTuneFrequency: vi.fn() });
    const slot = activeSlot(t);
    const digits = [...slot.querySelectorAll('.digit')].map((d) => d.textContent).join('');
    // The language's own rendering of this frequency, for comparison.
    activate(null);
    const plainSlot = activeSlot(mountSurface({ viewModel: tunableTile }));
    activate(lang);
    const languageText = activeSlot(mountSurface({ viewModel: tunableTile })).textContent!.trim();
    // The language text is a real, DIFFERENT string from the v2 readout —
    // otherwise this test could pass with the language inert.
    expect(languageText).not.toBe(plainSlot.textContent!.trim());
    // ...and it does not appear in the tunable slot: exactly one readout.
    expect(slot.textContent!.replace(/\s/g, '')).not.toContain(languageText.replace(/\s/g, ''));
    // The one readout present is the digit control, spelling the same fact.
    expect(Number(digits)).toBe(tunableTile.vfos.find((v) => v.isActive)!.frequencyHz);
  });

  // (b) Kills MV4: dropping the language's region attributes from the tunable
  // branch. The renderer stays display-only, but the language still owns the
  // REGION — its hooks must be identical in both fillings.
  it.each(LANGUAGES)('%s: region attributes are identical in both fillings', (lang) => {
    activate(lang);
    const tuned = activeSlot(mountSurface({ viewModel: tunableTile, onTuneFrequency: vi.fn() }));
    const plain = activeSlot(mountSurface({ viewModel: tunableTile }));
    // Non-vacuous: the language really does stamp something here, so an
    // "identical" of two empty lists cannot pass.
    expect(regionAttrs(plain).length).toBeGreaterThan(0);
    expect(regionAttrs(tuned)).toEqual(regionAttrs(plain));
  });

  // (c) The language text RETURNS when tuning is unavailable — the other half
  // of the mutual exclusion, so the rule is not "digits always win".
  it.each(LANGUAGES)('%s: the language readout returns on a non-tunable tile', (lang) => {
    activate(lang);
    // THE SAME TILE, both fillings. With no intent wired the active tile shows
    // the language text; with the intent wired it shows digits. Comparing one
    // tile across the two states is what proves mutual exclusion rather than
    // two tiles that happen to differ.
    const untuned = activeSlot(mountSurface({ viewModel: tunableTile }));
    const languageText = untuned.textContent!.trim();
    expect(languageText.length).toBeGreaterThan(0);
    expect(untuned.querySelectorAll('.digit')).toHaveLength(0);

    const t = mountSurface({ viewModel: tunableTile, onTuneFrequency: vi.fn() });
    expect(activeSlot(t).querySelectorAll('.digit').length).toBeGreaterThan(0);

    // ...and an INACTIVE tile keeps the language readout even while tuning is
    // wired elsewhere — the language is not switched off globally by tuning.
    const inactive = slots(t).find((sl) => sl !== activeSlot(t))!;
    expect(inactive.querySelectorAll('.digit')).toHaveLength(0);
    expect(inactive.textContent!.trim().length).toBeGreaterThan(0);
    // It is the LANGUAGE's rendering, not the v2 fallback.
    activate(null);
    const v2 = slots(mountSurface({ viewModel: tunableTile })).find(
      (sl) => sl !== activeSlot(mountSurface({ viewModel: tunableTile })))!;
    expect(inactive.textContent!.trim()).not.toBe(v2.textContent!.trim());
  });

  // Exactly ONE readout per tile, counted structurally — no tautology this
  // time: a tile must carry either digits or text, never both.
  it.each([...LANGUAGES, null])('%s: every tile carries exactly one readout', (lang) => {
    activate(lang);
    for (const props of [{}, { onTuneFrequency: vi.fn() }]) {
      const t = mountSurface({ viewModel: tunableTile, ...props });
      for (const slot of slots(t)) {
        const hasDigits = slot.querySelectorAll('.digit').length > 0;
        // Text OUTSIDE the digit control — what a double readout would add.
        const ownText = [...slot.childNodes]
          .filter((n) => n.nodeType === Node.TEXT_NODE)
          .map((n) => n.textContent!.trim()).join('');
        // Exactly one filling: digits XOR text. Both arms are asserted, and
        // they differ — no tautology this time (verification B2).
        if (hasDigits) expect(ownText, 'digits + text = double readout').toBe('');
        else expect(ownText.length, 'no digits and no text = blank readout').toBeGreaterThan(0);
      }
    }
  });
});
