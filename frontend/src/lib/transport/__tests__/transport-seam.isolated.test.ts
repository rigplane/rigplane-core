/**
 * Transport-seam guards (C4, no behaviour change).
 *
 * The UI must run over WebSockets today and over WebRTC data channels later.
 * Both seams live in exactly one place each:
 *   - message channels: `lib/transport/` (`message-transport.ts`,
 *     `websocket-transport.ts`, `transport-factory.ts`);
 *   - REST: `lib/api/api-fetch.ts` (`apiFetch`).
 *
 * Each test names the mutation it exists to kill: reaching past the seam
 * with a raw `WebSocket` or a raw `/api/v1` fetch.
 */
import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve, sep } from 'node:path';

// `process.cwd()` is `frontend/` under this project's vitest config.
const SRC_ROOT = resolve(process.cwd(), 'src');

function collectSourceFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      collectSourceFiles(full, out);
    } else if (full.endsWith('.ts') || full.endsWith('.svelte')) {
      out.push(full);
    }
  }
  return out;
}

function projectPath(full: string): string {
  return relative(SRC_ROOT, full).split(sep).join('/');
}

function isCommentLine(line: string): boolean {
  const trimmed = line.trim();
  return trimmed.startsWith('//') || trimmed.startsWith('*') || trimmed.startsWith('/*') || trimmed.startsWith('<!--');
}

function matchingLines(content: string, pattern: RegExp): string[] {
  return content.split('\n').filter((line) => !isCommentLine(line) && pattern.test(line));
}

describe('transport seam guards', () => {
  const files = collectSourceFiles(SRC_ROOT);

  it('no `new WebSocket(` outside lib/transport/', () => {
    const offenders: string[] = [];
    for (const full of files) {
      const rel = projectPath(full);
      if (rel.startsWith('lib/transport/')) continue;
      const hits = matchingLines(readFileSync(full, 'utf8'), /new WebSocket\(/);
      if (hits.length > 0) offenders.push(`${rel}: ${hits[0].trim()}`);
    }
    expect(offenders).toEqual([]);
  });

  it('every /api/v1 fetch goes through apiFetch', () => {
    // `lib/api/api-fetch.ts` owns the only `fetch(` allowed to stay: it IS
    // the seam (default implementation = fetch). `tuning.svelte.ts` talks to
    // the Pro supervisor companion surface (`/api/local/...`), which is not
    // part of the /api/v1 seam and keeps its own fetch.
    const offenders: string[] = [];
    for (const full of files) {
      const rel = projectPath(full);
      if (rel === 'lib/api/api-fetch.ts') continue;
      const content = readFileSync(full, 'utf8');
      for (const line of matchingLines(content, /fetch\(/)) {
        if (rel === 'lib/stores/tuning.svelte.ts' && line.includes('/api/local')) continue;
        offenders.push(`${rel}: ${line.trim()}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
