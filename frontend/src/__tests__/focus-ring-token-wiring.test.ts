/**
 * MOR-1232 built the shared focus tokens and pinned, among other things,
 * that the global default must be `outline`-shaped: a global `box-shadow`
 * (0,1,0) loses to any Svelte-scoped component shadow (0,2,0)+, and outline
 * was the one property components never set decoratively.
 *
 * MOR-2522 (owner rulings 2026-09-21/22: "no focus frame anywhere", with a
 * screenshot of the AF LEVEL slider carrying a 2 px cyan rectangle after a
 * mouse click) reverses the SHAPE, not the cascade discipline. The frame in
 * that screenshot was painted by wheel-control.ts as an INLINE outline on
 * pointerdown; the renderers' `:focus-visible` rectangles doubled it on
 * keyboard focus. Focus is now ILLUMINATION — the VFO deck's "lit, not
 * framed" treatment (PR #3562), app-wide:
 *
 *   1. app.css `:focus-visible` → `outline: none` + a drop-shadow glow in
 *      the per-skin knob colour. `filter`, because it is the one glow
 *      carrier a (0,1,0) global rule can safely use: scoped decorative
 *      `box-shadow`s (TxPanel's PTT transmit glow) would erase a global
 *      shadow, and scoped `filter` exists only in the deck, which overrides
 *      the global at (0,3,0) with its own brightness steps.
 *   2. tokens.css owns the whole `--v2-focus-*` set: one colour knob
 *      (`--v2-focus-ring-color`, contrast-pinned per skin ≥ 3:1 below) and
 *      the illumination values derived from it. The retired frame tokens
 *      still resolve (`--v2-focus-ring: none`) so unmigrated rules paint
 *      nothing instead of a border; PR 2 (MOR-2522) deletes them.
 *   3. The value-control layer lights the focused or wheel-armed track and
 *      thumb through `--vc-illum-focus-glow`, fed from the contract; the
 *      renderers' own outline rules are retired via `--vc-focus-ring-width:
 *      0px` and deleted in PR 2; wheel-control.ts no longer paints an
 *      inline outline at all.
 *
 * Perceivability is preserved (MOR-977 §1.2.5 "never re-suppressed"):
 * the illumination colour is the knob, recomputed ≥ 3:1 against every
 * surface of every skin (dark and light) in the matrix below; under
 * `prefers-contrast: more` the pair strengthens; under `forced-colors`
 * (where filters and shadows do not paint) a system outline is restored.
 * Nothing changes geometry: shadow, filter and outline paint outside the
 * box, and the tests below forbid size-affecting declarations in every
 * focus rule of the contract.
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
 * matched with `.includes`, never `===`. */
function ruleBlocks(css: string): Array<{ selector: string; body: string }> {
  const out: Array<{ selector: string; body: string }> = [];
  const re = /([^{}]+)\{([^{}]*)\}/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(css))) out.push({ selector: m[1].replace(/\s+/g, ' ').trim(), body: m[2] });
  return out;
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

const GEOMETRY = /\b(?:width|height|padding|margin|border(?:-width)?)\s*:/;

function globalFocusRule(): string {
  const rule = stripComments(read('app.css')).match(/:focus-visible\s*\{[^}]*\}/);
  expect(rule, 'expected a top-level :focus-visible rule in app.css').not.toBeNull();
  return rule![0].replace(/\s+/g, ' ');
}

/* ── 1. the app-wide contract (app.css) ──────────────────────────────────── */

describe('MOR-2522: the global focus rule is illumination, never a frame', () => {
  it('removes every outline (including the UA frame) and paints light', () => {
    const rule = globalFocusRule();
    expect(rule).toMatch(/outline:\s*none/);
    expect(rule).toMatch(/filter:\s*var\(--v2-focus-illum-filter,/);
  });

  it('keeps a legacy-accent fallback for the code-split pre-v2 window', () => {
    const rule = globalFocusRule();
    expect(rule).toMatch(/drop-shadow\(0 0 1px var\(--accent\)\)/);
    expect(rule).toMatch(/drop-shadow\(0 0 6px color-mix\(in srgb, var\(--accent\) 55%, transparent\)\)/);
  });

  it('carries no shadow (a global shadow loses to scoped component shadows) and no geometry', () => {
    const rule = globalFocusRule();
    expect(
      /box-shadow/.test(rule),
      'a (0,1,0) global box-shadow is erased by any Svelte-scoped component shadow',
    ).toBe(false);
    expect(GEOMETRY.test(rule), 'focus must not change any element geometry').toBe(false);
  });

  it('TxPanel PTT keeps its glow while transmitting (the MOR-1232 defect class)', () => {
    const tx = read('components-v2/panels/TxPanel.svelte');
    // The held/latched transmit state paints a scoped box-shadow …
    expect(tx).toMatch(/\.ptt-button\.ptt-held,[\s\S]{0,80}\{[^}]*box-shadow:/);
    // … and TxPanel declares no filter rule at all, so the global filter
    // glow survives that shadow — nothing else paints for the PTT button,
    // the most safety-critical control in the app.
    const css = stripComments(styleText('components-v2/panels/TxPanel.svelte', tx));
    expect(/(?<![\w-])filter\s*:/.test(css), 'TxPanel gained a filter rule; re-derive this test').toBe(
      false,
    );
    expect(globalFocusRule()).toMatch(/filter:\s*var\(--v2-focus-illum-filter,/);
  });
});

/* ── 2. the token set (components-v2/theme/tokens.css) ───────────────────── */

describe('MOR-2522: one --v2-focus-* set, one knob, illumination only', () => {
  // Raw text (newline structure intact) for the @media block extraction —
  // the blocks close with a column-0 `}`; flattened text for the token
  // values, which are declared across continuation lines.
  const raw = () => stripComments(read('components-v2/theme/tokens.css'));
  const flat = () => raw().replace(/\s+/g, ' ');

  it('derives every illumination value from the single per-skin knob', () => {
    const source = flat();
    expect(source).toMatch(/--v2-focus-ring-color: var\(--v2-accent-cyan\)/);
    expect(source).toMatch(
      /--v2-focus-illum-core: 0 0 4px 1px var\(--v2-focus-ring-color\)/,
    );
    expect(source).toMatch(
      /--v2-focus-illum-halo: 0 0 14px 2px color-mix\(in srgb, var\(--v2-focus-ring-color\) 55%, transparent\)/,
    );
    expect(source).toMatch(
      /--v2-focus-illum-shadow: var\(--v2-focus-illum-core\), var\(--v2-focus-illum-halo\)/,
    );
    expect(source).toMatch(
      /--v2-focus-illum-filter: drop-shadow\(0 0 1px var\(--v2-focus-ring-color\)\) drop-shadow\(0 0 6px color-mix\(in srgb, var\(--v2-focus-ring-color\) 55%, transparent\)\)/,
    );
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
    expect(flatBlock).toMatch(/--v2-focus-illum-core: 0 0 5px 2px var\(--v2-focus-ring-color\)/);
    expect(flatBlock).toMatch(/--v2-focus-illum-halo: 0 0 16px 3px var\(--v2-focus-ring-color\)/);
    expect(flatBlock).toMatch(
      /--v2-focus-illum-filter: drop-shadow\(0 0 2px var\(--v2-focus-ring-color\)\) drop-shadow\(0 0 8px var\(--v2-focus-ring-color\)\)/,
    );
    expect(block).not.toMatch(/outline\s*:/);
  });

  it('forced-colors restores a system outline (filters and shadows do not paint there)', () => {
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

  it('lights the focused control and declares no outline property at all', () => {
    const source = css();
    expect(source).not.toMatch(/outline\s*:/);
    const rule = source.match(
      /\[data-design-language='studioline'\]\[data-design-language\] :focus-visible\s*\{[^}]*\}/,
    )!;
    expect(rule[0].replace(/\s+/g, ' ')).toMatch(
      /filter:\s*var\(--v2-focus-illum-filter, drop-shadow\(0 0 1px var\(--dl-studioline-focus\)\) drop-shadow\(0 0 6px color-mix\(in srgb, var\(--dl-studioline-focus\) 55%, transparent\)\)\)/,
    );
  });

  /* MOR-977 §3.2/§4.4 obligations, recomputed from the literals actually in
   * the file: the illumination colour must clear 3:1 on BOTH language
   * surfaces — the dark default and the light-mode override. */
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

  it.each([
    ['dark', token('--dl-studioline-surface')],
    ['light', lightSurface()],
  ])('focus colour clears 3:1 on the %s surface', (_label, surface) => {
    const ratio = contrast(token('--dl-studioline-focus'), surface);
    expect(
      ratio,
      `studioline focus colour on ${surface} = ${ratio.toFixed(2)}:1 (WCAG 1.4.11 needs 3:1)`,
    ).toBeGreaterThanOrEqual(3);
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

/* ── 5. WCAG 1.4.11 contrast of the knob, recomputed per skin ────────────── */

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
});

/* ── 6. the deck's own focus steps stay exactly as shipped (MOR-2509) ────── */

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
      expect(stripComments(styleText(file, read(file))), file).not.toMatch(/outline\s*:/);
    }
  });
});

/* ── 7. scoped box-shadow illumination aligned to the contract ───────────── */

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

/* ── 8. known glow maskers (scoped filter beats the global glow) ─────────── */

describe('MOR-2522: scoped filters that can mask the global glow are enumerated', () => {
  /* A scoped `filter` on or around a focused element replaces the global
   * glow (the glow is carried by `filter` precisely because scoped
   * `box-shadow` is everywhere; scoped `filter` is rare instead — the
   * display-only filters in StatusBadge/LinearSMeter/lcd-vintage sit on
   * elements that can never match :focus-visible and mask nothing). Every
   * real masking site is listed with why it is acceptable; a new one must
   * be added here consciously, not discovered by the operator. */
  const KNOWN_GLOW_MASKERS: Array<{ file: string; decl: RegExp; why: string }> = [
    {
      file: 'components-v2/layout/RadioLayout.svelte',
      decl: /filter:\s*saturate\(0\.08\) contrast\(0\.5\) brightness\(0\.62\)/,
      why: 'data-link-fault veil: during a control-link fault it replaces the glow on the veiled children; the fault state ends interaction and the veil itself is the signal',
    },
    {
      file: 'components-v2/vfo/VfoPanel.svelte',
      decl: /filter:\s*brightness\(1\.15\)/,
      why: 'mode-badge chip hover brightening: a focused-and-hovered chip shows the hover step instead — the hover already reads as the live affordance',
    },
  ];

  it.each(KNOWN_GLOW_MASKERS)('$file still carries the listed filter', ({ file, decl }) => {
    expect(stripComments(styleText(file, read(file)))).toMatch(decl);
  });
});

/* ── 9. the frame ban: no outline in any :focus-visible rule outside ────────
 *      the contract files and the explicit PR-2 list ──────────────────────── */

describe('MOR-2522: no :focus-visible rule declares an outline outside the contract', () => {
  // The contract's own files: app.css (the global `outline: none`) and
  // theme/tokens.css (the forced-colors system-outline restoration).
  const CONTRACT_FILES = new Set(['app.css', 'components-v2/theme/tokens.css']);

  /* Every remaining outline declaration under a :focus-visible rule, filed
   * for PR 2 (MOR-2522). Groups:
   *   A. token consumers — `outline: var(--v2-focus-ring)` or
   *      `var(--vc-focus-ring-width, 2px) solid var(--vc-focus-ring)` —
   *      which now paint NOTHING (the tokens resolve to `none` / 0px); PR 2
   *      deletes the rules outright.
   *   B. literal frames — still visible today; PR 2 converts them to the
   *      illumination contract.
   *   C. the deck's two blocks in control-button.css — the contract's own
   *      model (`outline: none` + brightness step); they stay.
   * An entry that no longer matches must be removed from the list, not left
   * as cover (companion test below). */
  const OUTLINE_ALLOW: Record<string, string[]> = {
    /* legacy pre-v3 tree */
    'components/spectrum/SpectrumPanel.svelte': [
      '.spectrum-split-separator:focus-visible', // B
      '.passband-resize-zone:focus-visible', // A (`outline: none`, roving target)
    ],
    /* A — token consumers */
    'components-v2/controls/LanguageSelector.svelte': ['.lang-select:focus-visible'],
    'components-v2/controls/WorkspaceImportExport.svelte': ['button:focus-visible'],
    'components-v2/controls/WorkspaceSettingsPanel.svelte': ['select:focus-visible'],
    'components-v2/controls/control-button.css': ['.v2-control-button:focus-visible'],
    'components-v2/controls/value-control/HBarRenderer.svelte': ['.vc-track-container:focus-visible'],
    'components-v2/controls/value-control/BipolarRenderer.svelte': ['.vc-track-container:focus-visible'],
    'components-v2/controls/value-control/DiscreteRenderer.svelte': ['.vc-track-container:focus-visible'],
    'components-v2/controls/value-control/DualParamRenderer.svelte': ['.vc-track-container:focus-visible'],
    'components-v2/controls/value-control/KnobRenderer.svelte': ['.vc-knob-container:focus-visible'],
    'components-v2/dialogs/SendReportDialog.svelte': ['.field input:focus-visible'],
    'components-v2/layout/StatusBar.svelte': ['.skin-select:focus-visible'],
    'components-v2/panels/MemoryPanel.svelte': ['.ch-name-input:focus-visible'],
    /* B — literal frames */
    'components-v2/controls/BandSelector.svelte': ['.band-tab:focus-visible'],
    'components-v2/controls/value-control/skins/ProfessionalKnob.svelte': ['.pro-ctr:focus-visible'],
    'components-v2/layout/MobileRadioLayout.svelte': [
      '.m-ls-unkey:focus-visible',
      '.m-receiver-pill:focus-visible',
    ],
    'components-v2/layout/RadioLayout.svelte': ['.standard-tx-settings-trigger:focus-visible'],
    'components-v2/layout/mobile-chip-bar.svelte': ['.m-chip:focus-visible'],
    'components-v2/panels/ModePanel.svelte': ['.mod-input-select:focus-visible'],
    'presentation/languages/fieldline/fieldline.css': [":focus-visible"],
    'presentation/languages/segmentline/segmentline.css': ['.rx-tx-key:focus-visible'],
    'skins/desktop-v2/semantic-controls.css': [':is(button, input, select):focus-visible'],
    /* C — suppression paired with the contract glow; PR 2 drops the
     * redundant `outline: none` */
    'components-v2/vfo/ActiveReceiverToggle.svelte': ['.segment:focus-visible'],
    /* C — the deck model blocks */
    'components-v2/controls/control-button.css-DECK': [
      ".slot-choice, .vfo-freq[role='button']",
      ".slot-choice[data-vfo-active='true']",
    ],
  };

  it('every :focus-visible outline declaration is a contract file or on the PR-2 list', () => {
    const files = walk(FRONTEND_SRC);
    for (const file of files) {
      const rel = file.slice(FRONTEND_SRC.length + 1);
      if (CONTRACT_FILES.has(rel)) continue;
      const raw = readFileSync(file, 'utf-8');
      if (!raw.includes(':focus-visible')) continue;
      const css = stripComments(styleText(rel, raw));
      for (const block of ruleBlocks(css)) {
        if (!block.selector.includes(':focus-visible')) continue;
        if (!/(?<![\w-])outline\s*:/.test(block.body)) continue;
        const deckKey = `${rel}-DECK`;
        const entries = [...(OUTLINE_ALLOW[rel] ?? []), ...(OUTLINE_ALLOW[deckKey] ?? [])];
        const allowed = entries.some((s) => block.selector.includes(s));
        expect(
          allowed,
          `${rel} declares an outline in "${block.selector}" — the MOR-2522 contract ` +
            'paints focus as illumination (light in --v2-focus-ring-color), never a ' +
            'frame. Migrate the rule to the contract tokens and remove it from the ' +
            'PR-2 allow list, or list the file here with a reason.',
        ).toBe(true);
      }
    }
  });

  it('the PR-2 list stays honest: every entry still matches a real block', () => {
    for (const [key, selectors] of Object.entries(OUTLINE_ALLOW)) {
      const rel = key.replace(/-DECK$/, '');
      const css = stripComments(styleText(rel, read(rel)));
      const blocks = ruleBlocks(css).filter(
        (b) => b.selector.includes(':focus-visible') && /(?<![\w-])outline\s*:/.test(b.body),
      );
      for (const s of selectors) {
        expect(
          blocks.some((b) => b.selector.includes(s)),
          `${rel}: allow-list entry "${s}" matches no :focus-visible outline block — ` +
            'remove it (the migration happened; keep the list truthful)',
        ).toBe(true);
      }
    }
  });
});
