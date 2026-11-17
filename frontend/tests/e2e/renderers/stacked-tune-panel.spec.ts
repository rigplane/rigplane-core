import { test, expect } from '@playwright/test';

// MOR-4463: the 3D view's adjustments open against the viewport, not inside
// the pane that clips its content. Every slider and the reset key must be
// reachable and operable on a desktop window, on a phone and on a screen
// shorter than the list (where the list scrolls inside itself). The witness
// page declares no mobile viewport, so the phone cases are a narrow touch
// window rather than an emulated device, which would lay it out 980 px wide.
const SCREENS = [
  { name: 'desktop', context: { viewport: { width: 1280, height: 800 } } },
  { name: 'phone', context: { hasTouch: true, viewport: { width: 390, height: 700 } } },
  { name: 'short phone', context: { hasTouch: true, viewport: { width: 390, height: 300 } } },
];

for (const screen of SCREENS) {
  test(`all ten 3D adjustments and the reset are reachable on a ${screen.name}`, async ({ browser }) => {
    const context = await browser.newContext(screen.context);
    const page = await context.newPage();
    try {
      await page.addInitScript(() => localStorage.setItem('rigplane-spectrum-stacked-trace', '1'));
      await page.goto('/fixtures/spectrum-witness.html');
      await page.getByTestId('stacked-trace-tune').click({ timeout: 4000 });
      const panel = page.locator('.tune-panel');
      await expect(panel).toBeVisible();
      const box = (await panel.boundingBox())!;
      const view = page.viewportSize()!;
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.y).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(view.width);
      expect(box.y + box.height).toBeLessThanOrEqual(view.height);

      const sliders = panel.locator('input[type="range"]');
      await expect(sliders).toHaveCount(10);
      /** The element is the topmost one at its own centre: nothing clips or covers it. */
      const onTop = (element: Element) => {
        const rect = element.getBoundingClientRect();
        const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
        return hit === element || element.contains(hit);
      };
      for (let index = 0; index < 10; index++) {
        const slider = sliders.nth(index);
        await slider.scrollIntoViewIfNeeded({ timeout: 4000 });
        expect(await slider.evaluate(onTop)).toBe(true);
        const before = await slider.inputValue();
        await slider.focus({ timeout: 4000 });
        await page.keyboard.press((await slider.evaluate((input: HTMLInputElement) => input.value === input.max))
          ? 'ArrowLeft' : 'ArrowRight');
        expect(await slider.inputValue()).not.toBe(before);
      }
      const changed = await page.evaluate(() => localStorage.getItem('rigplane-spectrum-stacked-trace-settings'));
      expect(changed).not.toBeNull();

      const reset = page.getByTestId('stacked-trace-tune-reset');
      await reset.scrollIntoViewIfNeeded({ timeout: 4000 });
      expect(await reset.evaluate(onTop)).toBe(true);
      if (screen.context.hasTouch) await reset.tap({ timeout: 4000 }); else await reset.click({ timeout: 4000 });
      const defaults = await page.evaluate(async () =>
        (await import('/src/lib/renderers/stacked-trace-geometry.ts' as string)).defaultStackedTraceSettings);
      // A slider shows the default rounded to its own step.
      const shown = await sliders.evaluateAll((inputs) => inputs.map((input) => {
        const slider = input as HTMLInputElement;
        return { key: slider.dataset.setting!, value: Number(slider.value), step: Number(slider.step) };
      }));
      for (const { key, value, step } of shown) expect(Math.abs(value - defaults[key])).toBeLessThanOrEqual(step);
      expect(await page.evaluate(async () =>
        (await import('/src/lib/stores/stacked-trace.svelte.ts' as string)).stackedTraceSettings())).toEqual(defaults);
    } finally {
      await context.close();
    }
  });
}
