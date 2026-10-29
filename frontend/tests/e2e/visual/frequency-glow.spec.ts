/** MOR-3860: computed glow checks at fractional DPRs, without pixel baselines. */
import { test, expect, type Page } from '@playwright/test';

/** Split a `text-shadow` list on top-level commas (never inside rgba()/etc). */
function splitShadows(value: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let current = '';
  for (const char of value) {
    if (char === '(') depth += 1;
    if (char === ')') depth = Math.max(0, depth - 1);
    if (char === ',' && depth === 0) {
      parts.push(current);
      current = '';
    } else {
      current += char;
    }
  }
  if (current.trim() !== '') parts.push(current);
  return parts;
}

/** Blur radii (px) of every shadow in a computed `text-shadow` value. */
function blurRadii(value: string): number[] {
  if (value === '' || value === 'none') return [];
  return splitShadows(value).map((shadow) => {
    const lengths = [...shadow.matchAll(/(-?\d+(?:\.\d+)?)px/g)].map((match) => Number(match[1]));
    // Computed text-shadow lengths are offset-x, offset-y, and blur.
    expect(lengths.length, `expected a parseable shadow, got: ${shadow}`).toBeGreaterThanOrEqual(3);
    return lengths[2]!;
  });
}

const activeMain = '.vfo-tile.is-active[data-vfo-receiver="MAIN"] [data-vfo-freq]';

async function activeReadoutShadows(page: Page): Promise<string[]> {
  const outer = page.locator(activeMain).first();
  await expect(outer).toBeAttached();
  const inner = outer.locator('.freq').first();
  const nodes = [outer];
  if (await inner.count()) {
    await expect(inner).toBeVisible();
    nodes.push(inner);
  } else {
    await expect(outer).toBeVisible();
    await expect(outer).toContainText('14.250.000');
  }
  return Promise.all(nodes.map((node) => node.evaluate((el) => getComputedStyle(el).textShadow)));
}

for (const deviceScaleFactor of [1.25, 1.5]) {
  test.describe(`frequency glow at DPR ${deviceScaleFactor} (MOR-3860)`, () => {
    test.use({ deviceScaleFactor });

    for (const theme of ['crt-green', 'nixie-tube']) {
      for (const attribute of ['data-theme', 'data-vfo-theme']) {
        test(`desktop: ${theme} via ${attribute} preserves strong glow`, async ({ page }) => {
          await page.goto('/fixtures/skin-witness.html?skin=desktop-v2&fixture=topology-2-main-sub&theme=v2&language=studioline');
          await page.waitForSelector('body[data-harness-ready="true"]');
          await page.evaluate(({ attribute, theme }) => {
            document.documentElement.removeAttribute('data-theme');
            document.documentElement.removeAttribute('data-vfo-theme');
            document.documentElement.setAttribute(attribute, theme);
          }, { attribute, theme });
          expect(await page.evaluate(() => window.devicePixelRatio)).toBe(deviceScaleFactor);
          for (const shadow of await activeReadoutShadows(page)) {
            expect(Math.max(...blurRadii(shadow)), `expected strong glow: ${shadow}`).toBeGreaterThan(4);
          }
        });
      }
    }

    for (const language of ['fieldline', 'segmentline', 'studioline']) {
      test(`${language}: preserves no-glow language or contrast settings`, async ({ page }) => {
        if (language === 'studioline') await page.emulateMedia({ contrast: 'more' });
        await page.goto(`/fixtures/skin-witness.html?skin=desktop-v2&fixture=topology-2-main-sub&theme=v2&language=${language}`);
        await page.waitForSelector('body[data-harness-ready="true"]');
        for (const shadow of await activeReadoutShadows(page)) expect(shadow).toBe('none');
      });
    }

    for (const mode of ['desktop', 'interactive', 'passive', 'unknown']) {
      test(`${mode}: subtle glow comes only from the token`, async ({ page }) => {
        await page.goto(mode === 'desktop'
          ? '/fixtures/skin-witness.html?skin=desktop-v2&fixture=topology-2-main-sub&theme=v2&language=studioline'
          : `/fixtures/frequency-glow.html?mode=${mode}`);
        await page.waitForSelector('body[data-harness-ready="true"]');
        expect(await page.evaluate(() => window.devicePixelRatio)).toBe(deviceScaleFactor);
        for (const value of await activeReadoutShadows(page)) {
          const radii = blurRadii(value);
          expect(radii.length, 'expected a visible glow').toBeGreaterThan(0);
          for (const blur of radii) {
            expect(blur, `text-shadow must stay subtle: ${value}`).toBeLessThanOrEqual(4);
          }
          expect(radii, 'expected a single shadow').toHaveLength(1);
        }

        await page.evaluate(() => {
          document.documentElement.style.setProperty('--v2-vfo-main-freq-glow', '0 0 1px rgba(1, 2, 3, 0.4)');
        });
        for (const shadow of await activeReadoutShadows(page)) {
          expect(shadow).toBe('rgba(1, 2, 3, 0.4) 0px 0px 1px');
        }
      });
    }
  });
}
