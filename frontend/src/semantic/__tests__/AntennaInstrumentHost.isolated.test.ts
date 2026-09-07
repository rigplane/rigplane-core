import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
// @ts-expect-error -- Svelte's reactive test harness has no public types.
import { proxy } from 'svelte/internal/client';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import { toRadioViewModel } from '$lib/runtime/adapters/radio-view-model-adapter';
import { ANTENNA_BLOCKED_LABEL,
  type AntennaAuthorityPublication as Publication, type AntennaInstrumentHandles,
} from '../AntennaInstrumentHost.svelte';
import type { TxAuthoritySnapshot } from '../rx-tx-surface';
const seats = vi.hoisted(() => ({ destroy: [] as ReturnType<typeof vi.fn>[] }));
vi.mock('../../primitives/control-instruments/control-instrument-renderer.svelte', async (original) => {
  const actual = await original<typeof import('../../primitives/control-instruments/control-instrument-renderer.svelte')>();
  const track = <T extends { destroy(): void }>(seat: T): T => {
    const destroy = vi.fn(() => seat.destroy()); seats.destroy.push(destroy);
    return { ...seat, destroy };
  };
  return { ...actual,
    createAbsoluteChoiceRendererSeat: (...args: Parameters<typeof actual.createAbsoluteChoiceRendererSeat>) => track(actual.createAbsoluteChoiceRendererSeat(...args)),
    createToggleRendererSeat: (...args: Parameters<typeof actual.createToggleRendererSeat>) => track(actual.createToggleRendererSeat(...args)),
  };
});
import Renderer, { retainedInvocations, resetRetainedInvocations } from '../../primitives/control-instruments/__tests__/support/FiniteControlRendererFixture.svelte';
import Fixture from './fixtures/AntennaInstrumentHostFixture.svelte';
const idle: TxAuthoritySnapshot = { phase: 'idle', intent: null, radioTx: 'off', txRisk: 'none', fault: null };
const appearance = { action: Renderer, choice: Renderer, toggle: Renderer };
function source(): Publication {
  return { session: { state: 'connected', epoch: 1 },
    state: { providerGeneration: 1, active: 'MAIN', txAntenna: 1, rxAntenna1: false,
      rxAntenna2: true, tunerStatus: 0, fieldStatus: Object.fromEntries(
        ['active', 'txAntenna', 'rxAntenna1', 'rxAntenna2', 'tunerStatus'].map(key => [key,
          { observed: true, freshness: 'fresh', availability: 'available' }])) } as unknown as ServerState,
    caps: { model: 'fixture', providerGeneration: 1, receivers: 1, vfoScheme: 'single',
      scope: false, audio: false, tx: true, freqRanges: [], modes: [], filters: [], txBands: null,
      audioConfig: { sampleRate: 48000, channels: 1, codecs: [] }, webrtc: { available: false, enabled: false },
      antennas: 2, capabilities: ['tx', 'rx_antenna', 'tuner'] } as Capabilities };
}
let component: ReturnType<typeof mount> | undefined;
let target: HTMLDivElement;
afterEach(async () => { if (component) await unmount(component); component = undefined;
  target?.remove(); seats.destroy.length = 0; resetRetainedInvocations(); });
function render(external = true) {
  let publication = source(); let tx = idle; let subscriber: (p: Publication) => void = () => {};
  const stop = vi.fn(); const onSelectPort = vi.fn(); const onToggleRxAnt = vi.fn();
  const captures: AntennaInstrumentHandles[] = [];
  const props = proxy({ view: toRadioViewModel(publication.state, publication.caps)!, tx,
    finiteAppearance: external ? appearance : undefined, arrangement: 'grouped', body: true,
    onSelectPort, onToggleRxAnt, readTx: () => tx,
    capture: (handles: AntennaInstrumentHandles) => captures.push(handles),
    subscribeControlAuthority: (handler: (p: Publication) => void) => {
      subscriber = handler; handler(publication); return stop;
    } });
  target = document.createElement('div'); document.body.appendChild(target);
  component = mount(Fixture, { target, props }); flushSync();
  const pair = () => external
    ? [retainedInvocations.get('Transmit antenna')!, retainedInvocations.get('RX-ANT')!]
    : ['port-2', 'rx-toggle'].map(id => {
      const button = target.querySelector(`[data-testid="antenna-${id}"]`)!;
      return () => button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
  return { props, stop, onSelectPort, onToggleRxAnt, captures, pair,
    publish(next: Publication) { publication = next; subscriber(next); props.view = toRadioViewModel(next.state, next.caps); },
    tx(next: TxAuthoritySnapshot) { tx = next; }, source: () => publication,
  };
}
const invoke = (pair: ReturnType<ReturnType<typeof render>['pair']>) => { pair[0](2); pair[1](); };
describe('persistent antenna handles', () => {
  it.each([false, true])('retains same-source facts and refuses TX/ATU before flush (external=%s)', external => {
    const r = render(external); const callbacks = r.pair();
    invoke(callbacks); expect(r.onSelectPort).toHaveBeenCalledOnce(); expect(r.onToggleRxAnt).toHaveBeenCalledOnce();
    r.onSelectPort.mockClear(); r.onToggleRxAnt.mockClear();
    for (const unsafe of [{ ...idle, radioTx: 'on' }, { ...idle, radioTx: 'unknown' },
      { ...idle, phase: 'key-confirm-pending' }, { ...idle, txRisk: 'uncertain' },
      { ...idle, phase: 'failed', txRisk: 'confirmed-on' }] as TxAuthoritySnapshot[]) {
      r.tx(unsafe); invoke(callbacks);
      expect(r.onSelectPort).not.toHaveBeenCalled(); expect(r.onToggleRxAnt).not.toHaveBeenCalled();
    }
    r.tx(idle);
    for (const freshness of ['fresh', 'stale', 'unknown']) {
      const p = source(); p.state!.tunerStatus = freshness === 'fresh' ? 2 : 0;
      p.state!.fieldStatus!.tunerStatus = { ...p.state!.fieldStatus!.tunerStatus!,
        freshness: freshness === 'stale' ? 'stale' : 'fresh', observed: freshness !== 'unknown' };
      r.publish(p); invoke(callbacks);
      expect(r.onSelectPort).not.toHaveBeenCalled(); expect(r.onToggleRxAnt).not.toHaveBeenCalled();
    }
    const p = source(); p.state!.txAntenna = 2; r.publish(p); invoke(callbacks);
    expect(r.onSelectPort).toHaveBeenCalledOnce(); expect(r.onToggleRxAnt).toHaveBeenCalledOnce();
    flushSync(); expect(r.captures.every(handles => handles === r.captures[0])).toBe(true);
    expect(Object.keys(r.captures[0])).toEqual(['txPort', 'rxAnt']); expect(seats.destroy).toHaveLength(2);
    expect(external ? target.querySelector('[data-reading]')?.getAttribute('data-reading')
      : target.querySelector('[data-testid="antenna-port-value"]')?.textContent).toBe('2');
  });
  it('preserves native grouped and independent parity through the two stable handles', () => {
    const r = render(false); const handles = r.captures[0]; r.props.arrangement = 'independent'; flushSync();
    expect(target.querySelectorAll('[role="radio"]')).toHaveLength(2); invoke(r.pair());
    expect(r.onSelectPort).toHaveBeenCalledExactlyOnceWith(2); expect(r.onToggleRxAnt).toHaveBeenCalledOnce();
    expect(r.captures.at(-1)).toBe(handles); expect(target.querySelector('[data-testid="antenna-surface"]')).toBeNull();
  });
  it('renders blocked reasons for an independent arrangement, keyed to each seat by aria-describedby', () => {
    const r = render(false); r.props.arrangement = 'independent'; flushSync();
    const list = target.querySelector('[data-testid="independent-blocked"]')!;
    const port = target.querySelector('[data-testid="antenna-port-2"]')!;
    const toggle = target.querySelector('[data-testid="antenna-rx-toggle"]')!;
    expect(list.id).not.toBe(''); expect(port.getAttribute('aria-describedby')).toBe(list.id);
    expect(toggle.getAttribute('aria-describedby')).toBe(list.id);
    expect(list.children).toHaveLength(0);
    const p = source(); p.state!.tunerStatus = 2;
    p.state!.fieldStatus!.tunerStatus = { ...p.state!.fieldStatus!.tunerStatus!, freshness: 'fresh', observed: true };
    r.publish(p); flushSync();
    expect(list.children).toHaveLength(1);
    expect(list.querySelector('[data-reason="tuner-not-ready"]')?.textContent)
      .toBe(ANTENNA_BLOCKED_LABEL['tuner-not-ready']);
    r.publish(source()); flushSync();
    expect(list.children).toHaveLength(0);
  });
  it('admits an unknown absolute destination but refuses unknown relative RX and unoffered ports', () => {
    const r = render(); const p = source(); p.state!.txAntenna = undefined; r.publish(p); flushSync();
    const callbacks = r.pair(); callbacks[0](3); callbacks[1](); expect(r.onSelectPort).not.toHaveBeenCalled();
    callbacks[0](2); expect(r.onSelectPort).toHaveBeenCalledExactlyOnceWith(2); expect(r.onToggleRxAnt).not.toHaveBeenCalled();
    expect(target.querySelector('[data-reading]')?.getAttribute('data-reading')).toBe('unknown');
  });
  it('revokes both leases on session/provider/topology/structural A-B-A and invalid recovery', () => {
    const r = render();
    const variants = [
      (p: Publication) => ({ ...p, session: { ...p.session, epoch: 2 } }),
      (p: Publication) => ({ ...p, state: { ...p.state!, providerGeneration: 2 }, caps: { ...p.caps!, providerGeneration: 2 } }),
      (p: Publication) => ({ ...p, caps: { ...p.caps!, vfoScheme: 'ab' as const } }),
      (p: Publication) => ({ ...p, caps: { ...p.caps!, antennas: 1 } }),
      (p: Publication) => ({ ...p, caps: { ...p.caps!, capabilities: ['tx', 'tuner'] } }),
      (p: Publication) => ({ ...p, session: { ...p.session, state: 'disconnected' as const } }),
      (p: Publication) => ({ ...p, state: null }), (p: Publication) => ({ ...p, caps: null }),
      (p: Publication) => ({ ...p, caps: { ...p.caps!, providerGeneration: 3 } }),
    ];
    for (const change of variants) {
      const old = r.pair(); r.publish(change(source())); r.publish(source()); invoke(old);
      expect(r.onSelectPort).not.toHaveBeenCalled(); expect(r.onToggleRxAnt).not.toHaveBeenCalled();
      flushSync(); invoke(r.pair()); expect(r.onSelectPort).toHaveBeenCalledOnce(); expect(r.onToggleRxAnt).toHaveBeenCalledOnce();
      r.onSelectPort.mockClear(); r.onToggleRxAnt.mockClear();
    }
  });
  it('retains radio-wide leases when activeReceiver changes', () => {
    const r = render(); const old = r.pair(); const p = source(); p.state!.active = 'SUB';
    r.publish(p); invoke(old); expect(r.onSelectPort).toHaveBeenCalledOnce(); expect(r.onToggleRxAnt).toHaveBeenCalledOnce();
  });
  it('revokes appearance A-B-A, null body and unmounted callbacks; releases seats/subscription', async () => {
    const r = render(); const old = r.pair(); r.props.finiteAppearance = undefined; flushSync();
    const native = ['port-2', 'rx-toggle'].map(id => target.querySelector(`[data-testid="antenna-${id}"]`)!);
    r.props.finiteAppearance = appearance; flushSync(); invoke(old);
    native.forEach(node => {
      target.appendChild(node); node.dispatchEvent(new MouseEvent('click', { bubbles: true })); node.remove();
    });
    expect(r.onSelectPort).not.toHaveBeenCalled(); expect(r.onToggleRxAnt).not.toHaveBeenCalled();
    const removed = r.pair(); r.props.body = false; flushSync(); r.props.body = true; flushSync(); invoke(removed);
    expect(r.onSelectPort).not.toHaveBeenCalled(); invoke(r.pair()); expect(r.onSelectPort).toHaveBeenCalledOnce();
    const last = r.pair(); await unmount(component!); component = undefined;
    expect(r.stop).toHaveBeenCalledOnce(); expect(seats.destroy).toHaveLength(2);
    seats.destroy.forEach(destroy => expect(destroy).toHaveBeenCalledOnce());
    r.publish(source()); invoke(last); expect(r.onSelectPort).toHaveBeenCalledOnce(); expect(r.onToggleRxAnt).toHaveBeenCalledOnce();
    expect(target.querySelector('button')).toBeNull();
  });
});
