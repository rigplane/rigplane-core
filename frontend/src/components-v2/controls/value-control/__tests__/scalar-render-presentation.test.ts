import { describe, expect, it } from 'vitest';
import type { ContinuousScalarView } from '../../../../primitives/scalar/continuous-scalar.svelte';
import {
  projectScalarRenderPresentation,
  type LegacyReadingPresentation,
} from '../scalar-render-presentation';

const commandView = (): ContinuousScalarView => ({
  evidence: 'command-feedback',
  canonical: 20,
  draft: null,
  displayed: 20,
  interactionBase: 20,
  editable: true,
  busy: true,
  interaction: 'idle',
  domain: Object.freeze({
    min: 0, max: 100, step: 10, defaultValue: null, fineStepDivisor: 10,
  }),
  domainValid: true,
  feedback: {} as never,
  confirmed: 20,
  target: 30,
  requested: 30,
  phase: 'awaiting-confirmation',
  error: 'radio rejected',
  presentation: {
    attributes: {
      'data-command-phase': 'awaiting-confirmation',
      'aria-busy': 'true',
    },
    targetDescription: '30 Hz',
    currentStatus: 'Awaiting confirmation: 30 Hz',
    politeAnnouncement: null,
    state: { announcedTransitionIds: [] },
  },
  announcement: 'Awaiting confirmation: 30 Hz',
});

const readingView = (): ContinuousScalarView => ({
  evidence: 'reading',
  canonical: 20,
  draft: null,
  displayed: 20,
  interactionBase: 20,
  editable: true,
  busy: false,
  interaction: 'idle',
  domain: Object.freeze({
    min: 0, max: 100, step: 10, defaultValue: null, fineStepDivisor: 10,
  }),
  domainValid: true,
  reading: { status: 'known', value: 20 },
  confirmed: null,
  target: null,
  requested: null,
  phase: null,
  error: null,
  presentation: null,
  announcement: null,
});

describe('projectScalarRenderPresentation', () => {
  it('forwards only command-owner presentation and error truth', () => {
    const view = commandView();
    const legacy: LegacyReadingPresentation = {
      phase: 'legacy-phase',
      busy: false,
      description: 'legacy description',
      status: 'legacy status',
    };

    const projection = projectScalarRenderPresentation(view, legacy);

    expect(projection).toEqual({
      source: 'command-owner',
      attributes: view.presentation?.attributes,
      description: '30 Hz',
      currentStatus: 'Awaiting confirmation: 30 Hz',
      status: 'Awaiting confirmation: 30 Hz',
      error: 'radio rejected',
    });
    expect(projectScalarRenderPresentation(view, legacy)).toEqual(projection);
  });

  it('keeps current command status and error after the one-shot status is consumed', () => {
    const view = { ...commandView(), announcement: null } satisfies ContinuousScalarView;

    expect(projectScalarRenderPresentation(view)).toMatchObject({
      source: 'command-owner',
      currentStatus: 'Awaiting confirmation: 30 Hz',
      status: null,
      error: 'radio rejected',
    });
  });

  it('supplements command target description without replacing owner status or error truth', () => {
    const view = commandView();

    expect(projectScalarRenderPresentation(view, undefined, {
      description: 'Canonical 20 Hz; awaiting device confirmation',
      valueText: '20 Hz; awaiting confirmation of 30 Hz',
    })).toEqual({
      source: 'command-owner',
      attributes: view.presentation?.attributes,
      description: '30 Hz. Canonical 20 Hz; awaiting device confirmation',
      currentStatus: 'Awaiting confirmation: 30 Hz',
      status: 'Awaiting confirmation: 30 Hz',
      error: 'radio rejected',
    });
  });

  it('projects only non-empty descriptive metadata for an undecorated reading', () => {
    expect(projectScalarRenderPresentation(readingView(), undefined, {
      description: 'Canonical receiver gain',
      valueText: '20 percent',
    })).toMatchObject({
      source: 'none',
      description: 'Canonical receiver gain',
      status: null,
      error: null,
    });
    expect(projectScalarRenderPresentation(readingView(), undefined, {
      description: null,
      valueText: null,
    })).toEqual(projectScalarRenderPresentation(readingView()));
  });

  it('preserves arbitrary legacy reading decoration verbatim', () => {
    const legacy: LegacyReadingPresentation = {
      phase: 'caller-authored-phase',
      busy: false,
      description: '',
      status: 'literal legacy status',
    };

    expect(projectScalarRenderPresentation(readingView(), legacy)).toEqual({
      source: 'legacy-reading',
      attributes: {
        'data-command-phase': 'caller-authored-phase',
        'aria-busy': false,
      },
      description: '',
      currentStatus: null,
      status: 'literal legacy status',
      error: null,
    });
  });

  it('returns no feedback projection for an undecorated reading', () => {
    expect(projectScalarRenderPresentation(readingView(), {
      phase: null,
      busy: undefined,
      description: null,
      status: null,
    })).toEqual({
      source: 'none',
      attributes: {
        'data-command-phase': undefined,
        'aria-busy': undefined,
      },
      description: null,
      currentStatus: null,
      status: null,
      error: null,
    });
  });
});
