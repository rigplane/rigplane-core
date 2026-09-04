import { describe, expect, it } from 'vitest';
import { readManagedTransmitCountdown, startManagedTransmitCountdown } from '../managed-transmit-countdown';
describe('managed transmit countdown', () => { it('uses receipt-relative monotonic time, clamps at zero, and ignores backwards clocks', () => { const countdown = startManagedTransmitCountdown(42, 100); expect(readManagedTransmitCountdown(countdown, 110)).toBe(32); expect(readManagedTransmitCountdown(countdown, 99)).toBe(42); expect(readManagedTransmitCountdown(countdown, 999)).toBe(0); }); });
