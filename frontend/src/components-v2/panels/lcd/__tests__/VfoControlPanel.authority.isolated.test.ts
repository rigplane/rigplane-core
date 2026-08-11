import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { readFileSync } from 'node:fs';
import { keyBlockedReasons, type KeyBlockedReason } from '../../../../semantic/rx-tx-surface';

const bindings = vi.hoisted(() => ({
  vfo: { onSwap: vi.fn(), onEqual: vi.fn(), onDualWatchToggle: vi.fn(), onSplitToggle: vi.fn() },
  ritXit: { onXitToggle: vi.fn(), onClear: vi.fn() },
  cw: { onBreakInModeChange: vi.fn() },
  tx: { onAtuTune: vi.fn() },
  read: vi.fn(),
}));

const props = vi.hoisted(() => ({
  vfo: { hasDualRx: true, hasSplit: true, hasRit: true, hasTuner: true, isCwMode: true, hasCw: true, hasBreakIn: true, breakInMode: 0 },
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
vi.mock('../../../wiring/command-bus', () => ({
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
  tx: Object.freeze({ phase: 'idle', radioTx: 'off', txRisk: 'none', mayOwnKey: false, fault: null }) as Parameters<typeof keyBlockedReasons>[1],
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
  { name: 'key ownership', expected: 'tx-busy', view: allowed.view, tx: { ...allowed.tx, mayOwnKey: true } as typeof allowed.tx },
  { name: 'TX risk', expected: 'tx-busy', view: allowed.view, tx: { ...allowed.tx, txRisk: 'uncertain' } as typeof allowed.tx },
  { name: 'confirmed TX risk', expected: 'tx-busy', view: allowed.view, tx: { ...allowed.tx, txRisk: 'confirmed-on' } as typeof allowed.tx },
  { name: 'radio TX on', expected: 'radio-transmitting', view: allowed.view, tx: { ...allowed.tx, radioTx: 'on' } as typeof allowed.tx },
  { name: 'radio TX unknown', expected: 'rf-state-unknown', view: allowed.view, tx: { ...allowed.tx, radioTx: 'unknown' } as typeof allowed.tx },
];

function button(label: string): HTMLButtonElement {
  const found = Array.from(target.querySelectorAll('button')).find((node) => node.textContent?.trim() === label);
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
    expect(Array.from(target.querySelectorAll('button')).map((node) => node.textContent?.trim()))
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

  it.each(['generation', 'capability', 'availability', 'impossible physical SUB'])
  ('emits zero TUNE callbacks when %s collapses the tuner view to null', () => {
    mountPanel();
    bindings.read.mockReturnValue({ view: null, tx: allowed.tx });
    button('TUNE').click();
    expect(bindings.read).toHaveBeenCalledOnce();
    expect(bindings.tx.onAtuTune).not.toHaveBeenCalled();
  });
});
