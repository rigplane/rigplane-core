export type ManagedTransmitCountdown = Readonly<{ remainingAtReceiptMs: number; receivedAtPerformanceMs: number }>;
export const startManagedTransmitCountdown = (remainingMs: number, now: number): ManagedTransmitCountdown => ({ remainingAtReceiptMs: remainingMs, receivedAtPerformanceMs: now });
export const readManagedTransmitCountdown = (countdown: ManagedTransmitCountdown, now: number): number => Math.max(0, countdown.remainingAtReceiptMs - Math.max(0, now - countdown.receivedAtPerformanceMs));
