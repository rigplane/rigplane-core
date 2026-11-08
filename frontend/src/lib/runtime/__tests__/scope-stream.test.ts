import { describe, expect, it, vi } from 'vitest';
import fixtures from './fixtures/scope-wire.json';
import { ScopeStream } from '../scope-stream';
import type { ScopeMessageSource } from '../scope-stream';

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

describe('ScopeStream', () => {
  it('keeps latest per source/receiver outside the geometry store over 100 frames', () => {
    const input = source();
    const stream = new ScopeStream(input.port, context, 'borrowed');
    let writes = 0;
    const unwatch = stream.geometry.subscribe(() => { writes++; });
    const sink = vi.fn(); stream.subscribe(sink);
    for (let seq = 0; seq < 100; seq++) {
      const bytes = [...fixtures.radio]; bytes[11] = seq;
      const buffer = input.emit(bytes);
      expect(stream.latest('scope-1', 0)?.pixels?.buffer).toBe(buffer);
    }
    expect(writes).toBe(2); // initial null + first geometry
    expect(sink).toHaveBeenCalledTimes(100);
    input.emit(fixtures.audio);
    expect(stream.latest('scope-1', 0)?.seq).toBe(99);
    expect(stream.latest('scope-1', 1)?.seq).toBe(17);
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
  it('rejects malformed input without replacing latest or notifying sinks', () => {
    const input = source(); const malformed = vi.fn();
    const stream = new ScopeStream(input.port, context, 'borrowed', malformed);
    const sink = vi.fn(); const unsubscribe = stream.subscribe(sink);
    input.emit(); const frame = stream.latest('scope-1', 0);
    input.emit([1]);
    expect(malformed).toHaveBeenCalledWith('short header');
    expect(stream.latest('scope-1', 0)).toBe(frame);
    expect(sink).toHaveBeenCalledTimes(1);
    unsubscribe(); input.emit(); expect(sink).toHaveBeenCalledTimes(1);
    stream.dispose();
  });
  it.each(['owned', 'borrowed'] as const)('detaches and closes only %s source lifecycle', (ownership) => {
    const input = source(); const stream = new ScopeStream(input.port, context, ownership);
    const retained = input.retained(); const sink = vi.fn(); stream.subscribe(sink);
    stream.dispose(); stream.dispose(); retained(Uint8Array.from(fixtures.radio).buffer);
    expect(input.count()).toBe(0);
    expect(input.port.close).toHaveBeenCalledTimes(ownership === 'owned' ? 1 : 0);
    expect(sink).not.toHaveBeenCalled();
    expect(stream.latest('scope-1', 0)).toBeNull();
  });
});
