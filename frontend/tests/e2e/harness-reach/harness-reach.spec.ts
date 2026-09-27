/**
 * MOR-2676 — placeholder-guard part 1 smoke: every shipped face mounts in
 * the fixture harness with the all-unread and all-unsupported states, once
 * per design-language overlay, without a console error.
 *
 * The load matrix comes from `fixtures/catalog.ts`'s `HARNESS_REACH` — the
 * single source of truth for which faces exist and which fixture id carries
 * each state — so an unknown face id or a missing witness page fails here
 * by construction (the page throws before `body[data-harness-ready]` ever
 * appears, and the throw itself is a `pageerror`).
 *
 * This is the LOAD half of the guard only. Reading rendered output for
 * placeholder tokens, the ratchet and the `quick.yml` step are MOR-2677
 * (part 2), which builds on every face being reachable here.
 */
import { expect, test } from '@playwright/test';
import { HARNESS_REACH } from '../../../fixtures/catalog';

const LANGUAGES = [null, 'studioline', 'fieldline', 'segmentline'] as const;

interface Cell {
  readonly state: string;
  readonly language: (typeof LANGUAGES)[number];
}

function urlFor(face: (typeof HARNESS_REACH)[number], cell: Cell): string {
  return `/fixtures/${face.page}?fixture=${cell.state}&theme=v2`
    + (face.skin ? `&skin=${face.skin}` : '')
    + (cell.language ? `&language=${cell.language}` : '');
}

for (const face of HARNESS_REACH) {
  test(`face ${face.face}: every state × language loads with no console error`, async ({ page }) => {
    const cells: readonly Cell[] = [
      { state: face.allUnread, language: null },
      { state: face.allUnsupported, language: null },
      { state: face.allUnsupportedReading, language: null },
      ...LANGUAGES.flatMap((language) => (language === null ? [] : [
        { state: face.allUnread, language },
        { state: face.allUnsupported, language },
        { state: face.allUnsupportedReading, language },
      ])),
    ];
    const errors: string[] = [];
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(message.text());
    });
    page.on('pageerror', (error) => errors.push(`pageerror: ${String(error)}`));

    for (const cell of cells) {
      errors.length = 0;
      const url = urlFor(face, cell);
      await page.goto(url, { waitUntil: 'load' });
      await expect(
        page.locator('body[data-harness-ready="true"]'),
        `${face.face} never signalled harness-ready at ${url}`,
      ).toBeAttached({ timeout: 10_000 });
      expect(errors, `${face.face} · ${cell.state} · language=${cell.language ?? 'default'} · ${url}`)
        .toEqual([]);
    }
  });
}

// The "shell inert" page stays `caps-unloaded` on one face (the cockpit) —
// MOR-2676 keeps it exactly where it was, and the smoke test loads it too.
test('face dual-receiver-cockpit: caps-unloaded (shell inert) still loads', async ({ page }) => {
  const errors: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });
  page.on('pageerror', (error) => errors.push(`pageerror: ${String(error)}`));
  await page.goto('/fixtures/index.html?fixture=caps-unloaded&theme=v2', { waitUntil: 'load' });
  await expect(page.locator('body[data-harness-ready="true"]')).toBeAttached({ timeout: 10_000 });
  expect(errors).toEqual([]);
});
