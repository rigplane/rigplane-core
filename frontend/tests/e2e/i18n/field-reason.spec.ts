import { expect, test, type Page, type WebSocketRoute } from '@playwright/test';
import { CANONICAL_LAYOUT_MODES } from '../../../src/presentation/layout-mode';
import goldens from '../../../src/lib/transport/__tests__/fixtures/state-wire-goldens.json' with { type: 'json' };
import type { StateUpdateEnvelope } from '../../../src/lib/types/state';
import {
  fulfillControllerRoute, mockControllerHeartbeatAck, mockInfo, mockLocalControllerStatus,
} from './fixtures';

const golden = goldens['FTX-1'];
const refusal = golden.refusedMicGainFieldStatus.message;
const refusedDocument = golden.micGainRefused as StateUpdateEnvelope;
const recoveredDocument = golden.micGainRecovered as StateUpdateEnvelope;
const layouts = [...CANONICAL_LAYOUT_MODES].filter(layout => layout !== 'auto' && layout !== 'sdr-test');
// MOR-4829: only decks that mount the semantic TX composition render the
// `tx-aux-micGain` row. The segmentline glass skins (peer-split,
// unified-instrument, panadapter-first) and dual-sdr-face mount
// `SemanticRadioSurfaces` with `readonlyDisplay` only — the whole interactive
// composition lives in that component's `{:else}` branch — and `LcdLayout`
// suppresses its own semantic slot while the glass mounts. Those skins have
// no TX deck by design; their equivalent refusal surface is the shell-wide
// StatusBar radio indicator, whose title aggregates every fieldStatus
// message (`StatusBar.svelte`: the `radioTooltip` derivation).
const rowLayouts = layouts.filter(layout =>
  layout === 'lcd-cockpit' || layout === 'lcd-scope' || layout === 'standard');
const glassLayouts = layouts.filter(layout =>
  layout === 'peer-split' || layout === 'unified-instrument'
  || layout === 'panadapter-first' || layout === 'dual-sdr-face');

async function prepare(page: Page, layout: string) {
  let document = structuredClone(refusedDocument);
  let sequence = document.transportSeq;
  const sockets = new Set<WebSocketRoute>();
  await page.addInitScript(value => {
    localStorage.setItem('rigplane:workspace', JSON.stringify({ version: 1, layout: value,
      designLanguage: 'studioline', theme: 'github-light' }));
    localStorage.setItem('rigplane.i18n.locale', 'en-US');
  }, layout);
  await page.route('**/api/**', async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === '/api/v1/controller') return fulfillControllerRoute(route);
    const responses: Record<string, unknown> = {
      '/api/v1/state': document.data,
      '/api/v1/capabilities': golden.micGainCapabilities,
      '/api/v1/info': mockInfo,
      '/api/v1/controller': mockLocalControllerStatus,
    };
    return route.fulfill({ status: pathname.startsWith('/api/local/') ? 404 : 200,
      contentType: 'application/json', body: JSON.stringify(responses[pathname] ?? {}) });
  });
  await page.routeWebSocket(/.*/, socket => {
    socket.onMessage(message => {
      if (JSON.parse(String(message)).type === 'controller_heartbeat') {
        socket.send(JSON.stringify(mockControllerHeartbeatAck));
      }
    });
    if (new URL(socket.url()).pathname === '/api/v1/ws') {
      sockets.add(socket);
      socket.onClose(() => sockets.delete(socket));
      socket.send(JSON.stringify({ type: 'state_update', data: document }));
    }
  });
  return (next: StateUpdateEnvelope) => {
    const revision = Math.max(document.revision, next.revision) + 1;
    document = { ...structuredClone(next), transportSeq: ++sequence, revision, stateRevision: revision,
      data: { ...next.data!, revision, stateRevision: revision } };
    expect(sockets.size).toBeGreaterThan(0);
    for (const socket of sockets) socket.send(JSON.stringify({ type: 'state_update', data: document }));
  };
}

async function openMicGain(page: Page, phone: boolean) {
  if (phone) {
    await page.getByRole('tab', { name: 'TX', exact: true }).click();
    await page.locator('.m-tx-settings-btn').click();
    await page.getByRole('button', { name: /LEVELS/ }).click();
  } else {
    const disclosure = page.getByRole('button', { name: 'MIC GAIN settings', exact: true });
    await expect(disclosure.or(page.getByTestId('tx-aux-micGain')).first()).toBeVisible();
    if (await disclosure.count()) await disclosure.click();
  }
  const row = page.getByTestId('tx-aux-micGain');
  await expect(row).toBeVisible();
  return { row, slider: row.getByRole('slider') };
}

test('every canonical layout is classified as row or glass', () => {
  expect([...rowLayouts, ...glassLayouts].sort()).toEqual([...layouts].sort());
});

for (const layout of [...rowLayouts, 'phone']) {
  test(`${layout}: own MIC GAIN control explains refusal, recovery and a genuinely unobserved field`, async ({ page }) => {
    const phone = layout === 'phone';
    await page.setViewportSize(phone ? { width: 390, height: 844 } : { width: 1728, height: 1200 });
    const replay = await prepare(page, phone ? 'standard' : layout);
    await page.goto('/');
    if (!phone) await expect(page.locator('.skin-select')).toHaveValue(layout);
    const { row, slider } = await openMicGain(page, phone);
    await expect(slider).toHaveAttribute('aria-disabled', 'true');
    await expect(row).toHaveAttribute('title', refusal);
    await expect(slider).toHaveAccessibleDescription(refusal);

    replay(recoveredDocument);
    await expect(slider).toHaveAttribute('aria-disabled', 'false');
    await expect(row).not.toHaveAttribute('title', refusal);
    await expect(slider).not.toHaveAccessibleDescription(refusal);

    const unobserved = structuredClone(recoveredDocument);
    unobserved.data!.micGain = null;
    unobserved.data!.fieldStatus!.micGain = { ...refusedDocument.data!.fieldStatus!.micGain,
      availability: 'missing', message: undefined };
    replay(unobserved);
    await expect(slider).toHaveAttribute('aria-disabled', 'true');
    await expect(row).toHaveAttribute('title', 'Not yet observed');
    await expect(slider).toHaveAccessibleDescription('Not yet observed');
  });
}

for (const layout of glassLayouts) {
  test(`${layout}: StatusBar explains the MIC GAIN refusal and its recovery`, async ({ page }) => {
    await page.setViewportSize({ width: 1728, height: 1200 });
    const replay = await prepare(page, layout);
    await page.goto('/');
    await expect(page.locator('.skin-select')).toHaveValue(layout);
    // The admitted glass/face carries no TX deck, so no `tx-aux-micGain` row
    // exists here; the refusal surfaces on the shell-wide radio indicator.
    // (Before capabilities admit the glass, the lcd-cockpit fallback may
    // briefly mount the row — the absence assertion below polls until the
    // admitted skin has replaced it.)
    const radio = page.locator('.status-indicators .indicator').first();
    await expect(radio).toBeVisible();
    await expect(radio).toHaveAttribute('title', /refused to report mic gain/);
    await expect(radio).toHaveAttribute('aria-label', /refused to report mic gain/);
    await expect(page.getByTestId('tx-aux-micGain')).toHaveCount(0);

    replay(recoveredDocument);
    await expect(radio).not.toHaveAttribute('title', /refused to report mic gain/);
    await expect(radio).not.toHaveAttribute('aria-label', /refused to report mic gain/);

    const unobserved = structuredClone(recoveredDocument);
    unobserved.data!.micGain = null;
    unobserved.data!.fieldStatus!.micGain = { ...refusedDocument.data!.fieldStatus!.micGain,
      availability: 'missing', message: undefined };
    replay(unobserved);
    await expect(radio).not.toHaveAttribute('title', /refused to report mic gain/);
    await expect(radio).not.toHaveAttribute('aria-label', /refused to report mic gain/);
  });
}
