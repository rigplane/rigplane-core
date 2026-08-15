/** Pure, conservative object-flow helpers for the control-feedback inventory. */
const WRAPPERS = new Set([
  'TSAsExpression', 'TSTypeAssertion', 'TSNonNullExpression',
  'TSSatisfiesExpression', 'ParenthesizedExpression', 'ChainExpression',
]);
const SKIP = new Set(['parent', 'typeAnnotation', 'typeParameters', 'returnType', 'metadata']);
/** @typedef {{key: string|null, value: any, poison: boolean, start: number}} FlowEvent */
/** @typedef {{name: string, events: FlowEvent[]}} Root */
/** @typedef {{root: Root|null, string: string|null, mutable: boolean, initialized: boolean}} Binding */
/** @typedef {{parent: Scope|null, bindings: Map<string, Binding>}} Scope */

/** @param {any} node @returns {any|null} */
export function unwrapIdentifier(node) {
  while (node && WRAPPERS.has(node.type)) node = node.expression;
  return node?.type === 'Identifier' ? node : null;
}

/** @param {any} program @returns {Map<string, Array<{key: string|null, value: any, poison: boolean, start: number}>>} */
export function collectObjectFlow(program) {
  /** @type {Map<string, FlowEvent[]>} */
  const exposed = new Map();
  /** @param {Scope|null} [parent] @returns {Scope} */
  const scope = (parent = null) => ({ parent, bindings: new Map() });
  const rootScope = scope();
  /** @param {Scope} current @param {string} name @returns {Binding|null} */
  const lookup = (current, name) => {
    for (let cursor = /** @type {Scope|null} */ (current); cursor; cursor = cursor.parent) {
      if (cursor.bindings.has(name)) return cursor.bindings.get(name) ?? null;
    }
    return null;
  };
  /** @param {Scope} current @param {string} name @param {boolean} [mutable] @returns {Binding} */
  const declare = (current, name, mutable = true) => {
    /** @type {Binding} */
    const binding = { root: null, string: null, mutable, initialized: false };
    current.bindings.set(name, binding);
    return binding;
  };
  /** @param {any[]} body @param {Scope} current */
  const predeclare = (body, current) => {
    for (const statement of body ?? []) {
      if (statement.type === 'VariableDeclaration') {
        for (const item of statement.declarations) {
          if (item.id.type === 'Identifier') declare(current, item.id.name, statement.kind !== 'const');
        }
      } else if (statement.type === 'FunctionDeclaration' && statement.id) declare(current, statement.id.name);
    }
  };
  /** @param {any} node @param {Scope} current @returns {Root|null} */
  const resolvedRoot = (node, current) => {
    const identifier = unwrapIdentifier(node);
    return identifier ? lookup(current, identifier.name)?.root ?? null : null;
  };
  /** @param {any} node @param {Scope} current @returns {string|null} */
  const staticString = (node, current) => {
    while (node && WRAPPERS.has(node.type)) node = node.expression;
    if (node?.type === 'Literal' && typeof node.value === 'string') return node.value;
    if (node?.type === 'TemplateLiteral' && node.expressions.length === 0) return node.quasis[0].value.cooked;
    if (node?.type === 'Identifier') return lookup(current, node.name)?.string ?? null;
    return null;
  };
  /** @param {any} node @param {Scope} current @returns {Set<Root>} */
  const possibleRoots = (node, current) => {
    const direct = resolvedRoot(node, current);
    if (direct) return new Set([direct]);
    while (node && WRAPPERS.has(node.type)) node = node.expression;
    const parts = node?.type === 'SequenceExpression' ? [node.expressions.at(-1)]
      : node?.type === 'ConditionalExpression' ? [node.consequent, node.alternate]
        : node?.type === 'LogicalExpression' ? [node.left, node.right]
          : ['AwaitExpression', 'AssignmentExpression'].includes(node?.type) ? [node.argument ?? node.right] : [];
    return new Set(parts.flatMap((part) => [...possibleRoots(part, current)]));
  };
  /** @param {Root} root @param {string|null} key @param {any} value @param {boolean} poison @param {number|undefined} start */
  const event = (root, key, value, poison, start) => root.events.push({ key, value, poison, start: start ?? -1 });
  /** @param {Iterable<Root>} roots @param {number|undefined} start */
  const poisonRoots = (roots, start) => { for (const root of roots) event(root, null, null, true, start); };

  /** @param {any} node @param {Scope} current @returns {void} */
  const walk = (node, current) => {
    if (!node || typeof node !== 'object') return;
    if (node.type === 'Program' || node.type === 'BlockStatement') {
      const child = node.type === 'Program' ? current : scope(current);
      predeclare(node.body, child);
      for (const statement of node.body) walk(statement, child);
      return;
    }
    if (/Function(?:Declaration|Expression)$/.test(node.type) || node.type === 'ArrowFunctionExpression') {
      const child = scope(current);
      for (const parameter of node.params ?? []) if (parameter.type === 'Identifier') declare(child, parameter.name);
      walk(node.body, child);
      return;
    }
    if (['ForStatement', 'ForInStatement', 'ForOfStatement'].includes(node.type)) {
      const child = scope(current);
      const declaration = node.type === 'ForStatement' ? node.init : node.left;
      if (declaration?.type === 'VariableDeclaration') predeclare([declaration], child);
      if (node.type === 'ForStatement') {
        walk(node.init, child); walk(node.test, child); walk(node.update, child);
      } else {
        walk(node.right, current); walk(node.left, child);
      }
      walk(node.body, child);
      return;
    }
    if (node.type === 'CatchClause') {
      const child = scope(current);
      if (node.param?.type === 'Identifier') declare(child, node.param.name);
      walk(node.body, child);
      return;
    }
    if (node.type === 'VariableDeclaration') {
      for (const item of node.declarations) {
        if (item.id.type !== 'Identifier') continue;
        const binding = current.bindings.get(item.id.name) ?? declare(current, item.id.name, node.kind !== 'const');
        const alias = resolvedRoot(item.init, current);
        if (node.kind === 'const' && item.init?.type === 'ObjectExpression') {
          binding.root = { name: item.id.name, events: [] };
          if (current === rootScope) exposed.set(item.id.name, binding.root.events);
        } else if (node.kind === 'const' && alias) binding.root = alias;
        else if (alias) poisonRoots([alias], item.start);
        else poisonRoots(possibleRoots(item.init, current), item.start);
        binding.string = node.kind === 'const' ? staticString(item.init, current) : null;
        binding.initialized = true;
        if (!alias && item.init?.type !== 'ObjectExpression') walk(item.init, current);
      }
      return;
    }
    if (node.type === 'AssignmentExpression') {
      const member = node.left.type === 'MemberExpression' ? node.left : null;
      if (member) {
        const target = resolvedRoot(member.object, current);
        if (target) {
          const rawKey = member.computed ? staticString(member.property, current) : member.property.name;
          const key = rawKey === 'feedbackPolicy' ? 'feedback-policy' : rawKey;
          if (rawKey === null) event(target, null, null, true, node.start);
          else if (key === 'type' || key === 'feedback-policy') event(target, key, node.operator === '=' ? node.right : null, false, node.start);
        }
        if (member.computed) walk(member.property, current);
      } else if (node.left.type === 'Identifier') {
        const binding = lookup(current, node.left.name);
        if (binding) Object.assign(binding, { root: null, string: null, mutable: true });
      }
      walk(node.right, current);
      return;
    }
    if (node.type === 'CallExpression' || node.type === 'NewExpression') {
      const escaped = new Set();
      for (const argument of node.arguments ?? []) {
        const value = argument.type === 'SpreadElement' ? argument.argument : argument;
        for (const root of possibleRoots(value, current)) escaped.add(root);
        walk(value, current);
      }
      poisonRoots(escaped, node.start);
      walk(node.callee, current);
      return;
    }
    for (const [key, value] of Object.entries(node)) {
      if (SKIP.has(key)) continue;
      if (Array.isArray(value)) value.forEach((item) => walk(item, current));
      else if (value && typeof value === 'object') walk(value, current);
    }
  };

  walk(program, rootScope);
  return exposed;
}
