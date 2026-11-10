// MOR-4388 — aggregate the per-cell matrix-cell.json files (and the perf
// result when present) into matrix-results.json plus a compact markdown
// table. Plain Node (no TS runtime): run with
//   node frontend/tests/e2e/panorama-matrix/aggregate-matrix-results.mjs <outputDir> <runBase> <scope> <destDir>

import { readdir, readFile, writeFile, copyFile } from 'node:fs/promises';
import path from 'node:path';

/**
 * @param {string} outputDir
 * @param {string} runBase
 * @param {string} scope
 * @param {string} destDir
 */
async function main(outputDir, runBase, scope, destDir) {
  const cells = [];
  const pngs = [];
  /** @type {any|null} */
  let perf = null;
  const startedAt = new Date();

  async function walk(dir) {
    for (const entry of await readdir(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) await walk(full);
      else if (entry.name === 'matrix-cell.json') cells.push(JSON.parse(await readFile(full, 'utf8')));
      else if (entry.name === 'perf-result.json') perf = JSON.parse(await readFile(full, 'utf8'));
      else if (entry.name.endsWith('.png')) pngs.push(full);
    }
  }
  await walk(outputDir);
  cells.sort((a, b) => a.cell.id.localeCompare(b.cell.id));

  const counts = { pass: 0, fail: 0, na: 0, error: 0 };
  const invariantCounts = {};
  for (const cell of cells) counts[cell.status] = (counts[cell.status] ?? 0) + 1;
  for (const cell of cells) {
    for (const invariant of cell.invariants ?? []) {
      const key = `I${invariant.invariant}`;
      invariantCounts[key] = invariantCounts[key] ?? { pass: 0, fail: 0, na: 0, 'expected-fail': 0 };
      invariantCounts[key][invariant.status] = (invariantCounts[key][invariant.status] ?? 0) + 1;
    }
  }

  const results = {
    run: { base: runBase, scope, startedAt: startedAt.toISOString() },
    counts,
    invariantCounts,
    perf,
    cells,
  };
  await writeFile(path.join(destDir, 'matrix-results.json'), JSON.stringify(results, null, 2));
  for (const png of pngs) await copyFile(png, path.join(destDir, path.basename(png)));

  const lines = [
    `# MOR-4388 panorama acceptance matrix — ${runBase} (${scope})`,
    '',
    `Cells: ${counts.pass} pass, ${counts.fail} fail, ${counts.na} na, ${counts.error} error (${cells.length} total)`,
    '',
    '| invariant | pass | fail | expected-fail (MOR-4385) | na |',
    '| --- | --- | --- | --- | --- |',
  ];
  for (const key of Object.keys(invariantCounts).sort()) {
    const c = invariantCounts[key];
    lines.push(`| ${key} | ${c.pass} | ${c.fail} | ${c['expected-fail']} | ${c.na} |`);
  }
  lines.push('', '| cell | status | failing invariants | evidence |', '| --- | --- | --- | --- |');
  for (const cell of cells) {
    const bad = (cell.invariants ?? []).filter(i => i.status === 'fail' || i.status === 'expected-fail');
    const detail = bad.map(i => `I${i.invariant}(${i.status}): ${i.summary}`).join(' · ') || (cell.error ?? 'all invariants pass');
    lines.push(`| ${cell.cell.id} | ${cell.status} | ${bad.filter(i => i.status === 'fail').length} | ${detail.replace(/\|/g, '/')} |`);
  }
  await writeFile(path.join(destDir, 'matrix-results.md'), lines.join('\n'));
  console.log(JSON.stringify({ counts, invariantCounts, cells: cells.length, perf: !!perf, pngs: pngs.length }));
}

const [outputDir, runBase, scope, destDir] = process.argv.slice(2);
if (!outputDir || !destDir) {
  console.error('usage: aggregate-matrix-results.mjs <outputDir> <runBase> <scope> <destDir>');
  process.exit(1);
}
await main(outputDir, runBase ?? 'unknown', scope ?? 'subset', destDir);
