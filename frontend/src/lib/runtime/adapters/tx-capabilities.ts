import type { Capabilities } from '$lib/types/capabilities';
import type { KnownTxTargetPublic, UnknownTxTargetPublic } from '$lib/types/state';
import { getFrequencyPermit, type FrequencyPermit } from '$lib/utils/tx-permit';

type TxTarget = KnownTxTargetPublic | UnknownTxTargetPublic;
export type ModInputSource =
  | { status: 'known'; source: number }
  | { status: 'unknown' };
export type ModInputReadiness =
  | { status: 'not-applicable' }
  | { status: 'ready'; source: number }
  | { status: 'mismatch'; source: number }
  | { status: 'unknown' };
export interface TxCapabilityInput {
  txTarget: TxTarget;
  modInputSource: ModInputSource;
}
export interface TxCapabilityFacts {
  catPttAvailable: boolean;
  browserTxAudioAvailable: boolean;
  nativeVoiceTxAvailable: boolean;
  modInputRoutingAvailable: boolean;
  modInputReadiness: ModInputReadiness;
  txTarget: TxTarget;
  frequencyPermit: FrequencyPermit;
}

function normalizeTarget(caps: Capabilities, target: TxTarget): TxTarget {
  if (target.status === 'unknown') return { ...target };
  const receiver = target.receiver;
  const slot = target.slot;
  const valid = caps.vfoScheme === 'single'
    ? receiver === 'MAIN' && slot === null
    : caps.vfoScheme === 'ab'
      ? receiver === 'MAIN' && (slot === 'A' || slot === 'B')
      : caps.vfoScheme === 'ab_shared'
        ? slot === null && (receiver === 'MAIN' || receiver === 'SUB')
        : caps.vfoScheme === 'main_sub'
          && (receiver === 'MAIN' || receiver === 'SUB')
          && (slot === 'A' || slot === 'B');
  return valid ? { ...target } : { status: 'unknown', reason: 'contradiction' };
}

export function deriveTxCapabilities(
  caps: Capabilities,
  input: TxCapabilityInput,
): TxCapabilityFacts {
  const tags = new Set(caps.capabilities);
  const catPttAvailable = caps.tx === true && tags.has('tx');
  const modInputRoutingAvailable = tags.has('mod_input_routing');
  const requiredSource = caps.audioTxRequiredModInputSource;
  let modInputReadiness: ModInputReadiness;
  if (!modInputRoutingAvailable || requiredSource == null) {
    modInputReadiness = { status: 'not-applicable' };
  } else if (input.modInputSource.status === 'unknown') {
    modInputReadiness = { status: 'unknown' };
  } else if (input.modInputSource.source === requiredSource) {
    modInputReadiness = { status: 'ready', source: input.modInputSource.source };
  } else {
    modInputReadiness = { status: 'mismatch', source: input.modInputSource.source };
  }
  const txTarget = normalizeTarget(caps, input.txTarget);
  return {
    catPttAvailable,
    browserTxAudioAvailable: caps.audioTx === true && catPttAvailable,
    nativeVoiceTxAvailable: tags.has('voice_tx'),
    modInputRoutingAvailable,
    modInputReadiness,
    txTarget,
    frequencyPermit: getFrequencyPermit(
      txTarget.status === 'known' ? txTarget.frequencyHz : null,
      caps.txBands,
    ),
  };
}
