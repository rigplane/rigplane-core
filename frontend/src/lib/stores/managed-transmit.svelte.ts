import { UI_TELEMETRY_ENABLED, recordManagedTxGetFinish, recordManagedTxGetStart } from '../diagnostics/ui-telemetry';
import { ManagedTransmitClient } from '../transport/managed-transmit-client';
import { startManagedTransmitCountdown, readManagedTransmitCountdown, type ManagedTransmitCountdown } from '../runtime/managed-transmit-countdown';
import type { ManagedTransmitDocument } from '../types/managed-transmit';
let document = $state<ManagedTransmitDocument | null>(null); let countdown = $state<ManagedTransmitCountdown | null>(null); let stale = $state(true);
let invalidationRevision = 0;
let refreshRevision = 0;
let appliedRevision = 0;
const clock = () => globalThis.performance.now();
const contextIsCurrent = (revision: number): boolean => revision === invalidationRevision;
const applyManagedTransmitSnapshot = (value: ManagedTransmitDocument, receivedAt = clock()): void => {
  document = value;
  appliedRevision += 1;
  const tot = value.managedTransmit.status === 'available' ? value.managedTransmit.tot : null;
  countdown = tot === null || tot.remainingMs === null
    ? null
    : startManagedTransmitCountdown(tot.remainingMs, receivedAt);
  stale = value.managedTransmit.status !== 'available';
};
export function managedTransmitSnapshot(): ManagedTransmitDocument | null { return document; }
export function managedTransmitIsStale(): boolean { return stale; }
export function managedTransmitAppliedRevision(): number { return appliedRevision; }
export function managedTransmitRemainingMs(now = clock()): number | null { return !stale && countdown ? readManagedTransmitCountdown(countdown, now) : null; }
export function receiveManagedTransmitSnapshot(value: ManagedTransmitDocument, receivedAt = clock()): void { if (document !== null && Date.parse(value.sampledAt) < Date.parse(document.sampledAt)) return; applyManagedTransmitSnapshot(value, receivedAt); }
export function invalidateManagedTransmit(): void {
  invalidationRevision++;
  refreshRevision++;
  stale = true;
  countdown = null;
}
export async function refreshManagedTransmit(
  client: Pick<ManagedTransmitClient, 'snapshot'> = new ManagedTransmitClient(),
): Promise<void> {
  const invalidationAtStart = invalidationRevision;
  const refresh = ++refreshRevision;
  const diagnosticStarted = UI_TELEMETRY_ENABLED ? performance.now() : 0;
  if (UI_TELEMETRY_ENABLED) recordManagedTxGetStart();
  let value: ManagedTransmitDocument;
  try {
    value = await client.snapshot();
  } catch {
    const current = refresh === refreshRevision && contextIsCurrent(invalidationAtStart);
    if (UI_TELEMETRY_ENABLED) recordManagedTxGetFinish(
      current ? 'failed' : 'discarded', performance.now() - diagnosticStarted,
    );
    if (!current) return;
    invalidateManagedTransmit();
    return;
  }
  const current = refresh === refreshRevision && contextIsCurrent(invalidationAtStart);
  if (UI_TELEMETRY_ENABLED) recordManagedTxGetFinish(
    current ? 'completed' : 'discarded', performance.now() - diagnosticStarted,
  );
  if (!current) return;
  receiveManagedTransmitSnapshot(value);
}
export async function submitManagedTransmit(operation: 'transmit_on' | 'force_off', client = new ManagedTransmitClient()): Promise<'accepted' | 'rejected'> { const outcome = await client.command(operation); await refreshManagedTransmit(client); return outcome; }
export async function setManagedTransmitTot(
  configuredSeconds: number | null,
  client: Pick<ManagedTransmitClient, 'setTot'> = new ManagedTransmitClient(),
): Promise<void> {
  const invalidationAtStart = invalidationRevision;
  try {
    const value = await client.setTot(configuredSeconds);
    if (!contextIsCurrent(invalidationAtStart)) return;
    receiveManagedTransmitSnapshot(value);
  } catch (error) {
    if (!contextIsCurrent(invalidationAtStart)) return;
    invalidateManagedTransmit();
    throw error;
  }
}
