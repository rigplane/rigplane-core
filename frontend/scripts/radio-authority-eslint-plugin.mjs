/**
 * Bounded radio-authority guards for MOR-1406.
 *
 * These rules enforce module capabilities and a small set of declared sinks.
 * They intentionally do not attempt whole-program JavaScript provenance.
 */

const WRITER_EXPORTS = new Set([
  'patchActiveReceiver', 'patchRadioState', 'patchReceiver',
  'resetRadioState', 'setRadioState',
]);
const RADIO_SOURCE_EXPORTS = new Set([
  'getActiveReceiver', 'getRadioState', 'radio', 'subscribeRadioState',
]);
const SCOPE_METADATA = new Set([
  'centerHz', 'edgeHighHz', 'edgeLowHz', 'edges', 'mode', 'receiver',
  'span', 'spanHz',
]);

const LEGACY_PRESENTATION_AUTHORITY = new Set([]);
const LEGACY_WRITER_OWNERS = new Set([
  'src/lib/runtime/adapters/mod-input-auto.svelte.ts',
  'src/lib/runtime/commands/panel-commands.ts',
  'src/lib/runtime/frontend-runtime.ts',
  'src/lib/runtime/props/panel-props.ts',
  'src/lib/runtime/system-controller.ts',
  'src/lib/stores/commands.svelte.ts',
  'src/lib/transport/ws-client.ts',
]);
const ACQUISITION_OWNERS = new Set([
  'src/lib/runtime/frontend-runtime.ts',
  'src/lib/runtime/commands/panel-commands.ts',
  'src/lib/runtime/scope-controller.svelte.ts',
  'src/lib/runtime/tx-controller/browser-dependencies.ts',
  'src/lib/stores/connection.svelte.ts',
  'src/lib/transport/http-client.ts',
  'src/lib/transport/ws-client.ts',
]);
const SCOPE_METADATA_OWNERS = new Set([
  'src/components-v2/panels/audio-scope/AudioSpectrumPanel.svelte',
  'src/components-v2/panels/lcd/AmberCockpit.svelte',
  'src/components-v2/panels/lcd/AmberScope.svelte',
  'src/components/spectrum/SpectrumPanel.svelte',
  'src/lib/runtime/adapters/scope-adapter.ts',
]);
const READ_ONLY_SEAMS = [
  'src/lib/runtime/adapters',
  'src/lib/runtime/props',
  'src/semantic/radio-view-model.ts',
  'src/lib/radio/mode-filter-memory.ts',
];
const INTENT_FACADES = [
  'src/lib/runtime/commands',
];
const DIRECT_ORIGIN_OWNERS = new Set([
  'src/components/shared/Toast.svelte',
  'src/lib/local-extensions/host-api.ts',
]);

function projectPath(filename) {
  return filename.replaceAll('\\', '/').replace(/^.*\/frontend\//, '');
}

function unwrap(node) {
  let current = node;
  while (current && ['ChainExpression', 'TSAsExpression', 'TSNonNullExpression',
    'TSSatisfiesExpression', 'TypeCastExpression'].includes(current.type)) {
    current = current.expression;
  }
  return current;
}

function sourceText(node) {
  const value = node?.source?.value ?? node?.value;
  return typeof value === 'string' ? value : undefined;
}

function isRadioStore(source) {
  return typeof source === 'string' && /(?:^|\/)stores\/radio(?:\.svelte)?$/.test(source.replace(/^\$lib\//, 'lib/'));
}

function isTransport(source) {
  return typeof source === 'string' && /(?:^|\/)transport\/(?:ws-client|http-client)$/.test(source.replace(/^\$lib\//, 'lib/'));
}

function isCommandModule(source) {
  return typeof source === 'string' && /(?:^|\/)runtime\/commands\//.test(source.replace(/^\$lib\//, 'lib/'));
}

function isPresentation(path) {
  return /^src\/(?:semantic|presentation|primitives|skins|components\/spectrum|components-v2\/(?:controls|display|layout|meters|panels|vfo))\//.test(path);
}

function isPathOrChild(path, root) {
  return path === root || path.startsWith(`${root}/`);
}

function matchesAnyPath(path, roots) {
  return roots.some((root) => isPathOrChild(path, root));
}

function importedName(specifier) {
  if (specifier.type === 'ImportSpecifier') {
    return specifier.imported.name ?? specifier.imported.value;
  }
  return '*';
}

function memberName(node) {
  const current = unwrap(node);
  if (!current || !['MemberExpression', 'OptionalMemberExpression'].includes(current.type)) return undefined;
  if (!current.computed && current.property.type === 'Identifier') return current.property.name;
  if (current.computed && current.property.type === 'Literal') return String(current.property.value);
  return undefined;
}

function walk(node, visit, seen = new Set()) {
  if (!node || typeof node !== 'object' || seen.has(node)) return;
  seen.add(node);
  visit(node);
  for (const [key, value] of Object.entries(node)) {
    if (key === 'parent' || key === 'loc' || key === 'range' || key === 'tokens' || key === 'comments') continue;
    if (Array.isArray(value)) {
      for (const child of value) walk(child, visit, seen);
    } else if (value && typeof value === 'object' && typeof value.type === 'string') {
      walk(value, visit, seen);
    }
  }
}

const structuralBoundary = {
  meta: { type: 'problem', schema: [], messages: {
    boundary: 'Radio authority module {{source}} is not available across this presentation boundary.',
  } },
  create(context) {
    const path = projectPath(context.filename);
    function isTypeOnly(node) {
      if (node.importKind === 'type' || node.exportKind === 'type') return true;
      if (!node.specifiers?.length) return false;
      return node.specifiers.every((specifier) =>
        specifier.importKind === 'type' || specifier.exportKind === 'type');
    }
    function isReadOnlyRadioImport(node, source) {
      if (node.type !== 'ImportDeclaration' || !isRadioStore(source)) return false;
      const valueSpecifiers = node.specifiers.filter((specifier) =>
        specifier.importKind !== 'type' && node.importKind !== 'type');
      return valueSpecifiers.length > 0 && valueSpecifiers.every((specifier) =>
        specifier.type === 'ImportSpecifier' && RADIO_SOURCE_EXPORTS.has(importedName(specifier)));
    }
    function allowsStaticOrigin(node, source) {
      if (isTypeOnly(node)) return true;
      if (LEGACY_PRESENTATION_AUTHORITY.has(path) || LEGACY_WRITER_OWNERS.has(path)
        || ACQUISITION_OWNERS.has(path) || DIRECT_ORIGIN_OWNERS.has(path)) return true;
      if (matchesAnyPath(path, READ_ONLY_SEAMS) && isReadOnlyRadioImport(node, source)) return true;
      return matchesAnyPath(path, INTENT_FACADES)
        && (isTransport(source) || isCommandModule(source));
    }
    function report(node, source) {
      context.report({ node, messageId: 'boundary', data: { source } });
    }
    function checkStatic(node) {
      const source = sourceText(node);
      if (!source) return;
      const restricted = isRadioStore(source) || isTransport(source) || isCommandModule(source);
      if (!restricted) return;
      if (!allowsStaticOrigin(node, source)) report(node, source);
    }
    function checkDynamic(node, target) {
      if (!isPresentation(path) || LEGACY_PRESENTATION_AUTHORITY.has(path)) return;
      const source = sourceText(target);
      if (!source) {
        report(node, '<non-literal dynamic target>');
      } else if (isRadioStore(source) || isTransport(source) || isCommandModule(source)) {
        report(node, source);
      }
    }
    return {
      ImportDeclaration: checkStatic,
      ExportAllDeclaration: checkStatic,
      ExportNamedDeclaration(node) { if (node.source) checkStatic(node); },
      ImportExpression(node) { checkDynamic(node, node.source); },
      CallExpression(node) {
        const callee = unwrap(node.callee);
        if (callee?.type === 'Identifier' && callee.name === 'require') {
          checkDynamic(node, node.arguments[0]);
        }
      },
    };
  },
};

function authorityFacts(context) {
  const writerBindings = new Set();
  const sourceBindings = new Set();
  const localValues = new Map();
  const localFunctions = new Map();

  function recordImport(node) {
    const source = sourceText(node);
    if (!isRadioStore(source)) return;
    for (const specifier of node.specifiers) {
      if (specifier.type !== 'ImportSpecifier' || specifier.importKind === 'type') continue;
      const imported = importedName(specifier);
      if (WRITER_EXPORTS.has(imported)) writerBindings.add(specifier.local.name);
      if (RADIO_SOURCE_EXPORTS.has(imported)) sourceBindings.add(specifier.local.name);
    }
  }

  function recordVariable(node) {
    if (node.id.type === 'Identifier' && node.init) localValues.set(node.id.name, node.init);
  }

  function isAuthority(node, seen = new Set()) {
    const current = unwrap(node);
    if (!current || seen.has(current)) return false;
    seen.add(current);
    if (current.type === 'Identifier') {
      if (sourceBindings.has(current.name)) return true;
      const initializer = localValues.get(current.name);
      return initializer ? isAuthority(initializer, seen) : false;
    }
    if (['Literal', 'TemplateLiteral'].includes(current.type)) return false;
    if (current.type === 'CallExpression' || current.type === 'NewExpression') {
      if (current.callee.type === 'Identifier' && sourceBindings.has(current.callee.name)) return true;
      return current.arguments.some((argument) => argument.type !== 'SpreadElement'
        ? isAuthority(argument, new Set(seen))
        : isAuthority(argument.argument, new Set(seen)));
    }
    if (current.type === 'MemberExpression' || current.type === 'OptionalMemberExpression') {
      return isAuthority(current.object, seen);
    }
    if (current.type === 'Property') return isAuthority(current.value, seen);
    if (current.type === 'SpreadElement') return isAuthority(current.argument, seen);
    if (current.type === 'ObjectExpression') return current.properties.some((item) => isAuthority(item, new Set(seen)));
    if (current.type === 'ArrayExpression') return current.elements.some((item) => isAuthority(item, new Set(seen)));
    if (current.type === 'ConditionalExpression') {
      return isAuthority(current.test, new Set(seen)) || isAuthority(current.consequent, new Set(seen))
        || isAuthority(current.alternate, new Set(seen));
    }
    if (current.type === 'LogicalExpression' || current.type === 'BinaryExpression') {
      return isAuthority(current.left, new Set(seen)) || isAuthority(current.right, new Set(seen));
    }
    if (current.type === 'AssignmentExpression') return isAuthority(current.right, seen);
    return false;
  }

  return { writerBindings, sourceBindings, localFunctions, recordImport, recordVariable, isAuthority };
}

const authoritySink = {
  meta: { type: 'problem', schema: [], messages: {
    sink: 'Observed radio authority reached {{sink}} outside its declared owner.',
  } },
  create(context) {
    const path = projectPath(context.filename);
    if (LEGACY_WRITER_OWNERS.has(path) || LEGACY_PRESENTATION_AUTHORITY.has(path)) return {};
    const facts = authorityFacts(context);
    const reported = new Set();
    function report(node, sink) {
      const key = `${node.range?.[0] ?? node.loc?.start.line}:${sink}`;
      if (reported.has(key)) return;
      reported.add(key);
      context.report({ node, messageId: 'sink', data: { sink } });
    }
    function isModuleState(node) {
      const call = unwrap(node.init);
      if (!call || call.type !== 'CallExpression' || call.callee.type !== 'Identifier'
        || call.callee.name !== '$state') return false;
      let parent = node.parent;
      while (parent && parent.type !== 'Program' && !/Function/.test(parent.type)) parent = parent.parent;
      return parent?.type === 'Program' && call.arguments.some((argument) =>
        argument.type !== 'SpreadElement' && facts.isAuthority(argument));
    }
    return {
      ImportDeclaration: facts.recordImport,
      VariableDeclarator(node) {
        facts.recordVariable(node);
        if (isModuleState(node)) report(node, 'module-level live store');
      },
      CallExpression(node) {
        const callee = unwrap(node.callee);
        if (callee?.type === 'Identifier' && facts.writerBindings.has(callee.name)
          && !LEGACY_WRITER_OWNERS.has(path)) {
          report(node, 'canonical live writer');
        }
        if (memberName(callee) === 'setItem'
          && node.arguments.some((argument) => argument.type !== 'SpreadElement' && facts.isAuthority(argument))) {
          report(node, 'persistent storage');
        }
      },
      LogicalExpression(node) {
        if ((node.operator === '??' || node.operator === '||')
          && facts.isAuthority(node.left) && !facts.isAuthority(node.right)) {
          report(node, 'radio selector fallback');
        }
      },
      ConditionalExpression(node) {
        if (facts.isAuthority(node.test)
          && (!facts.isAuthority(node.consequent) || !facts.isAuthority(node.alternate))) {
          report(node, 'radio selector fallback');
        }
      },
    };
  },
};

function directScopeFrame(typeAnnotation) {
  const type = typeAnnotation?.typeAnnotation;
  return type?.type === 'TSTypeReference' && type.typeName?.type === 'Identifier'
    && type.typeName.name === 'ScopeFrame';
}

const scopeMetadata = {
  meta: { type: 'problem', schema: [], messages: {
    metadata: 'ScopeFrame.{{field}} is radio metadata; only the declared ingress/projection owner may consume it.',
  } },
  create(context) {
    const path = projectPath(context.filename);
    if (SCOPE_METADATA_OWNERS.has(path)) return {};
    const frames = new Set();
    return {
      ':function'(node) {
        for (const parameter of node.params ?? []) {
          if (parameter.type === 'Identifier' && directScopeFrame(parameter.typeAnnotation)) frames.add(parameter.name);
        }
      },
      VariableDeclarator(node) {
        if (node.id.type !== 'Identifier') return;
        if (directScopeFrame(node.id.typeAnnotation)
          || (node.init?.type === 'Identifier' && frames.has(node.init.name))) frames.add(node.id.name);
      },
      MemberExpression(node) {
        const field = memberName(node);
        const object = unwrap(node.object);
        if (field && SCOPE_METADATA.has(field) && object?.type === 'Identifier' && frames.has(object.name)) {
          context.report({ node, messageId: 'metadata', data: { field } });
        }
      },
    };
  },
};

const recurringControl = {
  meta: { type: 'problem', schema: [], messages: {
    recurring: 'Recurring callback crosses the radio read/write/transport seam outside an acquisition owner.',
  } },
  create(context) {
    const path = projectPath(context.filename);
    if (ACQUISITION_OWNERS.has(path)) return {};
    const authorityBindings = new Set();
    const localFunctions = new Map();
    function recordImport(node) {
      const source = sourceText(node);
      if (!isRadioStore(source) && !isTransport(source) && !isCommandModule(source)) return;
      for (const specifier of node.specifiers) {
        if (specifier.type === 'ImportSpecifier' && specifier.importKind !== 'type') {
          authorityBindings.add(specifier.local.name);
        }
      }
    }
    function crosses(node, visiting = new Set()) {
      let found = false;
      walk(node, (current) => {
        if (found || current.type !== 'CallExpression') return;
        const callee = unwrap(current.callee);
        if (callee?.type === 'Identifier' && authorityBindings.has(callee.name)) {
          found = true;
        } else if (callee?.type === 'Identifier' && localFunctions.has(callee.name)
          && !visiting.has(callee.name)) {
          const next = new Set(visiting).add(callee.name);
          if (crosses(localFunctions.get(callee.name), next)) found = true;
        }
      });
      return found;
    }
    return {
      ImportDeclaration: recordImport,
      FunctionDeclaration(node) { if (node.id) localFunctions.set(node.id.name, node); },
      VariableDeclarator(node) {
        if (node.id.type === 'Identifier' && node.init
          && ['ArrowFunctionExpression', 'FunctionExpression'].includes(node.init.type)) {
          localFunctions.set(node.id.name, node.init);
        }
      },
      'Program:exit'(node) {
        walk(node, (current) => {
          if (current.type !== 'CallExpression') return;
          const callee = unwrap(current.callee);
          const timer = callee?.type === 'Identifier' && ['setInterval', 'setTimeout'].includes(callee.name)
            || memberName(callee) && ['setInterval', 'setTimeout'].includes(memberName(callee));
          if (!timer || !current.arguments[0]) return;
          const callback = current.arguments[0];
          const target = callback.type === 'Identifier' ? localFunctions.get(callback.name) ?? callback : callback;
          if (crosses(target)) context.report({ node: current, messageId: 'recurring' });
        });
      },
    };
  },
};

export const radioAuthorityOwners = Object.freeze({
  legacyPresentationAuthority: [...LEGACY_PRESENTATION_AUTHORITY].sort(),
  legacyWriterOwners: [...LEGACY_WRITER_OWNERS].sort(),
  acquisitionOwners: [...ACQUISITION_OWNERS].sort(),
  scopeMetadataOwners: [...SCOPE_METADATA_OWNERS].sort(),
});

export default {
  rules: {
    'structural-boundary': structuralBoundary,
    'authority-sink': authoritySink,
    'scope-metadata': scopeMetadata,
    'recurring-control': recurringControl,
  },
};
