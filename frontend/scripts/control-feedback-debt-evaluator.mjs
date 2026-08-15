// @ts-nocheck -- parser AST nodes intentionally remain version-neutral plain objects.
/**
 * Pure, parser-node ordered-effect evaluator. Identity ownership remains with its caller.
 * @param {object} program
 * @param {{ isTracked?: (node: any, scope: any) => boolean }} options
 */
export function evaluateOrderedEffects(program, { isTracked = () => false } = {}) {
  const facts = [], active = new Set();
  const scope = (parent = null) => Object.create(parent);
  const emit = (kind, node, extra = {}) => facts.push({ kind, node, ...extra });
  const unwrap = (node) => {
    while (node && ['ParenthesizedExpression', 'TSAsExpression', 'TSTypeAssertion', 'TSNonNullExpression', 'TypeCastExpression'].includes(node.type)) node = node.expression;
    return node;
  };
  const lookup = (env, name) => env && (name in env) ? env[name] : undefined;
  const value = (node, env) => {
    node = unwrap(node);
    if (!node) return false;
    if (node.type === 'Identifier') return lookup(env, node.name)?.track ?? isTracked(node, env);
    if (node.type === 'MemberExpression' || node.type === 'OptionalMemberExpression') return value(node.object, env) || (node.computed && value(node.property, env));
    if (node.type === 'AssignmentExpression') return value(node.left, env) || value(node.right, env);
    return isTracked(node, env);
  };
  const callable = (node, env) => {
    node = unwrap(node);
    if (node?.type === 'Identifier') return lookup(env, node.name)?.fn;
    return node && ['FunctionExpression', 'ArrowFunctionExpression'].includes(node.type) ? { node, env } : undefined;
  };
  const hoist = (body, env) => body.forEach((node) => {
    if (node.type === 'FunctionDeclaration' && node.id) env[node.id.name] = { fn: { node, env }, track: false };
  });
  const bind = (pattern, argument, env) => {
    pattern = unwrap(pattern);
    if (!pattern) return;
    if (pattern.type === 'AssignmentPattern') {
      const missing = !argument || argument.missing || argument.undefined;
      const next = missing ? { track: evaluate(pattern.right, env) } : argument;
      bind(pattern.left, next, env); return;
    }
    if (pattern.type === 'Identifier') { env[pattern.name] = { track: !!argument?.track }; return; }
    if (pattern.type === 'RestElement') { bind(pattern.argument, { track: false }, env); return; }
    if (pattern.type === 'ObjectPattern') for (const property of pattern.properties || []) {
      if (property.type === 'RestElement') bind(property.argument, { track: false }, env);
      else bind(property.value, argument?.fields?.[property.key.name || property.key.value] || { missing: true }, env);
    }
    if (pattern.type === 'ArrayPattern') for (const element of pattern.elements || []) bind(element, { missing: true }, env);
  };
  const invoke = (fn, args) => {
    if (active.has(fn.node)) { emit('cycle', fn.node, { poison: true }); return false; }
    active.add(fn.node);
    const env = scope(fn.env);
    (fn.node.params || []).forEach((param, index) => bind(param, args[index] || { missing: true }, env));
    const result = fn.node.body?.type === 'BlockStatement' ? block(fn.node.body.body, env) : { track: evaluate(fn.node.body, env) };
    active.delete(fn.node); return !!result.track;
  };
  const evaluate = (node, env) => {
    node = unwrap(node);
    if (!node) return false;
    if (node.type === 'SequenceExpression') return node.expressions.reduce((tracked, part) => evaluate(part, env) || tracked, false);
    if (node.type === 'CallExpression' || node.type === 'NewExpression' || node.type === 'OptionalCallExpression') {
      const calleeTracked = evaluate(node.callee, env), fn = callable(node.callee, env);
      const args = (node.arguments || []).map((arg) => argument(arg, env));
      if (fn) return invoke(fn, args);
      const tracked = calleeTracked || args.some((arg) => arg.track);
      if (tracked) emit('escape', node); return tracked;
    }
    if (node.type === 'TaggedTemplateExpression') {
      const tag = evaluate(node.tag, env), substitutions = (node.quasi.expressions || []).map((part) => evaluate(part, env));
      const tracked = tag || substitutions.some(Boolean); if (tracked) emit('escape', node); return tracked;
    }
    if (node.type === 'AssignmentExpression' || node.type === 'UpdateExpression') {
      const left = evaluate(node.left, env), right = node.right ? evaluate(node.right, env) : false;
      const tracked = left || right; if (tracked) emit('mutation', node); return tracked;
    }
    if (node.type === 'MemberExpression' || node.type === 'OptionalMemberExpression') {
      const object = evaluate(node.object, env), property = node.computed ? evaluate(node.property, env) : false; return object || property || value(node, env);
    }
    if (node.type === 'Identifier') return value(node, env);
    if (node.type === 'BinaryExpression' || node.type === 'LogicalExpression' || node.type === 'ConditionalExpression' || node.type === 'UnaryExpression' || node.type === 'AwaitExpression' || node.type === 'SpreadElement') {
      return ['left', 'right', 'test', 'consequent', 'alternate', 'argument'].some((key) => evaluate(node[key], env));
    }
    if (node.type === 'ArrayExpression') return (node.elements || []).some((part) => evaluate(part, env));
    if (node.type === 'ObjectExpression') return (node.properties || []).some((part) => evaluate(part.value || part.argument, env));
    return value(node, env);
  };
  const argument = (node, env) => {
    const raw = unwrap(node), result = { undefined: raw?.type === 'Identifier' && raw.name === 'undefined' };
    if (raw?.type === 'ObjectExpression') {
      result.fields = Object.fromEntries(raw.properties.filter((part) => part.type === 'Property').map((part) => [part.key.name || part.key.value, argument(part.value, env)]));
      result.track = Object.values(result.fields).some((part) => part.track);
    } else result.track = evaluate(node, env);
    return result;
  };
  const statement = (node, env) => {
    if (node.type === 'FunctionDeclaration') return {};
    if (node.type === 'VariableDeclaration') for (const declaration of node.declarations) {
      const init = declaration.init, fn = callable(init, env), track = init ? evaluate(init, env) : false;
      if (declaration.id.type === 'Identifier') env[declaration.id.name] = { fn, track };
      else bind(declaration.id, { track }, env);
    }
    else if (node.type === 'ReturnStatement') { const track = evaluate(node.argument, env); if (track) emit('return', node); return { returned: true, track }; }
    else if (node.type === 'BlockStatement') return block(node.body, scope(env));
    else if (node.type === 'ExpressionStatement') evaluate(node.expression, env);
    else if (node.type === 'IfStatement') { evaluate(node.test, env); statement(node.consequent, env); if (node.alternate) statement(node.alternate, env); }
    return {};
  };
  const block = (body, env) => { hoist(body, env); for (const node of body) { const result = statement(node, env); if (result.returned) return result; } return {}; };
  block(program.body || program, scope());
  return facts;
}
