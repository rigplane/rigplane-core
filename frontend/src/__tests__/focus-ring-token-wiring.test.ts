/**
 * MOR-1232 built the shared focus tokens and pinned, among other things,
 * that the global default must be `outline`-shaped: a global `box-shadow`
 * (0,1,0) loses to any Svelte-scoped component shadow (0,2,0)+, and
 * outline was the one property components never set decoratively.
 *
 * MOR-2522 (owner rulings 2026-09-21/22: "no focus frame anywhere", with a
 * screenshot of the AF LEVEL slider carrying a 2 px cyan rectangle after a
 * mouse click) repaints focus as LIGHT while keeping that cascade
 * discipline in plain sight. Correction round (2026-09-22 review):
 *
 *   1. app.css `:focus-visible` → `outline: none` + a box-shadow-carried
 *      ring+halo in the per-skin knob colour. NO `filter` carrier exists
 *      anywhere in the contract: a filter on a focused element makes it
 *      the containing block for its fixed/absolute descendants (re-
 *      anchoring open popovers — the failure RadioLayout.svelte documents
 *      at its link-fault veil), forces offscreen compositing, and paints
 *      nothing on a transparent backdrop (ScopeSettingsPopover).
 *   2. tokens.css owns the `--v2-focus-*` set: one colour knob
 *      (`--v2-focus-ring-color`, contrast-pinned per skin ≥ 3:1 below),
 *      a full-opacity 2 px ring (the obligation bearer), an inset rim
 *      form for hosts that clip or animate their own shadow, and a
 *      decorative 55% halo. The composite numbers as rendered are pinned
 *      below — the ring carries the 3:1 obligation, not the halo.
 *   3. The cascade cost of the box-shadow carrier is paid explicitly: any
 *      scoped component box-shadow replaces the global light, so the
 *      masked sites this PR claims carry their own scoped illumination —
 *      value-control.css (all five renderers + wheel-armed), TxPanel's
 *      PTT held/latched state, PttFab (whose latch pulse ANIMATES the
 *      element shadow, so its rim rides ::after), and the shared v2
 *      button in control-button.css (same ::after trick; the deck scope
 *      suppresses it because the deck keeps its owner-approved brightness
 *      steps).
 *   4. The claim of this PR is exactly: the value-control sliders, the
 *      VFO deck, and the app-level default. The remaining literal outline
 *      sites (BandSelector, fieldline.css, semantic-controls.css, …)
 *      keep their existing VISIBLE outlines and move onto the contract in
 *      PR 2 — they are enumerated in the explicit allow-list below, and a
 *      stale allow-list entry fails its companion test.
 *   5. wheel-control.ts no longer paints an inline `style.outline` (the
 *      actual click-frame from the owner's screenshot); a guard below
 *      fails on any inline outline assignment anywhere in frontend/src.
 *
 * Perceivability (MOR-977 §1.2.5 "never re-suppressed"): the ring is the
 * knob at full opacity, recomputed ≥ 3:1 against every surface of every
 * skin (dark and light) in the matrix below; `prefers-contrast: more`
 * widens ring and halo; `forced-colors` (where shadows do not paint)
 * restores a system outline. Nothing changes geometry: box-shadow paints
 * outside the box, and the tests forbid size-affecting declarations in
 * every focus rule of the contract.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { extname, join } from 'node:path';
import { describe, expect, it } from 'vitest';

const FRONTEND_SRC = join(__dirname, '..');
const THEME_DIR = join(FRONTEND_SRC, 'components-v2/theme');

function read(relPath: string): string {
  return readFileSync(join(FRONTEND_SRC, relPath), 'utf-8');
}

/* ── shared CSS helpers ──────────────────────────────────────────────────── */

function styleText(path: string, text: string): string {
  if (extname(path) !== '.svelte') return text;
  return [...text.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((m) => m[1]).join('\n');
}

function stripComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, '');
}

/** Innermost rule blocks. `[^{}]` can't cross a brace, so a rule inside an
 * at-rule wrapper (`@media … { .a { … } }`) yields the inner rule with the
 * wrapper's trailing context in the selector — selectors are therefore
 * compared with `.includes`/normalization, never assumed to be standalone. */
function ruleBlocks(css: string): Array<{ selector: string; body: string }> {
  const out: Array<{ selector: string; body: string }> = [];
  const re = /([^{}]+)\{([^{}]*)\}/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(css))) out.push({ selector: m[1].replace(/\s+/g, ' ').trim(), body: m[2] });
  return out;
}

function walk(dir: string, extensions: string[], out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules' || entry.startsWith('.')) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, extensions, out);
    else if (extensions.includes(extname(entry))) out.push(full);
  }
  return out;
}

const GEOMETRY = /\b(?:width|height|padding|margin|border(?:-width)?)\s*:/;
const OUTLINE_DECL = /(?<![\w-])outline\s*:/;

function globalFocusRule(): string {
  const rule = stripComments(read('app.css')).match(/:focus-visible\s*\{[^}]*\}/);
  expect(rule, 'expected a top-level :focus-visible rule in app.css').not.toBeNull();
  return rule![0].replace(/\s+/g, ' ');
}

/** alpha-composite `over` onto `under` per channel in 8-bit sRGB — the
 * arithmetic `color-mix(in srgb, over N%, transparent)` performs over a
 * surface. Used to pin the numbers AS RENDERED, not just the opaque token. */
function mixOver(over: string, alpha: number, under: string): string {
  const ch = (hex: string, i: number) => parseInt(hex.slice(i * 2 + 1, i * 2 + 3), 16);
  const blended = [0, 1, 2].map((i) => Math.round(alpha * ch(over, i) + (1 - alpha) * ch(under, i)));
  return `#${blended.map((v) => v.toString(16).padStart(2, '0')).join('')}`;
}

/* ── 1. the app-wide contract (app.css) ──────────────────────────────────── */

describe('MOR-2522: the global focus rule is illumination, never a frame', () => {
  it('removes every outline (including the UA frame) and paints ring+halo', () => {
    const rule = globalFocusRule();
    expect(rule).toMatch(/outline:\s*none/);
    expect(rule).toMatch(/box-shadow:\s*var\(--v2-focus-illum-shadow,/);
    expect(rule).toMatch(
      /0 0 0 2px var\(--accent\), 0 0 14px 2px color-mix\(in srgb, var\(--accent\) 55%, transparent\)\)/,
    );
  });

  it('carries no filter (containing-block / popover re-anchoring defect) and no geometry', () => {
    const rule = globalFocusRule();
    expect(
      /(?<![\w-])filter\s*:/.test(rule),
      'a filter on the focused element re-anchors its fixed/absolute descendants',
    ).toBe(false);
    expect(GEOMETRY.test(rule), 'focus must not change any element geometry').toBe(false);
  });

  it('TxPanel PTT keeps a focus ring while transmitting (the MOR-1232 defect class)', () => {
    const tx = read('components-v2/panels/TxPanel.svelte');
    // The held/latched transmit state paints a scoped box-shadow …
    expect(tx).toMatch(/\.ptt-button\.ptt-held,[\s\S]{0,80}\{[^}]*box-shadow:/);
    // … which replaces the shadow-carried global light, so the focused
    // held/latched state repaints the transmit glow WITH the contract ring.
    expect(tx).toMatch(
      /\.ptt-button\.ptt-held:focus-visible,\s*\.ptt-button\.ptt-latched:focus-visible\s*\{[^}]*box-shadow:\s*0 0 12px rgba\(239, 68, 68, 0\.4\), var\(--v2-focus-illum-ring\)/,
    );
    // The plain focused button sets no shadow of its own, so the global
    // ring+halo reaches it untouched — the PTT button is the most
    // safety-critical control in the app.
    const base = stripComments(styleText('TxPanel.svelte', tx)).match(/\.ptt-button\s*\{[^}]*\}/);
    expect(base, 'expected a base .ptt-button rule').not.toBeNull();
    expect(base![0]).not.toMatch(/box-shadow/);
  });
});

/* ── 2. the token set (components-v2/theme/tokens.css) ───────────────────── */

describe('MOR-2522: one --v2-focus-* set, one knob, box-shadow only', () => {
  // Raw text (newline structure intact) for the @media block extraction —
  // the blocks close with a column-0 `}`; flattened text for the token
  // values, which are declared across continuation lines.
  const raw = () => stripComments(read('components-v2/theme/tokens.css'));
  const flat = () => raw().replace(/\s+/g, ' ');

  it('derives every illumination value from the single per-skin knob', () => {
    const source = flat();
    expect(source).toMatch(/--v2-focus-ring-color: var\(--v2-accent-cyan\)/);
    expect(source).toMatch(/--v2-focus-illum-ring: 0 0 0 2px var\(--v2-focus-ring-color\)/);
    expect(source).toMatch(/--v2-focus-illum-rim: inset 0 0 0 2px var\(--v2-focus-ring-color\)/);
    expect(source).toMatch(
      /--v2-focus-illum-halo: 0 0 14px 2px color-mix\(in srgb, var\(--v2-focus-ring-color\) 55%, transparent\)/,
    );
    expect(source).toMatch(
      /--v2-focus-illum-shadow: var\(--v2-focus-illum-ring\), var\(--v2-focus-illum-halo\)/,
    );
  });

  it('declares no filter carrier anywhere (banned by the correction round)', () => {
    expect(raw()).not.toMatch(/--v2-focus-illum-filter/);
  });

  it('retires the frame shapes so unmigrated rules paint nothing, not a border', () => {
    const source = flat();
    expect(source).toMatch(/--v2-focus-ring: none;/);
    expect(source).toMatch(/--v2-focus-ring-shadow: var\(--v2-focus-illum-shadow\)/);
  });

  it('keeps the deck brightness steps (the VFO deck is untouched by MOR-2522)', () => {
    const source = raw();
    expect(source).toMatch(/--v2-focus-ring-lit-filter:\s*brightness\([^;]+;/);
    expect(source).toMatch(/--v2-focus-ring-dim-filter:\s*brightness\([^;]+;/);
  });

  it('prefers-contrast: more strengthens the pair — still light, never an outline', () => {
    const block = raw().match(/@media\s*\(prefers-contrast:\s*more\)\s*\{[\s\S]*?\n\}/)![0];
    const flatBlock = block.replace(/\s+/g, ' ');
    expect(flatBlock).toMatch(/--v2-focus-illum-ring: 0 0 0 3px var\(--v2-focus-ring-color\)/);
    expect(flatBlock).toMatch(/--v2-focus-illum-rim: inset 0 0 0 3px var\(--v2-focus-ring-color\)/);
    expect(flatBlock).toMatch(/--v2-focus-illum-halo: 0 0 16px 3px var\(--v2-focus-ring-color\)/);
    expect(block).not.toMatch(OUTLINE_DECL);
  });

  it('forced-colors restores a system outline (shadows do not paint there)', () => {
    const block = raw().match(/@media\s*\(forced-colors:\s*active\)\s*\{[\s\S]*?\n\}/)![0];
    expect(block.replace(/\s+/g, ' ')).toMatch(
      /:focus-visible \{ outline: 2px solid Highlight !important/,
    );
  });
});

/* ── 3. per-design-language contract (studioline) ────────────────────────── */

describe('MOR-2522: a design language feeds the knob and lights focus itself', () => {
  const css = () => stripComments(read('presentation/languages/studioline/studioline.css'));

  it('repoints the contract knob at the language focus colour', () => {
    expect(css()).toMatch(/--v2-focus-ring-color:\s*var\(--dl-studioline-focus\)/);
  });

  it('lights the focused control and declares no outline and no filter at all', () => {
    const source = css();
    expect(source).not.toMatch(OUTLINE_DECL);
    expect(source).not.toMatch(/(?<![\w-])filter\s*:/);
    const rule = source.match(
      /\[data-design-language='studioline'\]\[data-design-language\] :focus-visible\s*\{[^}]*\}/,
    )!;
    expect(rule[0].replace(/\s+/g, ' ')).toMatch(
      /box-shadow: var\(--v2-focus-illum-shadow, 0 0 0 2px var\(--dl-studioline-focus\), 0 0 14px 2px color-mix\(in srgb, var\(--dl-studioline-focus\) 55%, transparent\)\)/,
    );
  });

  /* MOR-977 §3.2/§4.4 obligations, recomputed from the literals actually in
   * the file: the ring must clear 3:1 on BOTH language surfaces — the dark
   * default and the light-mode override — and the composited halo numbers
   * (as rendered, 55% over each surface) are pinned for the record: the
   * ring carries the ≥ 3:1 obligation, the halo is decorative. */
  const token = (name: string): string => {
    const m = css().match(new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`));
    expect(m, `expected a literal hex for ${name} in studioline.css`).not.toBeNull();
    return m![1];
  };

  /** The light-mode surface override: the FIRST `--dl-studioline-surface` is
   * the dark default, so this reaches into the light block for the second. */
  const lightSurface = (): string => {
    const m = css().match(
      /\[data-language-mode='light'\][^{}]*\{[^}]*--dl-studioline-surface:\s*(#[0-9a-fA-F]{6})/,
    );
    expect(m, 'expected a light-mode --dl-studioline-surface override').not.toBeNull();
    return m![1];
  };

  function luminance(hex: string): number {
    const chan = (i: number) => {
      const s = parseInt(hex.slice(i * 2 + 1, i * 2 + 3), 16) / 255;
      return s <= 0.04045 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * chan(0) + 0.7152 * chan(1) + 0.0722 * chan(2);
  }

  function contrast(a: string, b: string): number {
    const la = luminance(a);
    const lb = luminance(b);
    const [hi, lo] = la > lb ? [la, lb] : [lb, la];
    return (hi + 0.05) / (lo + 0.05);
  }

  it('the rendered composite numbers are pinned on both surfaces', () => {
    const focus = token('--dl-studioline-focus');
    const dark = token('--dl-studioline-surface');
    const light = lightSurface();
    const ringDark = contrast(focus, dark);
    const ringLight = contrast(focus, light);
    const haloDark = contrast(mixOver(focus, 0.55, dark), dark);
    const haloLight = contrast(mixOver(focus, 0.55, light), light);
    // The obligation bearer: full-opacity ring ≥ 3:1 in both modes.
    expect(ringDark).toBeGreaterThanOrEqual(3);
    expect(ringLight).toBeGreaterThanOrEqual(3);
    // Pin the as-rendered numbers (ring / halo composite):
    expect(ringDark).toBeCloseTo(4.19, 1);
    expect(ringLight).toBeCloseTo(4.23, 1);
    expect(haloDark).toBeCloseTo(2.07, 1);
    expect(haloLight).toBeCloseTo(2.14, 1);
  });
});

/* ── 4. the value-control layer ──────────────────────────────────────────── */

describe('MOR-2522: value controls light up; no frame, no inline outline', () => {
  const VC_CSS = 'components-v2/controls/value-control/value-control.css';

  it('retires the renderer outline rules through the width token', () => {
    const css = stripComments(read(VC_CSS));
    expect(css).toMatch(/--vc-focus-ring-width:\s*0px\s*;/);
    expect(css).toMatch(/--vc-focus-ring:\s*var\(--v2-focus-ring-color\)/);
  });

  it('feeds the illumination through --vc-illum-* from the contract', () => {
    expect(stripComments(read(VC_CSS))).toMatch(
      /--vc-illum-focus-glow:\s*var\(--v2-focus-illum-shadow\)/,
    );
  });

  it('declares the control-level glow once, for all five renderers and the armed state', () => {
    const css = stripComments(read(VC_CSS));
    const block = ruleBlocks(css).find(
      (b) => b.selector.includes('.vc-track-container:focus-visible'),
    );
    expect(block, 'expected the shared focus glow rule in value-control.css').not.toBeNull();
    expect(block!.selector).toContain('.vc-knob-container:focus-visible');
    expect(block!.selector).toContain("[data-wheel-armed='true']");
    expect(block!.body.replace(/\s+/g, ' ')).toMatch(
      /box-shadow:\s*var\(--vc-illum-focus-glow\)/,
    );
    expect(
      GEOMETRY.test(block!.body),
      'the focus glow must not change any element geometry',
    ).toBe(false);
  });

  it('wheel-control.ts no longer paints an inline outline (the click frame from the report)', () => {
    const source = read('components-v2/controls/value-control/wheel-control.ts');
    expect(source).not.toMatch(/style\.outline/);
    // The armed marker moved to CSS: the attribute must still be set, or the
    // illumination keyed on it in value-control.css never appears.
    expect(source).toMatch(/dataset\.wheelArmed\s*=\s*'true'/);
  });
});

/* ── 5. scoped shadows that replace the global light are paired with it ──── */

describe('MOR-2522: masked sites carry their own scoped illumination', () => {
  it('the shared v2 button: the rim rides ::after; the deck scope suppresses it', () => {
    const css = stripComments(read('components-v2/controls/control-button.css'));
    expect(css).toMatch(
      /\.v2-control-button:focus-visible::after\s*\{[^}]*box-shadow:\s*var\(--v2-focus-illum-rim\)[^}]*\}/,
    );
    // The deck keeps its owner-approved brightness steps (its two blocks
    // below), so the bridge scope under the deck appearance drops the rim.
    expect(css).toMatch(
      /\[data-vfo-appearance='standard'\] \[data-instrument-bridge\] \.v2-control-button:focus-visible::after\s*\{[^}]*content:\s*none/,
    );
    // The reason the rim cannot ride the element itself: the base rule
    // paints a scoped box-shadow that a (0,1,0) global cannot beat.
    const base = ruleBlocks(css).find(
      (b) => b.selector.includes('.v2-control-button') && /box-shadow:\s*inset 0 1px 0 var\(--btn-highlight\)/.test(b.body),
    );
    expect(base, 'expected the base .v2-control-button rule with its inset highlight').toBeTruthy();
  });

  it('PttFab: every state shadow, and the latch pulse, animate the element — the rim rides ::after', () => {
    const css = stripComments(styleText('PttFab.svelte', read('components-v2/controls/PttFab.svelte')));
    expect(css).toMatch(
      /\.ptt-fab:focus-visible::after\s*\{[^}]*box-shadow:\s*var\(--v2-focus-illum-rim\)/,
    );
    // The element shadow is animated in the latched state …
    expect(css).toMatch(/@keyframes ptt-fab-latch-pulse[\s\S]*?box-shadow:/);
    // … which is exactly why the focus rim may not ride the element.
  });

  it('TxPanel held/latched focus composes the transmit glow with the contract ring', () => {
    const tx = stripComments(styleText('TxPanel.svelte', read('components-v2/panels/TxPanel.svelte')));
    expect(tx).toMatch(
      /\.ptt-button\.ptt-held:focus-visible,\s*\.ptt-button\.ptt-latched:focus-visible\s*\{[^}]*var\(--v2-focus-illum-ring\)/,
    );
  });
});

/* ── 6. WCAG 1.4.11 contrast of the knob, recomputed per skin ────────────── */

describe('MOR-2522: the illumination colour clears 3:1 on every skin', () => {
  const SURFACES = [
    '--v2-bg-app',
    '--v2-bg-panel',
    '--v2-bg-card',
    '--v2-bg-input',
    '--v2-bg-darker',
    '--v2-bg-darkest',
  ];
  const MIN_RATIO = 3;

  function parseTokens(text: string): Record<string, string> {
    const out: Record<string, string> = {};
    const re = /(--[\w-]+):\s*([^;]+);/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(text))) out[m[1]] = m[2].trim();
    return out;
  }

  /** Resolve a custom property through `var()` indirection, theme over base. */
  function resolve(
    name: string,
    theme: Record<string, string>,
    base: Record<string, string>,
    depth = 0,
  ): string | null {
    if (depth > 8) return null;
    const value = theme[name] ?? base[name];
    if (!value) return null;
    const ref = value.match(/^var\(\s*(--[\w-]+)\s*(?:,\s*([\s\S]+))?\)$/);
    if (!ref) return value;
    return resolve(ref[1], theme, base, depth + 1) ?? (ref[2]?.trim() || null);
  }

  function luminance(hex: string): number {
    let h = hex.trim().replace('#', '');
    if (h.length === 3)
      h = h
        .split('')
        .map((c) => c + c)
        .join('');
    const chan = (i: number) => {
      const s = parseInt(h.slice(i * 2, i * 2 + 2), 16) / 255;
      return s <= 0.04045 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * chan(0) + 0.7152 * chan(1) + 0.0722 * chan(2);
  }

  function contrast(a: string, b: string): number {
    const la = luminance(a);
    const lb = luminance(b);
    const [hi, lo] = la > lb ? [la, lb] : [lb, la];
    return (hi + 0.05) / (lo + 0.05);
  }

  const base = parseTokens(readFileSync(join(THEME_DIR, 'tokens.css'), 'utf-8'));
  const skins: Array<{ id: string; tokens: Record<string, string> }> = [
    { id: ':root (no data-theme)', tokens: {} },
    ...readdirSync(join(THEME_DIR, 'themes'))
      .filter((f) => f.endsWith('.css'))
      .map((f) => ({
        id: f.replace(/\.css$/, ''),
        tokens: parseTokens(readFileSync(join(THEME_DIR, 'themes', f), 'utf-8')),
      })),
  ];

  it('every skin file is discovered (guards against a silently empty matrix)', () => {
    expect(skins.length).toBeGreaterThan(15);
  });

  for (const { id, tokens } of skins) {
    it(`${id}: illumination colour is >= ${MIN_RATIO}:1 against every surface`, () => {
      const colour = resolve('--v2-focus-ring-color', tokens, base);
      expect(colour, `${id} has no resolvable --v2-focus-ring-color`).toBeTruthy();
      expect(colour!, `${id} focus colour is not a hex literal`).toMatch(/^#[0-9a-fA-F]{3,6}$/);

      const checked: string[] = [];
      for (const surface of SURFACES) {
        const bg = resolve(surface, tokens, base);
        if (!bg || !/^#[0-9a-fA-F]{3,6}$/.test(bg)) continue;
        checked.push(surface);
        const ratio = contrast(colour!, bg);
        expect(
          ratio,
          `${id}: focus colour ${colour} on ${surface} ${bg} = ${ratio.toFixed(2)}:1 ` +
            `(WCAG 1.4.11 non-text minimum is ${MIN_RATIO}:1). Override ` +
            '--v2-focus-ring-color in this skin.',
        ).toBeGreaterThanOrEqual(MIN_RATIO);
      }
      expect(checked.length, `${id}: no surfaces resolved — the check was vacuous`).toBeGreaterThan(
        2,
      );
    });
  }

  it('default skin: the as-rendered ring and halo composite numbers are pinned', () => {
    const knob = resolve('--v2-focus-ring-color', {}, base)!;
    const darkest = resolve('--v2-bg-darkest', {}, base)!;
    // Ring: the full-opacity obligation bearer on the darkest surface.
    expect(contrast(knob, darkest)).toBeCloseTo(11.26, 1);
    // Halo: 55% over the same surface, as rendered — decorative, above 3:1
    // here but NOT the obligation carrier (studioline's is below; see §3).
    expect(contrast(mixOver(knob, 0.55, darkest), darkest)).toBeCloseTo(3.94, 1);
  });
});

/* ── 7. the deck's own focus steps stay exactly as shipped (MOR-2509) ────── */

describe('MOR-2509: the Standard deck uses the shared three-step focus treatment', () => {
  it('the deck rule brightens controls without a second frame or a size-affecting declaration', () => {
    const css = stripComments(read('components-v2/controls/control-button.css'));
    const deckRules = ruleBlocks(css).filter((block) =>
      block.selector.trim().startsWith("[data-vfo-appearance='standard']")
      && block.selector.includes(':focus-visible'));
    expect(deckRules.length).toBeGreaterThanOrEqual(2);
    expect(deckRules.some((block) => /filter:\s*var\(--v2-focus-ring-dim-filter\)/.test(block.body))).toBe(true);
    expect(deckRules.some((block) => /filter:\s*var\(--v2-focus-ring-lit-filter\)/.test(block.body))).toBe(true);
    for (const { selector, body } of deckRules) {
      expect(body, `${selector} removes the inherited browser frame`).toMatch(/outline:\s*none/);
      expect(body, `${selector} paints no second frame`).not.toMatch(/box-shadow|border(?:-width)?\s*:/);
      expect(body, `${selector} changes no box geometry`).not.toMatch(/(?:width|height|padding|margin)\s*:/);
    }
  });

  it('deck components declare no private outline treatment', () => {
    for (const file of ['components-v2/vfo/VfoPanel.svelte', 'semantic/VfoSurface.svelte']) {
      expect(stripComments(styleText(file, read(file))), file).not.toMatch(OUTLINE_DECL);
    }
  });
});

/* ── 8. scoped box-shadow illumination aligned to the contract ───────────── */

describe('MOR-2522: local shadow treatments read the contract glow token', () => {
  it('ActiveReceiverToggle.svelte .segment lights through --v2-focus-ring-shadow', () => {
    const rule = read('components-v2/vfo/ActiveReceiverToggle.svelte').match(
      /\.segment:focus-visible\s*\{[^}]*\}/,
    );
    expect(rule).not.toBeNull();
    expect(rule![0]).toMatch(/box-shadow:\s*var\(--v2-focus-ring-shadow\)/);
  });

  it('SegmentedButton.svelte .segmented-button lights through --v2-focus-ring-shadow', () => {
    const rule = read('components-v2/controls/SegmentedButton.svelte').match(
      /\.segmented-button:focus-visible\s*\{[^}]*\}/,
    );
    expect(rule).not.toBeNull();
    expect(rule![0]).toMatch(/box-shadow:\s*var\(--v2-focus-ring-shadow\)/);
  });
});

/* ── 9. the frame ban ───────────────────────────────────────────────────────
 *      (a) no outline in any :focus-visible rule outside the contract files
 *          and an EXACT allow-list (the PR-2 debt, selector-precise);
 *      (b) no inline `style.outline`/`style:outline` anywhere — the
 *          mechanism that painted the owner's click frame;
 *      (c) the detection pipeline demonstrably sees .svelte <style> blocks
 *          and plain .css rules, so a NEW file cannot slip past (a).      */

describe('MOR-2522: no :focus-visible rule declares an outline outside the contract', () => {
  // The contract's own files: app.css (the global `outline: none`) and
  // theme/tokens.css (the forced-colors system-outline restoration).
  const CONTRACT_FILES = new Set(['app.css', 'components-v2/theme/tokens.css']);

  /* Every remaining outline declaration under a :focus-visible rule, filed
   * for PR 2 (MOR-2522) — EXACT normalized selectors, so an entry can only
   * ever match the one rule it was written for; a new outline rule in an
   * already-listed file still fails the walk. Groups:
   *   A. token consumers — `outline: var(--v2-focus-ring)` or the retired
   *      renderer forms — which paint NOTHING now (the tokens resolve to
   *      `none` / 0px); PR 2 deletes the rules.
   *   B. literal frames — still visible today; PR 2 converts them.
   *   C. contract-conforming suppressions — `outline: none` paired with
   *      contract light in the same block. */
  const OUTLINE_ALLOW: Record<string, string[]> = {
    'components/spectrum/SpectrumPanel.svelte': [
      // B — legacy pre-v3 tree
      '.spectrum-split-separator:focus-visible',
      // A — roving target, suppression only
      '.passband-resize-zone:focus-visible',
    ],
    'components-v2/controls/BandSelector.svelte': ['.band-tab:focus-visible'], // B
    'components-v2/controls/LanguageSelector.svelte': ['.lang-select:focus-visible'], // A
    'components-v2/controls/WorkspaceImportExport.svelte': [
      'button:focus-visible, textarea:focus-visible, input:focus-visible', // A
    ],
    'components-v2/controls/WorkspaceSettingsPanel.svelte': [
      '.ws-row select:focus-visible, button:focus-visible', // A
    ],
    'components-v2/controls/control-button.css': [
      // A — the shared-button family; the rim ring now rides ::after (see §5)
      ".v2-control-button:focus-visible, .desktop-control-face.standard-face :is( .semantic-control-panel button, [data-vfo-appearance='standard'] .vfo-select, [data-vfo-operation-appearance='standard'] .fact-toggle, [data-vfo-operation-appearance='standard'] .vfo-op, .reset-order-btn, .status-bar .control-btn, .status-bar .now-playing, .status-bar .managed-tot-trigger, .status-bar .theme-button, .spectrum-toolbar .toolbar-btn ):not(.panel-header, .drag-handle, .passband-resize-zone, .band-segment):focus-visible, .desktop-control-face.standard-face .band-tab:focus-visible",
    ],
    'components-v2/controls/control-button.css#deck': [
      // C — the contract's own model: outline removed, brightness step kept
      "[data-vfo-appearance='standard'] :is( .panel-header, .slot-choice, .vfo-freq[role='button'], [data-instrument-bridge] .v2-control-button ):focus-visible",
      "[data-vfo-appearance='standard'] :is( .panel.active .panel-header, .panel.active .vfo-freq[role='button'], .slot-choice[data-vfo-active='true'], [data-instrument-bridge] .v2-control-button:is( .active, [data-active='true'], [aria-pressed='true'], [aria-checked='true'] ) ):focus-visible",
    ],
    'components-v2/controls/value-control/HBarRenderer.svelte': ['.vc-track-container:focus-visible'], // A
    'components-v2/controls/value-control/BipolarRenderer.svelte': ['.vc-track-container:focus-visible'], // A
    'components-v2/controls/value-control/DiscreteRenderer.svelte': ['.vc-track-container:focus-visible'], // A
    'components-v2/controls/value-control/DualParamRenderer.svelte': ['.vc-track-container:focus-visible'], // A
    'components-v2/controls/value-control/KnobRenderer.svelte': ['.vc-knob-container:focus-visible'], // A
    'components-v2/controls/value-control/skins/ProfessionalKnob.svelte': ['.pro-ctr:focus-visible'], // B
    'components-v2/dialogs/SendReportDialog.svelte': [
      '.field input:focus-visible, .field textarea:focus-visible', // A
    ],
    'components-v2/layout/MobileRadioLayout.svelte': [
      '.m-ls-unkey:focus-visible', // B
      '.m-receiver-pill:focus-visible', // B
    ],
    'components-v2/layout/RadioLayout.svelte': [
      '.standard-tx-settings-trigger:focus-visible, .standard-tx-disclosure:focus-visible', // B
    ],
    'components-v2/layout/StatusBar.svelte': ['.skin-select:focus-visible'], // A
    'components-v2/layout/mobile-chip-bar.svelte': ['.m-chip:focus-visible'], // B
    'components-v2/panels/MemoryPanel.svelte': ['.ch-name-input:focus-visible'], // A
    'components-v2/panels/ModePanel.svelte': ['.mod-input-select:focus-visible'], // B
    'components-v2/vfo/ActiveReceiverToggle.svelte': ['.segment:focus-visible'], // C
    'presentation/languages/fieldline/fieldline.css': [
      "[data-design-language='fieldline'][data-design-language] :focus-visible", // B
    ],
    'presentation/languages/segmentline/segmentline.css': [
      "[data-design-language='segmentline'][data-design-language] .rx-tx-key:focus-visible, [data-design-language='segmentline'][data-design-language] .rx-tx-unkey:focus-visible", // B
    ],
    'skins/desktop-v2/semantic-controls.css': [
      '.desktop-control-face .semantic-control-panel section :is(button, input, select):focus-visible', // B
    ],
  };

  it('every :focus-visible outline declaration is a contract file or an exact PR-2 entry', () => {
    const files = walk(FRONTEND_SRC, ['.svelte', '.css']);
    for (const file of files) {
      const rel = file.slice(FRONTEND_SRC.length + 1);
      if (CONTRACT_FILES.has(rel)) continue;
      const raw = readFileSync(file, 'utf-8');
      if (!raw.includes(':focus-visible')) continue;
      const css = stripComments(styleText(rel, raw));
      for (const block of ruleBlocks(css)) {
        if (!block.selector.includes(':focus-visible')) continue;
        if (!OUTLINE_DECL.test(block.body)) continue;
        const deckKey = `${rel}#deck`;
        const allowed = [
          ...(OUTLINE_ALLOW[rel] ?? []),
          ...(OUTLINE_ALLOW[deckKey] ?? []),
        ].some((s) => block.selector === s);
        expect(
          allowed,
          `${rel} declares an outline in "${block.selector}" — the MOR-2522 ` +
            'contract paints focus as box-shadow light in --v2-focus-ring-color, ' +
            'never a frame. Migrate the rule to the contract tokens and remove ' +
            'it from the PR-2 allow list; new entries must match the rule\'s ' +
            'normalized selector EXACTLY.',
        ).toBe(true);
      }
    }
  });

  it('the PR-2 list stays honest: every entry still matches a real block exactly', () => {
    for (const [key, selectors] of Object.entries(OUTLINE_ALLOW)) {
      const rel = key.replace(/#deck$/, '');
      const css = stripComments(styleText(rel, read(rel)));
      const blocks = ruleBlocks(css).filter(
        (b) => b.selector.includes(':focus-visible') && OUTLINE_DECL.test(b.body),
      );
      for (const s of selectors) {
        expect(
          blocks.some((b) => b.selector === s),
          `${rel}: allow-list entry no longer matches exactly — the migration ` +
            'happened; remove the entry (keep the list truthful)',
        ).toBe(true);
      }
    }
  });

  it('no inline outline is assigned anywhere (the wheel-control mechanism)', () => {
    // __tests__ are excluded because THIS test's own pattern text would
    // match itself; production code is the entire point of the scan.
    const offenders: string[] = [];
    for (const file of walk(FRONTEND_SRC, ['.ts', '.svelte'])) {
      const rel = file.slice(FRONTEND_SRC.length + 1);
      if (rel.includes('__tests__')) continue;
      const raw = readFileSync(file, 'utf-8');
      if (/style\.outline(?:Offset)?\s*=[^=]|style:outline\b/.test(raw)) {
        offenders.push(rel);
      }
    }
    expect(
      offenders,
      'an inline style.outline assignment (or Svelte style:outline directive) ' +
        'beats every stylesheet rule — this is the mechanism that painted the ' +
        '2 px cyan rectangle on every slider click. Use the CSS contract.',
    ).toEqual([]);
  });

  it('the detection pipeline sees .svelte <style> blocks and plain .css rules', () => {
    // (E) from the review: prove the walk's building blocks detect a NEW
    // file of either kind, so the exact allow-list cannot be bypassed by
    // choosing a file type.
    const svelteSample =
      '<script></script>\n<style>\n  .x:focus-visible { outline: 2px solid #fff; }\n</style>';
    const svelteBlocks = ruleBlocks(stripComments(styleText('sample.svelte', svelteSample)));
    expect(
      svelteBlocks.some(
        (b) => b.selector.includes(':focus-visible') && OUTLINE_DECL.test(b.body),
      ),
      'a :focus-visible outline inside a Svelte <style> block must be detected',
    ).toBe(true);

    const cssSample = '.y:focus-visible { outline: none; }';
    const cssBlocks = ruleBlocks(stripComments(styleText('sample.css', cssSample)));
    expect(
      cssBlocks.some(
        (b) => b.selector.includes(':focus-visible') && OUTLINE_DECL.test(b.body),
      ),
      'a :focus-visible outline in a plain .css file must be detected',
    ).toBe(true);
  });
});
