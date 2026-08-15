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

const DEFAULT_TIMEOUT_MS = 5_000;
/**
 * Terminal outcomes remain available for a five-second bounded presentation
 * announcement. This also preserves a normal 5s transport observation window
 * when independently submitted commands time out at staggered times.
 */
const OUTCOME_RETENTION_MS = 5_000;
const MAX_RETAINED_COMMANDS = 100;
const ACK_CORRELATION_PATHS = ['main.filterWidth', 'sub.filterWidth'] as const;
let commands = $state<CommandLifecycle[]>([]);
const timers = new Map<string, ReturnType<typeof setTimeout>>();
const supersededRecordKeys = new Set<string>();
let filterWidthReconciliationStarted = false;
const key = (id: string, epoch: number): string => `${epoch}:${id}`;

const receiverScope = (command: Pick<CommandLifecycle, 'params'>): 0 | 1 =>
  command.params.receiver === 1 ? 1 : 0;

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
    for (const path of ACK_CORRELATION_PATHS) {
      const field = radio?.fieldStatus?.[path];
      const marker = field?.lastObservedMonotonic;
      if (typeof marker === 'number' && Number.isFinite(marker)) observations[path] = marker;
    }
    command.ackFieldObservationTimes = observations;
    startLiveDeadline(command);
    if (command.name === 'set_filter_width') startFilterWidthReconciliation();
  } else {
    retainTerminalOutcome(command);
  }
}

/** Accepted state, not a presentation read, reconciles Filter Width. */
function reconcileFilterWidthCommands(state: ServerState | null): void {
  if (!state) return;
  for (const command of commands) {
    if (command.name !== 'set_filter_width' || command.status !== 'acknowledged' || isCommandLifecycleSuperseded(command)) continue;

    const receiver = receiverScope(command);
    const path = receiver === 1 ? 'sub.filterWidth' : 'main.filterWidth';
    const field = state.fieldStatus?.[path];
    const marker = field?.lastObservedMonotonic;
    const width = (receiver === 1 ? state.sub : state.main)?.filterWidth;
    const target = command.params.width;
    if (field?.observed !== true || field.freshness !== 'fresh' || field.availability !== 'available' || typeof marker !== 'number' || !Number.isFinite(marker)
      || typeof width !== 'number' || !Number.isFinite(width)
      || typeof target !== 'number' || !Number.isFinite(target)) continue;

    const boundaries = command.ackFieldObservationTimes;
    if (boundaries === undefined) continue;
    const boundary = boundaries[path];
    if (typeof boundary !== 'number' || !Number.isFinite(boundary)) {
      command.ackFieldObservationTimes = { ...boundaries, [path]: marker };
      continue;
    }
    if (marker > boundary && width === target) transition(command.id, command.originalEpoch, 'confirmed', command.eventEpoch ?? command.originalEpoch);
  }
}

function startFilterWidthReconciliation(): void {
  if (filterWidthReconciliationStarted) return;
  filterWidthReconciliationStarted = true;
  subscribeRadioState(reconcileFilterWidthCommands);
}

export function beginCommand(input: BeginCommandInput): CommandLifecycle {
  if (getCommandLifecycle(input.id, input.originalEpoch)) throw new Error('duplicate command id in control session');
  reserveRecordSlot();
  const now = Date.now();
  const timeoutMs = input.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const command: CommandLifecycle = { ...input, timeoutMs, createdAt: now, updatedAt: now, status: 'pending' };
  const scope = receiverScope(command);
  for (const existing of commands) if (existing.name === command.name && receiverScope(existing) === scope) {
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
