import { readFile, writeFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import { TraceMap, originalPositionFor } from '@jridgewell/trace-mapping';

const output = process.env.PROFILE_OUTPUT;
if (!output || !path.isAbsolute(output)) throw new Error('PROFILE_OUTPUT must be absolute');
const maps = new Map();
async function resolveFrame(frame) {
  if (!frame.url) return { function: frame.functionName || '(anonymous)', source: '(native)', line: null };
  const pathname = new URL(frame.url).pathname;
  if (!maps.has(pathname)) {
    try { maps.set(pathname, new TraceMap(JSON.parse(await readFile(path.join(output, 'dist', pathname + '.map'), 'utf8')))); }
    catch { maps.set(pathname, null); }
  }
  const map = maps.get(pathname);
  const position = map ? originalPositionFor(map, { line: frame.lineNumber + 1, column: frame.columnNumber }) : null;
  return { function: position?.name || frame.functionName || '(anonymous)',
    source: position?.source ?? pathname, line: position?.line ?? frame.lineNumber + 1,
    column: position?.column ?? frame.columnNumber, anonymous: !frame.functionName,
    mapped: !!position?.source };
}
const results = [];
for (const filename of (await readdir(output)).filter(f => f.endsWith('.cpuprofile')).sort()) {
  const profile = JSON.parse(await readFile(path.join(output, filename), 'utf8'));
  const nodes = new Map(profile.nodes.map(node => [node.id, node]));
  const parents = new Map();
  for (const node of profile.nodes) for (const child of node.children ?? []) parents.set(child, node.id);
  const frames = new Map();
  const grouped = new Map();
  for (const [id, node] of nodes) {
    const frame = await resolveFrame(node.callFrame);
    const key = JSON.stringify(frame);
    frames.set(id, key);
    if (!grouped.has(key)) grouped.set(key, { ...frame, selfMs: 0, inclusiveMs: 0 });
  }
  for (const [index, id] of profile.samples.entries()) {
    const timeMs = profile.timeDeltas[index] / 1000;
    grouped.get(frames.get(id)).selfMs += timeMs;
    const seen = new Set();
    for (let parent = id; parent !== undefined; parent = parents.get(parent)) {
      const key = frames.get(parent);
      if (!seen.has(key)) grouped.get(key).inclusiveMs += timeMs;
      seen.add(key);
    }
  }
  const rows = [...grouped.values()];
  const durationMs = (profile.endTime - profile.startTime) / 1000;
  const idleMs = rows.filter(r => r.function === '(idle)').reduce((sum, r) => sum + r.selfMs, 0);
  const sampledMs = profile.timeDeltas.reduce((sum, time) => sum + time, 0) / 1000;
  const sampledNonIdleMs = sampledMs - idleMs;
  for (const row of rows) Object.assign(row, { selfPct: 100 * row.selfMs / sampledMs,
    inclusivePct: 100 * row.inclusiveMs / sampledMs, selfNonIdlePct: 100 * row.selfMs / sampledNonIdleMs });
  const application = rows.filter(r => r.source.includes('/frontend/src/'));
  results.push({ filename, durationMs, idleMs, nonIdleMs: sampledNonIdleMs,
    topApplicationSelf: [...application].sort((a, b) => b.selfMs - a.selfMs).slice(0, 25),
    topApplicationInclusive: [...application].sort((a, b) => b.inclusiveMs - a.inclusiveMs).slice(0, 25),
    sampledMs: profile.timeDeltas.reduce((sum, time) => sum + time, 0) / 1000,
    samples: profile.samples.length,
    topSelf: rows.sort((a, b) => b.selfMs - a.selfMs).slice(0, 35),
    topInclusive: rows.sort((a, b) => b.inclusiveMs - a.inclusiveMs).slice(0, 35) });
}
await writeFile(path.join(output, 'hotspots.json'), JSON.stringify(results, null, 2));
console.log(JSON.stringify(results.map(r => ({ filename: r.filename, nonIdleMs: r.nonIdleMs,
  topSelf: r.topSelf.filter(f => f.source !== '(native)').slice(0, 8) })), null, 2));
