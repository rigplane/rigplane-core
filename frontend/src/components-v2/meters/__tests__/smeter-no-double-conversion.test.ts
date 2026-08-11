/**
 * MOR-1451 — structural regression guard against the OPPOSITE mistake this
 * PR made and then reverted: wrapping `sMeter` in `rawToDbm()` at a
 * production call site.
 *
 * `ServerState.main.sMeter` (and every derived `sValue`) is ALREADY the
 * calibrated dB-rel-S9 reading for any radio whose profile declares
 * `[meters.s_meter]` — the backend does that conversion itself
 * (`runtime/_civ_rx.py`'s `_calibrated_meter_value` -> `interpolate_meter`,
 * pinned server-side in `tests/test_civ_rx_coverage.py` and
 * `tests/test_rig_ic7300.py`). A frontend call site that also runs it
 * through `rawToDbm()` double-converts an already-calibrated value —
 * exactly the bug a PR reviewer caught in an earlier draft of this fix (a
 * live-evidence value of -30 dB-rel-S9 got reinterpreted as a raw byte,
 * clamped to 0, and rendered S0/-127dBm instead of the correct S4).
 *
 * `sMeter` legitimately stays RAW only for a radio with no calibration
 * table — `smeter-scale.ts`'s `isSmeterCalibrated()` / the honest-fallback
 * tests in `LinearSMeter.test.ts` and `meter-utils.test.ts` cover that
 * case; this file guards the opposite direction, at every call site that
 * reads `.sMeter` or a `sValue` derived from it.
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

function sourceOf(relPath: string): string {
  return readFileSync(relPath, 'utf8')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '');
}

/** No `rawToDbm(` call anywhere near an `sMeter`/`sValue` read in `source`. */
function hasNoRawToDbmOnSMeter(source: string): boolean {
  return !/rawToDbm\([^)]*(?:sMeter|sValue)/.test(source)
    && !/(?:sMeter|sValue)[^;]*rawToDbm\(/.test(source);
}

describe('sMeter is never re-wrapped in rawToDbm at a production call site (MOR-1451)', () => {
  const files = [
    'src/semantic/MetersSurface.svelte',
    'src/components-v2/vfo/VfoPanel.svelte',
    'src/components-v2/layout/RadioLayout.svelte',
    'src/components-v2/layout/MobileRadioLayout.svelte',
    'src/components-v2/layout/layout-utils.ts',
    'src/components-v2/panels/lcd/AmberScope.svelte',
    'src/components-v2/panels/lcd/AmberCockpit.svelte',
    'src/lib/runtime/props/panel-props.ts',
  ];

  it.each(files)('%s does not wrap sMeter/sValue in rawToDbm', (relPath) => {
    expect(hasNoRawToDbmOnSMeter(sourceOf(relPath))).toBe(true);
  });

  it('none of the guarded files import rawToDbm at all (the conversion belongs server-side)', () => {
    for (const relPath of files) {
      expect(sourceOf(relPath)).not.toMatch(/import\s*\{[^}]*rawToDbm/);
    }
  });
});
