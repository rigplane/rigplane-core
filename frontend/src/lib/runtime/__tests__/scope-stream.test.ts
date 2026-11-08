import { describe, expect, it, vi } from 'vitest';
import fixtures from './fixtures/scope-wire.json';
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
  bytes[11] = seq; bytes[12] = 0; bytes[16] = pixel;
  return bytes;
};

describe('ScopeStream', () => {
  it.each(['radio', 'audio_fft'] as const)('delivers %s pixels through its sink without copying', (sourceKind) => {
    const input = source(); const stream = new ScopeStream(input.port, { ...context, source: sourceKind }, 'borrowed');
    const sink = vi.fn(); stream.subscribe(sink);
    const buffer = input.emit(sourceKind === 'radio' ? fixtures.radio : fixtures.audio);
    const [frame, slotKey] = sink.mock.lastCall!;
    expect(frame?.source).toBe(sourceKind);
    expect(frame?.pixels.buffer).toBe(buffer);
    expect(frame?.pixels.byteOffset).toBe(16);
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
    expect(frame?.epoch).toBe(8);
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
