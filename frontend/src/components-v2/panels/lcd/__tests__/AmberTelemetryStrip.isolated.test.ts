/**
 * Component-level render tests for AmberTelemetryStrip (MOR-483 parts 2 & 3,
 * MOR-2659 unread treatment).
 *
 * Part 2 — telemetry labels must use the CALIBRATED meter-utils formatters
 * (formatVolts / formatAmps), not the old raw/255 linear maps.
 * Part 3 — the dead TEMP tile (IC-7610 exposes no CI-V temperature) is gone;
 * the strip renders exactly two tiles (VD · ID).
 * MOR-2659 — an unread meter renders an unlit, EMPTY value in a reserved
 * box, never a dash.
 *
 * Uses native svelte mount() in jsdom. The runtime adapter
 * `deriveAmberTelemetryProps` is mocked to feed deterministic raw values.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount } from 'svelte';
import { readFileSync } from 'node:fs';
import path from 'node:path';

import { formatVolts, formatAmps } from '../../meter-utils';

// MOR-1470: with a declared vd/id table, the backend publishes VOLTS/AMPS
// (engineering units) — 13.8 V is the operator's live-confirmed bench
// supply anchor (real IC-7610, raw 184 → 13.8 V; the curve now lives in
// the profile data, not in meter-utils).
const VD_VOLTS = 13.8;
const ID_AMPS = 10;

// MOR-2659: mutable so a test can feed unread (null) meters.
const mockTelemetry: { vdRaw: number | null; idRaw: number | null } = {
  vdRaw: VD_VOLTS,
  idRaw: ID_AMPS,
};

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  deriveAmberTelemetryProps: () => mockTelemetry,
}));

import type { Capabilities } from '$lib/types/capabilities';
import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';

function makeCaps(): Capabilities {
  return {
    model: 'IC-7610',
    scope: true,
    audio: true,
    tx: true,
    capabilities: ['scope', 'tx'],
    receivers: 2,
    vfoScheme: 'main_sub',
    freqRanges: [{ start: 1800000, end: 30000000, label: 'HF' }],
    modes: ['USB', 'LSB', 'CW', 'AM', 'FM'],
    filters: ['FIL1', 'FIL2', 'FIL3'],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['opus'] },
    webrtc: { available: true, enabled: false },
    txBands: null,
    stateContractVersion: 1,
    providerGeneration: 0,
    meterCalibrations: {
      vd: [
        { raw: 0, actual: 0, label: '0' },
        { raw: 184, actual: 13.8, label: '13.8' },
        { raw: 241, actual: 16, label: '16' },
      ],
      id: [
        { raw: 0, actual: 0, label: '0' },
        { raw: 151, actual: 10, label: '10' },
        { raw: 212, actual: 25, label: '25' },
      ],
    },
  };
}

import AmberTelemetryStrip from '../AmberTelemetryStrip.svelte';

let target: HTMLDivElement;

beforeEach(() => {
  target = document.createElement('div');
  document.body.appendChild(target);
  setCapabilities(makeCaps());
  mockTelemetry.vdRaw = VD_VOLTS;
  mockTelemetry.idRaw = ID_AMPS;
});

afterEach(() => {
  document.body.removeChild(target);
  clearCapabilities();
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
    expect(values[0]).toBe(formatVolts(VD_VOLTS));
    expect(values[0]).toContain('13.8');
    unmount(component);
  });

  it('formats the ID label with the calibrated formatAmps (10.0 A, not raw/255)', () => {
    const component = mount(AmberTelemetryStrip, { target, props: {} });
    const values = Array.from(target.querySelectorAll('.tile-value')).map((v) => v.textContent);
    expect(values[1]).toBe(formatAmps(ID_AMPS));
    expect(values[1]).toContain('10.0');
    unmount(component);
  });

  // ── MOR-2659: an unread meter renders unlit and empty, never a dash ──
  describe('unread meters (MOR-2659)', () => {
    it('renders an unlit, empty value while a meter is unread — never a dash', () => {
      mockTelemetry.vdRaw = null;
      mockTelemetry.idRaw = null;
      const component = mount(AmberTelemetryStrip, { target, props: {} });
      const values = Array.from(target.querySelectorAll('.tile-value'));
      expect(values.map((v) => v.textContent)).toEqual(['', '']);
      const tiles = Array.from(target.querySelectorAll('.tile'));
      expect(tiles.every((tile) => tile.classList.contains('tile-empty'))).toBe(true);
      expect(target.textContent).not.toContain('—');
      expect(target.textContent).not.toContain('unknown');
      unmount(component);
    });

    it('renders the unread tile empty while the other stays known', () => {
      mockTelemetry.vdRaw = null;
      const component = mount(AmberTelemetryStrip, { target, props: {} });
      const values = Array.from(target.querySelectorAll('.tile-value')).map((v) => v.textContent);
      expect(values[0]).toBe('');
      expect(values[1]).toBe(formatAmps(ID_AMPS));
      unmount(component);
    });

    // Reserved-slot pin (AmberIndStrip MOR-2546 precedent: readFileSync of the
    // component + a regex on its `<style>`, #3591): the value box must keep
    // its width in the unread state so the first reading cannot move the
    // layout. 7ch covers the widest text today ("13.8+ V").
    it('reserves the tile-value box for the widest text it can show', () => {
      const stripSource = readFileSync(
        path.resolve(process.cwd(), 'src/components-v2/panels/lcd/AmberTelemetryStrip.svelte'),
        'utf8',
      );
      expect(stripSource).toMatch(
        /\.tile-value\s*\{[^}]*min-inline-size:\s*7ch/,
      );
      expect(stripSource).toMatch(
        /\.tile-value\s*\{[^}]*font-variant-numeric:\s*tabular-nums/,
      );
    });
  });
});
