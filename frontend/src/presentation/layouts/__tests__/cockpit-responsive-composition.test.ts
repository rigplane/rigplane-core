/**
 * MOR-1069 — the dual-receiver cockpit's RESPONSIVE COMPOSITION policy, held
 * against the two descriptions that must agree: the manifest's declared
 * reflow breakpoints and the shell's actual media queries.
 *
 * Why source text and not a rendered assertion: jsdom does not evaluate media
 * queries, so a mounted tree can say nothing about which arrangement a
 * viewport gets. The established idiom for this in the codebase is a textual
 * pin over the stylesheet (`components-v2/theme/__tests__/tokens-contrast.
 * test.ts`, MOR-1233) and over a registry file that cannot be imported
 * (`cockpit-topology-adaptation.test.ts` F8, MOR-1068). The DOM half of this
 * ticket — element identity across an orientation change, focus order, the
 * bound rx-tx zone — is a real mounted test in `skins/dual-receiver-cockpit/
 * __tests__/DualReceiverCockpit.component.test.ts`.
 *
 * This file lives under `presentation/layouts/` deliberately: it is the only
 * place allowed to name the frozen sizing field (MOR-1247 guard,
 * `./stage-sizing-boundary.test.ts`), and the breakpoint agreement below has
 * to read it. It declares only — nothing here consumes it to make a layout
 * decision.
 *
 * Each test's doc line names the mutation it exists to kill.
 */
import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';
// Barrel, never '../dual-receiver-cockpit' — a direct manifest import fires
// `registerLayout` from this file and, under the fast pool's `isolate: false`,
// leaks that registration into sibling files (the MOR-1092 lesson).
import { dualReceiverCockpitLayout } from '../declarations';

const SHELL_PATH = 'src/skins/dual-receiver-cockpit/DualReceiverCockpit.svelte';
const WIRING_PATH = 'src/components-v2/wiring/SemanticRadioSurfaces.svelte';

/** Comments are prose ABOUT the policy and must never satisfy a scan FOR it —
 *  the shell's header comment names `matchMedia` and `transition` precisely
 *  because it forbids them. */
function withoutComments(source: string): string {
  return source
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '');
}

const shellSource = withoutComments(readFileSync(SHELL_PATH, 'utf8'));
const wiringSource = withoutComments(readFileSync(WIRING_PATH, 'utf8'));
const shellStyles = shellSource.slice(shellSource.indexOf('<style>'));

interface MediaBlock { readonly query: string; readonly body: string }

/** Brace-matched extraction — a media block contains nested rule braces, so a
 *  non-greedy regex would stop at the first inner `}`. */
function mediaBlocks(css: string): MediaBlock[] {
  const out: MediaBlock[] = [];
  const opener = /@media([^{]+)\{/g;
  let match: RegExpExecArray | null;
  while ((match = opener.exec(css)) !== null) {
    let depth = 1;
    let i = opener.lastIndex;
    while (i < css.length && depth > 0) {
      if (css[i] === '{') depth += 1;
      else if (css[i] === '}') depth -= 1;
      i += 1;
    }
    out.push({ query: match[1].trim(), body: css.slice(opener.lastIndex, i - 1) });
    opener.lastIndex = i;
  }
  return out;
}

function selectorsOf(body: string): string[] {
  return body
    .split('}')
    .map((rule) => rule.split('{')[0].trim())
    .filter(Boolean)
    .flatMap((selector) => selector.split(',').map((s) => s.trim()))
    .filter(Boolean);
}

const blocks = mediaBlocks(shellStyles);
const widthBlocks = blocks.filter((b) => /-width:/.test(b.query));
const find = (predicate: (b: MediaBlock) => boolean): MediaBlock | undefined => blocks.find(predicate);

describe('the declared reflow breakpoints and the shell agree, in both directions', () => {
  // Kills: leaving the manifest at the pre-MOR-1069 empty breakpoint list
  // while the shell reflows anyway — a layout that lies about being
  // responsive, which is the exact drift MOR-1094 avoided by recording
  // mobile's real threshold instead of a nominal one.
  it('the manifest declares the two thresholds as fluid reflow breakpoints', () => {
    expect(dualReceiverCockpitLayout.stageSizing).toEqual({
      mode: 'fluid',
      responsiveBreakpoints: [768, 1024],
    });
  });

  // Kills: moving a media threshold (or adding a third band) without
  // re-declaring it, AND declaring a breakpoint the shell never implements.
  // `max-width: N` is the band BELOW breakpoint N+1, `min-width: N` the band
  // at breakpoint N — so both spellings normalize onto the same number.
  it('every media threshold in the shell is a declared breakpoint, and vice versa', () => {
    const fromCss = new Set<number>();
    for (const block of widthBlocks) {
      for (const [, kind, px] of block.query.matchAll(/\((min|max)-width:\s*(\d+)px\)/g)) {
        fromCss.add(kind === 'min' ? Number(px) : Number(px) + 1);
      }
    }
    const sizing = dualReceiverCockpitLayout.stageSizing;
    const declared = sizing.mode === 'fluid' ? [...sizing.responsiveBreakpoints] : [];
    expect([...fromCss].sort((a, b) => a - b)).toEqual(declared.sort((a, b) => a - b));
  });
});

describe('portrait-mobile ruling: STACK, not exclude', () => {
  // The ruling, pinned at the manifest end. A fluid layout has no arithmetic
  // exclusion from a portrait phone the way a fixed-native one does, and the
  // two ways to build one back (a viewport consumer of the frozen sizing
  // field, or a second mobile behavior state machine) are both banned. Kills:
  // repurposing `fallbackLayoutId` as a viewport escape hatch, which would
  // also silently break MOR-1068's frozen topology table.
  it('stays fluid, and keeps a TOPOLOGY fallback rather than a viewport one', () => {
    expect(dualReceiverCockpitLayout.stageSizing.mode).toBe('fluid');
    expect(dualReceiverCockpitLayout.fallbackLayoutId).toBe('sdr-test');
  });

  // Kills: gating the compact stack on portrait, which is what "quietly loses
  // the portrait-mobile property" looks like in CSS — a phone in LANDSCAPE is
  // still ~700px wide and still cannot carry two channel strips.
  it('the compact band stacks to one column in BOTH orientations', () => {
    const compact = find((b) => /max-width:\s*767px/.test(b.query));
    expect(compact).toBeDefined();
    expect(compact!.query).not.toMatch(/orientation/);
    expect(compact!.body).toMatch(/grid-template-columns:\s*1fr/);
  });

  // Kills: dropping the orientation axis and stacking the whole tablet band
  // (a 1024x768 landscape tablet has the width for two strips and would lose
  // them), or extending the portrait stack up into desktop widths.
  it('the tablet band stacks in PORTRAIT only', () => {
    const tabletPortrait = find((b) => /orientation:\s*portrait/.test(b.query));
    expect(tabletPortrait).toBeDefined();
    expect(tabletPortrait!.query).toMatch(/min-width:\s*768px/);
    expect(tabletPortrait!.query).toMatch(/max-width:\s*1023px/);
    expect(tabletPortrait!.body).toMatch(/grid-template-columns:\s*1fr/);
  });
});

describe('nothing is hidden by a breakpoint (the MOR-557 / F1-mirror lens)', () => {
  // The ticket's "hidden secondary zones do not destroy active runtime
  // resources" is satisfied here by never hiding one. Kills: answering
  // "narrow" with `display: none` on a strip, the global row or the RX/TX
  // zone — a control removed by viewport is capability the operator silently
  // lost, with no other way to reach it in this layout.
  it('no media block hides a zone or a control', () => {
    for (const block of blocks) {
      expect(block.body).not.toMatch(/display:\s*none/);
      expect(block.body).not.toMatch(/visibility:\s*hidden/);
      expect(block.body).not.toMatch(/content-visibility:\s*hidden/);
    }
  });

  // Kills: a responsive rule that escapes the cockpit. The shell reaches into
  // the SHARED wiring's classes with `:global(...)`, and sdr-test / LCD /
  // mobile mount that same wiring — an unrooted selector would re-compose all
  // of them from here.
  it('every responsive selector is rooted at the cockpit', () => {
    const selectors = blocks.flatMap((b) => selectorsOf(b.body));
    expect(selectors.length).toBeGreaterThan(0);
    for (const selector of selectors) {
      expect(selector).toContain('.dual-receiver-cockpit');
    }
  });
});

describe('DOM order stays focus order at every breakpoint', () => {
  // "Keyboard and touch order remain logical" has exactly one mechanical
  // reading in a CSS-only composition: the reflow must never reorder boxes
  // away from source order, because tab order follows the DOM and touch
  // follows the pixels. Kills: `order:`, a `-reverse` flow, `dense` packing,
  // or lifting a zone out of flow to re-place it.
  it('the shell uses no order-divorcing property anywhere in its styles', () => {
    expect(shellStyles).not.toMatch(/(?:^|[;{\s])order\s*:/);
    expect(shellStyles).not.toMatch(/-reverse/);
    expect(shellStyles).not.toMatch(/grid-auto-flow:[^;]*dense/);
    expect(shellStyles).not.toMatch(/position:\s*(?:absolute|fixed)/);
  });
});

describe('reduced motion: the reflow has no motion to reduce', () => {
  // Kills: animating the reflow (a transition on grid-template-columns, a
  // slide between arrangements). The app-wide `prefers-reduced-motion` block
  // in styles/animations.css only shortens durations — a layout that composes
  // its arrangement change as motion still moves under it. Declaring none is
  // the honest floor; if motion is ever wanted here it must arrive inside a
  // `(prefers-reduced-motion: no-preference)` block, which this scan forces
  // the author to notice.
  it('declares no transition, animation or keyframes', () => {
    expect(shellStyles).not.toMatch(/(?:^|[;{\s])transition\b/);
    expect(shellStyles).not.toMatch(/(?:^|[;{\s])animation\b/);
    expect(shellStyles).not.toMatch(/@keyframes/);
  });
});

describe('touch targets are keyed off the pointer, never off a width', () => {
  // MOR-1160's note, made mechanical: cockpit chrome is fluid and never
  // uniformly scaled precisely because scaled controls fall below the minimum
  // hit size. Kills: dropping the floor, or re-keying it to a narrow width so
  // a touch laptop at desktop width gets mouse-sized controls.
  it('a pointer: coarse block gives every cockpit control a >= 44px hit box', () => {
    const coarse = find((b) => /pointer:\s*coarse/.test(b.query));
    expect(coarse).toBeDefined();
    expect(coarse!.query).not.toMatch(/-width:/);
    for (const axis of ['min-height', 'min-width']) {
      const declared = [...coarse!.body.matchAll(new RegExp(`${axis}:\\s*(\\d+)px`, 'g'))]
        .map((m) => Number(m[1]));
      expect(declared.length).toBeGreaterThan(0);
      expect(Math.min(...declared)).toBeGreaterThanOrEqual(44);
    }
    expect(coarse!.body).toMatch(/:global\(button\)/);
    expect(coarse!.body).toMatch(/:global\(\[role='switch'\]\)/);
  });
});

describe('no second mobile behavior state machine (the ticket\'s owned-area rule)', () => {
  // Kills: re-implementing the reflow in JS — a `matchMedia` subscription, a
  // resize/orientation listener, or width/height state. Beyond duplicating
  // MobileRadioLayout's machine, a JS-driven recomposition can REMOUNT the
  // surfaces on rotation, and that is what puts the TX lease identity and
  // live runtime resources at risk. The mounted counterpart (element identity
  // survives a rotation) is in the shell's component test.
  it.each([
    ['the cockpit shell', () => shellSource],
    ['the shared wiring', () => wiringSource],
  ])('%s observes no viewport signal', (_name, read) => {
    const source = read();
    expect(source).not.toMatch(/matchMedia/);
    expect(source).not.toMatch(/orientationchange/);
    expect(source).not.toMatch(/addEventListener\(\s*['"]resize['"]/);
    expect(source).not.toMatch(/window\.inner(?:Width|Height)/);
    expect(source).not.toMatch(/ResizeObserver/);
  });

  // MOR-1069 (N1) at source level. The rx-tx zone wrapper is now rendered
  // only in the dual composition, and the branch is a `{#snippet}` render so
  // there is exactly ONE `<RxTxSurface>` tag in the file. Kills: closing the
  // branch by duplicating the surface tag — two tags is one edit away from
  // two mounted key controls, and single TX authority is this layout's
  // hardest invariant.
  it('the wiring declares exactly one RxTxSurface tag', () => {
    expect(wiringSource.match(/<RxTxSurface\b/g)).toHaveLength(1);
  });
});
