/**
 * MOR-1232 — the `--focus-ring` design token (frontend/src/styles/tokens.css)
 * was declared but never referenced anywhere, and several components
 * suppressed keyboard focus outright with a bare `outline: none` that had no
 * visible replacement. MOR-1087 needs a v3 accessibility/keyboard-focus
 * evidence run to pass, and it provably cannot while the focus ring is dead.
 *
 * These tests pin the wiring and the contrast math, not the pixels:
 *
 *   1. The global default (`src/app.css`) consumes the ring tokens and is
 *      `outline`-shaped. This is load-bearing, not cosmetic: a global
 *      `:focus-visible` rule has specificity (0,1,0), so a `box-shadow` ring
 *      declared there is discarded by ANY Svelte-scoped component rule that
 *      sets `box-shadow` (compiled to `.foo.svelte-<hash>` = (0,2,0)+) — and
 *      `box-shadow` is the most-used decorative property in this codebase.
 *      `outline` paints on an independent layer, is not clipped by an
 *      ancestor `overflow: hidden`, and survives forced-colours mode.
 *      The concrete regression this pins is TxPanel's PTT button, which sets
 *      a red glow `box-shadow` while transmitting and has no focus rule of
 *      its own — see the dedicated test below.
 *
 *   2. components-v2 gets its own token trio
 *      (`--v2-focus-ring-color` → `--v2-focus-ring` / `--v2-focus-ring-shadow`)
 *      rather than being forced onto the legacy `--accent` (#4db6ff), which
 *      fails the WCAG 1.4.11 3:1 non-text minimum against every surface of all
 *      five light v2 skins. The colour is NOT assumed to be theme-correct just
 *      because it is per-skin: test group 5 recomputes the ratio for every skin
 *      from the real hex values and fails under 3:1. Two skins (nord-light,
 *      solarized-light) needed an explicit `--v2-focus-ring-color` override
 *      because their accent — not the legacy one — was itself under 3:1.
 *
 *   3. Every identified "suppress it entirely" site pairs its `outline: none`
 *      with a real `:focus-visible` treatment wired to one of those tokens.
 *
 *   4. The two local overrides whose shape already matched the token
 *      (box-shadow ring, same colour source) reference it.
 *
 *   5. A regression guard, scoped to the SELECTOR (not the file): a decorative
 *      `box-shadow` on an unrelated rule elsewhere in the file does not satisfy
 *      it. File-scoping was the first version's blind spot — it passed
 *      `control-button.css` and `StatusBar.svelte` on the pre-fix revision,
 *      i.e. it missed the very bug this ticket fixes in the two
 *      highest-impact files.
 *
 * Deliberately NOT touched (recorded, not silently dropped):
 *   - BandSelector.svelte / ProfessionalKnob.svelte keep their per-widget ring
 *     SHAPE (a tab strip's inset 1px ring, a knob's circular 4px-offset ring);
 *     forcing them onto --v2-focus-ring's 2px/2px-offset rectangle would
 *     reshape the widget without live visual verification. BandSelector's ring
 *     COLOUR is aligned to --v2-focus-ring-color (contrast, no shape change);
 *     ProfessionalKnob's comes from a per-instance `accentColor` prop
 *     (literal default #00e5ff), so aligning it is a behaviour question for
 *     the owner, not a token swap.
 *   - legacy `src/components/spectrum/*` suppressions: pre-v3 code, listed
 *     under LEGACY_DEBT below rather than silently masked by the guard.
 *
 * MOR-1254 follow-up (fixed here, see section 5's extended loop and section 7
 * below): the five value-control renderers (Knob/HBar/Discrete/Bipolar/
 * DualParam) used to colour their focus outline with `--vc-accent` /
 * `--vc-rf-accent` — the general per-instance accent, shared with tick marks
 * and fills, unchecked for contrast (1.48-1.74 on nord-light) — while
 * `--vc-focus-ring` (value-control.css) was declared and referenced NOWHERE,
 * a second instance of this ticket's dead-token defect. `--vc-focus-ring` now
 * resolves to `--v2-focus-ring-color` (the same contrast-pinned per-skin
 * knob), and all five renderers, including DualParamRenderer, consume it.
 *
 * MOR-2522 changed the CARRIER, not the colour: the renderers' outline frame
 * and wheel-control.ts's inline `node.style.outline` are gone; focus and
 * arming light the control with the full-opacity `--vc-focus-ring-shadow`
 * spread ring instead. Section 7 pins that shape contract, and the studioline
 * pins below hold the ring to 3:1 on both studioline surfaces.
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

// (?<![\w-]) keeps this from matching the tail of a custom-property name
// like `--vc-illum-thumb-outline: 0 0 0 1px ...` — that "0" is a box-shadow
// offset, not an `outline: 0` suppression.
const OUTLINE_NONE = /(?<![\w-])outline:\s*(none|0)\b/;
// A real, visible focus treatment: an outline set to a non-none value, a
// var()-driven box-shadow, or one of the Standard deck's two focus steps.
const HAS_REPLACEMENT = /(?<![\w-])outline:\s*(?!none\b|0\b)\S|box-shadow:\s*(?!none\b)[^;]*var\(|filter:\s*var\(--v2-focus-ring-(?:dim|lit)-filter\)/;

function styleText(path: string, text: string): string {
  if (extname(path) !== '.svelte') return text;
  return [...text.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((m) => m[1]).join('\n');
}

function stripComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, '');
}

/** Innermost rule blocks. `[^{}]` can't cross a brace, so an at-rule wrapper
 *  (`@media ... { .a { … } }`) yields the inner `.a` rule, not the wrapper. */
function ruleBlocks(css: string): Array<{ selector: string; body: string }> {
  const out: Array<{ selector: string; body: string }> = [];
  const re = /([^{}]+)\{([^{}]*)\}/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(css))) out.push({ selector: m[1].trim(), body: m[2] });
  return out;
}

/** The element(s) a selector list targets, with pseudo-classes/elements
 *  stripped: `.a:focus, .b input:focus-visible` → ['.a', '.b input'].
 *  A repeated simple selector in one compound names the same element —
 *  `.a.a:focus-visible` is the MOR-2522 specificity raise over the studioline
 *  focus contract — so compounds are deduplicated before comparison; raw
 *  string equality would misread the doubled class as a different element
 *  and report the base rule as unpaired. The simple-selector split is
 *  escape-aware: a `.` or `#` preceded by a backslash belongs to the
 *  preceding simple selector (`.a\.a` is ONE class whose name contains a
 *  dot), so `.a\.a.a` keeps its second, distinct class and does not pair
 *  with a lone `.a\.a`. */
function selectorTargets(selector: string): string[] {
  return selector
    .split(',')
    .map((s) =>
      s
        .replace(/::?[\w-]+(\([^()]*\))?/g, '')
        .split(/(\s|[>+~])/)
        .map((compound) =>
          compound.includes('.') || compound.includes('#')
            ? [...new Set(compound.split(/(?<!\\)(?=[.#])/))].join('')
            : compound,
        )
        .join('')
        .replace(/\s+/g, ' ')
        .trim(),
    )
    .filter(Boolean);
}

/**
 * Selector-scoped guard. A block that sets `outline: none` is only acceptable
 * if the SAME block, or another `:focus…` block targeting the SAME element,
 * provides a real treatment. A decorative shadow on an unrelated selector in
 * the same file does not count.
 */
function suppressionOffenders(path: string, raw: string): string[] {
  const css = stripComments(styleText(path, raw));
  const blocks = ruleBlocks(css);
  const treated = blocks
    .filter((b) => /:focus/.test(b.selector) && HAS_REPLACEMENT.test(b.body))
    .flatMap((b) => selectorTargets(b.selector));

  const offenders: string[] = [];
  for (const b of blocks) {
    if (b.selector.startsWith('@')) continue;
    if (!OUTLINE_NONE.test(b.body)) continue;
    if (HAS_REPLACEMENT.test(b.body)) continue;
    if (selectorTargets(b.selector).some((t) => treated.includes(t))) continue;
    offenders.push(b.selector.replace(/\s+/g, ' '));
  }
  return offenders;
}

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules' || entry.startsWith('.')) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (extname(entry) === '.svelte' || extname(entry) === '.css') out.push(full);
  }
  return out;
}

/* WCAG relative-luminance contrast from hex literals (section 5 and the
 * MOR-2522 studioline pins compute, never assume, the ratios). */
function parseTokens(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  const re = /(--[\w-]+):\s*([^;]+);/g;
  let m: RegExpExecArray | null;
  while (m = re.exec(text)) out[m[1]] = m[2].trim();
  return out;
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

/** (ids, class-level, types) specificity of one complex selector: ids → a;
 * classes, attributes and pseudo-classes → b; element types → c. String
 * values and parenthesised arguments are dropped first. A type is counted
 * once per compound, as a leading identifier — MOR-2522's native-range rule
 * (`input[type='range'][type='range']:focus-visible`, section 9) is the first
 * ranked selector whose win lives in the c column; the section-9 pin 'the
 * doubled attribute and the element type are both load-bearing' fails if the
 * count is dropped. Pseudo-elements keep their historical c count; no ranked
 * selector uses one. */
function specificity(selector: string): [number, number, number] {
  const stripped = selector.replace(/'[^']*'/g, "''").replace(/\([^()]*\)/g, '');
  let a = 0;
  let b = 0;
  let c = 0;
  for (const token of stripped.match(/\[[^\]]*\]|[#.][\w-]+|::?[\w-]+/g) ?? []) {
    if (token.startsWith('#')) a += 1;
    else if (token.startsWith('.') || token.startsWith('[')) b += 1;
    else if (token.startsWith('::')) c += 1;
    else b += 1; // single-colon pseudo-class
  }
  // `*` and compounds opening with a class/attribute/pseudo contribute no type.
  for (const compound of stripped.split(/[\s>+~]+/)) {
    if (/^[a-zA-Z][\w-]*/.test(compound)) c += 1;
  }
  return [a, b, c];
}

/** Compare two specificity tuples the way the cascade does. */
function outranks(
  winner: [number, number, number],
  loser: [number, number, number],
): boolean {
  return (
    winner[0] > loser[0] ||
    (winner[0] === loser[0] && winner[1] > loser[1]) ||
    (winner[0] === loser[0] && winner[1] === loser[1] && winner[2] > loser[2])
  );
}

/** Comma-split a box-shadow list at paren depth zero — the desktop-v2 track
 * chrome's `var(--vc-hw-channel-shadow-1, #000f)` fallbacks contain commas,
 * so a naive `value.split(',')` would shred every layer that carries one.
 * MOR-2522's compose pin (section 9) uses it to verify the layers of the ONE
 * --range-track-chrome declaration and both rules' references to it. */
function shadowLayers(value: string): string[] {
  const layers: string[] = [];
  let depth = 0;
  let start = 0;
  for (let i = 0; i < value.length; i += 1) {
    const ch = value[i];
    if (ch === '(') depth += 1;
    else if (ch === ')') depth -= 1;
    else if (ch === ',' && depth === 0) {
      layers.push(value.slice(start, i).trim());
      start = i + 1;
    }
  }
  layers.push(value.slice(start).trim());
  return layers;
}

/** Svelte compiles a scoped rule by appending one scope class to its
 * selector. The hash VALUE is irrelevant — whatever it is, it contributes
 * exactly one class-level token — so the compiled specificity is the source
 * specificity with b + 1. This models Svelte's append-a-class scoping; a
 * compiler that switched to zero-specificity :where() scoping would need
 * this model (and the doubled classes) re-derived. */
function svelteCompiled(selector: string): [number, number, number] {
  const [a, b, c] = specificity(selector);
  return [a, b + 1, c];
}

function globalFocusRule(): string {
  // Comments are stripped so prose about box-shadow/outline in the rule's own
  // explanatory comment cannot satisfy or defeat the assertions below.
  const rule = stripComments(read('app.css')).match(/:focus-visible\s*\{[^}]*\}/);
  expect(rule, 'expected a top-level :focus-visible rule in app.css').not.toBeNull();
  return rule![0];
}

/* ── 1. token wiring ─────────────────────────────────────────────────────── */

describe('MOR-1232: --focus-ring token wiring', () => {
  it('app.css global :focus-visible default consumes the ring tokens', () => {
    const rule = globalFocusRule();
    expect(rule).toMatch(/outline:\s*var\(--v2-focus-ring,\s*var\(--focus-ring\)\)/);
    expect(rule).toMatch(/outline-offset:/);
  });

  it('the legacy --focus-ring token is outline-shaped, so the global rule can use it', () => {
    const css = read('styles/tokens.css');
    expect(css).toMatch(/--focus-ring:\s*2px solid var\(--accent\)\s*;/);
    // A box-shadow-shaped value here would silently make `outline: var(--focus-ring)`
    // invalid at computed-value time — i.e. no ring at all.
    expect(css).not.toMatch(/--focus-ring:\s*0 0 0/);
  });

  it('components-v2 theme tokens declare the focus-ring trio off one per-skin colour knob', () => {
    const css = read('components-v2/theme/tokens.css');
    expect(css).toMatch(/--v2-focus-ring-color:\s*var\(--v2-accent-cyan\)/);
    expect(css).toMatch(/--v2-focus-ring:\s*2px solid var\(--v2-focus-ring-color\)/);
    expect(css).toMatch(/--v2-focus-ring-shadow:\s*0 0 0 2px var\(--v2-focus-ring-color\)/);
  });
});

describe('MOR-2509: the Standard deck uses the shared three-step focus treatment', () => {
  it('does not accept an unrelated filter as a replacement for outline suppression', () => {
    expect(HAS_REPLACEMENT.test('outline: none; filter: var(--unrelated-effect);')).toBe(false);
    expect(HAS_REPLACEMENT.test('outline: none; filter: var(--v2-focus-ring-dim-filter);')).toBe(true);
    expect(HAS_REPLACEMENT.test('outline: none; filter: var(--v2-focus-ring-lit-filter);')).toBe(true);
  });

  it('declares the two brightness steps beside the existing focus-ring trio', () => {
    const css = read('components-v2/theme/tokens.css');
    expect(css).toMatch(/--v2-focus-ring-lit-filter:\s*brightness\([^;]+;/);
    expect(css).toMatch(/--v2-focus-ring-dim-filter:\s*brightness\([^;]+;/);
  });

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
      expect(stripComments(styleText(file, read(file))), file).not.toMatch(/outline\s*:/);
    }
  });
});

/* ── 2. the global default must survive scoped box-shadows (F1) ──────────── */

describe('MOR-1232: the global focus default cannot be masked by a component box-shadow', () => {
  it('is outline-based, not box-shadow-based, and does not disable the outline', () => {
    const rule = globalFocusRule();
    expect(rule).toMatch(/outline:\s*var\(/);
    expect(
      OUTLINE_NONE.test(rule),
      'the global default must not set `outline: none` — nothing else can be relied on ' +
        'to paint a ring for components that have no focus rule of their own',
    ).toBe(false);
    expect(
      /box-shadow/.test(rule),
      'a box-shadow here is (0,1,0) and loses to every Svelte-scoped component shadow',
    ).toBe(false);
  });

  it('TxPanel PTT button keeps its focus ring while transmitting (.ptt-held red glow)', () => {
    const tx = read('components-v2/panels/TxPanel.svelte');
    // The held/latched transmit state paints a scoped box-shadow …
    expect(tx).toMatch(/\.ptt-button\.ptt-held,[\s\S]{0,80}\{[^}]*box-shadow:/);
    // … and TxPanel declares no focus treatment at all, so the PTT button —
    // the most safety-critical control in the app — depends entirely on the
    // global default surviving that shadow.
    expect(/:focus/.test(tx), 'TxPanel gained a focus rule; re-derive this test').toBe(false);
    expect(globalFocusRule()).toMatch(/outline:\s*var\(/);
  });

  it('many components-v2 files set a box-shadow with no focus rule of their own', () => {
    const unprotected = walk(join(FRONTEND_SRC, 'components-v2'))
      .filter((f) => {
        const css = stripComments(styleText(f, readFileSync(f, 'utf-8')));
        return /box-shadow:/.test(css) && !/:focus/.test(css);
      })
      .map((f) => f.slice(FRONTEND_SRC.length + 1));
    // Not an arbitrary threshold: this is the population that a box-shadow-shaped
    // global default would silently strip of any focus indicator.
    expect(unprotected.length).toBeGreaterThan(5);
    expect(unprotected).toContain('components-v2/panels/TxPanel.svelte');
  });
});

/* ── 3. the "suppress it entirely" sites ─────────────────────────────────── */

describe('MOR-1232: "suppress it entirely" sites now have a real focus-visible treatment', () => {
  const cases: Array<{ label: string; file: string; selector: RegExp }> = [
    {
      label: 'control-button.css .v2-control-button (shared by 7+ v2 buttons)',
      file: 'components-v2/controls/control-button.css',
      selector: /\.v2-control-button:focus-visible(?:\s*,[^{]*)?\s*\{[^}]*outline:\s*var\(--v2-focus-ring\)[^}]*\}/,
    },
    {
      label: 'StatusBar.svelte .skin-select',
      file: 'components-v2/layout/StatusBar.svelte',
      selector: /\.skin-select:focus-visible\s*\{[^}]*outline:\s*var\(--v2-focus-ring\)[^}]*\}/,
    },
    {
      label: 'MemoryPanel.svelte .ch-name-input',
      file: 'components-v2/panels/MemoryPanel.svelte',
      selector: /\.ch-name-input:focus-visible\s*\{[^}]*outline:\s*var\(--v2-focus-ring\)[^}]*\}/,
    },
    {
      label: 'LanguageSelector.svelte .lang-select',
      file: 'components-v2/controls/LanguageSelector.svelte',
      selector: /\.lang-select:focus-visible\s*\{[^}]*outline:\s*var\(--v2-focus-ring\)[^}]*\}/,
    },
    {
      label: 'SendReportDialog.svelte .field input/textarea',
      file: 'components-v2/dialogs/SendReportDialog.svelte',
      selector:
        /\.field input:focus-visible,\s*\n\s*\.field textarea:focus-visible\s*\{[^}]*outline:\s*var\(--v2-focus-ring\)[^}]*\}/,
    },
  ];

  for (const { label, file, selector } of cases) {
    it(`${label} has a :focus-visible rule using --v2-focus-ring`, () => {
      expect(read(file)).toMatch(selector);
    });
  }
});

/* ── 4. local overrides aligned to the shared v2 token ───────────────────── */

describe('MOR-1232: local overrides aligned to the shared v2 token', () => {
  it('ActiveReceiverToggle.svelte .segment ring uses --v2-focus-ring-shadow', () => {
    const rule = read('components-v2/vfo/ActiveReceiverToggle.svelte').match(
      /\.segment:focus-visible\s*\{[^}]*\}/,
    );
    expect(rule).not.toBeNull();
    expect(rule![0]).toMatch(/box-shadow:\s*var\(--v2-focus-ring-shadow\)/);
  });

  it('SegmentedButton.svelte .segmented-button ring uses --v2-focus-ring-shadow', () => {
    const rule = read('components-v2/controls/SegmentedButton.svelte').match(
      /\.segmented-button:focus-visible\s*\{[^}]*\}/,
    );
    expect(rule).not.toBeNull();
    expect(rule![0]).toMatch(/box-shadow:\s*var\(--v2-focus-ring-shadow\)/);
  });

  it('BandSelector.svelte .band-tab keeps its inset shape but takes the ring colour', () => {
    const rule = stripComments(read('components-v2/controls/BandSelector.svelte')).match(
      /\.band-tab:focus-visible\s*\{[^}]*\}/,
    );
    expect(rule).not.toBeNull();
    // Colour aligned so the tab strip clears 3:1 on the light skins …
    expect(rule![0]).toMatch(/outline:\s*1px solid var\(--v2-focus-ring-color\)/);
    // … while the deliberately inset 1px ring shape is preserved.
    expect(rule![0]).toMatch(/outline-offset:\s*-1px/);
  });
});

/* ── 5. WCAG 1.4.11 contrast, recomputed per skin (F2) ───────────────────── */

describe('MOR-1232: the focus ring clears WCAG 1.4.11 (3:1) on every skin', () => {
  // Surfaces a ring can be drawn against. `outline-offset` puts the ring just
  // outside the control, so the relevant neighbour is the container fill.
  const SURFACES = [
    '--v2-bg-app',
    '--v2-bg-panel',
    '--v2-bg-card',
    '--v2-bg-input',
    '--v2-bg-darker',
    '--v2-bg-darkest',
  ];
  const MIN_RATIO = 3;

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
    it(`${id}: ring colour is >= ${MIN_RATIO}:1 against every surface`, () => {
      const ring = resolve('--v2-focus-ring-color', tokens, base);
      expect(ring, `${id} has no resolvable --v2-focus-ring-color`).toBeTruthy();
      expect(ring!, `${id} ring colour is not a hex literal`).toMatch(/^#[0-9a-fA-F]{3,6}$/);

      const checked: string[] = [];
      for (const surface of SURFACES) {
        const bg = resolve(surface, tokens, base);
        if (!bg || !/^#[0-9a-fA-F]{3,6}$/.test(bg)) continue;
        checked.push(surface);
        const ratio = contrast(ring!, bg);
        expect(
          ratio,
          `${id}: ring ${ring} on ${surface} ${bg} = ${ratio.toFixed(2)}:1 ` +
            `(WCAG 1.4.11 non-text minimum is ${MIN_RATIO}:1). Override ` +
            '--v2-focus-ring-color in this skin.',
        ).toBeGreaterThanOrEqual(MIN_RATIO);
      }
      expect(checked.length, `${id}: no surfaces resolved — the check was vacuous`).toBeGreaterThan(
        2,
      );
    });
  }

  /* MOR-1254: the five value-control renderers colour their focus outline
   * with `--vc-focus-ring` (value-control.css), which now resolves through
   * this same `--v2-focus-ring-color` knob. Extending this loop — rather than
   * writing a parallel matrix — means a future change to either the knob or
   * value-control.css's wiring is caught by the same per-skin recompute. */
  const vcBase: Record<string, string> = {
    ...base,
    ...parseTokens(
      readFileSync(
        join(FRONTEND_SRC, 'components-v2/controls/value-control/value-control.css'),
        'utf-8',
      ),
    ),
  };

  for (const { id, tokens } of skins) {
    it(`${id}: value-control renderer focus ring (--vc-focus-ring) is >= ${MIN_RATIO}:1 against every surface`, () => {
      const ring = resolve('--vc-focus-ring', tokens, vcBase);
      expect(ring, `${id} has no resolvable --vc-focus-ring`).toBeTruthy();
      expect(ring!, `${id} --vc-focus-ring is not a hex literal`).toMatch(/^#[0-9a-fA-F]{3,6}$/);

      const checked: string[] = [];
      for (const surface of SURFACES) {
        const bg = resolve(surface, tokens, base);
        if (!bg || !/^#[0-9a-fA-F]{3,6}$/.test(bg)) continue;
        checked.push(surface);
        const ratio = contrast(ring!, bg);
        expect(
          ratio,
          `${id}: --vc-focus-ring ${ring} on ${surface} ${bg} = ${ratio.toFixed(2)}:1 ` +
            `(WCAG 1.4.11 non-text minimum is ${MIN_RATIO}:1). This drives the focus ` +
            'outline of all five value-control renderers (Knob/HBar/Discrete/Bipolar/DualParam).',
        ).toBeGreaterThanOrEqual(MIN_RATIO);
      }
      expect(checked.length, `${id}: no surfaces resolved — the check was vacuous`).toBeGreaterThan(
        2,
      );
    });
  }
});

/* ── 6. regression guard, selector-scoped (F3) ───────────────────────────── */

describe('MOR-1232: regression guard — no new unpaired outline:none suppression', () => {
  // Pre-existing debt this ticket did not fix (legacy pre-v3 tree, out of
  // scope — see the MOR-1232 fix report for the follow-up recommendation).
  // The companion test below keeps the list honest: an entry that no longer
  // needs its exemption must be removed, not left as cover.
  const LEGACY_DEBT = new Set([
    'components/spectrum/EiBiBrowser.svelte',
    'components/spectrum/SpectrumToolbar.svelte',
    'components/spectrum/SpectrumPanel.svelte',
  ]);

  // Deliberate suppressions on elements that are NOT in the tab order (roving
  // tabindex): the ring lives on the focusable container instead. Also kept
  // honest by a companion test.
  const ROVING_TABINDEX_EXEMPT: Record<string, string[]> = {
    'components-v2/controls/SegmentedButton.svelte': ['.segment:focus'],
  };

  const files = walk(FRONTEND_SRC);

  for (const file of files) {
    const rel = file.slice(FRONTEND_SRC.length + 1);
    if (LEGACY_DEBT.has(rel)) continue;
    const raw = readFileSync(file, 'utf-8');
    if (!OUTLINE_NONE.test(raw)) continue;
    const exempt = ROVING_TABINDEX_EXEMPT[rel] ?? [];

    it(`${rel}: every outline:none is paired with a focus treatment on the same element`, () => {
      const offenders = suppressionOffenders(rel, raw).filter((s) => !exempt.includes(s));
      expect(
        offenders,
        `${rel} suppresses the focus outline on ${offenders.join(', ')} with no visible ` +
          'replacement on the same element — this is the MOR-1232 "suppress it entirely" ' +
          'bug pattern. A decorative box-shadow elsewhere in the file does not count.',
      ).toEqual([]);
    });
  }

  // MOR-2522: the renderer focus rules double the container class to outrank
  // the studioline focus contract (cascade pins in section 8). A repeated
  // simple selector names the SAME element, so the pairing must read
  // `.x.x:focus-visible` as treating `.x`; before selectorTargets
  // normalised compounds, the raw string failed the equality and every
  // renderer's base `outline: none` counted as unpaired (round-4 RED).
  // The two halves prove the normalisation discriminates: pairing works
  // through the doubled class, and an unrelated doubled selector still
  // leaves the suppression unpaired.
  it('a doubled class in a :focus rule pairs with the single-class base rule (selectorTargets normalises compounds)', () => {
    const paired = suppressionOffenders(
      'guard-compound-normalisation.fixture.svelte',
      `<style>.x { outline: none; } .x.x:focus-visible { box-shadow: var(--r); }</style>`,
    );
    expect(paired).toEqual([]);

    const unpaired = suppressionOffenders(
      'guard-compound-normalisation.fixture.svelte',
      `<style>.x { outline: none; } .y.y:focus-visible { box-shadow: var(--r); }</style>`,
    );
    expect(unpaired).toEqual(['.x']);
  });

  // Round-5 edge of the same normalisation: `.a\.a` is ONE class whose name
  // contains a dot. Before the split recognised CSS escapes, `.a\.a.a`
  // collapsed to `.a\.a` and the false pair hid a real suppression — the
  // treatment additionally requires the distinct class `a`, so elements
  // with only `a.a` stayed treated by nothing. Both halves pin through the
  // real suppressionOffenders; `.a\.a` pairs only with a treatment that
  // demands no extra class.
  it('an escaped dot stays inside the class name: only escape-free repetition collapses', () => {
    const escapedPaired = suppressionOffenders(
      'guard-compound-normalisation.fixture.svelte',
      `<style>.a\\.a { outline: none; } .a\\.a:focus-visible { box-shadow: var(--r); }</style>`,
    );
    expect(escapedPaired).toEqual([]);

    const escapedFalsePair = suppressionOffenders(
      'guard-compound-normalisation.fixture.svelte',
      `<style>.a\\.a { outline: none; } .a\\.a.a:focus-visible { box-shadow: var(--r); }</style>`,
    );
    expect(escapedFalsePair).toEqual(['.a\\.a']);
  });

  it('LEGACY_DEBT entries still need their exemption (update the list, not silence it, once fixed)', () => {
    for (const rel of LEGACY_DEBT) {
      const raw = readFileSync(join(FRONTEND_SRC, rel), 'utf-8');
      expect(
        suppressionOffenders(rel, raw).length,
        `${rel} no longer matches the MOR-1232 suppression pattern — remove it from LEGACY_DEBT`,
      ).toBeGreaterThan(0);
    }
  });

  it('roving-tabindex exemptions still describe non-tabbable elements with a container ring', () => {
    for (const [rel, selectors] of Object.entries(ROVING_TABINDEX_EXEMPT)) {
      const raw = readFileSync(join(FRONTEND_SRC, rel), 'utf-8');
      expect(raw, `${rel}: exemption assumes the element is out of the tab order`).toMatch(
        /tabindex="-1"/,
      );
      // The focusable container must still carry a real ring of its own.
      const css = stripComments(styleText(rel, raw));
      expect(
        ruleBlocks(css).some((b) => /:focus-visible/.test(b.selector) && HAS_REPLACEMENT.test(b.body)),
        `${rel}: no container-level :focus-visible ring — the exemption is not safe`,
      ).toBe(true);
      // And the exemption must still be needed.
      expect(suppressionOffenders(rel, raw)).toEqual(expect.arrayContaining(selectors));
    }
  });
});

/* ── 7. MOR-2522: value-control focus lights the control, never a frame ───── */

describe('MOR-2522: value-control focus is an illumination, not an outline frame', () => {
  const VC_CSS = 'components-v2/controls/value-control/value-control.css';
  // jsdom cannot measure geometry from these stylesheets, so the no-shift pin
  // is the declared property set: a focus/arming rule that could move, resize
  // or reshape the control needs a property outside this list to do it.
  const RING_ONLY = /^(?:outline|box-shadow)$/;

  const RENDERERS: Array<{ file: string; container: string }> = [
    { file: 'components-v2/controls/value-control/HBarRenderer.svelte', container: '.vc-track-container' },
    { file: 'components-v2/controls/value-control/DiscreteRenderer.svelte', container: '.vc-track-container' },
    { file: 'components-v2/controls/value-control/BipolarRenderer.svelte', container: '.vc-track-container' },
    { file: 'components-v2/controls/value-control/KnobRenderer.svelte', container: '.vc-knob-container' },
    { file: 'components-v2/controls/value-control/DualParamRenderer.svelte', container: '.vc-track-container' },
  ];

  it('value-control.css wires the shadow twin off the contrast-pinned knob', () => {
    const css = read(VC_CSS);
    // MOR-1254 wiring unchanged: the colour still proxies --v2-focus-ring-color,
    // recomputed per skin by section 5's loop above.
    expect(css).toMatch(/--vc-focus-ring:\s*var\(--v2-focus-ring-color\)\s*;/);
    expect(css).not.toMatch(/--vc-focus-ring:\s*var\(--v2-accent-cyan\)/);
    // MOR-2522: the painted ring composes that same colour and the shared
    // width knob, so the section-5 matrix governs what reaches the screen.
    expect(css).toMatch(
      /--vc-focus-ring-shadow:\s*0 0 0 var\(--vc-focus-ring-width,\s*2px\)\s*var\(--vc-focus-ring\)\s*;/,
    );
  });

  it('wheel-control.ts no longer writes an inline outline (the defect mechanism this ticket removes)', () => {
    expect(read('components-v2/controls/value-control/wheel-control.ts')).not.toMatch(/style\.outline/);
  });

  it('arming is lit from the data-wheel-armed attribute with the same ring', () => {
    const css = stripComments(read(VC_CSS));
    const armed = ruleBlocks(css).find((b) => /\[data-wheel-armed='true'\]/.test(b.selector));
    expect(armed, 'expected a data-wheel-armed illumination rule in value-control.css').toBeTruthy();
    expect(armed!.selector).toContain('.vc-track-container');
    expect(armed!.selector).toContain('.vc-knob-container');
    expect(OUTLINE_NONE.test(armed!.body)).toBe(true);
    expect(armed!.body).toMatch(/box-shadow:\s*var\(--vc-focus-ring-shadow\)/);
  });

  for (const { file, container } of RENDERERS) {
    it(`${file}: :focus-visible draws no outline, declares only the ring`, () => {
      const css = stripComments(styleText(file, read(file)));
      const rule = css.match(/:focus-visible\s*\{[^}]*\}/);
      expect(rule, `${file}: expected a :focus-visible rule`).not.toBeNull();
      const body = rule![0];
      // The illumination is the shared full-opacity spread ring, and no
      // outline declaration in the focus rule survives other than `none`
      // (owner ruling: no frame). Declaration-level, because a negative
      // lookahead on the raw body backtracks past the whitespace and
      // "matches" `outline: none`.
      const declarations = body
        .slice(body.indexOf('{') + 1, -1)
        .split(';')
        .map((d) => d.trim())
        .filter(Boolean);
      const outlines = declarations.filter((d) => d.startsWith('outline:'));
      expect(outlines, `${file}: :focus-visible still declares an outline`).toEqual(['outline: none']);
      expect(declarations).toContain('box-shadow: var(--vc-focus-ring-shadow)');
      // No-shift pin: beyond outline/box-shadow a focus rule cannot change
      // what the operator sees of the control's geometry.
      const properties = declarations.map((d) => d.split(':')[0].trim());
      expect(properties, `${file}: :focus-visible declares more than the ring`).toEqual(
        properties.filter((p) => RING_ONLY.test(p)),
      );
      // Shape-following: a box-shadow ring follows the container's own
      // radius, so the radius must live on the unfocused base rule.
      const base = ruleBlocks(css).find((b) => b.selector === container);
      expect(base, `${file}: expected a base ${container} rule`).toBeTruthy();
      expect(base!.body, `${file}: base ${container} lost its border-radius`).toMatch(/border-radius:/);
    });
  }

  it('no value-control focus rule colours itself with the unchecked per-instance accent', () => {
    for (const { file } of RENDERERS) {
      const css = stripComments(styleText(file, read(file)));
      for (const block of ruleBlocks(css)) {
        if (!/:focus/.test(block.selector)) continue;
        // MOR-1254 regression guard, restated for the shadow carrier:
        // `--vc-accent` / `--vc-rf-accent` caused the 1.48-1.74 nord-light
        // ratios that ticket fixes. They remain legitimate for fills/ticks.
        expect(
          block.body,
          `${file}: ${block.selector} colours focus with the per-instance accent`,
        ).not.toMatch(/(?:outline|box-shadow):\s*[^;]*var\(--vc-(?:accent|rf-accent)\)/);
      }
    }
  });
});

describe('MOR-2522: the value-control ring clears 3:1 on the studioline surfaces', () => {
  const STUDIOLINE = 'presentation/languages/studioline/studioline.css';

  /** Effective tokens of one studioline selector. Comments are stripped
   * first (the root block's prose holds literal braces, which would end the
   * `[^{}]*` body early), and when the selector repeats — the light-mode
   * selector appears twice, lines ~142 and ~161 — the blocks merge in order,
   * because the cascade makes the later block's declarations win. */
  function studiolineTokens(selector: string): Record<string, string> {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const re = new RegExp(`${escaped}\\s*\\{([^{}]*)\\}`, 'g');
    const merged: Record<string, string> = {};
    let m: RegExpExecArray | null;
    let matched = false;
    while ((m = re.exec(stripComments(read(STUDIOLINE))))) {
      matched = true;
      Object.assign(merged, parseTokens(m[1]));
    }
    expect(matched, `expected a studioline block for ${selector}`).toBeTruthy();
    return merged;
  }

  it('the per-skin colour knob stays the ONLY declaration of --vc-focus-ring', () => {
    const css = read('components-v2/controls/value-control/value-control.css');
    // The section-5 matrix resolves --vc-focus-ring from this file with a
    // selector-blind token parse: any second declaration — a scoped
    // override later in the file — silently replaces the per-skin default
    // for EVERY skin (the cb052c2e failure). Scoped colours ride the shadow
    // token instead, which the matrix never resolves.
    const declarations = css.match(/--vc-focus-ring:/g) ?? [];
    expect(declarations, 'value-control.css must declare --vc-focus-ring exactly once').toHaveLength(1);
  });

  it('value-control.css carries the studioline ring colour only inside the language scope', () => {
    // Non-vacuous link: without this override the ring keeps the v2 cyan
    // knob, which reaches only ~1.66:1 on the light studioline surface —
    // the matrix below would be testing a colour the control never paints.
    // Scoped to --vc-focus-ring-shadow (the token the five renderers and the
    // armed rule actually consume), never to the per-skin knob itself.
    expect(stripComments(read('components-v2/controls/value-control/value-control.css'))).toMatch(
      /\[data-design-language='studioline'\]\s*\{[^}]*--vc-focus-ring-shadow:\s*0 0 0 var\(--vc-focus-ring-width,\s*2px\)\s*var\(--dl-studioline-focus,\s*#00819f\)\s*;/,
    );
  });

  it('the studioline ring colour clears 3:1 against BOTH studioline surfaces', () => {
    const dark = studiolineTokens("[data-design-language='studioline'][data-design-language]");
    const light = studiolineTokens(
      "[data-design-language='studioline'][data-design-language][data-language-mode='light']",
    );

    const ring = dark['--dl-studioline-focus'];
    expect(ring, 'studioline no longer declares --dl-studioline-focus').toMatch(/^#[0-9a-fA-F]{3,6}$/);
    for (const [mode, tokens] of [
      ['dark', dark],
      ['light', light],
    ] as const) {
      const surface = tokens['--dl-studioline-surface'];
      expect(surface, `studioline ${mode} surface literal is unreadable`).toMatch(/^#[0-9a-fA-F]{3,6}$/);
      const ratio = contrast(ring!, surface!);
      expect(
        ratio,
        `studioline ${mode}: value-control ring ${ring} on ${surface} = ${ratio.toFixed(2)}:1 ` +
          '(WCAG 1.4.11 non-text minimum is 3:1).',
      ).toBeGreaterThanOrEqual(3);
    }
  });
});

/* ── 8. MOR-2522: the suppression outranks the studioline focus contract ──── */

describe('MOR-2522: renderer focus suppression outranks the studioline focus contract', () => {
  // How the frame came back on the studioline page (review of bf14409b):
  // Svelte compiles `.vc-track-container:focus-visible` to
  // `.vc-track-container.svelte-xxxx:focus-visible` = (0,3,0); studioline's
  // `:focus-visible` rule is also (0,3,0) and loads dynamically AFTER the
  // component styles (fixtures/main.ts), so it won the tie on source order
  // and its literal outline framed the slider again. The fix doubles the
  // class — the same one-step raise studioline.css itself uses — so the
  // compiled rule is (0,4,0) and wins on specificity alone, whatever the
  // load order. These pins re-derive that ranking from both stylesheets:
  // a selector that drops back to a tie fails here, and so does a raise of
  // the studioline rule's own specificity.
  const STUDIOLINE = 'presentation/languages/studioline/studioline.css';
  const RENDERER_CONTAINERS: Array<{ file: string; container: string }> = [
    { file: 'components-v2/controls/value-control/HBarRenderer.svelte', container: '.vc-track-container' },
    { file: 'components-v2/controls/value-control/DiscreteRenderer.svelte', container: '.vc-track-container' },
    { file: 'components-v2/controls/value-control/BipolarRenderer.svelte', container: '.vc-track-container' },
    { file: 'components-v2/controls/value-control/KnobRenderer.svelte', container: '.vc-knob-container' },
    { file: 'components-v2/controls/value-control/DualParamRenderer.svelte', container: '.vc-track-container' },
  ];

  const studiolineFocus = ruleBlocks(stripComments(read(STUDIOLINE))).find(
    (b) => /:focus-visible/.test(b.selector) && /outline/.test(b.body),
  );

  it('the studioline focus contract is still the (0,3,0) literal-outline rule this ranking assumes', () => {
    expect(studiolineFocus, 'expected a :focus-visible outline rule in studioline.css').toBeTruthy();
    expect(`${studiolineFocus!.selector} = ${specificity(studiolineFocus!.selector)}`).toBe(
      `[data-design-language='studioline'][data-design-language] :focus-visible = 0,3,0`,
    );
  });

  it('the language stylesheet still loads dynamically, i.e. after the component styles', () => {
    // The premise of "wins on specificity alone, never on order": the
    // language CSS must stay a dynamic import in the harness. A static
    // import would flip the source order this pin deliberately ignores.
    expect(read('../fixtures/main.ts')).toMatch(
      /import\('\.\.\/src\/presentation\/languages\/studioline\/studioline\.css'\)/,
    );
  });

  for (const { file, container } of RENDERER_CONTAINERS) {
    it(`${file}: compiled :focus-visible rule outranks the studioline rule on specificity alone`, () => {
      const css = stripComments(styleText(file, read(file)));
      const rule = ruleBlocks(css).find(
        (b) => b.selector.includes(`${container}:focus-visible`) && b.body.includes('outline: none'),
      );
      expect(rule, `${file}: expected the ${container} :focus-visible suppression rule`).toBeTruthy();
      const compiled = svelteCompiled(rule!.selector);
      const rival = specificity(studiolineFocus!.selector);
      expect(
        outranks(compiled, rival),
        `${file}: compiled ${rule!.selector} → ${compiled} must outrank studioline ` +
          `${studiolineFocus!.selector} → ${rival} by specificity, because the language ` +
          'stylesheet loads later and would win any tie on source order.',
      ).toBe(true);
    });
  }
});

/* ── 9. MOR-2522: native range sliders share one lighting rule ───────────── */

describe('MOR-2522: native range sliders light on keyboard focus instead of drawing a frame', () => {
  // Six call sites — semantic/FilterSurface.svelte (WIDTH / IF SHIFT, the
  // stand defect), DspSurface.svelte, CwKeyerSurface.svelte,
  // RitXitScanSurface.svelte, components-v2/panels/AudioRoutingControl.svelte,
  // CwPanel.svelte — justify ONE shared rule, not six copies. Its home is
  // app.css, the only stylesheet loaded unconditionally in every context that
  // mounts them; value-control.css is not (nothing in those six files imports
  // it), so the rule consumes --vc-focus-ring-shadow only as a preference over
  // its theme twin — and the chain ends there. Every context that mounts a
  // native range loads one of the two token owners first (production reaches
  // value-control.css through the SemanticRadioSurfaces → DspScalarHost →
  // ValueControl import chain; the harness statically imports
  // DualReceiverCockpit and ReferenceLayout, which pull in the v2 theme and
  // the same value-control chain), so a terminal --accent fallback could
  // never paint — and if it could, #4db6ff is the sub-3:1 light-language
  // ring this file's header rules out.
  //
  // Round 2 (stand probe at 224ff7e3): the shared rule wins the language
  // contracts but LOSES box-shadow on the desktop-v2/sdr-test skins, where
  // semantic-controls.css's track chrome is (0,3,2) — box-shadow does not
  // merge across rules, so the focused slider showed track-only with no ring
  // at all. The illumination there is carried by a :focus-visible variant of
  // the track rule's own selector, pinned below from the real stylesheet.
  const RANGE_RULE_SELECTOR = "input[type='range'][type='range']:focus-visible";
  const RANGE_ONLY = /^(?:outline|box-shadow)$/;
  const SKIN_CSS = 'skins/desktop-v2/semantic-controls.css';
  const RING_TOKEN_CHAIN = 'var(--vc-focus-ring-shadow, var(--v2-focus-ring-shadow))';
  const NATIVE_RANGE_SURFACES = [
    'semantic/FilterSurface.svelte',
    'semantic/DspSurface.svelte',
    'semantic/CwKeyerSurface.svelte',
    'semantic/RitXitScanSurface.svelte',
    'components-v2/panels/AudioRoutingControl.svelte',
    'components-v2/panels/CwPanel.svelte',
  ];

  const rangeRule = ruleBlocks(stripComments(read('app.css'))).find(
    (b) => b.selector === RANGE_RULE_SELECTOR,
  );

  it('app.css carries the ONE shared focus rule for native range sliders, ring-only', () => {
    expect(rangeRule, 'expected the shared native-range focus rule in app.css').toBeTruthy();
    const declarations = rangeRule!.body
      .split(';')
      .map((d) => d.trim())
      .filter(Boolean);
    expect(declarations).toContain('outline: none');
    // The value-control token where value-control.css is loaded (studioline
    // colour override included), its theme twin where that file is absent —
    // never a colour literal, so every colour the chain can paint is a
    // declared token (the section-5 matrix governs both levels). The chain
    // ends at the theme twin: every render context loads one of the two token
    // owners before any native range mounts, so a terminal fallback would be
    // unreachable decoration.
    expect(declarations).toContain(
      'box-shadow: var(--vc-focus-ring-shadow, var(--v2-focus-ring-shadow))',
    );
    // No-shift pin, the same declared-property-set contract section 7 holds
    // the renderers to: beyond outline/box-shadow this rule cannot move,
    // resize or reshape the slider.
    const properties = declarations.map((d) => d.split(':')[0].trim());
    expect(properties).toEqual(properties.filter((p) => RANGE_ONLY.test(p)));
  });

  it('app.css is the unconditional shared load in both the app and the harness', () => {
    // The premise of "one shared home": both entry points import app.css
    // statically. The harness then loads the language stylesheet dynamically
    // (section 8's pin), which is exactly why the rule must win on
    // specificity, not order.
    expect(read('App.svelte')).toMatch(/import '\.\/app\.css';/);
    expect(read('../fixtures/main.ts')).toMatch(/import '\.\.\/src\/app\.css';/);
  });

  it('the shared rule outranks both language focus contracts on specificity alone', () => {
    for (const lang of ['studioline', 'fieldline']) {
      const file = `presentation/languages/${lang}/${lang}.css`;
      const contract = ruleBlocks(stripComments(read(file))).find(
        (b) => /:focus-visible/.test(b.selector) && /outline/.test(b.body),
      );
      expect(contract, `expected a :focus-visible outline rule in ${lang}.css`).toBeTruthy();
      const rival = specificity(contract!.selector);
      expect(
        outranks(specificity(RANGE_RULE_SELECTOR), rival),
        `${RANGE_RULE_SELECTOR} = ${specificity(RANGE_RULE_SELECTOR)} must outrank ` +
          `${contract!.selector} = ${rival}: the language stylesheet loads after app.css ` +
          '(fixtures/main.ts), so a tie would be lost on source order.',
      ).toBe(true);
    }
  });

  it('the doubled attribute and the element type are both load-bearing: weaker selectors tie or lose', () => {
    // Discriminating halves of the cascade pin. Drop one attribute and the
    // rule falls to (0,2,1), which the (0,3,0) studioline contract beats on
    // b — the exact regression shape 17b7c794 fixed for the renderers. Drop
    // the element type instead and (0,3,0) only TIES the contract, so the
    // later-loading language stylesheet wins on source order; the real rule's
    // victory lives in the c column, which is why specificity() counts types.
    const studioline = ruleBlocks(
      stripComments(read('presentation/languages/studioline/studioline.css')),
    ).find((b) => /:focus-visible/.test(b.selector) && /outline/.test(b.body))!;
    const rival = specificity(studioline.selector);
    expect(outranks(specificity("input[type='range']:focus-visible"), rival)).toBe(false);
    expect(outranks(specificity("[type='range'][type='range']:focus-visible"), rival)).toBe(false);
  });

  it('the desktop-v2 generic frame rule no longer matches range inputs', () => {
    // semantic-controls.css's `section :is(button, input:not([type='range']),
    // select):focus-visible` outranks even the doubled-attribute rule, so
    // excluding range inputs from that selector is what frees them from the
    // FRAME on the desktop-v2/sdr-test skins (the stand's filter panel is one:
    // `.desktop-control-face .semantic-control-panel`). The ILLUMINATION there
    // is not app.css's, though — the same file's track chrome owns box-shadow
    // at (0,3,2) — but the composing variant pinned directly below.
    const frame = ruleBlocks(stripComments(read(SKIN_CSS))).find(
      (b) => /:is\(button/.test(b.selector) && /:focus-visible/.test(b.selector),
    );
    expect(frame, 'expected the desktop-v2 generic :focus-visible frame rule').toBeTruthy();
    expect(frame!.selector).toContain("input:not([type='range'])");
    expect(frame!.body).toMatch(/outline:\s*2px solid/);
  });

  // The round-2 cascade pins. The track chrome rule and its :focus-visible
  // variant are both located in the REAL stylesheet, and the ranking is
  // recomputed from their live selectors: the pin fails if the track rule
  // gains specificity (a tie is a loss — the chrome would eat the ring again)
  // or if the focus variant loses it.
  const skinBlocks = ruleBlocks(stripComments(read(SKIN_CSS)));
  // The track chrome: the unfocused rule that owns box-shadow on the INPUT
  // element itself (the ::-webkit-slider-thumb / ::-moz-range-thumb rules
  // style a different element and never collide with the ring).
  const trackChrome = skinBlocks.find(
    (b) =>
      /input\[type='range'\]/.test(b.selector) &&
      !/:focus/.test(b.selector) &&
      !/::/.test(b.selector) &&
      /box-shadow/.test(b.body),
  );
  const skinFocus = skinBlocks.find((b) =>
    /input\[type='range'\]:focus-visible/.test(b.selector),
  );

  it('the desktop-v2 focus variant is the track rule’s own selector plus :focus-visible', () => {
    expect(trackChrome, 'expected the desktop-v2 track chrome rule').toBeTruthy();
    expect(skinFocus, 'expected a :focus-visible variant of the track rule').toBeTruthy();
    // Not a parallel selector that could drift: exactly the track compound
    // with the focus pseudo appended — same element, one state later.
    expect(skinFocus!.selector).toBe(`${trackChrome!.selector}:focus-visible`);
  });

  it('the focus variant outranks the track chrome on specificity alone', () => {
    const focus = specificity(skinFocus!.selector);
    const track = specificity(trackChrome!.selector);
    expect(
      outranks(focus, track),
      `${skinFocus!.selector} = ${focus} must outrank the track chrome ` +
        `${trackChrome!.selector} = ${track}: box-shadow does not merge across ` +
        'rules, so a tie or a loss means the focused slider paints track-only ' +
        'with no ring — the 224ff7e3 stand defect.',
    ).toBe(true);
  });

  it('app.css’s shared rule cannot win this collision — the skin variant is load-bearing', () => {
    // Discriminating half: (0,3,1) loses the c column to the track rule's two
    // type selectors (`section`, `input`). If the track rule ever DROPS below
    // the shared rule this pin fails too, because the variant would then be
    // redundant duplication rather than the only carrier of the ring.
    const track = specificity(trackChrome!.selector);
    expect(outranks(specificity(RANGE_RULE_SELECTOR), track)).toBe(false);
  });

  it('the focus variant composes: ring token chain prepended to the track chrome, layer-for-layer', () => {
    const shadowOf = (body: string) => {
      // A box-shadow value never contains `;` (its commas sit inside var()),
      // so cutting at the first semicolon isolates the one declaration even
      // in the track rule's multi-declaration body.
      const value = body.match(/box-shadow:\s*([^;]*)/)?.[1].trim();
      expect(value, 'expected a box-shadow declaration').toBeTruthy();
      return shadowLayers(value!);
    };
    // ONE owner of the chrome: the track rule declares the three-layer inset
    // stack exactly once, as a custom property on its own selector scope, and
    // the focus variant (the same element one state later) inherits it. This
    // is the discriminating check on the stack itself — a dropped, reordered
    // or retyped layer fails here, at the only place the literals live.
    const chrome = trackChrome!.body.match(/--range-track-chrome:\s*([^;]*)/)?.[1].trim();
    expect(
      chrome,
      'expected the ONE --range-track-chrome declaration on the track rule',
    ).toBeTruthy();
    expect(shadowLayers(chrome!)).toEqual([
      'inset 0 2px 4px var(--vc-hw-channel-shadow-1, #000f)',
      'inset 0 -1px 2px var(--vc-hw-channel-shadow-2, #0009)',
      'inset 0 0 0 1px var(--vc-hw-channel-border, #0008)',
    ]);
    // Both rules REFERENCE the one owner — a second literal copy of the stack
    // in either rule is the drift hazard this factoring removes, and it fails
    // here even if the copies happen to agree today.
    expect(shadowOf(trackChrome!.body)).toEqual(['var(--range-track-chrome)']);
    // The variant paints the ring token chain — never a colour literal — in
    // front of the same chrome, by reference, so the composition cannot
    // silently drop the channel shading when either rule is edited.
    expect(shadowOf(skinFocus!.body)).toEqual([RING_TOKEN_CHAIN, 'var(--range-track-chrome)']);
    // No-geometry pin: the variant declares the ring and nothing else.
    const properties = skinFocus!.body
      .split(';')
      .map((d) => d.split(':')[0].trim())
      .filter(Boolean);
    expect(properties).toEqual(['box-shadow']);
  });

  it('the six native-range surfaces declare no private outline treatment of their own', () => {
    // Same shape as MOR-2509's deck pin above: the shared rule in app.css is
    // the ONLY focus treatment these files get — a per-file copy is the
    // six-copies defect this ticket refuses.
    for (const file of NATIVE_RANGE_SURFACES) {
      expect(stripComments(styleText(file, read(file))), file).not.toMatch(/outline\s*:/);
    }
  });
});
