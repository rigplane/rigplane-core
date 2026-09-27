import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { readFileSync } from 'node:fs';
import { keyBlockedReasons, type KeyBlockedReason } from '../../../../semantic/rx-tx-surface';
import { toVfoControlProps } from '../../../../lib/runtime/props/panel-props';
import type { Capabilities } from '../../../../lib/types/capabilities';

const bindings = vi.hoisted(() => ({
  vfo: { onSwap: vi.fn(), onEqual: vi.fn(), onDualWatchToggle: vi.fn(), onSplitToggle: vi.fn() },
  ritXit: { onXitToggle: vi.fn(), onClear: vi.fn() },
  cw: { onBreakInModeChange: vi.fn() },
  tx: { onAtuTune: vi.fn() },
  read: vi.fn(),
}));

const props = vi.hoisted(() => ({
  vfo: {
    hasDualRx: true, hasSplit: true, hasRit: true, hasTuner: true, isCwMode: true,
    hasCw: true, hasBreakIn: true, breakInMode: 0 as number | null,
    breakInChoices: [
      { value: 0, label: 'OFF' }, { value: 1, label: 'SEMI' }, { value: 2, label: 'FULL' },
    ] as { value: number; label: string }[],
  },
  ritXit: { xitActive: false },
  ops: { dualWatch: false, splitActive: false },
}));

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  deriveVfoControlProps: () => props.vfo,
  deriveRitXitProps: () => props.ritXit,
  getVfoHandlers: () => bindings.vfo,
  getRitXitHandlers: () => bindings.ritXit,
  getCwHandlers: () => bindings.cw,
  getTxHandlers: () => bindings.tx,
  bindVfoTunerContext: () => ({ read: bindings.read }),
}));

vi.mock('$lib/runtime/adapters/vfo-adapter', () => ({ deriveVfoOps: () => props.ops }));
vi.mock('$lib/runtime/commands/panel-commands', () => ({
  makeVfoHandlers: () => bindings.vfo,
  makeRitXitHandlers: () => bindings.ritXit,
  makeCwPanelHandlers: () => bindings.cw,
}));
vi.mock('$lib/runtime', () => ({ runtime: {} }));

import VfoControlPanel from '../VfoControlPanel.svelte';

let component: ReturnType<typeof mount> | undefined;
let target: HTMLDivElement;

const allowed = Object.freeze({
  view: Object.freeze({ txTarget: { status: 'known' }, txPermit: { status: 'allowed' } }) as Parameters<typeof keyBlockedReasons>[0],
  tx: Object.freeze({ phase: 'idle', radioTx: 'off', txRisk: 'none', fault: null }) as Parameters<typeof keyBlockedReasons>[1],
});

const blockedCases: readonly Readonly<{
  name: string;
  expected: KeyBlockedReason;
  view: Parameters<typeof keyBlockedReasons>[0];
  tx: Parameters<typeof keyBlockedReasons>[1];
}>[] = [
  { name: 'unknown target', expected: 'tx-target-unknown', view: { ...allowed.view, txTarget: { status: 'unknown' } } as typeof allowed.view, tx: allowed.tx },
  { name: 'denied permit', expected: 'tx-permit-denied', view: { ...allowed.view, txPermit: { status: 'denied' } } as typeof allowed.view, tx: allowed.tx },
  { name: 'unknown permit', expected: 'tx-permit-unknown', view: { ...allowed.view, txPermit: { status: 'unknown' } } as typeof allowed.view, tx: allowed.tx },
  { name: 'fault', expected: 'tx-fault', view: allowed.view, tx: { ...allowed.tx, fault: 'fault' } as typeof allowed.tx },
  { name: 'non-idle phase', expected: 'tx-busy', view: allowed.view, tx: { ...allowed.tx, phase: 'key-confirm-pending' } as typeof allowed.tx },
  // MOR-1906: RF doubt is not a lease. Both rows still block the TUNE carrier
  // exactly as before — only the reason the operator is given changed, from
  // "a TX session is already in progress" to what the authority actually knows
  // about the RF state.
  { name: 'TX risk', expected: 'rf-state-unknown', view: allowed.view, tx: { ...allowed.tx, txRisk: 'uncertain' } as typeof allowed.tx },
  { name: 'confirmed TX risk', expected: 'radio-transmitting', view: allowed.view, tx: { ...allowed.tx, txRisk: 'confirmed-on' } as typeof allowed.tx },
  { name: 'radio TX on', expected: 'radio-transmitting', view: allowed.view, tx: { ...allowed.tx, radioTx: 'on' } as typeof allowed.tx },
  { name: 'radio TX unknown', expected: 'rf-state-unknown', view: allowed.view, tx: { ...allowed.tx, radioTx: 'unknown' } as typeof allowed.tx },
];

/* Accessible name of a node: everything except `aria-hidden` subtrees.
   The BK key's hidden sizer (MOR-2729) must not leak into it. */
function accessibleText(node: Element): string {
  const clone = node.cloneNode(true) as Element;
  for (const hidden of Array.from(clone.querySelectorAll('[aria-hidden="true"]'))) hidden.remove();
  return clone.textContent?.trim() ?? '';
}

function button(label: string): HTMLButtonElement {
  const found = Array.from(target.querySelectorAll('button')).find((node) => accessibleText(node) === label);
  if (!found) throw new Error(`missing ${label}`);
  return found as HTMLButtonElement;
}

function mountPanel() {
  component = mount(VfoControlPanel, { target });
  flushSync();
}

beforeEach(() => {
  target = document.createElement('div');
  document.body.appendChild(target);
  bindings.read.mockReset();
  bindings.read.mockReturnValue(allowed);
  for (const group of [bindings.vfo, bindings.ritXit, bindings.cw, bindings.tx]) {
    for (const callback of Object.values(group)) callback.mockClear();
  }
});

afterEach(() => {
  if (component) unmount(component);
  component = undefined;
  target.remove();
});

describe('VfoControlPanel authority boundary', () => {
  it('has only reviewed adapter bindings and the shared pure TUNE gate', () => {
    const source = readFileSync('src/components-v2/panels/lcd/VfoControlPanel.svelte', 'utf8');
    expect(source).toMatch(/getVfoHandlers/);
    expect(source).toMatch(/getRitXitHandlers/);
    expect(source).toMatch(/getCwHandlers/);
    expect(source).toMatch(/getTxHandlers/);
    expect(source).toMatch(/bindVfoTunerContext/);
    expect(source).toMatch(/keyBlockedReasons/);
    for (const forbidden of [
      'command-bus', 'panel-commands', 'radio-intents', "from '$lib/runtime'", '$lib/transport',
      '$lib/stores', 'audio-manager', 'dispatchRadioIntent', 'sendCommand', 'runtime.send',
      'patchRadioState', 'cw_auto_tune', 'set_tuner_status', 'import(', 'export {', 'export *',
    ]) expect(source).not.toContain(forbidden);
  });

  it('keeps callback identity, button order, and break-in parameters exact', () => {
    mountPanel();
    expect(Array.from(target.querySelectorAll('button')).map((node) => accessibleText(node)))
      .toEqual(['A↔B', 'A=B', 'DW', 'SPLIT', 'XIT', 'CLR', 'TUNE', 'BK-OFF']);
    button('A↔B').click(); button('A=B').click(); button('DW').click(); button('SPLIT').click();
    button('XIT').click(); button('CLR').click(); button('BK-OFF').click();
    expect(bindings.vfo.onSwap).toHaveBeenCalledOnce();
    expect(bindings.vfo.onEqual).toHaveBeenCalledOnce();
    expect(bindings.vfo.onDualWatchToggle).toHaveBeenCalledExactlyOnceWith(true);
    expect(bindings.vfo.onSplitToggle).toHaveBeenCalledOnce();
    expect(bindings.ritXit.onXitToggle).toHaveBeenCalledOnce();
    expect(bindings.ritXit.onClear).toHaveBeenCalledOnce();
    expect(bindings.cw.onBreakInModeChange).toHaveBeenCalledExactlyOnceWith(1);
    expect(bindings.tx.onAtuTune).not.toHaveBeenCalled();
  });

  it('reads tuner facts at click time and sends exactly one canonical callback only when shared gate permits', () => {
    mountPanel();
    const transmitting = { ...allowed.tx, radioTx: 'on' } as typeof allowed.tx;
    bindings.read.mockReturnValueOnce({ ...allowed, tx: transmitting });
    button('TUNE').click();
    expect(bindings.read).toHaveBeenCalledOnce();
    expect(bindings.tx.onAtuTune).not.toHaveBeenCalled();
    bindings.read.mockReturnValue(allowed);
    button('TUNE').click();
    expect(bindings.read).toHaveBeenCalledTimes(2);
    expect(keyBlockedReasons(allowed.view, transmitting)).toEqual(['radio-transmitting']);
    expect(keyBlockedReasons(allowed.view, allowed.tx)).toEqual([]);
    expect(bindings.tx.onAtuTune).toHaveBeenCalledOnce();
  });

  it.each(blockedCases)('emits zero TUNE callbacks for canonical $name', ({ expected, view, tx }) => {
    mountPanel();
    expect(keyBlockedReasons(view, tx)).toContain(expected);
    bindings.read.mockReturnValue({ view, tx });
    button('TUNE').click();
    expect(bindings.read).toHaveBeenCalledOnce();
    expect(bindings.tx.onAtuTune).not.toHaveBeenCalled();
  });

  /* MOR-2729 — the LCD break-in key cycles exactly the profile's published
     choices. On the FTX-1 ([0=OFF, 1=ON]) it can always get BACK to OFF,
     which the old hard-coded 0/1/2 cycle could not (stuck ON). */
  const legacyChoices: { value: number; label: string }[] = [
    { value: 0, label: 'OFF' }, { value: 1, label: 'SEMI' }, { value: 2, label: 'FULL' },
  ];
  function cycleCase(steps: readonly (readonly [label: string, mode: number, active: boolean])[]):
    void {
    for (const [label, mode, active] of steps) {
      props.vfo.breakInMode = mode;
      mountPanel();
      try {
        expect(button(label).classList.contains('active')).toBe(active);
        button(label).click();
        const values = [...props.vfo.breakInChoices.map((c: { value: number }) => c.value)];
        const next = values[(values.indexOf(mode) + 1) % values.length];
        expect(bindings.cw.onBreakInModeChange).toHaveBeenCalledWith(next);
      } finally {
        if (component) {
          unmount(component);
          component = undefined;
        }
      }
    }
  }

  it('FTX-1 cycle: BK-OFF → BK-ON → BK-OFF', () => {
    try {
      props.vfo.breakInChoices = [{ value: 0, label: 'OFF' }, { value: 1, label: 'ON' }];
      cycleCase([['BK-OFF', 0, false], ['BK-ON', 1, true]]);
    } finally {
      props.vfo.breakInChoices = legacyChoices;
      props.vfo.breakInMode = 0;
    }
  });

  it('IC-7300 cycle: BK-OFF → BK-SEMI → BK-FULL → BK-OFF', () => {
    cycleCase([['BK-OFF', 0, false], ['BK-SEMI', 1, true], ['BK-FULL', 2, true]]);
  });

  it('an empty published list (X6100, X6200) renders no break-in key', () => {
    try {
      props.vfo.breakInChoices = [];
      mountPanel();
      const names = Array.from(target.querySelectorAll('button')).map((b) => b.textContent?.trim());
      for (const label of ['OFF', 'ON', 'SEMI', 'FULL', 'BK', 'BK-OFF', 'BK-ON', 'BK-SEMI', 'BK-FULL'])
        expect(names).not.toContain(label);
    } finally {
      props.vfo.breakInChoices = legacyChoices;
    }
  });

  // Item 1 (review 2026-09-27): no LEGACY_BREAK_IN_CHOICES fallback — against
  // the REAL toVfoControlProps. Absent means an empty list means no BK key.
  it('a capabilities payload with no breakInChoices field gets no break-in choices (MOR-2729)', () => {
    const caps = { capabilities: [] } as unknown as Capabilities;
    expect(toVfoControlProps(null, caps).breakInChoices).toEqual([]);
  });

  /* Item 4 (review 2026-09-27): while break-in is unread the BK key is
     disabled and unlit and sends nothing — but its NAME ("BK") stays, in
     text and accessible name alike; once read it cycles as before. */
  it('while break-in is unread, the BK key shows exactly "BK", stays disabled and unlit, and sends nothing (MOR-2729)', () => {
    try {
      props.vfo.breakInMode = null;
      mountPanel();
      const bk = button('BK');
      expect(bk.disabled).toBe(true);
      expect(bk.classList.contains('active')).toBe(false);
      // the visible text is the accessible name — no separate aria-label
      // may compensate for an empty label (review 2026-09-27).
      expect(bk.getAttribute('aria-label') ?? bk.textContent?.trim()).toContain('BK');
      bk.click();
      expect(bindings.cw.onBreakInModeChange).not.toHaveBeenCalled();
    } finally {
      props.vfo.breakInMode = 0;
    }
  });

  /* MOR-2729 (GLM-5.3 delta review on fb8db37a): a `min-width: 7ch` rule
     could not reserve the widest BK text — with border-box the ch reserve
     also had to absorb padding, border, and letter-spacing, so the first
     reading grew the key by 15–22 px. The reserve is a hidden sizer:
     one aria-hidden element holding `BK` plus `BK-<label>` for every
     published choice, stacked with the visible span in one grid cell, so
     the key is always as wide as the widest possible text. */
  function sizerTexts(bk: HTMLElement): string[] {
    const sizer = bk.querySelector('[aria-hidden="true"]');
    expect(sizer, 'a hidden sizer span must exist inside the BK key').not.toBeNull();
    return Array.from(sizer!.querySelectorAll('span')).map((node) => node.textContent ?? '');
  }

  it('the BK key carries a hidden sizer holding every possible text (MOR-2729)', () => {
    try {
      props.vfo.breakInChoices = [{ value: 0, label: 'OFF' }, { value: 1, label: 'ON' }];
      mountPanel();
      const ftx1 = target.querySelector('.lcd-btn-bk') as HTMLElement;
      expect(sizerTexts(ftx1).sort()).toEqual(['BK', 'BK-OFF', 'BK-ON']);
      if (component == null) throw new Error('component must be mounted before unmount');
      unmount(component);
      component = undefined;
      props.vfo.breakInChoices = [
        { value: 0, label: 'OFF' }, { value: 1, label: 'SEMI' }, { value: 2, label: 'FULL' },
      ];
      mountPanel();
      const ic7300 = target.querySelector('.lcd-btn-bk') as HTMLElement;
      expect(sizerTexts(ic7300).sort()).toEqual(['BK', 'BK-FULL', 'BK-OFF', 'BK-SEMI']);
    } finally {
      props.vfo.breakInChoices = legacyChoices;
    }
  });

  it('the accessible name is exactly the visible text — no sizer text leaks (MOR-2729)', () => {
    try {
      props.vfo.breakInMode = null;
      mountPanel();
      expect(accessibleText(target.querySelector('.lcd-btn-bk') as Element)).toBe('BK');
      if (component == null) throw new Error('component must be mounted before unmount');
      unmount(component);
      component = undefined;
      props.vfo.breakInMode = 2;
      mountPanel();
      expect(accessibleText(target.querySelector('.lcd-btn-bk') as Element)).toBe('BK-FULL');
    } finally {
      props.vfo.breakInMode = 0;
    }
  });

  it.each(['generation', 'capability', 'availability', 'impossible physical SUB'])
  ('emits zero TUNE callbacks when %s collapses the tuner view to null', () => {
    mountPanel();
    bindings.read.mockReturnValue({ view: null, tx: allowed.tx });
    button('TUNE').click();
    expect(bindings.read).toHaveBeenCalledOnce();
    expect(bindings.tx.onAtuTune).not.toHaveBeenCalled();
  });
});
