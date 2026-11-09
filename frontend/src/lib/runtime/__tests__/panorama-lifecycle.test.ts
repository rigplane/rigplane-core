import { readFileSync } from 'node:fs';
import { ModuleKind, ScriptTarget, transpileModule } from 'typescript';
import { expect, it, vi } from 'vitest';

it('kills the acquired panorama server when browser instrumentation setup fails', async () => {
  const kill = vi.fn(), failure = new Error('instrumentation failed');
  let run = async (_context: unknown, _info: unknown) => {};
  const source = readFileSync('tests/e2e/journey/panorama-settle.spec.ts', 'utf8');
  const compiled = transpileModule(source, { compilerOptions: { module: ModuleKind.CommonJS, target: ScriptTarget.ES2022 } }).outputText;
  new Function('require', 'exports', compiled)((name: string) => {
    if (name === '@playwright/test') return { test: (_name: string, body: typeof run) => { run = body; } };
    if (name === './server-process') return { startJourneyServer: async () => ({ kill }) };
    return {};
  }, {});
  await expect(run({ page: { on() {}, addInitScript: async () => { throw failure; } } },
    { outputPath: () => 'radio.jsonl' })).rejects.toBe(failure);
  expect(kill).toHaveBeenCalledOnce();
});
