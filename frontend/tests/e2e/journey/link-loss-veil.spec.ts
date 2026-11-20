import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { startJourneyServer, type JourneyServerHandle } from './server-process';

/**
 * MOR-2241 — what every layout actually shows when the rig link is lost.
 *
 * Each scenario starts its own journey fake (tests/e2e/journey_web_server.py)
 * with the --rig-drop-file seam: writing the drop file flips the fake radio's
 * `connected` false and fires the server's own connection_state callback, the
 * same path a bench cable pull takes (the poller then backs off and state
 * broadcasts stop, so the frontend's 5 s staleness threshold trips too —
 * connection.svelte.ts: STALE_THRESHOLD_MS). Deleting the file restores the
 * link, which is the "clears on connection restored" arm of the ticket.
 *
 * Per layout the spec records four moments: connected, immediately after the
 * drop, settled (past the staleness threshold) and restored — screenshots plus
 * a JSON report with the radio chip's title, the StatusBar bad-link chip, the
 * layout root's data-link-fault attribute (the MOR-2425 link-fault veil) and
 * the phone layout's radio-link line.
 *
 * The layouts are the workspace layout ids (presentation/workspace/contract.ts:
 * LAYOUT_MANIFEST_ID), each seeded through the workspace store's localStorage
 * key; 'phone' is the viewport-driven mobile skin instead.
 */

const DROP_PORT = 18769;
const DROP_URL = `http://127.0.0.1:${DROP_PORT}`;
// connection.svelte.ts trips staleness at 5 s; give it margin.
const STALENESS_WAIT_MS = 6_500;
const HERE = path.dirname(new URL(import.meta.url).pathname);

// The live scope canvas can keep the headless compositor busy enough that the
// high-level screenshot call stalls on this host; the CDP path does not wait.
async function snap(page: Page, filePath: string): Promise<void> {
  try {
    await page.screenshot({ path: filePath, timeout: 15_000 });
  } catch {
    const cdp = await page.context().newCDPSession(page);
    const { data } = await cdp.send('Page.captureScreenshot', { format: 'png' });
    await writeFile(filePath, Buffer.from(data, 'base64'));
    await cdp.detach();
  }
}

const DESKTOP_LAYOUTS = [
  'standard',
  'sdr-test',
  'lcd-cockpit',
  'lcd-scope',
  'peer-split',
  'unified-instrument',
  'panadapter-first',
  'dual-sdr-face',
] as const;

interface LayoutObservation {
  layout: string;
  chipTitleImmediate: string | null;
  chipTitleSettled: string | null;
  badLinkChipSettled: boolean;
  linkFaultImmediate: string | null;
  linkFaultSettled: string | null;
  mobileLinkLineSettled: string | null;
  chipTitleRestored: string | null;
  linkFaultRestored: string | null;
  mobileLinkLineRestored: string | null;
}

const observations: LayoutObservation[] = [];

async function seedWorkspaceLayout(page: Page, layout: string): Promise<void> {
  await page.addInitScript(
    ([key, value]) => window.localStorage.setItem(key, value),
    [
      'rigplane:workspace',
      JSON.stringify({
        version: 2,
        layout,
        designLanguageBySkin: {},
        theme: 'default',
        density: 'comfortable',
        visibleSurfaces: {},
        zoneOrder: {},
        pinnedCommands: [],
      }),
    ],
  );
}

async function firstMatchingTitle(page: Page): Promise<string | null> {
  const chip = page.locator('.status-bar .indicator[title^="Radio ↔ Server:"]').first();
  return (await chip.count()) > 0 ? await chip.getAttribute('title') : null;
}

async function linkFaultOf(page: Page): Promise<string | null> {
  const veiled = page.locator('[data-link-fault]').first();
  return (await veiled.count()) > 0 ? await veiled.getAttribute('data-link-fault') : null;
}

async function mobileLinkLineOf(page: Page): Promise<string | null> {
  const line = page.getByTestId('radio-link-line');
  return (await line.count()) > 0 ? await line.textContent() : null;
}

test.describe.configure({ mode: 'serial' });

for (const layout of DESKTOP_LAYOUTS) {
  test(`link loss in layout: ${layout}`, async ({ page }, testInfo) => {
    test.setTimeout(90_000);
    const dir = await mkdtemp(path.join(tmpdir(), 'rig-drop-'));
    const dropFile = path.join(dir, 'drop');
    let server: JourneyServerHandle | undefined;
    try {
      server = await startJourneyServer(DROP_PORT, 14_074_000, ['--rig-drop-file', dropFile]);
      await seedWorkspaceLayout(page, layout);
      await page.goto(`${DROP_URL}/?ui=v2`, { waitUntil: 'domcontentloaded' });

      // Connected baseline: the control link is up and the rig fact arrived.
      await expect(
        page.locator('[role="status"][title="Control WebSocket: connected"]'),
      ).toBeVisible();
      await expect
        .poll(async () => (await (await page.request.get(`${DROP_URL}/api/v1/state`)).json()))
        .toMatchObject({ connection: { rigConnected: true } });
      await snap(page, testInfo.outputPath(`${layout}-1-connected.png`));

      // Rig link lost.
      await writeFile(dropFile, 'drop');
      await expect
        .poll(async () => (await (await page.request.get(`${DROP_URL}/api/v1/state`)).json()))
        .toMatchObject({ connection: { rigConnected: false } });
      // Let the rigConnected=false state_update reach the page, not beyond the
      // staleness threshold: sample the immediate indicators first.
      await page.waitForTimeout(1_000);
      const chipTitleImmediate = await firstMatchingTitle(page);
      const linkFaultImmediate = await linkFaultOf(page);
      await snap(page, testInfo.outputPath(`${layout}-2-link-lost-immediate.png`));

      // Settled: past the staleness threshold, whatever main does has done it.
      await page.waitForTimeout(STALENESS_WAIT_MS);
      const chipTitleSettled = await firstMatchingTitle(page);
      const badLinkChipSettled = (await page.getByTestId('bad-link-chip').count()) > 0;
      const linkFaultSettled = await linkFaultOf(page);
      const mobileLinkLineSettled = await mobileLinkLineOf(page);
      await snap(page, testInfo.outputPath(`${layout}-3-link-lost-settled.png`));

      // Restored: the indicators clear.
      await rm(dropFile);
      await expect
        .poll(async () => (await (await page.request.get(`${DROP_URL}/api/v1/state`)).json()))
        .toMatchObject({ connection: { rigConnected: true } });
      await page.waitForTimeout(1_500);
      const chipTitleRestored = await firstMatchingTitle(page);
      const linkFaultRestored = await linkFaultOf(page);
      const mobileLinkLineRestored = await mobileLinkLineOf(page);
      await snap(page, testInfo.outputPath(`${layout}-4-restored.png`));

      observations.push({
        layout,
        chipTitleImmediate,
        chipTitleSettled,
        badLinkChipSettled,
        linkFaultImmediate,
        linkFaultSettled,
        mobileLinkLineSettled,
        chipTitleRestored,
        linkFaultRestored,
        mobileLinkLineRestored,
      });
    } finally {
      await server?.kill();
      await rm(dir, { recursive: true, force: true });
    }
  });
}

// Phone layout: the mobile skin is chosen by the viewport, not the workspace.
test('link loss in layout: phone', async ({ page }, testInfo) => {
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 430, height: 932 });
  const dir = await mkdtemp(path.join(tmpdir(), 'rig-drop-'));
  const dropFile = path.join(dir, 'drop');
  let server: JourneyServerHandle | undefined;
  try {
    server = await startJourneyServer(DROP_PORT, 14_074_000, ['--rig-drop-file', dropFile]);
    await page.goto(`${DROP_URL}/?ui=v2`, { waitUntil: 'domcontentloaded' });

    await expect(page.getByRole('button', { name: 'Push to talk' })).toBeVisible();
    await expect
      .poll(async () => (await (await page.request.get(`${DROP_URL}/api/v1/state`)).json()))
      .toMatchObject({ connection: { rigConnected: true } });
    await snap(page, testInfo.outputPath('phone-1-connected.png'));

    await writeFile(dropFile, 'drop');
    await expect
      .poll(async () => (await (await page.request.get(`${DROP_URL}/api/v1/state`)).json()))
      .toMatchObject({ connection: { rigConnected: false } });
    await page.waitForTimeout(1_000);
    const linkFaultImmediate = await linkFaultOf(page);
    const mobileLineImmediate = await mobileLinkLineOf(page);
    await snap(page, testInfo.outputPath('phone-2-link-lost-immediate.png'));

    await page.waitForTimeout(STALENESS_WAIT_MS);
    const linkFaultSettled = await linkFaultOf(page);
    const mobileLinkLineSettled = await mobileLinkLineOf(page);
    await snap(page, testInfo.outputPath('phone-3-link-lost-settled.png'));

    await rm(dropFile);
    await expect
      .poll(async () => (await (await page.request.get(`${DROP_URL}/api/v1/state`)).json()))
      .toMatchObject({ connection: { rigConnected: true } });
    await page.waitForTimeout(1_500);
    const linkFaultRestored = await linkFaultOf(page);
    const mobileLinkLineRestored = await mobileLinkLineOf(page);
    await snap(page, testInfo.outputPath('phone-4-restored.png'));

    observations.push({
      layout: 'phone',
      chipTitleImmediate: null,
      chipTitleSettled: mobileLineImmediate,
      badLinkChipSettled: false,
      linkFaultImmediate,
      linkFaultSettled,
      mobileLinkLineSettled,
      chipTitleRestored: null,
      linkFaultRestored,
      mobileLinkLineRestored,
    });
  } finally {
    await server?.kill();
    await rm(dir, { recursive: true, force: true });
  }
});

test.afterAll(async () => {
  const reportPath = path.resolve(HERE, '../../.playwright-output/link-loss-report.json');
  await writeFile(reportPath, JSON.stringify(observations, null, 2));
});
