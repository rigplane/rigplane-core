import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import {
  mkdtemp, mkdir, readFile, readdir, realpath, rm, writeFile,
} from 'node:fs/promises';
import { createServer } from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import ts from 'typescript';
import { chromium } from 'playwright';

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

async function sha256(file) {
  return createHash('sha256').update(await readFile(file)).digest('hex');
}

async function withStaticServer(directory, visit) {
  const server = createServer(async (request, response) => {
    try {
      const pathname = new URL(request.url ?? '/', 'http://127.0.0.1').pathname;
      const relative = pathname === '/' ? 'index.html' : pathname.slice(1);
      const file = path.resolve(directory, relative);
      if (!file.startsWith(`${path.resolve(directory)}${path.sep}`)) throw new Error('bad path');
      const body = await readFile(file);
      response.setHeader('content-type', file.endsWith('.js') ? 'text/javascript' : 'text/html');
      response.end(body);
    } catch {
      response.statusCode = 404;
      response.end('not found');
    }
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  try {
    const address = server.address();
    assert(address && typeof address === 'object');
    await visit(`http://127.0.0.1:${address.port}`);
  } finally {
    await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  }
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
  const apiTarballSha256 = await sha256(apiPack.tarball);
  const fixtureTarballSha256 = await sha256(fixturePack.tarball);
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
      '@sveltejs/vite-plugin-svelte': '6.2.1',
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
    include: ['src/**/*.ts', 'src/**/*.svelte'],
  }, null, 2));
  await writeFile(path.join(consumer, 'vite.config.js'), `import { svelte } from '@sveltejs/vite-plugin-svelte';
export default {
  plugins: [svelte()],
  publicDir: false,
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
  type LevelMeterRendererProps,
  type MeterAppearance,
  type MeterNumericEvidence,
  type PresentationDeclaration,
  type ScalarAppearance,
  type SignalMeterRendererProps,
  type SignalMeterEvidence,
} from '@rigplane/component-kit-api';
import type { Component } from 'svelte';
import fixtureKit from '@rigplane/external-component-kit-fixture';

const declaration: ComponentKitDeclaration = fixtureKit;
const scalar: ScalarAppearance | undefined = declaration.scalarAppearances?.fixture;
const layout: LayoutManifest | undefined = declaration.layouts?.[0];
const presentation: PresentationDeclaration | undefined = declaration.presentations?.[0];
const finite: FiniteControlAppearance | undefined = declaration.finiteControlAppearances?.fixture;
const numericChoiceRenderer: Component<ChoiceRendererProps<number>> | undefined = finite?.choice;
const meter: MeterAppearance | undefined = declaration.meterAppearances?.fixture;
const signalMeterRenderer: Component<SignalMeterRendererProps> | undefined = meter?.signal;
const levelMeterRenderer: Component<LevelMeterRendererProps> | undefined = meter?.level;
const oldShape: ComponentKitDeclaration = { apiVersion: 1, id: 'old-shape' };
// @ts-expect-error a current numeric observation always carries its value
const invalidCurrentEvidence: MeterNumericEvidence = { state: 'current', domain: { kind: 'engineering', unit: 'w' } };
// @ts-expect-error unknown signal evidence cannot smuggle a numeric value
const invalidUnknownSignal: SignalMeterEvidence = { state: 'unknown', value: 0, domain: { kind: 'unknown' } };
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
void signalMeterRenderer;
void levelMeterRenderer;
void oldShape;
void invalidCurrentEvidence;
void invalidUnknownSignal;
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
  await writeFile(path.join(consumer, 'index.html'), `<main id="app"></main><script type="module" src="/src/mount.ts"></script>`);
  await writeFile(path.join(consumer, 'src', 'mount.ts'), `import { mount } from 'svelte';
import App from './App.svelte';
mount(App, { target: document.querySelector('#app')! });
`);
  await writeFile(path.join(consumer, 'src', 'App.svelte'), `<script lang="ts">
  import fixtureKit from '@rigplane/external-component-kit-fixture';
  import type {
    ActionRendererLease,
    LevelMeterRendererView,
    SignalMeterRendererView,
  } from '@rigplane/component-kit-api';

  const appearance = fixtureKit.meterAppearances?.fixture;
  if (appearance === undefined) throw new Error('packed meter appearance missing');
  const Signal = appearance.signal;
  const Level = appearance.level;
  const engineering = { kind: 'engineering', unit: 'db' } as const;
  const signals: readonly SignalMeterRendererView[] = [
    {
      kind: 'signal', evidence: { state: 'current', value: -12, domain: engineering },
      scaleMode: 's', displayedFraction: 0.4, peakFraction: 0.6,
      primaryText: 'S7', secondaryText: '−85 dBm', accessibleDescription: 'S meter S7',
      crossoverFraction: 0.55, marks: [{ actual: 0, fraction: 0.55, text: '9' }],
      ticks: [
        { fraction: 0, kind: 'major' }, { fraction: 0.1, kind: 'mid' },
        { fraction: 0.2, kind: 'minor' },
      ],
    },
    {
      kind: 'signal', evidence: { state: 'current', value: -12, domain: engineering },
      scaleMode: 'none', displayedFraction: null, peakFraction: null,
      primaryText: '−12 dB rel S9', secondaryText: 'scale unavailable',
      accessibleDescription: 'S meter minus 12 decibels relative to S9, scale unavailable',
      crossoverFraction: null, marks: [], ticks: [],
    },
    {
      kind: 'signal', evidence: { state: 'current', value: 112, domain: { kind: 'raw' } },
      scaleMode: 'raw', displayedFraction: 0.44, peakFraction: 0.5,
      primaryText: '112', secondaryText: 'uncalibrated',
      accessibleDescription: 'S meter 112 raw, uncalibrated', crossoverFraction: null,
      marks: [], ticks: [],
    },
    {
      kind: 'signal', evidence: { state: 'current', value: 41, domain: { kind: 'unknown' } },
      scaleMode: 'none', displayedFraction: null, peakFraction: null,
      primaryText: '41', secondaryText: 'unit unknown',
      accessibleDescription: 'S meter 41, unit unknown', crossoverFraction: null,
      marks: [], ticks: [],
    },
    {
      kind: 'signal', evidence: { state: 'unknown', domain: { kind: 'unknown' } },
      scaleMode: 'none', displayedFraction: null, peakFraction: null,
      primaryText: '?', secondaryText: 'unit unknown',
      accessibleDescription: 'S meter reading unknown, unit unknown', crossoverFraction: null,
      marks: [], ticks: [],
    },
  ];
  const levels: readonly LevelMeterRendererView[] = [
    {
      kind: 'level', key: 'power', label: 'Po',
      evidence: { state: 'current', value: 50, domain: { kind: 'engineering', unit: 'w' } },
      relevant: true, observed: true, displayedFraction: 0.5, peakFraction: 0.7,
      displayText: '50 W', stateText: '', accessibleDescription: 'Po: Current observation. 50 W',
      gauge: true, fault: false, peakEnabled: true,
    },
    {
      kind: 'level', key: 'alc', label: 'ALC',
      evidence: { state: 'stale', value: 0.2, domain: { kind: 'engineering', unit: 'normalized' } },
      relevant: true, observed: false, displayedFraction: null, peakFraction: null,
      displayText: 'STALE', stateText: 'STALE', accessibleDescription: 'ALC: Stale observation',
      gauge: true, fault: false, peakEnabled: false,
    },
    {
      kind: 'level', key: 'drainCurrent', label: 'Id',
      evidence: { state: 'idle', domain: { kind: 'engineering', unit: 'a' } },
      relevant: false, observed: false, displayedFraction: null, peakFraction: null,
      displayText: 'IDLE', stateText: 'IDLE', gauge: true, fault: false, peakEnabled: false,
    },
    {
      kind: 'level', key: 'drainVoltage', label: 'Vd',
      evidence: { state: 'unknown', domain: { kind: 'unknown' } },
      relevant: true, observed: false, displayedFraction: null, peakFraction: null,
      displayText: 'Vd ?', stateText: '', gauge: false, fault: false, peakEnabled: false,
    },
    {
      kind: 'level', key: 'compression', label: 'COMP',
      evidence: { state: 'unsupported', domain: { kind: 'engineering', unit: 'db' } },
      relevant: true, observed: false, displayedFraction: null, peakFraction: null,
      displayText: '?', stateText: '?', gauge: false, fault: false, peakEnabled: false,
    },
    {
      kind: 'level', key: 'swr', label: 'SWR',
      evidence: { state: 'current', value: 1.5, domain: { kind: 'engineering', unit: 'ratio' } },
      relevant: true, observed: true, displayedFraction: 0.2, peakFraction: null,
      displayText: '1.5', stateText: '', accessibleDescription: 'SWR: Current observation. 1.5',
      gauge: true, fault: false, peakEnabled: false, ratioScale: true,
    },
    {
      kind: 'level', key: 'swr', label: 'SWR',
      evidence: { state: 'current', value: 120, domain: { kind: 'raw' } },
      relevant: true, observed: true, displayedFraction: 0.47, peakFraction: null,
      displayText: '120 raw', stateText: '', accessibleDescription: 'SWR: 120 raw',
      gauge: true, fault: false, peakEnabled: false, ratioScale: false,
    },
  ];
  const counter = globalThis as typeof globalThis & {
    __resetCount: number;
    __disposeReset: () => void;
    __invokeReset: () => void;
  };
  let resetActive = $state(true);
  counter.__resetCount = 0;
  const resetPeak: ActionRendererLease = {
    get active() { return resetActive; },
    get view() { return resetActive ? { label: 'Reset peak', available: true } : undefined; },
    invoke() { if (resetActive) counter.__resetCount += 1; },
    dispose() { resetActive = false; },
  };
  const retainedResetInvoke = resetPeak.invoke.bind(resetPeak);
  counter.__disposeReset = () => resetPeak.dispose();
  counter.__invokeReset = retainedResetInvoke;
</script>

{#each signals as view}<Signal {view} />{/each}
{#each levels as view, index}<Level {view} resetPeak={index === 0 ? resetPeak : undefined} />{/each}
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

  await withStaticServer(path.join(consumer, 'dist'), async (origin) => {
    const browser = await chromium.launch({ headless: true });
    try {
      const page = await browser.newPage();
      const pageErrors = [];
      page.on('pageerror', (error) => pageErrors.push(error));
      await page.addInitScript(() => {
        globalThis.__fixtureTimerCalls = [];
        for (const name of ['setTimeout', 'setInterval', 'requestAnimationFrame']) {
          const original = globalThis[name];
          globalThis[name] = (...args) => {
            globalThis.__fixtureTimerCalls.push(name);
            return Reflect.apply(original, globalThis, args);
          };
        }
      });
      await page.goto(origin, { waitUntil: 'networkidle' });
      const signals = page.locator('[data-fixture-signal]');
      const levels = page.locator('[data-fixture-level]');
      assert.equal(await signals.count(), 5);
      assert.equal(await levels.count(), 7);
      assert.equal(await signals.nth(0).getAttribute('data-domain'), 'engineering:db');
      assert.equal(await signals.nth(0).getAttribute('data-tick-kinds'), 'major,mid,minor');
      assert.equal(await signals.nth(1).getAttribute('data-scale'), 'none');
      assert.equal(await signals.nth(1).getAttribute('data-value'), '-12');
      assert.equal(await signals.nth(2).getAttribute('data-domain'), 'raw');
      assert.equal(await signals.nth(3).getAttribute('data-domain'), 'unknown');
      assert.equal(await signals.nth(3).getAttribute('data-value'), '41');
      assert.equal(await signals.nth(4).getAttribute('data-value'), null);
      assert.equal(await page.locator('[data-fixture-level="alc"]').getAttribute('data-state'), 'stale');
      assert.equal(await page.locator('[data-fixture-level="alc"]').getAttribute('data-value'), '0.2');
      assert.equal(await page.locator('[data-fixture-level="drainCurrent"]').getAttribute('data-value'), null);
      assert.deepEqual(
        await page.locator('[data-fixture-level="swr"]').evaluateAll((nodes) =>
          nodes.map((node) => node.getAttribute('data-ratio-scale'))),
        ['true', 'false'],
      );
      assert.equal(await page.getByRole('button', { name: 'Reset peak' }).count(), 1);
      await page.getByRole('button', { name: 'Reset peak' }).click();
      assert.equal(await page.evaluate(() => globalThis.__resetCount), 1);
      await page.evaluate(() => globalThis.__disposeReset());
      await page.evaluate(() => globalThis.__invokeReset());
      assert.equal(await page.evaluate(() => globalThis.__resetCount), 1);
      assert.equal(await page.getByRole('button', { name: 'Reset peak' }).count(), 0);
      assert.deepEqual(await page.evaluate(() => globalThis.__fixtureTimerCalls), []);
      assert.deepEqual(pageErrors, []);
    } finally {
      await browser.close();
    }
  });

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
  const installedFixture = JSON.parse(await readFile(
    path.join(consumer, 'node_modules', '@rigplane', 'external-component-kit-fixture', 'package.json'),
    'utf8',
  ));
  const installedSvelte = JSON.parse(await readFile(
    path.join(consumer, 'node_modules', 'svelte', 'package.json'),
    'utf8',
  ));
  assert.equal(installedApi.version, '0.3.0');
  assert.equal(installedApi.peerDependencies.svelte, '>=5.45.2 <6');
  assert.equal(installedFixture.peerDependencies['@rigplane/component-kit-api'], '0.3.0');

  console.log('component-kit-api portable package verification: OK');
  console.log(`runtime exports: COMPONENT_KIT_API_VERSION, defineComponentKit`);
  console.log(`consumer peer: svelte@${installedSvelte.version} (${sveltePackages.length} physical install)`);
  console.log(`API declarations: ${apiDeclarations.length}`);
  console.log(`fixture declarations: ${fixtureDeclarations.length}`);
  console.log(`API tarball files (${apiPack.files.length}): ${packageFiles(apiPack).join(', ')}`);
  console.log(`fixture tarball files (${fixturePack.files.length}): ${packageFiles(fixturePack).join(', ')}`);
  console.log(`API tarball SHA-256: ${apiTarballSha256}`);
  console.log(`fixture tarball SHA-256 (browser mounted): ${fixtureTarballSha256}`);
} finally {
  await rm(temporaryRoot, { recursive: true, force: true });
}
