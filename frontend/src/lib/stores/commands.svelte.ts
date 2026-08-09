export type CommandLifecycleStatus = 'pending' | 'acknowledged' | 'failed' | 'cancelled' | 'timed-out';
export interface CommandLifecycle {
  id: string; name: string; params: Readonly<Record<string, unknown>>;
  originalEpoch: number; eventEpoch?: number; createdAt: number; updatedAt: number;
  timeoutMs: number; status: CommandLifecycleStatus; error?: string;
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
function boundRecords(): void {
  while (commands.length > MAX_RETAINED_COMMANDS) {
    const terminal = commands.findIndex((command) => command.status !== 'pending');
    const index = terminal >= 0 ? terminal : 0;
    clearRecordTimer(commands[index]); commands.splice(index, 1);
  }
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
  clearRecordTimer(command);
}

export function beginCommand(input: BeginCommandInput): CommandLifecycle {
  if (getCommandLifecycle(input.id, input.originalEpoch)) throw new Error('duplicate command id in control session');
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
  boundRecords();
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
