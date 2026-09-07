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

function interfaceMembers(source, interfaceName) {
  const parsed = ts.createSourceFile(
    'component-kit-api.d.ts', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS,
  );
  const declaration = parsed.statements.find((statement) =>
    ts.isInterfaceDeclaration(statement) && statement.name.text === interfaceName
  );
  assert(declaration && ts.isInterfaceDeclaration(declaration));
  return declaration.members.map((member) => {
    assert(ts.isPropertySignature(member));
    assert(member.name && ts.isIdentifier(member.name));
    return { name: member.name.text, optional: member.questionToken !== undefined };
  });
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

async function assertHostedFaceSources() {
  const faceFiles = ['FaceA.svelte', 'FaceB.svelte']
    .map((name) => path.join(fixtureRoot, 'src', name));
  const sources = await Promise.all(faceFiles.map((file) => readFile(file, 'utf8')));
  const operations = [
    'split', 'dualWatch', 'activeReceiver', 'equalize', 'swap',
    'quickSplit', 'quickDualWatch', 'speak',
  ];
  const txSeats = [
    'rfPower', 'micGain', 'driveGain', 'voxGain', 'antiVoxGain',
    'voxDelay', 'compressorLevel', 'monitorLevel',
  ];
  const stationMeters = [
    'signal', 'power', 'swr', 'alc', 'drainCurrent', 'drainVoltage', 'compression',
  ];

  for (const [index, source] of sources.entries()) {
    const imports = [...source.matchAll(/\bfrom\s+['"]([^'"]+)['"]/gu)]
      .map((match) => match[1]);
    assert.deepEqual(imports, ['@rigplane/component-kit-api']);
    assert(source.includes('instruments.receiver !== null'));
    assert(source.includes('receiver.mainFrequency'));
    assert(source.includes('receiver.mainSMeter'));
    assert(source.includes('receiver.subFrequency !== null'));
    assert(source.includes('receiver.subSMeter !== null'));
    assert(source.includes('instruments.vfoOperations !== null'));
    assert(source.includes('instruments.txAux !== null'));
    assert(source.includes('instruments.stationMeters !== null'));
    for (const field of operations) {
      assert.equal(
        [...source.matchAll(new RegExp(`operations\\.${field}\\(`, 'gu'))].length,
        1,
        `${path.basename(faceFiles[index])} must arrange ${field} exactly once`,
      );
    }
    for (const field of txSeats) {
      assert.equal(
        [...source.matchAll(new RegExp(`tx\\.${field}\\(`, 'gu'))].length,
        1,
        `${path.basename(faceFiles[index])} must arrange ${field} exactly once`,
      );
    }
    for (const field of stationMeters) {
      assert.equal(
        [...source.matchAll(new RegExp(`station\\.${field}\\(\\)`, 'gu'))].length,
        1,
        `${path.basename(faceFiles[index])} must place ${field} exactly once`,
      );
    }
    for (const forbidden of [
      '$lib', '/frontend/src/', 'InstrumentComposition', 'SignalMeterFrame',
      'vfoFreqHook', 'onFreqChange',
    ]) assert(!source.includes(forbidden));
  }
  assert(sources[0].indexOf('data-family="receiver"') < sources[0].indexOf('data-family="txAux"'));
  assert(sources[1].indexOf('data-family="txAux"') < sources[1].indexOf('data-family="receiver"'));
  assert(sources[0].includes("tx.rfPower({ form: 'hbar'"));
  assert(sources[1].includes("tx.rfPower({ form: 'knob'"));
  assert(sources.every((source) => source.includes('compact:')));
  assert(sources.every((source) => source.includes('showLabel:')));
  assert(sources.every((source) => source.includes('showValue:')));
  const stationOrder = (source) => stationMeters
    .map((field) => source.indexOf(`station.${field}()`));
  assert(stationOrder(sources[0]).every((index) => index >= 0));
  assert.notDeepEqual(stationOrder(sources[0]), stationOrder(sources[1]));
  const fixtureSource = await readFile(path.join(fixtureRoot, 'src', 'index.ts'), 'utf8');
  assert.match(fixtureSource, /id: 'meters', surfaces: \['meters'\]/u);
  assert.match(fixtureSource, /requiredSemanticSurfaces: \['vfo', 'txAux', 'meters'\]/u);
  assert.match(fixtureSource, /requiredSemanticSurfaces: \['txAux', 'vfo', 'meters'\]/u);
}

async function assertFixturePublicImports() {
  const sourceFiles = [
    'index.ts', 'FaceA.svelte', 'FaceB.svelte',
    'FixtureScalarRenderer.svelte', 'FixtureFrequencyRenderer.svelte',
    'FixtureActionRenderer.svelte', 'FixtureToggleRenderer.svelte',
    'FixtureChoiceRenderer.svelte', 'FixtureSignalMeter.svelte', 'FixtureLevelMeter.svelte',
  ];
  for (const name of sourceFiles) {
    const source = await readFile(path.join(fixtureRoot, 'src', name), 'utf8');
    const imports = [...source.matchAll(/\bfrom\s+['"]([^'"]+)['"]/gu)]
      .map((match) => match[1]);
    for (const specifier of imports) {
      if (specifier.startsWith('.')) {
        assert.equal(name, 'index.ts');
        assert.match(specifier, /^\.\/[^/]+\.svelte$/u);
      } else {
        assert(
          specifier === '@rigplane/component-kit-api' || specifier === 'svelte',
          `Unexpected fixture source import ${specifier} in ${name}`,
        );
      }
    }
    for (const forbidden of ['$lib', '/frontend/src/', '@rigplane/component-kit-api/']) {
      assert(!source.includes(forbidden));
    }
  }
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
await assertHostedFaceSources();
await assertFixturePublicImports();

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
    'dist/types/src/primitives/scalar/continuous-scalar.svelte.d.ts',
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
  const scalarDeclarationSource = await readFile(
    path.join(
      packageRoot, 'dist', 'types', 'src', 'primitives', 'scalar',
      'continuous-scalar.svelte.d.ts',
    ),
    'utf8',
  );
  assert(apiDeclarationSource.includes('ScalarRendererSeat'));
  assert(scalarDeclarationSource.includes('ContinuousScalarRendererSeat'));
  assert(!scalarDeclarationSource.includes('ContinuousScalarBinding'));
  assert(!scalarDeclarationSource.includes('createContinuousScalar('));
  assert(!scalarDeclarationSource.includes('createContinuousScalarRendererSeat('));
  assert(!scalarDeclarationSource.includes('destroy(): void'));
  assert(!scalarDeclarationSource.includes("'availability' | 'terminal' | 'owner-dispose'"));
  assert(!apiDeclarationSource.includes('PresentationHostMode'));
  assert(apiDeclarationSource.includes("'external-instruments-v1'"));
  assert(apiDeclarationSource.includes('HostedFacePropsV1'));
  assert(apiDeclarationSource.includes('ReceiverFrequencyHandleV1'));
  assert(apiDeclarationSource.includes('VfoOperationHandleV1'));
  assert(apiDeclarationSource.includes('TxAuxInstrumentFamilyV1'));
  assert(apiDeclarationSource.includes('StationMeterInstrumentFamilyV1'));
  assert.match(apiDeclarationSource, /type StationMeterHandleV1 = Snippet<\[\]>/u);
  assert(!apiDeclarationSource.includes('ReceiverMeterHandleV1'));
  assert(!apiDeclarationSource.includes('ReceiverVfoOperationsHandleV1'));
  assert(!apiDeclarationSource.includes('SignalMeterFrame'));
  assert.deepEqual(interfaceMembers(apiDeclarationSource, 'ReceiverInstrumentFamilyV1'), [
    { name: 'mainFrequency', optional: false },
    { name: 'subFrequency', optional: false },
    { name: 'mainSMeter', optional: false },
    { name: 'subSMeter', optional: false },
  ]);
  assert.deepEqual(interfaceMembers(apiDeclarationSource, 'ReceiverFrequencyPresentationV1'), [
    { name: 'compact', optional: true },
  ]);
  assert.deepEqual(interfaceMembers(apiDeclarationSource, 'VfoOperationInstrumentFamilyV1'), [
    { name: 'split', optional: false },
    { name: 'dualWatch', optional: false },
    { name: 'activeReceiver', optional: false },
    { name: 'equalize', optional: false },
    { name: 'swap', optional: false },
    { name: 'quickSplit', optional: false },
    { name: 'quickDualWatch', optional: false },
    { name: 'speak', optional: false },
  ]);
  assert.deepEqual(interfaceMembers(apiDeclarationSource, 'TxAuxScalarPresentationV1'), [
    { name: 'form', optional: true },
    { name: 'compact', optional: true },
    { name: 'showLabel', optional: true },
    { name: 'showValue', optional: true },
  ]);
  assert.deepEqual(interfaceMembers(apiDeclarationSource, 'TxAuxInstrumentFamilyV1'), [
    { name: 'rfPower', optional: false },
    { name: 'micGain', optional: false },
    { name: 'driveGain', optional: false },
    { name: 'voxGain', optional: false },
    { name: 'antiVoxGain', optional: false },
    { name: 'voxDelay', optional: false },
    { name: 'compressorLevel', optional: false },
    { name: 'monitorLevel', optional: false },
  ]);
  assert.deepEqual(interfaceMembers(apiDeclarationSource, 'StationMeterInstrumentFamilyV1'), [
    { name: 'signal', optional: false },
    { name: 'power', optional: false },
    { name: 'swr', optional: false },
    { name: 'alc', optional: false },
    { name: 'drainCurrent', optional: false },
    { name: 'drainVoltage', optional: false },
    { name: 'compression', optional: false },
  ]);
  assert.deepEqual(interfaceMembers(apiDeclarationSource, 'HostedInstrumentFamiliesV1'), [
    { name: 'receiver', optional: false },
    { name: 'vfoOperations', optional: false },
    { name: 'txAux', optional: false },
    { name: 'stationMeters', optional: false },
  ]);
  assert.deepEqual(interfaceMembers(apiDeclarationSource, 'HostedFaceAppearanceIdsV1'), [
    { name: 'scalar', optional: false },
    { name: 'frequency', optional: false },
    { name: 'finite', optional: false },
    { name: 'meter', optional: false },
  ]);
  assert.deepEqual(interfaceMembers(apiDeclarationSource, 'HostedFacePresentationV1'), [
    { name: 'hostMode', optional: false },
    { name: 'id', optional: false },
    { name: 'layoutId', optional: false },
    { name: 'loader', optional: false },
    { name: 'resources', optional: false },
    { name: 'appearances', optional: false },
  ]);
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
  const fixtureRuntime = await readFile(path.join(fixtureRoot, 'dist', 'index.js'), 'utf8');
  assert(fixtureRuntime.includes('external-instruments-v1'));
  assert(fixtureRuntime.includes('fixture-face-a'));
  assert(fixtureRuntime.includes('fixture-face-b'));
  assert(fixtureRuntime.includes('data-external-face'));
  for (const marker of [
    'data-fixture-scalar', 'data-fixture-frequency', 'data-fixture-action',
    'data-fixture-toggle', 'data-fixture-choice',
  ]) assert(fixtureRuntime.includes(marker));
  assert(!fixtureRuntime.includes('$lib'));
  assert(!fixtureRuntime.includes('/frontend/src/'));
  assert(!fixtureRuntime.includes('InstrumentComposition'));
  assert(!fixtureRuntime.includes('SignalMeterFrame'));
  assert(!fixtureRuntime.includes('activateComponentKits'));
  assert(!fixtureRuntime.includes('registerLayouts'));
  assert(!fixtureRuntime.includes('commitExternalPresentationBatch'));

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
  type HostedComponentKitDeclarationV1,
  type HostedFaceComponentV1,
  type HostedFacePresentationV1,
  type HostedFacePropsV1,
  type LayoutManifest,
  type LevelMeterRendererProps,
  type MeterAppearance,
  type MeterNumericEvidence,
  type PresentationDeclaration,
  type ScalarAppearance,
  type ScalarRendererSeat,
  type SignalMeterRendererProps,
  type SignalMeterEvidence,
  type StationMeterHandleV1,
  type StationMeterInstrumentFamilyV1,
  type TxAuxScalarPresentationV1,
} from '@rigplane/component-kit-api';
import type { Component } from 'svelte';
import fixtureKit, {
  FixtureFaceA,
  FixtureFaceB,
  faceAPresentation,
  faceBPresentation,
  reservedPresentation,
} from '@rigplane/external-component-kit-fixture';

const declaration: HostedComponentKitDeclarationV1 = fixtureKit;
const scalar: ScalarAppearance | undefined = declaration.scalarAppearances?.fixture;
declare const scalarRendererSeat: ScalarRendererSeat;
const scalarRendererLease = scalarRendererSeat.attachRenderer();
scalarRendererSeat.cancel('authority');
void [scalarRendererSeat.view, scalarRendererLease.view];
const layout: LayoutManifest | undefined = declaration.layouts?.[0];
const finite: FiniteControlAppearance | undefined = declaration.finiteControlAppearances?.fixture;
const numericChoiceRenderer: Component<ChoiceRendererProps<number>> | undefined = finite?.choice;
const meter: MeterAppearance | undefined = declaration.meterAppearances?.fixture;
const signalMeterRenderer: Component<SignalMeterRendererProps> | undefined = meter?.signal;
const levelMeterRenderer: Component<LevelMeterRendererProps> | undefined = meter?.level;
const oldShape: ComponentKitDeclaration = {
  apiVersion: 1,
  id: 'old-shape',
  presentations: [reservedPresentation],
};
const oldPresentation: PresentationDeclaration | undefined = oldShape.presentations?.[0];
const faceA: HostedFaceComponentV1 = FixtureFaceA;
const faceB: HostedFaceComponentV1 = FixtureFaceB;
const hostedA: HostedFacePresentationV1 = faceAPresentation;
const hostedB: HostedFacePresentationV1 = faceBPresentation;
const inferredHosted = defineComponentKit({
  apiVersion: 1,
  id: 'inferred-hosted',
  presentations: [faceAPresentation] as const,
  extensionMarker: 'preserved' as const,
});
const inferredMarker: 'preserved' = inferredHosted.extensionMarker;
const hosted = declaration.presentations?.[0];
if (hosted !== undefined && 'hostMode' in hosted) {
  const mode: 'external-instruments-v1' = hosted.hostMode;
  void mode;
}
const txPresentation: TxAuxScalarPresentationV1 = {
  form: 'knob', compact: true, showLabel: false, showValue: true,
};
declare const stationMeter: StationMeterHandleV1;
const completeStationMeters: StationMeterInstrumentFamilyV1 = {
  signal: stationMeter, power: stationMeter, swr: stationMeter, alc: stationMeter,
  drainCurrent: stationMeter, drainVoltage: stationMeter, compression: stationMeter,
};
const nullableStationMeters: HostedFacePropsV1['instruments'] = {
  receiver: null, vfoOperations: null, txAux: null, stationMeters: null,
};
function inspectHostedFace(props: HostedFacePropsV1): void {
  const receiver = props.instruments.receiver;
  const operations = props.instruments.vfoOperations;
  const txAux = props.instruments.txAux;
  const stationMeters = props.instruments.stationMeters;
  if (receiver !== null) void [receiver.mainFrequency, receiver.subFrequency, receiver.mainSMeter];
  if (operations !== null) void [operations.split, operations.equalize, operations.speak];
  if (txAux !== null) void [txAux.rfPower, txAux.voxDelay, txAux.monitorLevel];
  if (stationMeters !== null) void [
    stationMeters.signal, stationMeters.power, stationMeters.swr, stationMeters.alc,
    stationMeters.drainCurrent, stationMeters.drainVoltage, stationMeters.compression,
  ];
}
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
void numericChoiceRenderer;
void signalMeterRenderer;
void levelMeterRenderer;
void oldShape;
void oldPresentation;
void faceA;
void faceB;
void hostedA;
void hostedB;
void inferredMarker;
void txPresentation;
void completeStationMeters;
void nullableStationMeters;
void inspectHostedFace;
void invalidCurrentEvidence;
void invalidUnknownSignal;
void oldChoiceOption;
void reasonedChoiceOption;
void inspectChoice;
export const installedKit = defineComponentKit(declaration);
export const installedOldKit = defineComponentKit(oldShape);
export const apiVersion = COMPONENT_KIT_API_VERSION;
`);
  await writeFile(path.join(consumer, 'src', 'private-root-negative.ts'), `// @ts-expect-error host authority is not a public root export
import type { FiniteRendererContext } from '@rigplane/component-kit-api';
// @ts-expect-error host factories are not public root exports
import type { createFiniteRendererContext, createChoiceRendererSeat } from '@rigplane/component-kit-api';
// @ts-expect-error declaration-tree subpaths are not exported
import type { ChoiceRendererInput } from '@rigplane/component-kit-api/dist/types/src/primitives/control-instruments/control-instrument-renderer.svelte';
// @ts-expect-error private hosted composition types are not public root exports
import type { InstrumentComposition, SignalMeterFrame, FrequencyInstrumentBinding, ReceiverSMeterRenderer } from '@rigplane/component-kit-api';
import type {
  HostedFaceComponentV1,
  HostedFacePresentationV1,
  HostedInstrumentFamiliesV1,
  ReceiverFrequencyHandleV1,
  ReceiverFrequencyPresentationV1,
  ReceiverInstrumentFamilyV1,
  ReceiverSignalMeterHandleV1,
  StationMeterHandleV1,
  StationMeterInstrumentFamilyV1,
  TxAuxInstrumentFamilyV1,
  TxAuxScalarHandleV1,
  TxAuxScalarPresentationV1,
  VfoOperationHandleV1,
  VfoOperationInstrumentFamilyV1,
  ScalarRendererSeat,
} from '@rigplane/component-kit-api';
import type { Snippet } from 'svelte';

const wrongMode = 'instrument-handles';
declare const scalarRendererSeat: ScalarRendererSeat;
// @ts-expect-error renderer seats cannot destroy their host-owned scalar owner
scalarRendererSeat.destroy();
// @ts-expect-error external faces have one admitted host discriminator
const wrongHostMode: HostedFacePresentationV1['hostMode'] = wrongMode;
// @ts-expect-error the internal DOM hook is not a hosted-seat presentation option
const privateFrequencyHook: ReceiverFrequencyPresentationV1 = { vfoFreqHook: true };
// @ts-expect-error TX auxiliaries admit continuous hbar and knob forms only
const discreteTxControl: TxAuxScalarPresentationV1 = { form: 'discrete' };
// @ts-expect-error appearance IDs are resolved by the host declaration, not by a seat call
const invocationAppearance: TxAuxScalarPresentationV1 = { appearanceId: 'fixture' };
declare const frequency: ReceiverFrequencyHandleV1;
declare const meter: ReceiverSignalMeterHandleV1;
declare const scalar: TxAuxScalarHandleV1;
declare const operation: VfoOperationHandleV1;
declare const stationMeter: StationMeterHandleV1;
declare const face: HostedFaceComponentV1;
// @ts-expect-error the hosted discriminator is required
const missingHostMode = { id: 'bad', layoutId: 'bad', loader: async () => face, resources: [], appearances: { scalar: 'x', frequency: 'x', finite: 'x', meter: 'x' } } satisfies HostedFacePresentationV1;
// @ts-expect-error receiver seats are exact and do not carry host values
const receiverWithRawValue: ReceiverInstrumentFamilyV1 = { mainFrequency: frequency, subFrequency: null, mainSMeter: meter, subSMeter: null, confirmedHz: 14074000 };
// @ts-expect-error SUB structural absence is represented by required null fields
const receiverWithoutSub: ReceiverInstrumentFamilyV1 = { mainFrequency: frequency, mainSMeter: meter };
// @ts-expect-error every family key is required even though each family is nullable
const propsWithoutOperations: HostedInstrumentFamiliesV1 = { receiver: null, txAux: null, stationMeters: null };
// @ts-expect-error family absence is null, never undefined
const undefinedReceiver: HostedInstrumentFamiliesV1 = { receiver: undefined, vfoOperations: null, txAux: null, stationMeters: null };
// @ts-expect-error every family key is required even though each family is nullable
const missingStationFamily: HostedInstrumentFamiliesV1 = { receiver: null, vfoOperations: null, txAux: null };
// @ts-expect-error family absence is null, never undefined
const undefinedStationFamily: HostedInstrumentFamiliesV1 = { receiver: null, vfoOperations: null, txAux: null, stationMeters: undefined };
// @ts-expect-error all eight named VFO operation seats are required when admitted
const operationsWithoutSpeak: VfoOperationInstrumentFamilyV1 = { split: operation, dualWatch: operation, activeReceiver: operation, equalize: operation, swap: operation, quickSplit: operation, quickDualWatch: operation };
// @ts-expect-error operation-seat absence is null, never undefined
const undefinedOperation: VfoOperationInstrumentFamilyV1 = { split: undefined, dualWatch: null, activeReceiver: null, equalize: null, swap: null, quickSplit: null, quickDualWatch: null, speak: null };
// @ts-expect-error unoffered aggregate members are not admitted operation seats
const operationsWithAggregate = { split: null, dualWatch: null, activeReceiver: null, equalize: null, swap: null, quickSplit: null, quickDualWatch: null, speak: null, standard: operation } satisfies VfoOperationInstrumentFamilyV1;
declare const oldAggregateOperation: Snippet<[appearance: 'semantic' | 'sdr' | 'standard']>;
// @ts-expect-error old aggregate appearance vocabulary is not an opaque operation seat
const publicOperation: VfoOperationHandleV1 = oldAggregateOperation;
// @ts-expect-error all eight named TX scalar seats are required when admitted
const txWithoutMonitor: TxAuxInstrumentFamilyV1 = { rfPower: scalar, micGain: scalar, driveGain: scalar, voxGain: scalar, antiVoxGain: scalar, voxDelay: scalar, compressorLevel: scalar };
// @ts-expect-error all seven named station meter handles are required when admitted
const stationWithoutCompression: StationMeterInstrumentFamilyV1 = { signal: stationMeter, power: stationMeter, swr: stationMeter, alc: stationMeter, drainCurrent: stationMeter, drainVoltage: stationMeter };
// @ts-expect-error station meter handle absence is not encoded as undefined
const undefinedStationSignal: StationMeterInstrumentFamilyV1 = { signal: undefined, power: stationMeter, swr: stationMeter, alc: stationMeter, drainCurrent: stationMeter, drainVoltage: stationMeter, compression: stationMeter };
// @ts-expect-error aggregate meter calls are not admitted in the atomic station family
const stationWithAggregate = { signal: stationMeter, power: stationMeter, swr: stationMeter, alc: stationMeter, drainCurrent: stationMeter, drainVoltage: stationMeter, compression: stationMeter, meters: stationMeter } satisfies StationMeterInstrumentFamilyV1;
declare const privateStationHandle: Snippet<[frame: { readonly privateFrame: true }]>;
// @ts-expect-error opaque station meter handles take no frames or reset leases
const publicStationHandle: StationMeterHandleV1 = privateStationHandle;
declare const privateResetStationHandle: Snippet<[resetLease: { readonly privateReset: true }]>;
// @ts-expect-error opaque station meter handles cannot expose private reset leases
const publicResetStationHandle: StationMeterHandleV1 = privateResetStationHandle;
// @ts-expect-error layoutId is required on hosted declarations
const missingLayout = { hostMode: 'external-instruments-v1', id: 'bad', loader: async () => face, resources: [], appearances: { scalar: 'x', frequency: 'x', finite: 'x', meter: 'x' } } satisfies HostedFacePresentationV1;
// @ts-expect-error resources are required on hosted declarations
const missingResources = { hostMode: 'external-instruments-v1', id: 'bad', layoutId: 'bad', loader: async () => face, appearances: { scalar: 'x', frequency: 'x', finite: 'x', meter: 'x' } } satisfies HostedFacePresentationV1;
// @ts-expect-error scalar appearance is required
const missingScalarAppearance = { hostMode: 'external-instruments-v1', id: 'bad', layoutId: 'bad', loader: async () => face, resources: [], appearances: { frequency: 'x', finite: 'x', meter: 'x' } } satisfies HostedFacePresentationV1;
// @ts-expect-error frequency appearance is required
const missingFrequencyAppearance = { hostMode: 'external-instruments-v1', id: 'bad', layoutId: 'bad', loader: async () => face, resources: [], appearances: { scalar: 'x', finite: 'x', meter: 'x' } } satisfies HostedFacePresentationV1;
// @ts-expect-error finite appearance is required
const missingFiniteAppearance = { hostMode: 'external-instruments-v1', id: 'bad', layoutId: 'bad', loader: async () => face, resources: [], appearances: { scalar: 'x', frequency: 'x', meter: 'x' } } satisfies HostedFacePresentationV1;
// @ts-expect-error meter appearance is required
const missingMeterAppearance = { hostMode: 'external-instruments-v1', id: 'bad', layoutId: 'bad', loader: async () => face, resources: [], appearances: { scalar: 'x', frequency: 'x', finite: 'x' } } satisfies HostedFacePresentationV1;
// @ts-expect-error hosted declarations must provide a component loader
const missingLoader = { hostMode: 'external-instruments-v1', id: 'bad', layoutId: 'bad', resources: [], appearances: { scalar: 'x', frequency: 'x', finite: 'x', meter: 'x' } } satisfies HostedFacePresentationV1;

export type PrivateRootMustStayUnavailable = [
  FiniteRendererContext,
  typeof createFiniteRendererContext,
  typeof createChoiceRendererSeat,
  ChoiceRendererInput<string>,
  InstrumentComposition,
  SignalMeterFrame,
  FrequencyInstrumentBinding,
  ReceiverSMeterRenderer,
];
void [wrongHostMode, privateFrequencyHook, discreteTxControl, invocationAppearance, missingHostMode];
void [receiverWithRawValue, receiverWithoutSub, propsWithoutOperations, undefinedReceiver, missingStationFamily, undefinedStationFamily];
void [operationsWithoutSpeak, undefinedOperation, operationsWithAggregate, publicOperation, txWithoutMonitor];
void [stationWithoutCompression, undefinedStationSignal, stationWithAggregate, publicStationHandle, publicResetStationHandle];
void [missingLayout, missingResources, missingScalarAppearance, missingFrequencyAppearance];
void [missingFiniteAppearance, missingMeterAppearance, missingLoader];
`);
  await writeFile(path.join(consumer, 'index.html'), `<main id="app"></main><script type="module" src="/src/mount.ts"></script>`);
  await writeFile(path.join(consumer, 'src', 'mount.ts'), `import { mount } from 'svelte';
import App from './App.svelte';
mount(App, { target: document.querySelector('#app')! });
`);
  await writeFile(path.join(consumer, 'src', 'App.svelte'), `<script lang="ts">
  import fixtureKit, {
    FixtureFaceA,
    FixtureFaceB,
  } from '@rigplane/external-component-kit-fixture';
  import type {
    ActionRendererLease,
    ChoiceRendererLease,
    FiniteChoiceValue,
    FrequencyInteraction,
    FrequencyReadoutModel,
    LevelMeterRendererView,
    ScalarRendererLease,
    ScalarRendererSeat,
    ScalarRendererView,
    SignalMeterRendererView,
    ToggleRendererLease,
  } from '@rigplane/component-kit-api';

  const scalarAppearance = fixtureKit.scalarAppearances?.fixture;
  const frequency = fixtureKit.frequencyReadouts?.fixture;
  const finite = fixtureKit.finiteControlAppearances?.fixture;
  const meter = fixtureKit.meterAppearances?.fixture;
  if (scalarAppearance?.hbar === undefined) throw new Error('packed scalar appearance missing');
  if (frequency === undefined) throw new Error('packed frequency appearance missing');
  if (finite === undefined) throw new Error('packed finite appearance missing');
  if (meter === undefined) throw new Error('packed meter appearance missing');
  const Scalar = scalarAppearance.hbar;
  const Frequency = frequency;
  const Action = finite.action;
  const Toggle = finite.toggle;
  const Choice = finite.choice;
  const Signal = meter.signal;
  const Level = meter.level;

  const rendererHarness = globalThis as typeof globalThis & {
    __scalarStats: () => Readonly<{
      attachments: number; attachArguments: number[]; disposals: number;
      nativeInputs: number[]; keys: string[];
    }>;
    __frequencyStats: () => Readonly<{ clicks: number; wheels: number; keys: number }>;
    __finiteStats: () => Readonly<{
      actions: number; toggles: number; choices: FiniteChoiceValue[]; disposals: number;
    }>;
    __hideConcreteRenderers: () => void;
  };
  let scalarPresent = $state(true);
  let finitePresent = $state(true);
  const scalarStats = {
    attachments: 0, attachArguments: [] as number[], disposals: 0,
    nativeInputs: [] as number[], keys: [] as string[],
  };
  const scalarView: ScalarRendererView = {
    domain: { min: 0, max: 100, step: 5, defaultValue: 25, fineStepDivisor: 10 },
    domainValid: true, canonical: 25, draft: null, displayed: 25, interactionBase: 25,
    editable: true, busy: false, interaction: 'idle', evidence: 'reading',
    reading: { status: 'known', value: 25 }, confirmed: null, target: null, requested: null,
    phase: null, error: null, presentation: null, announcement: null,
  };
  const scalarLease: ScalarRendererLease = {
    view: scalarView,
    beginPointer: () => null,
    pointer: () => {},
    endPointer: () => {},
    cancelPointer: () => {},
    nativeInput: (candidate) => scalarStats.nativeInputs.push(candidate),
    wheel: () => {},
    key: ({ key }) => { scalarStats.keys.push(key); return true; },
    reset: () => {},
    cancel: () => {},
    dispose: () => { scalarStats.disposals += 1; },
  };
  const scalarSeat: ScalarRendererSeat = {
    view: scalarView,
    attachRenderer(...args: []) {
      scalarStats.attachments += 1;
      scalarStats.attachArguments.push(args.length);
      return scalarLease;
    },
    cancel: () => {},
  };
  rendererHarness.__scalarStats = () => scalarStats;

  const digit = { char: '7', multiplier: 1_000_000, digitIndex: 0 };
  const frequencyStats = { clicks: 0, wheels: 0, keys: 0 };
  const frequencyModel: FrequencyReadoutModel = {
    confirmedHz: 7_100_000, displayHz: 7_100_000, pendingDisplayHz: null,
    shownHz: 7_100_000, source: 'confirmed', status: 'confirmed', known: true,
    digits: [digit], groups: { mhz: [digit], khz: [], hz: [] },
    textGroups: { mhz: '7', khz: '100', hz: '000' },
  };
  const frequencyInteraction: FrequencyInteraction = {
    inert: false, selectedDigitIndex: 0, hoveredDigitIndex: null,
    handleWheel: () => { frequencyStats.wheels += 1; },
    handleDigitClick: () => { frequencyStats.clicks += 1; },
    handleKeyDown: () => { frequencyStats.keys += 1; },
    handleDigitEnter: () => {},
    handleDigitLeave: () => {},
    isSelected: () => true,
    isHovered: () => false,
  };
  rendererHarness.__frequencyStats = () => frequencyStats;

  const finiteStats = {
    actions: 0, toggles: 0, choices: [] as FiniteChoiceValue[], disposals: 0,
  };
  const actionLease: ActionRendererLease = {
    active: true,
    view: { label: 'Fixture action', available: true },
    invoke: () => { finiteStats.actions += 1; },
    dispose: () => { finiteStats.disposals += 1; },
  };
  const toggleLease: ToggleRendererLease = {
    active: true,
    view: { label: 'Fixture toggle', available: true, confirmed: false },
    invoke: () => { finiteStats.toggles += 1; },
    dispose: () => { finiteStats.disposals += 1; },
  };
  const choiceLease: ChoiceRendererLease<FiniteChoiceValue> = {
    active: true,
    view: {
      label: 'Fixture choice', available: true, reading: { status: 'known', value: 'USB' },
      selected: 'USB', options: [
        { value: 'USB', label: 'USB' },
        { value: 'LSB', label: 'LSB', disabled: true, disabledReason: 'Unavailable' },
      ],
    },
    invoke: (value) => finiteStats.choices.push(value),
    dispose: () => { finiteStats.disposals += 1; },
  };
  rendererHarness.__finiteStats = () => finiteStats;
  rendererHarness.__hideConcreteRenderers = () => {
    scalarPresent = false;
    finitePresent = false;
  };

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

  let face = $state<'a' | 'b'>('a');
  let receiverPresent = $state(true);
  let subPresent = $state(true);
  let operationsPresent = $state(true);
  let txPresent = $state(true);
  let stationMetersPresent = $state(true);
  let hiddenOperation = $state<string | null>(null);
  let operationInvocations = $state(0);
  const harness = globalThis as typeof globalThis & {
    __setFace: (value: 'a' | 'b') => void;
    __setFamily: (family: 'receiver' | 'vfoOperations' | 'txAux' | 'stationMeters', present: boolean) => void;
    __setSub: (present: boolean) => void;
    __hideOperation: (name: string | null) => void;
    __operationInvocations: () => number;
  };
  harness.__setFace = (value) => { face = value; };
  harness.__setFamily = (family, present) => {
    if (family === 'receiver') receiverPresent = present;
    if (family === 'vfoOperations') operationsPresent = present;
    if (family === 'txAux') txPresent = present;
    if (family === 'stationMeters') stationMetersPresent = present;
  };
  harness.__setSub = (present) => { subPresent = present; };
  harness.__hideOperation = (name) => { hiddenOperation = name; };
  harness.__operationInvocations = () => operationInvocations;
</script>

{#snippet mainFrequency(p = {})}<span data-seat="mainFrequency" data-compact={p.compact ?? false}>MAIN</span>{/snippet}
{#snippet subFrequency(p = {})}<span data-seat="subFrequency" data-compact={p.compact ?? false}>SUB</span>{/snippet}
{#snippet mainSMeter()}<span data-seat="mainSMeter">S MAIN</span>{/snippet}
{#snippet subSMeter()}<span data-seat="subSMeter">S SUB</span>{/snippet}
{#snippet split()}<button data-seat="split" disabled>Split</button>{/snippet}
{#snippet dualWatch()}<button data-seat="dualWatch">Dual watch</button>{/snippet}
{#snippet activeReceiver()}<button data-seat="activeReceiver">Active receiver</button>{/snippet}
{#snippet equalize()}<button data-seat="equalize" onclick={() => operationInvocations += 1}>Equalize</button>{/snippet}
{#snippet swap()}<button data-seat="swap">Swap</button>{/snippet}
{#snippet quickSplit()}<button data-seat="quickSplit">Quick split</button>{/snippet}
{#snippet quickDualWatch()}<button data-seat="quickDualWatch">Quick dual watch</button>{/snippet}
{#snippet speak()}<button data-seat="speak">Speak</button>{/snippet}
{#snippet rfPower(p = {})}<span data-seat="rfPower" data-form={p.form}>RF power</span>{/snippet}
{#snippet micGain(p = {})}<span data-seat="micGain" data-form={p.form}>Mic gain</span>{/snippet}
{#snippet driveGain(p = {})}<span data-seat="driveGain" data-form={p.form}>Drive</span>{/snippet}
{#snippet voxGain(p = {})}<span data-seat="voxGain" data-form={p.form}>VOX</span>{/snippet}
{#snippet antiVoxGain(p = {})}<span data-seat="antiVoxGain" data-form={p.form}>Anti VOX</span>{/snippet}
{#snippet voxDelay(p = {})}<span data-seat="voxDelay" data-form={p.form}>VOX delay</span>{/snippet}
{#snippet compressorLevel(p = {})}<span data-seat="compressorLevel" data-form={p.form}>Compressor</span>{/snippet}
{#snippet monitorLevel(p = {})}<span data-seat="monitorLevel" data-form={p.form}>Monitor</span>{/snippet}
{#snippet signal()}<span data-station-meter="signal">Signal</span>{/snippet}
{#snippet power()}<span data-station-meter="power">Power</span>{/snippet}
{#snippet swr()}<span data-station-meter="swr">SWR</span>{/snippet}
{#snippet alc()}<span data-station-meter="alc">ALC</span>{/snippet}
{#snippet drainCurrent()}<span data-station-meter="drainCurrent">Drain current</span>{/snippet}
{#snippet drainVoltage()}<span data-station-meter="drainVoltage">Drain voltage</span>{/snippet}
{#snippet compression()}<span data-station-meter="compression">Compression</span>{/snippet}
{#snippet hostedFace()}
  {@const instruments = {
    receiver: receiverPresent ? {
      mainFrequency,
      subFrequency: subPresent ? subFrequency : null,
      mainSMeter,
      subSMeter: subPresent ? subSMeter : null,
    } : null,
    vfoOperations: operationsPresent ? {
      split: hiddenOperation === 'split' ? null : split,
      dualWatch: hiddenOperation === 'dualWatch' ? null : dualWatch,
      activeReceiver: hiddenOperation === 'activeReceiver' ? null : activeReceiver,
      equalize: hiddenOperation === 'equalize' ? null : equalize,
      swap: hiddenOperation === 'swap' ? null : swap,
      quickSplit: hiddenOperation === 'quickSplit' ? null : quickSplit,
      quickDualWatch: hiddenOperation === 'quickDualWatch' ? null : quickDualWatch,
      speak: hiddenOperation === 'speak' ? null : speak,
    } : null,
    txAux: txPresent ? {
      rfPower, micGain, driveGain, voxGain, antiVoxGain,
      voxDelay, compressorLevel, monitorLevel,
    } : null,
    stationMeters: stationMetersPresent ? {
      signal, power, swr, alc, drainCurrent, drainVoltage, compression,
    } : null,
  }}
  {#if face === 'a'}
    <FixtureFaceA {instruments} />
  {:else}
    <FixtureFaceB {instruments} />
  {/if}
{/snippet}

{@render hostedFace()}
{#if scalarPresent}
  <Scalar
    binding={scalarSeat}
    label="Fixture scalar"
    compact={true}
    showLabel={false}
    showValue={true}
    unit=" W"
  />
{/if}
<Frequency
  model={frequencyModel}
  interaction={frequencyInteraction}
  presentation="interactive"
  compact={false}
  active={true}
  receiver="main"
  vfoFreqHook={false}
/>
{#if finitePresent}
  <Action lease={actionLease} />
  <Toggle lease={toggleLease} />
  <Choice lease={choiceLease} />
{/if}
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
      const operationNames = [
        'split', 'dualWatch', 'activeReceiver', 'equalize', 'swap',
        'quickSplit', 'quickDualWatch', 'speak',
      ];
      const seatNames = [
        'mainFrequency', 'subFrequency', 'mainSMeter', 'subSMeter',
        ...operationNames,
        'rfPower', 'micGain', 'driveGain', 'voxGain', 'antiVoxGain',
        'voxDelay', 'compressorLevel', 'monitorLevel',
      ];
      const stationMeterNames = [
        'signal', 'power', 'swr', 'alc', 'drainCurrent', 'drainVoltage', 'compression',
      ];
      const scalarControl = page.locator('[data-fixture-scalar]');
      assert.equal(await scalarControl.count(), 1);
      assert.equal(await scalarControl.getAttribute('data-compact'), 'true');
      assert.equal(await scalarControl.locator('span').count(), 0);
      assert.equal(await scalarControl.locator('output').textContent(), '25 W');
      assert.deepEqual(await page.evaluate(() => globalThis.__scalarStats()), {
        attachments: 1, attachArguments: [0], disposals: 0, nativeInputs: [], keys: [],
      });
      await scalarControl.locator('input').fill('40');
      await scalarControl.locator('input').press('ArrowRight');
      assert.deepEqual(await page.evaluate(() => globalThis.__scalarStats()), {
        attachments: 1, attachArguments: [0], disposals: 0,
        nativeInputs: [40], keys: ['ArrowRight'],
      });

      const frequencyDigit = page.locator('[data-fixture-frequency] button');
      await frequencyDigit.click();
      await frequencyDigit.dispatchEvent('wheel');
      await frequencyDigit.press('ArrowRight');
      assert.deepEqual(await page.evaluate(() => globalThis.__frequencyStats()), {
        clicks: 1, wheels: 1, keys: 1,
      });

      await page.locator('[data-fixture-action]').click();
      await page.locator('[data-fixture-toggle]').click();
      await page.locator('[data-fixture-choice] button', { hasText: 'USB' }).click();
      assert.equal(await page.locator('[data-fixture-choice] button', { hasText: 'LSB' }).isDisabled(), true);
      assert.deepEqual(await page.evaluate(() => globalThis.__finiteStats()), {
        actions: 1, toggles: 1, choices: ['USB'], disposals: 0,
      });
      await page.evaluate(() => globalThis.__hideConcreteRenderers());
      await scalarControl.waitFor({ state: 'detached' });
      await page.locator('[data-fixture-action]').waitFor({ state: 'detached' });
      assert.equal(await page.locator('[data-fixture-toggle]').count(), 0);
      assert.equal(await page.locator('[data-fixture-choice]').count(), 0);
      assert.equal((await page.evaluate(() => globalThis.__scalarStats())).disposals, 1);
      assert.equal((await page.evaluate(() => globalThis.__finiteStats())).disposals, 0);

      assert.equal(await page.locator('[data-external-face="a"]').count(), 1);
      assert.equal(await page.locator('[data-seat]').count(), 20);
      assert.deepEqual(
        await page.locator('[data-family="stationMeters"] [data-station-meter]').evaluateAll((nodes) =>
          nodes.map((node) => node.getAttribute('data-station-meter'))),
        stationMeterNames,
      );
      for (const name of seatNames) {
        assert.equal(await page.locator(`[data-seat="${name}"]`).count(), 1);
      }
      assert.equal(await page.locator('[data-seat="split"]').isDisabled(), true);
      await page.evaluate(() => {
        globalThis.__retainedEqualize = document.querySelector('[data-seat="equalize"]');
        globalThis.__setFace('b');
      });
      await page.locator('[data-external-face="b"]').waitFor();
      assert.equal(await page.locator('[data-seat]').count(), 20);
      assert.deepEqual(
        await page.locator('[data-family="stationMeters"] [data-station-meter]').evaluateAll((nodes) =>
          nodes.map((node) => node.getAttribute('data-station-meter'))),
        [...stationMeterNames].reverse(),
      );
      for (const name of seatNames) {
        assert.equal(await page.locator(`[data-seat="${name}"]`).count(), 1);
      }
      await page.evaluate(() => globalThis.__retainedEqualize.click());
      assert.equal(await page.evaluate(() => globalThis.__operationInvocations()), 0);
      await page.locator('[data-seat="equalize"]').click();
      assert.equal(await page.evaluate(() => globalThis.__operationInvocations()), 1);
      await page.evaluate(() => globalThis.__setFace('a'));
      await page.locator('[data-external-face="a"]').waitFor();

      await page.evaluate(() => globalThis.__setSub(false));
      await page.locator('[data-seat="subFrequency"]').waitFor({ state: 'detached' });
      assert.equal(await page.locator('[data-seat="subSMeter"]').count(), 0);
      assert.equal(await page.locator('[data-seat]').count(), 18);
      for (const hidden of operationNames) {
        await page.evaluate((name) => globalThis.__hideOperation(name), hidden);
        await page.locator(`[data-seat="${hidden}"]`).waitFor({ state: 'detached' });
        assert.equal(await page.locator('[data-family="vfoOperations"] [data-seat]').count(), 7);
        for (const visible of operationNames.filter((name) => name !== hidden)) {
          assert.equal(await page.locator(`[data-seat="${visible}"]`).count(), 1);
        }
        await page.evaluate(() => globalThis.__hideOperation(null));
        await page.locator(`[data-seat="${hidden}"]`).waitFor();
      }
      await page.evaluate(() => globalThis.__setFamily('receiver', false));
      await page.locator('[data-family="receiver"]').waitFor({ state: 'detached' });
      assert.equal(await page.locator('[data-seat="mainFrequency"]').count(), 0);
      await page.evaluate(() => globalThis.__setFamily('receiver', true));
      await page.locator('[data-family="receiver"]').waitFor();
      await page.evaluate(() => globalThis.__setFamily('vfoOperations', false));
      await page.locator('[data-family="vfoOperations"]').waitFor({ state: 'detached' });
      assert.equal(await page.locator('[data-seat="split"]').count(), 0);
      await page.evaluate(() => globalThis.__setFamily('vfoOperations', true));
      await page.locator('[data-family="vfoOperations"]').waitFor();
      await page.evaluate(() => globalThis.__setFamily('txAux', false));
      await page.locator('[data-family="txAux"]').waitFor({ state: 'detached' });
      assert.equal(await page.locator('[data-seat="rfPower"]').count(), 0);
      await page.evaluate(() => globalThis.__setFamily('stationMeters', false));
      await page.locator('[data-family="stationMeters"]').waitFor({ state: 'detached' });
      assert.equal(await page.locator('[data-station-meter]').count(), 0);
      await page.evaluate(() => {
        globalThis.__setFamily('txAux', true);
        globalThis.__setFamily('stationMeters', true);
        globalThis.__setSub(true);
        globalThis.__hideOperation(null);
      });
      await page.locator('[data-seat="subFrequency"]').waitFor();
      await page.locator('[data-seat="speak"]').waitFor();
      assert.equal(await page.locator('[data-seat]').count(), 20);
      assert.equal(await page.locator('[data-station-meter]').count(), 7);
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
assert.equal(globalThis.document, undefined);
const fixtureModule = await import('@rigplane/external-component-kit-fixture');
assert.equal(globalThis.document, undefined);
const {
  default: fixtureKit,
  FixtureFaceA,
  FixtureFaceB,
  faceAPresentation,
  faceBPresentation,
  reservedDesignLanguage,
  reservedInstrumentGroup,
  reservedPresentation,
} = fixtureModule;
assert.deepEqual(Object.keys(api).sort(), ['COMPONENT_KIT_API_VERSION', 'defineComponentKit']);
assert.equal(api.COMPONENT_KIT_API_VERSION, 1);
assert.equal(api.defineComponentKit(fixtureKit), fixtureKit);
assert.deepEqual(Object.keys(fixtureKit).sort(), [
  'apiVersion', 'finiteControlAppearances', 'frequencyReadouts', 'id', 'layouts',
  'meterAppearances', 'presentations', 'scalarAppearances',
]);
assert.equal(Object.hasOwn(fixtureKit, 'designLanguages'), false);
assert.equal(Object.hasOwn(fixtureKit, 'instrumentGroups'), false);
assert.equal(reservedDesignLanguage.id, 'fixture-line');
assert.equal(reservedInstrumentGroup.id, 'fixture-group');
const scalarAppearance = fixtureKit.scalarAppearances.fixture;
const finiteAppearance = fixtureKit.finiteControlAppearances.fixture;
const meterAppearance = fixtureKit.meterAppearances.fixture;
for (const component of [
  scalarAppearance.hbar, scalarAppearance.knob, scalarAppearance.bipolar,
  scalarAppearance.discrete, fixtureKit.frequencyReadouts.fixture,
  finiteAppearance.action, finiteAppearance.toggle, finiteAppearance.choice,
  meterAppearance.signal, meterAppearance.level,
]) assert.equal(typeof component, 'function');
assert.equal(scalarAppearance.hbar, scalarAppearance.knob);
assert.equal(scalarAppearance.hbar, scalarAppearance.bipolar);
assert.equal(scalarAppearance.hbar, scalarAppearance.discrete);
assert.notEqual(FixtureFaceA, FixtureFaceB);
assert.equal(faceAPresentation.hostMode, 'external-instruments-v1');
assert.equal(faceBPresentation.hostMode, 'external-instruments-v1');
assert.equal(faceAPresentation.layoutId, 'fixture-face-a');
assert.equal(faceBPresentation.layoutId, 'fixture-face-b');
assert.deepEqual(faceAPresentation.appearances, {
  scalar: 'fixture', frequency: 'fixture', finite: 'fixture', meter: 'fixture',
});
assert.deepEqual(faceAPresentation.resources, []);
assert.deepEqual(faceBPresentation.resources, []);
assert.equal(await faceAPresentation.loader(), FixtureFaceA);
assert.equal(await faceBPresentation.loader(), FixtureFaceB);
assert.equal(reservedPresentation.id, 'fixture-reserved-presentation');
assert.deepEqual(fixtureKit.layouts?.map(({ id }) => id), ['fixture-face-a', 'fixture-face-b']);
assert.deepEqual(fixtureKit.presentations?.map(({ id }) => id), ['fixture-face-a', 'fixture-face-b']);
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
  assert.equal(installedApi.version, '0.6.0');
  assert.equal(installedApi.peerDependencies.svelte, '>=5.45.2 <6');
  assert.equal(installedFixture.peerDependencies['@rigplane/component-kit-api'], '0.6.0');

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
