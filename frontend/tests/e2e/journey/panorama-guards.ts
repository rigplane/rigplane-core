import { expect, type Page } from '@playwright/test';

/**
 * MOR-4392: one error watch for every panorama spec. A Svelte
 * "effect_update_depth_exceeded" or an uncaught page error kills reactive
 * updates silently (the owner's dead MORE button recovered only by a
 * reload), so every panorama spec asserts their absence at the end.
 */
export interface PanoramaErrorWatch {
  readonly pageErrors: string[];
  readonly consoleErrors: string[];
}

export function watchPanoramaErrors(page: Page): PanoramaErrorWatch {
  const watch: PanoramaErrorWatch = { pageErrors: [], consoleErrors: [] };
  page.on('pageerror', (error) => watch.pageErrors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') watch.consoleErrors.push(message.text());
  });
  return watch;
}

/** Every panorama spec: zero page errors and no Svelte effect-depth loop. */
export function expectPanoramaSettled(watch: PanoramaErrorWatch): void {
  expect(watch.pageErrors, 'page errors').toEqual([]);
  expect(
    watch.consoleErrors.filter((text) => text.includes('effect_update_depth_exceeded')),
    'effect_update_depth_exceeded',
  ).toEqual([]);
}

/** Interactive churn specs additionally demand a clean console. */
export function expectPanoramaClean(watch: PanoramaErrorWatch): void {
  expectPanoramaSettled(watch);
  expect(watch.consoleErrors, 'console errors').toEqual([]);
}
