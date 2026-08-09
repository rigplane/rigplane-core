/**
 * MOR-1262 decomposition slice 9A (MOR-1296) — SAFETY CONSTRAINT 1:
 * THE CW SURFACE MUST NOT BECOME A KEY PATH.
 *
 * Break-in keys the transmitter. The `cwKeyer` fact group is therefore a PURE
 * READ-MODEL: deriving, validating or serializing it must emit no command, do
 * no transport I/O, and mutate no radio store — there must exist no code path
 * from "build the view model" to "the radio transmits". The three-pin
 * discipline is MOR-1274's (slice 3A), applied to the CW command seam instead
 * of the audio one, and deliberately non-interchangeable:
 *
 *  1. LOAD-TIME — spy call counts snapshotted at module-import time, BEFORE
 *     any `mockClear()`. Covers the whole transitive import closure of the
 *     adapter and the contract, and is the only pin that would see a side
 *     effect fired at import rather than at derive.
 *  2. BEHAVIOURAL — a full derive + validate + serialize round trip over a
 *     state whose break-in is ARMED (`full`/QSK) and whose TX permit is
 *     ALLOWED — the most dangerous input this family has — must leave the
 *     control transport at zero calls. A model with no `cwKeyer` group would
 *     make "zero calls" vacuous, so the pin asserts the group was produced.
 *  3. STRUCTURAL — the adapter's own source text imports no transport and no
 *     command module, and contains none of the CW command verbs the shipped
 *     handlers send (`set_break_in`, `cw_auto_tune`, …). SOURCE-LOCAL and
 *     NON-TRANSITIVE by construction; pins 1 and 2 are the closure-wide ones.
 *
 * Pool: `isolated` (MOR-1272). Module-scope `vi.mock` is exactly the
 * shared-state shape that is order-dependent under the fast pool's
 * `isolate: false` — a sibling that imports the real transport first would
 * pin it in the module cache and turn the hoisted mock into a no-op.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';

vi.mock('$lib/transport/ws-client', () => ({
  sendCommand: vi.fn(), connectWs: vi.fn(), disconnectWs: vi.fn(),
}));

import { sendCommand } from '$lib/transport/ws-client';
import type { Capabilities } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';
import { validateRadioViewModel } from '../../../../semantic/radio-view-model';
import { toRadioViewModel } from '../radio-view-model-adapter';

/** PIN 1 — the load-time snapshot, read at module scope so it runs after the
 *  imports above have fully evaluated the adapter's and the contract's entire
 *  transitive closure and BEFORE `beforeEach` erases the evidence. */
const loadTimeCommandCalls = vi.mocked(sendCommand).mock.calls.length;

const fresh: FieldStatus = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };

/** The most dangerous input this family has: full break-in (QSK — the key
 *  transmits immediately), on an in-band frequency with an observed TX
 *  target, so the model-level permit is positively `allowed` and nothing
 *  gates the affordance. */
const caps = {
  model: 'fixture', scope: false, audio: false, tx: true,
  capabilities: ['tx', 'cw', 'break_in', 'apf', 'twin_peak'],
  receivers: 1, vfoScheme: 'single', freqRanges: [], modes: ['CW'], filters: [],
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: [{ name: '20m', start: 14000000, end: 14350000 }],
  scopeSource: 'hardware', audioFftAvailable: false,
} as unknown as Capabilities;

const state = {
  active: 'MAIN', split: false, dualWatch: false, ptt: false,
  txTarget: { status: 'known', receiver: 'MAIN', slot: null, frequencyHz: 14030000 },
  main: {
    freqHz: 14030000, mode: 'CW', filter: 1, dataMode: 0, afLevel: 1, rfGain: 1,
    squelch: 0, sMeter: 0, apfTypeLevel: 1, twinPeakFilter: false,
  },
  breakIn: 2, breakInDelay: 0, keySpeed: 32, cwPitch: 600, dashRatio: 0,
  fieldStatus: {
    active: fresh, txTarget: fresh, 'main.freqHz': fresh, 'main.mode': fresh,
    breakIn: fresh, breakInDelay: fresh, keySpeed: fresh, cwPitch: fresh, dashRatio: fresh,
    'main.apfTypeLevel': fresh, 'main.twinPeakFilter': fresh,
  },
} as unknown as ServerState;

/** Comments are stripped before the structural assertions: the adapter
 *  DOCUMENTS this prohibition in prose (and names `cw_auto_tune` while doing
 *  so), and a naive text search would match the doctrine instead of the code. */
function code(path: string): string {
  return readFileSync(path, 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/.*$/gm, '$1');
}

const adapterSource = code('src/lib/runtime/adapters/radio-view-model-adapter.ts');
const contractSource = code('src/semantic/radio-view-model.ts');

describe('cwKeyer fact construction is not a key path (MOR-1296, safety constraint 1)', () => {
  beforeEach(() => {
    vi.mocked(sendCommand).mockClear();
  });

  // ── Pin 1: load-time, closure-wide ───────────────────────────────────────
  it('importing the adapter and contract sends no command at module-load time', () => {
    expect(loadTimeCommandCalls).toBe(0);
  });

  // ── Pin 2: behavioural, closure-wide ─────────────────────────────────────
  it('derives, validates and serializes an ARMED break-in model with zero commands sent', () => {
    const view = validateRadioViewModel(toRadioViewModel(state, caps));
    // The group really was produced, under a permit that does not gate it —
    // otherwise "zero calls" would prove nothing.
    expect(view.cwKeyer).toBeDefined();
    expect(view.cwKeyer!.breakIn.reading).toEqual({ status: 'known', value: 'full' });
    expect(view.txPermit.status).toBe('allowed');
    expect(view.disabledReasons.map((r) => r.field)).not.toContain('cwKeyer.breakIn');
    JSON.stringify(view);

    expect(sendCommand).not.toHaveBeenCalled();
  });

  it('repeated derivation stays side-effect free and stable', () => {
    const first = toRadioViewModel(state, caps);
    const second = toRadioViewModel(state, caps);
    expect(second).toEqual(first);
    expect(sendCommand).not.toHaveBeenCalled();
  });

  it('sends nothing even when the derivation runs against a TX-permitted, mode-matched CW state', () => {
    const view = validateRadioViewModel(toRadioViewModel(state, caps));
    expect(view.modeFilter!.currentMode.reading).toEqual({ status: 'known', value: 'CW' });
    // APF is in its permitted mode, so nothing disables it — the "enabled"
    // branch of the mutex is exercised here, not just the disabled one.
    expect(view.disabledReasons.map((r) => r.field)).not.toContain('cwKeyer.apf');
    expect(sendCommand).not.toHaveBeenCalled();
  });

  // ── Pin 3: structural, source-local ──────────────────────────────────────
  it('the adapter imports no transport and no command module', () => {
    expect(adapterSource).not.toMatch(/from\s+'\$lib\/transport\//);
    expect(adapterSource).not.toMatch(/from\s+'[^']*commands\//);
    expect(adapterSource).not.toMatch(/from\s+'[^']*components-v2\//);
    expect(adapterSource).not.toMatch(/\bsendCommand\b/);
  });

  it('the adapter contains none of the CW command verbs the shipped handlers send', () => {
    for (const verb of [
      'set_break_in', 'set_break_in_delay', 'cw_auto_tune', 'set_key_speed',
      'set_cw_pitch', 'set_apf', 'set_twin_peak', 'set_dash_ratio', 'set_ptt',
    ]) {
      expect(adapterSource).not.toContain(verb);
    }
  });

  it('the contract carries no command vocabulary and no transport import', () => {
    expect(contractSource).not.toMatch(/from\s+'\$lib\/transport\//);
    expect(contractSource).not.toMatch(/from\s+'\$lib\/runtime\//);
    expect(contractSource).not.toContain('set_break_in');
    expect(contractSource).not.toContain('cw_auto_tune');
  });

  /**
   * SAFETY CONSTRAINT 2 — NO SECOND PERMIT, structurally. The CW derivation
   * must consume the model's single `txPermit`, so `getFrequencyPermit` (the
   * one shipped permit derivation) must appear in this file exactly where
   * MOR-1294's band family already calls it, and nowhere in the CW path.
   */
  it('the CW derivation contains no permit re-derivation of its own', () => {
    const cwSection = adapterSource.slice(
      adapterSource.indexOf('function deriveCwKeyer('),
      adapterSource.indexOf('export interface RxAudioSnapshot'),
    );
    expect(cwSection.length).toBeGreaterThan(0);
    expect(cwSection).not.toContain('getFrequencyPermit');
    expect(cwSection).not.toContain('txBands');
    // R9: TX truth comes from the authority-derived permit, never `state.ptt`.
    expect(cwSection).not.toMatch(/\bptt\b/);
  });
});
