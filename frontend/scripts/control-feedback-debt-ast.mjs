// @ts-nocheck -- parser AST nodes are intentionally version-agnostic.
import { evaluateOrderedEffects } from './control-feedback-debt-evaluator.mjs';
/** Conservative, parser-only object-flow primitives for debt analysis. */
const WRAPPERS = new Set(['ParenthesizedExpression', 'TSAsExpression', 'TSSatisfiesExpression', 'TSTypeAssertion', 'TSNonNullExpression', 'ChainExpression', 'TSInstantiationExpression']);
const SKIP = new Set(['parent', 'metadata', 'typeAnnotation', 'typeParameters', 'returnType', 'loc']);

/** @param {any} node */
export function unwrapExpression(node) {
  let wrapped = false;
  while (node && WRAPPERS.has(node.type)) { wrapped = true; node = node.expression; }
  return { node, wrapped };
}
/** @param {any} node @returns {string[]} */
export function boundNames(node) {
  if (!node) return [];
  if (node.type === 'Identifier') return [node.name];
  if (node.type === 'RestElement' || node.type === 'AssignmentPattern') return boundNames(node.argument ?? node.left);
  if (node.type === 'ArrayPattern') return node.elements.flatMap(boundNames);
  if (node.type === 'ObjectPattern') return node.properties.flatMap((p) => boundNames(p.value ?? p.argument));
  return [];
}

/** @param {any} program @returns {Map<string, Array<{key:string|null,value:any,poison:boolean,start:number}>>} */
export function collectObjectFlow(program) {
  const exposed = new Map(), scopes = new WeakMap(), active = new Set(), scope = (parent = null, fn = false) => ({ parent, fn, bindings: new Map() }), rootScope = scope(null, true);
  const get = (s, n) => { for (; s; s = s.parent) if (s.bindings.has(n)) return s.bindings.get(n); return null; };
  const fnScope = (s) => { while (s.parent && !s.fn) s = s.parent; return s; };
  const declare = (s, n, mutable = true) => s.bindings.get(n) ?? (s.bindings.set(n, { root: null, text: null, fn: null, mutable }), s.bindings.get(n));
  const names = (s, pattern, mutable, kind = 'let') => boundNames(pattern).forEach((n) => declare(kind === 'var' ? fnScope(s) : s, n, mutable));
  const predeclare = (body, s) => (body ?? []).forEach((n) => {
    if (n.type === 'VariableDeclaration') n.declarations.forEach((d) => names(s, d.id, n.kind !== 'const', n.kind));
    if (n.type === 'FunctionDeclaration' && n.id) declare(s, n.id.name);
  });
  const hoistVars = (n, s) => {
    if (!n || typeof n !== 'object' || /Function(?:Declaration|Expression)$/.test(n.type) || n.type === 'ArrowFunctionExpression') return;
    if (n.type === 'VariableDeclaration' && n.kind === 'var') n.declarations.forEach((d) => names(s, d.id, true, 'var'));
    Object.entries(n).forEach(([k, v]) => { if (!SKIP.has(k)) Array.isArray(v) ? v.forEach((x) => hoistVars(x, s)) : hoistVars(v, s); });
  };
  const direct = (n, s) => { n = unwrapExpression(n).node; return n?.type === 'Identifier' ? get(s, n.name)?.root ?? null : null; };
  const text = (n, s) => { n = unwrapExpression(n).node; return n && Object.hasOwn(n, 'value') && (n.value === null || ['string', 'number', 'boolean', 'bigint'].includes(typeof n.value)) ? String(n.value) : n?.type === 'TemplateLiteral' && !n.expressions.length ? n.quasis[0].value.cooked : n?.type === 'Identifier' ? get(s, n.name)?.text ?? null : null; };
  const roots = (n, s, seen = new Set()) => {
    if (!n || typeof n !== 'object' || seen.has(n)) return new Set(); seen.add(n);
    const hit = direct(n, s); if (hit) return new Set([hit]);
    const unwrapped = unwrapExpression(n).node;
    const parts = ['SequenceExpression', 'ArrayExpression'].includes(unwrapped?.type) ? unwrapped.expressions ?? unwrapped.elements
      : unwrapped?.type === 'ObjectExpression' ? unwrapped.properties.map((p) => p.value ?? p.argument)
        : ['ConditionalExpression', 'LogicalExpression'].includes(unwrapped?.type) ? [unwrapped.consequent ?? unwrapped.left, unwrapped.alternate ?? unwrapped.right]
          : ['AssignmentExpression', 'AwaitExpression', 'SpreadElement'].includes(unwrapped?.type) ? [unwrapped.right ?? unwrapped.argument] : null;
    if (parts) return new Set(parts.flatMap((p) => [...roots(p, s, seen)]));
    return new Set(Object.entries(unwrapped ?? n).filter(([k]) => !SKIP.has(k)).flatMap(([, v]) => Array.isArray(v) ? v.flatMap((x) => [...roots(x, s, seen)]) : [...roots(v, s, seen)]));
  };
  const emit = (r, key, value, poison, start) => r.events.push({ key, value, poison, start: start ?? -1 });
  const poison = (rs, start) => rs.forEach((r) => emit(r, null, null, true, start));
  const defaults = (p, s) => { if (!p) return; if (p.type === 'AssignmentPattern') { poison(roots(p.right, s), p.start); defaults(p.left, s); } else if (p.type === 'RestElement') defaults(p.argument, s); else if (p.type === 'ArrayPattern') p.elements.forEach((x) => defaults(x, s)); else if (p.type === 'ObjectPattern') p.properties.forEach((x) => defaults(x.value ?? x.argument, s)); };
  const member = (n, s) => {
    if (n?.type !== 'MemberExpression') return { root: null, key: null, safe: false };
    const root = direct(n.object, s), nested = root ? false : roots(n.object, s).size > 0;
    const p = n.property, staticKey = n.computed ? text(p, s) : null;
    return { root, key: n.computed ? staticKey : n.property?.name ?? null, safe: !!root && !nested };
  };
  const walk = (n, s) => {
    if (!n || typeof n !== 'object') return;
    scopes.set(n, s);
    if (n.type === 'Program' || n.type === 'BlockStatement') { const child = n.type === 'Program' ? s : scope(s); predeclare(n.body, child); if (n.type === 'Program') hoistVars(n, child); n.body.forEach((x) => walk(x, child)); return; }
    if (n.type === 'FunctionDeclaration') { declare(s, n.id.name).fn = n; return; }
    if (/FunctionExpression$/.test(n.type) || n.type === 'ArrowFunctionExpression') return;
    if (n.type === 'CatchClause') { const child = scope(s); names(child, n.param); walk(n.body, child); return; }
    if (['ForStatement', 'ForInStatement', 'ForOfStatement'].includes(n.type)) { const child = scope(s), d = n.init ?? n.left; if (d?.type === 'VariableDeclaration') predeclare([d], child); walk(n.right, s); walk(n.init, child); walk(n.test, child); walk(n.update, child); walk(n.body, child); return; }
    if (n.type === 'VariableDeclaration') { n.declarations.forEach((d) => {
      names(s, d.id, n.kind !== 'const', n.kind); const target = n.kind === 'var' ? fnScope(s) : s, b = d.id?.type === 'Identifier' ? get(target, d.id.name) : null, alias = direct(d.init, s);
      if (b && n.kind === 'const' && d.init?.type === 'ObjectExpression') { b.root = { events: [] }; if (target === rootScope) exposed.set(d.id.name, b.root.events); }
      else if (b && n.kind === 'const' && alias) b.root = alias;
      else poison(roots(d.init, s), d.start);
      if (b && n.kind === 'const') b.fn = unwrapExpression(d.init).node;
      if (b) b.text = n.kind === 'const' ? text(d.init, s) : null; walk(d.init, s);
    }); return; }
    if (n.type === 'AssignmentExpression') { walk(n.right, s); const m = member(n.left, s); if (m.root) {
      const key = m.key === 'feedbackPolicy' ? 'feedback-policy' : m.key;
      poison([...roots(n.right, s)].filter((r) => r !== m.root), n.start); if (!m.safe || key === null || n.operator !== '=') poison([m.root], n.start); else if (key === 'type' || key === 'feedback-policy') emit(m.root, key, n.right, false, n.start);
    } else if (n.left?.type === 'Identifier') { const b = get(s, n.left.name); if (b?.root) poison([b.root], n.start); poison(roots(n.right, s), n.start); if (b) b.root = null; }
      else { poison(roots(n.left, s), n.start); poison(roots(n.right, s), n.start); } return; }
    if (n.type === 'UpdateExpression' || n.type === 'UnaryExpression' && n.operator === 'delete') { poison(roots(n.argument, s), n.start); return; }
    if (['ReturnStatement', 'ThrowStatement', 'YieldExpression'].includes(n.type)) { walk(n.argument, s); poison(roots(n.argument, s), n.start); return; }
    if (n.type === 'CallExpression' || n.type === 'NewExpression') { const fn = n.callee?.type === 'Identifier' ? get(s, n.callee.name)?.fn : unwrapExpression(n.callee).node; n.arguments.forEach((x) => walk(x, s)); if (fn?.type === 'FunctionExpression' || fn?.type === 'ArrowFunctionExpression' || fn?.type === 'FunctionDeclaration') { if (active.has(fn)) poison(roots(fn, s), n.start); else { active.add(fn); const child = scope(s, true); if (fn.type === 'FunctionExpression' && fn.id) declare(child, fn.id.name); fn.params?.forEach((p) => { defaults(p, child); names(child, p); }); if (fn.body?.type === 'BlockStatement') { predeclare(fn.body.body, child); hoistVars(fn.body, child); fn.body.body.forEach((x) => walk(x, child)); } else walk(fn.body, child); active.delete(fn); } } else { poison(roots(n.callee, s), n.start); poison(roots(n.arguments, s), n.start); } return; }
    if (n.type === 'TaggedTemplateExpression') { walk(n.quasi, s); poison(roots(n.tag, s), n.start); poison(roots(n.quasi, s), n.start); return; }
    Object.entries(n).forEach(([k, v]) => { if (!SKIP.has(k)) Array.isArray(v) ? v.forEach((x) => walk(x, s)) : walk(v, s); });
  };
  walk(program, rootScope);
  for (const fact of evaluateOrderedEffects(program, { isTracked: (node) => roots(node, scopes.get(node) ?? rootScope).size > 0 })) {
    const rs = roots(fact.node, scopes.get(fact.node) ?? rootScope);
    if (rs.size && ![...exposed.values()].some((events) => events.some((event) => event.start === fact.node?.start))) poison(rs, fact.node?.start);
  }
  return exposed;
}
