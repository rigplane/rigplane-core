/**
 * Component-level render tests for AmberTelemetryStrip (MOR-483 parts 2 & 3).
 *
 * Part 2 — telemetry labels must use the CALIBRATED meter-utils formatters
 * (formatVolts / formatAmps), not the old raw/255 linear maps.
 * Part 3 — the dead TEMP tile (IC-7610 exposes no CI-V temperature) is gone;
 * the strip renders exactly two tiles (VD · ID).
 *
 * Uses native svelte mount() in jsdom. The runtime adapter
 * `deriveAmberTelemetryProps` is mocked to feed deterministic raw values.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount } from 'svelte';

import { formatVolts, formatAmps } from '../../meter-utils';

// Nominal IC-7610 supply: raw 157 → ~13.8 V (calibrated), NOT ~9.8 V (raw/255*16).
const VD_RAW = 157;
const ID_RAW = 151; // → 10.0 A calibrated

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  deriveAmberTelemetryProps: () => ({ vdRaw: VD_RAW, idRaw: ID_RAW }),
}));

import AmberTelemetryStrip from '../AmberTelemetryStrip.svelte';

let target: HTMLDivElement;

beforeEach(() => {
  target = document.createElement('div');
  document.body.appendChild(target);
});

afterEach(() => {
  document.body.removeChild(target);
});

describe('AmberTelemetryStrip', () => {
  it('renders exactly two tiles (VD · ID) — no TEMP tile', () => {
    const component = mount(AmberTelemetryStrip, { target, props: {} });
    const tiles = target.querySelectorAll('.tile');
    expect(tiles.length).toBe(2);
    const tags = Array.from(target.querySelectorAll('.tile-tag')).map((t) => t.textContent);
    expect(tags).toEqual(['VD', 'ID']);
    expect(tags).not.toContain('TEMP');
    unmount(component);
  });

  it('formats the VD label with the calibrated formatVolts (≈13.8 V, not raw/255)', () => {
    const component = mount(AmberTelemetryStrip, { target, props: {} });
    const values = Array.from(target.querySelectorAll('.tile-value')).map((v) => v.textContent);
    expect(values[0]).toBe(formatVolts(VD_RAW));
    // Calibrated reading is ~13.8 V; the old raw/255*16 map gave ~9.8 V.
    expect(values[0]).toContain('13.8');
    unmount(component);
  });

  it('formats the ID label with the calibrated formatAmps (10.0 A, not raw/255)', () => {
    const component = mount(AmberTelemetryStrip, { target, props: {} });
    const values = Array.from(target.querySelectorAll('.tile-value')).map((v) => v.textContent);
    expect(values[1]).toBe(formatAmps(ID_RAW));
    expect(values[1]).toContain('10.0');
    unmount(component);
  });
});
