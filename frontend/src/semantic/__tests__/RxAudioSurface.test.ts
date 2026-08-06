/**
 * MOR-1279 — the semantic RX-audio surface (vocabulary slice 3B).
 *
 * SAFETY-CRITICAL. Every test below names the mutation it kills, because the
 * failure modes are operational:
 *   (a) the surface touching the audio path — "a view opened the transport on
 *       mount" is the MOR-972 P0 shape, and audio lifetime is App-owned
 *       (MOR-1058);
 *   (b) an unread fact rendered as a fabricated default — slice 3A degraded
 *       AF/focus/split to `unknown` precisely because the shipped v2 path
 *       substitutes 0.5 / 'both' / false; re-substituting HERE erases the
 *       whole honesty gain one layer up;
 *   (c) `live` offered from a capability re-derivation instead of the
 *       `liveAudio` fact;
 *   (d) the AF unit divided twice (0..100 → 0..1 happens exactly once, in the
 *       adapter) — a second divide moves the operator's AF by 100×;
 *   (e) a MOD-input `mismatch` left without a one-click remedy — that is the
 *       recorded "web voice TX = noise/squeal" configuration.
 *
 * Fast-pool-safe by construction (MOR-1272): no `vi.mock`, no `vi.stubGlobal`,
 * no global spy. The load-time/behavioural audio-seam pins live in the
 * isolated-pool wiring file `semantic-rx-audio-wiring.component.test.ts`.
 */
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import RxAudioSurface, {
  FOCUS_CHOICES, LINK_LOST_TEXT, MONITOR_MODES, READINESS_LABEL, SPLIT_CHOICES, UNKNOWN_TEXT,
} from '../RxAudioSurface.svelte';
import { topologyFixtures, withRxAudio } from '../fixtures/topologies';
import type {
  AudioFocus, Availability, MonitorMode, RadioViewModel, RxAudioField, RxAudioViewModel,
} from '../radio-view-model';

const SOURCE = readFileSync('src/semantic/RxAudioSurface.svelte', 'utf8');
/** Comments stripped, so the file's own doctrine prose can never be what a
 *  source-scanning test matches. */
const CODE = SOURCE
  .replace(/<!--[\s\S]*?-->/g, '')
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '');

const ON: Availability = { structural: true, operational: true };
const OFF: Availability = { structural: false, operational: false };
const DEGRADED: Availability = { structural: true, operational: false };

const base = (): RadioViewModel => withRxAudio(topologyFixtures['1/single']);
/** Re-shape the rxAudio group of an otherwise fully-observed fixture. */
const withRx = (over: Partial<RxAudioViewModel>): RadioViewModel => {
  const view = base();
  return { ...view, rxAudio: { ...view.rxAudio!, ...over } };
};
const unread = <T>(availability: Availability = ON): RxAudioField<T> =>
  ({ reading: { status: 'unknown' }, availability });
const known = <T>(value: T, availability: Availability = ON): RxAudioField<T> =>
  ({ reading: { status: 'known', value }, availability });

let target: HTMLDivElement;
beforeEach(() => { target = document.createElement('div'); document.body.appendChild(target); });
afterEach(() => { target.remove(); });

type Handlers = {
  onMonitorMode?: (mode: MonitorMode) => void;
  onAfLevel?: (level: number) => void;
  onRoutingFocus?: (focus: AudioFocus) => void;
  onRoutingSplit?: (split: boolean) => void;
  onSetModInputLan?: () => void;
};

function render(view: RadioViewModel, handlers: Handlers = {}) {
  const component = mount(RxAudioSurface, { target, props: { view, ...handlers } });
  flushSync();
  const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
  return {
    dispose: () => unmount(component),
    root: () => q('[data-testid="rx-audio-surface"]'),
    el: (id: string) => q<HTMLElement>(`[data-testid="rx-audio-${id}"]`),
    input: () => q<HTMLInputElement>('[data-testid="rx-audio-af"] input'),
    text: (id: string) => q<HTMLElement>(`[data-testid="rx-audio-${id}"]`)?.textContent?.trim(),
  };
}

/* ── (a) the surface never touches the audio path ─────────────── */

describe('the RX-audio surface owns no audio lifetime (MOR-972 P0 / MOR-1058)', () => {
  /** The whole static import closure of the file, allow-listed. Kills: adding
   *  ANY import that could reach transport or the audio manager — including
   *  through a relative specifier, which a `$lib/...` regex would miss. */
  it('imports nothing but the fact contract and the pure MOD-input constants', () => {
    const specifiers = [...CODE.matchAll(/from\s+'([^']+)'/g)].map((m) => m[1]);
    expect(specifiers.length).toBeGreaterThan(0);
    expect([...new Set(specifiers)].sort()).toEqual(['$lib/radio/mod-input', './radio-view-model']);
  });

  // Kills: `onMount(() => audioManager.startRx())` and every relative of it.
  it('declares no lifecycle hook and no effect that could start a stream', () => {
    for (const forbidden of ['onMount', 'onDestroy', '$effect', 'import(']) {
      expect(CODE).not.toContain(forbidden);
    }
  });

  // Kills: reaching for the raw capability store or the runtime barrel.
  it('never mentions capabilities, stores, transport or the audio manager', () => {
    for (const forbidden of [
      'capabilities', 'hasCap', '$lib/stores', '$lib/transport', 'audio-manager',
      'audioManager', 'AudioContext', 'WebSocket',
    ]) {
      expect(CODE).not.toContain(forbidden);
    }
  });

  // Kills: the surface growing a second props member and reading live state.
  it('takes exactly one state prop — the view model — plus intent callbacks', () => {
    const props = CODE.slice(CODE.indexOf('interface Props'), CODE.indexOf('}: Props'));
    expect([...props.matchAll(/^\s{4}(\w+)[?]?:/gm)].map((m) => m[1])).toEqual([
      'view', 'onMonitorMode', 'onAfLevel', 'onRoutingFocus', 'onRoutingSplit',
      'onSetModInputLan',
    ]);
  });

  // Kills: rendering an empty audio panel for a radio that has no audio chain.
  it('renders NOTHING at all when the view model carries no rxAudio group', () => {
    const view = { ...base() };
    delete (view as { rxAudio?: unknown }).rxAudio;
    const r = render(view);
    expect(r.root()).toBeNull();
    expect(target.textContent).toBe('');
    r.dispose();
  });
});

/* ── (c) `live` is offered by the fact, never re-derived ───────── */

describe('the liveAudio FACT decides whether `live` is offerable', () => {
  it.each(MONITOR_MODES)('renders the %s choice on a fully live-capable radio', (mode) => {
    const r = render(base());
    expect(r.el(`monitor-${mode}`)).not.toBeNull();
    r.dispose();
  });

  // Kills: offering `live` on a radio that streams no audio. The rest of the
  // group is fully observed here, so nothing else can explain the absence.
  it('omits the live choice when the liveAudio fact is structurally absent', () => {
    const r = render(withRx({ liveAudio: OFF }));
    expect(r.el('monitor-live')).toBeNull();
    expect(r.el('monitor-local')).not.toBeNull();
    expect(r.el('monitor-mute')).not.toBeNull();
    r.dispose();
  });

  // Kills: gating `live` on the audio-WS link. The WS opens BECAUSE the
  // operator picks live — disabling it there makes live permanently
  // unreachable, which is worse than a mis-styled button.
  it('keeps live reachable while the audio link is down, and says the link is down', () => {
    const emitted: MonitorMode[] = [];
    const r = render(
      withRx({ liveAudio: DEGRADED, monitorMode: 'local' }),
      { onMonitorMode: (m) => emitted.push(m) },
    );
    const live = r.el('monitor-live')!;
    expect(live.hasAttribute('disabled')).toBe(false);
    expect(live.dataset.liveLink).toBe('false');
    live.click();
    flushSync();
    expect(emitted).toEqual(['live']);
    r.dispose();
  });

  it.each(MONITOR_MODES)('marks %s as the checked choice when it is the mode', (mode) => {
    const r = render(withRx({ monitorMode: mode }));
    for (const other of MONITOR_MODES) {
      expect(r.el(`monitor-${other}`)!.getAttribute('aria-checked')).toBe(String(other === mode));
    }
    r.dispose();
  });

  it.each(MONITOR_MODES)('emits the %s intent when the choice is clicked', (mode) => {
    const onMonitorMode = vi.fn();
    const r = render(withRx({ monitorMode: 'local' }), { onMonitorMode });
    r.el(`monitor-${mode}`)!.click();
    flushSync();
    expect(onMonitorMode).toHaveBeenCalledExactlyOnceWith(mode);
    r.dispose();
  });
});

/* ── (f) the audio link-lost indication (MOR-1384) ─────────────── */

describe('an ENGAGED live link that is down is stated in words', () => {
  const focusable = () =>
    target.querySelectorAll('button, input, select, textarea, a[href], [tabindex]').length;

  // Kills: dropping the readout entirely — the S12 parity loss itself. The v2
  // `RxAudioPanel` said this and its sole consumer was retired with the panel,
  // so without this element the operator reads a dead audio path as a dead
  // radio.
  it('renders the link-lost readout while live is selected and the link is down', () => {
    const r = render(withRx({ liveAudio: DEGRADED, monitorMode: 'live' }));
    expect(r.el('link')).not.toBeNull();
    expect(r.text('link')).toBe(LINK_LOST_TEXT);
    r.dispose();
  });

  // Kills: an indication whose only channel is a colour or a data attribute —
  // the same forced-colors rule the unknown renderings follow (MOR-977).
  it('states the loss as TEXT inside the surface, not only as an attribute', () => {
    const r = render(withRx({ liveAudio: DEGRADED, monitorMode: 'live' }));
    expect(r.root()!.textContent).toContain(LINK_LOST_TEXT);
    r.dispose();
  });

  // Kills: claiming a loss on a healthy link. `operational: true` IS the
  // observation that the audio WS is up.
  it('renders nothing at all while the engaged link is up', () => {
    const r = render(withRx({ liveAudio: ON, monitorMode: 'live' }));
    expect(r.el('link')).toBeNull();
    expect(r.root()!.textContent).not.toContain(LINK_LOST_TEXT);
    r.dispose();
  });

  // THE EPISTEMIC PIN. Kills: dropping the `monitorMode === 'live'` leg. With
  // `local`/`mute` selected the audio WS is legitimately closed and was never
  // opened — "down" there is not a loss, it is the absence of a request, and
  // rendering it as a loss is a lie the operator would act on.
  it.each(['local', 'mute'] as const)(
    'makes no loss claim while %s is selected, even with the link down', (mode) => {
      const r = render(withRx({ liveAudio: DEGRADED, monitorMode: mode }));
      expect(r.el('link')).toBeNull();
      expect(r.root()!.textContent).not.toContain(LINK_LOST_TEXT);
      r.dispose();
    },
  );

  // Kills: dropping the structural leg. A radio that streams no audio at all
  // has no link to lose — the `live` choice is not even offered — so a model
  // that nonetheless names `live` must not be narrated as a dropped link.
  it('makes no loss claim on a radio whose live audio is structurally absent', () => {
    const r = render(withRx({ liveAudio: OFF, monitorMode: 'live' }));
    expect(r.el('monitor-live')).toBeNull();
    expect(r.el('link')).toBeNull();
    r.dispose();
  });

  // Kills: restoring the indication as a control. The rx-audio zone canon lets
  // a pure readout ride along; a focusable element here would change the zone's
  // tab order (MOR-1069/MOR-1304).
  it('adds no focusable element to the surface', () => {
    const up = render(withRx({ liveAudio: ON, monitorMode: 'live' }));
    const before = focusable();
    up.dispose();
    const down = render(withRx({ liveAudio: DEGRADED, monitorMode: 'live' }));
    expect(focusable()).toBe(before);
    expect(down.el('link')!.querySelectorAll('*').length).toBe(0);
    down.dispose();
  });
});

/* ── (b) unknown is rendered as unknown, per field ─────────────── */

describe('every unread fact renders honestly, never as the v2 default', () => {
  // Kills: `?? 0.5` — the shipped `toRxAudioProps` fabrication.
  it('renders an unread AF level as unknown, never as a level', () => {
    const r = render(withRx({ afLevel: unread<number>() }));
    expect(r.text('af-value')).toBe(UNKNOWN_TEXT);
    expect(r.el('af')!.dataset.observed).toBe('false');
    expect(r.text('af-value')).not.toContain('0.5');
    r.dispose();
  });

  // Kills: acting on a guessed slider position.
  it('makes the AF slider inert while the level is unread, and emits nothing', () => {
    const onAfLevel = vi.fn();
    const r = render(withRx({ afLevel: unread<number>() }), { onAfLevel });
    const input = r.input()!;
    expect(input.disabled).toBe(true);
    expect(input.valueAsNumber).toBe(0);
    input.value = '0.7';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(onAfLevel).not.toHaveBeenCalled();
    r.dispose();
  });

  // Kills: `?? 'both'` — the AudioRoutingControl fabrication.
  it('renders an unrestored routing focus as unknown, with nothing checked', () => {
    const r = render(withRx({ routingFocus: unread<AudioFocus>(DEGRADED) }));
    expect(r.text('focus-value')).toBe(UNKNOWN_TEXT);
    expect(r.el('focus')!.dataset.observed).toBe('false');
    for (const focus of FOCUS_CHOICES) {
      expect(r.el(`focus-${focus}`)!.getAttribute('aria-checked')).toBe('false');
    }
    r.dispose();
  });

  // Kills: `?? false` — the third v2 fabrication.
  it('renders an unrestored stereo split as unknown, with nothing checked', () => {
    const r = render(withRx({ routingSplit: unread<boolean>(DEGRADED) }));
    expect(r.text('split-value')).toBe(UNKNOWN_TEXT);
    expect(r.el('split')!.dataset.observed).toBe('false');
    for (const [, label] of SPLIT_CHOICES) {
      expect(r.el(`split-${label}`)!.getAttribute('aria-checked')).toBe('false');
    }
    r.dispose();
  });

  // Kills: presenting an unread MOD source as any concrete source.
  it('renders an unread MOD-input source as unknown, never as a source name', () => {
    const r = render(withRx({
      modInputSource: unread<number>(DEGRADED), modInputReadiness: { status: 'unknown' },
    }));
    expect(r.text('mod-source')).toBe(`MOD: ${UNKNOWN_TEXT}`);
    expect(r.text('mod-readiness')).toBe(UNKNOWN_TEXT);
    expect(r.el('mod-input')!.dataset.observed).toBe('false');
    r.dispose();
  });

  // Kills: collapsing "this radio has no such control" into "unreadable now".
  it.each([
    ['afLevel', 'af'], ['routingFocus', 'focus'],
    ['routingSplit', 'split'], ['modInputSource', 'mod-input'],
  ] as const)('renders no %s block at all when it is structurally absent', (field, id) => {
    const r = render(withRx({ [field]: unread(OFF) } as Partial<RxAudioViewModel>));
    expect(r.el(id)).toBeNull();
    r.dispose();
  });

  // Kills: an unknown state that is only a colour (forced-colors, MOR-977).
  it('keeps every unknown distinguishable as TEXT, not only as an attribute', () => {
    const r = render(withRx({
      afLevel: unread<number>(), routingFocus: unread<AudioFocus>(),
      routingSplit: unread<boolean>(),
    }));
    expect(r.root()!.textContent).toContain(UNKNOWN_TEXT);
    r.dispose();
  });
});

/* ── (d) the AF unit is converted exactly once ─────────────────── */

describe('AF level is 0..1 end to end — converted exactly once, in the adapter', () => {
  // Kills: `value / 100` or `value * 100` on the way IN. 0.42 is the fixture's
  // level, i.e. an RxAudioSnapshot volume of 42 already divided by the adapter.
  it('renders the fact verbatim, with no second scaling', () => {
    const r = render(base());
    expect(r.input()!.valueAsNumber).toBeCloseTo(0.42, 10);
    expect(r.text('af-value')).toBe('0.42');
    r.dispose();
  });

  it.each([0, 0.01, 0.5, 1])('renders the level %s verbatim', (value) => {
    const r = render(withRx({ afLevel: known(value) }));
    expect(r.input()!.valueAsNumber).toBe(value);
    r.dispose();
  });

  // Kills: `level / 100` or `level * 100` on the way OUT. The command handler
  // (`makeRxAudioHandlers().onAfLevelChange`) takes 0..1, so the round trip
  // must be the identity.
  it.each([0, 0.25, 0.7, 1])('emits the slider level %s verbatim', (value) => {
    const onAfLevel = vi.fn();
    const r = render(base(), { onAfLevel });
    const input = r.input()!;
    input.value = String(value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(onAfLevel).toHaveBeenCalledExactlyOnceWith(value);
    r.dispose();
  });

  // Kills: a range whose bounds silently rescale the fact.
  it('declares the slider on the contract`s own 0..1 scale', () => {
    const r = render(base());
    expect([r.input()!.min, r.input()!.max]).toEqual(['0', '1']);
    r.dispose();
  });
});

/* ── routing: absolute intents, observed state read back ───────── */

describe('routing focus and split are rendered from the facts and emitted absolutely', () => {
  it.each(FOCUS_CHOICES)('checks exactly the observed focus %s', (focus) => {
    const r = render(withRx({ routingFocus: known(focus) }));
    for (const other of FOCUS_CHOICES) {
      expect(r.el(`focus-${other}`)!.getAttribute('aria-checked')).toBe(String(other === focus));
    }
    expect(r.text('focus-value')).toBe(focus);
    r.dispose();
  });

  it.each(FOCUS_CHOICES)('emits the absolute focus intent %s', (focus) => {
    const onRoutingFocus = vi.fn();
    const r = render(withRx({ routingFocus: known<AudioFocus>('both') }), { onRoutingFocus });
    r.el(`focus-${focus}`)!.click();
    flushSync();
    expect(onRoutingFocus).toHaveBeenCalledExactlyOnceWith(focus);
    r.dispose();
  });

  it.each(SPLIT_CHOICES)('checks exactly the observed split %s', (value, label) => {
    const r = render(withRx({ routingSplit: known(value) }));
    expect(r.el(`split-${label}`)!.getAttribute('aria-checked')).toBe('true');
    expect(r.text('split-value')).toBe(String(value));
    r.dispose();
  });

  // Kills: a RELATIVE split toggle. `!unknown` is a guess, and gating the
  // toggle on `known` would leave the control permanently dead while the
  // prefs are unrestored — two absolute choices avoid both.
  it.each(SPLIT_CHOICES)('emits the absolute split intent %s even while unread', (value, label) => {
    const onRoutingSplit = vi.fn();
    const r = render(withRx({ routingSplit: unread<boolean>(DEGRADED) }), { onRoutingSplit });
    r.el(`split-${label}`)!.click();
    flushSync();
    expect(onRoutingSplit).toHaveBeenCalledExactlyOnceWith(value);
    r.dispose();
  });
});

/* ── (e) MOD-input readiness keeps a one-click remedy ──────────── */

describe('MOD-input readiness is stated, and a mismatch is never a dead end', () => {
  it('names the observed source and reports LAN readiness', () => {
    const r = render(base());
    expect(r.text('mod-source')).toBe('MOD: LAN');
    expect(r.text('mod-readiness')).toBe(READINESS_LABEL.ready);
    expect(r.el('mod-input')!.dataset.readiness).toBe('ready');
    r.dispose();
  });

  // The recorded failure: DATA OFF MOD = MIC while the browser streams over
  // LAN. Kills: a mismatch rendered as a bare status with no way out.
  it('offers a one-click LAN remedy for the recorded mismatch', () => {
    const onSetModInputLan = vi.fn();
    const r = render(
      withRx({ modInputSource: known(0), modInputReadiness: { status: 'mismatch', source: 0 } }),
      { onSetModInputLan },
    );
    expect(r.text('mod-source')).toBe('MOD: MIC');
    expect(r.el('mod-input')!.dataset.readiness).toBe('mismatch');
    const fix = r.el('mod-set-lan');
    expect(fix).not.toBeNull();
    fix!.click();
    flushSync();
    expect(onSetModInputLan).toHaveBeenCalledTimes(1);
    r.dispose();
  });

  // Kills: a mismatch whose only signal is a colour or an attribute.
  it('says what a mismatch COSTS, in text', () => {
    const r = render(withRx({ modInputReadiness: { status: 'mismatch', source: 3 } }));
    expect(r.text('mod-readiness')).toContain('not LAN');
    r.dispose();
  });

  // Kills: a stray remedy button on a correctly-routed radio.
  it.each(['ready', 'unknown', 'not-applicable'] as const)(
    'offers no remedy button while readiness is %s', (status) => {
      const r = render(withRx({
        modInputReadiness: status === 'ready' ? { status, source: 5 } : { status },
      }));
      expect(r.el('mod-set-lan')).toBeNull();
      r.dispose();
    },
  );

  // Kills: a label map that drifts from the contract union.
  it('has a label for every readiness the contract can state', () => {
    expect(Object.keys(READINESS_LABEL).sort())
      .toEqual(['mismatch', 'not-applicable', 'ready', 'unknown']);
  });
});
