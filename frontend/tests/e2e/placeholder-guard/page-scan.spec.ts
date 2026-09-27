/**
 * MOR-2677 — placeholder-guard part 2: the whole-page scan over the part-1
 * face × state × language matrix (`fixtures/catalog.ts`'s `HARNESS_REACH`),
 * initial render only (no clicks), against the token rule
 * (`src/lib/placeholder-token-rule.ts`) and the `offenders.json` ratchet.
 *
 * What is read per page, per MOR-2677:
 *
 *  - every text node's own trimmed content — this also covers `<option>`
 *    text and SVG `<text>`/`<title>`, whose content is text nodes;
 *  - `aria-label`, `aria-valuetext`, `title` (with the ticket's title
 *    allowance: rules 2–3 skipped for a 3-plus-word sentence title);
 *  - the text of each `aria-describedby` target.
 *
 * Ratchet: one `offenders.json` entry per known offender — (face, state,
 * language, locator class, token class) — matched per pattern, never per
 * count. An unlisted hit fails; a listed entry that no longer occurs also
 * fails, so the fixing PR deletes its own entries in the same change.
 */
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';
import { HARNESS_REACH } from '../../../fixtures/catalog';
import { findPlaceholderTokens } from '../../../src/lib/placeholder-token-rule';

const LANGUAGES = [null, 'studioline', 'fieldline', 'segmentline'] as const;

interface OffenderEntry {
  readonly face: string;
  readonly state: string;
  readonly language: string;
  readonly locator: string;
  readonly token: string;
  readonly ticket: string;
  /** Human-readable hit text at seeding time; not part of the match key. */
  readonly example?: string;
}

interface Cell {
  readonly state: string;
  readonly language: (typeof LANGUAGES)[number];
}

interface PageRecord {
  readonly locator: string;
  readonly text: string;
  readonly isTitle: boolean;
}

const offendersPath = join(dirname(fileURLToPath(import.meta.url)), 'offenders.json');
const OFFENDERS: readonly OffenderEntry[] =
  JSON.parse(readFileSync(offendersPath, 'utf8')) as OffenderEntry[];

const entryKey = (face: string, state: string, language: string, locator: string,
  token: string): string => `${face}|${state}|${language}|${locator}|${token}`;

function cellsFor(face: (typeof HARNESS_REACH)[number]): readonly Cell[] {
  return [
    { state: face.allUnread, language: null },
    { state: face.allUnsupported, language: null },
    { state: face.allUnsupportedReading, language: null },
    ...LANGUAGES.flatMap((language) => (language === null ? [] : [
      { state: face.allUnread, language },
      { state: face.allUnsupported, language },
      { state: face.allUnsupportedReading, language },
    ])),
  ];
}

function urlFor(face: (typeof HARNESS_REACH)[number], cell: Cell): string {
  return `/fixtures/${face.page}?fixture=${cell.state}&theme=v2`
    + (face.skin ? `&skin=${face.skin}` : '')
    + (cell.language ? `&language=${cell.language}` : '');
}

/**
 * Runs in the page. Collects every candidate string with a stable locator:
 * the closest `[data-testid]` or `[data-zone-id]`, the tag, and which
 * attribute (or `text`) the string came from. Token classification happens
 * Node-side, against the same pure module the vitest suite pins.
 */
async function collectRecords(page: Page): Promise<PageRecord[]> {
  return page.evaluate(() => {
    const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE']);
    const records: { locator: string; text: string; isTitle: boolean }[] = [];
    const seen = new Set<string>();
    const locatorFor = (el: Element, attribute: string): string => {
      const anchor = el.closest('[data-testid],[data-zone-id]');
      const id = anchor === null
        ? '[no-testid]'
        : anchor.hasAttribute('data-testid')
          ? `[data-testid="${anchor.getAttribute('data-testid')}"]`
          : `[data-zone-id="${anchor.getAttribute('data-zone-id')}"]`;
      return `${id} <${el.tagName.toLowerCase()}> ${attribute}`;
    };
    const push = (el: Element, attribute: string, text: string, isTitle: boolean) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      const locator = locatorFor(el, attribute);
      const key = `${locator}\u0000${trimmed}`;
      if (seen.has(key)) return;
      seen.add(key);
      records.push({ locator, text: trimmed, isTitle });
    };
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
      acceptNode: (node: Node) => {
        const parent = node.parentElement;
        if (parent === null || SKIP_TAGS.has(parent.tagName)) {
          return NodeFilter.FILTER_REJECT;
        }
        return (node.nodeValue ?? '').trim()
          ? NodeFilter.FILTER_ACCEPT
          : NodeFilter.FILTER_REJECT;
      },
    });
    for (let n = walker.nextNode(); n !== null; n = walker.nextNode()) {
      push(n.parentElement as Element, 'text', n.nodeValue ?? '', false);
    }
    for (const el of document.querySelectorAll('[aria-label]')) {
      push(el, 'aria-label', el.getAttribute('aria-label') ?? '', false);
    }
    for (const el of document.querySelectorAll('[aria-valuetext]')) {
      push(el, 'aria-valuetext', el.getAttribute('aria-valuetext') ?? '', false);
    }
    for (const el of document.querySelectorAll('[title]')) {
      push(el, 'title', el.getAttribute('title') ?? '', true);
    }
    for (const el of document.querySelectorAll('[aria-describedby]')) {
      for (const id of (el.getAttribute('aria-describedby') ?? '').split(/\s+/)) {
        const target = id ? document.getElementById(id) : null;
        if (target !== null) {
          push(el, `aria-describedby:${id}`, target.textContent ?? '', false);
        }
      }
    }
    return records;
  });
}

for (const face of HARNESS_REACH) {
  test(`face ${face.face}: placeholder tokens match the ratchet`, async ({ page }) => {
    const problems: string[] = [];
    const observed = new Set<string>();
    const dump: OffenderEntry[] = [];
    for (const cell of cellsFor(face)) {
      const language = cell.language ?? 'default';
      const url = urlFor(face, cell);
      await page.goto(url, { waitUntil: 'load' });
      await expect(
        page.locator('body[data-harness-ready="true"]'),
        `${face.face} never signalled harness-ready at ${url}`,
      ).toBeAttached({ timeout: 10_000 });
      const records = await collectRecords(page);
      for (const record of records) {
        for (const token of findPlaceholderTokens(record.text, { isTitle: record.isTitle })) {
          const key = entryKey(face.face, cell.state, language, record.locator, token.kind);
          if (!observed.has(key)) {
            observed.add(key);
            dump.push({
              face: face.face, state: cell.state, language,
              locator: record.locator, token: token.kind, ticket: 'TODO',
              example: record.text,
            });
          }
          if (!OFFENDERS.some((o) => entryKey(o.face, o.state, o.language, o.locator, o.token) === key)) {
            problems.push(
              `unlisted offender ${key} — text ${JSON.stringify(record.text)} `
              + `(token ${JSON.stringify(token.token)}) at ${url}`,
            );
          }
        }
      }
    }
    for (const o of OFFENDERS.filter((o) => o.face === face.face)) {
      if (!observed.has(entryKey(o.face, o.state, o.language, o.locator, o.token))) {
        problems.push(
          `stale offender ${entryKey(o.face, o.state, o.language, o.locator, o.token)} `
          + `(ticket ${o.ticket}) no longer occurs — delete its offenders.json entry in the `
          + `same change that fixed it`,
        );
      }
    }
    // Seeding support: with RP_PLACEHOLDER_GUARD_DUMP set to a directory,
    // every observed (face, state, language, locator, token class) is written
    // there as an offenders.json-shaped file (ticket left 'TODO') — the exact
    // input for seeding/re-seeding the ratchet. Written before the ratchet
    // assertion so a red run still produces the seed.
    const dumpDir = process.env.RP_PLACEHOLDER_GUARD_DUMP;
    if (dumpDir) {
      mkdirSync(dumpDir, { recursive: true });
      writeFileSync(
        join(dumpDir, `${face.face}.json`),
        `${JSON.stringify(dump, null, 1)}\n`,
      );
    }
    expect(problems, `${face.face} placeholder ratchet (offenders.json)`).toEqual([]);
  });
}
