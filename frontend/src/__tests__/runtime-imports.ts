/**
 * MOR-2719 — the ONE shared "no runtime import" detector.
 *
 * Walks the TypeScript AST instead of regexing the source, so every form
 * that adds a runtime edge is reported:
 *   - an `import` that is not `import type` — including `import { type X }`,
 *     which stays a runtime edge under `verbatimModuleSyntax`, and a bare
 *     side-effect import;
 *   - a re-export (`export … from`, `export * from`) that is not `export type`;
 *   - a dynamic `import(...)` in any spacing;
 *   - a `require(...)` call.
 * Type-only imports and re-exports report a `type` edge, so a pin can also
 * assert its module still imports something.
 */
import ts from 'typescript';

export interface ModuleEdge {
  readonly specifier: string;
  readonly kind: 'type' | 'runtime';
}

export function moduleEdges(fileName: string, source: string): ModuleEdge[] {
  const sourceFile = ts.createSourceFile(
    fileName, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS,
  );
  const edges: ModuleEdge[] = [];
  const add = (specifier: string, kind: ModuleEdge['kind']): void => {
    edges.push({ specifier, kind });
  };
  const specifierText = (expression: ts.Expression): string =>
    ts.isStringLiteralLike(expression) ? expression.text : '<computed>';

  const visit = (node: ts.Node): void => {
    if (ts.isImportDeclaration(node)) {
      add(specifierText(node.moduleSpecifier),
        node.importClause?.isTypeOnly === true ? 'type' : 'runtime');
    } else if (ts.isExportDeclaration(node) && node.moduleSpecifier !== undefined) {
      add(specifierText(node.moduleSpecifier), node.isTypeOnly ? 'type' : 'runtime');
    } else if (ts.isCallExpression(node)) {
      const isImportCall = node.expression.kind === ts.SyntaxKind.ImportKeyword;
      const isRequire = ts.isIdentifier(node.expression) && node.expression.text === 'require';
      const [argument] = node.arguments;
      if ((isImportCall || isRequire) && argument !== undefined) {
        add(specifierText(argument), 'runtime');
      }
    }
    node.forEachChild(visit);
  };
  visit(sourceFile);
  return edges;
}

export function runtimeImportEdges(fileName: string, source: string): string[] {
  return moduleEdges(fileName, source)
    .filter((edge) => edge.kind === 'runtime')
    .map((edge) => edge.specifier);
}
