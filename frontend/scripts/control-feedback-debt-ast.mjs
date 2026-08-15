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
  const exposed = new Map(), scopes = new WeakMap(), rootDecls = new Set(), scope = (parent = null, fn = false) => ({ parent, fn, bindings: new Map() }), rootScope = scope(null, true);
  const get = (s, n) => { for (; s; s = s.parent) if (s.bindings.has(n)) return s.bindings.get(n); return null; };
  const fnScope = (s) => { while (s.parent && !s.fn) s = s.parent; return s; };
  const declare = (s, n) => s.bindings.get(n) ?? (s.bindings.set(n, { root: null, text: null }), s.bindings.get(n));
  const names = (s, p, kind = 'let') => boundNames(p).forEach((n) => declare(kind === 'var' ? fnScope(s) : s, n));
  const predeclare = (body, s) => (body ?? []).forEach((n) => { if (n.type === 'VariableDeclaration') n.declarations.forEach((d) => names(s, d.id, n.kind)); if (n.type === 'FunctionDeclaration' && n.id) declare(s, n.id.name); });
  const hoist = (n, s) => { if (!n || typeof n !== 'object' || /Function(?:Declaration|Expression)$/.test(n.type) || n.type === 'ArrowFunctionExpression') return; if (n.type === 'VariableDeclaration' && n.kind === 'var') n.declarations.forEach((d) => names(s, d.id, 'var')); Object.entries(n).forEach(([k, v]) => { if (!SKIP.has(k)) Array.isArray(v) ? v.forEach((x) => hoist(x, s)) : hoist(v, s); }); };
  const direct = (n, s) => { n = unwrapExpression(n).node; return n?.type === 'Identifier' ? get(s, n.name)?.root ?? null : null; };
  const text = (n, s) => { n = unwrapExpression(n).node; return n && Object.hasOwn(n, 'value') && (n.value === null || ['string', 'number', 'boolean', 'bigint'].includes(typeof n.value)) ? String(n.value) : n?.type === 'TemplateLiteral' && !n.expressions.length ? n.quasis[0].value.cooked : n?.type === 'Identifier' ? get(s, n.name)?.text ?? null : null; };
  const member = (n, s) => { n = unwrapExpression(n).node; if (!['MemberExpression', 'OptionalMemberExpression'].includes(n?.type)) return { key: null }; const p = n.property; return { key: n.computed ? text(p, s) : p?.name ?? null }; };
  const indexParams = (params, s) => (params ?? []).forEach((p) => { index(p.type === 'AssignmentPattern' ? p.right : p, s); names(s, p); });
  const indexFunction = (n, s) => { const child = scope(s, true); if (n.type === 'FunctionExpression' && n.id) declare(child, n.id.name); indexParams(n.params, child); if (n.body?.type === 'BlockStatement') { predeclare(n.body.body, child); hoist(n.body, child); n.body.body.forEach((x) => index(x, child)); } else index(n.body, child); };
  const index = (n, s) => { if (!n || typeof n !== 'object') return; scopes.set(n, s);
    if (n.type === 'Program') { predeclare(n.body, s); hoist(n, s); n.body.forEach((x) => index(x, s)); return; }
    if (n.type === 'BlockStatement') { const child = scope(s); predeclare(n.body, child); n.body.forEach((x) => index(x, child)); return; }
    if (n.type === 'FunctionDeclaration') return indexFunction(n, s);
    if (/FunctionExpression$/.test(n.type) || n.type === 'ArrowFunctionExpression') return indexFunction(n, s);
    if (n.type === 'CatchClause') { const child = scope(s); names(child, n.param); index(n.body, child); return; }
    if (['ForStatement', 'ForInStatement', 'ForOfStatement'].includes(n.type)) { const child = scope(s), d = n.init ?? n.left; if (d?.type === 'VariableDeclaration') predeclare([d], child); index(n.init, child); index(n.test, child); index(n.update, child); index(n.right, s); index(n.body, child); return; }
    if (n.type === 'VariableDeclaration') { n.declarations.forEach((d) => { const target = n.kind === 'var' ? fnScope(s) : s, b = d.id?.type === 'Identifier' ? get(target, d.id.name) : null, init = unwrapExpression(d.init).node; if (b && target === rootScope && n.kind === 'const' && init?.type === 'ObjectExpression') { b.root = { events: [] }; exposed.set(d.id.name, b.root.events); rootDecls.add(d); } if (b) b.text = n.kind === 'const' ? text(d.init, s) : null; index(d.init, s); }); return; }
    Object.entries(n).forEach(([k, v]) => { if (!SKIP.has(k)) Array.isArray(v) ? v.forEach((x) => index(x, s)) : index(v, s); });
  };
  index(program, rootScope);
  const evaluated = { ...program, body: (program.body ?? []).flatMap((n) => n.type !== 'VariableDeclaration' ? [n] : (() => { const declarations = n.declarations.filter((d) => !rootDecls.has(d)); return declarations.length ? [{ ...n, declarations }] : []; })()) };
  const emit = (root, key, value, poison, node, order) => { const events = root.events; if (!events.some((e) => e.key === key && e.poison === poison && e.order === order)) events.push({ key, value, poison, start: node?.start ?? -1, order }); };
  const poison = (rs, node, order) => rs.forEach((r) => emit(r, null, null, true, node, order));
  const factRoots = (nodes) => new Set((nodes ?? []).map((node) => direct(node, scopes.get(node) ?? rootScope)).filter(Boolean));
  evaluateOrderedEffects(evaluated, { isTracked: (node) => !!direct(node, scopes.get(node) ?? rootScope) }).forEach((fact, order) => {
    const roots = factRoots(fact.roots), targets = factRoots(fact.targetRoots), escapes = factRoots(fact.escapeRoots), escaped = new Set([...escapes].filter((root) => !targets.has(root))), s = scopes.get(fact.node) ?? rootScope;
    if (fact.kind === 'cycle') { poison(roots, fact.node, order); return; }
    if (escaped.size) poison(escaped, fact.node, order);
    if (fact.kind === 'mutation' && targets.size) {
      if (fact.node?.type !== 'AssignmentExpression') { poison(targets, fact.node, order); return; }
      const key = member(fact.node.left, s).key === 'feedbackPolicy' ? 'feedback-policy' : member(fact.node.left, s).key;
      if (key === null || fact.node.operator !== '=') poison(targets, fact.node, order);
      else if (key === 'type' || key === 'feedback-policy') targets.forEach((root) => emit(root, key, fact.node.right, false, fact.node, order));
      return;
    }
    if (!escaped.size) poison(escapes.size ? escapes : roots, fact.node, order);
  });
  for (const events of exposed.values()) events.sort((a, b) => a.order - b.order).forEach((e) => delete e.order);
  return exposed;
}
