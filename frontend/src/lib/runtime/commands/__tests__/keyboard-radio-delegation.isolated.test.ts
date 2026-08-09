import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const root = resolve(import.meta.dirname, '../../../../..');
const panelCommands = readFileSync(resolve(root, 'src/lib/runtime/commands/panel-commands.ts'), 'utf8');
const commandBus = readFileSync(resolve(root, 'src/components-v2/wiring/command-bus.ts'), 'utf8');

describe('MOR-1409 A03d1 keyboard radio delegation', () => {
  it('requires the complete twenty-three-action canonical dispatcher boundary before deleting legacy bus ownership', () => {
    expect(panelCommands).toContain('dispatchKeyboardRadioAction');
    for (const action of [
      'tune', 'band_select', 'mode_select', 'cycle_data_mode', 'cycle_filter',
      'cycle_preamp', 'cycle_att', 'cycle_agc', 'toggle_nr', 'toggle_nb',
      'toggle_auto_notch', 'toggle_ip_plus',
      'toggle_rit', 'toggle_xit', 'clear_rit_xit',
      'adjust_af_level', 'adjust_rf_gain', 'toggle_monitor',
      'toggle_split', 'vfo_swap', 'vfo_equalize', 'switch_active_vfo', 'set_active_vfo',
    ]) {
      expect(commandBus).not.toContain(`case '${action}'`);
    }
    expect(commandBus).not.toContain('function cycleValue');
    expect(commandBus).not.toContain('activeReceiverParam');
    expect(commandBus).not.toContain('function _activateReceiver');
    expect(commandBus).not.toContain("case 'toggle_rit'");
    expect(commandBus).not.toContain("case 'set_active_vfo'");
  });
});
