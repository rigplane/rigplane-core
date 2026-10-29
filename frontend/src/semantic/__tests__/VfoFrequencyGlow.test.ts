import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';

const source = (path: string) => readFileSync(path, 'utf8');

function tokenValue(css: string, name: string): string {
  const match = new RegExp(`${name}:\\s*([^;]+);`).exec(css);
  expect(match, `expected ${name} to be defined`).not.toBeNull();
  return match![1];
}

function blurRadii(value: string): number[] {
  // Remove function arguments before splitting the shadow list.
  return value.replace(/rgba?\([^)]*\)/g, '').split(',').map((shadow) => {
    const lengths = shadow.trim().split(/\s+/).map(Number.parseFloat);
    expect(lengths).toHaveLength(3);
    expect(lengths.every(Number.isFinite)).toBe(true);
    return lengths[2]!;
  });
}

describe('MOR-3860: token-driven frequency glow', () => {
  it('VfoSurface uses the main token for active fallback and unknown readouts', () => {
    const surface = source('src/semantic/VfoSurface.svelte');
    const activeRule = surface.match(/\.receiver-instrument\s+\.is-active\s+\.vfo-freq\s*\{([^}]+)\}/)![1];
    expect(activeRule).toContain('text-shadow: var(--v2-vfo-main-freq-glow)');
    for (const [, , rule] of surface.matchAll(/([^{}]*\.vfo-freq[^{}]*)\{([^}]+)\}/g)) {
      expect(rule).not.toMatch(/text-shadow\s*:[^;]*\d+(?:\.\d+)?px/);
    }
    expect(surface).not.toMatch(/rgba\(124,\s*252,\s*229/);
    expect(surface).toMatch(/\.display-unknown[^{}]*\{[^}]*text-shadow:\s*inherit/);
  });

  it('the default desktop panel uses the receiver token', () => {
    const panel = source('src/components-v2/vfo/VfoPanel.svelte');
    expect(panel.includes("'--receiver-frequency-glow': `var(--v2-vfo-${receiver}-freq-glow)`")).toBe(true);
    expect(panel).toContain('--vfo-frequency-glow: var(--dl-vfo-frequency-glow, var(--v2-vfo-glow-override, var(--receiver-frequency-glow)));');
    expect(source('src/presentation/languages/studioline/studioline.css')).not.toMatch(/--dl-vfo-frequency-glow\s*:/);
    for (const language of ['fieldline', 'segmentline']) {
      expect(source(`src/presentation/languages/${language}/${language}.css`)).toMatch(/--dl-vfo-frequency-glow:\s*none\s*;/);
    }
  });

  it('the default tokens have one subtle shadow (blur <= 4px)', () => {
    const tokens = source('src/components-v2/theme/tokens.css');
    for (const name of ['--v2-vfo-main-freq-glow', '--v2-vfo-sub-freq-glow']) {
      const radii = blurRadii(tokenValue(tokens, name));
      expect(radii).toHaveLength(1);
      expect(radii[0]).toBeLessThanOrEqual(4);
    }
  });

  it('crt-green and nixie-tube keep their deliberate strong glows', () => {
    for (const directory of ['themes', 'vfo-themes']) {
      for (const theme of ['crt-green', 'nixie-tube']) {
        const css = source(`src/components-v2/theme/${directory}/${theme}.css`);
        expect(Math.max(...blurRadii(tokenValue(css, '--v2-vfo-main-freq-glow')))).toBeGreaterThan(4);
      }
    }
  });
});
