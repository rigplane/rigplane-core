/** Compatibility exports for control-feedback inventory object-flow consumers. */
import { unwrapExpression } from './control-feedback-debt-ast.mjs';

export { collectObjectFlow } from './control-feedback-debt-ast.mjs';

/** @param {any} node @returns {any|null} */
export function unwrapIdentifier(node) {
  const unwrapped = unwrapExpression(node).node;
  return unwrapped?.type === 'Identifier' ? unwrapped : null;
}
