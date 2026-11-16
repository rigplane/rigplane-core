import { test, expect } from '@playwright/test';
import en from '../../../src/lib/i18n/locales/en-US.json' with { type: 'json' };

// MOR-4463: the real SpectrumPanel with its real toolbar. The flat spectrum
// is the default; the 3D key switches the spectrum pane to the stacked-trace
// view and back, and the choice is kept per browser.
const flat = '.spectrum-container > canvas';

// WebKit reports a request that a navigation cut off as a page error, and
// the panel's band-plan overlay fetches on every view change. Let the
// network settle before reloading, so the strict "no page errors" check
// below only ever sees errors of the page itself.
async function reloadQuietly(page: import('@playwright/test').Page): Promise<void> {
  await page.waitForLoadState('networkidle');
  await page.reload();
}

test('flat by default; the 3D key switches the pane, survives a reload and switches back', async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await page.goto('/fixtures/spectrum-witness.html');
  const key = page.getByTestId('scope-3d-toggle').first();
  const stacked = page.getByTestId('stacked-trace');
  await expect(page.locator(flat)).toHaveCount(1);
  await expect(stacked).toHaveCount(0);
  await expect(key).toHaveText('3D');
  await expect(key).toHaveAttribute('aria-pressed', 'false');
  await expect(key).toHaveAttribute('title', en['core.scope.stackedTrace.title']);
  await expect(page.locator('.db-scale .tick').first()).toBeVisible();
  // AVG and PEAK shape the flat trace only: shown with it, hidden in 3D.
  const avg = page.getByRole('button', { name: 'AVG', exact: true }).first();
  const peak = page.getByRole('button', { name: 'PEAK', exact: true }).first();
  await expect(avg).toBeVisible();
  await expect(peak).toBeVisible();

  await key.click();
  await expect(stacked).toBeVisible();
  await expect(page.locator(flat)).toHaveCount(0);
  await expect(key).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('button', { hasText: /^AVG$/ }).first()).toBeHidden();
  await expect(page.locator('button', { hasText: /^PEAK$/ }).first()).toBeHidden();
  await expect(page.locator('button', { hasText: /^BANDS$/ }).first()).toBeVisible();
  // The frame the panel already holds reaches the new view.
  await expect.poll(async () => Number(await stacked.getAttribute('data-history-rows'))).toBeGreaterThan(0);
  // The level scale stays beside the pane in the 3D view.
  await expect(page.locator('.db-scale .tick').first()).toBeVisible();
  // The pane keeps its place: same box as the flat canvas had, waterfall untouched.
  const pane = await page.locator('.spectrum-area').boundingBox();
  const view = await stacked.boundingBox();
  expect(view).toEqual(pane);
  await expect(page.locator('.waterfall-history canvas')).toHaveCount(1);

  await reloadQuietly(page);
  await expect(page.getByTestId('stacked-trace')).toBeVisible();
  await expect(page.getByTestId('scope-3d-toggle').first()).toHaveAttribute('aria-pressed', 'true');

  await page.getByTestId('scope-3d-toggle').first().click();
  await expect(page.getByTestId('stacked-trace')).toHaveCount(0);
  await expect(page.locator(flat)).toHaveCount(1);
  await expect(page.getByRole('button', { name: 'AVG', exact: true }).first()).toBeVisible();
  await expect(page.getByRole('button', { name: 'PEAK', exact: true }).first()).toBeVisible();
  await reloadQuietly(page);
  await expect(page.locator(flat)).toHaveCount(1);
  await expect(page.getByTestId('stacked-trace')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('the 3D view can be adjusted on its own key; the values are kept and return to the defaults', async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await page.goto('/fixtures/spectrum-witness.html');
  await expect(page.getByTestId('stacked-trace-tune')).toHaveCount(0);
  await page.getByTestId('scope-3d-toggle').first().click();
  const key = page.getByTestId('stacked-trace-tune');
  await expect(key).toHaveAttribute('title', en['core.scope.stackedTrace.tune.title']);
  await key.click();
  const panel = page.getByRole('dialog', { name: en['core.scope.stackedTrace.tune.title'] });
  await expect(panel.locator('input[type="range"]')).toHaveCount(10);
  await expect(panel).toContainText(en['core.scope.stackedTrace.tune.haze']);
  const haze = panel.locator('input[data-setting="haze"]');
  const before = await haze.inputValue();
  // The scene repaints with the new value: the pane's pixels change.
  const scene = page.locator('[data-stacked-trace] canvas').first();
  const paintedBefore = await scene.screenshot();
  await haze.fill('0.9');
  await panel.locator('input[data-setting="recede"]').fill('2');
  await expect.poll(async () => (await scene.screenshot()).equals(paintedBefore)).toBe(false);
  // A press on the adjustments is not a click on the spectrum.
  expect(await page.evaluate(() => window.__spectrumWitness.commands)).toEqual([]);

  await reloadQuietly(page);
  await page.getByTestId('stacked-trace-tune').click();
  const restored = page.getByRole('dialog').locator('input[data-setting="haze"]');
  await expect(restored).toHaveValue('0.9');
  await page.getByTestId('stacked-trace-tune-reset').click();
  await expect(restored).toHaveValue(before);
  await reloadQuietly(page);
  await page.getByTestId('stacked-trace-tune').click();
  await expect(page.getByRole('dialog').locator('input[data-setting="haze"]')).toHaveValue(before);
  await page.getByTestId('scope-3d-toggle').first().click();
  expect(errors).toEqual([]);
});

test('a browser that refuses the 3D view keeps the flat spectrum and disables the key with the reason', async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    localStorage.setItem('rigplane-spectrum-stacked-trace', '1');
    const get = HTMLCanvasElement.prototype.getContext;
    // Only the stacked view's own canvas is refused; the waterfall keeps WebGL2.
    HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, kind: string, ...args: unknown[]) {
      return kind === 'webgl2' && this.closest('[data-stacked-trace]') ? null : Reflect.apply(get, this, [kind, ...args]);
    } as typeof get;
  });
  await page.goto('/fixtures/spectrum-witness.html');
  const key = page.getByTestId('scope-3d-toggle').first();
  await expect(page.locator(flat)).toHaveCount(1);
  await expect(page.getByTestId('stacked-trace')).toHaveCount(0);
  await expect(key).toBeDisabled();
  await expect(key).toHaveAttribute('aria-pressed', 'false');
  await expect(key).toHaveAttribute('title', en['core.scope.stackedTrace.unavailable']);
  expect(errors).toEqual([]);
});
