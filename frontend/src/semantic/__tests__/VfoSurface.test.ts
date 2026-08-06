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
