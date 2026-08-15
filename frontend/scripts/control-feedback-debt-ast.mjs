// @ts-nocheck -- parser AST nodes are intentionally version-agnostic.
import { evaluateOrderedEffects } from './control-feedback-debt-evaluator.mjs';

const WRAPPERS = new Set(['ParenthesizedExpression', 'TSAsExpression', 'TSSatisfiesExpression', 'TSTypeAssertion', 'TSNonNullExpression', 'ChainExpression', 'TSInstantiationExpression']);
const SKIP = new Set(['parent', 'metadata', 'typeAnnotation', 'typeParameters', 'returnType', 'loc']);

/** @param {any} node */
export function unwrapExpression(node) { let wrapped = false; while (node && WRAPPERS.has(node.type)) { wrapped = true; node = node.expression; } return { node, wrapped }; }
/** @param {any} node @returns {string[]} */
export function boundNames(node) { if (!node) return []; if (node.type === 'Identifier') return [node.name]; if (node.type === 'RestElement' || node.type === 'AssignmentPattern') return boundNames(node.argument ?? node.left); if (node.type === 'ArrayPattern') return node.elements.flatMap(boundNames); if (node.type === 'ObjectPattern') return node.properties.flatMap((p) => boundNames(p.value ?? p.argument)); return []; }

/** Index lexical identity and turn evaluator facts into canonical debt events. @param {any} program */
export function collectObjectFlow(program) {
  const exposed = new Map(), scopes = new WeakMap(), rootDecls = new Set(), defaults = new WeakSet(), deletes = new WeakSet(), scope = (parent = null, fn = false) => ({ parent, fn, bindings: new Map() }), rootScope = scope(null, true);
  const get = (s, n) => { for (; s; s = s.parent) if (s.bindings.has(n)) return s.bindings.get(n); return null; };
  const fnScope = (s) => { while (s.parent && !s.fn) s = s.parent; return s; };
  const declare = (s, n, mutable = true) => s.bindings.get(n) ?? (s.bindings.set(n, { root: null, text: null, fn: null, mutable }), s.bindings.get(n));
  const names = (s, p, mutable = true, kind = 'let') => boundNames(p).forEach((n) => declare(kind === 'var' ? fnScope(s) : s, n, mutable));
  const predeclare = (body, s) => (body ?? []).forEach((n) => { if (n.type === 'VariableDeclaration') n.declarations.forEach((d) => names(s, d.id, n.kind !== 'const', n.kind)); if (n.type === 'FunctionDeclaration' && n.id) { const b = declare(s, n.id.name); b.fn = n; } });
  const hoist = (n, s) => { if (!n || typeof n !== 'object' || /Function(?:Declaration|Expression)$/.test(n.type) || n.type === 'ArrowFunctionExpression') return; if (n.type === 'VariableDeclaration' && n.kind === 'var') n.declarations.forEach((d) => names(s, d.id, true, 'var')); Object.entries(n).forEach(([k, v]) => { if (!SKIP.has(k)) Array.isArray(v) ? v.forEach((x) => hoist(x, s)) : hoist(v, s); }); };
  const direct = (n, s) => { n = unwrapExpression(n).node; return n?.type === 'Identifier' ? get(s, n.name)?.root ?? null : null; };
  const text = (n, s) => { n = unwrapExpression(n).node; return n && Object.hasOwn(n, 'value') && (n.value === null || ['string', 'number', 'boolean', 'bigint'].includes(typeof n.value)) ? String(n.value) : n?.type === 'TemplateLiteral' && !n.expressions.length ? n.quasis[0].value.cooked : n?.type === 'Identifier' ? get(s, n.name)?.text ?? null : null; };
  const roots = (n, s, seen = new Set()) => { if (!n || typeof n !== 'object' || seen.has(n)) return new Set(); seen.add(n); const here = scopes.get(n) ?? s, hit = direct(n, here); if (hit) return new Set([hit]); const x = unwrapExpression(n).node; if (x?.type === 'ObjectExpression') return new Set(x.properties.flatMap((p) => [...roots(p.value ?? p.argument, here, seen)])); if (x?.type === 'MemberExpression') return new Set([...roots(x.object, here, seen), ...(x.computed ? roots(x.property, here, seen) : [])]); if (/Property$/.test(x?.type ?? '')) return roots(x.value ?? x.argument, here, seen); return new Set(Object.entries(x ?? n).filter(([k]) => !SKIP.has(k)).flatMap(([, v]) => Array.isArray(v) ? v.flatMap((p) => [...roots(p, here, seen)]) : [...roots(v, here, seen)])); };
  const member = (n, s) => { if (n?.type !== 'MemberExpression') return { root: null, key: null, safe: false }; const root = direct(n.object, s), p = n.property; return { root, key: n.computed ? text(p, s) : p?.name ?? null, safe: !!root }; };
  const mark = (n, set) => { if (!n || typeof n !== 'object' || set.has(n)) return; set.add(n); Object.entries(n).forEach(([k, v]) => { if (!SKIP.has(k)) Array.isArray(v) ? v.forEach((x) => mark(x, set)) : mark(v, set); }); };
  const indexParams = (params, s) => (params ?? []).forEach((p) => { if (p.type === 'AssignmentPattern') mark(p.right, defaults); index(p.type === 'AssignmentPattern' ? p.right : p, s); names(s, p); });
  const indexFunction = (n, s) => { const child = scope(s, true); if (n.type === 'FunctionExpression' && n.id) declare(child, n.id.name).fn = n; indexParams(n.params, child); if (n.body?.type === 'BlockStatement') { predeclare(n.body.body, child); hoist(n.body, child); n.body.body.forEach((x) => index(x, child)); } else index(n.body, child); };
  const index = (n, s) => { if (!n || typeof n !== 'object') return; scopes.set(n, s);
    if (n.type === 'Program') { predeclare(n.body, s); hoist(n, s); n.body.forEach((x) => index(x, s)); return; }
    if (n.type === 'BlockStatement') { const child = scope(s); predeclare(n.body, child); n.body.forEach((x) => index(x, child)); return; }
    if (n.type === 'FunctionDeclaration') return indexFunction(n, s);
    if (/FunctionExpression$/.test(n.type) || n.type === 'ArrowFunctionExpression') return indexFunction(n, s);
    if (n.type === 'CatchClause') { const child = scope(s); names(child, n.param); index(n.body, child); return; }
    if (['ForStatement', 'ForInStatement', 'ForOfStatement'].includes(n.type)) { const child = scope(s), d = n.init ?? n.left; if (d?.type === 'VariableDeclaration') predeclare([d], child); index(n.init, child); index(n.test, child); index(n.update, child); index(n.right, s); index(n.body, child); return; }
    if (n.type === 'VariableDeclaration') { n.declarations.forEach((d) => { const target = n.kind === 'var' ? fnScope(s) : s, b = d.id?.type === 'Identifier' ? get(target, d.id.name) : null, alias = direct(d.init, s); if (b && n.kind === 'const' && d.init?.type === 'ObjectExpression') { b.root = { events: [] }; if (target === rootScope) { exposed.set(d.id.name, b.root.events); rootDecls.add(d); } } else if (b && n.kind === 'const' && alias) b.root = alias; if (b) b.text = n.kind === 'const' ? text(d.init, s) : null; index(d.init, s); }); return; }
    if (n.type === 'UnaryExpression' && n.operator === 'delete') mark(n.argument, deletes);
    Object.entries(n).forEach(([k, v]) => { if (!SKIP.has(k)) Array.isArray(v) ? v.forEach((x) => index(x, s)) : index(v, s); });
  };
  index(program, rootScope);
  const cycleRoots = (n, seen = new Set()) => { if (!n || seen.has(n)) return new Set(); seen.add(n); const out = roots(n, scopes.get(n) ?? rootScope), visit = (x) => { if (!x || typeof x !== 'object') return; if (x.type === 'CallExpression' && x.callee?.type === 'Identifier') { const fn = get(scopes.get(x.callee) ?? rootScope, x.callee.name)?.fn; for (const root of cycleRoots(fn, seen)) out.add(root); } Object.entries(x).forEach(([k, v]) => { if (!SKIP.has(k)) Array.isArray(v) ? v.forEach(visit) : visit(v); }); }; visit(n.body); return out; };
  const evaluated = { ...program, body: (program.body ?? []).flatMap((n) => n.type !== 'VariableDeclaration' ? [n] : (() => { const declarations = n.declarations.filter((d) => !rootDecls.has(d)); return declarations.length ? [{ ...n, declarations }] : []; })()) };
  const emit = (root, key, value, poison, node, order) => { const events = root.events; if (!events.some((e) => e.key === key && e.poison === poison && e.order === order)) events.push({ key, value, poison, start: node?.start ?? -1, order }); };
  const poison = (rs, node, order) => rs.forEach((r) => emit(r, null, null, true, node, order));
  for (const root of new Set([...exposed.keys()].map((name) => get(rootScope, name).root))) {
    const reads = [], facts = evaluateOrderedEffects(evaluated, { isTracked: (node) => { const hit = direct(node, scopes.get(node) ?? rootScope) === root; if (hit && (defaults.has(node) || deletes.has(node))) reads.push(node); return hit; } });
    const records = facts.map((fact) => ({ fact }));
    reads.filter((node) => !facts.some((fact) => fact.node?.start <= node.start && fact.node?.end >= node.end)).forEach((node) => { const at = records.findIndex(({ fact }) => fact.node?.start > node.start); records.splice(at < 0 ? records.length : at, 0, { node }); });
    records.forEach(({ fact, node }, order) => {
      if (!fact) return poison([root], node, order);
      const s = scopes.get(fact.node) ?? rootScope, rs = fact.kind === 'cycle' ? cycleRoots(fact.node) : new Set([root]);
      if (fact.kind === 'cycle') for (const hit of rs) hit.events.splice(0, hit.events.length, ...hit.events.filter((event) => event.poison));
      if (fact.kind === 'mutation' && fact.node?.type === 'AssignmentExpression') { const m = member(fact.node.left, s), key = m.key === 'feedbackPolicy' ? 'feedback-policy' : m.key, rhs = roots(fact.node.right, s); if (m.root === root || !m.root && !rhs.has(root)) { if (key === null || fact.node.operator !== '=') poison([root], fact.node, order); else if (key === 'type' || key === 'feedback-policy') emit(root, key, fact.node.right, false, fact.node, order); } else poison(rs, fact.node, order); }
      else poison(rs, fact.node, order);
    });
  }
  for (const events of exposed.values()) events.sort((a, b) => a.order - b.order).forEach((e) => delete e.order);
  return exposed;
}
