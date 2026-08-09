import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const root = resolve(import.meta.dirname, '../../../../..');
const panelCommands = readFileSync(resolve(root, 'src/lib/runtime/commands/panel-commands.ts'), 'utf8');
const commandBus = readFileSync(resolve(root, 'src/components-v2/wiring/command-bus.ts'), 'utf8');

describe('MOR-1409 A03d1a keyboard radio delegation', () => {
  it('requires the twelve-action canonical dispatcher boundary before deleting legacy bus ownership', () => {
    expect(panelCommands).toContain('dispatchKeyboardRadioAction');
    for (const action of [
      'tune', 'band_select', 'mode_select', 'cycle_data_mode', 'cycle_filter',
      'cycle_preamp', 'cycle_att', 'cycle_agc', 'toggle_nr', 'toggle_nb',
      'toggle_auto_notch', 'toggle_ip_plus',
    ]) {
      expect(commandBus).not.toContain(`case '${action}'`);
    }
    expect(commandBus).not.toContain('function cycleValue');
  });
});
