import { createRequire } from 'node:module';

const require = createRequire(new URL('../../../frontend/package.json', import.meta.url));
const { test, expect } = require('@playwright/test');

test('launch', async ({ page }) => {
  await page.goto('about:blank');
  expect(await page.evaluate(() => 1 + 1)).toBe(2);
});
