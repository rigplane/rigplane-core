import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * Playwright globalSetup of the remote matrix (playwright.remote.config.ts):
 * build the R-A1 boot entry into frontend/dist/remote-entry so the station
 * (whose static_dir is frontend/dist) serves it. Runs once per invocation,
 * after `npm run build` has produced the real UI build — the real build
 * empties `dist/`, so this one must come second (the documented run order:
 * `npm run build && npx playwright test -c ./playwright.remote.config.ts`).
 *
 * It lives OUTSIDE tests/e2e/remote/ (the frontend's ESM scope, like
 * remote-matrix.ts) on purpose: that harness directory is CommonJS-scoped
 * for the spec graph, and Playwright compiles a globalSetup there to CJS
 * while evaluating it as ESM ("exports is not defined").
 */
export default function buildRemoteEntry(): void {
  // tests/e2e -> the frontend root (node_modules, the real vite.config.ts).
  const here = path.dirname(fileURLToPath(import.meta.url));
  const frontend = path.resolve(here, '../..');
  const vite = path.join(frontend, 'node_modules', '.bin', 'vite');
  execFileSync(
    vite,
    ['build', '-c', 'tests/e2e/remote/vite.remote-entry.config.ts'],
    { cwd: frontend, stdio: 'inherit' },
  );
}
