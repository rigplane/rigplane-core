import { readFileSync, readdirSync, statSync } from 'node:fs';
import { extname, join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

const FRONTEND_SRC = join(__dirname, '..');
const RENDERER_3569 = new Set([
  'components-v2/controls/value-control/BipolarRenderer.svelte',
  'components-v2/controls/value-control/DiscreteRenderer.svelte',
  'components-v2/controls/value-control/DualParamRenderer.svelte',
  'components-v2/controls/value-control/HBarRenderer.svelte',
  'components-v2/controls/value-control/KnobRenderer.svelte',
]);
const RANGE_3572 = new Set(['app.css', 'skins/desktop-v2/semantic-controls.css']);

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

type Rule = { selector: string; body: string; media: readonly string[] };

function rules(css: string, media: readonly string[] = []): Rule[] {
  const source = css.replace(/\/\*[\s\S]*?\*\//g, '');
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
