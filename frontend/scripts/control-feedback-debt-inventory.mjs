#!/usr/bin/env node
/** AST-backed shrink-only inventory for radio range-control feedback debt (MOR-1713). */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, posix, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse } from 'svelte/compiler';
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
/** @typedef {{value: any, text: string}} Entry */
/** @typedef {{identity: string, file: string, kind: string, label: string, value: string, policy: string}} Site */
/** @param {string} key */
const keyOf = (key) => key === 'feedbackPolicy' ? 'feedback-policy' : key;

/** @param {any} node @returns {any} */
const unwrap = (node) => {
  while (node && ['TSAsExpression', 'TSTypeAssertion', 'TSNonNullExpression', 'ChainExpression', 'ParenthesizedExpression'].includes(node.type)) {
    node = node.expression;
  }
  return node;
};

/** @param {any} ast @returns {any[]} */
function programs(ast) {
  return [ast.instance?.content, ast.module?.content].filter(Boolean);
}

/** @param {string} source @param {string} file */
function valueControlImport(source, file) {
  const clean = source.split(/[?#]/, 1)[0];
  const importer = posix.dirname(posix.normalize(file.replaceAll('\\', '/')));
  const target = clean.startsWith('.') ? posix.normalize(posix.join(importer, clean))
    : clean.startsWith('/src/') ? posix.normalize(clean).slice(1) : '';
  if (target === `${VALUE_CONTROL_DIR}/ValueControl.svelte`) return 'direct';
  const stem = target.replace(/\.(?:[cm]?[jt]s)$/, '');
  return stem === VALUE_CONTROL_DIR || stem === `${VALUE_CONTROL_DIR}/index` ? 'barrel' : null;
}

/** @param {any} ast @param {string} file */
function bindingsAndImports(ast, file) {
  const bindings = new Map();
  /** @type {Map<string, any[]>} */ const aliases = new Map();
  /** @type {Map<string, any[]>} */ const mutations = new Map();
  const valueControls = new Set();
  /** @param {string} name @param {any} value */
  const addAlias = (name, value) => aliases.set(name, [...(aliases.get(name) ?? []), value]);
  /** @param {string} name @param {any} key @param {any} value */
  const addMutation = (name, key, value) => mutations.set(name, [...(mutations.get(name) ?? []), { key, value }]);
  /** @param {any} node */
  const collectAssignments = (node) => {
    if (!node || typeof node !== 'object') return;
    if (node.type === 'AssignmentExpression' && node.left.type === 'Identifier') addAlias(node.left.name, node.right);
    const member = node.type === 'AssignmentExpression' && node.left.type === 'MemberExpression' ? node.left : null;
    if (member?.object.type === 'Identifier' && bindings.has(member.object.name)) {
      const key = member.computed ? staticValue(member.property, bindings) : member.property.name;
      addMutation(member.object.name, key, node.operator === '=' ? node.right : null);
    }
    if (node.type === 'CallExpression' || node.type === 'NewExpression') {
      for (const argument of node.arguments) if (argument.type === 'Identifier' && bindings.has(argument.name)) addMutation(argument.name, UNKNOWN, null);
    }
    for (const value of Object.values(node)) {
      if (Array.isArray(value)) value.forEach(collectAssignments);
      else if (value && typeof value === 'object') collectAssignments(value);
    }
  };
  for (const program of programs(ast)) {
    for (const statement of program.body) {
      if (statement.type === 'ImportDeclaration') {
        const source = String(statement.source.value);
        const target = valueControlImport(source, file);
        for (const specifier of statement.specifiers) {
          const imported = specifier.imported?.name ?? specifier.imported?.value;
          if ((target === 'direct' && specifier.type === 'ImportDefaultSpecifier') || (target === 'barrel' && imported === 'ValueControl')) {
            valueControls.add(specifier.local.name);
          }
        }
      }
      if (statement.type === 'VariableDeclaration') {
        for (const declaration of statement.declarations) {
          if (declaration.id.type !== 'Identifier' || !declaration.init) continue;
          addAlias(declaration.id.name, declaration.init);
          if (statement.kind === 'const') bindings.set(declaration.id.name, declaration.init);
        }
      }
    }
    collectAssignments(program);
  }
  return { aliases, bindings, mutations, valueControls };
}

/** @param {any} node @param {Map<string, any>} bindings @param {Set<string>} [seen] @returns {any} */
function staticValue(node, bindings, seen = new Set()) {
  node = unwrap(node);
  if (!node) return UNKNOWN;
  if (node.type === 'Literal') return node.value;
  if (node.type === 'TemplateLiteral' && node.expressions.length === 0) return node.quasis[0].value.cooked;
  if (node.type === 'Identifier' && bindings.has(node.name) && !seen.has(node.name)) {
    return staticValue(bindings.get(node.name), bindings, new Set([...seen, node.name]));
  }
  if (node.type === 'ConditionalExpression') {
    const yes = staticValue(node.consequent, bindings, seen);
    const no = staticValue(node.alternate, bindings, seen);
    return yes !== UNKNOWN && Object.is(yes, no) ? yes : UNKNOWN;
  }
  return UNKNOWN;
}

/** @param {any} node @param {Map<string, any[]>} aliases @param {Set<string>} seeds @param {Set<string>} [seen] @returns {boolean} */
function mayBeValueControl(node, aliases, seeds, seen = new Set()) {
  node = unwrap(node);
  if (!node) return false;
  if (node.type === 'Identifier') {
    if (seeds.has(node.name)) return true;
    if (seen.has(node.name) || !aliases.has(node.name)) return false;
    return (aliases.get(node.name) ?? []).some((value) => mayBeValueControl(value, aliases, seeds, new Set([...seen, node.name])));
  }
  if (node.type === 'ConditionalExpression') {
    return mayBeValueControl(node.consequent, aliases, seeds, seen)
      || mayBeValueControl(node.alternate, aliases, seeds, seen);
  }
  if (node.type === 'LogicalExpression' || node.type === 'SequenceExpression') {
    return Object.values(node).some((value) => Boolean(value?.type && mayBeValueControl(value, aliases, seeds, seen)));
  }
  return false;
}

/** @param {any} node @param {string} source */
function expressionText(node, source) {
  node = unwrap(node);
  return node?.start === undefined ? 'dynamic' : source.slice(node.start, node.end).replace(/\s+/g, ' ').trim();
}

/** @param {any} attribute @param {Map<string, any>} bindings @param {string} source @returns {Entry} */
function attributeEntry(attribute, bindings, source) {
  if (attribute.value === true) return { value: true, text: 'true' };
  const value = attribute.value;
  if (Array.isArray(value)) {
    if (value.length === 1 && value[0].type === 'Text') return { value: value[0].data, text: value[0].data };
    return { value: UNKNOWN, text: source.slice(attribute.start, attribute.end) };
  }
  const expression = value?.type === 'ExpressionTag' ? value.expression : value;
  return { value: staticValue(expression, bindings), text: expressionText(expression, source) };
}

/** @param {Map<string, Entry>} result @param {any} rawKey @param {any} value @param {Map<string, any>} bindings @param {string} source */
function setEntry(result, rawKey, value, bindings, source) {
  if (rawKey === UNKNOWN || typeof rawKey !== 'string') {
    for (const key of RELEVANT) result.set(key, { value: UNKNOWN, text: 'dynamic' });
    return;
  }
  const key = keyOf(rawKey);
  if (RELEVANT.includes(key)) result.set(key, value ? {
    value: staticValue(value, bindings), text: expressionText(value, source),
  } : { value: UNKNOWN, text: 'dynamic' });
}

/** @param {any} node @param {Map<string, any>} bindings @param {Map<string, any[]>} mutations @param {string} source @param {Set<string>} [seen] @returns {Map<string, Entry>|null} */
function objectEntries(node, bindings, mutations, source, seen = new Set()) {
  node = unwrap(node);
  if (node?.type === 'Identifier' && bindings.has(node.name) && !seen.has(node.name)) {
    const result = objectEntries(bindings.get(node.name), bindings, mutations, source, new Set([...seen, node.name]));
    if (result) for (const mutation of mutations.get(node.name) ?? []) setEntry(result, mutation.key, mutation.value, bindings, source);
    return result;
  }
  if (node?.type !== 'ObjectExpression') return null;
  const result = new Map();
  for (const property of node.properties) {
    if (property.type === 'SpreadElement') {
      const nested = objectEntries(property.argument, bindings, mutations, source, seen);
      if (!nested) for (const key of RELEVANT) result.set(key, { value: UNKNOWN, text: 'dynamic' });
      else for (const [key, value] of nested) result.set(key, value);
      continue;
    }
    if (property.type !== 'Property') continue;
    setEntry(result, property.computed ? staticValue(property.key, bindings) : property.key.name ?? property.key.value, property.value, bindings, source);
  }
  return result;
}

/** @param {any} node @param {Map<string, any>} bindings @param {Map<string, any[]>} mutations @param {string} source @returns {Map<string, Entry>} */
function effectiveAttributes(node, bindings, mutations, source) {
  const result = new Map();
  for (const attribute of node.attributes ?? []) {
    if (attribute.type === 'SpreadAttribute') {
      const entries = objectEntries(attribute.expression, bindings, mutations, source);
      if (!entries) for (const key of RELEVANT) result.set(key, { value: UNKNOWN, text: 'dynamic' });
      else for (const [key, value] of entries) result.set(key, value);
    } else if (attribute.type === 'Attribute' && RELEVANT.includes(keyOf(attribute.name))) {
      result.set(keyOf(attribute.name), attributeEntry(attribute, bindings, source));
    }
  }
  return result;
}

/** @param {Map<string, Entry>} attributes @param {string} identity */
function sitePolicy(attributes, identity) {
  const entry = attributes.get('feedback-policy');
  if (!entry) return 'radio-backed';
  if (entry.value === UNKNOWN || typeof entry.value !== 'string') throw new Error(`${identity}: static feedback policy required`);
  if (!POLICIES.has(entry.value)) throw new Error(`${identity}: unknown feedback policy ${entry.value}`);
  return entry.value;
}

/** @param {string} file @param {string} kind @param {Map<string, Entry>} attributes */
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
  const { aliases, bindings, mutations, valueControls } = bindingsAndImports(ast, file);
  /** @type {Site[]} */
  const sites = [];
  /** @param {any} node */
  const visit = (node) => {
    if (!node || typeof node !== 'object') return;
    let kind = null;
    if (node.type === 'Component' && mayBeValueControl({ type: 'Identifier', name: node.name }, aliases, valueControls)) kind = 'ValueControl';
    if (node.type === 'RegularElement' && node.name === 'input') kind = 'input';
    if (node.type === 'SvelteElement') {
      const tag = staticValue(node.tag, bindings);
      if (tag === 'input' || tag === UNKNOWN) kind = 'input';
    }
    if (kind) {
      const attributes = effectiveAttributes(node, bindings, mutations, source);
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

/** @param {string} directory @returns {string[]} */
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
