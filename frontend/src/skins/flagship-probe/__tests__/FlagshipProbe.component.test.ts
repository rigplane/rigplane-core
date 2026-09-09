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

/** A pixel-valued custom property declared in the skin's own style block. */
function px(name: string): number {
  const hit = source.match(new RegExp(`${name}:\\s*(\\d+)px`));
  expect(hit).not.toBeNull();
  return Number(hit![1]);
}

const LEFT_RAIL = ['rf', 'dsp', 'filter', 'rx-audio', 'antenna', 'band'];
const RIGHT_RAIL = ['tx-aux', 'cw-keyer', 'rit-xit'];

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

  // Kills: stacking the receivers, or letting the RX/TX column drift out from
  // between them.
  it.each([[0, 'narrow'], [1, 'wide']])('template %i (%s) keeps the two receivers side by side', (index) => {
    const template = templates()[index as number];
    const deckRow = rowOf(template, 'rx-main');
    expect(rowOf(template, 'rx-mid')).toBe(deckRow);
    expect(rowOf(template, 'rx-sub')).toBe(deckRow);
    expect(Math.max(...columnsOf(template, 'rx-main')))
      .toBeLessThan(Math.min(...columnsOf(template, 'rx-mid')));
    expect(Math.max(...columnsOf(template, 'rx-mid')))
      .toBeLessThan(Math.min(...columnsOf(template, 'rx-sub')));
  });
});

describe('the container-width switch', () => {
  // Kills: deleting the `@container` block, which would leave the narrow
  // arrangement in force at every width.
  it('declares exactly two arrangements, and the wide one is inside a container query', () => {
    expect(templates()).toHaveLength(2);
    const container = source.slice(source.indexOf('@container'));
    expect(container).toContain('grid-template-areas');
    expect(source.indexOf('@container')).toBeLessThan(source.lastIndexOf('grid-template-areas'));
  });

  // Kills: a "narrow" template that is really the wide one — the deck must
  // leave the rails' columns to it, which is the whole reason it moves.
  it('narrow gives the deck the full width; wide shares its row with both rails', () => {
    const [narrow, wide] = templates();
    const deck = ['rx-main', 'rx-mid', 'rx-sub'];
    const narrowDeckRow = narrow[rowOf(narrow, 'rx-main')];
    expect(new Set(narrowDeckRow)).toEqual(new Set(deck));
    const wideDeckRow = wide[rowOf(wide, 'rx-main')];
    expect(wideDeckRow[0]).toBe(LEFT_RAIL[0]);
    expect(wideDeckRow[wideDeckRow.length - 1]).toBe(RIGHT_RAIL[0]);
  });

  // Kills: changing the threshold in one place and not the other two — the
  // number is the sum of the arrangement's own declared minimum widths. The
  // manifest declares that same width, and
  // `presentation/layouts/__tests__/flagship-probe-registration.test.ts` is
  // where the two are required to agree (this directory may not name the
  // manifest field: `stage-sizing-boundary.test.ts`).
  it('the threshold is the sum of the declared minimum track widths', () => {
    const rail = px('--flagship-probe-rail-width');
    const strip = px('--flagship-probe-strip-min-width');
    const rxTx = px('--flagship-probe-rx-tx-min-width');
    const sum = 2 * rail + 2 * strip + rxTx;

    expect(px('--flagship-probe-switch-width')).toBe(sum);
    expect(Number(source.match(/@container \(min-width: (\d+)px\)/)![1])).toBe(sum);
  });
});

describe('geometry only — the shell draws nothing', () => {
  const style = source.slice(source.indexOf('<style>'), source.indexOf('</style>'))
    .replace(/\/\*[\s\S]*?\*\//g, '');

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
