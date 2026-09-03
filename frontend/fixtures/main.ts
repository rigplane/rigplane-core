/**
 * MOR-1070 — fixture harness entry.
 *
 * WHY THIS EXISTS. `skins/registry.ts:resolveSkinId()` has no branch that can
 * return `'dual-receiver-cockpit'` and `LayoutMode` has no matching value, so
 * the cockpit is unreachable through app navigation on `cdf0e99d` — no URL
 * param, no stored preference, no picker. This page therefore mounts the skin
 * DIRECTLY over fixture props, bypassing navigation entirely, which is also
 * why it can stay verification-only: routing the cockpit into the app is a
 * product decision (acceptance-package gate item (a)), not a capture task.
 *
 * Query parameters:
 *   ?fixture=<id>    one of `catalog.ts`'s FIXTURES ids (required)
 *   &theme=v2|none   load the components-v2 theme layer (default `v2`)
 *   &language=<id>   opt into a design language (MOR-1073; default: none)
 *   &mode=light      the language's light variant (explicit; dark is primary)
 *
 * `theme=none` is not a styling preference — it is the honest reading of what
 * the cockpit gets today: `components-v2/theme/index` is imported by
 * `RadioLayout` / `LcdLayout` only, and skins are code-split, so a cockpit
 * mounted on its own resolves every `--v2-*` token to its in-component
 * fallback. Both are captured; the difference is named in the manifest.
 */
import { flushSync, mount } from 'svelte';
import '../src/app.css';
import { desktopV2Layout, dualReceiverCockpitLayout } from '../src/presentation/layouts/declarations';
import { readWorkspace } from '../src/presentation/workspace/contract';
import { resolveSurfacePlan, SURFACE_PLAN_CONTEXT_KEY } from '../src/presentation/workspace/resolution';
import DualReceiverCockpit from '../src/skins/dual-receiver-cockpit/DualReceiverCockpit.svelte';
import PeerSplitLayout from '../src/skins/segmentline/PeerSplitLayout.svelte';
// MOR-2253 slice 1 F1 (verifier BLOCKED): this is the harness's own mount of
// the peer-split glass, the one other production/harness call site besides
// `components-v2/layout/LcdLayout.svelte` — that file passes canvasW/canvasH
// as required props now; this one did not, and mounted with an implicit
// `undefined` for both, which `ScaledStage` cannot guard against (`NaN`
// compares false against `<= 0`) and Svelte's `style:` directive drops
// silently rather than writing `"undefinedpx"`. Sourced from the group
// declaration, not literals: this file sits outside `src/`, where the
// "declared once" contour scan (`presentation/groups/__tests__/
// contract.test.ts`) does not reach, so a literal here would be invisible
// to the very guard this slice exists to add.
import { peerSplitGlassGroup } from '../src/presentation/groups/declarations';
import ReferenceLayout from './ReferenceLayout.svelte';
import {
  runAssertions, styleProbe, tokenSnapshot, type AssertionOptions,
} from './assertions';
import { clearCapabilities, setCapabilities } from '../src/lib/stores/capabilities.svelte';
import { fixtureById } from './catalog';
import { DEFAULT_AUDIO_RUNTIME, harness, IDLE_TX } from './harness-state';

const params = new URLSearchParams(window.location.search);
const id = params.get('fixture') ?? 'topology-2-main-sub';
const fixture = fixtureById(id);
if (!fixture) throw new Error(`MOR-1070 harness: unknown fixture id "${id}"`);

/**
 * MOR-1085 — which of the layouts this fixture mounts. The fixture ITSELF
 * carries this (not a separate `&layout=` query param) so one fixture id is
 * one grid cell: `catalog.ts`'s `toReferenceFixture` derives every
 * `--reference` id from its `dual-receiver-cockpit` sibling, and the two
 * always mount the corresponding real component. See `ReferenceLayout.svelte`
 * for why that component, rather than the full `RadioLayout`, stands in for
 * "the reference current layout" here.
 *
 * MOR-2153 adds `'peer-split'`, rooted at `PeerSplitLayout.svelte`'s own
 * `data-testid="peer-split-glass"` wrapper — the glass, not the stage holder
 * around it, since the holder is chrome-adjacent plumbing (Lesson 4 in that
 * file's header) rather than the composition `focusOrder()`/`activeControl()`
 * below are describing.
 */
const ROOT_TEST_ID = fixture.layout === 'reference' ? 'reference-layout'
  : fixture.layout === 'peer-split' ? 'peer-split-glass'
    : 'dual-receiver-cockpit';

if ((params.get('theme') ?? 'v2') === 'v2') {
  await import('../src/components-v2/theme/index');
}

// MOR-1073/MOR-1074: design languages are scoped to `[data-design-language]`,
// which is their ONLY activation mechanism (owner decision Q2) — and they have
// no activation path in the app yet (routing one in is cutover work,
// MOR-1048/MOR-1263). This param is the only opt-in, so the harness can capture
// each language over the same fixtures the unstyled baselines use.
const LANGUAGE_STYLESHEETS: Record<string, () => Promise<unknown>> = {
  studioline: () => import('../src/presentation/languages/studioline/studioline.css'),
  fieldline: () => import('../src/presentation/languages/fieldline/fieldline.css'),
  segmentline: () => import('../src/presentation/languages/segmentline/segmentline.css'),
};
/**
 * MOR-2153 — `peer-split` is segmentline's OWN skin: with no language
 * activated it renders as unstyled markup (no amber glass, no bezel colour,
 * no cell/readout treatment), which defeats the point of looking at it. The
 * explicit `&language=` param still wins when given (e.g. to inspect the
 * bare DOM), matching how every other fixture already lets the query
 * string override any default.
 */
const language = params.get('language') ?? (fixture.layout === 'peer-split' ? 'segmentline' : null);
if (language) {
  const load = LANGUAGE_STYLESHEETS[language];
  if (!load) throw new Error(`MOR-1074 harness: unknown design language "${language}"`);
  await load();
  document.documentElement.dataset.designLanguage = language;
  // Light is an explicit opt-in, never an OS-preference flip: the app's own
  // light/dark is a manual `[data-theme]` choice, so language and surface have
  // to be switched by the same deliberate act.
  if (params.get('mode') === 'light') document.documentElement.dataset.languageMode = 'light';
}

harness.state = fixture.state();
harness.caps = fixture.caps();
// MOR-1451: `harness.caps` above feeds the STUBBED `runtime.caps` seam only
// (`fixtures/stubs/runtime.ts`) — components that read capability-derived
// calibration data (e.g. `smeter-scale.ts`'s `getSmeterCalibration()`) go
// through the REAL, unstubbed `$lib/stores/capabilities.svelte` singleton
// instead, which production populates only via a live WebSocket
// (`ws-client.ts`). The fixture harness opens no WebSocket, so that
// singleton must be populated directly here for the S-meter (or any other
// capabilities-calibrated readout) to render as the fixture intends rather
// than falling back to the honest-uncalibrated path.
// `Fixture.caps` is nullable — `caps-unloaded` returns null — so the unloaded
// case clears the singleton instead of pushing null through `setCapabilities`,
// which takes a non-null `Capabilities`.
const fixtureCaps = fixture.caps();
if (fixtureCaps) setCapabilities(fixtureCaps);
else clearCapabilities();
harness.tx = { ...IDLE_TX, ...fixture.tx };
harness.modGuard = fixture.modGuard ?? { visible: false, sourceLabel: null };
harness.audioRuntime = { ...DEFAULT_AUDIO_RUNTIME, ...fixture.audioRuntime };
harness.calls = [];

/**
 * MOR-1355 — the ONE place this harness can supply a real resolved
 * `SurfacePlan`, exactly the recipe
 * `DualReceiverCockpit.component.test.ts`'s `render(plan?)` already proves:
 * `mount`'s `context` option carries `SURFACE_PLAN_CONTEXT_KEY` down to
 * `useSurfacePlan()`, so `SemanticRadioSurfaces.svelte`'s `zoneOwning()` stops
 * being unconditionally null. `fixture.planned` is opt-in and per-fixture —
 * every fixture that does not set it keeps mounting exactly as before
 * (`context: undefined`, the pre-MOR-1355 plan-less shape catalog.ts's
 * `baseCaps` comment still documents), which is deliberate: the ticket's
 * instruction is to prove the plan-ful path exists, not to make it the only
 * path — the plan-less captures still model the real gap MOR-1351 found
 * (`fixtures/main.ts` supplying no plan at all).
 *
 * `readWorkspace({ version: 1 }).workspace` is the DEFAULT workspace — no
 * operator `visibleSurfaces`/`zoneOrder` preference — over the REAL
 * `dualReceiverCockpitLayout` manifest, i.e. exactly what `App` resolves for
 * this layout the moment no preference has been saved. This is
 * production's own default, not a fixture invention.
 *
 * MOR-1379 — the reference/single composition's own residual: every
 * `--reference` fixture (`layout === 'reference'`, `ReferenceLayout.svelte`)
 * stands in for `desktop-v2`/`sdr-test`'s real wiring (see that file's own
 * header comment), but until now always mounted with `context: undefined` —
 * no plan at all, regardless of `fixture.planned` (which `toReferenceFixture`
 * in catalog.ts never sets). That made `desktopV2Layout` — the manifest S6a
 * through S6b-2 have been declaring zones onto, one S-slice at a time — a
 * manifest this harness never resolved, so every zoned optional surface
 * (`txAux`, `meters`, `scopeDisplay`, `filter`, `rfFrontEnd`, `band`,
 * `antenna`, `ritXitScan`, `rxAudio`, `dsp`, `cwKeyer`, `scopeControls`)
 * rendered bare there, indistinguishable from undeclared. Unlike the cockpit
 * side above, this is not an opt-in: EVERY reference-layout fixture now
 * resolves `desktopV2Layout`'s default-workspace plan, because "this stands
 * in for desktop-v2" is what `ReferenceLayout.svelte` already claims
 * unconditionally, not a per-fixture experiment — the same default-workspace
 * recipe as the cockpit's `dualReceiverCockpitLayout` resolution above.
 */
/**
 * MOR-2153 — `peer-split` fixtures never set `fixture.planned` (see
 * `PEER_SPLIT_FIXTURES` in `catalog.ts`), so `plan` falls through to `null`
 * for them here unchanged, on purpose: `PeerSplitLayout.svelte`'s single
 * declared zone (`peer-columns`: vfo+rxTx) does not gate anything this
 * chassis currently mounts — `.channel-strips`/`.cockpit-global-row`/
 * `.rx-tx-zone` render unconditionally and `txAux`/`meters`/`scopeDisplay`
 * render bare either way (their `allowBare` default is `true`) — so
 * resolving a plan here would add plumbing with no observable effect. See
 * `SemanticRadioSurfaces.svelte`'s MOR-2150 comment for the nine surfaces a
 * plan WOULD matter for; none is declared for `peer-split` yet.
 */
const plan = fixture.layout === 'reference'
  ? resolveSurfacePlan(desktopV2Layout, readWorkspace({ version: 1 }).workspace)
  : fixture.planned
    ? resolveSurfacePlan(dualReceiverCockpitLayout, readWorkspace({ version: 1 }).workspace)
    : null;
const context = plan === null
  ? undefined
  : new Map<unknown, unknown>([[SURFACE_PLAN_CONTEXT_KEY, () => plan]]);

document.title = `MOR-1070 · ${fixture.id}`;
const target = document.getElementById('app')!;
// `peer-split` needs its own mount call: it is the only one of the three
// with required props (canvasW/canvasH, see the import comment above), and
// `mount()`'s Props parameter cannot be inferred correctly across a single
// call shared with two components that take none.
if (fixture.layout === 'peer-split') {
  mount(PeerSplitLayout, {
    target,
    context,
    props: {
      canvasW: peerSplitGlassGroup.canvas.w,
      canvasH: peerSplitGlassGroup.canvas.h,
      minScale: peerSplitGlassGroup.scaling.minScale,
    },
  });
} else {
  mount(fixture.layout === 'reference' ? ReferenceLayout : DualReceiverCockpit, { target, context });
}
flushSync();

declare global {
  interface Window {
    __harness: {
      fixture: string;
      what: string;
      assert: (options?: AssertionOptions) => ReturnType<typeof runAssertions>;
      tokens: () => Record<string, string>;
      paint: () => Record<string, Record<string, string>>;
      calls: () => typeof harness.calls;
      focusOrder: () => string[];
      /** MOR-1087 checklist item 2: the SAME per-control descriptor
       *  `focusOrder()` uses, for whichever element is focused RIGHT NOW —
       *  `'NONE'` for `document.body`/nothing focused. `capture.mjs` samples
       *  this before and after a viewport resize to prove focus survives an
       *  orientation/layout reflow rather than silently dropping to `body`. */
      activeControl: () => string;
    };
  }
}

window.__harness = {
  fixture: fixture.id,
  what: fixture.what,
  // MOR-2153: `fixture.expect` is absent for `peer-split` fixtures —
  // `runAssertions` (`assertions.ts`, lines 171-683 — 513 lines) is written
  // against the cockpit/reference `zonedComposition` binary, which the
  // five-band chassis is neither; see the field's own doc comment on
  // `Fixture` in catalog.ts. Skip the pipeline rather than pass it a shape
  // it cannot check.
  assert: (options: AssertionOptions = {}) => (fixture.expect
    ? runAssertions(fixture.expect, { ...options, rootTestId: ROOT_TEST_ID })
    : [{
      name: 'peer-split-no-assertion-pipeline', ok: true,
      detail: 'peer-split fixtures carry no behavior-assertion pipeline yet (MOR-2153) — this '
        + 'confirms the harness mounted, not that the composition is correct.',
    }]),
  tokens: tokenSnapshot,
  paint: styleProbe,
  calls: () => harness.calls,
  /** A stable identifier per focusable control, in DOM order — the tab sequence. */
  focusOrder: () => [...document.querySelectorAll<HTMLElement>(
    `[data-testid="${ROOT_TEST_ID}"] button, `
    + `[data-testid="${ROOT_TEST_ID}"] input, `
    + `[data-testid="${ROOT_TEST_ID}"] select, `
    + `[data-testid="${ROOT_TEST_ID}"] a[href], `
    + `[data-testid="${ROOT_TEST_ID}"] [tabindex]`,
  )].map(describe),
  activeControl: () => {
    const el = document.activeElement;
    return el === null || el === document.body ? 'NONE' : describe(el as HTMLElement);
  },
};

function describe(el: HTMLElement): string {
  const zone = (el.closest('[data-zone-id]') as HTMLElement | null)?.dataset.zoneId ?? 'NO-ZONE';
  const strip = (el.closest('[data-testid^="channel-strip-"]') as HTMLElement | null)
    ?.dataset.stripReceiver;
  const name = el.dataset.testid
    ?? (el.hasAttribute('data-vfo-select') ? 'vfo-select'
      : el.hasAttribute('data-vfo-split') ? 'vfo-split'
        : el.hasAttribute('data-vfo-dual-watch') ? 'vfo-dual-watch'
          : el.tagName.toLowerCase());
  return `${zone}${strip ? `/${strip}` : ''}:${name}${el.matches(':disabled') ? '[disabled]' : ''}`;
}

document.body.dataset.harnessReady = 'true';
