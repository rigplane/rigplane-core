import { getRadioState, subscribeRadioState } from './radio.svelte';
import type { ServerState } from '../types/state';

export type CommandLifecycleStatus = 'pending' | 'acknowledged' | 'confirmed' | 'failed' | 'cancelled' | 'timed-out';
export interface CommandLifecycle {
  id: string; name: string; params: Readonly<Record<string, unknown>>;
  originalEpoch: number; eventEpoch?: number; createdAt: number; updatedAt: number;
  timeoutMs: number; status: CommandLifecycleStatus; error?: string;
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
}
export interface BeginCommandInput {
  id: string; name: string; params: Readonly<Record<string, unknown>>;
  originalEpoch: number; timeoutMs?: number;
}

export interface ControlFeedbackScope {
  readonly control: string;
  readonly receiver: 0 | 1;
  readonly slot?: string;
}
export type StateBackedRepeatPolicy = 'latest-target-wins';
export interface StateBackedCommandDescriptor<T> {
  readonly intentName: string;
  readonly repeatPolicy: StateBackedRepeatPolicy;
  scope(command: Pick<CommandLifecycle, 'params'>): ControlFeedbackScope | null;
  fieldPath(scope: ControlFeedbackScope): string;
  target(command: Pick<CommandLifecycle, 'params'>): T | null;
  confirmed(state: ServerState, scope: ControlFeedbackScope): T | null;
  matches(confirmed: T, target: T): boolean;
}

const finiteNumber = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;

/** First state-backed descriptor; later controls compose with this contract. */
export const FILTER_WIDTH_COMMAND_DESCRIPTOR: StateBackedCommandDescriptor<number> = Object.freeze({
  intentName: 'set_filter_width',
  repeatPolicy: 'latest-target-wins',
  scope: (command: Pick<CommandLifecycle, 'params'>) => Object.freeze({
    control: 'filter-width',
    receiver: command.params.receiver === 1 ? 1 : 0,
  }),
  fieldPath: (scope: ControlFeedbackScope) => scope.receiver === 1 ? 'sub.filterWidth' : 'main.filterWidth',
  target: (command: Pick<CommandLifecycle, 'params'>) => finiteNumber(command.params.width),
  confirmed: (state: ServerState, scope: ControlFeedbackScope) => finiteNumber(
    (scope.receiver === 1 ? state.sub : state.main)?.filterWidth,
  ),
  matches: (confirmed: number, target: number) => confirmed === target,
});

const stateBackedDescriptors = new Map<string, StateBackedCommandDescriptor<unknown>>([
  [FILTER_WIDTH_COMMAND_DESCRIPTOR.intentName, FILTER_WIDTH_COMMAND_DESCRIPTOR],
]);

export const getStateBackedCommandDescriptor = (
  intentName: string,
): StateBackedCommandDescriptor<unknown> | undefined => stateBackedDescriptors.get(intentName);

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
const supersededRecordKeys = new Set<string>();
let stateBackedReconciliationStarted = false;
const key = (id: string, epoch: number): string => `${epoch}:${id}`;

const receiverScope = (command: Pick<CommandLifecycle, 'params'>): 0 | 1 =>
  command.params.receiver === 1 ? 1 : 0;

const commandScopeKey = (command: Pick<CommandLifecycle, 'name' | 'params'>): string => {
  const descriptor = getStateBackedCommandDescriptor(command.name);
  const scope = descriptor?.scope(command);
  return scope === null || scope === undefined
    ? JSON.stringify(['legacy-receiver', receiverScope(command)])
    : JSON.stringify([scope.control, scope.receiver, scope.slot ?? null]);
};

export const isCommandLifecycleSuperseded = (command: CommandLifecycle): boolean => supersededRecordKeys.has(key(command.id, command.originalEpoch));

function clearRecordTimer(command: CommandLifecycle): void {
  const recordKey = key(command.id, command.originalEpoch);
  const timer = timers.get(recordKey);
  if (timer !== undefined) clearTimeout(timer);
  timers.delete(recordKey);
}
function retireRecord(command: CommandLifecycle): void {
  const index = commands.indexOf(command);
  if (index >= 0) commands.splice(index, 1);
  supersededRecordKeys.delete(key(command.id, command.originalEpoch));
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
    const descriptor = getStateBackedCommandDescriptor(command.name);
    const scope = descriptor?.scope(command);
    const path = scope === null || scope === undefined ? null : descriptor?.fieldPath(scope);
    if (path !== null && path !== undefined) {
      const field = radio?.fieldStatus?.[path];
      const marker = field?.lastObservedMonotonic;
      if (typeof marker === 'number' && Number.isFinite(marker)) observations[path] = marker;
    }
    command.ackFieldObservationTimes = observations;
    startLiveDeadline(command);
    if (descriptor !== undefined) startStateBackedReconciliation();
  } else {
    retainTerminalOutcome(command);
  }
}

/** Accepted StateStore observations, never presentation reads, reconcile commands. */
function reconcileStateBackedCommands(state: ServerState | null): void {
  if (!state) return;
  for (const command of commands) {
    if (command.status !== 'acknowledged' || isCommandLifecycleSuperseded(command)) continue;
    const descriptor = getStateBackedCommandDescriptor(command.name);
    const scope = descriptor?.scope(command);
    const target = descriptor?.target(command);
    if (descriptor === undefined || scope === null || scope === undefined || target === null || target === undefined) continue;
    const path = descriptor.fieldPath(scope);
    const field = state.fieldStatus?.[path];
    const marker = field?.lastObservedMonotonic;
    const confirmed = descriptor.confirmed(state, scope);
    if (field?.observed !== true || field.freshness !== 'fresh' || field.availability !== 'available' || typeof marker !== 'number' || !Number.isFinite(marker)
      || confirmed === null) continue;

    const boundaries = command.ackFieldObservationTimes;
    if (boundaries === undefined) continue;
    const boundary = boundaries[path];
    if (typeof boundary !== 'number' || !Number.isFinite(boundary)) {
      command.ackFieldObservationTimes = { ...boundaries, [path]: marker };
      continue;
    }
    if (marker > boundary && descriptor.matches(confirmed, target)) {
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
  const command: CommandLifecycle = { ...input, timeoutMs, createdAt: now, updatedAt: now, status: 'pending' };
  for (const existing of commands) if (existing.name === command.name
    && commandScopeKey(existing) === commandScopeKey(command)) {
    supersededRecordKeys.add(key(existing.id, existing.originalEpoch));
  }
  commands.push(command);
  startLiveDeadline(command);
  return command;
}
export const getCommandLifecycles = (): readonly CommandLifecycle[] => commands;
export const getCommandLifecycle = (id: string, epoch: number): CommandLifecycle | undefined =>
  commands.find((command) => command.id === id && command.originalEpoch === epoch);
export const hasPendingCommands = (): boolean => commands.some((command) => command.status === 'pending');
export const acknowledgeCommand = (id: string, epoch: number, eventEpoch: number): void =>
  transition(id, epoch, 'acknowledged', eventEpoch);
export const failCommand = (id: string, epoch: number, eventEpoch: number, error = 'Command failed'): void =>
  transition(id, epoch, 'failed', eventEpoch, error);
/** Downstream observation adapters may call this only after qualifying radio truth. */
export const confirmCommand = (id: string, epoch: number, eventEpoch: number): void =>
  transition(id, epoch, 'confirmed', eventEpoch);
export function cancelPendingCommands(epoch: number, error = 'session-disconnected'): void {
  for (const command of commands) {
    if (command.originalEpoch !== epoch || (command.status !== 'pending' && command.status !== 'acknowledged')) continue;
    command.status = 'cancelled'; command.updatedAt = Date.now(); command.error = error;
    retainTerminalOutcome(command);
  }
}
export function resetCommandLifecycle(): void {
  for (const timer of timers.values()) clearTimeout(timer);
  timers.clear(); commands = []; supersededRecordKeys.clear();
}
