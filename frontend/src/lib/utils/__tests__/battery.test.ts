import { describe, it, expect, vi, afterEach } from 'vitest';
import { getPollingMultiplier, initBatteryMonitor } from '../battery';
import type { BatteryManager } from '../battery';

// ─── getPollingMultiplier ───────────────────────────────────────────────────

describe('getPollingMultiplier', () => {
  it('returns 1 for null (API unavailable)', () => {
    expect(getPollingMultiplier(null)).toBe(1);
  });

  it('returns 1 when charging regardless of level', () => {
    const battery: BatteryManager = {
      charging: true,
      level: 0.05,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    };
    expect(getPollingMultiplier(battery)).toBe(1);
  });

  it('returns 1 when level > 0.20', () => {
    const battery: BatteryManager = {
      charging: false,
      level: 0.5,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    };
    expect(getPollingMultiplier(battery)).toBe(1);
  });

  it('returns 2 when level is between 0.10 and 0.20', () => {
    const battery: BatteryManager = {
      charging: false,
      level: 0.15,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    };
    expect(getPollingMultiplier(battery)).toBe(2);
  });

  it('returns 4 when level <= 0.10', () => {
    const battery: BatteryManager = {
      charging: false,
      level: 0.05,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    };
    expect(getPollingMultiplier(battery)).toBe(4);
  });

  it('returns 1 at exactly 0.20 boundary (not > 0.20)', () => {
    const battery: BatteryManager = {
      charging: false,
      level: 0.2,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    };
    expect(getPollingMultiplier(battery)).toBe(2);
  });

  it('returns 2 at exactly 0.10 boundary (not > 0.10)', () => {
    const battery: BatteryManager = {
      charging: false,
      level: 0.1,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    };
    expect(getPollingMultiplier(battery)).toBe(4);
  });
});

// ─── initBatteryMonitor ─────────────────────────────────────────────────────

describe('initBatteryMonitor', () => {
  const originalNavigator = globalThis.navigator;

  afterEach(() => {
    Object.defineProperty(globalThis, 'navigator', {
      value: originalNavigator,
      writable: true,
      configurable: true,
    });
  });

  it('returns noop cleanup when getBattery is not available', async () => {
    Object.defineProperty(globalThis, 'navigator', {
      value: {},
      writable: true,
      configurable: true,
    });

    const onChange = vi.fn();
    const cleanup = await initBatteryMonitor(onChange);

    expect(onChange).not.toHaveBeenCalled();
    expect(typeof cleanup).toBe('function');
    cleanup(); // should not throw
  });

  it('calls onChange with initial multiplier', async () => {
    const fakeBattery: BatteryManager = {
      charging: false,
      level: 0.15,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    };

    Object.defineProperty(globalThis, 'navigator', {
      value: { getBattery: () => Promise.resolve(fakeBattery) },
      writable: true,
      configurable: true,
    });

    const onChange = vi.fn();
    const cleanup = await initBatteryMonitor(onChange);

    expect(onChange).toHaveBeenCalledWith(2);
    cleanup();
  });

  it('subscribes to chargingchange and levelchange events', async () => {
    const fakeBattery: BatteryManager = {
      charging: true,
      level: 1.0,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    };

    Object.defineProperty(globalThis, 'navigator', {
      value: { getBattery: () => Promise.resolve(fakeBattery) },
      writable: true,
      configurable: true,
    });

    const onChange = vi.fn();
    const cleanup = await initBatteryMonitor(onChange);

    expect(fakeBattery.addEventListener).toHaveBeenCalledWith('chargingchange', expect.any(Function));
    expect(fakeBattery.addEventListener).toHaveBeenCalledWith('levelchange', expect.any(Function));

    cleanup();

    expect(fakeBattery.removeEventListener).toHaveBeenCalledWith('chargingchange', expect.any(Function));
    expect(fakeBattery.removeEventListener).toHaveBeenCalledWith('levelchange', expect.any(Function));
  });

  it('returns noop cleanup when getBattery rejects', async () => {
    Object.defineProperty(globalThis, 'navigator', {
      value: { getBattery: () => Promise.reject(new Error('not allowed')) },
      writable: true,
      configurable: true,
    });

    const onChange = vi.fn();
    const cleanup = await initBatteryMonitor(onChange);

    expect(onChange).not.toHaveBeenCalled();
    expect(typeof cleanup).toBe('function');
    cleanup();
  });
});
