/**
 * MOR-2111 — the repeater panel wired into `SemanticRadioSurfaces` and
 * mounted by the REAL `RadioLayout` in the `desktop-v2` `repeater` zone,
 * with facts from the real view-model adapter over a mocked runtime, and
 * commands through the real `makeRepeaterHandlers` into a mocked
 * `sendCommand`.
 *
 * Isolated pool by name (`*.component.test.ts`), per the MOR-1272 doctrine.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { CommandDeliveryEvent, ControlSessionTransition } from '$lib/transport/ws-client';
import type { ManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';
import type { RxAudioTargetSnapshot } from '$lib/stores/audio.svelte';

const h = vi.hoisted(() => ({
  state: null as unknown,
  caps: null as unknown,
  authoritySubscribers: new Set<(next: {
    state: unknown; caps: unknown; session: ControlSessionTransition;
    rxAudioTarget: RxAudioTargetSnapshot;
  }) => void>(),
  audio: { muted: false, rxEnabled: true, volume: 42 },
  audioConnected: true,
  rxEnabled: true,
  txController: null as ManagedAppTxController | null,
  session: { state: 'connected' as ControlSessionTransition['state'], epoch: 1 },
  sessionSubscriber: undefined as ((event: ControlSessionTransition) => void) | undefined,
  delivery: undefined as ((event: CommandDeliveryEvent) => void) | undefined,
  transition: undefined as ((event: ControlSessionTransition) => void) | undefined,
}));

vi.mock('$lib/transport/ws-client', () => ({
  sendCommand: vi.fn(() => true),
  getControlSession: () => h.session,
  onCommandDelivery: vi.fn((handler: (event: CommandDeliveryEvent) => void) => {
    h.delivery = handler;
    return () => { if (h.delivery === handler) h.delivery = undefined; };
  }),
  onControlSessionTransition: vi.fn((handler: (event: ControlSessionTransition) => void) => {
    h.transition = handler;
    return () => { if (h.transition === handler) h.transition = undefined; };
  }),
}));
vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    get rxEnabled() { return h.rxEnabled; },
    startRx: vi.fn(), stopRx: vi.fn(), setRxVolume: vi.fn(), setAudioConfig: vi.fn(),
    onTxAudioDied: () => () => {},
  },
}));
vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: {
    onTxAudioDied: () => () => {},
    get state() { return h.state; },
    get caps() { return h.caps; },
    get controlSession() { return h.session; },
    subscribeControlSession(handler: (event: ControlSessionTransition) => void) {
      h.sessionSubscriber = handler;
      return () => { if (h.sessionSubscriber === handler) h.sessionSubscriber = undefined; };
    },
    subscribeControlAuthority(handler: (typeof h.authoritySubscribers extends Set<infer T> ? T : never)) {
      h.authoritySubscribers.add(handler);
      handler({
        state: h.state, caps: h.caps, session: h.session,
        rxAudioTarget: Object.freeze({ muted: h.audio.muted, rxEnabled: h.audio.rxEnabled }),
      });
      return () => { h.authoritySubscribers.delete(handler); };
    },
    get audio() { return h.audio; },
    get connectionAudio() { return h.audioConnected; },
    get rxEnabled() { return h.rxEnabled; },
    setVolume: vi.fn(), setMuted: vi.fn(), setRxLive: vi.fn(), setRxVolume: vi.fn(),
    get defaultScopeStatus() {
      return {
        source: null, available: false, resourceSelected: false, demand: 0,
        lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
      };
    },
    get scope() { return { hardwareScopeConnected: false }; },
  },
}));
vi.mock('$lib/runtime', async () => ({
  runtime: (await import('$lib/runtime/frontend-runtime')).runtime,
}));
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => {
    if (!h.txController) throw new Error('managed TX harness is not installed');
    return h.txController;
  },
}));
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => ({ visible: false, sourceLabel: 'LAN' }),
  getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
}));

import { sendCommand } from '$lib/transport/ws-client';
import { resetCommandLifecycle } from '$lib/stores/commands.svelte';
import { resetRadioState, setRadioState } from '$lib/stores/radio.svelte';
import { setCapabilities } from '$lib/stores/capabilities.svelte';
import { setLocale } from '$lib/i18n/store.svelte';
import HostedRadioLayoutFixture from '../../layout/__tests__/fixtures/HostedRadioLayoutFixture.svelte';
import { desktopV2Layout } from '../../../presentation/layouts/declarations';
import { readWorkspace } from '../../../presentation/workspace/contract';
import { resolveSurfacePlan, SURFACE_PLAN_CONTEXT_KEY } from '../../../presentation/workspace/resolution';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';

const fresh = {
  storePath: 'x', observed: true, freshness: 'fresh', availability: 'available',
  lastObservedMonotonic: 10,
} as const;
const unread = {
  storePath: 'x', observed: false, freshness: 'unknown', availability: 'missing',
} as const;

/** One receiver's repeater registers: tone/tsql booleans, CTCSS centiHz and
 *  the wire shift (0 simplex / 1 plus / 2 minus / 3 ARS). */
interface Repeater { tone: boolean; tsql: boolean; toneFreq: number; shift: number }
const OFF: Repeater = { tone: false, tsql: false, toneFreq: 7190, shift: 0 };
const REPEATER_LEAVES = ['repeaterTone', 'repeaterTsql', 'toneFreq', 'repeaterShift'] as const;

const slot = (freqHz: number, mode: string) => ({ freqHz, mode, filterNum: 1, dataMode: 0 });
const receiver = (hz: number, rep: Repeater) => ({
  ...slot(hz, 'FM'), vfoA: slot(hz, 'FM'), vfoB: slot(hz + 50_000, 'FM'), activeSlot: 'A',
  filter: 1, sMeter: 0, att: 0, preamp: 0, nb: false, nr: false, afLevel: 0, rfGain: 0, squelch: 0,
  repeaterTone: rep.tone, repeaterTsql: rep.tsql, toneFreq: rep.toneFreq, repeaterShift: rep.shift,
});

function dualState(options: {
  mainHz: number; subHz: number; active?: 'MAIN' | 'SUB';
  main?: Repeater; sub?: Repeater; repeaterRead?: boolean; activeRead?: boolean;
}): ServerState {
  const paths: string[] = [];
  for (const rx of ['main', 'sub']) {
    paths.push(`${rx}.freqHz`, `${rx}.mode`, `${rx}.filter`, `${rx}.activeSlot`);
    for (const v of ['vfoA', 'vfoB']) paths.push(`${rx}.${v}.freqHz`, `${rx}.${v}.mode`, `${rx}.${v}.filterNum`);
  }
  const fieldStatus: Record<string, unknown> = Object.fromEntries(paths.map((p) => [p, fresh]));
  fieldStatus.active = options.activeRead === false ? unread : fresh;
  for (const rx of ['main', 'sub']) {
    for (const leaf of REPEATER_LEAVES) {
      fieldStatus[`${rx}.${leaf}`] = options.repeaterRead === false ? unread : fresh;
    }
  }
  const active = options.active ?? 'MAIN';
  return {
    revision: 1, stateRevision: 1, freshnessRevision: 1, observationSeq: 1,
    updatedAt: '2026-09-24T00:00:00Z', tunerStatus: 0,
    active, split: false, dualWatch: false, ptt: false,
    stateContractVersion: 1, providerGeneration: 0,
    txTarget: { status: 'known', receiver: active, slot: 'A',
      frequencyHz: active === 'MAIN' ? options.mainHz : options.subHz },
    main: receiver(options.mainHz, options.main ?? OFF),
    sub: receiver(options.subHz, options.sub ?? OFF),
    connection: { rigConnected: true, radioReady: true, controlConnected: true },
    fieldStatus,
  } as unknown as ServerState;
}

const REPEATER_RANGES = [
  { start: 144_000_000, end: 148_000_000, label: '2m', repeater: true },
  { start: 430_000_000, end: 450_000_000, label: '70cm', repeater: true },
];
/** Deliberately NOT the standard 50-tone chart: stepping 71.9 up must land
 *  on 88.5 here, where the standard chart would give 74.4. */
const CHART = [6700, 7190, 8850, 10000];

function dualCaps(tags: readonly string[] = ['repeater_tone', 'tsql', 'repeater_shift']): Capabilities {
  return {
    model: 'fixture', scope: false, audio: false, tx: true, stateContractVersion: 1, providerGeneration: 0,
    capabilities: ['dual_rx', ...tags], receivers: 2, vfoScheme: 'main_sub',
    freqRanges: [{ start: 1_800_000, end: 54_000_000, label: 'HF' }, ...REPEATER_RANGES],
    ctcssTones: CHART, modes: ['USB', 'FM'], filters: ['FIL1'],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
    webrtc: { available: false, enabled: false }, txBands: [], scopeSource: null, audioFftAvailable: false,
  } as unknown as Capabilities;
}

/** IC-705-shaped: one receiver, `repeater_tone` + `tsql`, no
 *  `repeater_shift` (`rigs/ic705.toml`). */
function ic705Caps(tags: readonly string[] = ['repeater_tone', 'tsql']): Capabilities {
  return {
    model: 'IC-705', scope: false, audio: false, tx: true, stateContractVersion: 1, providerGeneration: 0,
    capabilities: [...tags], receivers: 1, vfoScheme: 'ab', vfoReadback: 'selected_unselected',
    freqRanges: [{ start: 144_000_000, end: 148_000_000, label: '2m', repeater: true }],
    ctcssTones: CHART, modes: ['FM'], filters: [],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
    webrtc: { available: false, enabled: false }, txBands: [], scopeSource: null, audioFftAvailable: false,
  } as unknown as Capabilities;
}
function ic705State(): ServerState {
  const fieldStatus: Record<string, unknown> = { active: fresh, 'main.freqHz': fresh, 'main.mode': fresh };
  for (const leaf of REPEATER_LEAVES) fieldStatus[`main.${leaf}`] = fresh;
  return {
    active: 'MAIN',
    main: { freqHz: 145_500_000, mode: 'FM', repeaterTone: true, repeaterTsql: false, toneFreq: 8850, repeaterShift: 0 },
    fieldStatus,
  } as unknown as ServerState;
}

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;
let txHarness: ManagedAppTxHarness;

function use(state: ServerState, caps: Capabilities, runtimeState = state, runtimeCaps = caps): void {
  resetRadioState();
  setRadioState(state);
  setCapabilities(caps);
  h.state = runtimeState;
  h.caps = runtimeCaps;
}

function renderHosted(): void {
  target = document.createElement('div');
  document.body.appendChild(target);
  const context = new Map<unknown, unknown>([[SURFACE_PLAN_CONTEXT_KEY, () =>
    resolveSurfacePlan(desktopV2Layout, readWorkspace({ version: 1 }).workspace)]]);
  component = mount(HostedRadioLayoutFixture, { target, props: { skinId: 'desktop-v2' }, context });
  flushSync();
}

const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
const commands = () => vi.mocked(sendCommand).mock.calls.map(([name, params]) => ({ name, params }));
const press = (node: HTMLElement) => {
  node.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  flushSync();
};
const key = (testid: string) => q<HTMLButtonElement>(`[data-testid="${testid}"]`);
const surface = () => q('[data-testid="repeater-surface"]');

beforeEach(() => {
  setLocale('en-US');
  txHarness = new ManagedAppTxHarness();
  h.txController = txHarness.controller;
  h.session = { state: 'connected', epoch: 1 };
  h.sessionSubscriber = undefined;
  vi.mocked(sendCommand).mockClear();
  resetCommandLifecycle();
  localStorage.clear();
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
  localStorage.clear();
  resetCommandLifecycle();
  expect(h.sessionSubscriber).toBeUndefined();
  expect(h.authoritySubscribers.size).toBe(0);
});

describe('which receiver the repeater panel controls', () => {
  it('SUB on 144.700 while MAIN is selected on 14.250: the panel names SUB and a click emits receiver 1', () => {
    use(dualState({ mainHz: 14_250_000, subHz: 144_700_000, active: 'MAIN' }), dualCaps());
    renderHosted();
    const panel = q('.desktop-controls-left [data-panel-id="semantic-repeater"]');
    expect(panel).not.toBeNull();
    expect(q('[data-zone-id="repeater"] [data-testid="repeater-surface"]')).not.toBeNull();
    expect(surface()!.getAttribute('data-receiver')).toBe('SUB');
    expect(q('[data-testid="repeater-receiver"]')!.textContent).toBe('SUB');

    press(key('repeater-tone-tone')!);
    press(key('repeater-shift-minus')!);
    expect(commands()).toEqual([
      { name: 'set_repeater_tone', params: { on: true, receiver: 1 } },
      { name: 'set_repeater_shift', params: { direction: 2, receiver: 1 } },
    ]);
  });

  it('both receivers on HF: no panel and no zone', () => {
    use(dualState({ mainHz: 14_250_000, subHz: 7_185_000 }), dualCaps());
    renderHosted();
    expect(surface()).toBeNull();
    expect(q('[data-zone-id="repeater"]')).toBeNull();
  });

  it.each(['MAIN', 'SUB'] as const)('both on 144/430 with %s selected: the panel follows the selection', (active) => {
    use(dualState({ mainHz: 145_500_000, subHz: 433_000_000, active }), dualCaps());
    renderHosted();
    expect(surface()!.getAttribute('data-receiver')).toBe(active);
    press(key('repeater-tone-tone')!);
    expect(commands()).toEqual([
      { name: 'set_repeater_tone', params: { on: true, receiver: active === 'SUB' ? 1 : 0 } },
    ]);
  });

  it('both on 144/430 with the selection unread: no panel and no zone', () => {
    use(dualState({ mainHz: 145_500_000, subHz: 433_000_000, activeRead: false }), dualCaps());
    renderHosted();
    expect(surface()).toBeNull();
    expect(q('[data-zone-id="repeater"]')).toBeNull();
  });
});

describe('each control draws only when its own field is structural (B1)', () => {
  it('IC-705-shaped caps on 2 m: tone selector and stepper, no shift selector', () => {
    use(dualState({ mainHz: 14_250_000, subHz: 7_185_000 }), dualCaps(), ic705State(), ic705Caps());
    renderHosted();
    expect(surface()!.getAttribute('data-receiver')).toBe('MAIN');
    expect(key('repeater-tone-off')).not.toBeNull();
    expect(q('[data-testid="repeater-tone-freq"]')).not.toBeNull();
    expect(q('[data-testid="repeater-shift"]')).toBeNull();
    expect(key('repeater-shift-simplex')).toBeNull();
  });

  it('repeater_shift without tone tags: only the shift selector', () => {
    use(dualState({ mainHz: 14_250_000, subHz: 144_700_000 }), dualCaps(['repeater_shift']));
    renderHosted();
    expect(surface()!.getAttribute('data-receiver')).toBe('SUB');
    expect(key('repeater-shift-simplex')).not.toBeNull();
    expect(q('[data-testid="repeater-tone-mode"]')).toBeNull();
    expect(key('repeater-tone-off')).toBeNull();
    expect(q('[data-testid="repeater-tone-freq"]')).toBeNull();
  });

  it('no repeater control structural: no panel even on 2 m', () => {
    use(dualState({ mainHz: 14_250_000, subHz: 7_185_000 }), dualCaps(), ic705State(), ic705Caps([]));
    renderHosted();
    expect(surface()).toBeNull();
    expect(q('[data-zone-id="repeater"]')).toBeNull();
  });
});

describe('ARS: a fourth shift key only on a radio that declares it', () => {
  /** FTX-1-shaped: `rigs/ftx1.toml` declares `repeater_shift_ars`. */
  const FTX1_TAGS = ['repeater_tone', 'tsql', 'repeater_shift', 'repeater_shift_ars'] as const;
  const shiftKeys = () => [...surface()!.querySelectorAll<HTMLButtonElement>('[data-testid^="repeater-shift-"]')];
  const lit = () => shiftKeys().filter((node) => node.getAttribute('aria-checked') === 'true')
    .map((node) => node.textContent?.trim());

  it('FTX-1-shaped caps: four keys SIMP / − / + / ARS, and a reported 3 lights ARS alone', () => {
    use(dualState({ mainHz: 14_250_000, subHz: 144_700_000, sub: { ...OFF, shift: 3 } }), dualCaps(FTX1_TAGS));
    renderHosted();
    expect(shiftKeys().map((node) => node.textContent?.trim())).toEqual(['SIMP', '−', '+', 'ARS']);
    expect(lit()).toEqual(['ARS']);
    expect(key('repeater-shift-ars')!.getAttribute('data-active')).toBe('true');
  });

  it('a click on ARS sends direction 3 with receiver 1 for SUB and marks ARS pending', () => {
    use(dualState({ mainHz: 14_250_000, subHz: 144_700_000 }), dualCaps(FTX1_TAGS));
    renderHosted();
    press(key('repeater-shift-ars')!);
    expect(commands()).toEqual([{ name: 'set_repeater_shift', params: { direction: 3, receiver: 1 } }]);
    const armed = shiftKeys().filter((node) => node.getAttribute('data-armed') === 'true')
      .map((node) => node.textContent?.trim());
    expect(armed).toEqual(['ARS']);
    const described = key('repeater-shift-ars')!.getAttribute('aria-describedby');
    expect(described && document.getElementById(described)?.textContent).toBe('Pending, not yet confirmed');
  });

  it('caps without ARS: three keys, and a reported 3 lights none', () => {
    use(dualState({ mainHz: 14_250_000, subHz: 144_700_000, sub: { ...OFF, shift: 3 } }), dualCaps());
    renderHosted();
    expect(shiftKeys().map((node) => node.textContent?.trim())).toEqual(['SIMP', '−', '+']);
    expect(key('repeater-shift-ars')).toBeNull();
    expect(lit()).toEqual([]);
    expect(shiftKeys().every((node) => node.getAttribute('data-active') === 'false')).toBe(true);
  });
});

describe('the CTCSS stepper (B4)', () => {
  it('steps through caps.ctcssTones, writing set_tone_freq in TONE', () => {
    const tone: Repeater = { ...OFF, tone: true };
    use(dualState({ mainHz: 14_250_000, subHz: 144_700_000, sub: tone }), dualCaps());
    renderHosted();
    expect(q('[data-testid="repeater-tone-freq-value"]')!.textContent).toBe('71.9');
    press(key('repeater-tone-freq-up')!);
    expect(commands()).toEqual([{ name: 'set_tone_freq', params: { freq: 8850, receiver: 1 } }]);
  });

  it('writes set_tsql_freq in TSQL', () => {
    const tsql: Repeater = { ...OFF, tone: true, tsql: true };
    use(dualState({ mainHz: 14_250_000, subHz: 144_700_000, sub: tsql }), dualCaps());
    renderHosted();
    press(key('repeater-tone-freq-down')!);
    expect(commands()).toEqual([{ name: 'set_tsql_freq', params: { freq: 6700, receiver: 1 } }]);
  });

  it('is disabled with an unread tone mode, and a forced click sends nothing', () => {
    // `deriveRepeater` maps tone off + TSQL on to no mode: it reads unknown.
    const odd: Repeater = { ...OFF, tone: false, tsql: true };
    use(dualState({ mainHz: 14_250_000, subHz: 144_700_000, sub: odd }), dualCaps());
    renderHosted();
    const up = key('repeater-tone-freq-up')!;
    expect(up.disabled).toBe(true);
    expect(q('[data-testid="repeater-tone-freq-value"]')!.textContent).toBe('71.9');
    press(up);
    expect(commands()).toEqual([]);
  });
});

describe('unread facts and pending markers (B4)', () => {
  it('unread repeater facts: every key in place, none lit, no value text, no placeholder', () => {
    use(dualState({ mainHz: 14_250_000, subHz: 144_700_000, repeaterRead: false }), dualCaps());
    renderHosted();
    const keys = [...surface()!.querySelectorAll<HTMLButtonElement>('[data-testid^="repeater-tone-"][role="radio"], [data-testid^="repeater-shift-"]')];
    expect(keys.map((node) => node.textContent?.trim())).toEqual(['OFF', 'TONE', 'TSQL', 'SIMP', '−', '+']);
    expect(keys.every((node) => node.getAttribute('data-active') === 'false')).toBe(true);
    expect(keys.every((node) => node.getAttribute('aria-checked') === 'false')).toBe(true);
    expect(q('[data-testid="repeater-tone-freq-value"]')!.textContent).toBe('');
    expect(surface()!.textContent).not.toMatch(/\?|—|unknown|null/i);
  });

  it('a click marks its target key pending, and a step marks the value pending', () => {
    use(dualState({ mainHz: 14_250_000, subHz: 144_700_000 }), dualCaps());
    renderHosted();
    const tsql = key('repeater-tone-tsql')!;
    expect(tsql.getAttribute('data-armed')).toBeNull();
    press(tsql);
    expect(key('repeater-tone-tsql')!.getAttribute('data-armed')).toBe('true');
    expect(key('repeater-tone-tone')!.getAttribute('data-armed')).toBeNull();
    const described = key('repeater-tone-tsql')!.getAttribute('aria-describedby');
    expect(described && document.getElementById(described)?.textContent).toBe('Pending, not yet confirmed');

    press(key('repeater-shift-plus')!);
    expect(key('repeater-shift-plus')!.getAttribute('data-armed')).toBe('true');

    press(key('repeater-tone-freq-up')!);
    expect(q('[data-testid="repeater-tone-freq-value"]')!.getAttribute('data-pending')).toBe('true');
  });
});
