/** Normalized import boundary for pure ControlFeedback consumers (MOR-1712). */
import path from 'node:path';

function frontendRoot(filename, cwd, pathApi = path) {
  const absolute = pathApi.resolve(filename);
  const marker = `${pathApi.sep}src${pathApi.sep}`;
  const index = absolute.lastIndexOf(marker);
  return index < 0 ? pathApi.resolve(cwd) : absolute.slice(0, index);
}

function literalSource(node) {
  if (node?.type === 'Literal' && typeof node.value === 'string') return node.value;
  if (node?.type === 'TemplateLiteral' && node.expressions.length === 0) {
    return node.quasis[0]?.value.cooked ?? node.quasis[0]?.value.raw;
  }
  return null;
}

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

function isWithin(candidate, root) {
  const relative = path.relative(root, candidate);
  return relative === '' || (!relative.startsWith(`..${path.sep}`)
    && relative !== '..' && !path.isAbsolute(relative));
}

const normalizedImportBoundary = {
  meta: {
    type: 'problem', schema: [],
    messages: {
      runtime: 'Pure control feedback cannot import runtime ownership: {{source}}.',
      dynamic: 'Pure control feedback cannot prove a non-literal dynamic import is ownership-safe.',
    },
  },
  create(context) {
    const filename = context.physicalFilename ?? context.filename;
    const cwd = context.cwd ?? process.cwd();
    const runtimeRoot = path.resolve(frontendRoot(filename, cwd), 'src/lib/runtime');

    function checkStatic(node) {
      const source = literalSource(node.source);
      if (source === null) return;
      const resolved = resolveModuleForPath(source, filename, cwd);
      if (resolved !== null && isWithin(resolved, runtimeRoot)) {
        context.report({ node, messageId: 'runtime', data: { source } });
      }
    }

    function checkDynamic(node) {
      const source = literalSource(node.source);
      if (source === null) {
        context.report({ node, messageId: 'dynamic' });
        return;
      }
      const resolved = resolveModuleForPath(source, filename, cwd);
      if (resolved !== null && isWithin(resolved, runtimeRoot)) {
        context.report({ node, messageId: 'runtime', data: { source } });
      }
    }

    return {
      ImportDeclaration: checkStatic,
      ExportAllDeclaration: checkStatic,
      ExportNamedDeclaration(node) { if (node.source) checkStatic(node); },
      ImportExpression: checkDynamic,
    };
  },
};

export default {
  rules: { 'normalized-import-boundary': normalizedImportBoundary },
};
