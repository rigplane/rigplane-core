/**
 * MOR-2719 — the planted-form tests for the shared "no runtime import"
 * detector. Every form below ADDS a runtime edge; the detector must report
 * it. The type-only forms must stay clean so the pins keep admitting them.
 */
import { describe, expect, it } from 'vitest';
import { moduleEdges, runtimeImportEdges } from './runtime-imports';

const RUNTIME_FORMS = [
  { name: 'a value import', source: "import { x } from './m';" },
  { name: 'a side-effect import', source: "import './m';" },
  { name: 'a re-export (export … from)', source: "export { x } from './m';" },
  { name: 'a star re-export (export * from)', source: "export * from './m';" },
  { name: 'an indented import line', source: "  import { x } from './m';" },
  {
    name: 'a type import without a semicolon, then a runtime import',
    source: "import type { X } from './m'\nimport { y } from './m';",
  },
  { name: 'a dynamic import', source: "const m = await import('./m');" },
  { name: 'a dynamic import with a space', source: "const m = import ('./m');" },
  { name: 'a require call', source: "const m = require('./m');" },
  {
    name: 'an inline type-only named import — runtime under verbatimModuleSyntax',
    source: "import { type X } from './m';",
  },
] as const;

describe('the runtime-import detector (MOR-2719)', () => {
  it.each(RUNTIME_FORMS)('reports the runtime edge of $name', ({ source }) => {
    expect(runtimeImportEdges('planted.ts', source)).not.toEqual([]);
  });

  it('reports no runtime edge for a type-only import', () => {
    expect(runtimeImportEdges('planted.ts', "import type { X } from './m';")).toEqual([]);
  });

  it('reports no runtime edge for a type-only re-export', () => {
    expect(runtimeImportEdges('planted.ts', "export type { X } from './m';")).toEqual([]);
  });

  it('keeps the type edge of a type-only import, so the pins are not vacuous', () => {
    expect(moduleEdges('planted.ts', "import type { X } from './m';")).toEqual([
      { specifier: './m', kind: 'type' },
    ]);
  });
});
