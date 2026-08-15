#!/usr/bin/env node
/** AST-backed shrink-only inventory for radio range-control feedback debt (MOR-1713). */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse } from 'svelte/compiler';

const UNKNOWN = Symbol('dynamic');
const POLICIES = new Set(['radio-backed', 'feedback-integrated', 'local-resource']);
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const EXACT_DEMO_FILES = new Set([
  'src/components-v2/controls/ControlButtonDemo.svelte',
  'src/components-v2/controls/ValueControlLab.svelte',
  'src/components-v2/meters/SMeterDemo.svelte',
]);
const RELEVANT = ['type', 'feedback-policy', 'aria-label', 'label', 'value'];
/** @typedef {{value: any, text: string}} Entry */
/** @typedef {{identity: string, file: string, kind: string, label: string, value: string, policy: string}} Site */
/** @param {string} key */
const keyOf = (key) => key === 'feedbackPolicy' ? 'feedback-policy' : key;

// Updated only downward: removal means feedback integration; additions fail the gate.
export const FROZEN_RADIO_DEBT = new Set([
  'src/components-v2/layout/MobileRadioLayout.svelte::ValueControl::RF Power::tx.rfPower',
  'src/components-v2/panels/CwPanel.svelte::ValueControl::Break-in Delay::breakInDelay',
  'src/components-v2/panels/CwPanel.svelte::ValueControl::CW Pitch::cwPitch',
  'src/components-v2/panels/CwPanel.svelte::ValueControl::Key Speed::keySpeed',
  'src/components-v2/panels/DspPanel.svelte::ValueControl::AGC Time::agcTimeConstant',
  'src/components-v2/panels/DspPanel.svelte::ValueControl::NB Depth::nbDepth',
  'src/components-v2/panels/DspPanel.svelte::ValueControl::NB Level::nbLevel',
  'src/components-v2/panels/DspPanel.svelte::ValueControl::NB Width::nbWidth',
  'src/components-v2/panels/DspPanel.svelte::ValueControl::NR Level::nrLevel',
  'src/components-v2/panels/DspPanel.svelte::ValueControl::Notch Position::notchFreq',
  'src/components-v2/panels/EssentialsPanel.svelte::ValueControl::AF Level::rxAudio.afLevel',
  'src/components-v2/panels/FilterPanel.svelte::ValueControl::IF SHIFT::ifShift',
  'src/components-v2/panels/FilterPanel.svelte::ValueControl::PBT Inner::pbtInner',
  'src/components-v2/panels/FilterPanel.svelte::ValueControl::PBT Outer::pbtOuter',
  'src/components-v2/panels/FilterPanel.svelte::ValueControl::WIDTH::hzToTableIndex(filterWidth)',
  'src/components-v2/panels/FilterPanel.svelte::ValueControl::hasPbt ? "IF Shift (derived)" : "IF Shift"::ifShift',
  'src/components-v2/panels/FilterPanel.svelte::ValueControl::label::draftWidths[index] ?? visibleWidths[index] ?? factoryDefaults[index]',
  'src/components-v2/panels/FilterPanel.svelte::ValueControl::label::hzToTableIndex(draftWidths[index] ?? visibleWidths[index] ?? factoryDefaults[index])',
  'src/components-v2/panels/RfFrontEnd.svelte::ValueControl::RF Gain::rfGain',
  'src/components-v2/panels/RitXitPanel.svelte::ValueControl::Offset::offsetValue',
  'src/components-v2/panels/RxAudioPanel.svelte::ValueControl::AF Level::props.afLevel',
  'src/components-v2/panels/TxPanel.svelte::ValueControl::Comp Level::compLevel',
  'src/components-v2/panels/TxPanel.svelte::ValueControl::Drive Gain::driveGain',
  'src/components-v2/panels/TxPanel.svelte::ValueControl::Mic Gain::micGain',
  'src/components-v2/panels/TxPanel.svelte::ValueControl::Mon Level::monLevel',
  'src/components-v2/panels/TxPanel.svelte::ValueControl::RF Power::rfPower',
  'src/components-v2/panels/VoxPanel.svelte::ValueControl::Anti-VOX::antiVoxGain',
  'src/components-v2/panels/VoxPanel.svelte::ValueControl::VOX Delay::voxDelay',
  'src/components-v2/panels/VoxPanel.svelte::ValueControl::VOX Gain::voxGain',
  "src/semantic/CwKeyerSurface.svelte::input::unlabelled::f.reading.status === 'known' ? f.reading.value : min",
  'src/semantic/DspSurface.svelte::input::unlabelled::numberOf(dsp.nbLevel, 0)',
  'src/semantic/DspSurface.svelte::input::unlabelled::numberOf(dsp[field], min)',
  'src/semantic/FilterSurface.svelte::input::unlabelled::numberOf(filterPassband[field], min)',
  'src/semantic/FilterSurface.svelte::input::unlabelled::numberOf(modeFilter.filterWidth, 0)',
  'src/semantic/RfFrontEndSurface.svelte::input::unlabelled::combinedNormX',
  "src/semantic/RfFrontEndSurface.svelte::input::unlabelled::rf[field].reading.status === 'known' ? rf[field].reading.value : 0",
  "src/semantic/RitXitScanSurface.svelte::input::unlabelled::offset.reading.status === 'known' ? offset.reading.value : 0",
  "src/semantic/RxAudioSurface.svelte::input::unlabelled::rx.afLevel.reading.status === 'known' ? rx.afLevel.reading.value : 0",
  'src/semantic/TxAuxSurface.svelte::input::unlabelled::numberOf(txAux[field], min)',
]);

/** @param {any} node @returns {any} */
const unwrap = (node) => {
  while (node && ['TSAsExpression', 'TSTypeAssertion', 'TSNonNullExpression', 'ChainExpression', 'ParenthesizedExpression'].includes(node.type)) {
    node = node.expression;
  }
  return node;
};

/** @param {any} ast @returns {any[]} */
function programs(ast) {
  return [ast.instance?.content, ast.module?.content].filter(Boolean);
}

/** @param {any} ast */
function bindingsAndImports(ast) {
  const bindings = new Map();
  const valueControls = new Set();
  for (const program of programs(ast)) {
    for (const statement of program.body) {
      if (statement.type === 'ImportDeclaration') {
        const source = String(statement.source.value);
        const direct = source.endsWith('/ValueControl.svelte') || source === './ValueControl.svelte';
        const barrel = /(?:^|\/)value-control(?:\/index)?$/.test(source);
        for (const specifier of statement.specifiers) {
          const imported = specifier.imported?.name ?? specifier.imported?.value;
          if ((direct && specifier.type === 'ImportDefaultSpecifier') || (barrel && imported === 'ValueControl')) {
            valueControls.add(specifier.local.name);
          }
        }
      }
      if (statement.type === 'VariableDeclaration' && statement.kind === 'const') {
        for (const declaration of statement.declarations) {
          if (declaration.id.type === 'Identifier' && declaration.init) bindings.set(declaration.id.name, declaration.init);
        }
      }
    }
  }
  return { bindings, valueControls };
}

/** @param {any} node @param {Map<string, any>} bindings @param {Set<string>} [seen] @returns {any} */
function staticValue(node, bindings, seen = new Set()) {
  node = unwrap(node);
  if (!node) return UNKNOWN;
  if (node.type === 'Literal') return node.value;
  if (node.type === 'TemplateLiteral' && node.expressions.length === 0) return node.quasis[0].value.cooked;
  if (node.type === 'Identifier' && bindings.has(node.name) && !seen.has(node.name)) {
    return staticValue(bindings.get(node.name), bindings, new Set([...seen, node.name]));
  }
  if (node.type === 'ConditionalExpression') {
    const yes = staticValue(node.consequent, bindings, seen);
    const no = staticValue(node.alternate, bindings, seen);
    return yes !== UNKNOWN && Object.is(yes, no) ? yes : UNKNOWN;
  }
  return UNKNOWN;
}

/** @param {any} node @param {Map<string, any>} bindings @param {Set<string>} seeds @param {Set<string>} [seen] @returns {boolean} */
function mayBeValueControl(node, bindings, seeds, seen = new Set()) {
  node = unwrap(node);
  if (!node) return false;
  if (node.type === 'Identifier') {
    if (seeds.has(node.name)) return true;
    if (seen.has(node.name) || !bindings.has(node.name)) return false;
    return mayBeValueControl(bindings.get(node.name), bindings, seeds, new Set([...seen, node.name]));
  }
  if (node.type === 'ConditionalExpression') {
    return mayBeValueControl(node.consequent, bindings, seeds, seen)
      || mayBeValueControl(node.alternate, bindings, seeds, seen);
  }
  if (node.type === 'LogicalExpression' || node.type === 'SequenceExpression') {
    return Object.values(node).some((value) => Boolean(value?.type && mayBeValueControl(value, bindings, seeds, seen)));
  }
  return false;
}

/** @param {any} node @param {string} source */
function expressionText(node, source) {
  node = unwrap(node);
  return node?.start === undefined ? 'dynamic' : source.slice(node.start, node.end).replace(/\s+/g, ' ').trim();
}

/** @param {any} attribute @param {Map<string, any>} bindings @param {string} source @returns {Entry} */
function attributeEntry(attribute, bindings, source) {
  if (attribute.value === true) return { value: true, text: 'true' };
  const value = attribute.value;
  if (Array.isArray(value)) {
    if (value.length === 1 && value[0].type === 'Text') return { value: value[0].data, text: value[0].data };
    return { value: UNKNOWN, text: source.slice(attribute.start, attribute.end) };
  }
  const expression = value?.type === 'ExpressionTag' ? value.expression : value;
  return { value: staticValue(expression, bindings), text: expressionText(expression, source) };
}

/** @param {any} node @param {Map<string, any>} bindings @param {string} source @param {Set<string>} [seen] @returns {Map<string, Entry>|null} */
function objectEntries(node, bindings, source, seen = new Set()) {
  node = unwrap(node);
  if (node?.type === 'Identifier' && bindings.has(node.name) && !seen.has(node.name)) {
    return objectEntries(bindings.get(node.name), bindings, source, new Set([...seen, node.name]));
  }
  if (node?.type !== 'ObjectExpression') return null;
  const result = new Map();
  for (const property of node.properties) {
    if (property.type === 'SpreadElement') {
      const nested = objectEntries(property.argument, bindings, source, seen);
      if (!nested) for (const key of RELEVANT) result.set(key, { value: UNKNOWN, text: 'dynamic' });
      else for (const [key, value] of nested) result.set(key, value);
      continue;
    }
    if (property.type !== 'Property' || property.computed) continue;
    const key = keyOf(property.key.name ?? property.key.value);
    if (RELEVANT.includes(key)) result.set(key, {
      value: staticValue(property.value, bindings), text: expressionText(property.value, source),
    });
  }
  return result;
}

/** @param {any} node @param {Map<string, any>} bindings @param {string} source @returns {Map<string, Entry>} */
function effectiveAttributes(node, bindings, source) {
  const result = new Map();
  for (const attribute of node.attributes ?? []) {
    if (attribute.type === 'SpreadAttribute') {
      const entries = objectEntries(attribute.expression, bindings, source);
      if (!entries) for (const key of RELEVANT) result.set(key, { value: UNKNOWN, text: 'dynamic' });
      else for (const [key, value] of entries) result.set(key, value);
    } else if (attribute.type === 'Attribute' && RELEVANT.includes(keyOf(attribute.name))) {
      result.set(keyOf(attribute.name), attributeEntry(attribute, bindings, source));
    }
  }
  return result;
}

/** @param {Map<string, Entry>} attributes @param {string} identity */
function sitePolicy(attributes, identity) {
  const entry = attributes.get('feedback-policy');
  if (!entry) return 'radio-backed';
  if (entry.value === UNKNOWN || typeof entry.value !== 'string') throw new Error(`${identity}: static feedback policy required`);
  if (!POLICIES.has(entry.value)) throw new Error(`${identity}: unknown feedback policy ${entry.value}`);
  return entry.value;
}

/** @param {string} file @param {string} kind @param {Map<string, Entry>} attributes */
function isLocalGain(file, kind, attributes) {
  if (kind !== 'input' || !file.endsWith('src/components-v2/panels/AudioRoutingControl.svelte')) return false;
  const label = attributes.get('aria-label')?.value;
  const value = attributes.get('value')?.text;
  return (label === 'MAIN gain in decibels' && value === 'mainGainDb')
    || (label === 'SUB gain in decibels' && value === 'subGainDb');
}

/** @param {string} file @param {string} source @returns {Site[]} */
export function auditSvelteSource(file, source) {
  const ast = parse(source, { modern: true, filename: file });
  const { bindings, valueControls } = bindingsAndImports(ast);
  /** @type {Site[]} */
  const sites = [];
  /** @param {any} node */
  const visit = (node) => {
    if (!node || typeof node !== 'object') return;
    let kind = null;
    if (node.type === 'Component' && mayBeValueControl({ type: 'Identifier', name: node.name }, bindings, valueControls)) kind = 'ValueControl';
    if (node.type === 'RegularElement' && node.name === 'input') kind = 'input';
    if (node.type === 'SvelteElement') {
      const tag = staticValue(node.tag, bindings);
      if (tag === 'input' || tag === UNKNOWN) kind = 'input';
    }
    if (kind) {
      const attributes = effectiveAttributes(node, bindings, source);
      const type = attributes.get('type')?.value;
      if (kind === 'ValueControl' || type === 'range' || type === UNKNOWN) {
        const label = attributes.get(kind === 'input' ? 'aria-label' : 'label');
        const value = attributes.get('value');
        const identity = `${file}::${kind}::${label?.text ?? 'unlabelled'}::${value?.text ?? 'unbound'}`;
        let policy = sitePolicy(attributes, identity);
        if (policy === 'radio-backed' && !attributes.has('feedback-policy') && isLocalGain(file, kind, attributes)) policy = 'local-resource';
        if (policy === 'local-resource' && !isLocalGain(file, kind, attributes)) throw new Error(`${identity}: local-resource policy is not allowed`);
        sites.push({ identity, file, kind, label: label?.text ?? 'unlabelled', value: value?.text ?? 'unbound', policy });
      }
    }
    for (const [key, value] of Object.entries(node)) {
      if (key === 'metadata' || key === 'parent') continue;
      if (Array.isArray(value)) value.forEach(visit);
      else if (value && typeof value === 'object') visit(value);
    }
  };
  visit(ast.fragment);
  const duplicate = sites.find((site, index) => sites.findIndex((other) => other.identity === site.identity) !== index);
  if (duplicate) throw new Error(`${duplicate.identity}: duplicate control identity`);
  return sites;
}

/** @param {Array<{identity: string, policy?: string}>} sites @param {Set<string>} [frozen] */
export function assertShrinkOnly(sites, frozen = FROZEN_RADIO_DEBT) {
  const current = sites.filter((site) => !site.policy || site.policy === 'radio-backed').map((site) => site.identity).sort();
  const grew = current.filter((identity) => !frozen.has(identity));
  if (grew.length) throw new Error(`radio-backed feedback debt grew: ${grew.join(', ')}`);
  return current;
}

/** @param {string} directory @returns {string[]} */
function svelteFiles(directory) {
  return readdirSync(directory).flatMap((name) => {
    const path = resolve(directory, name);
    return statSync(path).isDirectory() ? svelteFiles(path) : path.endsWith('.svelte') ? [path] : [];
  });
}

/** @param {string} [root] @returns {Site[]} */
export function scanRepository(root = ROOT) {
  return svelteFiles(resolve(root, 'src')).flatMap((path) => {
    const file = relative(root, path);
    return EXACT_DEMO_FILES.has(file) ? [] : auditSvelteSource(file, readFileSync(path, 'utf8'));
  });
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const current = assertShrinkOnly(scanRepository());
  console.log(`control-feedback debt inventory: ${current.length} radio-backed range site(s)`);
}
