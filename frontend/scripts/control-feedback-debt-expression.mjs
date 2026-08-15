// @ts-nocheck -- parser AST nodes intentionally remain version-neutral plain objects.
const WRAPPERS = new Set(['ParenthesizedExpression', 'TSAsExpression', 'TSSatisfiesExpression', 'TSTypeAssertion', 'TSNonNullExpression', 'TypeCastExpression', 'ChainExpression', 'TSInstantiationExpression']);

/** Strip parser-only wrappers without dropping their runtime expression. */
export function unwrapExpression(node) {
  while (node && WRAPPERS.has(node.type)) node = node.expression;
  return node;
}

/** Runtime-ordered expression children for generic eager traversal. */
export function expressionChildren(node) {
  node = unwrapExpression(node);
  if (!node) return [];
  if (node.type === 'MemberExpression' || node.type === 'OptionalMemberExpression') return node.computed ? [node.object, node.property] : [node.object];
  if (node.type === 'CallExpression' || node.type === 'NewExpression' || node.type === 'OptionalCallExpression') return [node.callee, ...(node.arguments || [])];
  if (node.type === 'TaggedTemplateExpression') return [node.tag, ...(node.quasi.expressions || [])];
  if (node.type === 'ObjectExpression') return node.properties.flatMap((part) => part.type === 'SpreadElement' ? [part.argument] : [...(part.computed ? [part.key] : []), part.value]);
  if (node.type === 'ArrayExpression') return node.elements || [];
  if (node.type === 'TemplateLiteral') return node.expressions || [];
  return ['left', 'right', 'test', 'consequent', 'alternate', 'argument', 'expressions'].flatMap((key) => Array.isArray(node[key]) ? node[key] : node[key] ? [node[key]] : []);
}

/** `undefined` is special only when it is the global unbound identifier. */
export function isGenuineUndefined(node, isBound) {
  node = unwrapExpression(node);
  return node?.type === 'UnaryExpression' && node.operator === 'void' || node?.type === 'Identifier' && node.name === 'undefined' && !isBound('undefined');
}
