/** Normalized import boundary for pure ControlFeedback consumers (MOR-1712). */
import path from 'node:path';

/** @typedef {typeof path.win32} PathApi */
/** @typedef {import('eslint').Rule.RuleContext} RuleContext */
/** @typedef {import('eslint').Rule.RuleListener} RuleListener */
/** @typedef {import('eslint').Rule.Node} RuleNode */
/** @typedef {import('estree').Literal | import('estree').TemplateLiteral} LiteralSource */

/**
 * @param {string} filename
 * @param {string} cwd
 * @param {PathApi} [pathApi]
 * @returns {string}
 */
function frontendRoot(filename, cwd, pathApi = path) {
  const absolute = pathApi.resolve(filename);
  const marker = `${pathApi.sep}src${pathApi.sep}`;
  const index = absolute.lastIndexOf(marker);
  return index < 0 ? pathApi.resolve(cwd) : absolute.slice(0, index);
}

/**
 * @param {unknown} node
 * @returns {node is LiteralSource}
 */
function isLiteralSource(node) {
  return typeof node === 'object' && node !== null && 'type' in node
    && (node.type === 'Literal' || node.type === 'TemplateLiteral');
}

/**
 * @param {unknown} node
 * @returns {string | null}
 */
function literalSource(node) {
  if (!isLiteralSource(node)) return null;
  if (node?.type === 'Literal' && typeof node.value === 'string') return node.value;
  if (node?.type === 'TemplateLiteral' && node.expressions.length === 0) {
    return node.quasis[0]?.value.cooked ?? node.quasis[0]?.value.raw;
  }
  return null;
}

/**
 * @param {string} specifier
 * @param {string} filename
 * @param {string} cwd
 * @param {PathApi} [pathApi]
 * @returns {string | null}
 */
export function resolveModuleForPath(specifier, filename, cwd, pathApi = path) {
  const clean = specifier.replaceAll('\\', '/').split(/[?#]/, 1)[0];
  const root = frontendRoot(filename, cwd, pathApi);
  if (clean.startsWith('/@fs/')) {
    const target = pathApi.normalize(clean.slice('/@fs/'.length));
    return pathApi.resolve(pathApi.isAbsolute(target) ? target : `${pathApi.sep}${target}`);
  }
  if (clean === '$lib') return pathApi.resolve(root, 'src/lib');
  if (clean.startsWith('$lib/')) {
    const tail = clean.slice(5).replace(/^\/+/, '');
    return pathApi.resolve(root, 'src/lib', tail);
  }
  if (clean.startsWith('.')) return pathApi.resolve(pathApi.dirname(filename), clean);
  if (clean === '/src' || clean.startsWith('/src/')) {
    return pathApi.resolve(root, clean.replace(/^\/+/, ''));
  }
  if (pathApi.isAbsolute(clean)) return pathApi.resolve(clean);
  return null;
}

/**
 * @param {string} candidate
 * @param {string} root
 * @returns {boolean}
 */
function isWithin(candidate, root) {
  const relative = path.relative(root, candidate);
  return relative === '' || (!relative.startsWith(`..${path.sep}`)
    && relative !== '..' && !path.isAbsolute(relative));
}

/** @type {import('eslint').Rule.RuleModule} */
const normalizedImportBoundary = {
  meta: {
    type: 'problem', schema: [],
    messages: {
      runtime: 'Pure control feedback cannot import runtime ownership: {{source}}.',
      dynamic: 'Pure control feedback cannot prove a non-literal dynamic import is ownership-safe.',
    },
  },
  /**
   * @param {RuleContext} context
   * @returns {RuleListener}
   */
  create(context) {
    const filename = context.physicalFilename ?? context.filename;
    const cwd = context.cwd ?? process.cwd();
    const runtimeRoot = path.resolve(frontendRoot(filename, cwd), 'src/lib/runtime');

    /** @param {RuleNode} node */
    function checkStatic(node) {
      const source = literalSource('source' in node ? node.source : null);
      if (source === null) return;
      const resolved = resolveModuleForPath(source, filename, cwd);
      if (resolved !== null && isWithin(resolved, runtimeRoot)) {
        context.report({ node, messageId: 'runtime', data: { source } });
      }
    }

    /** @param {RuleNode} node */
    function checkDynamic(node) {
      const source = literalSource('source' in node ? node.source : null);
      if (source === null) {
        context.report({ node, messageId: 'dynamic' });
        return;
      }
      const resolved = resolveModuleForPath(source, filename, cwd);
      if (resolved !== null && isWithin(resolved, runtimeRoot)) {
        context.report({ node, messageId: 'runtime', data: { source } });
      }
    }

    /** @param {RuleNode} node */
    function checkNamedExport(node) {
      if ('source' in node && node.source) checkStatic(node);
    }

    /** @type {RuleListener} */
    const listeners = {
      ImportDeclaration: checkStatic,
      ExportAllDeclaration: checkStatic,
      ExportNamedDeclaration: checkNamedExport,
      ImportExpression: checkDynamic,
    };
    return listeners;
  },
};

export default {
  rules: { 'normalized-import-boundary': normalizedImportBoundary },
};
