import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const root = resolve(import.meta.dirname, '../../../../..');
const panelCommands = readFileSync(resolve(root, 'src/lib/runtime/commands/panel-commands.ts'), 'utf8');
const commandBusPath = resolve(root, 'src/components-v2/wiring/command-bus.ts');

describe('MOR-1409 A03d1 keyboard radio delegation', () => {
  it('keeps the complete twenty-three-action canonical dispatcher boundary', () => {
    expect(panelCommands).toContain('dispatchKeyboardRadioAction');
    for (const action of [
      'tune', 'band_select', 'mode_select', 'cycle_data_mode', 'cycle_filter',
      'cycle_preamp', 'cycle_att', 'cycle_agc', 'toggle_nr', 'toggle_nb',
      'toggle_auto_notch', 'toggle_ip_plus',
      'toggle_rit', 'toggle_xit', 'clear_rit_xit',
      'adjust_af_level', 'adjust_rf_gain', 'toggle_monitor',
      'toggle_split', 'vfo_swap', 'vfo_equalize', 'switch_active_vfo', 'set_active_vfo',
    ]) {
      expect(panelCommands).toContain(`case '${action}'`);
    }
  });

  // MOR-1409 A15 completes what this test was gating: the legacy bus is not
  // merely emptied of keyboard ownership, it is deleted. An absence pin on a
  // file that no longer exists is the strongest form of the original
  // "contains no `case '<action>'`" assertions, and unlike them it cannot be
  // satisfied by a shim that quietly grows the logic back.
  it('deletes the legacy bus rather than leaving it emptied', () => {
    expect(existsSync(commandBusPath)).toBe(false);
  });
});
