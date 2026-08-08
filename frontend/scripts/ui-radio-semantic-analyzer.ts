import fs from 'node:fs';
import path from 'node:path';

import { parse } from 'svelte/compiler';
import ts from 'typescript';

type Counts = Record<string, Record<string, number>>;

interface AnalyzerRequest {
  root: string;
  truthNames: string[];
  scopeNames: string[];
  scenarios: Record<string, Record<string, string>>;
}

interface AnalyzerResponse {
  scenarios: Record<string, Counts>;
  errors: Record<string, string[]>;
}

interface VirtualProject {
  program: ts.Program;
  checker: ts.TypeChecker;
  displayPath: Map<string, string>;
  originalPath: Map<string, string>;
  resolveModule: (specifier: string, importer: ts.SourceFile) => ts.SourceFile | undefined;
  errors: string[];
}

const FRONTEND_GUARDS = [
  'truth_patch_calls',
  'parallel_state_writers',
  'presentation_transport',
  'fabricated_defaults',
  'truth_persistence',
  'spectrum_metadata',
  'frontend_timers',
  'parallel_truth_store',
] as const;

const WRITER_EXPORTS = new Set([
  'applyOptimistic',
  '_applyOptimistic',
  'patchActiveReceiver',
  'patchRadioState',
  'patchReceiver',
]);
const STATE_WRITER_EXPORTS = new Set(['setRadioState']);
const TRANSPORT_MODULES = new Set([
  'frontend/src/lib/transport/ws-client.ts',
  'frontend/src/lib/transport/http-client.ts',
]);
const PRESENTATION_PREFIXES = [
  'frontend/src/semantic/',
  'frontend/src/components-v2/panels/',
  'frontend/src/components-v2/layout/',
  'frontend/src/components/spectrum/',
];
const AUTHORITY_PREFIXES = [
  ...PRESENTATION_PREFIXES,
  'frontend/src/presentation/',
  'frontend/src/skins/',
  'frontend/src/components/',
  'frontend/src/lib/',
  'frontend/src/lib/stores/',
  'frontend/src/lib/runtime/commands/',
  'frontend/src/lib/transport/',
];
const RESTRICTED_CONSTRUCTS = new Set(['eval', 'Function', 'Proxy']);
const SCOPE_SAMPLE_FIELDS = new Set([
  'bins',
  'magnitudes',
  'pcm',
  'pixels',
  'samples',
]);
const ASSIGNMENT_KINDS = new Set<ts.SyntaxKind>([
  ts.SyntaxKind.EqualsToken,
  ts.SyntaxKind.PlusEqualsToken,
  ts.SyntaxKind.MinusEqualsToken,
  ts.SyntaxKind.AsteriskEqualsToken,
  ts.SyntaxKind.SlashEqualsToken,
  ts.SyntaxKind.PercentEqualsToken,
  ts.SyntaxKind.AmpersandEqualsToken,
  ts.SyntaxKind.BarEqualsToken,
  ts.SyntaxKind.CaretEqualsToken,
  ts.SyntaxKind.LessThanLessThanEqualsToken,
  ts.SyntaxKind.GreaterThanGreaterThanEqualsToken,
  ts.SyntaxKind.GreaterThanGreaterThanGreaterThanEqualsToken,
  ts.SyntaxKind.AsteriskAsteriskEqualsToken,
  ts.SyntaxKind.QuestionQuestionEqualsToken,
  ts.SyntaxKind.BarBarEqualsToken,
  ts.SyntaxKind.AmpersandAmpersandEqualsToken,
]);

function normalize(value: string): string {
  return value.replace(/[^A-Za-z0-9]/g, '').toLowerCase();
}

function words(value: string): string[] {
  return value
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .split(/[^A-Za-z0-9]+/)
    .map((part) => part.toLowerCase())
    .filter(Boolean);
}

function posix(value: string): string {
  return value.split(path.sep).join('/');
}

function isTestPath(relative: string): boolean {
  const parts = relative.split('/');
  const name = parts.at(-1) ?? '';
  return parts.includes('__tests__') || name.includes('.test.') || name.includes('.spec.');
}

function walk(directory: string): string[] {
  const found: string[] = [];
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const candidate = path.join(directory, entry.name);
    if (entry.isDirectory()) found.push(...walk(candidate));
    else found.push(candidate);
  }
  return found;
}

function svelteScript(source: string, filename: string): string {
  const ast = parse(source, { filename, modern: true }) as unknown as {
    instance?: { content?: { start: number; end: number } };
    module?: { content?: { start: number; end: number } };
  };
  const blocks: string[] = [];
  for (const script of [ast.module, ast.instance]) {
    if (script?.content) blocks.push(source.slice(script.content.start, script.content.end));
  }
  return blocks.join('\n');
}

function createProject(root: string, overrides: Record<string, string>): VirtualProject {
  const frontendRoot = path.join(root, 'frontend');
  const sourceRoot = path.join(frontendRoot, 'src');
  const raw = new Map<string, string>();
  for (const filename of walk(sourceRoot)) {
    const relative = posix(path.relative(root, filename));
    if (isTestPath(relative) || (!filename.endsWith('.ts') && !filename.endsWith('.svelte'))) continue;
    raw.set(path.resolve(filename), fs.readFileSync(filename, 'utf8'));
  }
  for (const [relative, source] of Object.entries(overrides)) {
    if (!relative.startsWith('frontend/src/') || isTestPath(relative)) continue;
    raw.set(path.resolve(root, relative), source);
  }

  const virtual = new Map<string, string>();
  const displayPath = new Map<string, string>();
  const originalPath = new Map<string, string>();
  const errors: string[] = [];
  for (const [filename, source] of raw) {
    try {
      const virtualName = filename.endsWith('.svelte') ? `${filename}.ts` : filename;
      virtual.set(virtualName, filename.endsWith('.svelte') ? svelteScript(source, filename) : source);
      displayPath.set(virtualName, posix(path.relative(root, filename)));
      originalPath.set(filename, virtualName);
    } catch (error) {
      errors.push(`Svelte parse failed for ${posix(path.relative(root, filename))}: ${String(error)}`);
    }
  }

  const config = ts.readConfigFile(path.join(frontendRoot, 'tsconfig.app.json'), ts.sys.readFile);
  if (config.error) errors.push(ts.flattenDiagnosticMessageText(config.error.messageText, '\n'));
  const parsed = ts.parseJsonConfigFileContent(config.config ?? {}, ts.sys, frontendRoot);
  const options: ts.CompilerOptions = {
    ...parsed.options,
    noEmit: true,
    allowArbitraryExtensions: true,
  };
  const defaultHost = ts.createCompilerHost(options, true);

  function candidateVirtual(base: string): string | undefined {
    const candidates = [
      base,
      `${base}.ts`,
      `${base}.svelte`,
      `${base}.svelte.ts`,
      path.join(base, 'index.ts'),
    ];
    for (const candidate of candidates) {
      const direct = virtual.has(candidate) ? candidate : originalPath.get(candidate);
      if (direct && virtual.has(direct)) return direct;
    }
    return undefined;
  }

  function resolveVirtual(specifier: string, containingFile: string): string | undefined {
    const importerOriginal = containingFile.endsWith('.svelte.ts')
      ? containingFile.slice(0, -3)
      : containingFile;
    let base: string | undefined;
    if (specifier === '$lib') base = path.join(sourceRoot, 'lib');
    else if (specifier.startsWith('$lib/')) base = path.join(sourceRoot, 'lib', specifier.slice(5));
    else if (specifier.startsWith('.')) base = path.resolve(path.dirname(importerOriginal), specifier);
    if (base) return candidateVirtual(base);
    return undefined;
  }

  const host: ts.CompilerHost = {
    ...defaultHost,
    fileExists: (filename) => virtual.has(filename) || defaultHost.fileExists(filename),
    readFile: (filename) => virtual.get(filename) ?? defaultHost.readFile(filename),
    realpath: (filename) => filename,
    getSourceFile: (filename, languageVersion, onError, shouldCreateNewSourceFile) => {
      const source = virtual.get(filename);
      if (source !== undefined) return ts.createSourceFile(filename, source, languageVersion, true, ts.ScriptKind.TS);
      return defaultHost.getSourceFile(filename, languageVersion, onError, shouldCreateNewSourceFile);
    },
    resolveModuleNameLiterals: (literals, containingFile) =>
      literals.map((literal) => {
        const resolvedVirtual = resolveVirtual(literal.text, containingFile);
        if (resolvedVirtual) {
          return {
            resolvedModule: {
              resolvedFileName: resolvedVirtual,
              extension: ts.Extension.Ts,
              isExternalLibraryImport: false,
            },
          };
        }
        const resolved = ts.resolveModuleName(literal.text, containingFile, options, host).resolvedModule;
        return { resolvedModule: resolved };
      }),
  };

  const program = ts.createProgram({ rootNames: [...virtual.keys()], options, host });
  const checker = program.getTypeChecker();
  const project: VirtualProject = {
    program,
    checker,
    displayPath,
    originalPath,
    errors,
    resolveModule: (specifier, importer) => {
      const resolved = resolveVirtual(specifier, importer.fileName);
      return resolved ? program.getSourceFile(resolved) : undefined;
    },
  };

  const radio = program.getSourceFile(path.join(sourceRoot, 'lib/stores/radio.svelte.ts'));
  if (!radio) errors.push('canonical radio store module did not resolve');
  return project;
}

function analyzeFrontend(
  project: VirtualProject,
  truthInput: string[],
  scopeInput: string[],
): Counts {
  const { program, checker } = project;
  const truth = new Set(truthInput.map(normalize));
  const truthAtoms = new Set(truthInput.flatMap(words));
  const scopeAtoms = new Set(scopeInput.flatMap(words));
  const counts: Counts = Object.fromEntries(FRONTEND_GUARDS.map((guard) => [guard, {}]));
  const sourceFiles = program.getSourceFiles().filter((source) => project.displayPath.has(source.fileName));
  const symbolMemo = new Map<ts.Symbol, Set<ts.Symbol>>();

  function add(guard: (typeof FRONTEND_GUARDS)[number], source: ts.SourceFile, amount = 1): void {
    const relative = project.displayPath.get(source.fileName);
    if (!relative) return;
    counts[guard][relative] = (counts[guard][relative] ?? 0) + amount;
  }

  function unwrap(expression: ts.Expression): ts.Expression {
    let current = expression;
    while (
      ts.isParenthesizedExpression(current) ||
      ts.isAsExpression(current) ||
      ts.isTypeAssertionExpression(current) ||
      ts.isNonNullExpression(current) ||
      ts.isSatisfiesExpression(current) ||
      ts.isAwaitExpression(current)
    ) {
      current = current.expression;
    }
    return current;
  }

  function accessName(expression: ts.Expression): string | undefined {
    const current = unwrap(expression);
    if (ts.isPropertyAccessExpression(current)) return current.name.text;
    if (ts.isElementAccessExpression(current)) {
      const values = literalStrings(current.argumentExpression);
      return values.size === 1 ? [...values][0] : undefined;
    }
    if (ts.isIdentifier(current)) return current.text;
    return undefined;
  }

  function directSymbol(expression: ts.Expression): ts.Symbol | undefined {
    const current = unwrap(expression);
    if (ts.isPropertyAccessExpression(current)) return checker.getSymbolAtLocation(current.name);
    if (ts.isElementAccessExpression(current)) {
      const name = accessName(current);
      return name ? checker.getTypeAtLocation(current.expression).getProperty(name) : undefined;
    }
    return checker.getSymbolAtLocation(current);
  }

  function bindingInitializer(element: ts.BindingElement): ts.Expression | undefined {
    let owner: ts.Node = element.parent;
    while (ts.isObjectBindingPattern(owner) || ts.isArrayBindingPattern(owner)) {
      const parent = owner.parent;
      if (ts.isBindingElement(parent)) {
        owner = parent.parent;
        continue;
      }
      if (ts.isVariableDeclaration(parent) || ts.isParameter(parent)) return parent.initializer;
      return undefined;
    }
    return undefined;
  }

  function bindingPropertyNames(element: ts.BindingElement): string[] {
    const names: string[] = [];
    let current: ts.BindingElement | undefined = element;
    while (current) {
      const property = current.propertyName ?? (ts.isIdentifier(current.name) ? current.name : undefined);
      if (property) {
        if (ts.isIdentifier(property) || ts.isStringLiteralLike(property) || ts.isNumericLiteral(property)) {
          names.push(property.text);
        }
      }
      const pattern: ts.Node = current.parent;
      current = ts.isBindingElement(pattern.parent) ? pattern.parent : undefined;
    }
    return names;
  }

  function assignmentLeaves(expression: ts.Expression): ts.Expression[] {
    const current = unwrap(expression);
    if (ts.isObjectLiteralExpression(current)) {
      return current.properties.flatMap((property): ts.Expression[] => {
        if (ts.isPropertyAssignment(property)) return assignmentLeaves(property.initializer);
        if (ts.isShorthandPropertyAssignment(property)) return [property.name];
        if (ts.isSpreadAssignment(property)) return assignmentLeaves(property.expression);
        return [];
      });
    }
    if (ts.isArrayLiteralExpression(current)) {
      return current.elements.flatMap((element): ts.Expression[] => {
        if (ts.isOmittedExpression(element)) return [];
        if (ts.isSpreadElement(element)) return assignmentLeaves(element.expression);
        return assignmentLeaves(element);
      });
    }
    return [current];
  }

  function symbolOrigins(symbol: ts.Symbol, seen = new Set<ts.Symbol>()): Set<ts.Symbol> {
    const memo = symbolMemo.get(symbol);
    if (memo) return memo;
    if (seen.has(symbol)) return new Set();
    seen.add(symbol);
    let current = symbol;
    if (current.flags & ts.SymbolFlags.Alias) {
      const aliased = checker.getAliasedSymbol(current);
      if (aliased !== current) return symbolOrigins(aliased, seen);
    }
    const result = new Set<ts.Symbol>();
    for (const declaration of current.declarations ?? []) {
      if (ts.isVariableDeclaration(declaration) && declaration.initializer) {
        for (const origin of expressionOrigins(declaration.initializer, seen)) result.add(origin);
      }
      if (ts.isBindingElement(declaration)) {
        const initializer = bindingInitializer(declaration);
        if (initializer) {
          for (const origin of expressionOrigins(initializer, seen)) result.add(origin);
        }
      }
    }
    if (!result.size) result.add(current);
    symbolMemo.set(symbol, result);
    return result;
  }

  function expressionOrigins(expression: ts.Expression, seen = new Set<ts.Symbol>()): Set<ts.Symbol> {
    const current = unwrap(expression);
    const symbol = directSymbol(current);
    if (symbol) return symbolOrigins(symbol, seen);
    if (ts.isConditionalExpression(current)) {
      return new Set([
        ...expressionOrigins(current.whenTrue, seen),
        ...expressionOrigins(current.whenFalse, seen),
      ]);
    }
    return new Set();
  }

  function symbolKey(symbol: ts.Symbol): string {
    const declaration = symbol.declarations?.[0];
    return declaration ? `${declaration.getSourceFile().fileName}:${declaration.pos}` : symbol.getName();
  }

  function exportedAnchors(module: ts.SourceFile | undefined, names: Set<string>): Set<string> {
    const anchors = new Set<string>();
    const moduleSymbol = module && checker.getSymbolAtLocation(module);
    if (!module || !moduleSymbol) return anchors;
    for (const exported of checker.getExportsOfModule(moduleSymbol)) {
      if (!names.has(exported.getName())) continue;
      for (const origin of symbolOrigins(exported)) anchors.add(symbolKey(origin));
    }
    return anchors;
  }

  const radioModule = program.getSourceFile(
    path.join(path.dirname(path.dirname(path.dirname(sourceFiles[0]?.fileName ?? ''))), 'src/lib/stores/radio.svelte.ts'),
  ) ?? sourceFiles.find((source) => project.displayPath.get(source.fileName) === 'frontend/src/lib/stores/radio.svelte.ts');
  const writerAnchors = exportedAnchors(radioModule, WRITER_EXPORTS);
  const stateWriterAnchors = exportedAnchors(radioModule, STATE_WRITER_EXPORTS);
  if (writerAnchors.size < 3 || stateWriterAnchors.size !== 1) {
    project.errors.push('canonical radio-store writer anchors did not resolve completely');
  }

  const scopeFrameModule = sourceFiles.find(
    (source) => project.displayPath.get(source.fileName) === 'frontend/src/lib/runtime/adapters/scope-adapter.ts',
  );
  const scopeFrameModuleSymbol = scopeFrameModule && checker.getSymbolAtLocation(scopeFrameModule);
  const scopeFrameExport = scopeFrameModuleSymbol && checker.getExportsOfModule(scopeFrameModuleSymbol)
    .find((symbol) => symbol.getName() === 'ScopeFrame');
  const scopeFrameSymbol = scopeFrameExport && (scopeFrameExport.flags & ts.SymbolFlags.Alias)
    ? checker.getAliasedSymbol(scopeFrameExport)
    : scopeFrameExport;
  const scopeFrameType = scopeFrameSymbol && checker.getDeclaredTypeOfSymbol(scopeFrameSymbol);
  const scopeFrameMetadata = new Set(
    (scopeFrameType?.getProperties() ?? [])
      .map((property) => property.getName())
      .filter((name) => !SCOPE_SAMPLE_FIELDS.has(normalize(name))),
  );
  if (!scopeFrameSymbol || !scopeFrameMetadata.size) {
    project.errors.push('canonical ScopeFrame metadata schema did not resolve');
  }

  function isCanonicalScopeFrame(type: ts.Type): boolean {
    if (type.isUnionOrIntersection()) return type.types.some(isCanonicalScopeFrame);
    const symbol = type.aliasSymbol ?? type.symbol;
    if (!symbol || !scopeFrameSymbol) return false;
    if (symbol === scopeFrameSymbol) return true;
    return [...symbolOrigins(symbol)].some((origin) => origin === scopeFrameSymbol);
  }

  function hasAnchor(expression: ts.Expression, anchors: Set<string>): boolean {
    return [...expressionOrigins(expression)].some((origin) => anchors.has(symbolKey(origin)));
  }

  function literalStrings(expression: ts.Expression | undefined, seen = new Set<ts.Symbol>()): Set<string> {
    if (!expression) return new Set();
    const current = unwrap(expression);
    if (ts.isStringLiteralLike(current)) return new Set([current.text]);
    if (ts.isConditionalExpression(current)) {
      return new Set([...literalStrings(current.whenTrue, seen), ...literalStrings(current.whenFalse, seen)]);
    }
    if (ts.isIdentifier(current)) {
      const symbol = directSymbol(current);
      if (!symbol || seen.has(symbol)) return new Set();
      seen.add(symbol);
      const values = new Set<string>();
      for (const declaration of symbol.declarations ?? []) {
        if (ts.isVariableDeclaration(declaration) && declaration.initializer) {
          for (const value of literalStrings(declaration.initializer, seen)) values.add(value);
        }
      }
      return values;
    }
    return new Set();
  }

  function isLiteralValue(expression: ts.Expression, seen = new Set<ts.Symbol>()): boolean {
    const current = unwrap(expression);
    if (
      ts.isLiteralExpression(current) ||
      current.kind === ts.SyntaxKind.TrueKeyword ||
      current.kind === ts.SyntaxKind.FalseKeyword ||
      current.kind === ts.SyntaxKind.NullKeyword ||
      current.kind === ts.SyntaxKind.UndefinedKeyword
    ) return true;
    if (ts.isPrefixUnaryExpression(current)) return isLiteralValue(current.operand, seen);
    if (ts.isConditionalExpression(current)) {
      return isLiteralValue(current.whenTrue, seen) && isLiteralValue(current.whenFalse, seen);
    }
    if (ts.isIdentifier(current)) {
      const symbol = directSymbol(current);
      if (!symbol || seen.has(symbol)) return false;
      seen.add(symbol);
      return (symbol.declarations ?? []).some(
        (declaration) => ts.isVariableDeclaration(declaration) && !!declaration.initializer && isLiteralValue(declaration.initializer, seen),
      );
    }
    return false;
  }

  const truthTypeNames = new Set<string>();
  for (const source of sourceFiles) {
    const visit = (node: ts.Node): void => {
      if (ts.isInterfaceDeclaration(node) || ts.isTypeAliasDeclaration(node)) {
        const type = checker.getTypeAtLocation(node);
        const score = type.getProperties().filter((property) => truth.has(normalize(property.getName()))).length;
        if (score >= 2) truthTypeNames.add(normalize(node.name.text));
      }
      ts.forEachChild(node, visit);
    };
    visit(source);
  }

  function isTruthType(type: ts.Type): boolean {
    if (type.isUnionOrIntersection()) return type.types.some(isTruthType);
    const typeName = normalize(type.aliasSymbol?.getName() ?? type.symbol?.getName() ?? '');
    if (typeName && truthTypeNames.has(typeName)) return true;
    if (!(type.flags & ts.TypeFlags.Object)) return false;
    const declaration = type.symbol?.declarations?.[0] ?? type.aliasSymbol?.declarations?.[0];
    if (!declaration || !project.displayPath.has(declaration.getSourceFile().fileName)) return false;
    return type.getProperties().filter((property) => truth.has(normalize(property.getName()))).length >= 2;
  }

  function typeHasTruth(expression: ts.Expression): boolean {
    return isTruthType(checker.getTypeAtLocation(expression));
  }

  function isTruthExpression(expression: ts.Expression, seen = new Set<ts.Symbol>()): boolean {
    const current = unwrap(expression);
    if (ts.isPropertyAccessExpression(current) || ts.isElementAccessExpression(current)) {
      const name = accessName(current);
      if (name && truth.has(normalize(name))) return true;
      return isTruthExpression(current.expression, seen);
    }
    if (ts.isIdentifier(current)) {
      const normalized = normalize(current.text);
      if ([...truthTypeNames].some((typeName) => normalized.includes(typeName))) return true;
      const symbol = directSymbol(current);
      if (!symbol || seen.has(symbol)) return typeHasTruth(current);
      seen.add(symbol);
      return (symbol.declarations ?? []).some((declaration) => {
        if (ts.isVariableDeclaration(declaration) && declaration.initializer) {
          return isTruthExpression(declaration.initializer, seen);
        }
        if (ts.isBindingElement(declaration)) {
          if (bindingPropertyNames(declaration).some((name) => truth.has(normalize(name)))) return true;
          const initializer = bindingInitializer(declaration);
          return !!initializer && isTruthExpression(initializer, seen);
        }
        return false;
      }) || typeHasTruth(current);
    }
    if (ts.isCallExpression(current)) {
      const name = accessName(current.expression);
      if (name && ['String', 'Number', 'Boolean', 'JSON.parse'].includes(name)) {
        return current.arguments.some((argument) => isTruthExpression(argument, seen));
      }
      return typeHasTruth(current);
    }
    if (ts.isObjectLiteralExpression(current)) {
      return current.properties.some((property) => {
        const name = property.name && ts.isPropertyName(property.name) ? property.name.getText().replace(/["']/g, '') : '';
        if (truth.has(normalize(name))) return true;
        if (ts.isPropertyAssignment(property)) return isTruthExpression(property.initializer, seen);
        if (ts.isShorthandPropertyAssignment(property)) return isTruthExpression(property.name, seen);
        if (ts.isSpreadAssignment(property)) return isTruthExpression(property.expression, seen);
        return false;
      });
    }
    if (ts.isArrayLiteralExpression(current)) {
      return current.elements.some((element) =>
        !ts.isOmittedExpression(element) && isTruthExpression(element, seen),
      );
    }
    if (ts.isConditionalExpression(current)) {
      return isTruthExpression(current.whenTrue, seen) || isTruthExpression(current.whenFalse, seen);
    }
    return typeHasTruth(current);
  }

  function isTruthKey(value: string): boolean {
    if (truth.has(normalize(value))) return true;
    const parts = words(value);
    return parts.length >= 2 && parts.every((part) => truthAtoms.has(part));
  }

  function isStorage(expression: ts.Expression, seen = new Set<ts.Symbol>()): boolean {
    const current = unwrap(expression);
    if (ts.isIdentifier(current) && ['localStorage', 'sessionStorage'].includes(current.text)) return true;
    if (ts.isPropertyAccessExpression(current) || ts.isElementAccessExpression(current)) {
      const name = accessName(current);
      const base = current.expression;
      if (name && ['localStorage', 'sessionStorage'].includes(name)) {
        return ts.isIdentifier(base) && ['window', 'globalThis'].includes(base.text);
      }
    }
    const symbol = directSymbol(current);
    if (!symbol || seen.has(symbol)) return false;
    seen.add(symbol);
    return (symbol.declarations ?? []).some((declaration) => {
      if (ts.isVariableDeclaration(declaration) && declaration.initializer) return isStorage(declaration.initializer, seen);
      if (ts.isBindingElement(declaration)) {
        const name = declaration.propertyName?.getText() ?? declaration.name.getText();
        return ['localStorage', 'sessionStorage'].includes(name);
      }
      return false;
    });
  }

  function originalRoot(expression: ts.Expression, seen = new Set<ts.Symbol>()): string {
    const current = unwrap(expression);
    const symbol = directSymbol(current);
    if (symbol && !seen.has(symbol)) {
      seen.add(symbol);
      for (const declaration of symbol.declarations ?? []) {
        if (ts.isVariableDeclaration(declaration) && declaration.initializer) {
          return originalRoot(declaration.initializer, seen);
        }
      }
      return symbolKey(symbol);
    }
    return `${current.getSourceFile().fileName}:${current.getText()}`;
  }

  function isPresentation(source: ts.SourceFile): boolean {
    const relative = project.displayPath.get(source.fileName) ?? '';
    return PRESENTATION_PREFIXES.some((prefix) => relative.startsWith(prefix));
  }

  function isAuthoritySensitive(source: ts.SourceFile): boolean {
    const relative = project.displayPath.get(source.fileName) ?? '';
    return AUTHORITY_PREFIXES.some((prefix) => relative.startsWith(prefix));
  }

  function isAuthorityModuleSpecifier(specifier: string): boolean {
    return ['', '.cjs', '.js', '.mjs', '.svelte', '.ts'].includes(path.posix.extname(specifier));
  }

  function moduleExportsTransport(module: ts.SourceFile, seen = new Set<ts.SourceFile>()): boolean {
    const relative = project.displayPath.get(module.fileName);
    if (relative && TRANSPORT_MODULES.has(relative)) return true;
    if (seen.has(module)) return false;
    seen.add(module);
    const moduleSymbol = checker.getSymbolAtLocation(module);
    if (!moduleSymbol) return false;
    return checker.getExportsOfModule(moduleSymbol).some((exported) =>
      [...symbolOrigins(exported)].some((origin) => {
        const declaration = origin.declarations?.[0];
        const originPath = declaration && project.displayPath.get(declaration.getSourceFile().fileName);
        return !!originPath && TRANSPORT_MODULES.has(originPath);
      }),
    );
  }

  function sourceTouchesTransport(source: ts.SourceFile): boolean {
    let touches = false;
    const visit = (node: ts.Node): void => {
      if (touches) return;
      if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)) {
        const module = project.resolveModule(node.moduleSpecifier.text, source);
        if (module && moduleExportsTransport(module)) {
          touches = true;
          return;
        }
      }
      ts.forEachChild(node, visit);
    };
    visit(source);
    return touches;
  }

  function restrictedConstruct(expression: ts.Expression): string | undefined {
    const direct = accessName(expression);
    if (direct && RESTRICTED_CONSTRUCTS.has(direct)) return direct;
    for (const origin of expressionOrigins(expression)) {
      if (RESTRICTED_CONSTRUCTS.has(origin.getName())) return origin.getName();
    }
    return undefined;
  }

  function expressionTouchesAuthority(expression: ts.Expression): boolean {
    if (isTruthExpression(expression)) return true;
    if (hasAnchor(expression, writerAnchors) || hasAnchor(expression, stateWriterAnchors)) return true;
    return [...expressionOrigins(expression)].some((origin) => {
      const declaration = origin.declarations?.[0];
      const relative = declaration && project.displayPath.get(declaration.getSourceFile().fileName);
      return !!relative && TRANSPORT_MODULES.has(relative);
    });
  }

  function timerCall(expression: ts.Expression): boolean {
    for (const origin of expressionOrigins(expression)) {
      if (!['setInterval', 'setTimeout'].includes(origin.getName())) continue;
      const declaration = origin.declarations?.[0];
      if (declaration && !project.displayPath.has(declaration.getSourceFile().fileName)) return true;
    }
    return false;
  }

  const stateRoots = new Map<string, { source: ts.SourceFile; truth: boolean }>();
  function stateCall(node: ts.CallExpression): boolean {
    const callee = unwrap(node.expression);
    return (ts.isIdentifier(callee) && callee.text === '$state') ||
      (ts.isPropertyAccessExpression(callee) && callee.expression.getText() === '$state');
  }
  function declarationRoot(node: ts.CallExpression): ts.Expression | undefined {
    const parent = node.parent;
    if (ts.isVariableDeclaration(parent) && ts.isIdentifier(parent.name)) return parent.name;
    if (ts.isPropertyDeclaration(parent) && parent.name && ts.isIdentifier(parent.name)) return parent.name;
    return undefined;
  }

  function isModuleStore(root: ts.Expression, source: ts.SourceFile): boolean {
    const relative = project.displayPath.get(source.fileName) ?? '';
    if (!relative.endsWith('.svelte.ts')) return false;
    if (!ts.isIdentifier(root)) return false;
    const declaration = directSymbol(root)?.declarations?.[0];
    if (!declaration) return false;
    if (ts.isPropertyDeclaration(declaration)) return true;
    return ts.isVariableDeclaration(declaration) &&
      ts.isVariableDeclarationList(declaration.parent) &&
      ts.isVariableStatement(declaration.parent.parent) &&
      ts.isSourceFile(declaration.parent.parent.parent);
  }

  function stateKey(expression: ts.Expression, seen = new Set<ts.Symbol>()): string | undefined {
    const current = unwrap(expression);
    if (ts.isPropertyAccessExpression(current) || ts.isElementAccessExpression(current)) {
      return stateKey(current.expression, seen);
    }
    const symbol = directSymbol(current);
    if (!symbol || seen.has(symbol)) return undefined;
    const direct = symbolKey(symbol);
    if (stateRoots.has(direct)) return direct;
    seen.add(symbol);
    for (const declaration of symbol.declarations ?? []) {
      if (ts.isVariableDeclaration(declaration) && declaration.initializer) {
        const nested = stateKey(declaration.initializer, seen);
        if (nested) return nested;
      }
      if (ts.isBindingElement(declaration)) {
        const initializer = bindingInitializer(declaration);
        if (initializer) {
          const nested = stateKey(initializer, seen);
          if (nested) return nested;
        }
      }
    }
    return undefined;
  }

  const frameAccesses = new Map<string, Array<{ source: ts.SourceFile; name: string; base: ts.Expression }>>();

  for (const source of sourceFiles) {
    const transportInvolved = sourceTouchesTransport(source);
    const visit = (node: ts.Node): void => {
      if (ts.isImportDeclaration(node) && isAuthoritySensitive(source) && ts.isStringLiteral(node.moduleSpecifier)) {
        const module = project.resolveModule(node.moduleSpecifier.text, source);
        const authorityModule = isAuthorityModuleSpecifier(node.moduleSpecifier.text);
        if (!module && authorityModule && (node.moduleSpecifier.text.startsWith('$lib/') || node.moduleSpecifier.text.startsWith('.'))) {
          project.errors.push(`unresolved authority import ${node.moduleSpecifier.text} in ${project.displayPath.get(source.fileName)}`);
        } else if (isPresentation(source) && module && moduleExportsTransport(module)) {
          add('presentation_transport', source);
        }
      }

      if (ts.isCallExpression(node)) {
        if (hasAnchor(node.expression, writerAnchors)) add('truth_patch_calls', source);
        if (hasAnchor(node.expression, stateWriterAnchors)) add('parallel_state_writers', source);
        if (timerCall(node.expression)) add('frontend_timers', source);

        const restricted = restrictedConstruct(node.expression);
        if (
          restricted &&
          (isAuthoritySensitive(source) || transportInvolved || node.arguments.some(expressionTouchesAuthority))
        ) {
          project.errors.push(
            `restricted ${restricted} construct in ${project.displayPath.get(source.fileName)}`,
          );
        }

        if (node.expression.kind === ts.SyntaxKind.ImportKeyword) {
          const specifiers = literalStrings(node.arguments[0]);
          if (!specifiers.size && isAuthoritySensitive(source)) {
            project.errors.push(`non-constant dynamic import in ${project.displayPath.get(source.fileName)}`);
          }
          if (isPresentation(source)) {
            for (const specifier of specifiers) {
              const module = project.resolveModule(specifier, source);
              if (!module && isAuthorityModuleSpecifier(specifier) && (specifier.startsWith('$lib/') || specifier.startsWith('.'))) {
                project.errors.push(`unresolved dynamic import ${specifier} in ${project.displayPath.get(source.fileName)}`);
              }
              else if (module && moduleExportsTransport(module)) add('presentation_transport', source);
            }
          } else if (isAuthoritySensitive(source)) {
            for (const specifier of specifiers) {
              const module = project.resolveModule(specifier, source);
              if (!module && isAuthorityModuleSpecifier(specifier) && (specifier.startsWith('$lib/') || specifier.startsWith('.'))) {
                project.errors.push(`unresolved authority import ${specifier} in ${project.displayPath.get(source.fileName)}`);
              }
            }
          }
        }

        const method = accessName(node.expression);
        const callee = unwrap(node.expression);
        const receiver = ts.isPropertyAccessExpression(callee) || ts.isElementAccessExpression(callee)
          ? callee.expression
          : undefined;
        if (receiver && method && ['getItem', 'setItem'].includes(method) && isStorage(receiver)) {
          const keys = literalStrings(node.arguments[0]);
          const keyIsTruth = [...keys].some(isTruthKey);
          const valueIsTruth = node.arguments[1] ? isTruthExpression(node.arguments[1]) : false;
          if (keyIsTruth || valueIsTruth) add('truth_persistence', source);
        }

        if (stateCall(node)) {
          const root = declarationRoot(node);
          if (root && isModuleStore(root, source)) {
            const symbol = directSymbol(root);
            if (!symbol) {
              project.errors.push(`unresolved module $state declaration in ${project.displayPath.get(source.fileName)}`);
              ts.forEachChild(node, visit);
              return;
            }
            const key = symbolKey(symbol);
            const truthInitial = !!node.arguments[0] && isTruthExpression(node.arguments[0]);
            const typeTruth = node.typeArguments?.some((argument) => {
              return isTruthType(checker.getTypeFromTypeNode(argument));
            }) ?? false;
            stateRoots.set(key, { source, truth: truthInitial || typeTruth });
          }
        }
      }

      if (ts.isNewExpression(node)) {
        const restricted = restrictedConstruct(node.expression);
        const argumentsList = node.arguments ?? [];
        if (
          restricted &&
          (isAuthoritySensitive(source) || transportInvolved || argumentsList.some(expressionTouchesAuthority))
        ) {
          project.errors.push(
            `restricted ${restricted} construct in ${project.displayPath.get(source.fileName)}`,
          );
        }
      }

      if (ts.isBinaryExpression(node)) {
        if (
          [ts.SyntaxKind.QuestionQuestionToken, ts.SyntaxKind.BarBarToken].includes(node.operatorToken.kind) &&
          isTruthExpression(node.left) &&
          isLiteralValue(node.right)
        ) add('fabricated_defaults', source);

        if (ASSIGNMENT_KINDS.has(node.operatorToken.kind)) {
          let truthMutation = false;
          for (const leaf of assignmentLeaves(node.left)) {
            const target = unwrap(leaf);
            if (!ts.isPropertyAccessExpression(target) && !ts.isElementAccessExpression(target)) continue;
            const property = accessName(target);
            const rootKey = stateKey(target.expression);
            const state = rootKey ? stateRoots.get(rootKey) : undefined;
            const leafIsTruth = !!property && truth.has(normalize(property));
            if (state && (leafIsTruth || isTruthExpression(node.right))) state.truth = true;
            if ((leafIsTruth || isTruthExpression(node.right)) && isTruthExpression(target.expression)) {
              truthMutation = true;
            }
          }
          if (truthMutation) add('truth_patch_calls', source);
        }
      }

      if (ts.isConditionalExpression(node)) {
        const condition = unwrap(node.condition);
        if (ts.isBinaryExpression(condition)) {
          const nullish = [ts.SyntaxKind.EqualsEqualsToken, ts.SyntaxKind.EqualsEqualsEqualsToken].includes(condition.operatorToken.kind) &&
            (condition.left.kind === ts.SyntaxKind.NullKeyword || condition.right.kind === ts.SyntaxKind.NullKeyword ||
             condition.left.kind === ts.SyntaxKind.UndefinedKeyword || condition.right.kind === ts.SyntaxKind.UndefinedKeyword);
          const candidate = condition.left.kind === ts.SyntaxKind.NullKeyword || condition.left.kind === ts.SyntaxKind.UndefinedKeyword
            ? condition.right
            : condition.left;
          if (nullish && isTruthExpression(candidate) && (isLiteralValue(node.whenTrue) || isLiteralValue(node.whenFalse))) {
            add('fabricated_defaults', source);
          }
        }
      }

      if (ts.isPropertyAccessExpression(node) || ts.isElementAccessExpression(node)) {
        const name = accessName(node);
        if (name) {
          const key = originalRoot(node.expression);
          const entries = frameAccesses.get(key) ?? [];
          entries.push({ source, name, base: node.expression });
          frameAccesses.set(key, entries);
        }
      }

      if (ts.isBindingElement(node) && ts.isObjectBindingPattern(node.parent)) {
        const initializer = bindingInitializer(node);
        const property = node.propertyName ?? (ts.isIdentifier(node.name) ? node.name : undefined);
        if (initializer && property && isCanonicalScopeFrame(checker.getTypeAtLocation(initializer))) {
          const name = property.getText().replace(/["']/g, '');
          const key = originalRoot(initializer);
          const entries = frameAccesses.get(key) ?? [];
          entries.push({ source, name, base: initializer });
          frameAccesses.set(key, entries);
        }
      }
      ts.forEachChild(node, visit);
    };
    visit(source);
  }

  for (const state of stateRoots.values()) {
    if (state.truth) add('parallel_truth_store', state.source);
  }

  for (const accesses of frameAccesses.values()) {
    const unresolvedMetadata: typeof accesses = [];
    for (const access of accesses) {
      const type = checker.getTypeAtLocation(access.base);
      if (isCanonicalScopeFrame(type)) {
        if (scopeFrameMetadata.has(access.name)) add('spectrum_metadata', access.source);
        continue;
      }
      if (type.flags & (ts.TypeFlags.Any | ts.TypeFlags.Unknown)) unresolvedMetadata.push(access);
    }
    const metadata = unresolvedMetadata.filter(({ name }) =>
      words(name).some((word) => scopeAtoms.has(word)),
    );
    const strong = metadata.some(({ name }) => {
      const parts = words(name);
      return parts.some((word) => ['hz', 'freq', 'frequency'].includes(word)) &&
        parts.some((word) => scopeAtoms.has(word));
    });
    if (!strong) continue;
    for (const access of metadata) add('spectrum_metadata', access.source);
  }

  return counts;
}

export function analyze(request: AnalyzerRequest): AnalyzerResponse {
  const scenarios: Record<string, Counts> = {};
  const errors: Record<string, string[]> = {};
  for (const [id, overrides] of Object.entries(request.scenarios)) {
    const project = createProject(request.root, overrides);
    scenarios[id] = analyzeFrontend(project, request.truthNames, request.scopeNames);
    errors[id] = project.errors;
  }
  return { scenarios, errors };
}
