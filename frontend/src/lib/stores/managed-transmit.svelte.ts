import { ManagedTransmitClient } from '../transport/managed-transmit-client';
import { startManagedTransmitCountdown, readManagedTransmitCountdown, type ManagedTransmitCountdown } from '../runtime/managed-transmit-countdown';
import type { ManagedTransmitDocument } from '../types/managed-transmit';
let document = $state<ManagedTransmitDocument | null>(null); let countdown = $state<ManagedTransmitCountdown | null>(null); let stale = $state(true);
const clock = () => globalThis.performance.now();
export function managedTransmitSnapshot(): ManagedTransmitDocument | null { return document; }
export function managedTransmitIsStale(): boolean { return stale; }
export function managedTransmitRemainingMs(now = clock()): number | null { return !stale && countdown ? readManagedTransmitCountdown(countdown, now) : null; }
export function receiveManagedTransmitSnapshot(value: ManagedTransmitDocument, receivedAt = clock()): void { if (document !== null && Date.parse(value.sampledAt) < Date.parse(document.sampledAt)) return; document = value; const tot = value.managedTransmit.status === 'available' ? value.managedTransmit.tot : null; countdown = tot === null || tot.remainingMs === null ? null : startManagedTransmitCountdown(tot.remainingMs, receivedAt); stale = false; }
export function invalidateManagedTransmit(): void { stale = true; countdown = null; }
export async function refreshManagedTransmit(client = new ManagedTransmitClient()): Promise<void> { invalidateManagedTransmit(); receiveManagedTransmitSnapshot(await client.snapshot()); }
export async function submitManagedTransmit(operation: 'transmit_on' | 'force_off', client = new ManagedTransmitClient()): Promise<'accepted' | 'rejected'> { const outcome = await client.command(operation); await refreshManagedTransmit(client); return outcome; }
