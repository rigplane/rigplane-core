import { ESLint } from 'eslint';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(fileURLToPath(import.meta.url), '../../..');
const RULE = 'control-feedback-ownership/normalized-import-boundary';
const FILES = [
  'src/primitives/control-feedback/Probe.ts',
  'src/semantic/controls/Probe.ts',
] as const;

async function messages(code: string, filePath: string): Promise<readonly string[]> {
  const [result] = await new ESLint({ cwd: ROOT }).lintText(code, { filePath });
  return result.messages.map((message) => message.ruleId ?? '<parser>');
}

const FORBIDDEN = [
  [`import value from '$lib/runtime/tx-controller/model';`, 'static import'],
  [`import type { ControlFeedback } from '$lib/types/../runtime/adapters/panel-adapters';`, 'type import alias traversal'],
  [`export { runtime } from '$lib/runtime/frontend-runtime';`, 'named export'],
  [`export * from '$lib/runtime/commands/radio-intents';`, 'export star'],
  [`void import('$lib/runtime/tx-controller/model');`, 'literal dynamic import'],
  ['void import(`$lib/runtime/frontend-runtime`);', 'literal template dynamic import'],
  [`void import(\`$lib/runtime/${'${owner}'}\`);`, 'template dynamic import'],
  [`void import(owner);`, 'non-literal dynamic import'],
  [`import value from '../../lib/runtime/tx-controller/model';`, 'relative traversal'],
] as const;

describe('normalized ControlFeedback ownership boundary (MOR-1712)', () => {
  it.each(FILES)('rejects every runtime ownership form from %s', async (filePath) => {
    for (const [code] of FORBIDDEN) expect(await messages(code, filePath)).toContain(RULE);
  });

  it.each(FILES)('preserves normalized pure/type imports from %s', async (filePath) => {
    const allowed = [
      `import type { ServerState } from '$lib/types/state';`,
      `export type { ServerState } from '$lib/types/../types/state';`,
      `import type { ServerState } from '$lib/runtime/../types/state';`,
      `import type { ServerState } from '../../lib/runtime/../types/state';`,
      `import type { Snippet } from 'svelte';`,
      `import type { Presentation } from '../../primitives/control-feedback/control-feedback-presentation';`,
      `void import('$lib/types/state');`,
      'void import(`$lib/types/state`);',
    ];
    for (const code of allowed) expect(await messages(code, filePath)).not.toContain(RULE);
  });

  it.each([
    'src/primitives/control-feedback/Probe.svelte',
    'src/semantic/controls/Probe.svelte',
  ])('enforces the same boundary in Svelte module scripts from %s', async (filePath) => {
    expect(await messages(`<script>import x from '$lib/runtime/frontend-runtime';</script>`, filePath))
      .toContain(RULE);
    expect(await messages(`<script>import type { ServerState } from '$lib/types/state';</script>`, filePath))
      .not.toContain(RULE);
  });
});
