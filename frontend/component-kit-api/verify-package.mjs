import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import {
  mkdtemp, mkdir, readFile, readdir, realpath, rm, writeFile,
} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import ts from 'typescript';

const packageRoot = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.dirname(packageRoot);
const fixtureRoot = path.join(packageRoot, 'fixtures', 'external-kit');

function execute(command, args, cwd, allowFailure = false) {
  const result = spawnSync(command, args, { cwd, encoding: 'utf8' });
  if (!allowFailure && result.status !== 0) {
    throw new Error(`${command} ${args.join(' ')} failed.\n${result.stdout}${result.stderr}`);
  }
  return result;
}

async function filesUnder(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(entries.map(async (entry) => {
    const candidate = path.join(directory, entry.name);
    return entry.isDirectory() ? filesUnder(candidate) : [candidate];
  }));
  return nested.flat();
}

function packageFiles(pack) {
  return pack.files.map(({ path: file }) => file).sort();
}

async function assertPackedShape(pack, directory, requiredTypes) {
  const files = packageFiles(pack);
  const builtFiles = (await filesUnder(path.join(directory, 'dist')))
    .map((file) => path.relative(directory, file).replaceAll(path.sep, '/'))
    .sort();
  assert(files.includes('package.json'));
  assert(files.includes('dist/index.js'));
  for (const declaration of requiredTypes) assert(files.includes(declaration));
  assert(files.every((file) => file === 'package.json' || file.startsWith('dist/')));
  assert.deepEqual(files.filter((file) => file !== 'package.json'), builtFiles);
  assert.deepEqual(files.filter((file) => file.endsWith('.js')), ['dist/index.js']);
  assert(!files.some((file) => file.includes('/src/') && !file.startsWith('dist/types/')));
}

function moduleSpecifiers(source, file) {
  const parsed = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const specifiers = [];
  const visit = (node) => {
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node))
      && node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier)) {
      specifiers.push(node.moduleSpecifier.text);
    } else if (ts.isImportTypeNode(node)
      && ts.isLiteralTypeNode(node.argument) && ts.isStringLiteral(node.argument.literal)) {
      specifiers.push(node.argument.literal.text);
    }
    ts.forEachChild(node, visit);
  };
  visit(parsed);
  return specifiers;
}

async function assertClosedDeclarations(root, allowedBare) {
  const declarations = (await filesUnder(root)).filter((file) => file.endsWith('.d.ts'));
  assert(declarations.length > 0);
  for (const file of declarations) {
    const source = await readFile(file, 'utf8');
    assert(!source.includes('$lib'));
    assert(!source.includes(frontendRoot));
    for (const specifier of moduleSpecifiers(source, file)) {
      assert(!path.isAbsolute(specifier));
      assert(!specifier.startsWith('src/'));
      if (!specifier.startsWith('.')) {
        assert(allowedBare.has(specifier), `Unexpected bare declaration import ${specifier} in ${file}`);
        continue;
      }
      const target = path.resolve(path.dirname(file), specifier);
      assert(target.startsWith(root + path.sep));
      const candidates = [target, `${target}.d.ts`, path.join(target, 'index.d.ts')];
      const resolved = await Promise.all(candidates.map(async (candidate) => {
        try {
          return (await realpath(candidate)) === candidate;
        } catch {
          return false;
        }
      }));
      assert(resolved.some(Boolean), `Unresolved declaration import ${specifier} in ${file}`);
    }
  }
  return declarations;
}

function pack(directory, destination) {
  const result = execute('npm', [
    'pack', '--json', '--ignore-scripts', '--pack-destination', destination,
  ], directory);
  const [metadata] = JSON.parse(result.stdout);
  assert(metadata);
  return { ...metadata, tarball: path.join(destination, metadata.filename) };
}

async function installedSveltePackages(nodeModules) {
  const found = [];
  async function visit(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      const candidate = path.join(directory, entry.name);
      if (entry.name === 'svelte') {
        found.push(await realpath(candidate));
      } else if (entry.name.startsWith('@') || entry.name === 'node_modules') {
        await visit(candidate);
      } else {
        const nested = path.join(candidate, 'node_modules');
        try {
          await visit(nested);
        } catch {
          // Most packages do not own a nested node_modules directory.
        }
      }
    }
  }
  await visit(nodeModules);
  return [...new Set(found)];
}

execute(process.execPath, [path.join(packageRoot, 'build.mjs')], frontendRoot);

const temporaryRoot = await mkdtemp(path.join(os.tmpdir(), 'rigplane-component-kit-'));
try {
  const tarballs = path.join(temporaryRoot, 'tarballs');
  const consumer = path.join(temporaryRoot, 'consumer');
  await mkdir(tarballs);
  await mkdir(path.join(consumer, 'src'), { recursive: true });

  const apiPack = pack(packageRoot, tarballs);
  const fixturePack = pack(fixtureRoot, tarballs);
  await assertPackedShape(apiPack, packageRoot, [
    'dist/types/component-kit-api/src/index.d.ts',
    'dist/types/src/primitives/control-instruments/control-instrument-behavior.d.ts',
    'dist/types/src/primitives/control-instruments/control-instrument-renderer.svelte.d.ts',
  ]);
  await assertPackedShape(fixturePack, fixtureRoot, ['dist/index.d.ts']);

  const apiDeclarations = await assertClosedDeclarations(
    path.join(packageRoot, 'dist'),
    new Set(['svelte']),
  );
  const apiDeclarationPaths = apiDeclarations.map((file) =>
    path.relative(path.join(packageRoot, 'dist'), file).replaceAll(path.sep, '/')
  );
  assert(!apiDeclarationPaths.some((file) => file.endsWith('/skins/registry.d.ts')));
  assert(!apiDeclarationPaths.some((file) => file.endsWith('/instrument-composition.d.ts')));
  const apiDeclarationSource = (await Promise.all(
    apiDeclarations.map((file) => readFile(file, 'utf8')),
  )).join('\n');
  assert(!apiDeclarationSource.includes('PresentationHostMode'));
  const fixtureDeclarations = await assertClosedDeclarations(
    path.join(fixtureRoot, 'dist'),
    new Set(['@rigplane/component-kit-api', 'svelte']),
  );
  const apiRuntime = await readFile(path.join(packageRoot, 'dist', 'index.js'), 'utf8');
  assert(!apiRuntime.includes('svelte'));
  assert(!apiRuntime.includes('continuous-scalar'));
  assert(!apiRuntime.includes('frequency-interaction'));
  assert(!apiRuntime.includes('control-instrument-renderer'));
  assert(!apiRuntime.includes('createFiniteRendererContext'));

  await writeFile(path.join(consumer, 'package.json'), JSON.stringify({
    name: 'component-kit-portable-consumer',
    private: true,
    type: 'module',
    dependencies: {
      '@rigplane/component-kit-api': `file:${apiPack.tarball}`,
      '@rigplane/external-component-kit-fixture': `file:${fixturePack.tarball}`,
      svelte: '5.55.8',
    },
    devDependencies: {
      typescript: '5.9.3',
      vite: '7.3.3',
    },
  }, null, 2));
  await writeFile(path.join(consumer, 'tsconfig.json'), JSON.stringify({
    compilerOptions: {
      lib: ['ES2022', 'DOM'],
      module: 'ESNext',
      moduleResolution: 'Bundler',
      noEmit: true,
      strict: true,
      target: 'ES2022',
      types: ['svelte'],
    },
    include: ['src/**/*.ts'],
  }, null, 2));
  await writeFile(path.join(consumer, 'vite.config.js'), `export default {
  publicDir: false,
  build: {
    lib: { entry: 'src/index.ts', formats: ['es'], fileName: () => 'consumer.js' },
  },
};
`);
  await writeFile(path.join(consumer, 'src', 'index.ts'), `import {
  COMPONENT_KIT_API_VERSION,
  defineComponentKit,
  type ComponentKitDeclaration,
  type ChoiceRendererProps,
  type ControlOption,
  type FiniteChoiceValue,
  type FiniteControlAppearance,
  type FiniteControlReading,
  type FrequencyRendererProps,
  type LayoutManifest,
  type PresentationDeclaration,
  type ScalarAppearance,
} from '@rigplane/component-kit-api';
import type { Component } from 'svelte';
import fixtureKit from '@rigplane/external-component-kit-fixture';

const declaration: ComponentKitDeclaration = fixtureKit;
const scalar: ScalarAppearance | undefined = declaration.scalarAppearances?.fixture;
const layout: LayoutManifest | undefined = declaration.layouts?.[0];
const presentation: PresentationDeclaration | undefined = declaration.presentations?.[0];
const finite: FiniteControlAppearance | undefined = declaration.finiteControlAppearances?.fixture;
const numericChoiceRenderer: Component<ChoiceRendererProps<number>> | undefined = finite?.choice;
const oldChoiceOption: ControlOption<'OFF'> = { value: 'OFF', label: 'Off' };
const reasonedChoiceOption: ControlOption<'DATA1'> = {
  value: 'DATA1', label: 'Data 1', disabled: true, disabledReason: 'Not available',
};
function inspectChoice(props: ChoiceRendererProps<'OFF' | 'DATA1'>):
  FiniteControlReading<'OFF' | 'DATA1'> | undefined {
  const view = props.lease.view;
  if (view === undefined) return undefined;
  const option = view.options[0]?.value;
  const disabledReason: string | undefined = view.options[0]?.disabledReason;
  const supported: FiniteChoiceValue | undefined = option;
  const optionalEvidence = [view.defaultValue, view.requested, view.feedback] as const;
  const request = option === undefined ? undefined : () => props.lease.invoke(option);
  void supported;
  void disabledReason;
  void optionalEvidence;
  void request;
  return view.reading;
}
type FrequencyProps = FrequencyRendererProps;
void (null as FrequencyProps | null);
void scalar;
void layout;
void presentation;
void numericChoiceRenderer;
void oldChoiceOption;
void reasonedChoiceOption;
void inspectChoice;
export const installedKit = defineComponentKit(declaration);
export const apiVersion = COMPONENT_KIT_API_VERSION;
`);
  await writeFile(path.join(consumer, 'src', 'private-root-negative.ts'), `// @ts-expect-error host authority is not a public root export
import type { FiniteRendererContext } from '@rigplane/component-kit-api';
// @ts-expect-error host factories are not public root exports
import type { createFiniteRendererContext, createChoiceRendererSeat } from '@rigplane/component-kit-api';
// @ts-expect-error declaration-tree subpaths are not exported
import type { ChoiceRendererInput } from '@rigplane/component-kit-api/dist/types/src/primitives/control-instruments/control-instrument-renderer.svelte';

export type PrivateRootMustStayUnavailable = [
  FiniteRendererContext,
  typeof createFiniteRendererContext,
  typeof createChoiceRendererSeat,
  ChoiceRendererInput<string>,
];
`);

  execute('npm', [
    'install', '--ignore-scripts', '--no-audit', '--no-fund', '--package-lock=false',
  ], consumer);
  execute(process.execPath, [
    path.join(consumer, 'node_modules', 'typescript', 'bin', 'tsc'), '-p', 'tsconfig.json',
  ], consumer);
  execute(process.execPath, [
    path.join(consumer, 'node_modules', 'vite', 'bin', 'vite.js'), 'build', '--config', 'vite.config.js',
  ], consumer);

  await writeFile(path.join(consumer, 'inspect.mjs'), `import assert from 'node:assert/strict';
import * as api from '@rigplane/component-kit-api';
import fixtureKit from '@rigplane/external-component-kit-fixture';
assert.deepEqual(Object.keys(api).sort(), ['COMPONENT_KIT_API_VERSION', 'defineComponentKit']);
assert.equal(api.COMPONENT_KIT_API_VERSION, 1);
assert.equal(api.defineComponentKit(fixtureKit), fixtureKit);
`);
  execute(process.execPath, ['inspect.mjs'], consumer);
  const deepImport = execute(process.execPath, [
    '--input-type=module', '--eval', "await import('@rigplane/component-kit-api/src/index.ts')",
  ], consumer, true);
  assert.notEqual(deepImport.status, 0);
  assert.match(deepImport.stderr, /ERR_PACKAGE_PATH_NOT_EXPORTED/);
  const declarationDeepImport = execute(process.execPath, [
    '--input-type=module', '--eval',
    "await import('@rigplane/component-kit-api/dist/types/src/primitives/control-instruments/control-instrument-renderer.svelte')",
  ], consumer, true);
  assert.notEqual(declarationDeepImport.status, 0);
  assert.match(declarationDeepImport.stderr, /ERR_PACKAGE_PATH_NOT_EXPORTED/);

  const sveltePackages = await installedSveltePackages(path.join(consumer, 'node_modules'));
  assert.equal(sveltePackages.length, 1);
  const installedApi = JSON.parse(await readFile(
    path.join(consumer, 'node_modules', '@rigplane', 'component-kit-api', 'package.json'),
    'utf8',
  ));
  const installedSvelte = JSON.parse(await readFile(
    path.join(consumer, 'node_modules', 'svelte', 'package.json'),
    'utf8',
  ));
  assert.equal(installedApi.version, '0.2.0');
  assert.equal(installedApi.peerDependencies.svelte, '>=5.45.2 <6');

  console.log('component-kit-api portable package verification: OK');
  console.log(`runtime exports: COMPONENT_KIT_API_VERSION, defineComponentKit`);
  console.log(`consumer peer: svelte@${installedSvelte.version} (${sveltePackages.length} physical install)`);
  console.log(`API declarations: ${apiDeclarations.length}`);
  console.log(`fixture declarations: ${fixtureDeclarations.length}`);
  console.log(`API tarball files (${apiPack.files.length}): ${packageFiles(apiPack).join(', ')}`);
  console.log(`fixture tarball files (${fixturePack.files.length}): ${packageFiles(fixturePack).join(', ')}`);
} finally {
  await rm(temporaryRoot, { recursive: true, force: true });
}
