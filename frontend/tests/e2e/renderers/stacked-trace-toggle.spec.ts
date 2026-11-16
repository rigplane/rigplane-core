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
  await expect(page.locator('.db-scale .tick').first()).toBeHidden();
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

// The panel's own full-height marks read a flat trace. Over the scene they
// would stand upright against rows drawn in perspective, so the 3D view hides
// them (it draws the marker and the hatch itself) and keeps the passband
// handle to the front rows, where it is exact.
test('the upright marks of the flat trace give way to the scene in the 3D view and return with the flat one', async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await page.goto('/fixtures/spectrum-witness.html');
  const area = page.locator('.spectrum-area').first();
  const box = (await area.boundingBox())!;
  // Stand-ins carrying the panel's class names, so the test does not depend on the witness state.
  await area.evaluate((element) => {
    for (const name of ['tune-line', 'scope-hatch', 'passband-resize-zone']) {
      const mark = document.createElement('div');
      mark.className = name; mark.dataset.witness = name;
      mark.style.cssText = 'position:absolute;left:10%;width:2px;bottom:0;pointer-events:none';
      if (name !== 'passband-resize-zone') mark.style.top = '0';
      element.append(mark);
    }
  });
  const witness = (name: string) => page.locator(`[data-witness="${name}"]`);
  const handleTop = () => witness('passband-resize-zone').evaluate(element => Number.parseFloat(getComputedStyle(element).top));
  await expect(witness('tune-line')).toBeVisible();
  await expect(witness('scope-hatch')).toBeVisible();
  const flatTop = await handleTop();

  await page.getByTestId('scope-3d-toggle').first().click();
  await expect(page.getByTestId('stacked-trace')).toBeVisible();
  await expect(witness('tune-line')).toBeHidden();
  await expect(witness('scope-hatch')).toBeHidden();
  expect(await handleTop()).toBeCloseTo(box.height * 0.6, 0);
  // The stand-in has no top of its own: only the 3D view's rule puts it there.
  expect(Math.abs(flatTop - box.height * 0.6)).toBeGreaterThan(box.height * 0.3);

  await page.getByTestId('scope-3d-toggle').first().click();
  await expect(page.locator(flat)).toHaveCount(1);
  await expect(witness('tune-line')).toBeVisible();
  await expect(witness('scope-hatch')).toBeVisible();
  expect(await handleTop()).toBe(flatTop);
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
