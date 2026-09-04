/**
 * MOR-2309 — the seven DUAL intents bind once to the real frontend facades.
 *
 * `SemanticRadioSurfaces` binds all seven `VfoSurface` props by name
 * (`onEqualizeVfos={vfo.onEqual}`, …). Its own component test mocks
 * `../command-bus`, so a renamed or deleted handler there would be invisible:
 * the mock would keep answering. This file closes that gap the same way
 * `tx-aux-command-bus.isolated.test.ts` does — load the real module, read the
 * names off the real wiring source, and assert each one exists AND emits the
 * command the radio expects.
 *
 * The pair that matters most is `onQuickSplit` / `onQuickDw`: they name the
 * frontend composite intents (epic #774). This proves dispatch into the
 * typed intent facade only, not provider consumption. A
 * silent rename would leave the deck's quick buttons wired to `undefined` —
 * or, worse, to a neighbouring VFO command that moves a frequency the
 * operator did not ask to move.
 *
 * Each test's doc line names the mutation it exists to kill.
 */
import { readFileSync } from 'node:fs';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const h = vi.hoisted(() => ({
  patchActiveReceiver: vi.fn(),
  patchRadioState: vi.fn(),
  patchReceiver: vi.fn(),
}));

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
  patchActiveReceiver: h.patchActiveReceiver,
  patchRadioState: h.patchRadioState,
  patchReceiver: h.patchReceiver,
}));
vi.mock('$lib/stores/capabilities.svelte', () => ({
  getCapabilities: vi.fn(() => ({
    receivers: 2, vfoScheme: 'main_sub',
    capabilities: [
      'dual_rx', 'dual_watch', 'split', 'main_sub_tracking',
      'vfo_swap', 'vfo_equalize', 'speech',
    ],
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
import { audioManager } from '$lib/audio/audio-manager';
import {
  makeSystemHandlers, makeVfoHandlers,
} from '$lib/runtime/commands/panel-commands';

const wiringSource = readFileSync('src/components-v2/wiring/SemanticRadioSurfaces.svelte', 'utf8');
const commandSource = readFileSync('src/lib/runtime/commands/panel-commands.ts', 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/\/\/.*$/gm, '');

/**
 * The seven surface props and the exact frontend intent tuple each facade
 * receives. Read as pairs so a cross-wiring fails on the command, not merely
 * on "something was called".
 */
const OPS = [
  { prop: 'onSelectMainReceiver', owner: 'vfo', handler: 'onMainVfoClick', command: ['set_vfo', { vfo: 'MAIN' }], focus: 'main' },
  { prop: 'onSelectSubReceiver', owner: 'vfo', handler: 'onSubVfoClick', command: ['set_vfo', { vfo: 'SUB' }], focus: 'sub' },
  { prop: 'onEqualizeVfos', owner: 'vfo', handler: 'onEqual', command: ['vfo_equalize', {}] },
  { prop: 'onSwapVfos', owner: 'vfo', handler: 'onSwap', command: ['vfo_swap', {}] },
  { prop: 'onQuickSplit', owner: 'vfo', handler: 'onQuickSplit', command: ['quick_split', {}] },
  { prop: 'onQuickDualWatch', owner: 'vfo', handler: 'onQuickDw', command: ['quick_dualwatch', {}] },
  { prop: 'onSpeak', owner: 'systemIntents', handler: 'onSpeak', command: ['speak', { mode: 0 }] },
] as const;

type OpHandlerName = (typeof OPS)[number]['handler'];

function handlerFor(handler: OpHandlerName): () => void {
  const vfo = makeVfoHandlers();
  switch (handler) {
    case 'onMainVfoClick': return vfo.onMainVfoClick;
    case 'onSubVfoClick': return vfo.onSubVfoClick;
    case 'onEqual': return vfo.onEqual;
    case 'onSwap': return vfo.onSwap;
    case 'onQuickSplit': return vfo.onQuickSplit;
    case 'onQuickDw': return vfo.onQuickDw;
    case 'onSpeak': return makeSystemHandlers().onSpeak;
  }
}

beforeEach(() => {
  vi.mocked(sendCommand).mockClear();
  vi.mocked(audioManager.setAudioConfig).mockClear();
  h.patchActiveReceiver.mockClear();
  h.patchRadioState.mockClear();
  h.patchReceiver.mockClear();
});

describe('the wiring binds every VFO op to a handler the real command bus provides', () => {
  // Kills: a prop bound to `vfo.onQuickDW` (or any other typo) — Svelte would
  // pass `undefined` silently and the button would do nothing at runtime.
  it.each(OPS)('$prop is bound to $owner.$handler in the real wiring source', ({ prop, owner, handler }) => {
    expect(wiringSource).toContain(`${prop}={${owner}.${handler}}`);
  });

  // Kills: a rename/removal in command-bus.ts that the mocked component test
  // cannot see.
  it.each(OPS)('$owner.$handler exists and is callable', ({ owner, handler }) => {
    expect(typeof handlerFor(handler), owner).toBe('function');
  });

  // Kills: cross-wiring. Each handler must emit ITS command and no other —
  // "quick split fired something" is not the claim; "quick split fired
  // quick_split" is.
  it.each(OPS)('$owner.$handler emits exactly $command.0 once with no side-channel mutation', ({ handler, command, ...entry }) => {
    handlerFor(handler)();
    expect(vi.mocked(sendCommand).mock.calls).toEqual([[command[0], command[1]]]);
    expect(vi.mocked(audioManager.setAudioConfig).mock.calls).toEqual(
      'focus' in entry ? [[{ focus: entry.focus }]] : [],
    );
    expect(h.patchActiveReceiver.mock.calls).toEqual([]);
    expect(h.patchRadioState.mock.calls).toEqual([]);
    expect(h.patchReceiver.mock.calls).toEqual([]);
    expect(command[0]).not.toMatch(/ptt|key|start_tx|stop_tx|tune/i);
  });

  // R9. None of the seven may touch a key path: the transmitter is keyed only
  // through the App TX controller, never from a VFO action.
  it('no VFO op emits a TX key/unkey command', () => {
    for (const { handler } of OPS) {
      handlerFor(handler)();
    }
    const sent = vi.mocked(sendCommand).mock.calls.map((c) => String(c[0]));
    for (const forbidden of ['set_ptt', 'ptt', 'start_tx', 'stop_tx', 'tx']) {
      expect(sent, forbidden).not.toContain(forbidden);
    }
  });

  it('the facade source cannot bypass typed intents into provider/transport APIs', () => {
    expect(commandSource).not.toMatch(/rigctld|WebSocket|fetch\s*\(|sendCommand|\$lib\/transport/);
    expect(commandSource).not.toMatch(/from\s+['"][^'"]*provider[^'"]*['"]/);
  });
});

describe('placement — the ops ride with the radio-wide facts', () => {
  // Kills: binding the ops onto the per-receiver strip surface, which would
  // render one equalize button PER RECEIVER in the dual-receiver cockpit. The
  // strip mount is the one that sets `showRadioWideFacts={false}`; the two
  // legitimate mounts are the cockpit's global row and the single composition.
  it.each(OPS)('$prop is bound exactly twice — the global row and the single composition', ({ prop }) => {
    const occurrences = wiringSource.split(`${prop}={`).length - 1;
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
