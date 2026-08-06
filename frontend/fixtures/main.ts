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
import DualReceiverCockpit from '../src/skins/dual-receiver-cockpit/DualReceiverCockpit.svelte';
import ReferenceLayout from './ReferenceLayout.svelte';
import {
  runAssertions, styleProbe, tokenSnapshot, type AssertionOptions,
} from './assertions';
import { fixtureById } from './catalog';
import { harness, IDLE_TX } from './harness-state';

const params = new URLSearchParams(window.location.search);
const id = params.get('fixture') ?? 'topology-2-main-sub';
const fixture = fixtureById(id);
if (!fixture) throw new Error(`MOR-1070 harness: unknown fixture id "${id}"`);

/**
 * MOR-1085 — which of the two layouts this fixture mounts. The fixture ITSELF
 * carries this (not a separate `&layout=` query param) so one fixture id is
 * one grid cell: `catalog.ts`'s `toReferenceFixture` derives every
 * `--reference` id from its `dual-receiver-cockpit` sibling, and the two
 * always mount the corresponding real component. See `ReferenceLayout.svelte`
 * for why that component, rather than the full `RadioLayout`, stands in for
 * "the reference current layout" here.
 */
const ROOT_TEST_ID = fixture.layout === 'reference' ? 'reference-layout' : 'dual-receiver-cockpit';

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
};
const language = params.get('language');
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
harness.tx = { ...IDLE_TX, ...fixture.tx };
harness.modGuard = fixture.modGuard ?? { visible: false, sourceLabel: null };
harness.calls = [];

document.title = `MOR-1070 · ${fixture.id}`;
mount(fixture.layout === 'reference' ? ReferenceLayout : DualReceiverCockpit, {
  target: document.getElementById('app')!,
});
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
  assert: (options: AssertionOptions = {}) =>
    runAssertions(fixture.expect, { ...options, rootTestId: ROOT_TEST_ID }),
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
