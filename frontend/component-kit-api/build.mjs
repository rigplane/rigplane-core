import { spawnSync } from 'node:child_process';
import { mkdir, readFile, readdir, rm, unlink, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { build } from 'vite';

const packageRoot = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.dirname(packageRoot);
const apiDist = path.join(packageRoot, 'dist');
const fixtureRoot = path.join(packageRoot, 'fixtures', 'external-kit');
const fixtureDist = path.join(fixtureRoot, 'dist');
const tsc = path.join(frontendRoot, 'node_modules', 'typescript', 'bin', 'tsc');

function runTypeScript(config) {
  const result = spawnSync(process.execPath, [tsc, '-p', config], {
    cwd: frontendRoot,
    encoding: 'utf8',
  });
  if (result.status !== 0) {
    throw new Error(`TypeScript declaration build failed.\n${result.stdout}${result.stderr}`);
  }
}

async function bundle(entry, outDir, external) {
  await build({
    configFile: false,
    logLevel: 'warn',
    publicDir: false,
    build: {
      emptyOutDir: false,
      lib: { entry, formats: ['es'], fileName: () => 'index.js' },
      minify: false,
      outDir,
      rollupOptions: { external },
      sourcemap: false,
    },
  });
}

async function declarationFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(entries.map(async (entry) => {
    const candidate = path.join(directory, entry.name);
    return entry.isDirectory() ? declarationFiles(candidate) : candidate.endsWith('.d.ts') ? [candidate] : [];
  }));
  return nested.flat();
}

async function rewriteLibAliases() {
  const typesRoot = path.join(apiDist, 'types');
  const libRoot = path.join(typesRoot, 'src', 'lib');
  for (const file of await declarationFiles(typesRoot)) {
    const source = await readFile(file, 'utf8');
    const rewritten = source.replace(/(['"])\$lib(?:\/([^'"]+))?\1/g, (_match, quote, suffix = '') => {
      const target = path.join(libRoot, suffix);
      let relative = path.relative(path.dirname(file), target).replaceAll(path.sep, '/');
      if (!relative.startsWith('.')) relative = `./${relative}`;
      return `${quote}${relative}${quote}`;
    });
    if (rewritten !== source) await writeFile(file, rewritten);
  }
}

async function buildApi() {
  await rm(apiDist, { recursive: true, force: true });
  await mkdir(apiDist, { recursive: true });
  await bundle(path.join(packageRoot, 'src', 'index.ts'), apiDist, ['svelte']);
  runTypeScript(path.join(packageRoot, 'tsconfig.json'));
  await rewriteLibAliases();
}

async function buildFixture() {
  await rm(fixtureDist, { recursive: true, force: true });
  await mkdir(fixtureDist, { recursive: true });
  await bundle(
    path.join(fixtureRoot, 'src', 'index.ts'),
    fixtureDist,
    ['@rigplane/component-kit-api', 'svelte'],
  );
  const temporaryConfig = path.join(fixtureDist, 'tsconfig.build.json');
  await writeFile(temporaryConfig, JSON.stringify({
    extends: '../../../../tsconfig.app.json',
    compilerOptions: {
      allowJs: false,
      baseUrl: '../../../..',
      checkJs: false,
      composite: false,
      declaration: true,
      declarationMap: false,
      emitDeclarationOnly: true,
      noEmit: false,
      outDir: '.',
      paths: {
        '@rigplane/component-kit-api': [
          'component-kit-api/dist/types/component-kit-api/src/index.d.ts',
        ],
      },
      rootDir: '../src',
      stripInternal: true,
      tsBuildInfoFile: '../../../../node_modules/.tmp/component-kit-fixture.tsbuildinfo',
    },
    include: ['../src/index.ts'],
  }, null, 2));
  try {
    runTypeScript(temporaryConfig);
  } finally {
    await unlink(temporaryConfig).catch(() => {});
  }
}

await buildApi();
await buildFixture();
