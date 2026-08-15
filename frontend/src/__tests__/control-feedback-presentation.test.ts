import { createHash } from 'node:crypto';
import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { ESLint } from 'eslint';
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
});

const LEGACY_CONTROL_SITES = new Set([
  'src/components-v2/layout/MobileRadioLayout.svelte#a9f799a13d75',
  'src/components-v2/panels/AudioRoutingControl.svelte#719e91fcc74e',
  'src/components-v2/panels/AudioRoutingControl.svelte#e035d8474aab',
  'src/components-v2/panels/CwPanel.svelte#82d0e521912b', 'src/components-v2/panels/CwPanel.svelte#e302d3a8de4b',
  'src/components-v2/panels/CwPanel.svelte#fb6740dac91e', 'src/components-v2/panels/DspPanel.svelte#31a348dbab6d',
  'src/components-v2/panels/DspPanel.svelte#409c72b51337', 'src/components-v2/panels/DspPanel.svelte#5a8dc962ead0',
  'src/components-v2/panels/DspPanel.svelte#6a0b4a87ab69', 'src/components-v2/panels/DspPanel.svelte#ce12aeb06e25',
  'src/components-v2/panels/DspPanel.svelte#eddb9f478a23', 'src/components-v2/panels/EssentialsPanel.svelte#39653f7fe058',
  'src/components-v2/panels/FilterPanel.svelte#2702e87b2f75', 'src/components-v2/panels/FilterPanel.svelte#2889c9b45305',
  'src/components-v2/panels/FilterPanel.svelte#45a2a8b76cdd', 'src/components-v2/panels/FilterPanel.svelte#45b5af130dd8',
  'src/components-v2/panels/FilterPanel.svelte#4a136523ec4e', 'src/components-v2/panels/FilterPanel.svelte#66a02375d5b3',
  'src/components-v2/panels/FilterPanel.svelte#79ad46e5c3ed', 'src/components-v2/panels/RfFrontEnd.svelte#a8f8303a6300',
  'src/components-v2/panels/RitXitPanel.svelte#7054e7896804', 'src/components-v2/panels/RxAudioPanel.svelte#af4ec0da24ce',
  'src/components-v2/panels/TxPanel.svelte#08e2cabde016', 'src/components-v2/panels/TxPanel.svelte#20e21329ce88',
  'src/components-v2/panels/TxPanel.svelte#677483b940e2', 'src/components-v2/panels/TxPanel.svelte#8290a6295502',
  'src/components-v2/panels/TxPanel.svelte#b2422ee3628b', 'src/components-v2/panels/VoxPanel.svelte#99fac974649b',
  'src/components-v2/panels/VoxPanel.svelte#c65cb9bcc7b3', 'src/components-v2/panels/VoxPanel.svelte#d3cec4d95b37',
  'src/semantic/CwKeyerSurface.svelte#3033c3406521', 'src/semantic/DspSurface.svelte#2e25a1814865',
  'src/semantic/DspSurface.svelte#5bf413796508', 'src/semantic/FilterSurface.svelte#0f4ef749400b',
  'src/semantic/FilterSurface.svelte#9d3ff04482f4', 'src/semantic/RfFrontEndSurface.svelte#f5b2db843152',
  'src/semantic/RfFrontEndSurface.svelte#fc3221708e49', 'src/semantic/RitXitScanSurface.svelte#53d8f04e654a',
  'src/semantic/RxAudioSurface.svelte#73cd6e7c05ad', 'src/semantic/TxAuxSurface.svelte#51284cb32937',
]);

function sources(root: string): string[] {
  return readdirSync(root, { withFileTypes: true }).flatMap((entry) => entry.isDirectory()
    ? entry.name === '__tests__' ? [] : sources(path.join(root, entry.name))
    : entry.name.endsWith('.svelte') ? [path.join(root, entry.name)] : []);
}

it('keeps unannotated radio-backed raw controls on the shrink-only inventory', () => {
  const candidates = sources(path.join(ROOT, 'src')).filter((file) =>
    !/(ControlButtonDemo|ValueControlLab|SMeterDemo)/.test(file)
    && (/\/semantic\//.test(file) || /\/components-v2\/panels\//.test(file)
      || file.endsWith('/components-v2/layout/MobileRadioLayout.svelte')));
  const tag = /<(?:ValueControl\b|input\b(?=[^>]*\btype\s*=\s*["']range["']))[\s\S]*?>/g;
  const actual = candidates.flatMap((file) => [...readFileSync(file, 'utf8').matchAll(tag)]
    .map((match) => match[0].replace(/\s+/g, ' ').trim())
    .filter((match) => !/(?:feedbackPolicy|data-control-feedback-policy)\s*=/.test(match))
    .map((match) => `${path.relative(ROOT, file)}#${createHash('sha256').update(match).digest('hex').slice(0, 12)}`));
  expect(actual.length).toBeLessThanOrEqual(LEGACY_CONTROL_SITES.size);
  expect(actual.filter((site) => !LEGACY_CONTROL_SITES.has(site))).toEqual([]);
});
