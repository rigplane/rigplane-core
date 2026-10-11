/**
 * Command vocabulary contract: the web UI's radio intents against
 * `contracts/command-vocabulary.json`, which
 * `.github/scripts/gen-command-vocabulary.py` dumps from the server's
 * `ControlHandler._COMMANDS` (freshness: `tests/test_command_vocabulary_contract.py`).
 *
 * 1. Name coverage: every `RADIO_INTENT_NAMES` entry is a key of the
 *    fixture's `commands`.
 * 2. Response-key characterization: for an intent whose fixture entry is not
 *    `null`, its `RADIO_INTENT_PARAM_NAMES` set equals the entry's
 *    `result_keys`, the keys `CommandDescriptor.result` puts in the server's
 *    response. Those are response keys, not the parameters the server's
 *    bind function accepts, so drift in accepted parameter names is not
 *    covered here.
 */
import { describe, expect, it, vi } from 'vitest';
import vocabulary from '../../../../../../contracts/command-vocabulary.json';

vi.mock('$lib/transport/ws-client', () => ({
  getControlSession: vi.fn(() => ({ state: 'connected', epoch: 1 })),
  onCommandDelivery: vi.fn(() => () => {}),
  onCommandLifecycleDelivery: vi.fn(() => () => {}),
  onControlSessionTransition: vi.fn(() => () => {}),
  sendCommand: vi.fn(() => true),
}));

type VocabularyEntry = { readonly descriptor: string; readonly result_keys: readonly string[] } | null;
const commands = (vocabulary as { commands: Record<string, VocabularyEntry> }).commands;

describe('command vocabulary contract (radio intents vs ControlHandler._COMMANDS)', () => {
  it('every intent name is a server command', async () => {
    const { RADIO_INTENT_NAMES } = await import('../radio-intents');
    expect(RADIO_INTENT_NAMES.length).toBeGreaterThan(0);
    const missing = RADIO_INTENT_NAMES.filter((name) => !Object.prototype.hasOwnProperty.call(commands, name));
    expect(missing).toEqual([]);
  });

  it('intent param names equal descriptor response keys (accepted-param drift not covered)', async () => {
    const { RADIO_INTENT_NAMES, RADIO_INTENT_PARAM_NAMES } = await import('../radio-intents');
    const mismatches: string[] = [];
    let compared = 0;
    for (const name of RADIO_INTENT_NAMES) {
      const entry = commands[name];
      if (entry === null || entry === undefined) continue;
      compared += 1;
      const intentKeys = [...RADIO_INTENT_PARAM_NAMES[name]].sort().join(',');
      const responseKeys = [...entry.result_keys].sort().join(',');
      if (intentKeys !== responseKeys) mismatches.push(`${name}: [${intentKeys}] != result_keys [${responseKeys}]`);
    }
    expect(compared).toBeGreaterThan(0);
    expect(mismatches).toEqual([]);
  });
});
