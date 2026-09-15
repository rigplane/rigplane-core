import {
  createFrequencyInteraction,
  createFrequencyInteractionLease,
  type FrequencyInteractionLease,
} from './frequency-interaction.svelte';
import {
  projectFrequencyReadout,
  type FrequencyReadoutModel,
} from './frequency-readout';

export interface FrequencyInstrumentInput {
  readonly confirmedHz: number | null;
  readonly displayHz?: number | null;
  readonly pendingDisplayHz?: number | null;
  readonly pendingAnnouncement?: string;
  readonly disabled: boolean;
  readonly context: object | null;
  readonly receiver: 'main' | 'sub';
  readonly minFreq: number;
  readonly maxFreq: number;
  readonly onFreqChange?: (frequencyHz: number) => void;
}

export interface FrequencyInstrumentBinding {
  readonly context: object | null;
  readonly model: FrequencyReadoutModel;
  attachRenderer(isCurrent: () => boolean): FrequencyInteractionLease;
}

export function createFrequencyInstrumentBinding(
  current: FrequencyInstrumentInput,
): FrequencyInstrumentBinding {
  const model = $derived.by(() => projectFrequencyReadout({
    confirmedHz: current.confirmedHz,
    displayHz: current.displayHz,
    pendingDisplayHz: current.pendingDisplayHz,
    pendingAnnouncement: current.pendingAnnouncement,
  }));
  const interaction = createFrequencyInteraction({
    get confirmedHz() { return current.confirmedHz; },
    get digits() { return model.digits; },
    get disabled() { return current.disabled; },
    get receiver() { return current.receiver; },
    get minFreq() { return current.minFreq; },
    get maxFreq() { return current.maxFreq; },
    get onFreqChange() { return current.onFreqChange; },
  });

  return {
    get context() { return current.context; },
    get model() { return model; },
    attachRenderer(isCurrent) {
      const context = current.context;
      return createFrequencyInteractionLease(
        interaction,
        () => context !== null && Object.is(current.context, context) && isCurrent(),
      );
    },
  };
}
