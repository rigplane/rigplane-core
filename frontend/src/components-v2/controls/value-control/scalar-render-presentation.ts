import type {
  ContinuousScalarView,
} from '../../../primitives/scalar/continuous-scalar.svelte';

export interface LegacyReadingPresentation {
  readonly phase: string | null;
  readonly busy: boolean | undefined;
  readonly description: string | null;
  readonly status: string | null;
}

export interface ScalarRenderPresentation {
  readonly source: 'command-owner' | 'legacy-reading' | 'none';
  readonly attributes: Readonly<{
    'data-command-phase': string | null | undefined;
    'aria-busy': 'true' | 'false' | boolean | undefined;
  }>;
  readonly description: string | null;
  readonly status: string | null;
  readonly error: string | null;
}

const NONE: Readonly<ScalarRenderPresentation> = Object.freeze({
  source: 'none',
  attributes: Object.freeze({
    'data-command-phase': undefined,
    'aria-busy': undefined,
  }),
  description: null,
  status: null,
  error: null,
});

export function projectScalarRenderPresentation(
  view: Readonly<ContinuousScalarView>,
  legacy?: Readonly<LegacyReadingPresentation>,
): Readonly<ScalarRenderPresentation> {
  if (view.evidence === 'command-feedback') {
    return Object.freeze({
      source: 'command-owner',
      attributes: view.presentation.attributes,
      description: view.presentation.targetDescription,
      status: view.announcement,
      error: view.error,
    });
  }
  if (legacy === undefined || (
    legacy.phase === null
    && legacy.busy === undefined
    && legacy.description === null
    && legacy.status === null
  )) return NONE;
  return Object.freeze({
    source: 'legacy-reading',
    attributes: Object.freeze({
      'data-command-phase': legacy.phase,
      'aria-busy': legacy.busy,
    }),
    description: legacy.description,
    status: legacy.status,
    error: null,
  });
}
