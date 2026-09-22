#!/usr/bin/env node

import { spawn } from 'node:child_process';
import { cpSync, existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(scriptDir, '..');

function readArg(name, fallback) {
  const index = process.argv.indexOf(name);
  if (index === -1) return fallback;
  const value = process.argv[index + 1];
  if (!value || value.startsWith('--')) {
    throw new Error(`missing value for ${name}`);
  }
  return value;
}

const port = readArg('--port', '4173');
const host = readArg('--host', '127.0.0.1');
const sourceDist = join(frontendRoot, 'dist');
const sourceIndex = join(sourceDist, 'index.html');

if (!existsSync(sourceIndex)) {
  throw new Error(
    `frontend dist is missing at ${sourceIndex}; run npm run build before npm run test:e2e:i18n`,
  );
}

const previewDist = mkdtempSync(join(tmpdir(), 'rigplane-i18n-preview-'));
rmSync(previewDist, { recursive: true, force: true });
cpSync(sourceDist, previewDist, { recursive: true, dereference: true });

const viteBin = join(frontendRoot, 'node_modules', 'vite', 'bin', 'vite.js');
const child = spawn(
  process.execPath,
  [
    viteBin,
    'preview',
    '--outDir',
    previewDist,
    '--port',
    port,
    '--strictPort',
    '--host',
    host,
  ],
  {
    cwd: frontendRoot,
    stdio: 'inherit',
  },
);

let cleaned = false;

function cleanup() {
  if (cleaned) return;
  cleaned = true;
  rmSync(previewDist, { recursive: true, force: true });
}

child.on('exit', (code, signal) => {
  cleanup();
  if (signal) {
    const signalExitCodes = {
      SIGINT: 130,
      SIGTERM: 143,
    };
    process.exit(signalExitCodes[signal] ?? 1);
  }
  process.exit(code ?? 0);
});

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => {
    child.kill(signal);
    cleanup();
  });
}
