import { test, expect, type Page, type Locator } from '@playwright/test';

test.use({ actionTimeout: 10_000 });
test.setTimeout(30_000);

async function open(page: Page, path: string, inset: number) {
  await page.goto(path);
  await page.waitForSelector('body[data-harness-ready="true"]');
  await page.evaluate((value) => {
    document.documentElement.style.setProperty('--rp-chrome-top', `${value}px`);
  }, inset);
}

async function fits(locator: Locator, top: number, bottom: number) {
  const box = await locator.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.height).toBeGreaterThan(0);
  expect(box!.y).toBeGreaterThanOrEqual(top - 1);
  expect(box!.y + box!.height).toBeLessThanOrEqual(bottom + 1);
}

test('desktop defaults to the full viewport without a shell inset', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 812 });
  await page.goto('/fixtures/skin-witness.html?skin=desktop-v2');
  await page.waitForSelector('body[data-harness-ready="true"]');
  const box = await page.locator('.radio-layout').boundingBox();
  expect(box!.y).toBe(0);
  expect(box!.y + box!.height).toBe(812);
});

for (const inset of [0, 48]) {
  for (const layout of [
    { name: 'desktop', path: '/fixtures/skin-witness.html?skin=desktop-v2', selector: '.radio-layout', width: 1280 },
    { name: 'lcd', path: '/fixtures/skin-witness.html?skin=lcd-cockpit', selector: '.lcd-layout', width: 1280 },
    { name: 'portrait', path: '/fixtures/mobile-witness.html', selector: '.m-layout', width: 375 },
  ]) {
    test(`${layout.name} fits both viewport edges with ${inset}px chrome`, async ({ page }) => {
      await page.setViewportSize({ width: layout.width, height: 812 });
      await open(page, layout.path, inset);
      await fits(page.locator(layout.selector), inset, 812);
      const box = await page.locator(layout.selector).boundingBox();
      expect(box!.y).toBeCloseTo(inset, 0);
      expect(box!.y + box!.height).toBeCloseTo(812, 0);
    });
  }

  test(`landscape phone fits both viewport edges with ${inset}px chrome`, async ({ page }) => {
    await page.setViewportSize({ width: 812, height: 375 });
    await open(page, '/fixtures/mobile-witness.html', inset);
    const layout = page.locator('.m-landscape');
    await fits(layout, inset, 375);
    const box = await layout.boundingBox();
    expect(box!.y).toBeCloseTo(inset, 0);
    expect(box!.y + box!.height).toBeCloseTo(375, 0);
  });

  test(`confirmation backdrop clears ${inset}px chrome`, async ({ page }) => {
    await page.setViewportSize({ width: 800, height: 500 });
    await open(page, '/fixtures/spectrum-witness.html', inset);
    await page.evaluate(async () => {
      const sveltePath = '/node_modules/.vite/deps/svelte.js';
      const componentPath = '/src/components-v2/dialogs/ConfirmDialog.svelte';
      const { mount } = await import(sveltePath);
      const { default: ConfirmDialog } = await import(componentPath);
      mount(ConfirmDialog, { target: document.body, props: {
        open: true, message: 'Confirm geometry', confirmLabel: 'Confirm', cancelLabel: 'Cancel',
        onConfirm: () => {}, onCancel: () => {},
      } });
    });
    await fits(page.locator('.modal-backdrop'), inset, 500);
    const box = await page.locator('.modal-backdrop').boundingBox();
    expect(box!.y).toBeCloseTo(inset, 0);
    expect(box!.y + box!.height).toBeCloseTo(500, 0);
    await fits(page.getByRole('alertdialog'), inset, 500);
  });

  test(`fullscreen spectrum fits both viewport edges with ${inset}px chrome`, async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await open(page, '/fixtures/spectrum-witness.html', inset);
    await page.getByTitle('Toggle fullscreen', { exact: true }).click();
    const panel = page.locator('.spectrum-panel.fullscreen');
    await fits(panel, inset, 800);
    const box = await panel.boundingBox();
    expect(box!.y).toBeCloseTo(inset, 0);
    expect(box!.y + box!.height).toBeCloseTo(800, 0);
  });

  test(`landscape mobile fullscreen fits both viewport edges with ${inset}px chrome`, async ({ page }) => {
    await page.setViewportSize({ width: 812, height: 375 });
    await open(page, '/fixtures/mobile-witness.html', inset);
    const fullscreen = page.getByTitle('Toggle fullscreen', { exact: true });
    await fullscreen.focus();
    await fullscreen.press('Enter');
    const panel = page.locator('.spectrum-panel.fullscreen');
    await fits(panel, inset, 375);
    const box = await panel.boundingBox();
    expect(box!.y).toBeCloseTo(inset, 0);
    expect(box!.y + box!.height).toBeCloseTo(375, 0);
  });

  test(`filter settings fit the remaining viewport with ${inset}px chrome`, async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 260 });
    await open(page, '/fixtures/skin-witness.html?skin=desktop-v2', inset);
    await page.evaluate(async () => {
      const sveltePath = '/node_modules/.vite/deps/svelte.js';
      const panelPath = '/src/components-v2/panels/FilterPanel.svelte';
      const { mount } = await import(sveltePath);
      const { default: FilterPanel } = await import(panelPath);
      const target = document.createElement('div');
      target.style.cssText = 'position:fixed;top:100px;left:0;z-index:9999';
      document.body.append(target);
      mount(FilterPanel, { target });
    });
    await page.getByRole('button', { name: 'Open filter settings' }).click();
    const dialog = page.getByRole('dialog', { name: /Filter settings/ });
    await expect(dialog).toBeVisible();
    await fits(dialog, inset, 260);
  });

  test(`keyboard help fits the remaining viewport with ${inset}px chrome`, async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 260 });
    await open(page, '/fixtures/skin-witness.html?skin=desktop-v2', inset);
    await page.keyboard.press('?');
    const card = page.locator('.keyboard-help-card');
    await expect(card).toBeVisible();
    await fits(card, inset, 260);
  });

  test(`two floating extensions stay reachable with ${inset}px chrome at 1280x500`, async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 500 });
    await page.route('**/api/local/v1/ui/manifest', (route) => route.fulfill({ json: {
      version: 1, host_api: '2.0', extensions: [
        { id: 'geometry-a', title: 'Geometry A', mount: 'floating-overlay', entry: '/geometry-a.js' },
        { id: 'geometry-b', title: 'Geometry B', mount: 'floating-overlay', entry: '/geometry-b.js' },
      ],
    } }));
    for (const id of ['geometry-a', 'geometry-b']) {
      await page.route(`**/${id}.js`, (route) => route.fulfill({
        contentType: 'text/javascript',
        body: `window.rigplaneExtensionHost.register({id:"${id}",render(node){const content=document.createElement("div");content.style.height="2000px";content.textContent="${id}";node.append(content);}});`,
      }));
    }
    await open(page, '/fixtures/spectrum-witness.html', inset);
    await page.evaluate(async () => {
      const meta = document.createElement('meta');
      meta.name = 'rigplane-local-supervisor';
      document.head.append(meta);
      localStorage.setItem('rigplane:local-extension-dock-layout:v1', JSON.stringify({
        version: 1, extensions: { 'geometry-a': { mode: 'floating' }, 'geometry-b': { mode: 'floating' } },
      }));
      const sveltePath = '/node_modules/.vite/deps/svelte.js';
      const hostPath = '/src/lib/local-extensions/LocalExtensionsHost.svelte';
      const { mount } = await import(sveltePath);
      const { default: Host } = await import(hostPath);
      mount(Host, { target: document.body });
    });
    const stack = page.locator('.local-extension-stack--floating');
    await expect(stack).toBeVisible();
    await fits(stack, inset + 12, 500 - 12);
    const panels = stack.locator('.local-extension-panel');
    await expect(panels).toHaveCount(2);
    const stackBox = await stack.boundingBox();
    await fits(panels.nth(0), stackBox!.y, stackBox!.y + stackBox!.height);
    await panels.nth(1).scrollIntoViewIfNeeded();
    await fits(panels.nth(1), stackBox!.y, stackBox!.y + stackBox!.height);
  });

  for (const withBar of [false, true]) {
    test(`short scope More panel with ${inset}px chrome, bottom bar ${withBar}`, async ({ page }) => {
      await page.setViewportSize({ width: 800, height: 260 });
      await open(page, '/fixtures/spectrum-witness.html', inset);
      await page.evaluate(async (bar) => {
        const sveltePath = '/node_modules/.vite/deps/svelte.js';
        const componentPath = '/src/components/spectrum/ScopeMorePanel.svelte';
        const { mount, createRawSnippet } = await import(sveltePath);
        const { default: ScopeMorePanel } = await import(componentPath);
        const anchor = document.createElement('button');
        anchor.style.cssText = 'position:fixed;top:190px;right:20px;height:25px';
        document.body.append(anchor);
        if (bar) {
          const strip = document.createElement('div');
          strip.dataset.bottomBar = '';
          strip.style.cssText = 'position:fixed;bottom:0;height:40px;width:100%';
          document.body.append(strip);
        }
        mount(ScopeMorePanel, { target: document.body, props: {
          anchor, onClose: () => {},
          radioHeld: createRawSnippet(() => ({ render: () => '<div style="height:500px">Tall controls</div>' })),
        } });
      }, withBar);
      await fits(page.getByTestId('scope-more-panel'), inset + 8, withBar ? 212 : 252);
    });
  }

  test(`phone DSP dialog fits between ${inset}px chrome and tuning strip`, async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 500 });
    await open(page, '/fixtures/mobile-witness.html?fixture=topology-2-main-sub--phone', inset);
    await page.evaluate(async () => {
      const radioPath = '/src/lib/stores/radio.svelte.ts';
      const harnessPath = '/fixtures/harness-state.ts';
      const { setRadioState } = await import(radioPath);
      const { harness } = await import(harnessPath);
      const unobserved = {
        dataMode: null, sMeter: null, att: null, preamp: null,
        nb: null, nr: null, afLevel: null, rfGain: null, squelch: null,
      };
      const state = {
        ...harness.state,
        revision: 1, stateRevision: 1, freshnessRevision: 1, observationSeq: 1,
        updatedAt: '2026-10-28T00:00:00Z', tunerStatus: null,
        connection: { rigConnected: true, radioReady: true, controlConnected: true },
        main: { ...unobserved, ...harness.state.main },
        sub: { ...unobserved, ...harness.state.sub },
      };
      if (!setRadioState(state)) throw new Error('Phone fixture radio state was rejected');
    });
    await page.locator('[aria-controls="m-chip-panel-dsp"]').click();
    const nr = page.getByTitle('NR — click to toggle; long-press for settings', { exact: true });
    await nr.dispatchEvent('pointerdown');
    const dialog = page.getByRole('dialog', { name: 'Noise reduction settings', exact: true });
    await expect(dialog).toBeVisible();
    await nr.dispatchEvent('pointerup');
    const strip = await page.locator('.m-tuning-strip').boundingBox();
    await fits(dialog, inset + 8, strip!.y - 8);
  });

  test(`flipped TX settings fit a short viewport with ${inset}px chrome`, async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 260 });
    await open(page, '/fixtures/skin-witness.html?skin=desktop-v2', inset);
    const trigger = page.getByRole('button', { name: 'VOX settings', exact: true });
    await trigger.evaluate((node) => {
      (node as HTMLElement).style.cssText = 'position:fixed;top:100px;right:20px';
    });
    await trigger.click();
    await fits(page.getByTestId('standard-tx-settings-popover'), inset + 8, 252);
  });

  for (const { mode, height, width } of [
    { mode: 'dock-left', height: 500, width: 1280 }, { mode: 'dock-right', height: 500, width: 1280 },
    { mode: 'floating', height: 500, width: 1280 }, { mode: 'floating', height: 180, width: 1280 },
    { mode: 'floating', height: 180, width: 375 },
    { mode: 'dock-bottom', height: 180, width: 1280 },
    { mode: 'dock-bottom', height: 180, width: 375 },
  ]) {
    test(`${mode} fits below ${inset}px chrome at ${width}x${height}`, async ({ page }) => {
      await page.setViewportSize({ width, height });
      await page.route('**/api/local/v1/ui/manifest', (route) => route.fulfill({ json: {
        version: 1, host_api: '2.0', extensions: [
          { id: 'geometry', title: 'Geometry', mount: 'floating-overlay', entry: '/geometry-extension.js' },
        ],
      } }));
      await page.route('**/geometry-extension.js', (route) => route.fulfill({
        contentType: 'text/javascript',
        body: 'window.rigplaneExtensionHost.register({id:"geometry",render(node){const content=document.createElement("div");content.style.height="2000px";content.textContent="Geometry";node.append(content);}});',
      }));
      await open(page, '/fixtures/spectrum-witness.html', inset);
      await page.evaluate(async (dockMode) => {
        const meta = document.createElement('meta');
        meta.name = 'rigplane-local-supervisor';
        document.head.append(meta);
        localStorage.setItem('rigplane:local-extension-dock-layout:v1', JSON.stringify({
          version: 1, extensions: { geometry: { mode: dockMode } },
        }));
        const sveltePath = '/node_modules/.vite/deps/svelte.js';
        const hostPath = '/src/lib/local-extensions/LocalExtensionsHost.svelte';
        const { mount } = await import(sveltePath);
        const { default: Host } = await import(hostPath);
        mount(Host, { target: document.body });
      }, mode);
      const stack = page.locator(`.local-extension-stack--${mode}`);
      await expect(stack).toBeVisible();
      const margin = width <= 760 ? 8 : 12;
      await fits(stack, inset + margin, height - margin);
      await fits(stack.locator('.local-extension-panel'), inset + margin, height - margin);
    });
  }
}
