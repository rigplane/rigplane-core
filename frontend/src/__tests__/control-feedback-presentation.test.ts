import { createHash } from 'node:crypto';
import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { ESLint } from 'eslint';
import { parse } from 'svelte/compiler';
import { describe, expect, it } from 'vitest';
import type { ControlFeedback } from '$lib/runtime/adapters/panel-adapters';
import {
  confirmedChecked, confirmedPressed, confirmedSelected,
  projectControlFeedbackPresentation,
  type ControlFeedbackPresentationInput, type ControlFeedbackPresentationState,
  type PresentationPhase,
} from '../primitives/control-feedback/control-feedback-presentation';

type ActualContractFits = ControlFeedback<number> extends ControlFeedbackPresentationInput<number>
  ? true : false;
const ACTUAL_CONTRACT_FITS: ActualContractFits = true;

const PHASES: readonly PresentationPhase[] = [
  'unavailable', 'idle', 'submitted', 'queued', 'dispatched',
  'awaiting-confirmation', 'confirmed', 'failed', 'timed-out', 'cancelled', 'superseded',
];
const BUSY = new Set<PresentationPhase>([
  'submitted', 'queued', 'dispatched', 'awaiting-confirmation',
]);
const EMPTY: ControlFeedbackPresentationState = { announcedTransitionIds: [] };
const feedback = <T>(phase: PresentationPhase, overrides: Partial<ControlFeedbackPresentationInput<T>> = {}) => ({
  confirmed: null, target: null, requestedTarget: null, phase,
  transitionId: phase === 'idle' || phase === 'unavailable' ? null : `transition-${phase}`,
  outcome: ['confirmed', 'failed', 'timed-out', 'cancelled', 'superseded'].includes(phase)
    ? { phase: phase as 'confirmed' | 'failed' | 'timed-out' | 'cancelled' | 'superseded' }
    : null,
  ...overrides,
} satisfies ControlFeedbackPresentationInput<T>);

describe('pure ControlFeedback presentation contract (MOR-1700)', () => {
  it('stays structurally assignable from the real ControlFeedback type', () => {
    expect(ACTUAL_CONTRACT_FITS).toBe(true);
  });

  it.each(PHASES)('maps phase %s deterministically', (phase) => {
    const result = projectControlFeedbackPresentation(
      feedback<number>(phase, { target: 2400, requestedTarget: 2400 }), EMPTY, String,
    );
    expect(result.attributes).toEqual({
      'data-command-phase': phase, 'aria-busy': BUSY.has(phase) ? 'true' : 'false',
    });
    expect(result.targetDescription).toBe('2400');
    expect(result.politeAnnouncement === null).toBe(phase === 'idle' || phase === 'unavailable');
  });

  it.each(['confirmed', 'failed', 'timed-out', 'cancelled', 'superseded'] as const)(
    'maps terminal outcome %s without a busy ARIA state', (phase) => {
      const result = projectControlFeedbackPresentation(
        feedback<number>(phase, { requestedTarget: 1800, outcome: { phase, error: 'bounded' } }),
        EMPTY, (value) => `${value} Hz`,
      );
      expect(result.attributes['aria-busy']).toBe('false');
      expect(result.politeAnnouncement).toMatchObject({
        politeness: 'polite', transitionId: `transition-${phase}`, phase,
        targetDescription: '1800 Hz',
      });
    },
  );

  it('announces one immutable transition once, then a new transition once', () => {
    const first = projectControlFeedbackPresentation(
      feedback<number>('submitted', { target: 3000, requestedTarget: 3000 }), EMPTY, String,
    );
    expect(first.politeAnnouncement?.transitionId).toBe('transition-submitted');
    const repeated = projectControlFeedbackPresentation(
      feedback<number>('submitted', { target: 3000, requestedTarget: 3000 }), first.state, String,
    );
    expect(repeated.politeAnnouncement).toBeNull();
    const next = projectControlFeedbackPresentation(
      feedback<number>('awaiting-confirmation', {
        target: 3000, requestedTarget: 3000, transitionId: 'transition-new',
      }), repeated.state, String,
    );
    expect(next.politeAnnouncement?.transitionId).toBe('transition-new');
    const delayedOld = projectControlFeedbackPresentation(
      feedback<number>('submitted', { transitionId: 'transition-submitted' }), next.state, String,
    );
    expect(delayedOld.politeAnnouncement).toBeNull();
  });

  it('derives toggle and choice ARIA only from confirmed truth', () => {
    const toggle = feedback<boolean>('submitted', { confirmed: false, target: true, requestedTarget: true });
    expect(confirmedPressed(toggle)).toBe(false);
    expect(confirmedChecked(toggle)).toBe(false);
    const choice = feedback<string>('submitted', { confirmed: 'A', target: 'B', requestedTarget: 'B' });
    expect(confirmedSelected(choice, 'A')).toBe(true);
    expect(confirmedSelected(choice, 'B')).toBe(false);
  });
});

const ROOT = path.resolve(fileURLToPath(import.meta.url), '../../..');
const forbidden = [
  `$lib/stores/commands.svelte`, `$lib/runtime/adapters/panel-adapters`,
  `$lib/runtime/tx-controller/model`, `../../lib/runtime/tx-controller/model`,
  `$lib/transport/ws-client`, `$lib/types/protocol`, `$lib/radio-model/ic7300`,
];

describe('control presentation ownership guards', () => {
  it.each(['src/primitives/control-feedback/Probe.ts', 'src/semantic/controls/Probe.ts'])(
    'rejects command/runtime/transport/protocol/model ownership from %s', async (filePath) => {
      const eslint = new ESLint({ cwd: ROOT });
      for (const specifier of forbidden) {
        const [result] = await eslint.lintText(`import x from '${specifier}';`, { filePath });
        expect(result.messages.some((message) => message.ruleId?.includes('restricted-imports')))
          .toBe(true);
      }
    },
  );

  it.each(['src/primitives/control-feedback/Probe.ts', 'src/semantic/controls/Probe.ts'])(
    'allows pure state and primitive/type imports from %s', async (filePath) => {
      const eslint = new ESLint({ cwd: ROOT });
      const [result] = await eslint.lintText([
        `import type { ServerState } from '$lib/types/state';`,
        `import type { Snippet } from 'svelte';`,
        `import type { View } from '../../primitives/control-feedback/view';`,
      ].join('\n'), { filePath });
      expect(result.messages.filter((message) => message.ruleId?.includes('restricted-imports'))).toEqual([]);
    },
  );
});

const LEGACY_CONTROL_SITES = new Set([
  'src/components-v2/layout/MobileRadioLayout.svelte#a9f799a13d75',
  'src/components-v2/panels/CwPanel.svelte#82d0e521912b', 'src/components-v2/panels/CwPanel.svelte#e302d3a8de4b',
  'src/components-v2/panels/CwPanel.svelte#fb6740dac91e', 'src/components-v2/panels/DspPanel.svelte#31a348dbab6d',
  'src/components-v2/panels/DspPanel.svelte#409c72b51337', 'src/components-v2/panels/DspPanel.svelte#5a8dc962ead0',
  'src/components-v2/panels/DspPanel.svelte#6a0b4a87ab69', 'src/components-v2/panels/DspPanel.svelte#ce12aeb06e25',
  'src/components-v2/panels/DspPanel.svelte#eddb9f478a23', 'src/components-v2/panels/EssentialsPanel.svelte#39653f7fe058',
  'src/components-v2/panels/FilterPanel.svelte#49a0b65296e0', 'src/components-v2/panels/FilterPanel.svelte#2889c9b45305',
  'src/components-v2/panels/FilterPanel.svelte#45a2a8b76cdd', 'src/components-v2/panels/FilterPanel.svelte#07912b556d70',
  'src/components-v2/panels/FilterPanel.svelte#83b16c4e4461', 'src/components-v2/panels/FilterPanel.svelte#ddac0de5d53d',
  'src/components-v2/panels/FilterPanel.svelte#8db073f4ac05', 'src/components-v2/panels/RfFrontEnd.svelte#a8f8303a6300',
  'src/components-v2/panels/RitXitPanel.svelte#7054e7896804', 'src/components-v2/panels/RxAudioPanel.svelte#af4ec0da24ce',
  'src/components-v2/panels/TxPanel.svelte#08e2cabde016', 'src/components-v2/panels/TxPanel.svelte#20e21329ce88',
  'src/components-v2/panels/TxPanel.svelte#677483b940e2', 'src/components-v2/panels/TxPanel.svelte#8290a6295502',
  'src/components-v2/panels/TxPanel.svelte#b2422ee3628b', 'src/components-v2/panels/VoxPanel.svelte#99fac974649b',
  'src/components-v2/panels/VoxPanel.svelte#c65cb9bcc7b3', 'src/components-v2/panels/VoxPanel.svelte#d3cec4d95b37',
  'src/semantic/CwKeyerSurface.svelte#6a9f08a3182a', 'src/semantic/DspSurface.svelte#03d935d52f33',
  'src/semantic/DspSurface.svelte#92b721bc2c82', 'src/semantic/FilterSurface.svelte#c8f40c9e3388',
  'src/semantic/FilterSurface.svelte#9f68312db9f4', 'src/semantic/RfFrontEndSurface.svelte#897264c6b463',
  'src/semantic/RfFrontEndSurface.svelte#eea85ce44dd4', 'src/semantic/RitXitScanSurface.svelte#b660fad6e34f',
  'src/semantic/RxAudioSurface.svelte#ddfaff2e05ce', 'src/semantic/TxAuxSurface.svelte#4a989eede846',
]);

function sources(root: string): string[] {
  return readdirSync(root, { withFileTypes: true }).flatMap((entry) => entry.isDirectory()
    ? entry.name === '__tests__' ? [] : sources(path.join(root, entry.name))
    : entry.name.endsWith('.svelte') ? [path.join(root, entry.name)] : []);
}

const LOCAL_RESOURCE_CONTROL_FILES = new Set([
  // Browser-local audio routing; these never write radio state or own command lifecycle.
  'src/components-v2/panels/AudioRoutingControl.svelte',
]);
const FEEDBACK_POLICIES = new Set(['state-backed']);
type AstNode = Record<string, any>;

function visit(value: unknown, enter: (node: AstNode) => void): void {
  if (Array.isArray(value)) { value.forEach((item) => visit(item, enter)); return; }
  if (value === null || typeof value !== 'object') return;
  const node = value as AstNode;
  if (typeof node.type === 'string') enter(node);
  for (const [key, child] of Object.entries(node)) {
    if (key !== 'loc' && key !== 'metadata') visit(child, enter);
  }
}

function literalAttribute(node: AstNode, name: string): { present: boolean; value: string | null } {
  const attribute = (node.attributes ?? []).find((candidate: AstNode) =>
    candidate.type === 'Attribute' && candidate.name === name);
  if (!attribute) return { present: false, value: null };
  const part = Array.isArray(attribute.value) && attribute.value.length === 1 ? attribute.value[0] : null;
  if (part?.type === 'Text') return { present: true, value: part.data };
  if (part?.type === 'MustacheTag' && part.expression?.type === 'Literal'
    && typeof part.expression.value === 'string') return { present: true, value: part.expression.value };
  return { present: true, value: null };
}

function discoverControls(source: string): { fragments: string[]; invalidPolicies: string[] } {
  const fragments: string[] = [];
  const invalidPolicies: string[] = [];
  const ast = parse(source) as AstNode;
  const valueControls = new Set(['ValueControl']);
  for (const statement of ast.instance?.content?.body ?? []) {
    if (statement.type === 'ImportDeclaration' && /(?:^|\/)ValueControl\.svelte$/.test(statement.source?.value)) {
      statement.specifiers.forEach((specifier: AstNode) => valueControls.add(specifier.local.name));
    }
  }
  for (const statement of ast.instance?.content?.body ?? []) {
    if (statement.type !== 'VariableDeclaration') continue;
    statement.declarations.forEach((declaration: AstNode) => {
      if (declaration.id?.type === 'Identifier' && declaration.init?.type === 'Identifier'
        && valueControls.has(declaration.init.name)) valueControls.add(declaration.id.name);
    });
  }
  visit(ast.html, (node) => {
    const component = node.type === 'InlineComponent' && (valueControls.has(node.name)
      || (node.name === 'svelte:component' && valueControls.has(node.expression?.name)));
    const element = node.type === 'Element';
    const input = element && (node.name === 'input'
      || (node.name === 'svelte:element' && node.tag?.type === 'Literal' && node.tag.value === 'input'));
    const policyName = component ? 'feedbackPolicy' : element ? 'data-control-feedback-policy' : '';
    const policy = policyName ? literalAttribute(node, policyName) : { present: false, value: null };
    if (policy.present && (policy.value === null || !FEEDBACK_POLICIES.has(policy.value))) {
      invalidPolicies.push(policy.value ?? '<dynamic>');
      return;
    }
    if (!component && !input) return;
    const attributes = node.attributes ?? [];
    const lastSpread = attributes.findLastIndex((attribute: AstNode) => attribute.type === 'Spread');
    const spreads = lastSpread >= 0;
    const type = input ? literalAttribute(node, 'type') : { present: false, value: null };
    if (input && !spreads && (!type.present || (type.value !== null && type.value !== 'range'))) return;
    const policyIndex = attributes.findIndex((attribute: AstNode) =>
      attribute.type === 'Attribute' && attribute.name === policyName);
    if (!policy.present || policyIndex < lastSpread) {
      fragments.push(source.slice(node.start, node.end).replace(/\s+/g, ' ').trim());
    }
  });
  return { fragments, invalidPolicies };
}

it.each([
  [`<input type={'range'} />`, 'literal expression'],
  [`<script>let kind = 'text';</script><input type={kind} />`, 'dynamic type'],
  [`<svelte:element this={'input'} type={'range'} />`, 'dynamic-element range'],
  [`<script>let attrs = {};</script><input {...attrs} />`, 'spread input'],
  [`<script>let attrs = {};</script><input type="range" data-control-feedback-policy="state-backed" {...attrs} />`, 'late-spread input'],
  [`<script>let props = {};</script><ValueControl {...props} />`, 'spread ValueControl'],
  [`<script>import VC from './ValueControl.svelte'; const Control = VC;</script><Control />`, 'aliased ValueControl'],
])('discovers a %s radio-control candidate', (source) => {
  expect(discoverControls(source).fragments).toHaveLength(1);
});

it('accepts only the closed state-backed feedback policy vocabulary', () => {
  expect(discoverControls(`<input type="range" data-control-feedback-policy="state-backed" />`))
    .toEqual({ fragments: [], invalidPolicies: [] });
  expect(discoverControls(`<ValueControl feedbackPolicy="invented" />`).invalidPolicies)
    .toEqual(['invented']);
  expect(discoverControls(`<input type="text" data-control-feedback-policy={'invented'} />`).invalidPolicies)
    .toEqual(['invented']);
});

it('keeps unannotated radio-backed raw controls on the shrink-only inventory', () => {
  const candidates = sources(path.join(ROOT, 'src')).filter((file) =>
    !/(ControlButtonDemo|ValueControlLab|SMeterDemo)/.test(file)
    && (/\/semantic\//.test(file) || /\/components-v2\/panels\//.test(file)
      || file.endsWith('/components-v2/layout/MobileRadioLayout.svelte'))
    && !LOCAL_RESOURCE_CONTROL_FILES.has(path.relative(ROOT, file)));
  const discovered = candidates.map((file) => ({ file, ...discoverControls(readFileSync(file, 'utf8')) }));
  expect(discovered.flatMap(({ invalidPolicies }) => invalidPolicies)).toEqual([]);
  const actual = discovered.flatMap(({ file, fragments }) => fragments.map((fragment) =>
    `${path.relative(ROOT, file)}#${createHash('sha256').update(fragment).digest('hex').slice(0, 12)}`));
  expect(actual.length).toBeLessThanOrEqual(LEGACY_CONTROL_SITES.size);
  expect(actual.filter((site) => !LEGACY_CONTROL_SITES.has(site))).toEqual([]);
});
