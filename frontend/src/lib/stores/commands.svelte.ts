import { getRadioState, subscribeRadioState } from './radio.svelte';
import type { ServerState } from '../types/state';
import type { RadioIntentName } from '../runtime/commands/radio-intents';

export type CommandLifecycleStatus = 'pending' | 'acknowledged' | 'confirmed' | 'failed' | 'cancelled' | 'timed-out';
export interface CommandLifecycle {
  id: string; name: string; params: Readonly<Record<string, unknown>>;
  originalEpoch: number; eventEpoch?: number; createdAt: number; updatedAt: number;
  timeoutMs: number; status: CommandLifecycleStatus; error?: string;
  /** Actual provider at submission; absent/null legacy records fail closed. */
  providerGeneration?: number | null;
  /**
   * The radio-observed `observationSeq` (MOR-1488 review R2) at the instant
   * this command transitioned to 'acknowledged', or `undefined` if no radio
   * state had been observed yet. `observationSeq` is the one counter that
   * increments on every applied state push regardless of whether any field's
   * value actually changed (`core.state_store._apply_one` bumps it
   * unconditionally, before the semantic-change check that gates
   * `stateRevision`) — the "did a fresh poll cycle happen since ack" signal
   * `panel-adapters.ts`'s `latestPendingParam` needs. `stateRevision` would
   * NOT serve this: a poll that re-confirms an unchanged value never bumps
   * it, which is exactly the case a fast double-toggle produces (the
   * superseded command's target coincides with the pre-existing confirmed
   * value) and would leave the sequence guard permanently unable to fire.
   */
  ackObservationSeq?: number;
  /** Bounded correlation markers; absent legacy records must fail closed. */
  ackFieldObservationTimes?: Readonly<Record<string, number>>;
  /** Exact transport handoff evidence; absence preserves legacy records. */
  dispatchedEventEpoch?: number;
  /** Real backend retention evidence, stored on this reactive lifecycle record. */
  hold?: Readonly<CommandLifecycleHold>;
  /** Permanent latest-target eligibility marker until this record retires. */
  locallyObsolete?: true;
  /** A backend terminal outcome, distinct from local latest-target hiding. */
  terminalOutcome?: 'superseded';
}
export interface BeginCommandInput {
  id: string; name: string; params: Readonly<Record<string, unknown>>;
  originalEpoch: number; timeoutMs?: number;
}
export interface CommandLifecycleHold {
  readonly commandId: string; readonly originalEpoch: number; readonly eventEpoch: number;
  readonly kind: 'held'; readonly reason: 'tx_active'; readonly expiresAt: number;
}
export interface CommandLifecycleProjection {
  readonly commandId: string; readonly originalEpoch: number; readonly eventEpoch: number;
  readonly kind: 'held' | 'superseded' | 'timed-out' | 'failed';
  readonly reason?: string; readonly expiresAt?: number; readonly error?: string;
}

export interface ControlFeedbackScope {
  readonly control: string;
  readonly receiver: 0 | 1;
  readonly slot?: string;
}
export type StateBackedRepeatPolicy = 'latest-target-wins';
export interface StateBackedCommandDescriptor<T> {
  readonly intentName: RadioIntentName;
  readonly repeatPolicy: StateBackedRepeatPolicy;
  /** Keep awaiting radio truth when a post-ack observation reports another value. */
  readonly requireTargetMatch?: boolean;
  scope(command: Pick<CommandLifecycle, 'params'>): ControlFeedbackScope | null;
  fieldPath(scope: ControlFeedbackScope): string;
  target(command: Pick<CommandLifecycle, 'params'>): T | null;
  confirmed(state: ServerState, scope: ControlFeedbackScope): T | null;
  matches(confirmed: T, target: T): boolean;
}

export type FilterWidthFieldPath = 'main.filterWidth' | 'sub.filterWidth';
const finiteNumber = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;
const safeInteger = (value: unknown): number | null =>
  typeof value === 'number' && Number.isSafeInteger(value) ? value : null;
const providerGeneration = (value: unknown): number | null =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : null;

function exactSafeIntegerTarget(
  command: Pick<CommandLifecycle, 'params'>, key: string,
): number | null {
  try {
    const params = command.params;
    return Reflect.ownKeys(params).length === 1
      && Object.prototype.hasOwnProperty.call(params, key)
      ? safeInteger(params[key]) : null;
  } catch {
    return null;
  }
}

type ExactReceiverSafeIntegerCommand = Readonly<{ receiver: 0 | 1; target: number }>;
function exactReceiverSafeIntegerCommand(
  command: Pick<CommandLifecycle, 'params'>, key: string,
): ExactReceiverSafeIntegerCommand | null {
  try {
    const params = command.params;
    if (typeof params !== 'object' || params === null) return null;
    const keys = Reflect.ownKeys(params);
    if (keys.length !== 2
      || !Object.prototype.hasOwnProperty.call(params, key)
      || !Object.prototype.hasOwnProperty.call(params, 'receiver')) return null;
    const target = safeInteger(params[key]);
    const receiver = params.receiver;
    return target !== null && (receiver === 0 || receiver === 1)
      ? Object.freeze({ receiver, target }) : null;
  } catch {
    return null;
  }
}

type NormalizedLevelCommand = Readonly<{ receiver: 0 | 1; target: number }>;
function normalizedLevelCommand(
  command: Pick<CommandLifecycle, 'params'>,
): NormalizedLevelCommand | null {
  try {
    const params = command.params;
    if (typeof params !== 'object' || params === null) return null;
    const keys = Reflect.ownKeys(params);
    if (keys.length !== 2
      || !Object.prototype.hasOwnProperty.call(params, 'level')
      || !Object.prototype.hasOwnProperty.call(params, 'receiver')) return null;
    const level = params.level;
    const receiver = params.receiver;
    return typeof level === 'number' && Number.isSafeInteger(level) && level >= 0 && level <= 255
      && (receiver === 0 || receiver === 1)
      ? Object.freeze({ receiver, target: level / 255 }) : null;
  } catch {
    return null;
  }
}

export const FILTER_WIDTH_COMMAND_DESCRIPTOR: StateBackedCommandDescriptor<number> = Object.freeze({
  intentName: 'set_filter_width', repeatPolicy: 'latest-target-wins',
  scope: (command: Pick<CommandLifecycle, 'params'>) => {
    const receiver = command.params.receiver;
    return receiver === undefined || receiver === 0 || receiver === 1
      ? Object.freeze({ control: 'filter-width', receiver: receiver === 1 ? 1 : 0 }) : null;
  },
  fieldPath: (scope: ControlFeedbackScope): FilterWidthFieldPath =>
    scope.receiver === 1 ? 'sub.filterWidth' : 'main.filterWidth',
  target: (command: Pick<CommandLifecycle, 'params'>) => safeInteger(command.params.width),
  confirmed: (state: ServerState, scope: ControlFeedbackScope) => finiteNumber(
    (scope.receiver === 1 ? state.sub : state.main)?.filterWidth,
  ),
  matches: (confirmed: number, target: number) => confirmed === target,
});

type DirectVfoFrequencyCommand = Readonly<{
  receiver: 0 | 1;
  slot: 'A' | 'B';
  target: number;
}>;
function directVfoFrequencyCommand(
  command: Pick<CommandLifecycle, 'params'>,
): DirectVfoFrequencyCommand | null {
  const params = command.params;
  if (Reflect.ownKeys(params).length !== 5) return null;
  const target = safeInteger(params.freq);
  const receiver = params.receiver;
  const slot = params.slot;
  const expected = params.expected_active_slot;
  const generation = providerGeneration(params.provider_generation);
  return target !== null && target > 0 && (receiver === 0 || receiver === 1)
    && (slot === 'A' || slot === 'B') && (expected === 'A' || expected === 'B')
    && generation !== null
    ? Object.freeze({ receiver, slot, target }) : null;
}

export const DIRECT_VFO_FREQUENCY_COMMAND_DESCRIPTOR: StateBackedCommandDescriptor<number> = Object.freeze({
  intentName: 'set_vfo_freq', repeatPolicy: 'latest-target-wins', requireTargetMatch: true,
  scope: (command) => {
    const parsed = directVfoFrequencyCommand(command);
    return parsed === null ? null : Object.freeze({
      control: 'vfo-frequency', receiver: parsed.receiver, slot: parsed.slot,
    });
  },
  fieldPath: (scope) => `${scope.receiver === 1 ? 'sub' : 'main'}.vfo${scope.slot}.freqHz`,
  target: (command) => directVfoFrequencyCommand(command)?.target ?? null,
  confirmed: (state, scope) => safeInteger(
    (scope.receiver === 1 ? state.sub : state.main)?.[scope.slot === 'B' ? 'vfoB' : 'vfoA']?.freqHz,
  ),
  matches: (confirmed, target) => confirmed === target,
} satisfies StateBackedCommandDescriptor<number>);

const breakInDelayTarget = (command: Pick<CommandLifecycle, 'params'>): number | null =>
  Reflect.ownKeys(command.params).length === 1
    && Object.prototype.hasOwnProperty.call(command.params, 'level')
    ? safeInteger(command.params.level) : null;

export const BREAK_IN_DELAY_COMMAND_DESCRIPTOR: StateBackedCommandDescriptor<number> = Object.freeze({
  intentName: 'set_break_in_delay', repeatPolicy: 'latest-target-wins',
  scope: (command: Pick<CommandLifecycle, 'params'>) => breakInDelayTarget(command) === null
    ? null : Object.freeze({ control: 'break-in-delay', receiver: 0 }),
  fieldPath: () => 'breakInDelay',
  target: breakInDelayTarget,
  confirmed: (state: ServerState) => safeInteger(state.breakInDelay),
  matches: (confirmed: number, target: number) => confirmed === target,
});

export const RF_GAIN_COMMAND_DESCRIPTOR: StateBackedCommandDescriptor<number> = Object.freeze({
  intentName: 'set_rf_gain', repeatPolicy: 'latest-target-wins',
  scope: (command: Pick<CommandLifecycle, 'params'>) => {
    const parsed = normalizedLevelCommand(command);
    return parsed === null ? null : Object.freeze({ control: 'rf-gain', receiver: parsed.receiver });
  },
  fieldPath: (scope: ControlFeedbackScope) => scope.receiver === 1 ? 'sub.rfGain' : 'main.rfGain',
  target: (command: Pick<CommandLifecycle, 'params'>) => normalizedLevelCommand(command)?.target ?? null,
  confirmed: (state: ServerState, scope: ControlFeedbackScope) => {
    const value = finiteNumber((scope.receiver === 1 ? state.sub : state.main)?.rfGain);
    return value !== null && value >= 0 && value <= 1 ? value : null;
  },
  matches: (confirmed: number, target: number) => confirmed === target,
});

export const SQUELCH_COMMAND_DESCRIPTOR: StateBackedCommandDescriptor<number> = Object.freeze({
  intentName: 'set_squelch', repeatPolicy: 'latest-target-wins',
  scope: (command: Pick<CommandLifecycle, 'params'>) => {
    const parsed = normalizedLevelCommand(command);
    return parsed === null ? null : Object.freeze({ control: 'squelch', receiver: parsed.receiver });
  },
  fieldPath: (scope: ControlFeedbackScope) => scope.receiver === 1 ? 'sub.squelch' : 'main.squelch',
  target: (command: Pick<CommandLifecycle, 'params'>) => normalizedLevelCommand(command)?.target ?? null,
  confirmed: (state: ServerState, scope: ControlFeedbackScope) => {
    const value = finiteNumber((scope.receiver === 1 ? state.sub : state.main)?.squelch);
    return value !== null && value >= 0 && value <= 1 ? value : null;
  },
  matches: (confirmed: number, target: number) => confirmed === target,
});

const cwPitchTarget = (command: Pick<CommandLifecycle, 'params'>): number | null =>
  exactSafeIntegerTarget(command, 'value');
export const CW_PITCH_COMMAND_DESCRIPTOR: StateBackedCommandDescriptor<number> = Object.freeze({
  intentName: 'set_cw_pitch', repeatPolicy: 'latest-target-wins',
  scope: (command: Pick<CommandLifecycle, 'params'>) => cwPitchTarget(command) === null
    ? null : Object.freeze({ control: 'cw-pitch', receiver: 0 }),
  fieldPath: () => 'cwPitch',
  target: cwPitchTarget,
  confirmed: (state: ServerState) => safeInteger(state.cwPitch),
  matches: (confirmed: number, target: number) => confirmed === target,
});

const keySpeedTarget = (command: Pick<CommandLifecycle, 'params'>): number | null =>
  exactSafeIntegerTarget(command, 'speed');
export const KEY_SPEED_COMMAND_DESCRIPTOR: StateBackedCommandDescriptor<number> = Object.freeze({
  intentName: 'set_key_speed', repeatPolicy: 'latest-target-wins',
  scope: (command: Pick<CommandLifecycle, 'params'>) => keySpeedTarget(command) === null
    ? null : Object.freeze({ control: 'keyer-speed', receiver: 0 }),
  fieldPath: () => 'keySpeed',
  target: keySpeedTarget,
  confirmed: (state: ServerState) => safeInteger(state.keySpeed),
  matches: (confirmed: number, target: number) => confirmed === target,
});

export type TxAuxCommandFeedbackField =
  | 'micGain' | 'driveGain' | 'voxGain' | 'antiVoxGain' | 'voxDelay'
  | 'compressorLevel' | 'monitorGain';
type TxAuxCommandFeedbackIntent =
  | 'set_mic_gain' | 'set_drive_gain' | 'set_vox_gain' | 'set_anti_vox_gain'
  | 'set_vox_delay' | 'set_compressor_level' | 'set_monitor_gain';

function rawTxAuxCommandDescriptor(
  intentName: TxAuxCommandFeedbackIntent,
  control: string,
  field: TxAuxCommandFeedbackField,
): StateBackedCommandDescriptor<number> {
  const target = (command: Pick<CommandLifecycle, 'params'>): number | null =>
    exactSafeIntegerTarget(command, 'level');
  return Object.freeze({
    intentName, repeatPolicy: 'latest-target-wins' as const,
    scope: (command: Pick<CommandLifecycle, 'params'>) => target(command) === null
      ? null : Object.freeze({ control, receiver: 0 as const }),
    fieldPath: () => field,
    target,
    confirmed: (state: ServerState) => safeInteger(state[field]),
    matches: (confirmed: number, requested: number) => confirmed === requested,
  });
}

export const TX_AUX_COMMAND_DESCRIPTORS = Object.freeze({
  micGain: rawTxAuxCommandDescriptor('set_mic_gain', 'mic-gain', 'micGain'),
  driveGain: rawTxAuxCommandDescriptor('set_drive_gain', 'drive-gain', 'driveGain'),
  voxGain: rawTxAuxCommandDescriptor('set_vox_gain', 'vox-gain', 'voxGain'),
  antiVoxGain: rawTxAuxCommandDescriptor('set_anti_vox_gain', 'anti-vox-gain', 'antiVoxGain'),
  voxDelay: rawTxAuxCommandDescriptor('set_vox_delay', 'vox-delay', 'voxDelay'),
  compressorLevel: rawTxAuxCommandDescriptor(
    'set_compressor_level', 'compressor-level', 'compressorLevel',
  ),
  monitorGain: rawTxAuxCommandDescriptor('set_monitor_gain', 'monitor-level', 'monitorGain'),
}) satisfies Readonly<Record<TxAuxCommandFeedbackField, StateBackedCommandDescriptor<number>>>;

export type DspCommandFeedbackField =
  | 'nbLevel' | 'nbWidth' | 'nrLevel' | 'nbDepth'
  | 'notchFilter' | 'manualNotchWidth' | 'agcTimeConstant';
type ReceiverDspCommandFeedbackField = Exclude<DspCommandFeedbackField, 'nbWidth' | 'nbDepth'>;
type ReceiverDspCommandFeedbackIntent =
  | 'set_nb_level' | 'set_nr_level'
  | 'set_notch_filter' | 'set_manual_notch_width' | 'set_agc_time_constant';

function rawReceiverDspCommandDescriptor(
  intentName: ReceiverDspCommandFeedbackIntent,
  control: string,
  field: ReceiverDspCommandFeedbackField,
  param: 'level' | 'value',
): StateBackedCommandDescriptor<number> {
  const parsed = (command: Pick<CommandLifecycle, 'params'>) =>
    exactReceiverSafeIntegerCommand(command, param);
  return Object.freeze({
    intentName, repeatPolicy: 'latest-target-wins' as const,
    scope: (command: Pick<CommandLifecycle, 'params'>) => {
      const value = parsed(command);
      return value === null ? null : Object.freeze({ control, receiver: value.receiver });
    },
    fieldPath: (scope: ControlFeedbackScope) => `${scope.receiver === 1 ? 'sub' : 'main'}.${field}`,
    target: (command: Pick<CommandLifecycle, 'params'>) => parsed(command)?.target ?? null,
    confirmed: (state: ServerState, scope: ControlFeedbackScope) =>
      safeInteger((scope.receiver === 1 ? state.sub : state.main)?.[field]),
    matches: (confirmed: number, requested: number) => confirmed === requested,
  });
}

const nbWidthTarget = (command: Pick<CommandLifecycle, 'params'>): number | null =>
  exactSafeIntegerTarget(command, 'level');
const NB_WIDTH_COMMAND_DESCRIPTOR: StateBackedCommandDescriptor<number> = Object.freeze({
  intentName: 'set_nb_width', repeatPolicy: 'latest-target-wins',
  scope: (command: Pick<CommandLifecycle, 'params'>) => nbWidthTarget(command) === null
    ? null : Object.freeze({ control: 'nb-width', receiver: 0 }),
  fieldPath: () => 'nbWidth',
  target: nbWidthTarget,
  confirmed: (state: ServerState) => safeInteger(state.nbWidth),
  matches: (confirmed: number, requested: number) => confirmed === requested,
});

const nbDepthTarget = (command: Pick<CommandLifecycle, 'params'>): number | null =>
  exactSafeIntegerTarget(command, 'level');
const NB_DEPTH_COMMAND_DESCRIPTOR: StateBackedCommandDescriptor<number> = Object.freeze({
  intentName: 'set_nb_depth', repeatPolicy: 'latest-target-wins',
  scope: (command: Pick<CommandLifecycle, 'params'>) => nbDepthTarget(command) === null
    ? null : Object.freeze({ control: 'nb-depth', receiver: 0 }),
  fieldPath: () => 'nbDepth',
  target: nbDepthTarget,
  confirmed: (state: ServerState) => safeInteger(state.nbDepth),
  matches: (confirmed: number, requested: number) => confirmed === requested,
});

export const DSP_COMMAND_DESCRIPTORS = Object.freeze({
  nbLevel: rawReceiverDspCommandDescriptor('set_nb_level', 'nb-level', 'nbLevel', 'level'),
  nbWidth: NB_WIDTH_COMMAND_DESCRIPTOR,
  nrLevel: rawReceiverDspCommandDescriptor('set_nr_level', 'nr-level', 'nrLevel', 'level'),
  nbDepth: NB_DEPTH_COMMAND_DESCRIPTOR,
  notchFilter: rawReceiverDspCommandDescriptor(
    'set_notch_filter', 'notch-position', 'notchFilter', 'value',
  ),
  manualNotchWidth: rawReceiverDspCommandDescriptor(
    'set_manual_notch_width', 'manual-notch-width', 'manualNotchWidth', 'value',
  ),
  agcTimeConstant: rawReceiverDspCommandDescriptor(
    'set_agc_time_constant', 'agc-time', 'agcTimeConstant', 'value',
  ),
}) satisfies Readonly<Record<DspCommandFeedbackField, StateBackedCommandDescriptor<number>>>;

type RawReceiverEchoField = 'pbtInner' | 'pbtOuter' | 'ifShift';
type RawReceiverEchoParam = 'value' | 'offset';
type RawReceiverEchoIntent = 'set_pbt_inner' | 'set_pbt_outer' | 'set_if_shift';

/**
 * Mirrors `FILTER_WIDTH_COMMAND_DESCRIPTOR`'s exact-raw-equality shape
 * (MOR-2425 census `pbt-descriptor-lane-census-20260907.md` §A/§F4): receiver
 * defaults to 0 when omitted, `target` reads the named param directly (both
 * `set_pbt_inner`/`set_pbt_outer`'s `value` and `set_if_shift`'s `offset` are
 * already raw at dispatch — `panel-commands.ts`'s `onPbtInnerChange`/
 * `onPbtOuterChange` call `pbtHzToRaw` before `dispatchRadioIntent`, and the
 * FTX-1 `if_shift` command is identity-mapped Hz), and `confirmed` reads the
 * SAME raw `ServerState` field the command's own CI-V/CAT echo populates —
 * never the Hz-converted `filterPassband.*` view-model. Factors PBT inner,
 * PBT outer, and IF-shift into one helper instead of three near-identical
 * blocks, the same shape `rawReceiverDspCommandDescriptor` above uses for the
 * DSP family.
 */
function rawReceiverEchoCommandDescriptor(
  intentName: RawReceiverEchoIntent,
  control: string,
  field: RawReceiverEchoField,
  param: RawReceiverEchoParam,
): StateBackedCommandDescriptor<number> {
  return Object.freeze({
    intentName, repeatPolicy: 'latest-target-wins' as const,
    scope: (command: Pick<CommandLifecycle, 'params'>) => {
      const receiver = command.params.receiver;
      return receiver === undefined || receiver === 0 || receiver === 1
        ? Object.freeze({ control, receiver: receiver === 1 ? 1 : 0 }) : null;
    },
    fieldPath: (scope: ControlFeedbackScope) => `${scope.receiver === 1 ? 'sub' : 'main'}.${field}`,
    target: (command: Pick<CommandLifecycle, 'params'>) => safeInteger(command.params[param]),
    confirmed: (state: ServerState, scope: ControlFeedbackScope) =>
      finiteNumber((scope.receiver === 1 ? state.sub : state.main)?.[field]),
    matches: (confirmed: number, target: number) => confirmed === target,
  });
}

export const PBT_INNER_COMMAND_DESCRIPTOR: StateBackedCommandDescriptor<number> =
  rawReceiverEchoCommandDescriptor('set_pbt_inner', 'pbt-inner', 'pbtInner', 'value');
export const PBT_OUTER_COMMAND_DESCRIPTOR: StateBackedCommandDescriptor<number> =
  rawReceiverEchoCommandDescriptor('set_pbt_outer', 'pbt-outer', 'pbtOuter', 'value');
/**
 * Only meaningful where `if_shift` is a real command (Yaesu FTX-1) — Icom
 * radios never dispatch `set_if_shift` (`onIfShiftChange`'s PBT-only branch
 * dispatches `set_pbt_inner`/`set_pbt_outer` instead), so this descriptor
 * simply never engages there. `panel-adapters.ts`'s `getIfShiftControlFeedback`
 * derives a PBT-only radio's IF-shift feedback from the two PBT descriptors
 * instead of using this one.
 */
export const IF_SHIFT_COMMAND_DESCRIPTOR: StateBackedCommandDescriptor<number> =
  rawReceiverEchoCommandDescriptor('set_if_shift', 'if-shift', 'ifShift', 'offset');

export const STATE_BACKED_COMMAND_DESCRIPTORS: ReadonlyMap<RadioIntentName, StateBackedCommandDescriptor<unknown>> =
  new Map([
    [FILTER_WIDTH_COMMAND_DESCRIPTOR.intentName, FILTER_WIDTH_COMMAND_DESCRIPTOR],
    [DIRECT_VFO_FREQUENCY_COMMAND_DESCRIPTOR.intentName, DIRECT_VFO_FREQUENCY_COMMAND_DESCRIPTOR],
    [BREAK_IN_DELAY_COMMAND_DESCRIPTOR.intentName, BREAK_IN_DELAY_COMMAND_DESCRIPTOR],
    [RF_GAIN_COMMAND_DESCRIPTOR.intentName, RF_GAIN_COMMAND_DESCRIPTOR],
    [SQUELCH_COMMAND_DESCRIPTOR.intentName, SQUELCH_COMMAND_DESCRIPTOR],
    [CW_PITCH_COMMAND_DESCRIPTOR.intentName, CW_PITCH_COMMAND_DESCRIPTOR],
    [KEY_SPEED_COMMAND_DESCRIPTOR.intentName, KEY_SPEED_COMMAND_DESCRIPTOR],
    ...Object.values(TX_AUX_COMMAND_DESCRIPTORS).map(
      descriptor => [descriptor.intentName, descriptor] as const,
    ),
    ...Object.values(DSP_COMMAND_DESCRIPTORS).map(
      descriptor => [descriptor.intentName, descriptor] as const,
    ),
    [PBT_INNER_COMMAND_DESCRIPTOR.intentName, PBT_INNER_COMMAND_DESCRIPTOR],
    [PBT_OUTER_COMMAND_DESCRIPTOR.intentName, PBT_OUTER_COMMAND_DESCRIPTOR],
    [IF_SHIFT_COMMAND_DESCRIPTOR.intentName, IF_SHIFT_COMMAND_DESCRIPTOR],
  ]);
export const getStateBackedCommandDescriptor = (intentName: string): StateBackedCommandDescriptor<unknown> | undefined =>
  (STATE_BACKED_COMMAND_DESCRIPTORS as ReadonlyMap<string, StateBackedCommandDescriptor<unknown>>).get(intentName);

/**
 * The `ServerState` receiver field each pending-marker intent is confirmed
 * against — the same `confirmedField` `panel-adapters.ts`'s pending
 * accessors pass to `latestPendingParam` for that intent. Used here only to
 * capture that field's ack-time observation marker; an intent absent from
 * this table simply captures no marker.
 */
const PENDING_CONFIRM_FIELDS: Readonly<Record<string, keyof ServerState['main']>> = Object.freeze({
  set_freq: 'freqHz',
  set_mode: 'mode',
  set_filter: 'filter',
  set_agc: 'agc',
  set_preamp: 'preamp',
  set_attenuator: 'att',
  set_nb: 'nb',
  set_nr: 'nr',
  set_data_mode: 'dataMode',
  set_auto_notch: 'autoNotch',
  set_manual_notch: 'manualNotch',
});

const DEFAULT_TIMEOUT_MS = 5_000;
/**
 * Terminal outcomes remain available for a five-second bounded presentation
 * announcement. This also preserves a normal 5s transport observation window
 * when independently submitted commands time out at staggered times.
 */
const OUTCOME_RETENTION_MS = 5_000;
const MAX_RETAINED_COMMANDS = 100;
let commands = $state<CommandLifecycle[]>([]);
const timers = new Map<string, ReturnType<typeof setTimeout>>();
let stateBackedReconciliationStarted = false;
const key = (id: string, epoch: number): string => `${epoch}:${id}`;

const receiverScope = (command: Pick<CommandLifecycle, 'params'>): 0 | 1 =>
  command.params.receiver === 1 ? 1 : 0;
const scopeKey = (scope: ControlFeedbackScope): string =>
  JSON.stringify([scope.control, scope.receiver, scope.slot ?? null]);
const commandScopeKey = (command: Pick<CommandLifecycle, 'name' | 'params'>): string => {
  const scope = getStateBackedCommandDescriptor(command.name)?.scope(command);
  return scope === null || scope === undefined
    ? JSON.stringify(['legacy-receiver', receiverScope(command)]) : scopeKey(scope);
};

export const isCommandLifecycleSuperseded = (command: CommandLifecycle): boolean => {
  const current = getCommandLifecycle(command.id, command.originalEpoch) ?? command;
  return current.locallyObsolete === true || current.terminalOutcome === 'superseded';
};
export const getCommandLifecycleHold = (command: Pick<CommandLifecycle, 'id' | 'originalEpoch'>): Readonly<CommandLifecycleHold> | undefined =>
  getCommandLifecycle(command.id, command.originalEpoch)?.hold;
const clearCommandHold = (command: Pick<CommandLifecycle, 'id' | 'originalEpoch'>): void => {
  const current = getCommandLifecycle(command.id, command.originalEpoch);
  if (current?.hold !== undefined) delete current.hold;
};

function clearRecordTimer(command: CommandLifecycle): void {
  const recordKey = key(command.id, command.originalEpoch);
  const timer = timers.get(recordKey);
  if (timer !== undefined) clearTimeout(timer);
  timers.delete(recordKey);
}
function retireRecord(command: CommandLifecycle): void {
  const index = commands.indexOf(command);
  if (index >= 0) commands.splice(index, 1);
  clearCommandHold(command);
}
function retainTerminalOutcome(command: CommandLifecycle): void {
  clearRecordTimer(command);
  timers.set(key(command.id, command.originalEpoch), setTimeout(() => {
    const current = getCommandLifecycle(command.id, command.originalEpoch);
    if (current === command && current.status !== 'pending' && current.status !== 'acknowledged') retireRecord(current);
    timers.delete(key(command.id, command.originalEpoch));
  }, OUTCOME_RETENTION_MS));
}
/**
 * Both submission and post-ack confirmation use this bounded, per-record
 * deadline. A delivery acknowledgement restarts it instead of leaving the
 * lifecycle dependent on an adapter's non-reactive clock.
 */
function startLiveDeadline(command: CommandLifecycle): void {
  clearRecordTimer(command);
  timers.set(key(command.id, command.originalEpoch), setTimeout(() => {
    const current = getCommandLifecycle(command.id, command.originalEpoch);
    if (!current || (current.status !== 'pending' && current.status !== 'acknowledged')) return;
    clearCommandHold(current);
    current.status = 'timed-out'; current.updatedAt = Date.now();
    retainTerminalOutcome(current);
  }, command.timeoutMs));
}
function reserveRecordSlot(): void {
  if (commands.length < MAX_RETAINED_COMMANDS) return;
  const terminal = commands.findIndex((command) => command.status !== 'pending' && command.status !== 'acknowledged');
  if (terminal < 0) throw new Error('Command lifecycle capacity exhausted');
  clearRecordTimer(commands[terminal]); retireRecord(commands[terminal]);
}
function transition(
  id: string, originalEpoch: number,
  status: 'acknowledged' | 'failed' | 'confirmed', eventEpoch: number, error?: string,
): void {
  const command = commands.find((item) => item.id === id && item.originalEpoch === originalEpoch
    && ((item.status === 'pending' && status !== 'confirmed')
      || (item.status === 'acknowledged' && (status === 'failed' || status === 'confirmed'))));
  if (!command) return;
  command.status = status; command.eventEpoch = eventEpoch; command.updatedAt = Date.now();
  if (error) command.error = error;
  if (status === 'acknowledged') {
    const radio = getRadioState(); command.ackObservationSeq = radio?.observationSeq;
    const observations: Record<string, number> = {};
    const captureBoundary = (path: string | null | undefined): void => {
      if (path === null || path === undefined) return;
      const marker = radio?.fieldStatus?.[path]?.lastObservedMonotonic;
      if (typeof marker === 'number' && Number.isFinite(marker)) observations[path] = marker;
    };
    const descriptor = getStateBackedCommandDescriptor(command.name);
    const scope = descriptor?.scope(command);
    captureBoundary(scope === null || scope === undefined ? null : descriptor?.fieldPath(scope));
    const pendingField = PENDING_CONFIRM_FIELDS[command.name];
    if (pendingField !== undefined) {
      captureBoundary(`${receiverScope(command) === 1 ? 'sub' : 'main'}.${pendingField}`);
    }
    command.ackFieldObservationTimes = observations;
    startLiveDeadline(command);
    if (descriptor !== undefined) startStateBackedReconciliation();
  } else {
    clearCommandHold(command);
    retainTerminalOutcome(command);
  }
}

/** Accepted StateStore observations, never presentation reads, reconcile commands. */
function reconcileStateBackedCommands(state: ServerState | null): void {
  if (!state) return;
  const currentProviderGeneration = providerGeneration(state.providerGeneration);
  for (const command of commands) {
    if (command.status !== 'acknowledged' || isCommandLifecycleSuperseded(command)) continue;
    const commandProviderGeneration = providerGeneration(command.providerGeneration);
    if (currentProviderGeneration === null || commandProviderGeneration !== currentProviderGeneration) continue;
    const descriptor = getStateBackedCommandDescriptor(command.name);
    const scope = descriptor?.scope(command);
    const target = descriptor?.target(command);
    if (descriptor === undefined || scope === null || scope === undefined || target === null || target === undefined) continue;
    const path = descriptor.fieldPath(scope);
    const field = state.fieldStatus?.[path];
    const marker = field?.lastObservedMonotonic;
    const confirmed = descriptor.confirmed(state, scope);
    if (field?.observed !== true || field.freshness === 'unknown' || field.availability === 'missing'
      || typeof marker !== 'number' || !Number.isFinite(marker)
      || confirmed === null) continue;

    const boundaries = command.ackFieldObservationTimes;
    if (boundaries === undefined) continue;
    const boundary = boundaries[path];
    if (typeof boundary !== 'number' || !Number.isFinite(boundary)) {
      command.ackFieldObservationTimes = { ...boundaries, [path]: marker };
      continue;
    }
    if (marker > boundary && (!descriptor.requireTargetMatch || descriptor.matches(confirmed, target))) {
      transition(command.id, command.originalEpoch, 'confirmed', command.eventEpoch ?? command.originalEpoch);
    }
  }
}

function startStateBackedReconciliation(): void {
  if (stateBackedReconciliationStarted) return;
  stateBackedReconciliationStarted = true;
  subscribeRadioState(reconcileStateBackedCommands);
}

export function beginCommand(input: BeginCommandInput): CommandLifecycle {
  if (getCommandLifecycle(input.id, input.originalEpoch)) throw new Error('duplicate command id in control session');
  reserveRecordSlot();
  const now = Date.now();
  const timeoutMs = input.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const command: CommandLifecycle = {
    ...input,
    providerGeneration: providerGeneration(getRadioState()?.providerGeneration),
    timeoutMs,
    createdAt: now,
    updatedAt: now,
    status: 'pending',
  };
  for (const existing of commands) if (existing.originalEpoch === command.originalEpoch
    && existing.name === command.name && commandScopeKey(existing) === commandScopeKey(command)) {
    existing.locallyObsolete = true;
  }
  commands.push(command);
  startLiveDeadline(command);
  return command;
}
export const getCommandLifecycles = (): readonly CommandLifecycle[] => commands;
export const getCommandLifecycle = (id: string, epoch: number): CommandLifecycle | undefined =>
  commands.find((command) => command.id === id && command.originalEpoch === epoch);
export const hasPendingCommands = (): boolean => commands.some((command) => command.status === 'pending');
export function markCommandDispatched(id: string, originalEpoch: number, eventEpoch: number): void {
  const command = getCommandLifecycle(id, originalEpoch);
  if (!command || command.status !== 'pending' || command.dispatchedEventEpoch === eventEpoch) return;
  command.dispatchedEventEpoch = eventEpoch;
  command.updatedAt = Date.now();
}
export const acknowledgeCommand = (id: string, epoch: number, eventEpoch: number): void =>
  transition(id, epoch, 'acknowledged', eventEpoch);
export const failCommand = (id: string, epoch: number, eventEpoch: number, error = 'Command failed'): void =>
  transition(id, epoch, 'failed', eventEpoch, error);
/** Downstream observation adapters may call this only after qualifying radio truth. */
export const confirmCommand = (id: string, epoch: number, eventEpoch: number): void =>
  transition(id, epoch, 'confirmed', eventEpoch);
export function applyCommandLifecycleProjection(event: CommandLifecycleProjection, currentEpoch: number): void {
  if (event.eventEpoch !== currentEpoch) return;
  const command = getCommandLifecycle(event.commandId, event.originalEpoch);
  if (!command || command.status === 'confirmed'
    || (command.eventEpoch !== undefined && command.eventEpoch !== event.eventEpoch)) return;
  if (event.kind === 'held') {
    if ((command.status !== 'pending' && command.status !== 'acknowledged')
      || event.reason !== 'tx_active' || typeof event.expiresAt !== 'number'
      || !Number.isFinite(event.expiresAt) || event.expiresAt < 0) return;
    const held = Object.freeze({ commandId: event.commandId, kind: 'held' as const,
      originalEpoch: event.originalEpoch, eventEpoch: event.eventEpoch,
      reason: 'tx_active' as const, expiresAt: event.expiresAt });
    const existing = command.hold;
    if (existing && existing.expiresAt === held.expiresAt && existing.eventEpoch === held.eventEpoch) return;
    command.hold = held;
    command.updatedAt = Date.now();
    return;
  }
  clearCommandHold(command); clearRecordTimer(command);
  command.eventEpoch = event.eventEpoch; command.updatedAt = Date.now();
  if (event.error === undefined) delete command.error; else command.error = event.error;
  if (event.kind === 'superseded') {
    command.terminalOutcome = 'superseded'; command.status = 'cancelled';
  } else {
    if (command.terminalOutcome !== undefined) delete command.terminalOutcome;
    command.status = event.kind;
  }
  retainTerminalOutcome(command);
}
export function cancelPendingCommands(epoch: number, error = 'session-disconnected'): void {
  for (const command of commands) {
    if (command.originalEpoch !== epoch || (command.status !== 'pending' && command.status !== 'acknowledged')) continue;
    command.status = 'cancelled'; command.updatedAt = Date.now(); command.error = error;
    clearCommandHold(command);
    retainTerminalOutcome(command);
  }
}
export function resetCommandLifecycle(): void {
  for (const timer of timers.values()) clearTimeout(timer);
  timers.clear(); commands = [];
}
