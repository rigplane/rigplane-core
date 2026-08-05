import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { CommandDeliveryEvent, ControlSessionTransition } from '$lib/transport/ws-client';
import type { AppAuthorityProjection } from '../app-authority';
const h = vi.hoisted(() => ({
  radio: null as any, caps: null as any, deliveries: new Set<(event: CommandDeliveryEvent) => void>(),
  sessions: new Set<(event: ControlSessionTransition) => void>(), send: vi.fn((_name: string, _params: Record<string, unknown>, _id?: string) => true),
  start: vi.fn(async (): Promise<string | null> => null), stop: vi.fn(), restore: vi.fn(), ids: 0,
}));
vi.mock('$lib/stores/radio.svelte', () => ({ getRadioState: () => h.radio }));
vi.mock('$lib/stores/capabilities.svelte', () => ({ getCapabilities: () => h.caps }));
vi.mock('$lib/types/protocol', () => ({ makeCommandId: () => `cmd-${++h.ids}` }));
vi.mock('$lib/runtime/adapters/tx-adapter', () => ({ getTxAudioControl: () => ({
  startTx: h.start, stopLocalAudio: h.stop, restoreModAfterConfirmedOff: h.restore,
}) }));
vi.mock('$lib/transport/ws-client', () => ({
  sendCommand: h.send,
  onCommandDelivery: (fn: (event: CommandDeliveryEvent) => void) => {
    h.deliveries.add(fn); return () => h.deliveries.delete(fn);
  },
  onControlSessionTransition: (fn: (event: ControlSessionTransition) => void) => {
    h.sessions.add(fn); return () => h.sessions.delete(fn);
  },
}));
import { createBrowserTxControllerDependencies } from '../browser-dependencies';
import { TxController } from '../controller';
const field = (at: number) => ({ observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: at, source: { source: 'poll_response' } });
const target = { receiver: 'SUB' as const, slot: 'B' as const, frequencyHz: 150 };
function resetFacts() {
  h.radio = { revision: 1, ptt: false, active: 'SUB', txTarget: { status: 'known', ...target },
    main: { dataMode: 0 }, sub: { dataMode: 1 }, data1ModInput: 5,
    fieldStatus: { ptt: field(1), txTarget: field(1), data1ModInput: field(1) } };
  h.caps = { tx: true, audioTx: true, capabilities: ['tx', 'mod_input_routing'],
    vfoScheme: 'main_sub', audioTxRequiredModInputSource: 5, txBands: [{ start: 100, end: 200 }] } as Capabilities;
}
const session: ControlSessionTransition = { state: 'connected', epoch: 4 };
const flush = async () => { await Promise.resolve(); await Promise.resolve(); };
// Real TxController wired to the real dependency factory (only its inner module-scope
// imports are mocked) — mirrors provideAppTxControllerHost's own construction in
// app-host.ts: a baseline projectAuthority call fixes the controller's initial
// epoch/marker, a second (fresh) call supplies the eligibility + PTT observation that
// is actually fed into the `start` event.
function makeController() {
  const factory = createBrowserTxControllerDependencies();
  const baseline = factory.projectAuthority(session);
  const controller = new TxController(baseline.epoch, baseline.ptt.marker, factory.dependencies);
  return { factory, controller };
}
function freshAuthority(factory: ReturnType<typeof createBrowserTxControllerDependencies>): AppAuthorityProjection {
  h.radio = { ...h.radio, fieldStatus: { ...h.radio.fieldStatus, ptt: field(2) } };
  return factory.projectAuthority(session);
}
function dispatchStart(controller: TxController, authority: AppAuthorityProjection, leaseId: string): void {
  controller.dispatch({ type: 'start', sourceId: 'test', leaseId, intent: 'momentary', eligibility: authority.eligibility, ptt: authority.ptt });
}
beforeEach(() => {
  vi.useFakeTimers(); resetFacts(); h.deliveries.clear(); h.sessions.clear();
  h.send.mockReset().mockReturnValue(true); h.start.mockReset().mockResolvedValue(null);
  h.stop.mockClear(); h.restore.mockClear(); h.ids = 0;
});
describe('browser TxController dependencies — fault injection at the production seam', () => {
  it('fails closed when startTx rejects: no ON dispatched, local audio stopped, fault surfaced', async () => {
    const { factory, controller } = makeController();
    h.start.mockImplementationOnce(() => Promise.reject(new Error('mic permission denied')));
    dispatchStart(controller, freshAuthority(factory), 'lease-reject');
    await flush();
    expect(controller.snapshot()).toMatchObject({ phase: 'failed', fault: 'audio-failed', mayOwnKey: false });
    expect(h.send).not.toHaveBeenCalled();
    expect(h.stop).toHaveBeenCalledTimes(1);
  });
  it('fails closed when startTx throws synchronously: no ON dispatched, local audio stopped, fault surfaced', () => {
    const { factory, controller } = makeController();
    h.start.mockImplementationOnce(() => { throw new Error('synchronous audio failure'); });
    dispatchStart(controller, freshAuthority(factory), 'lease-throw');
    expect(controller.snapshot()).toMatchObject({ phase: 'failed', fault: 'audio-failed', mayOwnKey: false });
    expect(h.send).not.toHaveBeenCalled();
    expect(h.stop).toHaveBeenCalledTimes(1);
  });
  it('fails closed when startTx resolves an error string: no ON dispatched, local audio stopped, fault surfaced', async () => {
    const { factory, controller } = makeController();
    h.start.mockImplementationOnce(() => Promise.resolve('mic permission denied'));
    dispatchStart(controller, freshAuthority(factory), 'lease-error-string');
    await flush();
    expect(controller.snapshot()).toMatchObject({ phase: 'failed', fault: 'audio-failed', mayOwnKey: false });
    expect(h.send).not.toHaveBeenCalled();
    expect(h.stop).toHaveBeenCalledTimes(1);
  });
  it('refuses to start on capability facts with an "unknown" frequency permit: no audio, no ON', () => {
    const { factory, controller } = makeController();
    h.caps = { ...h.caps, txBands: null };
    const authority = freshAuthority(factory);
    expect(authority.eligibility.permit).toBe('unknown');
    dispatchStart(controller, authority, 'lease-unknown-permit');
    expect(controller.snapshot()).toMatchObject({ phase: 'failed', fault: 'not-eligible' });
    expect(h.start).not.toHaveBeenCalled();
    expect(h.send).not.toHaveBeenCalled();
  });
  it('refuses to start on capability facts with a "denied" frequency permit: no audio, no ON', () => {
    const { factory, controller } = makeController();
    h.caps = { ...h.caps, txBands: [{ start: 400_000, end: 500_000 }] };
    const authority = freshAuthority(factory);
    expect(authority.eligibility.permit).toBe('denied');
    dispatchStart(controller, authority, 'lease-denied-permit');
    expect(controller.snapshot()).toMatchObject({ phase: 'failed', fault: 'not-eligible' });
    expect(h.start).not.toHaveBeenCalled();
    expect(h.send).not.toHaveBeenCalled();
  });
});
