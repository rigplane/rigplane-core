import { describe, expect, it, vi } from 'vitest';
import { scopeFixtures as fixtures } from './scope-wire-fixture';
import { ScopeStream } from '../scope-stream';
import type { ScopeMessageSource } from '../scope-stream';
import { scopeSlotKey } from '../adapters/scope-adapter';

function source() {
  const sinks = new Set<(buffer: ArrayBuffer) => void>();
  const port: ScopeMessageSource = {
    subscribe(sink) { sinks.add(sink); return () => { sinks.delete(sink); }; },
    close: vi.fn(),
  };
  return { port, emit(bytes = fixtures.radio) {
    const buffer = Uint8Array.from(bytes).buffer;
    for (const sink of sinks) sink(buffer);
    return buffer;
  }, retained: () => [...sinks][0], count: () => sinks.size };
}
const context = { source: 'radio' as const, sourceId: 'scope-1', epoch: 7 };
const emitBytes = (bytes: number[]) => Uint8Array.from(bytes).buffer;
const withSeq = (seq: number, pixel: number) => {
  const bytes = [...fixtures.radio];
  const view = new DataView(Uint8Array.from(bytes).buffer);
  view.setUint32(12, seq, true); view.setUint8(56 + "scope-1".length, pixel);
  return Array.from(new Uint8Array(view.buffer));
};

describe('ScopeStream', () => {
  it.each(['radio', 'audio_fft'] as const)('delivers %s pixels through its sink without copying', (sourceKind) => {
    const input = source(); const stream = new ScopeStream(input.port, { ...context, source: sourceKind }, 'borrowed');
    const sink = vi.fn(); stream.subscribe(sink);
    const buffer = input.emit(sourceKind === 'radio' ? fixtures.radio : fixtures.audio);
    const [frame, slotKey] = sink.mock.lastCall!;
    expect(frame?.source).toBe(sourceKind);
    expect(frame?.pixels.buffer).toBe(buffer);
    expect(frame?.pixels.byteOffset).toBe(56 + "scope-1".length);
    expect(slotKey).toBe(scopeSlotKey('scope-1', frame.receiver));
    stream.dispose();
  });
  it('keeps latest per slot outside the geometry store over 100 frames', () => {
    const input = source();
    const stream = new ScopeStream(input.port, context, 'borrowed');
    let writes = 0;
    const unwatch = stream.geometry.subscribe(() => { writes++; });
    const sink = vi.fn(); stream.subscribe(sink);
    for (let seq = 0; seq < 100; seq++) {
      const buffer = input.emit(withSeq(seq, seq));
      expect(stream.latest(scopeSlotKey('scope-1', 0))?.pixels?.buffer).toBe(buffer);
    }
    expect(writes).toBe(2); // initial null + first geometry
    expect(sink).toHaveBeenCalledTimes(100);
    expect(stream.receipt).toBe(100);
    input.emit(fixtures.audio);
    expect(stream.receipt).toBe(101);
    expect(stream.latest(scopeSlotKey('scope-1', 0))?.seq).toBe(99);
    expect(stream.latest(scopeSlotKey('scope-1', 1))?.seq).toBe(17);
    unwatch(); stream.dispose();
  });
  it('notifies only actual geometry and explicit freshness transitions', () => {
    const input = source(); const stream = new ScopeStream(input.port, context, 'borrowed');
    const changes = vi.fn(); stream.geometry.subscribe(changes);
    input.emit(); stream.setFreshness('stale'); stream.setFreshness('stale'); input.emit();
    expect(changes).toHaveBeenCalledTimes(4);
    input.emit(fixtures.outOfRange);
    expect(changes.mock.lastCall?.[0]).toMatchObject({ hasPixels: false, outOfRange: true, receiver: 1 });
    stream.dispose();
  });
  it('publishes geometry only for the bound slot while sinks still see every frame', () => {
    const input = source(); const stream = new ScopeStream(input.port, context, 'borrowed');
    stream.setSlotFilter(scopeSlotKey('scope-1', 0));
    const changes = vi.fn(); stream.geometry.subscribe(changes);
    const sink = vi.fn(); stream.subscribe(sink);
    input.emit();                                    // MAIN frame
    expect(changes).toHaveBeenCalledTimes(2);        // initial null + MAIN geometry
    input.emit(fixtures.audio);                      // SUB frame
    expect(changes).toHaveBeenCalledTimes(2);        // slot filter holds MAIN geometry
    expect(sink).toHaveBeenCalledTimes(2);           // the SUB frame still reaches sinks
    expect(stream.latest(scopeSlotKey('scope-1', 1))?.seq).toBe(17);
    input.emit(fixtures.outOfRange);                 // SUB out-of-range frame
    expect(changes).toHaveBeenCalledTimes(2);
    stream.dispose();
  });
  it('a null slot filter accepts every arriving slot (unknown-authority fallback)', () => {
    const input = source(); const stream = new ScopeStream(input.port, context, 'borrowed');
    stream.setSlotFilter(scopeSlotKey('scope-1', 0));
    const changes = vi.fn(); stream.geometry.subscribe(changes);
    input.emit(fixtures.audio);
    expect(changes).toHaveBeenCalledTimes(1); // only the initial null
    stream.setSlotFilter(null);
    input.emit(fixtures.audio);
    expect(changes).toHaveBeenCalledTimes(2);
    stream.dispose();
  });
  it('reset clears latest frames and geometry for a new transport session', () => {
    const input = source(); const stream = new ScopeStream(input.port, context, 'borrowed');
    const changes = vi.fn(); stream.geometry.subscribe(changes);
    input.emit();
    stream.setSessionEpoch(8);
    expect(stream.latest(scopeSlotKey('scope-1', 0))).toBeNull();
    expect(changes.mock.lastCall?.[0]).toBeNull();
    const buffer = input.emit(withSeq(5, 9));
    const frame = stream.latest(scopeSlotKey('scope-1', 0));
    expect(frame?.epoch).toBe(7);
    expect(frame?.pixels?.buffer).toBe(buffer);
    stream.dispose();
  });
  it('rejects malformed input and clears displayed geometry without notifying sinks', () => {
    const input = source(); const malformed = vi.fn();
    const stream = new ScopeStream(input.port, context, 'borrowed', malformed);
    const sink = vi.fn(); const unsubscribe = stream.subscribe(sink);
    const geometry = vi.fn(); stream.geometry.subscribe(geometry);
    input.emit();
    input.emit([1]);
    expect(malformed).toHaveBeenCalledWith('short header');
    expect(stream.latest(scopeSlotKey('scope-1', 0))).toBeNull();
    expect(geometry.mock.lastCall?.[0]).toBeNull();
    expect(sink).toHaveBeenCalledTimes(1);
    unsubscribe(); input.emit(); expect(sink).toHaveBeenCalledTimes(1);
    stream.dispose();
  });
  it.each(['owned', 'borrowed'] as const)('detaches and closes only %s source lifecycle', (ownership) => {
    const input = source(); const stream = new ScopeStream(input.port, context, ownership);
    const retained = input.retained(); const sink = vi.fn(); stream.subscribe(sink);
    stream.dispose(); stream.dispose(); retained(emitBytes(fixtures.radio));
    expect(input.count()).toBe(0);
    expect(input.port.close).toHaveBeenCalledTimes(ownership === 'owned' ? 1 : 0);
    expect(sink).not.toHaveBeenCalled();
    expect(stream.latest(scopeSlotKey('scope-1', 0))).toBeNull();
  });
});

it('isolates throwing sinks and uses exact subscriptions during notification', () => {
  const input = source(); const stream = new ScopeStream(input.port, context, 'borrowed');
  const failure = new Error('observer'); const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const healthy = vi.fn(); const late = vi.fn(); let remove = () => {};
  remove = stream.subscribe(() => { remove(); stream.subscribe(late); throw failure; });
  const unsubscribe = stream.subscribe(healthy);
  input.emit();
  expect(healthy).toHaveBeenCalledTimes(1); expect(late).not.toHaveBeenCalled();
  expect(warn).toHaveBeenCalledWith('Scope stream subscriber failed', failure);
  input.emit(); expect(healthy).toHaveBeenCalledTimes(2); expect(late).toHaveBeenCalledTimes(1);
  unsubscribe(); unsubscribe(); input.emit(); expect(healthy).toHaveBeenCalledTimes(2);
  stream.dispose(); warn.mockRestore();
});

it('delivers one post-boundary selected geometry receipt without changing the shared cache or reactive geometry', () => {
  const input = source(); const stream = new ScopeStream(input.port, context, 'borrowed');
  stream.setSlotFilter(scopeSlotKey('scope-1', 0));
  const changes = vi.fn(); stream.geometry.subscribe(changes); input.emit();
  const cached = stream.latest(scopeSlotKey('scope-1', 0)); const sink = vi.fn();
  const cancel = stream.subscribeNextGeometry(stream.receipt, sink);
  expect(sink).not.toHaveBeenCalled(); expect(stream.latest(scopeSlotKey('scope-1', 0))).toBe(cached);
  expect(changes).toHaveBeenCalledTimes(2);
  input.emit(fixtures.audio); expect(sink).not.toHaveBeenCalled();
  input.emit(); expect(sink).toHaveBeenCalledTimes(1);
  expect(sink.mock.lastCall).toHaveLength(1);
  expect(sink.mock.lastCall?.[0]).toEqual(changes.mock.lastCall?.[0]);
  expect(sink.mock.lastCall?.[0]).not.toHaveProperty('pixels');
  expect(changes).toHaveBeenCalledTimes(2);
  input.emit(); expect(sink).toHaveBeenCalledTimes(1);
  cancel(); cancel(); stream.dispose();
});

it('cancels a geometry receipt wait and refuses epoch-zero or malformed geometry', () => {
  const input = source(); const stream = new ScopeStream(input.port, context, 'borrowed');
  const cancelled = vi.fn(); const cancel = stream.subscribeNextGeometry(stream.receipt, cancelled);
  cancel(); cancel(); input.emit(); expect(cancelled).not.toHaveBeenCalled();
  const sink = vi.fn(); stream.subscribeNextGeometry(stream.receipt, sink);
  stream.setSessionEpoch(0); input.emit(); expect(sink).not.toHaveBeenCalled();
  stream.setSessionEpoch(8); input.emit([1]); expect(sink).not.toHaveBeenCalled();
  input.emit(); expect(sink).toHaveBeenCalledTimes(1);
  input.emit(); expect(sink).toHaveBeenCalledTimes(1);
  stream.dispose();
});

it('advances selected geometry receipt only on a matching valid-session frame and retains its floor across resets', () => {
  const input = source(); const stream = new ScopeStream(input.port, context, 'borrowed');
  stream.setSlotFilter(scopeSlotKey('scope-1', 0));
  expect(stream.geometryReceipt).toBe(0);
  input.emit(); expect(stream.geometryReceipt).toBe(1);
  input.emit(fixtures.audio); expect(stream.receipt).toBe(2); expect(stream.geometryReceipt).toBe(1);
  stream.setFreshness('stale'); expect(stream.geometryReceipt).toBe(1);
  stream.reset(); expect(stream.geometryReceipt).toBe(1);
  stream.setSessionEpoch(0); input.emit(); expect(stream.receipt).toBe(3); expect(stream.geometryReceipt).toBe(1);
  stream.setSessionEpoch(8); input.emit(fixtures.audio); expect(stream.geometryReceipt).toBe(1);
  const next = vi.fn(); stream.subscribeNextGeometry(stream.geometryReceipt, next);
  input.emit(); expect(stream.geometryReceipt).toBe(5); expect(next).toHaveBeenCalledTimes(1);
  stream.dispose();
});

it('exposes selected cached pixel presence through grants, health ticks and pixel gaps', () => {
  const input = source(); const stream = new ScopeStream(input.port, context, 'borrowed');
  stream.setSlotFilter(scopeSlotKey('scope-1', 0));
  expect(stream.lastFrameHasPixels).toBe(false);
  input.emit(); input.emit();
  expect(stream.lastFrameHasPixels).toBe(true);
  stream.handleMessage({ type: 'granted', sourceId: 'scope-1', receiver: 0,
    view: { startHz: 14_000_000, endHz: 14_050_000 }, width: 3, fps: 20 });
  stream.setFreshness('fresh');
  expect(stream.lastFrameHasPixels).toBe(true);
  input.emit(fixtures.outOfRange);
  expect(stream.lastFrameHasPixels).toBe(true); // Unselected SUB cannot retire MAIN.
  stream.setSlotFilter(scopeSlotKey('scope-1', 1));
  input.emit(fixtures.outOfRange);
  expect(stream.lastFrameHasPixels).toBe(false);
  stream.reset(); expect(stream.lastFrameHasPixels).toBe(false);
  stream.dispose();
});
