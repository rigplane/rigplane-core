// @ts-nocheck -- TypeScript AST nodes are walked structurally.
/**
 * Inventory of frontend tests that read source text at test time, and of
 * source digests (`createHash` over a file read).
 *
 * File rule: a `*.{test,spec}.{ts,js,mjs}` file under `frontend/` (outside
 * `node_modules`) whose text matches `READ_PATTERN`, minus `DATA_READERS`.
 * Test rule: inside such a file, an `it`/`test` block counts when its
 * callback contains a read call, a `?raw` import binding, or a binding that is
 * (transitively) bound or assigned from one.
 *
 *   node scripts/source-text-pin-inventory.mjs   # prints the baseline module
 */
import { readdirSync, readFileSync } from 'node:fs';
import { dirname, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

export const FRONTEND_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
export const TEST_FILE = /\.(?:test|spec)\.(?:ts|js|mjs)$/;
export const READ_PATTERN = /readFileSync|readFile\(|import\.meta\.glob|\?raw/;

/** Files that match READ_PATTERN but read data, not product source. */
export const DATA_READERS = Object.freeze({
  'src/__tests__/control-feedback-ownership.test.ts': '`?raw` occurs only inside string literals',
  'src/components-v2/panels/meter-utils.test.ts': 'reads rigs/*.toml calibration data',
  'src/lib/transport/__tests__/ws-client.isolated.test.ts': 'reads wire vectors from docs/api/command-catalog.md',
  'tests/e2e/i18n/mobile-operator-recovery.spec.ts': 'reads a capture file named by the environment',
  'tests/e2e/placeholder-guard/page-scan.spec.ts': 'reads its own offenders.json',
});

/** Digest reads allowed by path prefix: vendored fixtures, not product source. */
export const DIGEST_READ_EXEMPT_PREFIXES = Object.freeze({
  'component-kit-api/fixtures/': 'vendored external-kit fixture bytes',
});

const READ_CALLS = new Set(['readFileSync', 'readFile']);
const TEST_ROOTS = new Set(['it', 'test']);
const NOT_A_TEST = new Set(['describe', 'beforeAll', 'beforeEach', 'afterAll', 'afterEach', 'step', 'use', 'extend', 'info', 'setTimeout', 'slow', 'expect']);

function walk(node, visit) {
  visit(node);
  ts.forEachChild(node, (child) => walk(child, visit));
}

function calleeName(expr) {
  if (ts.isIdentifier(expr)) return expr.text;
  if (ts.isPropertyAccessExpression(expr)) return expr.name.text;
  return null;
}

function isReadCall(node) {
  if (!ts.isCallExpression(node)) return false;
  const callee = node.expression;
  if (callee.kind === ts.SyntaxKind.ImportKeyword) {
    const arg = node.arguments[0];
    return !!arg && ts.isStringLiteralLike(arg) && arg.text.includes('?raw');
  }
  if (ts.isPropertyAccessExpression(callee) && callee.name.text === 'glob'
    && ts.isMetaProperty(callee.expression)) return true;
  return READ_CALLS.has(calleeName(callee));
}

/** Parse `sources` (path -> text) into one program so names resolve lexically. */
function analyze(sources) {
  const options = { noLib: true, noResolve: true, allowJs: true, types: [], noEmit: true };
  const files = new Map();
  for (const [path, text] of sources) files.set(`/pins/${path}`, ts.createSourceFile(`/pins/${path}`, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS));
  const host = ts.createCompilerHost(options, true);
  host.getSourceFile = (name) => files.get(name);
  host.fileExists = (name) => files.has(name);
  host.getCurrentDirectory = () => '/pins';
  const checker = ts.createProgram([...files.keys()], options, host).getTypeChecker();
  const symbol = (id) => (ts.isShorthandPropertyAssignment(id.parent) && id.parent.name === id
    ? checker.getShorthandAssignmentValueSymbol(id.parent) : checker.getSymbolAtLocation(id));
  return (path) => ({ sf: files.get(`/pins/${path}`), symbol });
}

/** Collect symbols bound or assigned (transitively) from a source read. */
function taintedSymbols(sf, symbol) {
  const tainted = new Set();
  const ctx = { tainted, symbol };
  const bindingIds = (name, out = []) => {
    if (ts.isIdentifier(name)) out.push(name);
    else name.elements?.forEach((e) => { if (!ts.isOmittedExpression(e)) bindingIds(e.name, out); });
    return out;
  };
  let changed = true;
  const add = (ids) => ids.forEach((id) => { const s = symbol(id); if (s && !tainted.has(s)) { tainted.add(s); changed = true; } });
  walk(sf, (node) => {
    if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)
      && node.moduleSpecifier.text.includes('?raw') && node.importClause) {
      const clause = node.importClause;
      const bindings = clause.namedBindings;
      add([clause.name, bindings && ts.isNamespaceImport(bindings) ? bindings.name : null,
        ...(bindings && ts.isNamedImports(bindings) ? bindings.elements.map((e) => e.name) : [])].filter(Boolean));
    }
  });
  while (changed) {
    changed = false;
    walk(sf, (node) => {
      if (ts.isVariableDeclaration(node) && node.initializer && reads(node.initializer, ctx)) add(bindingIds(node.name));
      if (ts.isFunctionDeclaration(node) && node.name && node.body && reads(node.body, ctx)) add([node.name]);
      if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.EqualsToken
        && ts.isIdentifier(node.left) && reads(node.right, ctx)) add([node.left]);
    });
  }
  return ctx;
}

function isReference(node) {
  const parent = node.parent;
  if (!parent) return true;
  if (ts.isPropertyAccessExpression(parent) && parent.name === node) return false;
  if ((ts.isPropertyAssignment(parent) || ts.isVariableDeclaration(parent) || ts.isBindingElement(parent)
    || ts.isFunctionDeclaration(parent) || ts.isParameter(parent)) && parent.name === node) return false;
  return true;
}

/** True when `node` contains a read call or references a tainted symbol. */
function reads(node, { tainted, symbol }) {
  let found = false;
  walk(node, (n) => {
    if (found) return;
    if (isReadCall(n)) found = true;
    else if (ts.isIdentifier(n) && isReference(n) && tainted.has(symbol(n))) found = true;
  });
  return found;
}

/** Resolve `it`, `it.skip`, `it.each(t)`, `test.only`… to their root and kind. */
function classifyCall(call) {
  let expr = call.expression;
  const chain = [];
  while (true) {
    if (ts.isCallExpression(expr)) { expr = expr.expression; continue; }
    if (ts.isPropertyAccessExpression(expr)) { chain.unshift(expr.name.text); expr = expr.expression; continue; }
    break;
  }
  if (!ts.isIdentifier(expr)) return null;
  const root = expr.text;
  if (root === 'describe' || (root === 'test' && chain[0] === 'describe')) return 'describe';
  if (TEST_ROOTS.has(root) && !chain.some((part) => NOT_A_TEST.has(part))) return 'test';
  return null;
}

function titleOf(arg, sf) {
  if (!arg) return '<anonymous>';
  if (ts.isStringLiteralLike(arg)) return arg.text;
  return arg.getText(sf).replace(/\s+/g, ' ');
}

/** `path::describe > … > test` for every test block in `text` that reads source. */
export function auditTestSource(path, text, unit = analyze([[path, text]])(path)) {
  const { sf } = unit;
  const ctx = taintedSymbols(sf, unit.symbol);
  const pins = new Set();
  const visit = (node, titles) => {
    if (ts.isCallExpression(node)) {
      const kind = classifyCall(node);
      if (kind) {
        const title = [...titles, titleOf(node.arguments[0], sf)];
        const body = node.arguments.slice(1);
        if (kind === 'test' && body.some((arg) => reads(arg, ctx))) pins.add(`${path}::${title.join(' > ')}`);
        if (kind === 'describe') body.forEach((arg) => visit(arg, title));
        return;
      }
    }
    ts.forEachChild(node, (child) => visit(child, titles));
  };
  visit(sf, []);
  return [...pins];
}

/** Every `createHash(...).update(x)` whose `x` is, or derives from, a file read. */
export function auditDigests(path, text, unit = analyze([[path, text]])(path)) {
  const { sf } = unit;
  const ctx = taintedSymbols(sf, unit.symbol);
  const hashers = new Set();
  walk(sf, (node) => {
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer
      && ts.isCallExpression(node.initializer) && calleeName(node.initializer.expression) === 'createHash') hashers.add(node.name.text);
  });
  const isHasher = (expr) => {
    while (ts.isCallExpression(expr) || ts.isPropertyAccessExpression(expr)) {
      if (ts.isCallExpression(expr) && calleeName(expr.expression) === 'createHash') return true;
      expr = expr.expression;
    }
    return ts.isIdentifier(expr) && hashers.has(expr.text);
  };
  const exempt = (arg) => isReadCall(arg) && arg.arguments[0] && ts.isStringLiteralLike(arg.arguments[0])
    && Object.keys(DIGEST_READ_EXEMPT_PREFIXES).some((prefix) => arg.arguments[0].text.startsWith(prefix));
  const found = [];
  walk(sf, (node) => {
    if (!ts.isCallExpression(node) || !ts.isPropertyAccessExpression(node.expression)) return;
    if (node.expression.name.text !== 'update' || !isHasher(node.expression.expression)) return;
    const arg = node.arguments[0];
    if (arg && !exempt(arg) && reads(arg, ctx)) {
      const { line } = sf.getLineAndCharacterOfPosition(node.getStart(sf));
      found.push(`${path}:${line + 1}`);
    }
  });
  return found;
}

function listTestFiles(root) {
  const out = [];
  const visit = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue;
      const full = resolve(dir, entry.name);
      if (entry.isDirectory()) visit(full);
      else if (TEST_FILE.test(entry.name)) out.push(relative(root, full).split(sep).join('/'));
    }
  };
  visit(root);
  return out.sort();
}

/** Scan the frontend tree: matching files, pinned tests, and source digests. */
export function scanSourceTextPins(root = FRONTEND_ROOT) {
  const sources = listTestFiles(root).map((path) => [path, readFileSync(resolve(root, path), 'utf8')])
    .filter(([, text]) => text.includes('createHash') || READ_PATTERN.test(text));
  const unit = analyze(sources);
  const files = [], pins = [], digests = [];
  for (const [path, text] of sources) {
    if (text.includes('createHash')) digests.push(...auditDigests(path, text, unit(path)));
    if (!READ_PATTERN.test(text) || Object.hasOwn(DATA_READERS, path)) continue;
    files.push(path);
    pins.push(...auditTestSource(path, text, unit(path)));
  }
  return { files, pins: [...new Set(pins)].sort(), digests };
}

export function renderBaseline(pins) {
  const rows = pins.map((pin) => `  ${JSON.stringify(pin)},`).join('\n');
  return '// Generated by scripts/source-text-pin-inventory.mjs.\n'
    + `export const SOURCE_TEXT_PIN_BASELINE = Object.freeze([\n${rows}\n]);\n`;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.stdout.write(renderBaseline(scanSourceTextPins().pins));
}
