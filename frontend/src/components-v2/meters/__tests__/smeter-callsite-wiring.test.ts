/**
 * MOR-1451 — structural regression guard over every production call site
 * that hands the raw CI-V S-meter byte (0-255, `sMeter` in `ServerState`)
 * to a component whose `value`/`sValue` prop is documented as calibrated
 * dB-rel-S9 (`LinearSMeter`, `AmberSmeter`, `MetersDockPanel`,
 * `DockMeterPanel`).
 *
 * The root cause of the reported bug (raw 53 rendering "S9+40") was NOT a
 * bad calibration curve — it was these call sites skipping the raw ->
 * calibrated conversion (`rawToDbm`, `smeter-scale.ts`) entirely. A future
 * edit that re-introduces a bare `.sMeter` read into one of these call
 * sites would reintroduce the exact same class of bug without any of the
 * component-level tests noticing (they only see whatever value they're
 * handed). Source scans are the right instrument here, the same one
 * `MetersSurface.test.ts` and `LinearSMeter.test.ts` already use to pin
 * structural invariants a behavioural test cannot see.
 *
 * Every fixture below strips comments first so a `// rawToDbm(...)` mention
 * in prose can't fake a pass.
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

function sourceOf(relPath: string): string {
  return readFileSync(relPath, 'utf8')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '');
}

describe('every raw-sMeter call site converts through rawToDbm before reaching a calibrated meter prop', () => {
  it('semantic/MetersSurface.svelte wraps the LinearSMeter value', () => {
    const source = sourceOf('src/semantic/MetersSurface.svelte');
    expect(source).toMatch(/<LinearSMeter\s+value=\{rawToDbm\(/);
  });

  it('components-v2/vfo/VfoPanel.svelte wraps sValue before LinearSMeter', () => {
    const source = sourceOf('src/components-v2/vfo/VfoPanel.svelte');
    expect(source).toMatch(/let sDbm = \$derived\(rawToDbm\(sValue\)\)/);
    expect(source).toMatch(/<LinearSMeter\s+value=\{sDbm\}/);
  });

  it('components-v2/layout/MobileRadioLayout.svelte wraps mainVfo.sValue for both meter consumers', () => {
    const source = sourceOf('src/components-v2/layout/MobileRadioLayout.svelte');
    expect(source).toMatch(/let mainSDbm = \$derived\(rawToDbm\(mainVfo\.sValue\)\)/);
    expect(source).toMatch(/<LinearSMeter\s+value=\{mainSDbm\}/);
    expect(source).toMatch(/sValue=\{mainSDbm\}/);
  });

  it('components-v2/layout/RadioLayout.svelte wraps the MetersDockPanel sValue ("STATION METERS")', () => {
    const source = sourceOf('src/components-v2/layout/RadioLayout.svelte');
    expect(source).toMatch(/dockSDbm = \$derived\([^)]*rawToDbm\(dockSMeterRaw\)/);
    expect(source).toMatch(/<MetersDockPanel[\s\S]*?sValue=\{dockSDbm\}/);
  });

  it('components-v2/panels/lcd/AmberScope.svelte wraps the AmberSmeter value', () => {
    const source = sourceOf('src/components-v2/panels/lcd/AmberScope.svelte');
    expect(source).toMatch(/<AmberSmeter\s+value=\{rawToDbm\(/);
  });

  it('components-v2/panels/lcd/AmberCockpit.svelte wraps every S-source raw read (subSValue, meterValue, mainSMeter)', () => {
    const source = sourceOf('src/components-v2/panels/lcd/AmberCockpit.svelte');
    expect(source).toMatch(/let subSValue = \$derived\(rawToDbm\(/);
    expect(source).toMatch(/default: return rawToDbm\(rx\?\.sMeter/);
    expect(source).toMatch(/let mainSMeter = \$derived\(rawToDbm\(/);
  });
});
