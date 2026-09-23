/**
 * MOR-2522 (owner rulings 2026-09-22): "remove every focus frame and show
 * focus in NO way", keeping the control illumination without a frame.
 *
 * Kept illumination owners, and only these:
 *   - #3569 value-control renderers (Bipolar/Discrete/DualParam/HBar/Knob):
 *     `outline: none` + `--vc-focus-ring-shadow` on the doubled container
 *     selector, plus the wheel-armed twin in value-control.css;
 *   - #3572 native range sliders: app.css's shared
 *     `input[type='range'][type='range']:focus-visible` rule and the
 *     desktop-v2 composing variant in skins/desktop-v2/semantic-controls.css,
 *     including its `--range-track-chrome` layer contract;
 *   - forced-colors system outlines (e.g. Highlight) inside
 *     `@media (forced-colors: active)`.
 *
 * The remaining describes are the round-2 restore: the protection for the
 * KEPT illumination that the guard rewrite had deleted. They pin the
 * per-skin WCAG 1.4.11 contrast matrix for the ring colour (nord-light and
 * solarized-light carry explicit overrides), the value-control no-frame /
 * no-shift / token-wiring contracts, the studioline ring-colour pins, and
 * the native-range cascade/composition pins.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { extname, join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

const FRONTEND_SRC = join(__dirname, '..');
const THEME_DIR = join(FRONTEND_SRC, 'components-v2/theme');
const RENDERER_3569 = new Set([
  'components-v2/controls/value-control/BipolarRenderer.svelte',
  'components-v2/controls/value-control/DiscreteRenderer.svelte',
  'components-v2/controls/value-control/DualParamRenderer.svelte',
  'components-v2/controls/value-control/HBarRenderer.svelte',
  'components-v2/controls/value-control/KnobRenderer.svelte',
]);

function read(relPath: string): string {
  return readFileSync(join(FRONTEND_SRC, relPath), 'utf-8');
}

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules' || entry.startsWith('.') || entry === '__tests__' || entry === 'tests') continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else if ((extname(entry) === '.css' || extname(entry) === '.svelte') && !/\.(?:test|spec)\./.test(entry)) out.push(full);
  }
  return out;
}

function styles(path: string): string {
  const source = readFileSync(path, 'utf8');
  return extname(path) === '.svelte'
    ? [...source.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((match) => match[1]).join('\n')
    : source;
}

function styleText(path: string, text: string): string {
  if (extname(path) !== '.svelte') return text;
  return [...text.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((m) => m[1]).join('\n');
}

function stripComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, '');
}

type Rule = { selector: string; body: string; media: readonly string[] };

function rules(css: string, media: readonly string[] = []): Rule[] {
  const source = stripComments(css);
  const out: Rule[] = [];
  let cursor = 0;
  while (cursor < source.length) {
    const open = source.indexOf('{', cursor);
    if (open < 0) break;
    const header = source.slice(cursor, open).trim();
    let depth = 1;
    let close = open + 1;
    while (close < source.length && depth > 0) {
      if (source[close] === '{') depth += 1;
      else if (source[close] === '}') depth -= 1;
      close += 1;
    }
    const body = source.slice(open + 1, close - 1);
    if (header.startsWith('@media')) out.push(...rules(body, [...media, header]));
    else if (!header.startsWith('@')) out.push({ selector: header, body, media });
    cursor = close;
  }
  return out;
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

function focusBranches(selector: string): string[] {
  return selector.split(',').map((branch) => branch.trim()).filter((branch) => /:focus(?:-visible|-within)?/.test(branch));
}

function allowedFocusShadow(path: string, branch: string): boolean {
  if (!branch.endsWith(':focus-visible')) return false;
  if (RENDERER_3569.has(path)) return /\.vc-(?:track|knob)-container\.vc-(?:track|knob)-container:focus-visible$/.test(branch);
  if (path === 'app.css') return /input\[type=['"]range['"]\]\[type=['"]range['"]\]:focus-visible$/.test(branch);
  if (path === 'skins/desktop-v2/semantic-controls.css') return /input\[type=['"]range['"]\]:focus-visible$/.test(branch);
  return false;
}

function isRingShadow(value: string): boolean {
  return /var\(--(?:v2|vc)-focus-ring(?:-shadow)?\)/.test(value)
    || /(?:^|,)\s*(?:inset\s+)?0\s+0\s+0\s+[^,;]+/.test(value);
}

/* WCAG relative-luminance contrast from hex literals (the per-skin matrix
 * and the MOR-2522 studioline pins compute, never assume, the ratios). */
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
 *  classes, attributes and pseudo-classes → b; element types → c. String
 *  values and parenthesised arguments are dropped first. A type is counted
 *  once per compound, as a leading identifier — the native-range focus
 *  variant's win over the desktop-v2 track chrome can live in the c column. */
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
 *  chrome's `var(--vc-hw-channel-shadow-1, #000f)` fallbacks contain commas,
 *  so a naive `value.split(',')` would shred every layer that carries one.
 *  The compose pin uses it to verify the layers of the ONE
 *  --range-track-chrome declaration and both rules' references to it. */
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

/* ── the no-focus-frame sweep ────────────────────────────────────────────── */

describe('MOR-2522: no focus frames', () => {
  it('allows only app suppression, #3569/#3572 illumination, or forced-colors system outlines', () => {
    const app = styles(join(FRONTEND_SRC, 'app.css'));
    expect(app).toMatch(/:focus,\s*:focus-visible\s*\{\s*outline:\s*none;/);

    const offenders: string[] = [];
    for (const file of walk(FRONTEND_SRC)) {
      const path = relative(FRONTEND_SRC, file);
      for (const rule of rules(styles(file))) {
        const branches = focusBranches(rule.selector);
        if (branches.length === 0) continue;
        const forcedColors = rule.media.some((header) => /forced-colors\s*:\s*active/.test(header));
        const outline = rule.body.match(/(?:^|[;\n])\s*outline\s*:\s*([^;]+)/);
        if (outline && outline[1].trim() !== 'none' && !forcedColors) {
          offenders.push(`${path}: ${rule.selector} declares outline ${outline[1].trim()}`);
        }
        if (/\boutline-offset\s*:/.test(rule.body) && !forcedColors) {
          offenders.push(`${path}: ${rule.selector} declares outline-offset`);
        }
        // No kept owner paints a border or a background under a focus
        // selector — the renderer, app.css and semantic-controls focus rules
        // declare outline/box-shadow only — so this check carries no
        // exemption: under a focus selector it rejects exactly `border`,
        // `border-color`, `background` and `background-color`, whatever file
        // they land in. `border-radius`/`border-*-width` are geometry, not
        // paint, and do not match.
        const paint = rule.body.match(/(?:^|[;\n])\s*(border(?:-color)?|background(?:-color)?)\s*:/);
        if (paint) {
          offenders.push(`${path}: ${rule.selector} declares ${paint[1]} under a focus selector`);
        }

        const allBranchesFocus = branches.length === rule.selector.split(',').length;
        const shadow = rule.body.match(/\bbox-shadow\s*:\s*([^;]+)/);
        if (shadow && allBranchesFocus && isRingShadow(shadow[1])
          && !branches.every((branch) => allowedFocusShadow(path, branch))) {
          offenders.push(`${path}: ${rule.selector} declares a focus ring shadow`);
        }
      }
    }
    expect(offenders).toEqual([]);

    const readers = walk(FRONTEND_SRC)
      .map((file) => ({ path: relative(FRONTEND_SRC, file), text: styles(file) }))
      .filter(({ text }) => /var\(--v2-focus-ring-shadow\)|var\(--vc-focus-ring-shadow\)/.test(text))
      .map(({ path }) => path)
      .sort();
    expect(readers).toEqual([
      'app.css',
      'components-v2/controls/value-control/BipolarRenderer.svelte',
      'components-v2/controls/value-control/DiscreteRenderer.svelte',
      'components-v2/controls/value-control/DualParamRenderer.svelte',
      'components-v2/controls/value-control/HBarRenderer.svelte',
      'components-v2/controls/value-control/KnobRenderer.svelte',
      'components-v2/controls/value-control/value-control.css',
      'skins/desktop-v2/semantic-controls.css',
    ]);
  });
});

/* ── WCAG 1.4.11 contrast, recomputed per skin ───────────────────────────── */

describe('MOR-1232: the focus ring clears WCAG 1.4.11 (3:1) on every skin', () => {
  // Surfaces a ring can be drawn against. The spread ring hugs the control,
  // so the relevant neighbour is the container fill.
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

  /* MOR-1254: the five value-control renderers colour their focus
   * illumination with `--vc-focus-ring` (value-control.css), which resolves
   * through this same `--v2-focus-ring-color` knob. Extending this loop —
   * rather than writing a parallel matrix — means a future change to either
   * the knob or value-control.css's wiring is caught by the same per-skin
   * recompute. */
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
            'illumination of all five value-control renderers (Knob/HBar/Discrete/Bipolar/DualParam).',
        ).toBeGreaterThanOrEqual(MIN_RATIO);
      }
      expect(checked.length, `${id}: no surfaces resolved — the check was vacuous`).toBeGreaterThan(
        2,
      );
    });
  }
});

/* ── value-control focus lights the control, never a frame ───────────────── */

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
    // recomputed per skin by the contrast matrix above.
    expect(css).toMatch(/--vc-focus-ring:\s*var\(--v2-focus-ring-color\)\s*;/);
    expect(css).not.toMatch(/--vc-focus-ring:\s*var\(--v2-accent-cyan\)/);
    // MOR-2522: the painted ring composes that same colour and the shared
    // width knob, so the per-skin matrix governs what reaches the screen.
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
    expect(/(?<![\w-])outline:\s*(none|0)\b/.test(armed!.body)).toBe(true);
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
   * selector appears twice — the blocks merge in order, because the cascade
   * makes the later block's declarations win. */
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
    // The per-skin matrix resolves --vc-focus-ring from this file with a
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

/* ── native range sliders share one lighting rule ────────────────────────── */

describe('MOR-2522: native range sliders light on keyboard focus instead of drawing a frame', () => {
  // Six call sites — semantic/FilterSurface.svelte (WIDTH / IF SHIFT, the
  // stand defect), DspSurface.svelte, CwKeyerSurface.svelte,
  // RitXitScanSurface.svelte, components-v2/panels/AudioRoutingControl.svelte,
  // CwPanel.svelte — justify ONE shared rule, not six copies. Its home is
  // app.css, the only stylesheet loaded unconditionally in every context that
  // mounts them; value-control.css is not (nothing in those six files imports
  // it), so the rule consumes --vc-focus-ring-shadow only as a preference over
  // its theme twin — and the chain ends there: the two-level
  // `var(--vc-focus-ring-shadow, var(--v2-focus-ring-shadow))` pinned below,
  // with nothing declared past the theme twin.
  //
  // One collision the shared rule cannot win: on the desktop-v2/sdr-test
  // skins semantic-controls.css's track chrome is (0,3,2) — box-shadow does
  // not merge across rules, so the focused slider would show track-only with
  // no ring at all. The illumination there is carried by a :focus-visible
  // variant of the track rule's own selector, pinned below from the real
  // stylesheet.
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
    // declared token (the per-skin contrast matrix above governs both
    // levels). The chain is exactly these two levels — nothing is declared
    // past the theme twin.
    expect(declarations).toContain(
      'box-shadow: var(--vc-focus-ring-shadow, var(--v2-focus-ring-shadow))',
    );
    // No-shift pin, the same declared-property-set contract the renderer
    // describe holds the five renderers to: beyond outline/box-shadow this
    // rule cannot move, resize or reshape the slider.
    const properties = declarations.map((d) => d.split(':')[0].trim());
    expect(properties).toEqual(properties.filter((p) => RANGE_ONLY.test(p)));
  });

  it('app.css is the unconditional shared load in both the app and the harness', () => {
    // The premise of "one shared home": both entry points import app.css
    // statically.
    expect(read('App.svelte')).toMatch(/import '\.\/app\.css';/);
    expect(read('../fixtures/main.ts')).toMatch(/import '\.\.\/src\/app\.css';/);
  });

  // The cascade pins. The track chrome rule and its :focus-visible variant
  // are both located in the REAL stylesheet, and the ranking is recomputed
  // from their live selectors: the pin fails if the track rule gains
  // specificity (a tie is a loss — the chrome would eat the ring again) or
  // if the focus variant loses it.
  const skinCss = stripComments(read(SKIN_CSS));
  const skinBlocks = ruleBlocks(skinCss);
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
    // ONE owner of the chrome, file-wide: EVERY --range-track-chrome
    // declaration in semantic-controls.css is collected (comments stripped
    // above), not just the first match in the track rule's body — a second
    // declaration later in the same rule (a dropped or reordered stack) would
    // override the first in the browser while a first-match read stays green,
    // so uniqueness is asserted before the sole value is validated. The layer
    // check is then the discriminating check on the stack itself — a dropped,
    // reordered or retyped layer fails here, at the only place the literals
    // live.
    const chromeDeclarations = [...skinCss.matchAll(/--range-track-chrome:\s*([^;]*)/g)].map(
      (m) => m[1].trim(),
    );
    expect(
      chromeDeclarations.length,
      'expected exactly ONE --range-track-chrome declaration in semantic-controls.css',
    ).toBe(1);
    expect(shadowLayers(chromeDeclarations[0])).toEqual([
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
    // Same shape as the renderer describe's ring-only pin: the shared rule
    // in app.css is the ONLY focus treatment these files get — a per-file
    // copy is the six-copies defect this ticket refuses.
    for (const file of NATIVE_RANGE_SURFACES) {
      expect(stripComments(styleText(file, read(file))), file).not.toMatch(/outline\s*:/);
    }
  });
});
