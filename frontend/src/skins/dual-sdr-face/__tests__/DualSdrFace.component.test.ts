import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount, tick, unmount } from 'svelte';
import DualSdrFace from '../DualSdrFace.svelte';
import type { RadioViewModel } from '../../../semantic/radio-view-model';
import type { ScopeFrame } from '../../../lib/runtime/adapters/scope-adapter';

const known = <T>(value: T) => ({ reading: { status: 'known' as const, value }, availability: { structural: true, operational: true } });
const unknown = () => ({ reading: { status: 'unknown' as const }, availability: { structural: true, operational: false } });
const canvas = { clearRect: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), stroke: vi.fn(), strokeStyle: '', lineWidth: 0 };
beforeEach(() => { for (const call of Object.values(canvas)) if (typeof call === 'function') call.mockClear(); vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(canvas as unknown as CanvasRenderingContext2D); Object.defineProperty(HTMLCanvasElement.prototype, 'clientWidth', { configurable: true, get: () => 10 }); Object.defineProperty(HTMLCanvasElement.prototype, 'clientHeight', { configurable: true, get: () => 10 }); });

function view(): RadioViewModel {
  return {
    topologyId: '2/main_sub', vfoScheme: 'main_sub', activeReceiver: { status: 'known', receiver: 'MAIN' },
    vfos: [
      { receiver: 'MAIN', slot: { kind: 'unslotted' }, label: 'MAIN', frequencyHz: 7_070_000, mode: 'LSB', filter: 'FIL2', isActive: true, isActiveSlot: true, isTxTarget: false },
      { receiver: 'SUB', slot: { kind: 'unslotted' }, label: 'SUB', frequencyHz: null, mode: null, filter: null, isActive: false, isActiveSlot: true, isTxTarget: false },
    ], split: { status: 'unknown' }, dualWatch: { status: 'unknown' }, txTarget: { status: 'unknown', reason: 'not-observed' }, txPermit: { status: 'unknown', reason: 'target-unknown' },
    scope: { hardwareScope: { structural: true, operational: true }, audioFftScope: { structural: false, operational: false } }, disabledReasons: [], receiverIndicators: [], radioWideIndicators: { rfState: 'unknown', antenna: unknown(), atu: unknown(), ritActive: unknown(), ritOffset: unknown(), xitActive: unknown(), xitOffset: unknown(), actions: { main: { structural: false, operational: false }, sub: { structural: false, operational: false }, equalize: { structural: false, operational: false }, swap: { structural: false, operational: false }, quickSplit: { structural: false, operational: false }, quickDualWatch: { structural: false, operational: false }, speak: { structural: false, operational: false } } },
    meters: { rfState: 'receiving', signal: known(12), power: unknown(), swr: unknown(), alc: unknown(), compression: unknown(), drainVoltage: unknown(), drainCurrent: unknown() },
    rfFrontEnd: { preamp: known(1), preValues: [0, 1, 2], attenuator: known(0), attValues: [0, 20], rfGain: unknown(), squelch: unknown(), digiSel: unknown(), ipPlus: known(false) },
    scopeControls: { mode: known(0), edge: known(1), span: known(0), speed: known(0), hold: known(false), refDb: known(0), dual: known(false), receiver: known(0), duringTx: known(false), centerType: known(0), vbwNarrow: known(false), rbw: known(0) },
    txAux: { atu: unknown(), vox: known(false), voxGain: unknown(), antiVoxGain: unknown(), voxDelay: unknown(), compressor: known(false), compressorLevel: unknown(), monitor: unknown(), monitorLevel: unknown(), rfPower: unknown(), micGain: unknown(), driveGain: unknown() },
  } as unknown as RadioViewModel;
}

describe('DualSdrFace', () => {
  it('mounts one cluster per receiver, demuxes one scope subscription, and cleans it up', async () => {
    let listener: ((frame: ScopeFrame) => void) | undefined;
    const unsubscribe = vi.fn();
    const source = { subscribe: vi.fn((next: (frame: ScopeFrame) => void) => { listener = next; return unsubscribe; }) };
    const target = document.createElement('div');
    const component = mount(DualSdrFace, { target, props: { view: view(), scopeSource: source } });
    await tick();
    expect(source.subscribe).toHaveBeenCalledOnce();
    expect(target.querySelectorAll('[data-receiver-cluster]').length).toBe(2);
    expect(target.querySelector('[data-receiver-cluster="0"] [data-scope-state]')?.getAttribute('data-scope-state')).toBe('unknown');
    const pixels = new Uint8Array([1, 2]);
    listener?.({ receiver: 0, mode: 0, startFreq: 1, endFreq: 2, pixels });
    await tick();
    await Promise.resolve();
    expect(target.querySelector('[data-receiver-cluster="0"] [data-scope-state]')?.getAttribute('data-scope-state')).toBe('frame');
    expect(target.querySelector('[data-receiver-cluster="1"] [data-scope-state]')?.getAttribute('data-scope-state')).toBe('unknown');
    pixels[0] = 255;
    expect(target.querySelector('[data-receiver-cluster="0"] canvas')?.getAttribute('data-supplied-pixels')).toBe('2');
    expect(canvas.lineTo).toHaveBeenCalledWith(1, expect.closeTo(8.96, 2));
    listener?.({ receiver: 1, mode: 0, startFreq: 3, endFreq: 4, pixels: new Uint8Array([3]) });
    await tick();
    expect(target.querySelector('[data-receiver-cluster="1"] [data-scope-state]')?.getAttribute('data-scope-state')).toBe('frame');
    listener?.({ receiver: 9, mode: 0, startFreq: 9, endFreq: 9, pixels: new Uint8Array([9]) });
    await tick();
    expect(target.querySelector('[data-receiver-cluster="1"] .axis')?.textContent).toContain('3 — 4');
    unmount(component);
    expect(unsubscribe).toHaveBeenCalledOnce();
  });

  it('keeps unknown receiver display unasserted and enables only backed PRE', async () => {
    const onPreChange = vi.fn();
    const target = document.createElement('div');
    mount(DualSdrFace, { target, props: { view: view(), scopeSource: { subscribe: () => () => {} }, onPreChange } });
    await tick();
    expect(target.querySelector('[data-receiver-cluster="1"] [data-frequency]')?.textContent).toContain('—');
    expect(target.querySelector('[data-receiver-cluster="1"] [data-needle]')).toBeNull();
    const pre = target.querySelector<HTMLButtonElement>('[data-control="pre"]')!;
    expect(pre.disabled).toBe(false); await pre.click(); await tick(); expect(onPreChange).toHaveBeenCalledWith(2);
    for (const name of ['hold', 'main-sub', 'dual', 'mode', 'edge', 'att', 'ip', 'agc', 'vox', 'comp', 'ant', 'menu1', 'cent-fix', 'expd-set']) expect(target.querySelector<HTMLButtonElement>(`[data-control="${name}"]`)?.disabled).toBe(true);
  });
});
