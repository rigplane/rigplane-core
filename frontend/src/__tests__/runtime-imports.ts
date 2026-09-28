/**
 * MOR-2719 — the ONE shared "no runtime import" detector.
 *
 * The three pins (`reading-text.test.ts`, `pressed-of.test.ts`,
 * `control-instrument-behavior.test.ts`) each carried a private copy of the
 * same regex. This is that logic, verbatim, in one place; the planted-form
 * tests in `runtime-imports.test.ts` hold it to the ticket's contract.
 */
export interface ModuleEdge {
  readonly specifier: string;
  readonly kind: 'type' | 'runtime';
}

export function moduleEdges(_fileName: string, source: string): ModuleEdge[] {
  const statements = [...source.matchAll(/^import\b[^;]*;/gm)].map((m) => m[0]);
  const edges: ModuleEdge[] = statements
    .filter((statement) => !statement.startsWith('import type '))
    .map((statement) => ({ specifier: statement, kind: 'runtime' }));
  for (const forbidden of ['import(', 'require(']) {
    if (source.includes(forbidden)) edges.push({ specifier: forbidden, kind: 'runtime' });
  }
  return edges;
}

export function runtimeImportEdges(fileName: string, source: string): string[] {
  return moduleEdges(fileName, source)
    .filter((edge) => edge.kind === 'runtime')
    .map((edge) => edge.specifier);
}
