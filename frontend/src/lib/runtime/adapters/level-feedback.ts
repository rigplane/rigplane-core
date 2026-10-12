/**
 * Level-feedback decisions — the CW (pitch / keyer speed), TX-aux,
 * AF-level and RF-power control-feedback qualification split out of
 * `panel-adapters.ts`. Each function takes the live inputs its wrapper
 * in `panel-adapters.ts` reads and returns the qualified feedback; the
 * projection core and its single acknowledged environment read live in
 * `./control-feedback-projection`.
 */

import type {
  CommandLifecycle,
  StateBackedCommandDescriptor,
  TxAuxCommandFeedbackField,
} from '$lib/stores/commands.svelte';
import type { ServerState } from '$lib/types/state';
import type { Capabilities } from '$lib/types/capabilities';
import { toRadioViewModel } from './radio-view-model-adapter';
import { qualifyDisplayObservation, qualifyRadioDisplayObservation } from './display-observation';
import {
  hasUsableObservation,
  normalizedUsableObservation,
  projectControlFeedback,
  unavailableControlFeedback,
  type ControlFeedback,
} from './control-feedback-projection';

export type GlobalCwControl = 'cw-pitch' | 'keyer-speed';
export type GlobalCwField = 'cwPitch' | 'keySpeed';

/** Pure decision behind the radio-global CW pitch and keyer-speed accessors. */
export function projectGlobalCwControlFeedback(
  descriptor: StateBackedCommandDescriptor<number>,
  state: ServerState | null,
  caps: Capabilities | null,
  commands: readonly CommandLifecycle[],
  epoch: number,
  superseded: (command: CommandLifecycle) => boolean,
  connected: boolean,
  control: GlobalCwControl,
  field: GlobalCwField,
): Readonly<ControlFeedback<number>> {
  const scope = { control, receiver: 0 } as const;
  const feedback = projectControlFeedback(descriptor, state, commands, scope, epoch, superseded);
  try {
    const observation = qualifyRadioDisplayObservation({
      state, caps, path: field,
      structural: caps?.capabilities.includes('cw') === true,
      value: state?.[field],
    });
    return connected && epoch >= 0
      && hasUsableObservation(observation) && Number.isSafeInteger(observation.value)
      ? feedback : unavailableControlFeedback(feedback);
  } catch {
    return unavailableControlFeedback(feedback);
  }
}

export type TxAuxControlFeedbackField = TxAuxCommandFeedbackField;

const TX_AUX_FEEDBACK_CAPABILITIES: Readonly<Record<
  TxAuxControlFeedbackField, readonly string[]
>> = Object.freeze({
  micGain: Object.freeze(['tx']),
  driveGain: Object.freeze(['tx', 'drive_gain']),
  voxGain: Object.freeze(['tx', 'vox']),
  antiVoxGain: Object.freeze(['tx', 'vox']),
  voxDelay: Object.freeze(['tx', 'vox']),
  compressorLevel: Object.freeze(['tx', 'compressor']),
  monitorGain: Object.freeze(['tx', 'monitor']),
});

/** Pure decision behind the TX/VOX auxiliary-level accessors. */
export function projectTxAuxControlFeedback(
  descriptor: StateBackedCommandDescriptor<number>,
  state: ServerState | null,
  caps: Capabilities | null,
  commands: readonly CommandLifecycle[],
  epoch: number,
  superseded: (command: CommandLifecycle) => boolean,
  connected: boolean,
  field: TxAuxControlFeedbackField,
): Readonly<ControlFeedback<number>> {
  const scope = descriptor.scope({ params: { level: 0 } })!;
  const feedback = projectControlFeedback(descriptor, state, commands, scope, epoch, superseded);
  try {
    const tags = Array.isArray(caps?.capabilities) ? caps.capabilities : [];
    const structural = TX_AUX_FEEDBACK_CAPABILITIES[field].every(tag => tags.includes(tag));
    const observation = qualifyRadioDisplayObservation({
      state, caps, path: field, structural, value: state?.[field],
    });
    return connected && epoch >= 0
      && hasUsableObservation(observation) && Number.isSafeInteger(observation.value)
      ? feedback : unavailableControlFeedback(feedback);
  } catch {
    return unavailableControlFeedback(feedback);
  }
}

/** Pure decision behind the AF-level accessor; `receiver` defaults to the
 *  active receiver when none is named. */
export function projectAfLevelControlFeedback(
  descriptor: StateBackedCommandDescriptor<number>,
  state: ServerState | null,
  caps: Capabilities | null,
  commands: readonly CommandLifecycle[],
  epoch: number,
  superseded: (command: CommandLifecycle) => boolean,
  connected: boolean,
  receiver?: 'MAIN' | 'SUB',
): Readonly<ControlFeedback<number>> {
  const scopeOnSub = receiver === undefined ? state?.active === 'SUB' : receiver === 'SUB';
  const feedback = projectControlFeedback(
    descriptor, state, commands,
    { control: 'af-level', receiver: scopeOnSub ? 1 : 0 }, epoch,
    superseded,
  );
  try {
    const view = toRadioViewModel(state, caps);
    const laneReceiver = receiver ?? (view === null || view.activeReceiver.status !== 'known'
      ? null : view.activeReceiver.receiver);
    const receiverEntries = view?.receiverIndicators?.filter(
      entry => entry.receiver === laneReceiver,
    ) ?? [];
    const tags = Array.isArray(caps?.capabilities) ? caps.capabilities : [];
    const observation = qualifyDisplayObservation({
      state, caps, receiver: laneReceiver ?? 'MAIN',
      path: laneReceiver === 'SUB' ? 'sub.afLevel' : 'main.afLevel',
      structural: tags.includes('af_level'),
      value: (laneReceiver === 'SUB' ? state?.sub : state?.main)?.afLevel,
    });
    if (!connected || epoch < 0 || laneReceiver === null
      || receiverEntries.length !== 1 || !receiverEntries[0].availability.operational
      || normalizedUsableObservation(observation) === null) {
      return unavailableControlFeedback(feedback);
    }
    return feedback;
  } catch {
    return unavailableControlFeedback(feedback);
  }
}

/** Pure decision behind the RF-power accessor, on the normalized `powerLevel` scale. */
export function projectRfPowerControlFeedback(
  descriptor: StateBackedCommandDescriptor<number>,
  state: ServerState | null,
  caps: Capabilities | null,
  commands: readonly CommandLifecycle[],
  epoch: number,
  superseded: (command: CommandLifecycle) => boolean,
  connected: boolean,
): Readonly<ControlFeedback<number>> {
  const feedback = projectControlFeedback(
    descriptor, state, commands,
    { control: 'rf-power', receiver: 0 }, epoch, superseded,
  );
  try {
    const tags = Array.isArray(caps?.capabilities) ? caps.capabilities : [];
    const observation = qualifyRadioDisplayObservation({
      state, caps, path: 'powerLevel', structural: tags.includes('tx'),
      value: state?.powerLevel,
    });
    return connected && epoch >= 0
      && normalizedUsableObservation(observation) !== null
      ? feedback : unavailableControlFeedback(feedback);
  } catch {
    return unavailableControlFeedback(feedback);
  }
}
