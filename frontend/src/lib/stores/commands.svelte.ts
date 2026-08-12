import { getRadioState } from './radio.svelte';

export type CommandLifecycleStatus = 'pending' | 'acknowledged' | 'failed' | 'cancelled' | 'timed-out';
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
}
export interface BeginCommandInput {
  id: string; name: string; params: Readonly<Record<string, unknown>>;
  originalEpoch: number; timeoutMs?: number;
}

const DEFAULT_TIMEOUT_MS = 5_000;
const MAX_RETAINED_COMMANDS = 100;
let commands = $state<CommandLifecycle[]>([]);
const timers = new Map<string, ReturnType<typeof setTimeout>>();
const key = (id: string, epoch: number): string => `${epoch}:${id}`;

function clearRecordTimer(command: CommandLifecycle): void {
  const recordKey = key(command.id, command.originalEpoch);
  const timer = timers.get(recordKey);
  if (timer !== undefined) clearTimeout(timer);
  timers.delete(recordKey);
}
function reserveRecordSlot(): void {
  if (commands.length < MAX_RETAINED_COMMANDS) return;
  const terminal = commands.findIndex((command) => command.status !== 'pending');
  if (terminal < 0) throw new Error('Command lifecycle capacity exhausted');
  clearRecordTimer(commands[terminal]); commands.splice(terminal, 1);
}
function transition(
  id: string, originalEpoch: number,
  status: 'acknowledged' | 'failed', eventEpoch: number, error?: string,
): void {
  const command = commands.find((item) => item.id === id && item.originalEpoch === originalEpoch
    && (item.status === 'pending' || (status === 'failed' && item.status === 'acknowledged')));
  if (!command) return;
  command.status = status; command.eventEpoch = eventEpoch; command.updatedAt = Date.now();
  if (error) command.error = error;
  if (status === 'acknowledged') command.ackObservationSeq = getRadioState()?.observationSeq;
  clearRecordTimer(command);
}

export function beginCommand(input: BeginCommandInput): CommandLifecycle {
  if (getCommandLifecycle(input.id, input.originalEpoch)) throw new Error('duplicate command id in control session');
  reserveRecordSlot();
  const now = Date.now();
  const timeoutMs = input.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const command: CommandLifecycle = { ...input, timeoutMs, createdAt: now, updatedAt: now, status: 'pending' };
  commands.push(command);
  timers.set(key(command.id, command.originalEpoch), setTimeout(() => {
    const current = getCommandLifecycle(command.id, command.originalEpoch);
    if (current?.status !== 'pending') return;
    current.status = 'timed-out'; current.updatedAt = Date.now();
    timers.delete(key(current.id, current.originalEpoch));
  }, timeoutMs));
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
export function cancelPendingCommands(epoch: number, error = 'session-disconnected'): void {
  for (const command of commands) {
    if (command.originalEpoch !== epoch || command.status !== 'pending') continue;
    command.status = 'cancelled'; command.updatedAt = Date.now(); command.error = error;
    clearRecordTimer(command);
  }
}
export function resetCommandLifecycle(): void {
  for (const timer of timers.values()) clearTimeout(timer);
  timers.clear(); commands = [];
}
