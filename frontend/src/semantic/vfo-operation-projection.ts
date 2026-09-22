import type {
  ActiveRx,
  AtuStatus,
  BooleanFact,
  DualActionBlockViewModel,
  ReceiverId,
  TxAuxField,
} from './radio-view-model';
export type VfoOperationReceiver = Extract<ReceiverId, 'MAIN' | 'SUB'>;
export type VfoOperationIntent =
  | Readonly<{ kind: 'select-receiver'; receiver: VfoOperationReceiver }>
  | Readonly<{ kind: 'toggle-split' | 'toggle-dual-watch' }>
  | Readonly<{
      kind: 'equalize' | 'swap' | 'quick-split' | 'quick-dual-watch' | 'speak';
    }>
  | Readonly<{ kind: 'toggle-tuner' | 'toggle-vox' | 'toggle-dial-lock' }>;

export type VfoOperationAvailability = Readonly<{
  structural: boolean;
  operational: boolean;
  reason: string | undefined;
}>;

export type VfoOperationCallbacks = Readonly<{
  onToggleSplit: (() => void) | undefined; onToggleDualWatch: (() => void) | undefined;
  onSelectMainReceiver: (() => void) | undefined; onSelectSubReceiver: (() => void) | undefined;
  onEqualizeVfos: (() => void) | undefined; onSwapVfos: (() => void) | undefined;
  onQuickSplit: (() => void) | undefined; onQuickDualWatch: (() => void) | undefined;
  onSpeak: (() => void) | undefined;
}>;

export type VfoOperationReasonText = Readonly<{
  receiverUnavailable: string; identityUnknown: string;
  splitUnknown: string; dualWatchUnknown: string;
}>;

/** Radio-function facts for the MOR-2509 bridge keys: the SAME view-model
 *  fields the TX panel reads (`radioWideIndicators.atu`/`dialLock`,
 *  `txAux.vox`), so two control points can never disagree. `undefined`
 *  means the profile does not carry the fact at all — the key is not
 *  drawn, never rendered disabled. */
export type VfoRadioFunctionsInput = Readonly<{
  tuner: TxAuxField<AtuStatus> | undefined;
  vox: TxAuxField<boolean> | undefined;
  dialLock: TxAuxField<boolean> | undefined;
  onToggleTuner: (() => void) | undefined;
  onToggleVox: (() => void) | undefined;
  onToggleDialLock: (() => void) | undefined;
}>;

export type VfoOperationProjectionInput = Readonly<{
  hasVfoPair: boolean; hasDualReceiver: boolean; relativeIdentityUnknown: boolean;
  /** Capability gates (MOR-2509 review): a radio without the `split`
   *  (resp. `dual_watch`) capability renders no SPLIT (resp. DW) key at
   *  all — the caller reads them off the caps, the same one mechanism
   *  `hasDualReceiver` uses; defaults exist only on the capability-blind
   *  surface's own props. */
  hasSplit: boolean; hasDualWatch: boolean;
  activeReceiver: ActiveRx;
  split: BooleanFact; dualWatch: BooleanFact;
  actions: DualActionBlockViewModel | undefined;
  callbacks: VfoOperationCallbacks;
  reasons: VfoOperationReasonText;
  radioFunctions?: VfoRadioFunctionsInput;
}>;

export type VfoToggleOperation = Readonly<{
  kind: 'toggle';
  reading: BooleanFact;
  availability: VfoOperationAvailability;
}>;

export type VfoActionOperation = Readonly<{
  kind: 'action';
  availability: VfoOperationAvailability;
}>;

export type VfoReceiverOption = Readonly<{
  value: VfoOperationReceiver;
  availability: VfoOperationAvailability;
}>;

export type VfoReceiverChoiceOperation = Readonly<{
  kind: 'choice';
  reading: ActiveRx;
  availability: VfoOperationAvailability;
  options: readonly [VfoReceiverOption, VfoReceiverOption];
}>;

export type VfoRadioFunctionOperation = Readonly<{
  availability: VfoOperationAvailability;
  reading: BooleanFact | { status: 'known'; value: AtuStatus } | { status: 'unknown' };
}>;

export type VfoRadioFunctionsProjection = Readonly<{
  tuner: VfoRadioFunctionOperation;
  vox: VfoRadioFunctionOperation;
  dialLock: VfoRadioFunctionOperation;
}>;

export type VfoOperationProjection = Readonly<{
  split: VfoToggleOperation; dualWatch: VfoToggleOperation;
  activeReceiver: VfoReceiverChoiceOperation;
  equalize: VfoActionOperation; swap: VfoActionOperation;
  quickSplit: VfoActionOperation; quickDualWatch: VfoActionOperation;
  speak: VfoActionOperation;
  radioFunctions: VfoRadioFunctionsProjection;
  groupReason: string | undefined;
}>;

const availability = (
  structural: boolean,
  operational: boolean,
  reason: string | undefined,
): VfoOperationAvailability => ({ structural, operational, reason });

function fallbackActions(input: VfoOperationProjectionInput): DualActionBlockViewModel {
  const absent = { structural: false, operational: false };
  return {
    main: absent, sub: absent,
    equalize: { structural: input.hasVfoPair, operational: input.callbacks.onEqualizeVfos !== undefined },
    swap: { structural: input.hasVfoPair, operational: input.callbacks.onSwapVfos !== undefined },
    quickSplit: absent, quickDualWatch: absent, speak: absent,
  };
}

export function projectVfoOperations(
  input: VfoOperationProjectionInput,
): VfoOperationProjection {
  const actions = input.actions ?? fallbackActions(input);
  const admitted = (
    action: keyof DualActionBlockViewModel,
    callback: (() => void) | undefined,
    reason?: string,
  ): VfoActionOperation => ({ kind: 'action', availability: availability(
    actions[action].structural, actions[action].operational && callback !== undefined, reason,
  ) });
  const receiverOption = (
    value: VfoOperationReceiver,
    action: 'main' | 'sub',
    callback: (() => void) | undefined,
  ): VfoReceiverOption => ({
    value,
    availability: availability(
      actions[action].structural,
      actions[action].operational && callback !== undefined,
      actions[action].structural && !actions[action].operational
        ? input.reasons.receiverUnavailable
        : undefined,
    ),
  });
  const main = receiverOption('MAIN', 'main', input.callbacks.onSelectMainReceiver);
  const sub = receiverOption('SUB', 'sub', input.callbacks.onSelectSubReceiver);
  const functionOperation = (
    field: TxAuxField<AtuStatus> | TxAuxField<boolean> | undefined,
    callback: (() => void) | undefined,
  ): VfoRadioFunctionOperation => {
    const structural = field !== undefined && field.availability.structural;
    return {
      availability: availability(
        structural,
        structural && field!.availability.operational
          && field!.reading.status === 'known' && callback !== undefined,
        undefined,
      ),
      reading: field === undefined ? { status: 'unknown' } : field.reading,
    };
  };
  const functions = input.radioFunctions;
  const splitUnknown = input.split.status === 'unknown';
  const dualWatchUnknown = input.dualWatch.status === 'unknown';
  const quickSplitReason = input.relativeIdentityUnknown
    ? input.reasons.identityUnknown
    : splitUnknown ? input.reasons.splitUnknown : undefined;
  const quickDualWatchReason = input.relativeIdentityUnknown
    ? input.reasons.identityUnknown
    : dualWatchUnknown ? input.reasons.dualWatchUnknown : undefined;

  return {
    split: {
      kind: 'toggle',
      reading: input.split,
      availability: availability(
        input.hasSplit,
        input.hasSplit && !splitUnknown,
        splitUnknown ? input.reasons.splitUnknown : undefined,
      ),
    },
    dualWatch: {
      kind: 'toggle',
      reading: input.dualWatch,
      availability: availability(
        input.hasDualWatch && input.hasDualReceiver,
        input.hasDualWatch && input.hasDualReceiver && !dualWatchUnknown,
        dualWatchUnknown ? input.reasons.dualWatchUnknown : undefined,
      ),
    },
    activeReceiver: {
      kind: 'choice',
      reading: input.activeReceiver,
      availability: availability(
        main.availability.structural || sub.availability.structural,
        (main.availability.structural && main.availability.operational)
          || (sub.availability.structural && sub.availability.operational),
        undefined,
      ),
      options: [main, sub],
    },
    equalize: admitted('equalize', input.callbacks.onEqualizeVfos),
    swap: admitted('swap', input.callbacks.onSwapVfos),
    quickSplit: admitted(
      'quickSplit',
      input.relativeIdentityUnknown || splitUnknown ? undefined : input.callbacks.onQuickSplit,
      quickSplitReason,
    ),
    quickDualWatch: admitted(
      'quickDualWatch',
      input.relativeIdentityUnknown || dualWatchUnknown ? undefined : input.callbacks.onQuickDualWatch,
      quickDualWatchReason,
    ),
    speak: admitted('speak', input.callbacks.onSpeak),
    radioFunctions: {
      tuner: functionOperation(functions?.tuner, functions?.onToggleTuner),
      vox: functionOperation(functions?.vox, functions?.onToggleVox),
      dialLock: functionOperation(functions?.dialLock, functions?.onToggleDialLock),
    },
    groupReason: input.relativeIdentityUnknown ? input.reasons.identityUnknown : undefined,
  };
}

export function invokeVfoOperation(
  readCurrent: () => VfoOperationProjectionInput,
  intent: VfoOperationIntent,
): void {
  const input = readCurrent();
  const projected = projectVfoOperations(input);
  let current: VfoOperationAvailability;
  let callback: (() => void) | undefined;

  switch (intent.kind) {
    case 'select-receiver': {
      const index = intent.receiver === 'MAIN' ? 0 : 1;
      current = projected.activeReceiver.options[index].availability;
      callback = intent.receiver === 'MAIN'
        ? input.callbacks.onSelectMainReceiver
        : input.callbacks.onSelectSubReceiver;
      break;
    }
    case 'toggle-split':
      current = projected.split.availability;
      callback = input.callbacks.onToggleSplit;
      break;
    case 'toggle-dual-watch':
      current = projected.dualWatch.availability;
      callback = input.callbacks.onToggleDualWatch;
      break;
    case 'equalize':
      current = projected.equalize.availability;
      callback = input.callbacks.onEqualizeVfos;
      break;
    case 'swap':
      current = projected.swap.availability;
      callback = input.callbacks.onSwapVfos;
      break;
    case 'quick-split':
      current = projected.quickSplit.availability;
      callback = input.callbacks.onQuickSplit;
      break;
    case 'quick-dual-watch':
      current = projected.quickDualWatch.availability;
      callback = input.callbacks.onQuickDualWatch;
      break;
    case 'speak':
      current = projected.speak.availability;
      callback = input.callbacks.onSpeak;
      break;
    case 'toggle-tuner':
      current = projected.radioFunctions.tuner.availability;
      callback = input.radioFunctions?.onToggleTuner;
      break;
    case 'toggle-vox':
      current = projected.radioFunctions.vox.availability;
      callback = input.radioFunctions?.onToggleVox;
      break;
    case 'toggle-dial-lock':
      current = projected.radioFunctions.dialLock.availability;
      callback = input.radioFunctions?.onToggleDialLock;
      break;
  }

  if (current.structural && current.operational) callback?.();
}
