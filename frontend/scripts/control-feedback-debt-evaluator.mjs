// @ts-nocheck -- parser AST nodes intentionally remain version-neutral plain objects.
import { expressionChildren, isGenuineUndefined, unwrapExpression } from './control-feedback-debt-expression.mjs';

/**
 * Evaluate parser expressions in execution order; callers retain canonical identity ownership.
 * @param {object} program
 * @param {{ isTracked?: (node: any, scope: any) => boolean }} options
 */
export function evaluateOrderedEffects(program, { isTracked = () => false } = {}) {
  const facts = [], active = new Map(), scope = (parent = null) => Object.create(parent);
  const frozen = (values = []) => Object.freeze([...new Set(values)]);
  const roots = (values) => frozen(values.flatMap((value) => value?.roots || []));
  const noteActive = (provenance) => { if (provenance?.length) for (const state of active.values()) state.roots = frozen([...state.roots, ...provenance]); };
  const emit = (kind, node, extra = {}) => { facts.push(Object.freeze({ kind, node, ...extra })); noteActive(extra.roots || extra.targetRoots || extra.escapeRoots); };
  const merge = (values) => { const provenance = roots(values); return { track: provenance.length > 0 || values.some((value) => value?.track), roots: provenance }; };
  const hasBinding = (env, name) => !!env && name in env;
  const binding = (env, name) => hasBinding(env, name) ? env[name] : undefined;
  const known = (value) => ({ known: true, value, track: false });
  const isKnown = (value) => value?.known === true;
  const unboundUndefined = (node, env) => isGenuineUndefined(node, (name) => hasBinding(env, name));
  const read = (node, env) => {
    node = unwrapExpression(node);
    if (!node) return { missing: true };
    if (node.type === 'Identifier') {
      const value = binding(env, node.name)?.value;
      if (value) return value;
      const track = isTracked(node, env);
      return { track, roots: track ? frozen([node]) : frozen() };
    }
    if (node.type === 'MemberExpression' || node.type === 'OptionalMemberExpression') return merge([read(node.object, env), node.computed ? read(node.property, env) : null]);
    const track = isTracked(node, env); return { track, roots: track ? frozen([node]) : frozen() };
  };
  const callable = (node, env) => {
    node = unwrapExpression(node);
    if (node?.type === 'Identifier') return binding(env, node.name)?.fn;
    return node && ['FunctionExpression', 'ArrowFunctionExpression'].includes(node.type) ? { node, env } : undefined;
  };
  const hoist = (body, env) => { for (const node of body) if (node.type === 'FunctionDeclaration' && node.id) env[node.id.name] = { fn: { node, env }, value: { track: false } }; };
  const bind = (pattern, value, env) => {
    pattern = unwrapExpression(pattern); value ||= { missing: true };
    if (!pattern) return;
    if (pattern.type === 'AssignmentPattern') { const next = value.missing || value.undefined || isKnown(value) && value.value === undefined ? evaluate(pattern.right, env) : value; return next.abrupt ? next : bind(pattern.left, next, env); }
    if (pattern.type === 'Identifier') { env[pattern.name] = { value }; return; }
    if (pattern.type === 'RestElement') return bind(pattern.argument, value.rest || value, env);
    if (pattern.type === 'ArrayPattern') {
      for (let index = 0; index < pattern.elements.length; index += 1) {
        const part = pattern.elements[index]; if (!part) continue;
        const rest = (value.items || []).slice(index), restValue = merge(rest);
        const result = bind(part, part.type === 'RestElement' ? { items: rest, roots: frozen([...restValue.roots, ...(value.aggregate ? value.roots : [])]), track: value.aggregate || (value.items ? restValue.track : value.track) } : value.items?.[index] || (value.track ? { track: true, roots: value.roots } : { missing: true }), env); if (result?.abrupt) return result;
      }
    }
    if (pattern.type === 'ObjectPattern') { const used = new Set(); for (const part of pattern.properties || []) {
      if (part.type === 'RestElement') { const fields = Object.fromEntries(Object.entries(value.fields || {}).filter(([key]) => !used.has(key))), rest = merge(Object.values(fields)); const result = bind(part.argument, { fields, roots: frozen([...rest.roots, ...(value.aggregate ? value.roots : [])]), track: value.aggregate || (value.fields ? rest.track : value.track) }, env); if (result?.abrupt) return result; }
      else { const key = part.key.name || part.key.value; used.add(String(key)); const result = bind(part.value, value.fields?.[key] || (value.track ? { track: true, roots: value.roots } : { missing: true }), env); if (result?.abrupt) return result; }
    }
    }
  };
  const invoke = (fn, args) => {
    const invocation = merge(args), previous = active.get(fn.node);
    if (previous) { const provenance = roots([invocation, previous]); emit('cycle', fn.node, { poison: true, roots: provenance, escapeRoots: provenance }); return { track: true, roots: provenance, poison: true }; }
    active.set(fn.node, { roots: invocation.roots }); const env = scope(fn.env), unknownSpread = args.findIndex((value) => value.spread && !value.items);
    if (fn.node.type === 'FunctionExpression' && fn.node.id) env[fn.node.id.name] = { fn, value: { track: false } };
    for (let index = 0; index < (fn.node.params || []).length; index += 1) { const param = fn.node.params[index], spreadCovers = unknownSpread >= 0 && index >= unknownSpread, result = bind(param, param.type === 'RestElement' ? { items: args.slice(index), ...merge(args.slice(index)), track: spreadCovers || merge(args.slice(index)).track } : spreadCovers ? { track: true, roots: invocation.roots } : args[index] || { missing: true }, env); if (result?.abrupt) { active.delete(fn.node); return result; } }
    const result = fn.node.body?.type === 'BlockStatement' ? block(fn.node.body.body, env) : { value: evaluate(fn.node.body, env) };
    active.delete(fn.node); return result.abrupt ? { ...(result.value || known(undefined)), abrupt: true } : result.value || { ...known(undefined), undefined: true };
  };
  const argument = (node, env) => {
    const raw = unwrapExpression(node);
    if (raw?.type === 'SpreadElement') return { ...argument(raw.argument, env), spread: true };
    if (raw?.type === 'ArrayExpression') {
      const items = []; let aggregate = false; for (const part of raw.elements || []) { const value = part ? argument(part, env) : { missing: true }; if (value.abrupt) return value; aggregate ||= !!value.spread && !value.items && !!value.track; if (value.spread && value.items) items.push(...value.items); else items.push(value); }
      return { items, ...merge(items), aggregate };
    }
    if (raw?.type === 'ObjectExpression') {
      const fields = {};
      let track = false, aggregate = false, spreadRoots = frozen();
      for (const part of raw.properties || []) {
        if (part.type === 'SpreadElement') { const spread = argument(part.argument, env); if (spread.abrupt) return spread; Object.assign(fields, spread.fields || {}); spreadRoots = frozen([...spreadRoots, ...(spread.roots || [])]); track ||= !!spread.track; aggregate ||= !spread.fields && !!spread.track; continue; }
        if (part.computed) { const key = evaluate(part.key, env); if (key.abrupt) return key; }
        const value = argument(part.value, env); if (value.abrupt) return value; fields[part.key.name || part.key.value] = value;
      }
      const value = merge(Object.values(fields)); return { fields, roots: frozen([...value.roots, ...spreadRoots]), track: track || value.track, aggregate };
    }
    const value = evaluate(node, env); if (unboundUndefined(raw, env)) return { ...known(undefined), undefined: true }; return value;
  };
  const evaluate = (node, env, inChain = false) => {
    if (node?.type === 'ChainExpression') return evaluate(node.expression, env, true);
    node = unwrapExpression(node); if (!node) return { missing: true };
    if (node.type === 'Literal') return known(node.value);
    if (node.type === 'Identifier') return unboundUndefined(node, env) ? { ...known(undefined), undefined: true } : read(node, env);
    if (node.type === 'FunctionExpression' || node.type === 'ArrowFunctionExpression') return { track: false };
    if (node.type === 'ArrayExpression' || node.type === 'ObjectExpression') return argument(node, env);
    if (node.type === 'UnaryExpression') {
      const value = evaluate(node.argument, env); if (value.abrupt) return value;
      if (node.operator === 'delete' && value.track) emit('mutation', node, { roots: value.roots, targetRoots: frozen(value.roots), escapeRoots: frozen(), poison: true });
      if (node.operator === 'void') return { ...known(undefined), undefined: true, track: value.track };
      if (!isKnown(value)) return value;
      if (node.operator === '!') return { ...known(!value.value), track: value.track };
      if (node.operator === '~') return { ...known(~value.value), track: value.track };
      if (node.operator === '+') return { ...known(+value.value), track: value.track };
      if (node.operator === '-') return { ...known(-value.value), track: value.track };
      if (node.operator === 'typeof') return { ...known(typeof value.value), track: value.track };
      return value;
    }
    if (node.type === 'CallExpression' || node.type === 'NewExpression' || node.type === 'OptionalCallExpression') {
      const callee = evaluate(node.callee, env, inChain), fn = callable(node.callee, env), args = [];
      if (callee.abrupt || (node.optional && isKnown(callee) && callee.value == null) || inChain && callee.chainShortCircuit) return callee;
      for (const part of node.arguments || []) { const value = argument(part, env); if (value.abrupt) return value; if (value.spread && value.items) args.push(...value.items); else args.push(value); }
      if (fn) return invoke(fn, args);
      const value = merge([callee, ...args]); if (value.track) emit('escape', node, { roots: value.roots, escapeRoots: value.roots }); return value;
    }
    if (node.type === 'TaggedTemplateExpression') {
      const values = [evaluate(node.tag, env)]; if (values[0].abrupt) return values[0]; for (const part of node.quasi.expressions || []) { const value = evaluate(part, env); if (value.abrupt) return value; values.push(value); }
      const value = merge(values); if (value.track) emit('escape', node, { roots: value.roots, escapeRoots: value.roots }); return value;
    }
    if (node.type === 'AssignmentExpression' || node.type === 'UpdateExpression') {
      const left = evaluate(node.type === 'UpdateExpression' ? node.argument : node.left, env); if (left.abrupt) return left; const right = node.right ? evaluate(node.right, env) : { track: false }; if (right.abrupt) return right; const value = merge([left, right]);
      if (value.track) emit('mutation', node, { roots: value.roots, targetRoots: frozen(left.roots), escapeRoots: frozen(right.roots), poison: !left.track && right.track }); return value;
    }
    if (node.type === 'MemberExpression' || node.type === 'OptionalMemberExpression') {
      const values = [evaluate(node.object, env, inChain)];
      if (values[0].abrupt) return values[0];
      if (inChain && values[0].chainShortCircuit) return values[0];
      if (node.optional && isKnown(values[0]) && values[0].value == null) return { ...values[0], chainShortCircuit: true };
      if (node.computed) { const property = evaluate(node.property, env); if (property.abrupt) return property; values.push(property); }
      if (isKnown(values[0]) && values[0].value == null) return { ...values[0], abrupt: true };
      return merge(values);
    }
    if (node.type === 'BinaryExpression') {
      const left = evaluate(node.left, env); if (left.abrupt) return left; const right = evaluate(node.right, env); if (right.abrupt) return right;
      if (!isKnown(left) || !isKnown(right)) return merge([left, right]);
      const operation = {
        '==': () => left.value == right.value, '===': () => left.value === right.value,
        '!=': () => left.value != right.value, '!==': () => left.value !== right.value,
        '<': () => left.value < right.value, '<=': () => left.value <= right.value,
        '>': () => left.value > right.value, '>=': () => left.value >= right.value,
        '+': () => left.value + right.value, '-': () => left.value - right.value,
        '*': () => left.value * right.value, '/': () => left.value / right.value,
        '%': () => left.value % right.value, '**': () => left.value ** right.value,
        '&': () => left.value & right.value, '|': () => left.value | right.value,
        '^': () => left.value ^ right.value, '<<': () => left.value << right.value,
        '>>': () => left.value >> right.value, '>>>': () => left.value >>> right.value,
      }[node.operator];
      return operation ? { ...known(operation()), track: merge([left, right]).track } : merge([left, right]);
    }
    if (node.type === 'LogicalExpression') { const left = evaluate(node.left, env); if (left.abrupt) return left; if (isKnown(left)) { const takeRight = node.operator === '&&' ? !!left.value : node.operator === '||' ? !left.value : left.value == null; return takeRight ? evaluate(node.right, env) : left; } return merge([left, evaluate(node.right, env)]); }
    if (node.type === 'ConditionalExpression') { const test = evaluate(node.test, env); if (test.abrupt) return test; if (isKnown(test)) return evaluate(test.value ? node.consequent : node.alternate, env); const yes = evaluate(node.consequent, env), no = evaluate(node.alternate, env); return yes.abrupt && no.abrupt ? { ...merge([yes, no]), abrupt: true } : merge([test, yes, no]); }
    const values = []; for (const child of expressionChildren(node)) { const value = evaluate(child, env); if (value.abrupt) return value; values.push(value); }
    return values.length ? merge(values) : read(node, env);
  };
  const statement = (node, env) => {
    if (node.type === 'FunctionDeclaration') return {};
    if (node.type === 'VariableDeclaration') for (const declaration of node.declarations) {
      const fn = callable(declaration.init, env), value = declaration.init ? evaluate(declaration.init, env) : { missing: true }; if (value.abrupt) return { complete: true, abrupt: true, value };
      if (declaration.id.type === 'Identifier') env[declaration.id.name] = { fn, value }; else bind(declaration.id, value, env);
    }
    else if (node.type === 'ReturnStatement') { const value = node.argument ? evaluate(node.argument, env) : { ...known(undefined), undefined: true }; if (value.abrupt) return { complete: true, abrupt: true, value }; if (value.track) emit('return', node, { roots: value.roots }); return { complete: true, value }; }
    else if (node.type === 'ThrowStatement') return { complete: true, abrupt: true, value: evaluate(node.argument, env) };
    else if (node.type === 'BlockStatement') return block(node.body, scope(env));
    else if (node.type === 'ExpressionStatement') { const value = evaluate(node.expression, env); if (value.abrupt) return { complete: true, abrupt: true, value }; }
    else if (node.type === 'IfStatement') {
      const test = evaluate(node.test, env); if (test.abrupt) return { complete: true, abrupt: true, value: test };
      if (isKnown(test)) {
        if (!test.value && !node.alternate) return {};
        return statement(test.value ? node.consequent : node.alternate, scope(env));
      }
      const yes = statement(node.consequent, scope(env)), no = node.alternate ? statement(node.alternate, scope(env)) : {};
      if (yes.complete && no.complete) return { complete: true, abrupt: yes.abrupt && no.abrupt, value: merge([yes.value, no.value]) };
    }
    return {};
  };
  const block = (body, env) => { hoist(body, env); for (const node of body) { const result = statement(node, env); if (result.complete) return result; } return {}; };
  block(program.body || program, scope()); return facts;
}
