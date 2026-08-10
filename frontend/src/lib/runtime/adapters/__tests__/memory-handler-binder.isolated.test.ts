import { describe, expect, it, vi } from 'vitest';

const factory = vi.hoisted(() => vi.fn(() => Object.freeze({ onRecall: vi.fn(), onStore: vi.fn(), onClear: vi.fn() })));
vi.mock('$lib/runtime/commands/panel-commands', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/runtime/commands/panel-commands')>(),
  makeMemoryHandlers: factory,
}));
vi.mock('$lib/runtime/frontend-runtime', () => ({ runtime: { state: null, caps: null } }));

import { getMemoryHandlers } from '../panel-adapters';

describe('MOR-1409 A05a memory handler binder', () => {
  it('creates one canonical memory handler object and returns its exact identity', () => {
    const first = getMemoryHandlers();
    const second = getMemoryHandlers();
    expect(factory).toHaveBeenCalledTimes(1);
    expect(first).toBe(second);
    expect(first).toBe(factory.mock.results[0]!.value);
  });
});
