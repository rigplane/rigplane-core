/**
 * Receiver-facing control-feedback qualification — the pure decision core
 * for the DSP, RF/SQL, receiver-echo and PBT families, split out of
 * `panel-adapters.ts`. Every function here is a function of its
 * arguments: the thin wrappers in `panel-adapters.ts` read the live
 * runtime values (state, caps, command lifecycles, the control session)
 * and the store-registered command descriptors, then pass them in. No
 * store value, runtime singleton or rune is read here.
 */

import { toRadioViewModel } from './radio-view-model-adapter';
import { qualifyDisplayObservation, qualifyRadioDisplayObservation } from './display-observation';
import {
  hasUsableObservation,
  projectControlFeedback,
  projectControlFeedbackToDisplay,
  projectPendingTargetToDisplay,
  unavailableControlFeedback,
  type ControlFeedback,
} from './control-feedback-projection';
import {
  measuredPbtRawToHz,
  pbtRangeFromCaps,
  resolveControlContract,
} from '$lib/radio/filter-controls';
import type {
  CommandLifecycle,
  ControlFeedbackScope,
  DspCommandFeedbackField,
  StateBackedCommandDescriptor,
} from '$lib/stores/commands.svelte';
import type { ServerState } from '$lib/types/state';
import type { Capabilities } from '$lib/types/capabilities';
import { manualNotchReading, resolveFilterModeConfig } from '../props/panel-props';

/** The control-session facts the qualification decisions read. */
export interface ControlSessionFacts {
  readonly state: string;
  readonly epoch: number;
}

const DSP_FEEDBACK_CAPABILITIES: Readonly<Record<
  Exclude<DspCommandFeedbackField, 'nbWidth' | 'nbDepth'>, string
>> = Object.freeze({
  nbLevel: 'nb', nrLevel: 'nr',
  notchFilter: 'notch', manualNotchWidth: 'notch', agcTimeConstant: 'agc',
});

/** Pure DSP feedback decision; the wrapper supplies the live inputs and
 *  the store-registered descriptor for `field`. */
export function dspControlFeedback(
  field: DspCommandFeedbackField,
  session: ControlSessionFacts,
  state: ServerState | null,
  caps: Capabilities | null,
  commands: readonly CommandLifecycle[],
  descriptor: StateBackedCommandDescriptor<number>,
  superseded: (command: CommandLifecycle) => boolean,
): Readonly<ControlFeedback<number>> {
  const epoch = Number.isSafeInteger(session.epoch) && session.epoch >= 0 ? session.epoch : -1;
  // On the FTX-1 the poll feeds `manualNotchFreq` while `notchFilter` is
  // only written by `set_notch_filter`'s post-write readback, so the
  // confirmed echo must follow the SAME source selection the value side
  // reads — `manualNotchReading`'s choice, never a second one here. The
  // store-registered descriptor identity is untouched; this local variant
  // only repoints the state-backed confirmation field.
  const notchReading = field === 'notchFilter' ? manualNotchReading(state, caps) : null;
  const echoDescriptor = notchReading !== null && notchReading.source === 'manualNotchFreq'
    ? Object.freeze({
      ...descriptor,
      fieldPath: (scope: ControlFeedbackScope) => `${scope.receiver === 1 ? 'sub' : 'main'}.manualNotchFreq`,
      confirmed: () => Number.isSafeInteger(notchReading.notchFreq) ? notchReading.notchFreq : null,
    })
    : descriptor;
  const receiver: 0 | 1 = state?.active === 'SUB' ? 1 : 0;
  const scope = Object.freeze({
    control: field === 'nbLevel' ? 'nb-level'
      : field === 'nbWidth' ? 'nb-width'
        : field === 'nrLevel' ? 'nr-level'
          : field === 'nbDepth' ? 'nb-depth'
            : field === 'notchFilter' ? 'notch-position'
              : field === 'manualNotchWidth' ? 'manual-notch-width' : 'agc-time',
    receiver: field === 'nbWidth' || field === 'nbDepth' ? 0 as const : receiver,
  });
  const rawFeedback = projectControlFeedback(
    echoDescriptor, state, commands, scope, epoch, superseded,
  );
  // The pending target is the raw CAT code the command dispatched. Decode
  // it through the SAME resolved contract and the SAME source decision the
  // confirmed reading uses. A reading that fell back to the raw field (no
  // published domain, or the domain's field unusable) keeps the target raw.
  const feedback = notchReading?.source === 'manualNotchFreq'
    ? projectPendingTargetToDisplay(rawFeedback, (raw) =>
      resolveControlContract(caps, 'manual_notch_freq').rawToDisplay(raw))
    : rawFeedback;
  try {
    const view = toRadioViewModel(state, caps);
    if (session.state !== 'connected' || epoch < 0
      || view === null || view.activeReceiver.status !== 'known') {
      return unavailableControlFeedback(feedback);
    }
    const activeReceiver = view.activeReceiver.receiver;
    const receiverEntries = view.receiverIndicators?.filter(
      entry => entry.receiver === activeReceiver,
    ) ?? [];
    if (receiverEntries.length !== 1 || !receiverEntries[0].availability.operational) {
      return unavailableControlFeedback(feedback);
    }
    if (field === 'nbWidth' || field === 'nbDepth') {
      const observation = qualifyRadioDisplayObservation({
        state, caps, path: field, structural: caps?.controls?.nb_depth != null,
        value: state?.[field],
      });
      return hasUsableObservation(observation) && Number.isSafeInteger(observation.value)
        ? feedback : unavailableControlFeedback(feedback);
    }
    const receiverId = activeReceiver;
    const receiverState = receiverId === 'SUB' ? state?.sub : state?.main;
    const base = receiverId === 'SUB' ? 'sub' : 'main';
    const tags = Array.isArray(caps?.capabilities) ? caps.capabilities : [];
    const echoField = notchReading?.source === 'manualNotchFreq' ? 'manualNotchFreq' : field;
    const observation = qualifyDisplayObservation({
      state, caps, receiver: receiverId, path: `${base}.${echoField}`,
      structural: tags.includes(DSP_FEEDBACK_CAPABILITIES[field]),
      value: receiverState?.[echoField],
    });
    return hasUsableObservation(observation) && Number.isSafeInteger(observation.value)
      ? feedback : unavailableControlFeedback(feedback);
  } catch {
    return unavailableControlFeedback(feedback);
  }
}

export type RfSqlFeedbackLane = Readonly<{
  command: string;
  feedback: Readonly<ControlFeedback<number>>;
}>;

/** Pure two-lane RF/SQL decision; the wrapper supplies the live inputs and
 *  the store-registered RF-gain and squelch descriptors. */
export function rfSqlControlFeedback(
  session: ControlSessionFacts,
  state: ServerState | null,
  caps: Capabilities | null,
  commands: readonly CommandLifecycle[],
  rfGainDescriptor: StateBackedCommandDescriptor<number>,
  squelchDescriptor: StateBackedCommandDescriptor<number>,
  superseded: (command: CommandLifecycle) => boolean,
): Readonly<{ rf: RfSqlFeedbackLane; sql: RfSqlFeedbackLane }> | null {
  const sessionState = session.state;
  const sessionEpoch = session.epoch;
  const stateGeneration = state?.providerGeneration;
  if (sessionState !== 'connected'
    || !Number.isSafeInteger(sessionEpoch) || sessionEpoch < 0
    || state === null || caps === null
    || state.stateContractVersion !== 1 || caps.stateContractVersion !== 1
    || !Number.isSafeInteger(stateGeneration) || (stateGeneration as number) < 0
    || caps.providerGeneration !== stateGeneration) return null;

  const view = toRadioViewModel(state, caps);
  if (view === null || view.activeReceiver.status !== 'known') return null;
  const receiver = view.activeReceiver.receiver;
  const receiverEntries = view.receiverIndicators?.filter((entry) => entry.receiver === receiver) ?? [];
  if (receiverEntries.length !== 1 || !receiverEntries[0].availability.operational) return null;
  const receiverIndex: 0 | 1 = receiver === 'SUB' ? 1 : 0;
  const receiverState = receiver === 'SUB' ? state.sub : state.main;
  const base = receiver === 'SUB' ? 'sub' : 'main';
  const tags = Array.isArray(caps.capabilities) ? caps.capabilities : [];

  const lane = (
    command: 'set_rf_gain' | 'set_squelch',
    descriptor: StateBackedCommandDescriptor<number>,
    control: 'rf-gain' | 'squelch',
    leaf: 'rfGain' | 'squelch',
    structural: boolean,
  ): RfSqlFeedbackLane => {
    const scope = { control, receiver: receiverIndex } as const;
    const feedback = projectControlFeedback(
      descriptor, state, commands, scope, sessionEpoch, superseded,
    );
    const observation = qualifyDisplayObservation({
      state, caps, receiver, path: `${base}.${leaf}`, structural,
      value: receiverState?.[leaf],
    });
    const qualified = hasUsableObservation(observation) ? feedback : Object.freeze({
      ...feedback,
      confirmed: null, target: null, requestedTarget: null,
      phase: 'unavailable' as const, busy: false, availability: 'unavailable' as const,
      outcome: null, lifecycleId: null, transitionId: null,
    });
    return Object.freeze({ command, feedback: qualified });
  };

  return Object.freeze({
    rf: lane(
      'set_rf_gain', rfGainDescriptor, 'rf-gain', 'rfGain', tags.includes('rf_gain'),
    ),
    sql: lane(
      'set_squelch', squelchDescriptor, 'squelch', 'squelch', tags.includes('squelch'),
    ),
  });
}

/**
 * Shared qualification for a single raw receiver-scoped "echo" control:
 * PBT inner/outer, and the real `if_shift` command on a radio that has
 * one. Mirrors `dspControlFeedback`'s active-receiver/operational/
 * observation gating; `structuralFor` supplies the one thing that differs
 * per field (PBT needs both the `pbt` capability tag AND a usable
 * `pbt_inner` range, while `if_shift` needs only its own capability tag).
 */
export function receiverEchoControlFeedback(
  session: ControlSessionFacts,
  state: ServerState | null,
  caps: Capabilities | null,
  commands: readonly CommandLifecycle[],
  descriptor: StateBackedCommandDescriptor<number>,
  control: string,
  field: 'pbtInner' | 'pbtOuter' | 'ifShift',
  structuralFor: (caps: Capabilities | null | undefined, tags: readonly string[]) => boolean,
  superseded: (command: CommandLifecycle) => boolean,
): Readonly<ControlFeedback<number>> {
  const epoch = Number.isSafeInteger(session.epoch) && session.epoch >= 0 ? session.epoch : -1;
  const receiver: 0 | 1 = state?.active === 'SUB' ? 1 : 0;
  const scope = Object.freeze({ control, receiver });
  const feedback = projectControlFeedback(
    descriptor, state, commands, scope, epoch, superseded,
  );
  try {
    const view = toRadioViewModel(state, caps);
    if (session.state !== 'connected' || epoch < 0
      || view === null || view.activeReceiver.status !== 'known') {
      return unavailableControlFeedback(feedback);
    }
    const activeReceiver = view.activeReceiver.receiver;
    const receiverEntries = view.receiverIndicators?.filter(
      entry => entry.receiver === activeReceiver,
    ) ?? [];
    if (receiverEntries.length !== 1 || !receiverEntries[0].availability.operational) {
      return unavailableControlFeedback(feedback);
    }
    const receiverState = activeReceiver === 'SUB' ? state?.sub : state?.main;
    const base = activeReceiver === 'SUB' ? 'sub' : 'main';
    const tags = Array.isArray(caps?.capabilities) ? caps.capabilities : [];
    const observation = qualifyDisplayObservation({
      state, caps, receiver: activeReceiver, path: `${base}.${field}`,
      structural: structuralFor(caps, tags), value: receiverState?.[field],
    });
    return observation.state === 'current' && Number.isSafeInteger(observation.value)
      ? feedback : unavailableControlFeedback(feedback);
  } catch {
    return unavailableControlFeedback(feedback);
  }
}

export const pbtStructural = (caps: Capabilities | null | undefined, tags: readonly string[]): boolean =>
  tags.includes('pbt') && pbtRangeFromCaps(caps) !== undefined;

/**
 * The measured twin-PBT lattice for the ACTIVE receiver — the mode's
 * declared `pbtStepHz` at the observed filter width — or `null` when no
 * lattice forms (no step for the mode, unobserved width, legacy payload).
 * The same three reads `toFilterProps` and `getIfShiftControlFeedback`
 * base their conversions on.
 */
export function measuredPbtLatticeHz(
  state: ServerState | null, caps: Capabilities | null | undefined,
): Readonly<{ filterWidthHz: number; stepHz: number }> | null {
  const rx = state ? (state.active === 'SUB' ? state.sub : state.main) : undefined;
  const pbtStepHz = resolveFilterModeConfig(caps ?? null, rx?.mode ?? undefined, rx?.dataMode ?? undefined)?.pbtStepHz;
  const pbtWidthHz = rx?.filterWidth;
  return pbtStepHz !== undefined && typeof pbtWidthHz === 'number'
    ? { filterWidthHz: pbtWidthHz, stepHz: pbtStepHz } : null;
}

/**
 * Converts a raw-domain PBT feedback onto the measured lattice's Hz — the
 * unit the displayed value and the write path (`onPbtInnerChange` and
 * friends) already share. A feedback that is already unavailable, or a
 * mode with no lattice (FM), fails the whole projection closed; on the
 * lattice every non-null value of `confirmed`, `target` and
 * `requestedTarget` must convert, so one unconvertible raw fails the
 * whole feedback closed instead of mixing a raw target into an Hz echo.
 */
export function pbtFeedbackOnMeasuredLattice(
  raw: Readonly<ControlFeedback<number>>,
  lattice: Readonly<{ filterWidthHz: number; stepHz: number }> | null,
): Readonly<ControlFeedback<number>> {
  if (raw.availability !== 'available' || lattice === null) {
    return unavailableControlFeedback(raw);
  }
  return projectControlFeedbackToDisplay(
    raw, (value) => measuredPbtRawToHz(value, lattice.filterWidthHz, lattice.stepHz),
  );
}
