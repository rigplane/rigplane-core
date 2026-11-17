import { test, expect } from '@playwright/test';

// MOR-4463: on a touch screen the spectrum/waterfall divider must be
// reachable by a finger: it answers ten pixels above and below its 8px line.
// With a mouse it stays the thin line it was.
for (const touch of [true, false]) {
  test(`the split divider ${touch ? 'takes a finger-sized band on a touch screen' : 'stays a thin line for a mouse'}`, async ({ browser }) => {
    const context = await browser.newContext(touch
      ? { hasTouch: true, isMobile: true, viewport: { width: 430, height: 800 } }
      : { viewport: { width: 1280, height: 800 } });
    const page = await context.newPage();
    try {
      await page.goto('/fixtures/spectrum-witness.html');
      const separator = page.locator('.spectrum-split-separator');
      await expect(separator).toBeVisible();
      const hits = await separator.evaluate((element) => {
        const box = element.getBoundingClientRect();
        const x = box.left + box.width / 4;
        const at = (y: number) => document.elementFromPoint(x, y) === element;
        return { on: at(box.top + box.height / 2), above: at(box.top - 10), below: at(box.bottom + 10),
          farAbove: at(box.top - 20), farBelow: at(box.bottom + 20) };
      });
      expect(hits).toEqual({ on: true, above: touch, below: touch, farAbove: false, farBelow: false });
    } finally {
      await context.close();
    }
  });
}
