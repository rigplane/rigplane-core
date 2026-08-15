// @ts-nocheck -- parser AST nodes intentionally remain version-neutral plain objects.
import { expressionChildren, isGenuineUndefined, unwrapExpression } from './control-feedback-debt-expression.mjs';

/**
 * Evaluate parser expressions in execution order; callers retain canonical identity ownership.
 * @param {object} program
 * @param {{ isTracked?: (node: any, scope: any) => boolean }} options
 */
export function evaluateOrderedEffects(program, { isTracked = () => false } = {}) {
  const facts = [], active = new Set(), scope = (parent = null) => Object.create(parent);
  const emit = (kind, node, extra = {}) => facts.push({ kind, node, ...extra });
  const merge = (values) => { let track = false; for (const value of values) { if (value?.track) track = true; } return { track }; };
  const hasBinding = (env, name) => !!env && name in env;
  const binding = (env, name) => hasBinding(env, name) ? env[name] : undefined;
  const known = (value) => ({ known: true, value, track: false });
  const isKnown = (value) => value?.known === true;
  const unboundUndefined = (node, env) => isGenuineUndefined(node, (name) => hasBinding(env, name));
  const read = (node, env) => {
    node = unwrapExpression(node);
    if (!node) return { missing: true };
    if (node.type === 'Identifier') return binding(env, node.name)?.value || { track: isTracked(node, env) };
    if (node.type === 'MemberExpression' || node.type === 'OptionalMemberExpression') return merge([read(node.object, env), node.computed ? read(node.property, env) : null]);
    return { track: isTracked(node, env) };
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
    if (pattern.type === 'AssignmentPattern') return bind(pattern.left, value.missing || value.undefined || isKnown(value) && value.value === undefined ? evaluate(pattern.right, env) : value, env);
    if (pattern.type === 'Identifier') { env[pattern.name] = { value }; return; }
    if (pattern.type === 'RestElement') return bind(pattern.argument, value.rest || value, env);
    if (pattern.type === 'ArrayPattern') {
      for (let index = 0; index < pattern.elements.length; index += 1) {
        const part = pattern.elements[index]; if (!part) continue;
        const rest = (value.items || []).slice(index);
        bind(part, part.type === 'RestElement' ? { items: rest, track: merge(rest).track } : value.items?.[index] || { missing: true }, env);
      }
    }
    if (pattern.type === 'ObjectPattern') { const used = new Set(); for (const part of pattern.properties || []) {
      if (part.type === 'RestElement') { const fields = Object.fromEntries(Object.entries(value.fields || {}).filter(([key]) => !used.has(key))); bind(part.argument, { fields, track: merge(Object.values(fields)).track }, env); }
      else { const key = part.key.name || part.key.value; used.add(String(key)); bind(part.value, value.fields?.[key] || { missing: true }, env); }
    }
    }
  };
  const invoke = (fn, args) => {
    if (active.has(fn.node)) { emit('cycle', fn.node, { poison: true }); return { track: true, poison: true }; }
    active.add(fn.node); const env = scope(fn.env);
    if (fn.node.type === 'FunctionExpression' && fn.node.id) env[fn.node.id.name] = { fn, value: { track: false } };
    for (let index = 0; index < (fn.node.params || []).length; index += 1) { const param = fn.node.params[index]; bind(param, param.type === 'RestElement' ? { items: args.slice(index), track: merge(args.slice(index)).track } : args[index] || { missing: true }, env); }
    const result = fn.node.body?.type === 'BlockStatement' ? block(fn.node.body.body, env) : { value: evaluate(fn.node.body, env) };
    active.delete(fn.node); return result.value || { track: false };
  };
  const argument = (node, env) => {
    const raw = unwrapExpression(node);
    if (raw?.type === 'SpreadElement') return { ...argument(raw.argument, env), spread: true };
    if (raw?.type === 'ArrayExpression') {
      const items = []; for (const part of raw.elements || []) { const value = part ? argument(part, env) : { missing: true }; if (value.spread && value.items) items.push(...value.items); else items.push(value); }
      return { items, track: merge(items).track };
    }
    if (raw?.type === 'ObjectExpression') {
      const fields = {};
      for (const part of raw.properties || []) {
        if (part.type === 'SpreadElement') { const spread = argument(part.argument, env); Object.assign(fields, spread.fields || {}); continue; }
        if (part.computed) evaluate(part.key, env);
        fields[part.key.name || part.key.value] = argument(part.value, env);
      }
      return { fields, track: merge(Object.values(fields)).track };
    }
    const value = evaluate(node, env); if (unboundUndefined(raw, env)) return { ...known(undefined), undefined: true }; return value;
  };
  const evaluate = (node, env) => {
    node = unwrapExpression(node); if (!node) return { missing: true };
    if (node.type === 'Literal') return known(node.value);
    if (node.type === 'Identifier') return unboundUndefined(node, env) ? { ...known(undefined), undefined: true } : read(node, env);
    if (node.type === 'FunctionExpression' || node.type === 'ArrowFunctionExpression') return { track: false };
    if (node.type === 'ArrayExpression' || node.type === 'ObjectExpression') return argument(node, env);
    if (node.type === 'UnaryExpression') {
      const value = evaluate(node.argument, env);
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
      const callee = evaluate(node.callee, env), fn = callable(node.callee, env), args = [];
      if (node.optional && isKnown(callee) && callee.value == null) return callee;
      for (const part of node.arguments || []) { const value = argument(part, env); if (value.spread && value.items) args.push(...value.items); else args.push(value); }
      if (fn) return invoke(fn, args);
      const value = merge([callee, ...args]); if (value.track) emit('escape', node); return value;
    }
    if (node.type === 'TaggedTemplateExpression') {
      const values = [evaluate(node.tag, env)]; for (const part of node.quasi.expressions || []) values.push(evaluate(part, env));
      const value = merge(values); if (value.track) emit('escape', node); return value;
    }
    if (node.type === 'AssignmentExpression' || node.type === 'UpdateExpression') {
      const left = evaluate(node.left, env), right = node.right ? evaluate(node.right, env) : { track: false }, value = merge([left, right]);
      if (value.track) emit('mutation', node); return value;
    }
    if (node.type === 'MemberExpression' || node.type === 'OptionalMemberExpression') {
      const values = [evaluate(node.object, env)];
      if (node.optional && isKnown(values[0]) && values[0].value == null) return values[0];
      if (node.computed) values.push(evaluate(node.property, env)); return merge(values);
    }
    if (node.type === 'BinaryExpression') {
      const left = evaluate(node.left, env), right = evaluate(node.right, env);
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
    if (node.type === 'LogicalExpression') { const left = evaluate(node.left, env); if (isKnown(left)) { const takeRight = node.operator === '&&' ? !!left.value : node.operator === '||' ? !left.value : left.value == null; return takeRight ? evaluate(node.right, env) : left; } return merge([left, evaluate(node.right, env)]); }
    if (node.type === 'ConditionalExpression') { const test = evaluate(node.test, env); return isKnown(test) ? evaluate(test.value ? node.consequent : node.alternate, env) : merge([test, evaluate(node.consequent, env), evaluate(node.alternate, env)]); }
    const values = []; for (const child of expressionChildren(node)) values.push(evaluate(child, env));
    return values.length ? merge(values) : read(node, env);
  };
  const statement = (node, env) => {
    if (node.type === 'FunctionDeclaration') return {};
    if (node.type === 'VariableDeclaration') for (const declaration of node.declarations) {
      const fn = callable(declaration.init, env), value = declaration.init ? evaluate(declaration.init, env) : { missing: true };
      if (declaration.id.type === 'Identifier') env[declaration.id.name] = { fn, value }; else bind(declaration.id, value, env);
    }
    else if (node.type === 'ReturnStatement') { const value = evaluate(node.argument, env); if (value.track) emit('return', node); return { complete: true, value }; }
    else if (node.type === 'BlockStatement') return block(node.body, scope(env));
    else if (node.type === 'ExpressionStatement') evaluate(node.expression, env);
    else if (node.type === 'IfStatement') {
      const test = evaluate(node.test, env);
      if (isKnown(test)) {
        if (!test.value && !node.alternate) return {};
        return statement(test.value ? node.consequent : node.alternate, scope(env));
      }
      const yes = statement(node.consequent, scope(env)), no = node.alternate ? statement(node.alternate, scope(env)) : {};
      if (yes.complete && no.complete) return { complete: true, value: merge([yes.value, no.value]) };
    }
    return {};
  };
  const block = (body, env) => { hoist(body, env); for (const node of body) { const result = statement(node, env); if (result.complete) return result; } return {}; };
  block(program.body || program, scope()); return facts;
}
