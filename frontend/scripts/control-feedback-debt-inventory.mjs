#!/usr/bin/env node
// @ts-nocheck -- parser AST nodes and lexical scope records are intentionally version-agnostic.
/** AST-backed shrink-only inventory for radio range-control feedback debt (MOR-1713). */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, posix, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse } from 'svelte/compiler';
import { boundNames, collectSvelteObjectFlows, unwrapExpression } from './control-feedback-debt-ast.mjs';
import { CONTROL_FEEDBACK_DEBT_BASELINE } from './control-feedback-debt-baseline.mjs';

const UNKNOWN = Symbol('dynamic');
const POLICIES = new Set(['radio-backed', 'feedback-integrated', 'local-resource']);
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const EXACT_DEMO_FILES = new Set([
  'src/components-v2/controls/ControlButtonDemo.svelte',
  'src/components-v2/controls/ValueControlLab.svelte',
  'src/components-v2/meters/SMeterDemo.svelte',
]);
const RELEVANT = ['type', 'feedback-policy', 'aria-label', 'label', 'value'];
const VALUE_CONTROL_DIR = 'src/components-v2/controls/value-control';
const FROZEN_RADIO_DEBT = new Set(CONTROL_FEEDBACK_DEBT_BASELINE);
/** @typedef {{identity: string, file: string, kind: string, label: string, value: string, policy: string}} Site */
const keyOf = (key) => key === 'feedbackPolicy' ? 'feedback-policy' : key;

function valueControlImport(source, file) {
  const clean = source.split(/[?#]/, 1)[0];
  const importer = posix.dirname(posix.normalize(file.replaceAll('\\', '/')));
  const target = clean.startsWith('.') ? posix.normalize(posix.join(importer, clean))
    : clean.startsWith('/src/') ? posix.normalize(clean).slice(1) : '';
  if (target === `${VALUE_CONTROL_DIR}/ValueControl.svelte`) return 'direct';
  const stem = target.replace(/\.(?:[cm]?[jt]s)$/, '');
  return stem === VALUE_CONTROL_DIR || stem === `${VALUE_CONTROL_DIR}/index` ? 'barrel' : null;
}

function bindingsAndImports(ast, file) {
  const flows = collectSvelteObjectFlows(ast.module?.content, ast.instance?.content);
  const makeScope = (outer, objectFlows) => ({
    bindings: new Map(outer?.bindings), aliases: new Map(outer?.aliases),
    objectFlows, valueControls: new Set(outer?.valueControls),
  });
  let scope;
  const addAlias = (name, value) => scope.aliases.set(name, [...(scope.aliases.get(name) ?? []), { node: value, scope }]);
  const shadow = (name) => { scope.aliases.delete(name); scope.bindings.delete(name); scope.valueControls.delete(name); };
  const collectAssignments = (node) => {
    if (!node || typeof node !== 'object') return;
    if (node.type === 'AssignmentExpression' && node.left.type === 'Identifier') addAlias(node.left.name, node.right);
    for (const value of Object.values(node)) {
      if (Array.isArray(value)) value.forEach(collectAssignments);
      else if (value && typeof value === 'object') collectAssignments(value);
    }
  };
  for (const [program, objectFlows] of [[ast.module?.content, flows.module], [ast.instance?.content, flows.instance]]) {
    if (!program) continue;
    scope = makeScope(scope, objectFlows);
    if (program === ast.instance?.content) for (const name of flows.module.keys()) if (!objectFlows.has(name)) shadow(name);
    for (const statement of program.body) {
      if (statement.id?.type === 'Identifier') shadow(statement.id.name);
      if (statement.type === 'ImportDeclaration') {
        const source = String(statement.source.value);
        const target = valueControlImport(source, file);
        for (const specifier of statement.specifiers) {
          shadow(specifier.local.name);
          const imported = specifier.imported?.name ?? specifier.imported?.value;
          if ((target === 'direct' && specifier.type === 'ImportDefaultSpecifier') || (target === 'barrel' && imported === 'ValueControl')) {
            scope.valueControls.add(specifier.local.name);
          }
        }
      }
      if (statement.type === 'VariableDeclaration') {
        for (const declaration of statement.declarations) {
          for (const name of boundNames(declaration.id)) shadow(name);
          if (declaration.id.type !== 'Identifier' || !declaration.init) continue;
          addAlias(declaration.id.name, declaration.init);
          if (statement.kind === 'const') scope.bindings.set(declaration.id.name, { node: declaration.init, scope });
        }
      }
    }
    collectAssignments(program);
  }
  return scope ?? makeScope(null, flows.instance);
}

function staticValue(node, scope, seen = new Set()) {
  node = unwrapExpression(node).node;
  if (!node) return UNKNOWN;
  if (node.type === 'Literal') return node.value;
  if (node.type === 'TemplateLiteral' && node.expressions.length === 0) return node.quasis[0].value.cooked;
  const binding = node.type === 'Identifier' ? scope.bindings.get(node.name) : null;
  if (binding && !seen.has(binding)) {
    return staticValue(binding.node, binding.scope, new Set([...seen, binding]));
  }
  if (node.type === 'ConditionalExpression') {
    const yes = staticValue(node.consequent, scope, seen);
    const no = staticValue(node.alternate, scope, seen);
    return yes !== UNKNOWN && Object.is(yes, no) ? yes : UNKNOWN;
  }
  return UNKNOWN;
}

function mayBeValueControl(node, scope, seen = new Set()) {
  node = unwrapExpression(node).node;
  if (!node) return false;
  if (node.type === 'Identifier') {
    if (scope.valueControls.has(node.name)) return true;
    return (scope.aliases.get(node.name) ?? []).some((binding) => !seen.has(binding)
      && mayBeValueControl(binding.node, binding.scope, new Set([...seen, binding])));
  }
  if (node.type === 'ConditionalExpression') {
    return mayBeValueControl(node.consequent, scope, seen)
      || mayBeValueControl(node.alternate, scope, seen);
  }
  if (node.type === 'LogicalExpression' || node.type === 'SequenceExpression') {
    return Object.values(node).some((value) => Boolean(value?.type && mayBeValueControl(value, scope, seen)));
  }
  return false;
}

function expressionText(node, source) {
  node = unwrapExpression(node).node;
  return node?.start === undefined ? 'dynamic' : source.slice(node.start, node.end).replace(/\s+/g, ' ').trim();
}

function attributeEntry(attribute, scope, source) {
  if (attribute.value === true) return { value: true, text: 'true' };
  const value = attribute.value;
  if (Array.isArray(value)) {
    if (value.length === 1 && value[0].type === 'Text') return { value: value[0].data, text: value[0].data };
    return { value: UNKNOWN, text: source.slice(attribute.start, attribute.end) };
  }
  const expression = value?.type === 'ExpressionTag' ? value.expression : value;
  return { value: staticValue(expression, scope), text: expressionText(expression, source) };
}

function setEntry(result, rawKey, value, scope, source) {
  if (rawKey === UNKNOWN || typeof rawKey !== 'string') {
    for (const key of RELEVANT) result.set(key, { value: UNKNOWN, text: 'dynamic' });
    return;
  }
  const key = keyOf(rawKey);
  if (RELEVANT.includes(key)) result.set(key, value ? {
    value: staticValue(value, scope), text: expressionText(value, source),
  } : { value: UNKNOWN, text: 'dynamic' });
}

function objectEntries(node, scope, source, seen = new Set()) {
  node = unwrapExpression(node).node;
  const binding = node?.type === 'Identifier' ? scope.bindings.get(node.name) : null;
  if (binding && !seen.has(binding)) {
    const result = objectEntries(binding.node, binding.scope, source, new Set([...seen, binding]));
    if (result) for (const event of binding.scope.objectFlows.get(node.name) ?? []) setEntry(result, event.poison ? UNKNOWN : event.key, event.value, binding.scope, source);
    return result;
  }
  if (node?.type !== 'ObjectExpression') return null;
  const result = new Map();
  for (const property of node.properties) {
    if (property.type === 'SpreadElement') {
      const nested = objectEntries(property.argument, scope, source, seen);
      if (!nested) for (const key of RELEVANT) result.set(key, { value: UNKNOWN, text: 'dynamic' });
      else for (const [key, value] of nested) result.set(key, value);
      continue;
    }
    if (property.type !== 'Property') continue;
    setEntry(result, property.computed ? staticValue(property.key, scope) : property.key.name ?? property.key.value, property.value, scope, source);
  }
  return result;
}

function effectiveAttributes(node, scope, source) {
  const result = new Map();
  for (const attribute of node.attributes ?? []) {
    if (attribute.type === 'SpreadAttribute') {
      const entries = objectEntries(attribute.expression, scope, source);
      if (!entries) for (const key of RELEVANT) result.set(key, { value: UNKNOWN, text: 'dynamic' });
      else for (const [key, value] of entries) result.set(key, value);
    } else if (attribute.type === 'Attribute' && RELEVANT.includes(keyOf(attribute.name))) {
      result.set(keyOf(attribute.name), attributeEntry(attribute, scope, source));
    }
  }
  return result;
}

function sitePolicy(attributes, identity) {
  const entry = attributes.get('feedback-policy');
  if (!entry) return 'radio-backed';
  if (entry.value === UNKNOWN || typeof entry.value !== 'string') throw new Error(`${identity}: static feedback policy required`);
  if (!POLICIES.has(entry.value)) throw new Error(`${identity}: unknown feedback policy ${entry.value}`);
  return entry.value;
}

function isLocalGain(file, kind, attributes) {
  const normalizedFile = posix.normalize(file.replaceAll('\\', '/'));
  if (kind !== 'input' || normalizedFile !== 'src/components-v2/panels/AudioRoutingControl.svelte') return false;
  const label = attributes.get('aria-label')?.value;
  const value = attributes.get('value')?.text;
  return (label === 'MAIN gain in decibels' && value === 'mainGainDb')
    || (label === 'SUB gain in decibels' && value === 'subGainDb');
}

/** @param {string} file @param {string} source @returns {Site[]} */
export function auditSvelteSource(file, source) {
  const ast = parse(source, { modern: true, filename: file });
  const scope = bindingsAndImports(ast, file);
  const sites = [];
  const visit = (node) => {
    if (!node || typeof node !== 'object') return;
    let kind = null;
    if (node.type === 'Component' && mayBeValueControl({ type: 'Identifier', name: node.name }, scope)) kind = 'ValueControl';
    if (node.type === 'RegularElement' && node.name === 'input') kind = 'input';
    if (node.type === 'SvelteElement') {
      const tag = staticValue(node.tag, scope);
      if (tag === 'input' || tag === UNKNOWN) kind = 'input';
    }
    if (kind) {
      const attributes = effectiveAttributes(node, scope, source);
      const type = attributes.get('type')?.value;
      if (kind === 'ValueControl' || type === 'range' || type === UNKNOWN) {
        const label = attributes.get(kind === 'input' ? 'aria-label' : 'label');
        const value = attributes.get('value');
        const identity = `${file}::${kind}::${label?.text ?? 'unlabelled'}::${value?.text ?? 'unbound'}`;
        let policy = sitePolicy(attributes, identity);
        if (policy === 'radio-backed' && !attributes.has('feedback-policy') && isLocalGain(file, kind, attributes)) policy = 'local-resource';
        if (policy === 'local-resource' && !isLocalGain(file, kind, attributes)) throw new Error(`${identity}: local-resource policy is not allowed`);
        sites.push({ identity, file, kind, label: label?.text ?? 'unlabelled', value: value?.text ?? 'unbound', policy });
      }
    }
    for (const [key, value] of Object.entries(node)) {
      if (key === 'metadata' || key === 'parent') continue;
      if (Array.isArray(value)) value.forEach(visit);
      else if (value && typeof value === 'object') visit(value);
    }
  };
  visit(ast.fragment);
  const duplicate = sites.find((site, index) => sites.findIndex((other) => other.identity === site.identity) !== index);
  if (duplicate) throw new Error(`${duplicate.identity}: duplicate control identity`);
  return sites;
}

/** @param {Array<{identity: string, policy?: string}>} sites @param {Set<string>} [frozen] */
export function assertShrinkOnly(sites, frozen = FROZEN_RADIO_DEBT) {
  const current = sites.filter((site) => !site.policy || site.policy === 'radio-backed').map((site) => site.identity).sort();
  const grew = current.filter((identity) => !frozen.has(identity));
  if (grew.length) throw new Error(`radio-backed feedback debt grew: ${grew.join(', ')}`);
  return current;
}

function svelteFiles(directory) {
  return readdirSync(directory).flatMap((name) => {
    const path = resolve(directory, name);
    return statSync(path).isDirectory() ? svelteFiles(path) : path.endsWith('.svelte') ? [path] : [];
  });
}

/** @param {string} [root] @returns {Site[]} */
export function scanRepository(root = ROOT) {
  return svelteFiles(resolve(root, 'src')).flatMap((path) => {
    const file = relative(root, path);
    return EXACT_DEMO_FILES.has(file) ? [] : auditSvelteSource(file, readFileSync(path, 'utf8'));
  });
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const current = assertShrinkOnly(scanRepository());
  console.log(`control-feedback debt inventory: ${current.length} radio-backed range site(s)`);
}
