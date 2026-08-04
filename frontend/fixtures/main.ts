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
import {
  runAssertions, styleProbe, tokenSnapshot, type AssertionOptions,
} from './assertions';
import { fixtureById } from './catalog';
import { harness, IDLE_TX } from './harness-state';

const params = new URLSearchParams(window.location.search);
const id = params.get('fixture') ?? 'topology-2-main-sub';
const fixture = fixtureById(id);
if (!fixture) throw new Error(`MOR-1070 harness: unknown fixture id "${id}"`);

if ((params.get('theme') ?? 'v2') === 'v2') {
  await import('../src/components-v2/theme/index');
}

harness.state = fixture.state();
harness.caps = fixture.caps();
harness.tx = { ...IDLE_TX, ...fixture.tx };
harness.modGuard = fixture.modGuard ?? { visible: false, sourceLabel: null };
harness.calls = [];

document.title = `MOR-1070 · ${fixture.id}`;
mount(DualReceiverCockpit, { target: document.getElementById('app')! });
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
    };
  }
}

window.__harness = {
  fixture: fixture.id,
  what: fixture.what,
  assert: (options: AssertionOptions = {}) => runAssertions(fixture.expect, options),
  tokens: tokenSnapshot,
  paint: styleProbe,
  calls: () => harness.calls,
  /** A stable identifier per focusable control, in DOM order — the tab sequence. */
  focusOrder: () => [...document.querySelectorAll<HTMLElement>(
    '[data-testid="dual-receiver-cockpit"] button, '
    + '[data-testid="dual-receiver-cockpit"] input, '
    + '[data-testid="dual-receiver-cockpit"] select, '
    + '[data-testid="dual-receiver-cockpit"] a[href], '
    + '[data-testid="dual-receiver-cockpit"] [tabindex]',
  )].map(describe),
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
