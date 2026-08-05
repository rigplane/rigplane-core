/**
 * MOR-1262 decomposition slice 3A (MOR-1274) — SAFETY CONSTRAINT 1.
 *
 * The `rxAudio` fact group is a PURE READ-MODEL. Constructing or serializing
 * it must never open, start, probe or configure the audio path: audio lifetime
 * is App-owned (MOR-1058) and "opens transport on mount" is the recorded
 * MOR-972 P0 finding this family must not reintroduce. The snapshot is an
 * INPUT precisely so the read-model has nothing to reach for.
 *
 * Three pins with DIFFERENT, deliberately stated coverage — they are not
 * interchangeable, and no one of them is sufficient (MOR-1274 verification F1):
 *  1. LOAD-TIME — spy call counts snapshotted at module-import time, BEFORE any
 *     `mockClear()`. Covers the whole transitive import closure of the adapter
 *     and the contract, and it is the only pin that sees a side effect fired at
 *     import rather than at derive. This is the MOR-972 P0 shape ("opens
 *     transport on mount"), and it is reachable without editing this slice at
 *     all — any of the five transitive modules could acquire it later.
 *  2. BEHAVIOURAL — the audio manager, the control transport, `AudioContext`
 *     and `localStorage` are all spied; a full derive + validate + serialize
 *     round trip must leave every one of them at zero calls. Covers derive-time
 *     contact anywhere in the closure, including lazy getters; blind to
 *     anything that already happened at import (hence pin 1).
 *  3. STRUCTURAL — the adapter's and contract's own source text import none of
 *     those modules. This pin is SOURCE-LOCAL and NON-TRANSITIVE: it reads two
 *     files and matches `$lib/...` specifiers, so a relative-path import or a
 *     dependency-of-a-dependency slips past it. It is a fast, precise guard on
 *     the two files this slice owns, not a closure-wide proof (pins 1 and 2 are).
 *
 * Pool: `isolated` (MOR-1272). Module-scope `vi.mock` plus `vi.stubGlobal` on
 * `AudioContext`/`localStorage` are exactly the shared-state shapes that are
 * order-dependent under the fast pool's `isolate: false`.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';

vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    rxEnabled: false,
    startRx: vi.fn(), stopRx: vi.fn(), startTx: vi.fn(), stopTx: vi.fn(),
    setRxVolume: vi.fn(), setAudioConfig: vi.fn(),
  },
}));

vi.mock('$lib/transport/ws-client', () => ({
  sendCommand: vi.fn(), connectWs: vi.fn(), disconnectWs: vi.fn(),
}));

import { audioManager } from '$lib/audio/audio-manager';
import { sendCommand } from '$lib/transport/ws-client';
import type { Capabilities } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';
import { validateRadioViewModel } from '../../../../semantic/radio-view-model';
import { toRadioViewModel, type RxAudioSnapshot } from '../radio-view-model-adapter';

/**
 * PIN 1 — the load-time snapshot. Read at module scope, so it runs after the
 * imports above have fully evaluated the adapter's and the contract's entire
 * transitive closure, and BEFORE `beforeEach`'s `mockClear()` erases the
 * evidence. Anything in that closure that touches a seam at import time is
 * recorded here and nowhere else.
 */
const loadTimeCalls = [
  audioManager.startRx, audioManager.stopRx, audioManager.setRxVolume,
  audioManager.setAudioConfig, sendCommand,
].map((spy) => vi.mocked(spy).mock.calls.length);

/** Comments are stripped before the structural assertions below: both files
 *  DOCUMENT this prohibition in prose ("never opens `audio-manager`…"), and a
 *  naive text search would match the doctrine instead of the code. */
function code(path: string): string {
  return readFileSync(path, 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/.*$/gm, '$1');
}

const adapterSource = code('src/lib/runtime/adapters/radio-view-model-adapter.ts');
const contractSource = code('src/semantic/radio-view-model.ts');

const fresh: FieldStatus = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };

const caps = {
  model: 'fixture', scope: false, audio: true, tx: true,
  capabilities: ['audio', 'tx', 'mod_input_routing', 'af_level', 'dual_rx'],
  receivers: 1, vfoScheme: 'single', freqRanges: [], modes: [], filters: [],
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: [], scopeSource: 'hardware', audioFftAvailable: false,
  audioTxRequiredModInputSource: 5,
} as unknown as Capabilities;

const state = {
  active: 'MAIN', split: false, dualWatch: false, ptt: false,
  txTarget: { status: 'unknown', reason: 'not-observed' },
  main: {
    freqHz: 14195000, mode: 'USB', filter: 1, dataMode: 0, afLevel: 0.31, sMeter: 120,
  },
  dataOffModInput: 0,
  fieldStatus: { active: fresh, dataOffModInput: fresh, 'main.afLevel': fresh },
} as unknown as ServerState;

const snapshot: RxAudioSnapshot = {
  muted: false, rxEnabled: true, volume: 42, connected: true,
  routing: { focus: 'sub', splitStereo: true },
};

const audioContextCtor = vi.fn();
class SpyAudioContext {
  constructor(...args: unknown[]) { audioContextCtor(...args); }
}
const storage = {
  getItem: vi.fn(() => null), setItem: vi.fn(), removeItem: vi.fn(), clear: vi.fn(),
  key: vi.fn(() => null), length: 0,
};

describe('rxAudio fact construction never touches the audio path (MOR-1058 / MOR-972 P0)', () => {
  beforeEach(() => {
    vi.stubGlobal('AudioContext', SpyAudioContext);
    vi.stubGlobal('webkitAudioContext', SpyAudioContext);
    vi.stubGlobal('localStorage', storage);
    audioContextCtor.mockClear();
    for (const spy of [
      audioManager.startRx, audioManager.stopRx, audioManager.startTx, audioManager.stopTx,
      audioManager.setRxVolume, audioManager.setAudioConfig,
      sendCommand, storage.getItem, storage.setItem,
    ]) {
      vi.mocked(spy).mockClear();
    }
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  // ── Pin 1: load-time, closure-wide ───────────────────────────────────────
  it('importing the adapter and contract calls no audio or transport seam', () => {
    expect(loadTimeCalls).toEqual([0, 0, 0, 0, 0]);
  });

  it('derives, validates and serializes the group with zero audio-manager calls', () => {
    const view = validateRadioViewModel(toRadioViewModel(state, caps, null, snapshot));
    // The group really was produced — otherwise "zero calls" proves nothing.
    expect(view.rxAudio).toBeDefined();
    expect(view.rxAudio!.monitorMode).toBe('live');
    JSON.stringify(view);

    expect(audioManager.startRx).not.toHaveBeenCalled();
    expect(audioManager.stopRx).not.toHaveBeenCalled();
    expect(audioManager.startTx).not.toHaveBeenCalled();
    expect(audioManager.stopTx).not.toHaveBeenCalled();
    expect(audioManager.setRxVolume).not.toHaveBeenCalled();
    expect(audioManager.setAudioConfig).not.toHaveBeenCalled();
  });

  it('sends no control command while building the group', () => {
    validateRadioViewModel(toRadioViewModel(state, caps, null, snapshot));
    expect(sendCommand).not.toHaveBeenCalled();
  });

  it('constructs no AudioContext, in either vendor spelling', () => {
    validateRadioViewModel(toRadioViewModel(state, caps, null, snapshot));
    expect(audioContextCtor).not.toHaveBeenCalled();
  });

  it('reads no browser storage — routing prefs arrive through the snapshot, not localStorage', () => {
    const view = validateRadioViewModel(toRadioViewModel(state, caps, null, snapshot));
    expect(view.rxAudio!.routingFocus.reading).toEqual({ status: 'known', value: 'sub' });
    expect(storage.getItem).not.toHaveBeenCalled();
    expect(storage.setItem).not.toHaveBeenCalled();
  });

  it('repeated derivation is side-effect free and stable', () => {
    const first = toRadioViewModel(state, caps, null, snapshot);
    const second = toRadioViewModel(state, caps, null, snapshot);
    expect(second).toEqual(first);
    expect(audioContextCtor).not.toHaveBeenCalled();
    expect(sendCommand).not.toHaveBeenCalled();
    expect(audioManager.startRx).not.toHaveBeenCalled();
  });

  // ── Structural pin: the handles are not even importable from here ────────
  it('the adapter imports no audio or transport module', () => {
    expect(adapterSource).not.toMatch(/from\s+'\$lib\/audio\//);
    expect(adapterSource).not.toMatch(/from\s+'\$lib\/transport\//);
    expect(adapterSource).not.toMatch(/\baudioManager\b/);
    expect(adapterSource).not.toMatch(/\bAudioContext\b/);
    expect(adapterSource).not.toMatch(/\blocalStorage\b/);
  });

  it('the contract imports no audio, transport or adapter module', () => {
    expect(contractSource).not.toMatch(/from\s+'\$lib\/audio\//);
    expect(contractSource).not.toMatch(/from\s+'\$lib\/transport\//);
    expect(contractSource).not.toMatch(/from\s+'\$lib\/runtime\//);
    expect(contractSource).not.toMatch(/\bAudioContext\b/);
  });
});
