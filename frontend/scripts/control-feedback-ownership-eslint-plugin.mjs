/** Normalized import boundary for pure ControlFeedback consumers (MOR-1712). */
import path from 'node:path';

function frontendRoot(filename, cwd) {
  const absolute = path.resolve(filename);
  const marker = `${path.sep}src${path.sep}`;
  const index = absolute.lastIndexOf(marker);
  return index < 0 ? path.resolve(cwd) : absolute.slice(0, index);
}

function literalSource(node) {
  if (node?.type === 'Literal' && typeof node.value === 'string') return node.value;
  if (node?.type === 'TemplateLiteral' && node.expressions.length === 0) {
    return node.quasis[0]?.value.cooked ?? node.quasis[0]?.value.raw;
  }
  return null;
}

function resolveModule(specifier, filename, cwd) {
  const clean = specifier.split(/[?#]/, 1)[0];
  const root = frontendRoot(filename, cwd);
  if (clean.startsWith('/@fs/')) {
    const target = path.normalize(clean.slice('/@fs/'.length));
    return path.resolve(path.isAbsolute(target) ? target : `${path.sep}${target}`);
  }
  if (clean === '$lib') return path.resolve(root, 'src/lib');
  if (clean.startsWith('$lib/')) {
    const tail = clean.slice(5).replace(/^\/+/, '');
    return path.resolve(root, 'src/lib', tail);
  }
  if (clean.startsWith('.')) return path.resolve(path.dirname(filename), clean);
  if (clean === '/src' || clean.startsWith('/src/')) {
    return path.resolve(root, clean.replace(/^\/+/, ''));
  }
  if (path.isAbsolute(clean)) return path.resolve(clean);
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
      const resolved = resolveModule(source, filename, cwd);
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
      const resolved = resolveModule(source, filename, cwd);
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
