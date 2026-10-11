/**
 * Command vocabulary contract: every `intentSpecs` entry in `radio-intents.ts`
 * against `contracts/command-vocabulary.json`, which
 * `.github/scripts/gen-command-vocabulary.py` dumps from the server's
 * `ControlHandler._COMMANDS` (freshness: `tests/test_command_vocabulary_contract.py`).
 *
 * Compatible means: (1) the intent name is a key of the fixture's `commands`;
 * (2) when that entry is not `null` (a `CommandDescriptor` resolves the name),
 * the intent's parameter names, required and optional together, equal its
 * `result_keys`. `null` entries are checked by name only. Parameter kinds are
 * not compared.
 *
 * `intentSpecs` is not exported, so its array literal is read from source and
 * evaluated; the flattened names must equal the exported `RADIO_INTENT_NAMES`.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
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
interface ParsedSpec {
  readonly names: readonly string[];
  readonly params: Readonly<Record<string, string>>;
}

const DECLARATION_START = 'const intentSpecs = [';
const DECLARATION_END = '] as const satisfies readonly IntentSpec[];';

function parseIntentSpecs(): ParsedSpec[] {
  const source = readFileSync(resolve(process.cwd(), 'src/lib/runtime/commands/radio-intents.ts'), 'utf8');
  const start = source.indexOf(DECLARATION_START);
  const end = source.indexOf(DECLARATION_END, start);
  expect(start, 'intentSpecs declaration not found').toBeGreaterThanOrEqual(0);
  expect(end, 'intentSpecs terminator not found').toBeGreaterThan(start);
  const literal = source.slice(start + DECLARATION_START.length - 1, end + 1);
  return new Function(`return ${literal};`)() as ParsedSpec[];
}

function vocabularyMismatches(specs: readonly ParsedSpec[], commands: Readonly<Record<string, VocabularyEntry>>): string[] {
  const problems: string[] = [];
  for (const spec of specs) {
    const intentKeys = Object.keys(spec.params).sort();
    for (const name of spec.names) {
      if (!Object.prototype.hasOwnProperty.call(commands, name)) {
        problems.push(`${name}: not in ControlHandler._COMMANDS`);
        continue;
      }
      const entry = commands[name];
      if (entry === null) continue;
      const serverKeys = [...entry.result_keys].sort();
      if (intentKeys.join(',') !== serverKeys.join(',')) {
        problems.push(`${name}: intent params [${intentKeys.join(', ')}] != server result_keys [${serverKeys.join(', ')}]`);
      }
    }
  }
  return problems;
}

describe('command vocabulary contract (intentSpecs vs ControlHandler._COMMANDS)', () => {
  const specs = parseIntentSpecs();
  const commands = (vocabulary as { commands: Record<string, VocabularyEntry> }).commands;

  it('parses the same intent names the module exports', async () => {
    const { RADIO_INTENT_NAMES } = await import('../radio-intents');
    expect(specs.flatMap((spec) => spec.names)).toEqual([...RADIO_INTENT_NAMES]);
  });

  it('names only server commands, with descriptor-declared params matching', () => {
    expect(Object.keys(commands).length).toBeGreaterThan(0);
    expect(vocabularyMismatches(specs, commands)).toEqual([]);
  });
});
