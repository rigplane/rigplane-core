import type {
  ContinuousScalarView,
} from '../../../primitives/scalar/continuous-scalar.svelte';

export interface LegacyReadingPresentation {
  readonly phase: string | null;
  readonly busy: boolean | undefined;
  readonly description: string | null;
  readonly status: string | null;
}

export type ScalarAccessibilityPresentation = Readonly<{
  description?: string | null;
  valueText?: string | null;
}>;

export interface ScalarRenderPresentation {
  readonly source: 'command-owner' | 'legacy-reading' | 'none';
  readonly attributes: Readonly<{
    'data-command-phase': string | null | undefined;
    'aria-busy': 'true' | 'false' | boolean | undefined;
  }>;
  readonly description: string | null;
  readonly currentStatus: string | null;
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
  currentStatus: null,
  status: null,
  error: null,
});

function supplementDescription(
  existing: string | null,
  supplemental: string | null,
): string | null {
  if (supplemental === null) return existing;
  return existing === null || existing.length === 0
    ? supplemental
    : `${existing}. ${supplemental}`;
}

export function projectScalarRenderPresentation(
  view: Readonly<ContinuousScalarView>,
  legacy?: Readonly<LegacyReadingPresentation>,
  accessibility?: ScalarAccessibilityPresentation,
): Readonly<ScalarRenderPresentation> {
  const supplementalDescription = accessibility?.description?.trim()
    ? accessibility.description
    : null;
  if (view.evidence === 'command-feedback') {
    return Object.freeze({
      source: 'command-owner',
      attributes: view.presentation.attributes,
      description: supplementDescription(
        view.presentation.targetDescription,
        supplementalDescription,
      ),
      currentStatus: view.presentation.currentStatus,
      status: view.announcement,
      error: view.error,
    });
  }
  if (legacy === undefined || (
    legacy.phase === null
    && legacy.busy === undefined
    && legacy.description === null
    && legacy.status === null
  )) {
    return supplementalDescription === null
      ? NONE
      : Object.freeze({ ...NONE, description: supplementalDescription });
  }
  return Object.freeze({
    source: 'legacy-reading',
    attributes: Object.freeze({
      'data-command-phase': legacy.phase,
      'aria-busy': legacy.busy,
    }),
    description: supplementDescription(legacy.description, supplementalDescription),
    currentStatus: null,
    status: legacy.status,
    error: null,
  });
}
