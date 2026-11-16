import { test, expect } from '@playwright/test';

// MOR-4463: the real SpectrumPanel with its real toolbar. The flat spectrum
// is the default; the 3D key switches the spectrum pane to the stacked-trace
// view and back, and the choice is kept per browser.
const flat = '.spectrum-container > canvas';

test('flat by default; the 3D key switches the pane, survives a reload and switches back', async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await page.goto('/fixtures/spectrum-witness.html');
  const key = page.getByTestId('scope-3d-toggle').first();
  const stacked = page.getByTestId('stacked-trace');
  await expect(page.locator(flat)).toHaveCount(1);
  await expect(stacked).toHaveCount(0);
  await expect(key).toHaveText('3D');
  await expect(key).toHaveAttribute('aria-pressed', 'false');
  await expect(key).toHaveAttribute('title', '3D view: recent spectrum history in depth');
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

  await page.reload();
  await expect(page.getByTestId('stacked-trace')).toBeVisible();
  await expect(page.getByTestId('scope-3d-toggle').first()).toHaveAttribute('aria-pressed', 'true');

  await page.getByTestId('scope-3d-toggle').first().click();
  await expect(page.getByTestId('stacked-trace')).toHaveCount(0);
  await expect(page.locator(flat)).toHaveCount(1);
  await expect(page.getByRole('button', { name: 'AVG', exact: true }).first()).toBeVisible();
  await expect(page.getByRole('button', { name: 'PEAK', exact: true }).first()).toBeVisible();
  await page.reload();
  await expect(page.locator(flat)).toHaveCount(1);
  await expect(page.getByTestId('stacked-trace')).toHaveCount(0);
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
  await expect(key).toHaveAttribute('title', '3D view is not available: this browser provides no WebGL2');
  expect(errors).toEqual([]);
});
