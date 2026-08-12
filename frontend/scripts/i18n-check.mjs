#!/usr/bin/env node
/**
 * RigPlane Core i18n catalog lint (RP-ML-013A).
 *
 * This script validates the JSON catalogs under
 * `frontend/src/lib/i18n/locales/` against the English source of truth
 * (`en-US.json`). It is the CI floor for community translation PRs.
 *
 * What it checks
 * --------------
 * 1. JSON validity. Invalid JSON fails fast with a line/column hint.
 * 2. Schema-wrapper handling. The optional `$schema` key is ignored
 *    (matches `runtime.ts#stripSchemaWrapper`).
 * 3. Key set against `en-US.json`:
 *      - keys present in a translation but absent from en-US are reported
 *        (typos / removed keys);
 *      - keys present in en-US but missing from a translation are NOT a
 *        failure (silent en-US fallback is the documented runtime
 *        behaviour). They are reported in the summary as informational
 *        coverage so a maintainer can spot incomplete locales.
 * 4. Placeholder set. For every shared key, the set of `{name}`
 *    placeholders in the translation must equal the set in en-US.
 *    Missing or extra placeholders, including typos like `{nam}`, fail.
 * 5. Glossary-token preservation. For every translated string, any
 *    glossary token (see `GLOSSARY_TOKENS` below — sourced verbatim from
 *    strategy glossary §2.A and §2.B) that appears in the en-US value
 *    must also appear verbatim in the translated value.
 *
 * Design constraints
 * ------------------
 * - Plain Node ESM. No npm dependencies. Community contributors can run
 *   `npm run i18n:check` after `npm ci`.
 * - Glossary token list is embedded INLINE in this script. The script
 *   does NOT reach into rigplane-strategy at runtime. When the strategy
 *   glossary is updated, this list must be updated in lockstep — see
 *   the comment block on `GLOSSARY_TOKENS`.
 * - The runtime is not imported; this script reads the JSON files
 *   directly so it can run before any Vite/TS pipeline is set up.
 * - Output prefers actionable messages over compact format. Each problem
 *   names the file path, key, and a sentence a translator can act on.
 *
 * Exit code
 * ---------
 *   0  catalogs OK
 *   1  one or more failures
 *   2  internal script error (treat as CI infra problem)
 */

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, relative, resolve as resolvePath } from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// frontend/scripts/i18n-check.mjs -> frontend/src/lib/i18n/locales
const LOCALES_DIR = resolvePath(__dirname, '..', 'src', 'lib', 'i18n', 'locales');
const SOURCE_LOCALE = 'en-US';
const REPO_ROOT = resolvePath(__dirname, '..', '..');
const CONTRIBUTING_REL = 'frontend/src/lib/i18n/CONTRIBUTING.md';

/**
 * Glossary token allow-list — strings that must never be translated.
 *
 * Sourced VERBATIM from `rigplane-strategy/docs/i18n/glossary-and-policy.md`
 * sections §2.A (Product And Brand Terms) and §2.B (Radio And Operating
 * Abbreviations). Embedded inline so this script has no runtime
 * dependency on the strategy repo.
 *
 * Maintenance contract: when strategy §2.A or §2.B changes, update this
 * list in the same PR or land a follow-up PR before merging the strategy
 * change. The values must match exactly (casing, hyphens, spaces).
 *
 * Notes:
 *   - Tokens with a trailing hyphen (e.g. `IC-`, `FT-`, `TS-`) are radio
 *     model prefixes; we match them as plain substrings, which catches
 *     full model designators like `IC-7300` as well.
 *   - "squelch" is in §2.B but is also a documented per-locale glossary
 *     term in §2.D / `glossary.squelch`. To keep this lint useful we
 *     EXEMPT translations of `glossary.*` keys from glossary-token
 *     preservation (see `isGlossaryNamespace`).
 *   - We do NOT include short single-letter tokens like `A`, `B`, `A=B`
 *     because they would false-positive on ordinary words. The runtime
 *     keeps them verbatim by virtue of catalog authoring, not lint.
 */
const GLOSSARY_TOKENS = [
  // §2.A — Product and brand terms
  'RigPlane Pro Companion',
  'RigPlane Core',
  'RigPlane Pro',
  'RigPlane',
  'Tower',
  'rigctld',
  'Hamlib',
  'CI-V',
  'WSJT-X',
  'fldigi',
  'JS8Call',
  'IC-',
  'FT-',
  'TS-',
  // §2.B — Radio and operating abbreviations
  'VFO',
  'MAIN',
  'SUB',
  'M-VFO',
  'PTT',
  'TX/RX',
  'TX',
  'RX',
  'MOX',
  'RIT',
  'XIT',
  'IF Shift',
  'Notch',
  'BPF',
  'Roofing',
  'CW',
  'SSB',
  'USB',
  'LSB',
  'AM',
  'FM',
  'NFM',
  'WFM',
  'DV',
  'DD',
  'RTTY',
  'PSK',
  'DATA',
  'DIGITAL',
  'AGC',
  'DSP',
  'NB',
  'NR',
  'NF',
  'MN',
  'VOX',
  'SWR',
  'ALC',
  'COMP',
  'MIC',
  'MON',
  'PWR',
  'S-meter',
  'squelch',
  'QSO',
  'QRZ',
  'QRM',
  'QRN',
  'QRP',
  'QSL',
  'QTH',
  'QSY',
  // §2.B band-name and unit suffixes are protocol values and are not
  // typically embedded as substrings of UI prose, so we omit them from
  // the substring scan to avoid false positives ("dB" in "doubt" etc.).

  // --- MOR-1450 additions -------------------------------------------------
  // NOT sourced from the strategy glossary (§2.A/§2.B do not cover these) —
  // these are rigplane-core-local faceplate terms per the MOR-1450 owner
  // ruling (see docs/i18n/faceplate-locale-invariance.md). Two casings are
  // listed for each because en-US uses the label casing standalone
  // ("Split", "Dual watch") and a lowercase mid-sentence casing inside
  // action-tooltip prose ("Quick split", "Quick dual watch"); this scan is
  // case-sensitive substring matching, not case-folded.
  'Split',
  'split',
  'Dual watch',
  'dual watch',
];

// ---------------------------------------------------------------------------
// Reporting
// ---------------------------------------------------------------------------

const errors = [];
const warnings = [];

function fail(filePath, message) {
  errors.push({ filePath, message });
}

function warn(filePath, message) {
  warnings.push({ filePath, message });
}

function formatErrors() {
  if (errors.length === 0) return '';
  const lines = ['', `i18n-check: ${errors.length} problem(s)`, ''];
  for (const { filePath, message } of errors) {
    const rel = filePath ? relative(REPO_ROOT, filePath) : '(unknown file)';
    lines.push(`  ${rel}`);
    for (const line of message.split('\n')) {
      lines.push(`    ${line}`);
    }
    lines.push('');
  }
  lines.push(`See ${CONTRIBUTING_REL} for translator guidance.`);
  return lines.join('\n');
}

function formatWarnings() {
  if (warnings.length === 0) return '';
  const lines = ['', `i18n-check: ${warnings.length} note(s) (informational)`, ''];
  for (const { filePath, message } of warnings) {
    const rel = filePath ? relative(REPO_ROOT, filePath) : '(unknown file)';
    lines.push(`  ${rel}`);
    for (const line of message.split('\n')) {
      lines.push(`    ${line}`);
    }
    lines.push('');
  }
  return lines.join('\n');
}

// ---------------------------------------------------------------------------
// JSON loading
// ---------------------------------------------------------------------------

/**
 * Convert a SyntaxError thrown by `JSON.parse` into a `(line, col, msg)`
 * triple. V8 reports `position N` in the message; we map that back to a
 * line/column for translator-friendly output. Falls back to the raw
 * message if parsing the message itself fails.
 */
function locateJsonError(raw, err) {
  const msg = err && err.message ? err.message : String(err);
  const m = /position\s+(\d+)/.exec(msg);
  if (!m) return { line: null, column: null, message: msg };
  const pos = Number(m[1]);
  let line = 1;
  let col = 1;
  for (let i = 0; i < pos && i < raw.length; i++) {
    if (raw.charCodeAt(i) === 0x0a) {
      line += 1;
      col = 1;
    } else {
      col += 1;
    }
  }
  return { line, column: col, message: msg };
}

/**
 * Load a catalog. Returns `{ catalog, raw }` where `catalog` is the
 * `$schema`-stripped string->string map. On failure, records an error
 * and returns `null`.
 */
function loadCatalog(filePath) {
  let raw;
  try {
    raw = readFileSync(filePath, 'utf8');
  } catch (err) {
    fail(filePath, `Could not read file: ${err.message}`);
    return null;
  }
  // UTF-8 BOM check. JSON.parse tolerates BOM in Node, but the rule
  // documented in CONTRIBUTING.md is no-BOM, so flag explicitly.
  if (raw.charCodeAt(0) === 0xfeff) {
    fail(filePath, 'File starts with a UTF-8 BOM. Save as UTF-8 without BOM.');
    return null;
  }
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    const loc = locateJsonError(raw, err);
    const where = loc.line ? ` (line ${loc.line}, column ${loc.column})` : '';
    fail(filePath, `Invalid JSON${where}: ${loc.message}`);
    return null;
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    fail(filePath, 'Top-level JSON value must be an object mapping keys to strings.');
    return null;
  }
  const catalog = {};
  for (const k of Object.keys(parsed)) {
    if (k === '$schema') continue;
    const v = parsed[k];
    if (typeof v !== 'string') {
      fail(
        filePath,
        `Value for key \`${k}\` is not a string. All catalog values must be plain JSON strings.`,
      );
      continue;
    }
    catalog[k] = v;
  }
  return { catalog, raw };
}

// ---------------------------------------------------------------------------
// Per-string checks
// ---------------------------------------------------------------------------

const PLACEHOLDER_RE = /\{([a-zA-Z][a-zA-Z0-9]*)\}/g;

function extractPlaceholders(value) {
  const out = new Set();
  // Skip literal-brace escapes `'{'` — they are not placeholders.
  const stripped = value.replace(/'\{'/g, '');
  let m;
  PLACEHOLDER_RE.lastIndex = 0;
  while ((m = PLACEHOLDER_RE.exec(stripped)) !== null) {
    out.add(m[1]);
  }
  return out;
}

function setEqual(a, b) {
  if (a.size !== b.size) return false;
  for (const v of a) {
    if (!b.has(v)) return false;
  }
  return true;
}

function diffSets(expected, actual) {
  const missing = [];
  const extra = [];
  for (const v of expected) if (!actual.has(v)) missing.push(v);
  for (const v of actual) if (!expected.has(v)) extra.push(v);
  return { missing, extra };
}

/**
 * `glossary.*` keys are the documented per-locale exception (strategy
 * §2.D, `CONTRIBUTING.md` "Glossary tokens"). Translators are explicitly
 * invited to translate `glossary.callsign` into コールサイン etc., so we
 * skip the glossary-preservation check for that namespace.
 */
function isGlossaryNamespace(key) {
  return key.startsWith('glossary.');
}

function findEmbeddedTokens(value) {
  const found = [];
  for (const token of GLOSSARY_TOKENS) {
    if (value.includes(token)) found.push(token);
  }
  return found;
}

// ---------------------------------------------------------------------------
// Catalog-level checks
// ---------------------------------------------------------------------------

function checkAgainstSource(filePath, catalog, sourceCatalog) {
  const sourceKeys = new Set(Object.keys(sourceCatalog));
  const catalogKeys = new Set(Object.keys(catalog));

  // Unknown keys (in translation but not in en-US).
  for (const key of catalogKeys) {
    if (!sourceKeys.has(key)) {
      fail(
        filePath,
        `Unknown key \`${key}\`. The English source \`en-US.json\` does not define this key.\n` +
          `If you intended to add a new key, add it to en-US.json first (see ${CONTRIBUTING_REL}).\n` +
          'If it is a typo, fix the spelling so it matches the en-US key exactly.',
      );
    }
  }

  // Coverage notes (en-US has it, this locale doesn't).
  const missingFromLocale = [];
  for (const key of sourceKeys) {
    if (!catalogKeys.has(key)) missingFromLocale.push(key);
  }
  if (missingFromLocale.length > 0) {
    const shown = missingFromLocale.slice(0, 10).map((k) => `\`${k}\``).join(', ');
    const more = missingFromLocale.length > 10
      ? ` … and ${missingFromLocale.length - 10} more`
      : '';
    warn(
      filePath,
      `Locale is missing ${missingFromLocale.length} key(s) defined in en-US: ${shown}${more}.\n` +
        'This is allowed — missing keys silently fall back to English in production.\n' +
        'Translate when you are ready; CI does not require full coverage.',
    );
  }

  // Per-shared-key checks: placeholders and glossary tokens.
  for (const key of catalogKeys) {
    if (!sourceKeys.has(key)) continue; // already reported above
    const sourceValue = sourceCatalog[key];
    const value = catalog[key];

    const expected = extractPlaceholders(sourceValue);
    const actual = extractPlaceholders(value);
    if (!setEqual(expected, actual)) {
      const { missing, extra } = diffSets(expected, actual);
      const parts = [];
      if (missing.length > 0) {
        parts.push(
          `missing placeholder(s) ${missing.map((n) => `\`{${n}}\``).join(', ')}`,
        );
      }
      if (extra.length > 0) {
        parts.push(
          `unexpected placeholder(s) ${extra.map((n) => `\`{${n}}\``).join(', ')}`,
        );
      }
      fail(
        filePath,
        `Placeholder mismatch for key \`${key}\`: ${parts.join('; ')}.\n` +
          `English source: ${JSON.stringify(sourceValue)}\n` +
          `Your translation: ${JSON.stringify(value)}\n` +
          'Every placeholder in the English source must appear in your translation,\n' +
          `and no new placeholders may be introduced. See ${CONTRIBUTING_REL}.`,
      );
    }

    if (!isGlossaryNamespace(key)) {
      const sourceTokens = findEmbeddedTokens(sourceValue);
      const translatedTokens = findEmbeddedTokens(value);
      const lostTokens = sourceTokens.filter((t) => !translatedTokens.includes(t));
      if (lostTokens.length > 0) {
        const list = lostTokens.map((t) => `\`${t}\``).join(', ');
        fail(
          filePath,
          `Glossary token(s) ${list} appear in the English source for key \`${key}\` but are missing from the translation.\n` +
            'These tokens are part of RigPlane\'s brand or the international radio vocabulary\n' +
            'and must appear verbatim in every locale (same spelling, same casing).\n' +
            `English source: ${JSON.stringify(sourceValue)}\n` +
            `Your translation: ${JSON.stringify(value)}\n` +
            `Keep the token in English even inside a translated sentence. See ${CONTRIBUTING_REL}.`,
        );
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Driver
// ---------------------------------------------------------------------------

function listLocaleFiles() {
  let entries;
  try {
    entries = readdirSync(LOCALES_DIR);
  } catch (err) {
    console.error(`i18n-check: cannot read ${LOCALES_DIR}: ${err.message}`);
    process.exit(2);
  }
  const files = [];
  for (const name of entries) {
    if (!name.endsWith('.json')) continue;
    const full = resolvePath(LOCALES_DIR, name);
    try {
      if (!statSync(full).isFile()) continue;
    } catch {
      continue;
    }
    files.push({ name, path: full });
  }
  return files.sort((a, b) => a.name.localeCompare(b.name));
}

function main() {
  const files = listLocaleFiles();
  if (files.length === 0) {
    console.error(`i18n-check: no locale files found under ${LOCALES_DIR}`);
    process.exit(2);
  }

  const sourceEntry = files.find((f) => f.name === `${SOURCE_LOCALE}.json`);
  if (!sourceEntry) {
    console.error(
      `i18n-check: source catalog ${SOURCE_LOCALE}.json is missing from ${LOCALES_DIR}`,
    );
    process.exit(2);
  }

  const sourceLoaded = loadCatalog(sourceEntry.path);
  if (sourceLoaded === null) {
    // Source itself failed to load — record was emitted; report and exit.
    console.error(formatErrors());
    process.exit(1);
  }
  const sourceCatalog = sourceLoaded.catalog;

  // Self-check the source: en-US must not contain unknown placeholders
  // relative to itself (trivially passes), and we sanity-check that no
  // catalog value is an empty string.
  for (const key of Object.keys(sourceCatalog)) {
    if (sourceCatalog[key] === '') {
      fail(
        sourceEntry.path,
        `Source key \`${key}\` has an empty value. Provide an English string or remove the key.`,
      );
    }
  }

  for (const entry of files) {
    if (entry.name === `${SOURCE_LOCALE}.json`) continue;
    const loaded = loadCatalog(entry.path);
    if (loaded === null) continue;
    // Also sanity-check empty values in translations.
    for (const key of Object.keys(loaded.catalog)) {
      if (loaded.catalog[key] === '') {
        fail(
          entry.path,
          `Key \`${key}\` has an empty value. Remove the entry to fall back to English, or provide a translation.`,
        );
      }
    }
    checkAgainstSource(entry.path, loaded.catalog, sourceCatalog);
  }

  const warnText = formatWarnings();
  if (warnText) console.log(warnText);

  if (errors.length > 0) {
    console.error(formatErrors());
    process.exit(1);
  }

  console.log(
    `i18n-check: OK (${files.length} locale file(s) checked, ${
      Object.keys(sourceCatalog).length
    } source keys).`,
  );
}

try {
  main();
} catch (err) {
  console.error(`i18n-check: internal error: ${err && err.stack ? err.stack : err}`);
  process.exit(2);
}
