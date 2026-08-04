import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

// MOR-1233: nothing in the frontend responded to the OS-level
// `prefers-contrast` / `forced-colors` accessibility signals before this —
// only a manually-selectable "High Contrast" theme existed (see
// ../themes/high-contrast.css), which a user has to opt into explicitly.
// This pins the minimal automatic token-layer strategy: the v2 token root
// (this file, not styles/tokens.css which owns --focus-ring / MOR-1232)
// gains `@media (prefers-contrast: more)` and `@media (forced-colors:
// active)` blocks that bump border/text separation, scoped to
// `:root:not([data-theme])` so an explicit manual theme choice always wins
// over the automatic OS signal.

const source = readFileSync(
  resolve(process.cwd(), 'src/components-v2/theme/tokens.css'),
  'utf8',
);

describe('components-v2/theme/tokens.css — prefers-contrast / forced-colors (MOR-1233)', () => {
  it('defines an automatic prefers-contrast: more block', () => {
    expect(source).toMatch(/@media\s*\(prefers-contrast:\s*more\)/);
  });

  it('defines an automatic forced-colors: active block', () => {
    expect(source).toMatch(/@media\s*\(forced-colors:\s*active\)/);
  });

  it('scopes both blocks to :root:not([data-theme]) so an explicit theme choice wins', () => {
    const contrastBlock = source.match(
      /@media\s*\(prefers-contrast:\s*more\)\s*\{([\s\S]*?)\n\}/,
    );
    const forcedColorsBlock = source.match(
      /@media\s*\(forced-colors:\s*active\)\s*\{([\s\S]*?)\n\}/,
    );
    expect(contrastBlock).not.toBeNull();
    expect(forcedColorsBlock).not.toBeNull();
    expect(contrastBlock![1]).toMatch(/:root:not\(\[data-theme\]\)/);
    expect(forcedColorsBlock![1]).toMatch(/:root:not\(\[data-theme\]\)/);
  });

  it('bumps border tokens under both blocks (the concrete accessibility win)', () => {
    const contrastBlock = source.match(
      /@media\s*\(prefers-contrast:\s*more\)\s*\{[\s\S]*?\n\}/,
    )![0];
    const forcedColorsBlock = source.match(
      /@media\s*\(forced-colors:\s*active\)\s*\{[\s\S]*?\n\}/,
    )![0];
    expect(contrastBlock).toMatch(/--v2-border:/);
    expect(forcedColorsBlock).toMatch(/--v2-border:/);
  });

  it('does not declare --focus-ring (owned by MOR-1232 in styles/tokens.css)', () => {
    // Matches an actual custom-property *declaration*, not a prose mention
    // of the name in a comment (this file's MOR-1233 block references it).
    expect(source).not.toMatch(/--focus-ring\s*:/);
  });
});
