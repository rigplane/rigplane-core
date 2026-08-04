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
 *   - value-control renderers (Knob/HBar/Discrete/Bipolar/DualParam): their
 *     focus outlines use `--vc-accent` — which is the general accent, shared
 *     with tick marks and fills — while `--vc-focus-ring`
 *     (value-control.css:24) is itself declared and referenced NOWHERE, i.e.
 *     a second instance of this ticket's dead-token defect. Fixing it means
 *     retargeting five renderers, one of which (DualParamRenderer) backs the
 *     dual-receiver cockpit that is out of scope here. Follow-up ticket.
 *   - legacy `src/components/spectrum/*` suppressions: pre-v3 code, listed
 *     under LEGACY_DEBT below rather than silently masked by the guard.
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
// A real, visible focus treatment: an outline set to a non-none value, or a
// var()-driven box-shadow.
const HAS_REPLACEMENT = /(?<![\w-])outline:\s*(?!none\b|0\b)\S|box-shadow:\s*(?!none\b)[^;]*var\(/;

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
 *  stripped: `.a:focus, .b input:focus-visible` → ['.a', '.b input']. */
function selectorTargets(selector: string): string[] {
  return selector
    .split(',')
    .map((s) =>
      s
        .replace(/::?[\w-]+(\([^()]*\))?/g, '')
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
      selector: /\.v2-control-button:focus-visible\s*\{[^}]*outline:\s*var\(--v2-focus-ring\)[^}]*\}/,
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
