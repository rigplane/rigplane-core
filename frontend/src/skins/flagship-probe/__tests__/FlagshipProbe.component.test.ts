/**
 * The flagship geometry probe, mounted end to end against the real adapter,
 * the real wiring and the real semantic surfaces. Replaced: the runtime
 * singleton, the TX authority, the MOD-input guard and the command
 * vocabulary — all four by the same pattern
 * `DualReceiverCockpit.component.test.ts` uses — and `SpectrumPanel`, by the
 * shared `SpectrumPanelStub`, so this file pays for the arrangement rather
 * than for a canvas.
 *
 * The two arrangements are a container query, and jsdom evaluates none, so
 * the DOM half of this file proves what mounts and the source half proves
 * where the two `grid-template-areas` put it. Each test's doc line names the
 * mutation it kills.
 */
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';

let txHarness: ManagedAppTxHarness;

const h = vi.hoisted(() => ({
  state: null as unknown,
  caps: null as unknown,
  session: { state: 'disconnected' as const, epoch: 0 },
  selectVfo: vi.fn(),
  splitToggle: vi.fn(),
  dualWatchToggle: vi.fn(),
  modInputGuard: { visible: false, sourceLabel: null } as { visible: boolean; sourceLabel: string | null },
  /** One stand-in for every command intent: this file asserts arrangement,
   *  never command dispatch. */
  noop: vi.fn(),
}));

vi.mock('$lib/runtime', () => ({
  runtime: {
    onTxAudioDied: () => () => {},
    get state() { return h.state; },
    get caps() { return h.caps; },
    get controlSession() { return h.session; },
    subscribeControlAuthority(handler: (publication: unknown) => void) {
      handler({
        state: h.state, caps: h.caps, session: h.session,
        rxAudioTarget: Object.freeze({ muted: true, rxEnabled: false }),
      });
      return () => {};
    },
    get audio() { return { muted: true, rxEnabled: false, volume: 0 }; },
    get connectionAudio() { return false; },
    get defaultScopeStatus() {
      return {
        source: null, available: false, resourceSelected: false, demand: 0,
        lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
      };
    },
    get radioPowerOn() { return null; },
    get scope() { return { hardwareScopeConnected: false }; },
  },
}));
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => txHarness.controller,
}));
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => h.modInputGuard,
  getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
}));
/** The shared stub records `hideScopeControls`, which is the one prop this
 *  skin passes that the arrangement depends on — the scope toolbar's
 *  fact-backed half must not double the semantic `scopeControls` zone. */
vi.mock('../../../components/spectrum/SpectrumPanel.svelte', async () => ({
  default: (await import('../../../components-v2/layout/__tests__/SpectrumPanelStub.svelte')).default,
}));
vi.mock('$lib/runtime/commands/panel-commands', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/commands/panel-commands')>();
  return {
    ...actual,
    makeVfoHandlers: () => ({
      onVfoSelect: h.selectVfo, onSplitToggle: h.splitToggle, onDualWatchToggle: h.dualWatchToggle,
    }),
    makeVoxHandlers: () => ({
      onVoxToggle: h.noop, onVoxGainChange: h.noop, onAntiVoxGainChange: h.noop, onVoxDelayChange: h.noop,
    }),
    makeTxHandlers: () => ({
      onRfPowerChange: h.noop, onMicGainChange: h.noop, onAtuToggle: h.noop, onAtuTune: h.noop,
      onVoxToggle: h.noop, onCompToggle: h.noop, onCompLevelChange: h.noop, onMonToggle: h.noop,
      onMonLevelChange: h.noop, onDriveGainChange: h.noop,
    }),
    makeRxAudioHandlers: () => ({ onMonitorModeChange: h.noop, onAfLevelChange: h.noop }),
    makeCwPanelHandlers: () => ({
      onKeySpeedChange: h.noop, onCwPitchChange: h.noop, onBreakInDelayChange: h.noop,
      onBreakInModeChange: h.noop, onApfChange: h.noop, onTwinPeakToggle: h.noop,
      onReversePaddleToggle: h.noop,
    }),
    makeAudioRoutingHandlers: () => ({ onFocusChange: h.noop, onSplitStereoChange: h.noop }),
    makeModeHandlers: () => ({
      onModInputChange: h.noop, onModeChange: h.noop, onDataModeChange: h.noop,
    }),
    makeFilterHandlers: () => ({
      onFilterChange: h.noop, onFilterWidthChange: h.noop, onFilterShapeChange: h.noop,
      onIfShiftChange: h.noop, onPbtInnerChange: h.noop, onPbtOuterChange: h.noop,
    }),
    makeDspHandlers: () => ({
      onNrModeChange: h.noop, onNrLevelChange: h.noop, onNbToggle: h.noop, onNbLevelChange: h.noop,
      onNbDepthChange: h.noop, onNbWidthChange: h.noop, onNotchModeChange: h.noop,
      onNotchFreqChange: h.noop, onManualNotchWidthChange: h.noop, onAgcTimeChange: h.noop,
    }),
    makeAgcHandlers: () => ({ onAgcModeChange: h.noop }),
    makeRfFrontEndHandlers: () => ({
      onAttChange: h.noop, onPreChange: h.noop, onRfGainChange: h.noop, onSquelchChange: h.noop,
      onDigiSelToggle: h.noop, onIpPlusToggle: h.noop,
    }),
    makeBandHandlers: () => ({ onBandSelect: h.noop }),
    makeAntennaHandlers: () => ({ onSelectAnt1: h.noop, onSelectAnt2: h.noop, onToggleRxAnt: h.noop }),
    makeRitXitHandlers: () => ({
      onRitToggle: h.noop, onXitToggle: h.noop, onRitOffsetChange: h.noop,
      onXitOffsetChange: h.noop, onClear: h.noop,
    }),
    makeScanHandlers: () => ({
      onScanStart: h.noop, onScanStop: h.noop, onDfSpanChange: h.noop, onResumeChange: h.noop,
    }),
    makeScopeControlsHandlers: () => ({
      onModeChange: h.noop, onEdgeChange: h.noop, onSpanChange: h.noop, onSpeedChange: h.noop,
      onHoldChange: h.noop, onRefChange: h.noop, onDualChange: h.noop, onReceiverChange: h.noop,
      onDuringTxChange: h.noop, onCenterTypeChange: h.noop, onVbwChange: h.noop, onRbwChange: h.noop,
    }),
  };
});

import FlagshipProbeSkin from '../FlagshipProbeSkin.svelte';
// Read through the app-wide registration barrel, never through a direct
// module import, so the zone ids asserted below are the ids the app registers.
import { flagshipProbeLayout } from '../../../presentation/layouts/declarations';
import { readWorkspace } from '../../../presentation/workspace/contract';
import {
  resolveSurfacePlan, SURFACE_PLAN_CONTEXT_KEY, type SurfacePlan,
} from '../../../presentation/workspace/resolution';

const SKIN_PATH = 'src/skins/flagship-probe/FlagshipProbeSkin.svelte';
const source = readFileSync(SKIN_PATH, 'utf8');
/** The style block alone, comments stripped — so no assertion below can be
 *  satisfied by the header comment quoting the CSS it describes. */
const style = source.slice(source.indexOf('<style>'), source.indexOf('</style>'))
  .replace(/\/\*[\s\S]*?\*\//g, '');

const fresh = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };

/**
 * 2/main_sub: MAIN and SUB each carry A/B slots (4 vfo tiles total). Meter
 * readings are part of the fixture because `deriveMeters` returns the group
 * only when at least one raw meter value is present, and this layout declares
 * a `meters` zone — without them the dock would be an empty declared zone.
 */
function mainSubState(): ServerState {
  const paths = ['active', 'split', 'dualWatch', 'txTarget',
    'main.sMeter', 'powerMeter', 'swrMeter', 'alcMeter'];
  for (const rx of ['main', 'sub']) {
    paths.push(`${rx}.activeSlot`);
    for (const v of ['vfoA', 'vfoB']) paths.push(`${rx}.${v}.freqHz`, `${rx}.${v}.mode`, `${rx}.${v}.filterNum`);
  }
  const slot = (hz: number) => ({ freqHz: hz, mode: 'USB', filterNum: 1 });
  const receiver = (hz: number, sMeter?: number) => ({
    vfoA: slot(hz), vfoB: slot(hz + 30000), activeSlot: 'A', sMeter,
  });
  return {
    active: 'MAIN', split: true, dualWatch: true, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: receiver(14250000, 42), sub: receiver(21295000),
    powerMeter: 0, swrMeter: 0, alcMeter: 0,
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}

/** Copied verbatim from `DualReceiverCockpit.component.test.ts`'s
 *  `mainSubCaps()`. It carries evidence for every group this layout declares
 *  a zone for — which is what the "binds every zone id" test above proves. */
const mainSubCaps = (): Capabilities => ({
  model: 'fixture', scope: true, audio: true, tx: true,
  capabilities: [
    'audio', 'tx', 'dual_rx', 'tuner', 'dual_watch', 'lan_dual_rx_audio_routing',
    'af_level', 'rf_gain', 'squelch', 'attenuator', 'preamp', 'digisel', 'ip_plus',
    'antenna', 'rx_antenna', 'nb', 'nr', 'notch', 'apf', 'twin_peak', 'pbt',
    'filter_width', 'filter_shape', 'split', 'ssb_tx_bw', 'cw', 'break_in', 'rit', 'xit',
    'meters', 'data_mode', 'mod_input_routing', 'agc', 'power_control', 'dial_lock',
    'scan', 'bsr', 'main_sub_tracking', 'tuning_step', 'band_edge', 'xfc', 'system_settings',
    'scope', 'vox', 'compressor', 'monitor', 'drive_gain',
  ],
  receivers: 2, vfoScheme: 'main_sub',
  freqRanges: [{ start: 1800000, end: 54000000, label: 'HF+6m', bands: [
    { name: '20m', start: 14000000, end: 14350000, default: 14195000 },
    { name: '40m', start: 7000000, end: 7300000, default: 7100000 },
  ] }],
  modes: ['USB', 'LSB', 'CW', 'CW-R', 'AM', 'FM', 'RTTY', 'RTTY-R', 'PSK', 'PSK-R'],
  filters: ['FIL1', 'FIL2', 'FIL3'],
  antennas: 2,
  attValues: [0, 3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36, 39, 42, 45],
  preValues: [0, 1, 2],
  agcModes: [1, 2, 3], agcLabels: { '1': 'FAST', '2': 'MID', '3': 'SLOW' },
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
  scopeSource: null, audioFftAvailable: false,
} as unknown as Capabilities);

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

/** The plan App resolves for this layout when the operator expressed no
 *  visibleSurfaces/zoneOrder preference — every declared zone, verbatim. */
function defaultPlan(): SurfacePlan {
  return resolveSurfacePlan(flagshipProbeLayout, readWorkspace({ version: 1 }).workspace);
}

function render(): void {
  target = document.createElement('div');
  document.body.appendChild(target);
  const plan = defaultPlan();
  component = mount(FlagshipProbeSkin, {
    target,
    context: new Map<unknown, unknown>([[SURFACE_PLAN_CONTEXT_KEY, () => plan]]),
  });
  flushSync();
}

const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
const qa = <T extends HTMLElement>(sel: string) => [...target.querySelectorAll<T>(sel)];

beforeEach(() => {
  txHarness = new ManagedAppTxHarness();
  h.state = mainSubState();
  h.caps = mainSubCaps();
  h.modInputGuard = { visible: false, sourceLabel: null };
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
});

// ── The arrangement, as CSS ────────────────────────────────────────────────
//
// One `grid-template-areas` per arrangement, parsed out of the skin's own
// style block. Cell [row][column]; '.' is an empty cell.
type Template = string[][];

function templates(): Template[] {
  const blocks = [...source.matchAll(/grid-template-areas:([^;]*);/g)];
  return blocks.map(([, body]) =>
    [...body.matchAll(/'([^']*)'/g)].map(([, row]) => row.trim().split(/\s+/)));
}

/** A pixel-valued declaration in the skin's own style block. */
function px(name: string): number {
  const hit = style.match(new RegExp(`${name}:\\s*(\\d+)px`));
  expect(hit).not.toBeNull();
  return Number(hit![1]);
}

const LEFT_RAIL = ['rf', 'dsp', 'filter', 'rx-audio', 'antenna', 'band'];
const RIGHT_RAIL = ['rx-tx', 'tx-aux', 'cw-keyer', 'rit-xit'];

/** Every column index at which `area` appears in `template`. */
function columnsOf(template: Template, area: string): number[] {
  const cols = new Set<number>();
  for (const row of template) row.forEach((cell, index) => { if (cell === area) cols.add(index); });
  return [...cols].sort((a, b) => a - b);
}

/** The single row index `area` occupies; fails loudly if it spans rows. */
function rowOf(template: Template, area: string): number {
  const rows = template.flatMap((row, index) => (row.includes(area) ? [index] : []));
  expect(new Set(rows).size).toBe(1);
  return rows[0];
}

describe('the arrangement mounts the surfaces the manifest declares', () => {
  // Kills: a shell that mounts the wiring without `strips="dual"`, or one
  // receiver's strip going missing under a dual-receiver view model.
  it('renders both receiver strips, side by side, always', () => {
    render();
    expect(qa('[data-testid^="channel-strip-"]')).toHaveLength(2);
    expect(q('[data-testid="channel-strip-MAIN"]')).not.toBeNull();
    expect(q('[data-testid="channel-strip-SUB"]')).not.toBeNull();
  });

  // Kills: a zone id in the style block that no mounted element carries —
  // the silent way this arrangement would lose a rail panel.
  it('binds every zone id the style block places to a real element', () => {
    render();
    const placed = [...source.matchAll(/\[data-zone-id='([a-z-]+)'\]/g)].map(([, id]) => id);
    for (const id of new Set(placed)) expect(qa(`[data-zone-id="${id}"]`)).toHaveLength(1);
    // Total in both directions: the mounted zones are exactly the placed ones
    // plus the deck's two receiver strips, which this skin places by
    // `data-strip-receiver` instead — so a declared zone that mounts nothing,
    // and a mounted zone this arrangement never places, both fail here.
    expect(qa('[data-zone-id]').map((el) => el.dataset.zoneId).sort())
      .toEqual([...new Set([...placed, 'primary-vfo', 'secondary-vfo'])].sort());
    expect(new Set(placed).size).toBe(flagshipProbeLayout.zones.length - 2);
  });

  // Kills: dropping `hideScopeControls`, which would draw the scope toolbar's
  // fact-backed controls a second time beside the semantic scopeControls zone.
  it('mounts the panorama with the scope toolbar\'s fact-backed half suppressed', () => {
    render();
    const panorama = q('[data-testid="probe-panorama"]')!;
    expect(panorama).not.toBeNull();
    expect(panorama.querySelector('[data-hide-scope-controls="true"]')).not.toBeNull();
  });
});

describe('the panorama sits between the two rails, in both arrangements', () => {
  // Kills: moving the panorama into a rail column, or widening a rail across
  // the column the panorama occupies.
  it.each([[0, 'narrow'], [1, 'wide']])('template %i (%s)', (index) => {
    const template = templates()[index as number];
    for (const area of LEFT_RAIL) expect(columnsOf(template, area)).toEqual([0]);
    const lastColumn = template[0].length - 1;
    for (const area of RIGHT_RAIL) expect(columnsOf(template, area)).toEqual([lastColumn]);
    const panorama = columnsOf(template, 'panorama');
    expect(panorama.length).toBeGreaterThan(0);
    expect(Math.min(...panorama)).toBeGreaterThan(0);
    expect(Math.max(...panorama)).toBeLessThan(lastColumn);
  });

  // Kills: stacking the receivers, and putting any third area — the transmit
  // key's old `rx-mid` track among them — back between them.
  it.each([[0, 'narrow'], [1, 'wide']])('template %i (%s) keeps the two receivers side by side', (index) => {
    const template = templates()[index as number];
    const deckRow = rowOf(template, 'rx-main');
    expect(rowOf(template, 'rx-sub')).toBe(deckRow);
    // Between the two rail columns the deck row is one cell per strip.
    expect(template[deckRow].slice(1, template[0].length - 1)).toEqual(['rx-main', 'rx-sub']);
    expect(Math.max(...columnsOf(template, 'rx-main')))
      .toBeLessThan(Math.min(...columnsOf(template, 'rx-sub')));
  });

  // Kills: the transmit key going back into the deck, or anywhere but first
  // in the right rail. The art-direction line's rule of 2026-09-09: nothing
  // that keys the transmitter may live in a scrolling or clipped column. The
  // rail column's declared width is asserted here rather than assumed,
  // because that width is the whole of the key's room.
  it.each([[0, 'narrow'], [1, 'wide']])('template %i (%s) puts rx-tx first in the right rail', (index) => {
    const template = templates()[index as number];
    const lastColumn = template[0].length - 1;
    expect(columnsOf(template, 'rx-tx')).toEqual([lastColumn]);
    expect(columnTemplates()[index as number][lastColumn]).toBe(RAIL_TRACK);
    // First in the rail: no other transmit control comes between the top of
    // the transmit column and the key.
    const rows = RIGHT_RAIL.map((area) => rowOf(template, area));
    expect(rows).toEqual([...rows].sort((a, b) => a - b));
  });
});

describe('the container-width switch', () => {
  // Kills: deleting the `@container` block, which would leave the narrow
  // arrangement in force at every width.
  it('declares exactly two arrangements, and the wide one is inside a container query', () => {
    expect(templates()).toHaveLength(2);
    const container = style.slice(style.indexOf('@container'));
    expect(container).toContain('grid-template-areas');
    expect(style.indexOf('@container')).toBeLessThan(style.lastIndexOf('grid-template-areas'));
  });

  // Kills: dropping `container-type` from the skin root, which leaves the
  // query above resolving against some ancestor's width — or, with no
  // container anywhere above, never matching at all.
  it('declares the query container on the skin root itself', () => {
    expect(style).toMatch(/\.flagship-probe\s*\{[^}]*container-type:\s*inline-size/);
  });

  // Kills: a "narrow" template that is really the wide one — the deck must
  // leave the rails' columns to it, which is the whole reason it moves.
  it('narrow gives the deck the full width; wide shares its row with both rails', () => {
    const [narrow, wide] = templates();
    const deck = ['rx-main', 'rx-sub'];
    const narrowDeckRow = narrow[rowOf(narrow, 'rx-main')];
    expect(new Set(narrowDeckRow)).toEqual(new Set(deck));
    const wideDeckRow = wide[rowOf(wide, 'rx-main')];
    expect(wideDeckRow[0]).toBe(LEFT_RAIL[0]);
    expect(wideDeckRow[wideDeckRow.length - 1]).toBe(RIGHT_RAIL[0]);
  });

  // Kills: changing the threshold in one place and not the other two — the
  // number is the sum of the arrangement's own declared minimum widths and
  // the three gutters between the four columns they size. The
  // manifest declares that same width, and
  // `presentation/layouts/__tests__/flagship-probe-registration.test.ts` is
  // where the two are required to agree (this directory may not name the
  // manifest field: `stage-sizing-boundary.test.ts`).
  it('the threshold is the sum of the declared minimum track widths and the gutters', () => {
    const rail = px('--flagship-probe-rail-width');
    const strip = px('--flagship-probe-strip-min-width');
    // Four columns, three gutters. Read from the same style block as the
    // widths, so the gap and the threshold cannot drift apart either.
    const sum = 2 * rail + 2 * strip + 3 * px('gap');

    expect(px('--flagship-probe-switch-width')).toBe(sum);
    expect(Number(style.match(/@container \(min-width: (\d+)px\)/)![1])).toBe(sum);
  });
});

// ── The tracks ─────────────────────────────────────────────────────────────
//
// One `grid-template-columns` per arrangement, parsed out of the same style
// block and split into its track sizing functions at paren depth 0.

function splitTracks(body: string): string[] {
  const tracks: string[] = [];
  let depth = 0;
  let current = '';
  for (const character of body) {
    if (character === '(') depth += 1;
    if (character === ')') depth -= 1;
    if (depth === 0 && /\s/.test(character)) {
      if (current !== '') tracks.push(current);
      current = '';
    } else current += character;
  }
  if (current !== '') tracks.push(current);
  return tracks.map((track) => track.replace(/\s+/g, ' '));
}

function columnTemplates(): string[][] {
  return [...style.matchAll(/grid-template-columns:([^;]*);/g)]
    .map(([, body]) => splitTracks(body));
}

/** The largest px length a track sizing function declares, after substituting
 *  the custom properties it is written with: a rail's maximum (its declared
 *  width, the minimum being the floor), and a strip's minimum (its maximum
 *  being `1fr`, which declares no length). */
function declaredPx(track: string): number {
  const resolved = track.replace(/var\((--[a-z-]+)\)/g, (_match, name: string) => `${px(name)}px`);
  const lengths = [...resolved.matchAll(/(\d+)px/g)].map(([, value]) => Number(value));
  expect(lengths.length).toBeGreaterThan(0);
  return Math.max(...lengths);
}

/** The one shape a rail track may take. */
const RAIL_TRACK = 'minmax(var(--flagship-probe-rail-floor), var(--flagship-probe-rail-width))';

const CONTENT_SIZED = /\b(auto|min-content|max-content|fit-content)\b/;

describe('no surface may size a track', () => {
  // Kills: a track written `auto` — the shape the left rail carried, which
  // grew it to the band surface's max-content width and left the two
  // receiver strips' flexible columns at 0 — or written `min-content`,
  // `max-content` or `fit-content`, in either direction.
  it.each([[0, 'narrow'], [1, 'wide']])('column template %i (%s) declares no content-sized track', (index) => {
    const tracks = columnTemplates()[index as number];
    expect(tracks).toHaveLength(4);
    for (const track of tracks) expect(track).not.toMatch(CONTENT_SIZED);
  });

  // Kills: a rail whose maximum is anything but the declared rail width, and
  // a rail whose minimum is anything but the rail floor — including
  // the `minmax(0, …)` this rail carried before the floor existed, which let
  // it compress to any width at all.
  it.each([[0, 'narrow'], [1, 'wide']])('column template %i (%s) floors both rails at the rail floor and caps them at the declared rail width', (index) => {
    const tracks = columnTemplates()[index as number];
    expect(tracks[0]).toBe(RAIL_TRACK);
    expect(tracks[3]).toBe(tracks[0]);
  });

  // Kills: the strip columns losing their declared floor, which is the half
  // of the threshold arithmetic below that makes it true of the layout
  // rather than only of the variables.
  it('gives the wide arrangement its strip minimum as a track minimum', () => {
    const [, wide] = columnTemplates();
    expect(wide[1]).toBe('minmax(var(--flagship-probe-strip-min-width), 1fr)');
    expect(wide[2]).toBe(wide[1]);
  });

  // Kills: the threshold drifting away from the widths the wide column
  // template actually declares — the `@container` literal must be the sum of
  // those four and the three gutters between them.
  it('switches at the sum of the wide template\'s four declared widths and the gutters', () => {
    const [, wide] = columnTemplates();
    const sum = wide.reduce((total, track) => total + declaredPx(track), 0) + 3 * px('gap');
    expect(px('--flagship-probe-switch-width')).toBe(sum);
    expect(Number(style.match(/@container \(min-width: (\d+)px\)/)![1])).toBe(sum);
  });

  // Kills: dropping `min-width: 0` from a grid item, which restores the
  // item's automatic minimum size — its min-content width — and lets a
  // surface push its own box past its column; and any `width`, `min-width`
  // or `max-width` other than zero in a grid-item rule, each of which is a
  // surface claiming track width again. Scanned over the rules whose
  // selector names a grid item — `[data-zone-id`, `[data-strip-receiver`,
  // `.probe-panorama` — so a width on `.flagship-probe`, the query
  // container, is out of scope rather than a failure.
  it('caps every grid item at its column: no grid-item rule declares a width, min-width or max-width other than zero', () => {
    expect(style).toMatch(/:global\(\[data-zone-id\]\)\s*\{[^}]*min-width:\s*0/);
    expect(style).toMatch(/\.probe-panorama\s*\{[^}]*min-width:\s*0/);
    // Innermost rules only: `[^{}]` on both sides skips the `@container`
    // prelude and cannot span a nested block.
    const declared = [...style.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
      .filter(([, selector]) => /\[data-zone-id|\[data-strip-receiver|\.probe-panorama/.test(selector))
      .flatMap(([, , body]) => [...body.matchAll(/(?<![-\w(])(?:min-|max-)?width:\s*([^;{}]+)/g)])
      .map(([, value]) => value.trim());
    expect(declared.length).toBeGreaterThan(1);
    expect(declared.filter((value) => !/^0(px)?$/.test(value))).toEqual([]);
  });

  // Kills: leaving a grid item's overflow visible, which puts the part that
  // does not fit over the neighbouring column instead of inside its own.
  it('contains what does not fit inside the column it belongs to', () => {
    expect(style).toMatch(/:global\(\[data-zone-id\]\)\s*\{[^}]*overflow:\s*auto/);
    expect(style).toMatch(/\.probe-panorama\s*\{[^}]*overflow:\s*auto/);
  });
});

describe('the rail floor', () => {
  /**
   * THE RAIL RULE (art direction, 2026-09-09): a rail is as wide as the
   * widest thing that must be readable in ONE column of controls — never a
   * two-key row, and never the transmit pair, which may wrap.
   * Pseudo-localisation is a stress model, not a width source, so it fixes a
   * number only where clipping would be a safety failure — the transmit
   * key — and every other rail key is measured in English.
   *
   * Both properties are that one rule, measured 2026-09-09 in Chromium on
   * the real `FlagshipProbeSkin` mounted through the fixture-stub seam
   * (`vite.fixtures.config.ts`) from a harness entry written for the
   * measurement — `fixtures/main.ts` mounts no flagship-probe fixture. Every
   * `<button>` the ten rail zones mount was taken alone at
   * `width: max-content` plus the padding and border between its border box
   * and its zone box, and taken again with `on` and with `off` in place of
   * an unread fact:
   *
   *   safety term  — the transmit key, pseudo-localised with
   *                  `lib/i18n/pseudo.ts`'s `pseudoize()`, 185.20px. 186 is
   *                  that rounded up, and it governs both properties.
   *   English term — the widest rail key in English is the CW keyer's
   *                  `Reverse paddle: on`, 131.61px at a computed font-size
   *                  of 13.3333px, which is below 186.
   *
   * Pseudo-localised, that CW key is 191.77px with its fact unread and
   * 201.05 / 208.47 at `on` / `off`; the rule excludes all three, it being no
   * safety key. The PR body carries the full table.
   *
   * Kills: lowering the floor to make some layout fit. With the floor at 180
   * and the width left at 186, this is the only assertion in the file that
   * fails — every other one is about the FORM of the track, and
   * `minmax(var(--floor), var(--width))` keeps its form at any value of
   * either.
   */
  it('declares the measured one-key floor, and a rail width equal to it', () => {
    expect(px('--flagship-probe-rail-floor')).toBe(186);
    expect(px('--flagship-probe-rail-width')).toBe(186);
  });

  // Kills: a rail width below its own floor, which would make `minmax()`
  // resolve to the floor and leave the declared width a dead number.
  it('never declares a rail width below the floor', () => {
    expect(px('--flagship-probe-rail-floor'))
      .toBeLessThanOrEqual(px('--flagship-probe-rail-width'));
  });
});

describe('the collapsed middle track leaves no gutter behind', () => {
  // Kills: leaving the deck's former 160px RX/TX track in either column
  // template now that the transmit key has moved to the rail. A track no
  // area names is an empty column, and the two gaps on either side of it are
  // the reserved gutter this arrangement must not hold open.
  it.each([[0, 'narrow'], [1, 'wide']])('column template %i (%s) declares one track per named column, none of them empty', (index) => {
    const template = templates()[index as number];
    const tracks = columnTemplates()[index as number];
    expect(tracks).toHaveLength(template[0].length);
    for (let column = 0; column < tracks.length; column += 1) {
      expect(template.some((row) => row[column] !== '.')).toBe(true);
    }
  });
});

describe('a zone whose surface does not mount reserves nothing', () => {
  // Kills: a row sizing function or an item height, either of which would
  // keep a row open at a height of its own when the zones on it do not
  // mount. jsdom performs no layout, so this is asserted on the CSS that
  // leaves every row `auto`; the browser measurement is in the PR body.
  it('declares no row sizing and no item height', () => {
    const grid = style.slice(style.indexOf('.probe-stage'));
    expect(grid).not.toMatch(/grid-template-rows|grid-auto-rows/);
    expect(grid).not.toMatch(/(?<![-\w])(min-|max-)?height:/);
  });

  // Kills: mounting an empty box for a surface the radio does not carry,
  // which is a hole the arrangement would then hold a row open for.
  it('mounts no element for a zone whose surface the radio does not carry', () => {
    const base = mainSubCaps();
    const dropped = ['antenna', 'rx_antenna', 'scope'];
    h.caps = {
      ...base,
      scope: false,
      antennas: 0,
      capabilities: (base.capabilities as readonly string[]).filter((name) => !dropped.includes(name)),
    } as unknown as Capabilities;
    render();
    expect(q('[data-zone-id="antenna"]')).toBeNull();
    expect(q('[data-zone-id="scope-controls"]')).toBeNull();
    // The rails they sat on are still there, so the absence above is the
    // surface's own gate and not a collapsed mount.
    expect(q('[data-zone-id="band"]')).not.toBeNull();
    expect(q('[data-zone-id="rf-front-end"]')).not.toBeNull();
  });
});

describe('geometry only — the shell draws nothing', () => {
  // Kills: the shell acquiring a look of its own — a colour, a font size, a
  // border or a lamp — which is exactly what this PR is not allowed to add
  // and what its design-language exemption
  // (`presentation/languages/__tests__/layout-compatibility-inventory.test.ts`)
  // is granted on.
  it('declares no colour, font, border or shadow', () => {
    const properties = [...style.matchAll(/(^|[;{])\s*([a-z-]+)\s*:/gm)].map(([, , name]) => name);
    expect(properties.filter((name) => /^(color|background|font|border|box-shadow|text-)/.test(name)))
      .toEqual([]);
  });

  // Kills: a second grid or flex container creeping in beside the one the
  // arrangement is expressed as.
  it('declares exactly one grid and no other layout container', () => {
    expect([...style.matchAll(/display:\s*grid/g)]).toHaveLength(1);
    expect(style).not.toMatch(/display:\s*(flex|inline-flex)/);
  });

  // Kills: the semantic scope controls drifting away from the panel they
  // belong to — the spec places them directly under the panorama, in both
  // arrangements, and nothing else may come between.
  it.each([[0, 'narrow'], [1, 'wide']])('template %i (%s) puts scope-ctl directly under the panorama', (index) => {
    const template = templates()[index as number];
    const panoramaRows = template.flatMap((row, i) => (row.includes('panorama') ? [i] : []));
    expect(rowOf(template, 'scope-ctl')).toBe(Math.max(...panoramaRows) + 1);
    expect(columnsOf(template, 'scope-ctl')).toEqual(columnsOf(template, 'panorama'));
  });
});

describe('R52 — this shell announces nothing it does not know', () => {
  /** The markup between the script and the style block: everything this skin
   *  itself puts in the DOM. */
  const markup = source
    .slice(source.indexOf('</script>'), source.indexOf('<style>'))
    .replace(/<!--[\s\S]*?-->/g, '');

  // Kills: adding a dash/placeholder readout or an inert control of the
  // shell's own, the shape `dual-sdr-face`'s hand-drawn rail took.
  it('renders no placeholder and no control of its own', () => {
    expect(markup).not.toMatch(/—|&mdash;|&#8212;/);
    expect(markup).not.toMatch(/\bdisabled\b|aria-disabled/);
    expect(markup).not.toMatch(/<button|<input|<select/);
  });

  // Kills: a row, area or track made conditional on TX (or on anything else),
  // which is how the geometry would come to differ between receive and
  // transmit.
  it('has no conditional in its markup, so the geometry cannot change with state', () => {
    expect(markup).not.toContain('{#if');
    expect(markup).not.toContain('{#each');
  });
});

describe('the band key prints its name only', () => {
  /**
   * Kills: this shell omitting `bandPermitCaption` or passing `true`, and the
   * wiring not threading it to the band surface — either leaves the caption
   * printed. Also kills a suppression that takes the permit away with the
   * print: the key's accessible name and `data-default-permit` still carry
   * it, which is the same split
   * `semantic/__tests__/BandInstrumentHost.isolated.test.ts`'s "suppresses
   * only the printed caption, keeping the accessible name and the attribute"
   * pins at the surface.
   */
  it('mounts band keys with no printed permit, each still naming its permit', () => {
    render();
    const keys = qa<HTMLButtonElement>('button[data-testid^="band-choice-"]');
    expect(keys.length).toBeGreaterThan(0);
    expect(qa('[data-testid^="band-choice-permit-"]')).toEqual([]);
    for (const key of keys) {
      const name = key.dataset.testid!.slice('band-choice-'.length);
      expect(key.textContent).toBe(name);
      const label = key.getAttribute('aria-label') ?? '';
      expect(label.startsWith(`${name} — `)).toBe(true);
      expect(label.length).toBeGreaterThan(`${name} — `.length);
      expect(key.dataset.defaultPermit).toBeTruthy();
    }
  });
});
