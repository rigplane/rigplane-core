// MOR-3634: the flat rated maximum (`maxPowerW`) and the per-band rated maximum
// (`txBands[].maxPowerW`) are profile data the RF power slider scales to. The
// server owns the facts; the goldens below are generated from the real
// profiles by scripts/gen_state_wire_goldens.py.
import { describe, expect, it } from 'vitest';
import type { Capabilities } from '../capabilities';
import { validateCapabilities } from '../capabilities';
import goldens from '$lib/transport/__tests__/fixtures/state-wire-goldens.json';

const baseCapabilities = {
  model: 'Test Radio',
  scope: false,
  audio: false,
  tx: true,
  capabilities: [],
  receivers: 1,
  vfoScheme: 'ab' as const,
  freqRanges: [],
  modes: [],
  filters: [],
  audioConfig: { sampleRate: 48_000, channels: 1, codecs: ['pcm'] },
  webrtc: { available: false, enabled: false },
  txBands: null,
};

const band = (extra: Record<string, unknown> = {}) => ({
  name: '70cm', start: 430_000_000, end: 450_000_000, ...extra,
});

const optionalOnOlderServers: Pick<Capabilities, 'maxPowerW' | 'powerUnit'> = {};
void optionalOnOlderServers;

describe('capabilities maxPowerW', () => {
  it('accepts a payload without any maximum (older servers, radios with no [power])', () => {
    const caps = validateCapabilities({ ...baseCapabilities, txBands: [band()] });
    expect(caps.maxPowerW).toBeUndefined();
    expect(caps.txBands?.[0]?.maxPowerW).toBeUndefined();
  });

  it('accepts the flat maximum and a per-band maximum', () => {
    const caps = validateCapabilities({
      ...baseCapabilities, maxPowerW: 100, txBands: [band({ maxPowerW: 75 })],
    });
    expect(caps.maxPowerW).toBe(100);
    expect(caps.txBands?.[0]?.maxPowerW).toBe(75);
  });

  it.each(['raw_255', 'watts'] as const)('accepts powerUnit %s', (powerUnit) => {
    expect(validateCapabilities({ ...baseCapabilities, maxPowerW: 100, powerUnit }).powerUnit).toBe(powerUnit);
  });

  it.each(['percent', 255, null])('rejects an unknown powerUnit %o', (powerUnit) => {
    expect(() => validateCapabilities({ ...baseCapabilities, maxPowerW: 100, powerUnit }))
      .toThrow(/\$\.powerUnit: expected "raw_255" or "watts"/);
  });

  it.each([0, -1, 7.5, '100', null])('rejects a malformed flat maximum %o', (maxPowerW) => {
    expect(() => validateCapabilities({ ...baseCapabilities, maxPowerW }))
      .toThrow(/\$\.maxPowerW: expected a positive integer/);
  });

  it.each([0, -1, 7.5, '75', null])('rejects a malformed per-band maximum %o', (maxPowerW) => {
    expect(() => validateCapabilities({ ...baseCapabilities, txBands: [band({ maxPowerW })] }))
      .toThrow(/\$\.txBands\[0\]\.maxPowerW: expected a positive integer/);
  });

  it('state-wire goldens carry the IC-9700 per-band maximum and no other radio invents one', () => {
    type Golden = { capabilities: { maxPowerW?: number; txBands: Array<{ name: string; maxPowerW?: number }> | null } };
    const byModel = goldens as unknown as Record<string, Golden>;
    const ic9700 = byModel['IC-9700'].capabilities;
    expect(ic9700.maxPowerW).toBe(100);
    expect(Object.fromEntries((ic9700.txBands ?? []).map((b) => [b.name, b.maxPowerW])))
      .toEqual({ '2m': 100, '70cm': 75, '23cm': 10 });
    for (const [model, golden] of Object.entries(byModel)) {
      if (model === 'IC-9700') continue;
      expect(
        (golden.capabilities.txBands ?? []).filter((b) => b.maxPowerW !== undefined),
        model,
      ).toEqual([]);
    }
    expect(byModel['IC-7300'].capabilities.maxPowerW).toBe(100);
  });
});
