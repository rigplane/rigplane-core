import { describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';

const factories = vi.hoisted(() => Object.fromEntries([
  'makeAgcHandlers', 'makeAntennaHandlers', 'makeAudioRoutingHandlers',
  'makeBandHandlers', 'makeCwPanelHandlers', 'makeDspHandlers',
  'makeFilterHandlers', 'makeModeHandlers', 'makeRfFrontEndHandlers',
  'makeRitXitHandlers', 'makeRxAudioHandlers', 'makeScanHandlers',
  'makeScopeControlsHandlers', 'makeTxHandlers', 'makeVfoHandlers', 'makeVoxHandlers',
].map((name) => [name, vi.fn(() => Object.freeze({ name }))])));
const tuner = vi.hoisted(() => ({
  state: { revision: 1 },
  caps: { generation: 1 },
  snapshot: Object.freeze({ phase: 'idle', radioTx: 'off' }) as Readonly<{ phase: string; radioTx: string }>,
  view: { activeReceiver: 'MAIN' },
  controller: null as unknown,
}));

vi.mock('$lib/runtime/commands/panel-commands', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/runtime/commands/panel-commands')>(),
  ...factories,
}));
vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: {
    get state() { return tuner.state; },
    get caps() { return tuner.caps; },
  },
}));
vi.mock('$lib/runtime/tx-controller/app-host', () => ({
  getAppTxController: () => tuner.controller,
}));
vi.mock('$lib/runtime/adapters/radio-view-model-adapter', () => ({
  toRadioViewModel: vi.fn(() => tuner.view),
}));

import { bindSemanticSurfaceHandlers, bindVfoTunerContext } from '../panel-adapters';

const expected = {
  agc: 'makeAgcHandlers', antenna: 'makeAntennaHandlers', audioRouting: 'makeAudioRoutingHandlers',
  band: 'makeBandHandlers', cw: 'makeCwPanelHandlers', dsp: 'makeDspHandlers',
  filter: 'makeFilterHandlers', mode: 'makeModeHandlers', rfFrontEnd: 'makeRfFrontEndHandlers',
  ritXit: 'makeRitXitHandlers', rxAudio: 'makeRxAudioHandlers', scan: 'makeScanHandlers',
  scopeControls: 'makeScopeControlsHandlers', tx: 'makeTxHandlers', vfo: 'makeVfoHandlers', vox: 'makeVoxHandlers',
} as const;

describe('semantic surface handler binder (MOR-1409 A04a)', () => {
  it('creates every canonical family exactly once and retains each exact factory object', () => {
    for (const factory of Object.values(factories)) vi.mocked(factory).mockClear();

    const bound = bindSemanticSurfaceHandlers();

    expect(Object.isFrozen(bound)).toBe(true);
    for (const [family, factoryName] of Object.entries(expected)) {
      const factory = vi.mocked(factories[factoryName]);
      expect(factory).toHaveBeenCalledTimes(1);
      expect(bound[family as keyof typeof bound]).toBe(factory.mock.results[0]!.value);
    }
  });

  it('keeps the semantic root behind the adapter binder rather than a command facade', () => {
    const source = readFileSync('src/components-v2/wiring/SemanticRadioSurfaces.svelte', 'utf8');
    expect(source).toContain("from '$lib/runtime/adapters/panel-adapters';");
    expect(source).not.toMatch(/from ['"][^'"]*(?:command-bus|panel-commands)['"]/);
    expect(source).not.toMatch(/\b(?:dispatchRadioIntent|sendCommand)\b/);
  });

  it('binds a live read-only tuner context without leaking TX mutation authority', () => {
    tuner.controller = { snapshot: vi.fn(() => tuner.snapshot) };
    const context = bindVfoTunerContext();
    const first = context.read();
    tuner.snapshot = Object.freeze({ phase: 'fault', radioTx: 'unknown' });
    const second = context.read();

    expect(Object.isFrozen(context)).toBe(true);
    expect(Object.isFrozen(first)).toBe(true);
    expect(first.tx).not.toBe(second.tx);
    expect(second.tx).toBe(tuner.snapshot);
    expect(second.view).toBe(tuner.view);
    for (const name of ['start', 'setIntent', 'release', 'resetFault']) {
      expect(context).not.toHaveProperty(name);
      expect(second).not.toHaveProperty(name);
    }
  });
});
