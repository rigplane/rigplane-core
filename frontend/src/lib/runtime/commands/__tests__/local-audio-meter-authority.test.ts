import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const panelSource = readFileSync(
  'src/lib/runtime/commands/panel-commands.ts',
  'utf8',
);
const busSource = readFileSync(
  'src/components-v2/wiring/command-bus.ts',
  'utf8',
);
const stateSource = readFileSync('src/lib/types/state.ts', 'utf8');
const fixtureBusSource = readFileSync('fixtures/stubs/command-bus.ts', 'utf8');
const panelPropsSource = readFileSync('src/lib/runtime/props/panel-props.ts', 'utf8');
const stateAdapterSource = readFileSync(
  'src/components-v2/wiring/state-adapter.ts',
  'utf8',
);
const mobileSource = readFileSync(
  'src/components-v2/layout/MobileRadioLayout.svelte',
  'utf8',
);
const lcdSource = readFileSync(
  'src/components-v2/panels/lcd/AmberCockpit.svelte',
  'utf8',
);

function factoryBlock(source: string, factory: string): string {
  const start = source.indexOf(`export function ${factory}`);
  expect(start).toBeGreaterThanOrEqual(0);
  const next = source.indexOf('\nexport function ', start + 1);
  return source.slice(start, next < 0 ? source.length : next);
}

describe('MOR-1409 A03c2 local audio and meter authority', () => {
  it('owns the complete audio-routing factory canonically and keeps the bus as a re-export', () => {
    const block = factoryBlock(panelSource, 'makeAudioRoutingHandlers');
    expect(busSource).toContain('makeAudioRoutingHandlers,');
    expect(busSource).not.toContain('export function makeAudioRoutingHandlers');
    expect(block).toContain('audioManager.setAudioConfig');
    expect(panelSource).toContain("const LS_FOCUS = 'icom.audio.focus'");
    expect(panelSource).toContain("const LS_SPLIT = 'icom.audio.split_stereo'");
    expect(panelSource).toContain("const LS_MAIN_DB = 'icom.audio.main_gain_db'");
    expect(panelSource).toContain("const LS_SUB_DB = 'icom.audio.sub_gain_db'");
    expect(block).toContain('Number.isFinite');
    expect(block).not.toMatch(
      /\b(?:cmd|sendCommand|dispatchRadioIntent|patchRadioState|patchActiveReceiver|patchReceiver)\s*\(/,
    );
  });

  it('deletes both dead meter factories, their fixture no-op, and Store-writer residue', () => {
    expect(panelSource).not.toContain('export function makeMeterHandlers');
    expect(busSource).not.toContain('export function makeMeterHandlers');
    expect(fixtureBusSource).not.toContain('export function makeMeterHandlers');
    expect(`${panelSource}\n${busSource}`).not.toMatch(
      /patchRadioState\s*\(\s*\{\s*meterSource\s*:/,
    );
  });

  it('removes only the hand-written ServerState augmentation and preserves local selectors', () => {
    const handWrittenState = stateSource.slice(stateSource.indexOf('// END GENERATED'));
    expect(handWrittenState).not.toContain('meterSource');
    expect(handWrittenState).toContain('transportSeq?: number');
    expect(handWrittenState).toContain('sub: ReceiverState');

    expect(mobileSource).toContain("$state<MeterSource>('POWER')");
    expect(mobileSource).toContain('selectMobileMeterSource');
    expect(lcdSource).toContain("$state<MeterSource>('S')");
    expect(lcdSource).toContain('cycleMeterSource');
  });

  it('keeps keyboard, scope/system, and meter projection ownership canonical', () => {
    for (const canonical of [
      'makeSystemHandlers',
      'makeScopeControlsHandlers',
      'makeKeyboardHandlers',
    ]) {
      expect(busSource).toContain(`${canonical},`);
      expect(busSource).not.toContain(`export function ${canonical}`);
      expect(panelSource).toContain(`export function ${canonical}`);
    }
    expect(busSource).not.toContain('function _activateReceiver');
    expect(busSource).not.toContain("case 'set_active_vfo'");
    expect(panelPropsSource).not.toContain('meterSource');
    expect(stateAdapterSource).not.toContain('meterSource');
  });
});
