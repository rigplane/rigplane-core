/**
 * MOR-1321 (S3a) — the VFO-ops intents agree with the REAL command bus.
 *
 * `SemanticRadioSurfaces` binds the four new `VfoSurface` props by name
 * (`onEqualizeVfos={vfo.onEqual}`, …). Its own component test mocks
 * `../command-bus`, so a renamed or deleted handler there would be invisible:
 * the mock would keep answering. This file closes that gap the same way
 * `tx-aux-command-bus.isolated.test.ts` does — load the real module, read the
 * names off the real wiring source, and assert each one exists AND emits the
 * command the radio expects.
 *
 * The pair that matters most is `onQuickSplit` / `onQuickDw`: they are the
 * BACKEND composite triggers (epic #774, equalize-then-toggle-on, atomic). A
 * silent rename would leave the deck's quick buttons wired to `undefined` —
 * or, worse, to a neighbouring VFO command that moves a frequency the
 * operator did not ask to move.
 *
 * Each test's doc line names the mutation it exists to kill.
 */
import { readFileSync } from 'node:fs';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('$lib/transport/ws-client', () => ({ sendCommand: vi.fn() }));
vi.mock('$lib/runtime/commands/radio-intents', async () => {
  const { sendCommand } = await import('$lib/transport/ws-client');
  return { dispatchRadioIntent: ({ name, params }: { name: string; params: Record<string, unknown> }) => sendCommand(name, params) };
});
vi.mock('$lib/stores/radio.svelte', () => ({
  getActiveReceiver: vi.fn(() => null),
  getRadioState: vi.fn(() => ({
    stateContractVersion: 1, providerGeneration: 1, active: 'MAIN',
    main: { freqHz: 14_074_000 }, sub: { freqHz: 7_074_000 },
    split: false, dualWatch: false, mainSubTracking: false,
  })),
  patchActiveReceiver: vi.fn(),
  patchRadioState: vi.fn(),
  patchReceiver: vi.fn(),
}));
vi.mock('$lib/stores/capabilities.svelte', () => ({
  getCapabilities: vi.fn(() => ({
    receivers: 2, vfoScheme: 'main_sub',
    capabilities: ['dual_rx', 'dual_watch', 'split', 'main_sub_tracking'],
  })),
  capabilitiesMatchGeneration: vi.fn(() => true),
  getControlRange: vi.fn(() => null),
}));
vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    setAudioConfig: vi.fn(), startRx: vi.fn(), stopRx: vi.fn(),
    setRxVolume: vi.fn(), rxEnabled: false,
  },
}));

import { sendCommand } from '$lib/transport/ws-client';
import { makeVfoHandlers } from '$lib/runtime/commands/panel-commands';

const wiringSource = readFileSync('src/components-v2/wiring/SemanticRadioSurfaces.svelte', 'utf8');

/**
 * The four MOR-1321 surface props, and the command each one must ultimately
 * reach. Read as pairs so a cross-wiring (swap bound to equalize) fails on the
 * COMMAND, not merely on "something was called".
 */
const OPS = [
  { prop: 'onEqualizeVfos', handler: 'onEqual', command: 'vfo_equalize' },
  { prop: 'onSwapVfos', handler: 'onSwap', command: 'vfo_swap' },
  { prop: 'onQuickSplit', handler: 'onQuickSplit', command: 'quick_split' },
  { prop: 'onQuickDualWatch', handler: 'onQuickDw', command: 'quick_dualwatch' },
] as const;

beforeEach(() => { vi.mocked(sendCommand).mockClear(); });

describe('the wiring binds every VFO op to a handler the real command bus provides', () => {
  // Kills: a prop bound to `vfo.onQuickDW` (or any other typo) — Svelte would
  // pass `undefined` silently and the button would do nothing at runtime.
  it.each(OPS)('$prop is bound to vfo.$handler in the real wiring source', ({ prop, handler }) => {
    expect(wiringSource).toContain(`${prop}={vfo.${handler}}`);
  });

  // Kills: a rename/removal in command-bus.ts that the mocked component test
  // cannot see.
  it.each(OPS)('vfo.$handler exists and is callable', ({ handler }) => {
    expect(typeof (makeVfoHandlers() as Record<string, unknown>)[handler]).toBe('function');
  });

  // Kills: cross-wiring. Each handler must emit ITS command and no other —
  // "quick split fired something" is not the claim; "quick split fired
  // quick_split" is.
  it.each(OPS)('vfo.$handler emits $command', ({ handler, command }) => {
    (makeVfoHandlers() as unknown as Record<string, () => void>)[handler]();
    const sent = vi.mocked(sendCommand).mock.calls.map((c) => c[0]);
    expect(sent).toContain(command);
    // No sibling op's command rode along.
    for (const other of OPS) {
      if (other.command !== command) expect(sent).not.toContain(other.command);
    }
  });

  // R9. None of the four may touch a key path: the transmitter is keyed only
  // through the App TX controller, never from a VFO action.
  it('no VFO op emits a TX key/unkey command', () => {
    for (const { handler } of OPS) {
      (makeVfoHandlers() as unknown as Record<string, () => void>)[handler]();
    }
    const sent = vi.mocked(sendCommand).mock.calls.map((c) => String(c[0]));
    for (const forbidden of ['set_ptt', 'ptt', 'start_tx', 'stop_tx', 'tx']) {
      expect(sent, forbidden).not.toContain(forbidden);
    }
  });
});

describe('placement — the ops ride with the radio-wide facts', () => {
  // Kills: binding the ops onto the per-receiver strip surface, which would
  // render one equalize button PER RECEIVER in the dual-receiver cockpit. The
  // strip mount is the one that sets `showRadioWideFacts={false}`; the two
  // legitimate mounts are the cockpit's global row and the single composition.
  it.each(OPS)('$prop is bound exactly twice — the global row and the single composition', ({ prop }) => {
    const occurrences = wiringSource.split(`${prop}={vfo.`).length - 1;
    expect(occurrences).toBe(2);
  });

  // The strip mount stays op-free — asserted on the strip's own markup block
  // rather than on a global count, so this cannot pass by miscounting above.
  it('the per-receiver strip mount binds no ops', () => {
    const stripBlock = wiringSource.slice(
      wiringSource.indexOf('showRadioWideFacts={false}') - 400,
      wiringSource.indexOf('showRadioWideFacts={false}') + 400,
    );
    for (const { prop } of OPS) expect(stripBlock, prop).not.toContain(prop);
  });
});
