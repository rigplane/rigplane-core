/**
 * MOR-2843 guard: a Svelte component's CSS is scoped to that component, and
 * the repo ships no global `.sr-only` rule, so every `.svelte` file whose
 * markup renders the `sr-only` class must define it in its own `<style>`
 * block (directly or through `:global(.sr-only)`). A renderer that uses the
 * class without defining it paints its screen-reader status text on screen —
 * the MOR-2843 "confirmed confirmed" line under the RF/SQL control.
 *
 * This file reads sources as TEXT and never imports them: like the sibling
 * inventory guards, it must fail on the source of an unmounted component.
 */
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const SRC_ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '..',
);

function collectSvelteFiles(dir: string, files: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      collectSvelteFiles(full, files);
    } else if (entry.name.endsWith('.svelte')) {
      files.push(full);
    }
  }
  return files;
}

function isTestFile(relPath: string): boolean {
  return /\.test\./.test(relPath) || relPath.includes('__tests__');
}

/** Markup uses the class: `class="…sr-only…"` or `class:sr-only={…}`. */
function usesSrOnlyClass(markup: string): boolean {
  return /class="[^"]*\bsr-only\b[^"]*"/.test(markup)
    || /class:sr-only\b/.test(markup);
}

/** The style blocks define the rule: `.sr-only {…}` (alone or in a selector
 *  list) or `:global(.sr-only)`. CSS comments are stripped first so a
 *  comment naming the rule cannot satisfy the guard. */
function definesSrOnlyRule(styleSource: string): boolean {
  const stripped = styleSource.replace(/\/\*[\s\S]*?\*\//g, '');
  return /\.sr-only(?=[\s.,{:])/.test(stripped)
    || /:global\(\s*\.sr-only\s*\)/.test(stripped);
}

describe('MOR-2843: sr-only class is defined wherever it is used', () => {
  it('every .svelte using the sr-only class defines it in its own style block', () => {
    const offenders = collectSvelteFiles(SRC_ROOT)
      .map(file => path.relative(SRC_ROOT, file))
      .filter(relPath => !isTestFile(relPath))
      .filter(relPath => {
        const source = readFileSync(path.join(SRC_ROOT, relPath), 'utf8');
        const markup = source
          .replace(/<style[\s\S]*?<\/style>/g, '')
          .replace(/<!--[\s\S]*?-->/g, '');
        if (!usesSrOnlyClass(markup)) return false;
        const styles = (source.match(/<style[^>]*>[\s\S]*?<\/style>/g) ?? [])
          .join('\n');
        return !definesSrOnlyRule(styles);
      });
    expect(
      offenders,
      'these components use the sr-only class without defining it in their own style block '
      + '(Svelte scopes CSS per component and there is no global .sr-only, so their '
      + 'screen-reader status text renders on screen — MOR-2843)',
    ).toEqual([]);
  });
});
