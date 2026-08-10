/**
 * MOR-1409 A13a — the two missing adapter-layer handler accessors.
 *
 * `makeKeyboardHandlers` (needed by all three layouts) and
 * `makeSystemHandlers` (needed by `RadioLayout`) had no sanctioned
 * adapter-layer path at exact main `674fb8f3`: they are absent from
 * `panel-adapters.ts` AND from `bindSemanticSurfaceHandlers()`'s frozen
 * 16-member object. That gap — not LOC — is what fired A13's split
 * (correction 5246842617 §2), and A13a closes it so A13b compiles on arrival.
 *
 * The grant is RESTRICTED: these two accessors, nothing else in the file.
 * Each test names the mutation it kills.
 */
import { describe, expect, it, vi } from 'vitest';

const factories = vi.hoisted(() => ({
  makeKeyboardHandlers: vi.fn(() => Object.freeze({ family: 'keyboard' })),
  makeSystemHandlers: vi.fn(() => Object.freeze({ family: 'system' })),
}));

vi.mock('$lib/runtime/commands/panel-commands', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/runtime/commands/panel-commands')>(),
  ...factories,
}));
vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: { get state() { return null; }, get caps() { return null; } },
}));
vi.mock('$lib/runtime/tx-controller/app-host', () => ({
  getAppTxController: () => null,
}));

import { getKeyboardHandlers, getSystemHandlers } from '../panel-adapters';

describe('panel-adapters keyboard/system accessors (MOR-1409 A13a)', () => {
  // Kills: never adding the accessors — layouts would have to import the
  // command module directly, leaving A15 a residue it is not scoped to remove.
  it('exposes a keyboard handler accessor delegating to the command factory', () => {
    expect(getKeyboardHandlers()).toEqual({ family: 'keyboard' });
  });

  it('exposes a system handler accessor delegating to the command factory', () => {
    expect(getSystemHandlers()).toEqual({ family: 'system' });
  });

  // Kills: minting a fresh family object per call. Every other non-binder
  // accessor in this module returns a module-scope singleton; keyboard and
  // system must follow, or a layout that re-reads them would silently swap
  // handler identity mid-life.
  it('returns the same singleton on every call, like its 14 siblings', () => {
    expect(getKeyboardHandlers()).toBe(getKeyboardHandlers());
    expect(getSystemHandlers()).toBe(getSystemHandlers());
    expect(factories.makeKeyboardHandlers).toHaveBeenCalledTimes(1);
    expect(factories.makeSystemHandlers).toHaveBeenCalledTimes(1);
  });
});
