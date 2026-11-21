/**
 * MOR-4250 — the i18n lint's hardcoded-Cyrillic source scan.
 *
 * `scripts/i18n-check.mjs` (RP-ML-013A, extended by MOR-4250) fails CI on
 * any hardcoded Cyrillic string literal in `frontend/src` outside the
 * catalogs and tests — that is the guard which would have caught the
 * customer bug where the English UI rendered Russian controller errors.
 *
 * These tests pin the detector's behaviour (literals vs comments, string
 * state) and assert the live tree is clean, so a newly introduced
 * hardcoded literal fails here in vitest as well as in the script itself.
 */
import { describe, it, expect } from 'vitest';
import {
  findCyrillicStringLiterals,
  scannableText,
  scanFrontendSources,
} from '../../../../scripts/i18n-check.mjs';

describe('findCyrillicStringLiterals (i18n-check.mjs MOR-4250 scan)', () => {
  it('flags Cyrillic in single-quoted, double-quoted and template literals', () => {
    const hits = findCyrillicStringLiterals(
      "const a = 'Удалённое управление потеряно.';\n"
      + 'const b = "Станция недоступна";\n'
      + 'const c = `потеряно ${reason}`;\n',
    );
    expect(hits).toHaveLength(3);
    expect(hits.map((h) => h.line)).toEqual([1, 2, 3]);
    expect(hits[0].snippet).toContain('Удалённое управление потеряно.');
  });

  it('ignores Cyrillic in line and block comments', () => {
    const hits = findCyrillicStringLiterals(
      '// Удалённое управление потеряно — комментарий\n'
      + 'const ok = "plain English";\n'
      + '/* Станция отказала\n'
      + '   в доступе */\n',
    );
    expect(hits).toEqual([]);
  });

  it('does not treat // inside a string literal as a comment start', () => {
    const hits = findCyrillicStringLiterals(
      "const url = 'https://example.com/станция';\n",
    );
    expect(hits).toHaveLength(1);
    expect(hits[0].line).toBe(1);
  });

  it('passes over plain ASCII literals', () => {
    expect(findCyrillicStringLiterals(
      "const a = 'Remote control was lost. Reconnect.'; // ok\n",
    )).toEqual([]);
  });

  it('extracts uppercase <SCRIPT> blocks: tag names are case-insensitive', () => {
    // CodeQL flagged the old case-sensitive match: a Cyrillic literal
    // inside <SCRIPT>…</SCRIPT> escaped the scan entirely.
    const blocks = scannableText(
      'Panel.svelte',
      '<SCRIPT>const a = \'Потеряно\';</SCRIPT>\n<p>Текст</p>\n',
    );
    expect(blocks).toHaveLength(1);
    expect(findCyrillicStringLiterals(blocks[0])).toHaveLength(1);
  });

  it('ends a <script> block at an end tag with whitespace before `>`', () => {
    // `</script >` closes the block in HTML; a scan that misses it would
    // run past the end tag and treat markup text as script.
    const blocks = scannableText(
      'Panel.svelte',
      '<script>const a = \'Потеряно\';</script >\n<p>Текст</p>\n<script>const b = 1;</script\t\n>\n',
    );
    expect(blocks).toEqual(["const a = 'Потеряно';", 'const b = 1;']);
  });

  it('flags Cyrillic literals inside Svelte template expressions', () => {
    // `<p>{'Потеряно'}</p>` renders Russian in the English UI exactly like
    // a script literal; the scan used to stop at the </script> end tag.
    const regions = scannableText(
      'Panel.svelte',
      '<script>const t = 1;</script>\n'
      + "<p>{'Потеряно'}</p>\n"
      + "<span>{cond ? 'Да' : 'Нет'}</span>\n"
      + "<button on:click={() => say('Ошибка')}>x</button>\n",
    );
    const hits = regions.flatMap((text) => findCyrillicStringLiterals(text));
    expect(hits.map((h) => h.snippet)).toEqual(['Потеряно', 'Да', 'Нет', 'Ошибка']);
  });

  it('flags Cyrillic literals in quoted attribute values, exactly once', () => {
    const regions = scannableText(
      'Panel.svelte',
      '<script>const cond = true;</script>\n'
      + '<p title="Потеряно">x</p>\n'
      + "<p aria-label='Закрыть'>y</p>\n"
      + '<p title="{cond ? \'Да\' : \'Нет\'}">z</p>\n',
    );
    const hits = regions.flatMap((text) => findCyrillicStringLiterals(text));
    // The interpolated value is scanned once (as an expression), not twice
    // (expression + attribute), so each literal below appears one time.
    expect(hits.map((h) => h.snippet)).toEqual(['Да', 'Нет', 'Потеряно', 'Закрыть']);
  });

  it('accepts comments, catalog lookups and markup text', () => {
    // Catalog lookups (`t('core.…')`) are the sanctioned mechanism; HTML
    // comments are not code; markup TEXT nodes are guarded by the
    // placeholder check, not the literal scan.
    const regions = scannableText(
      'Panel.svelte',
      '<script>import { t } from \'$lib/i18n\';</script>\n'
      + "<!-- {'Потеряно'} и просто текст -->\n"
      + "<p>{t('core.panel.title')}</p>\n"
      + '<p aria-label={t(\'core.panel.close\')}>Потеряно как текст</p>\n',
    );
    const hits = regions.flatMap((text) => findCyrillicStringLiterals(text));
    expect(hits).toEqual([]);
  });
});

describe('scanFrontendSources (live tree)', () => {
  it('finds no hardcoded Cyrillic string literals outside catalogs and tests', () => {
    // The allowlisted data literals (language endonyms, permit-status width
    // data) are the documented exceptions; anything else fails here.
    expect(scanFrontendSources()).toEqual([]);
  });
});
