import { describe, expect, it } from 'vitest';
import { parse } from 'svelte/compiler';
// @ts-ignore -- intentionally plain Node ESM shared by the inventory script.
import { collectObjectFlow, collectSvelteObjectFlows, unwrapIdentifier } from '../../scripts/control-feedback-debt-flow.mjs';
// @ts-ignore -- compatibility identity must remain delegated to the shared AST helper.
import { collectObjectFlow as collectSharedObjectFlow } from '../../scripts/control-feedback-debt-ast.mjs';

const program = (source: string) => parse(`<script lang="ts">${source}</script>`, { modern: true }).instance!.content;
const flow = (source: string, root = 'props') => collectObjectFlow(program(source)).get(root) ?? [];
const summary = (source: string) => flow(source).map(({ key, value, poison }: any) => ({
  key, value: value?.value ?? null, poison,
}));
const linked = (moduleSource: string, instanceSource: string) => {
  const parsed: any = parse(`<script module lang="ts">${moduleSource}</script><script lang="ts">${instanceSource}</script>`, { modern: true });
  return collectSvelteObjectFlows(parsed.module.content, parsed.instance.content);
};
const eventSummary = (events: any[] = []) => events.map(({ key, value, poison }: any) => ({
  key, value: value?.value ?? null, poison,
}));

describe('control feedback object flow (MOR-1715)', () => {
  it('delegates collection to the shared AST implementation', () => {
    expect(collectObjectFlow).toBe(collectSharedObjectFlow);
  });

  it('unwraps supported wrappers, including TypeScript instantiation', () => {
    const identifier = { type: 'Identifier', name: 'props' };
    let wrapped: any = identifier;
    for (const type of ['TSAsExpression', 'TSTypeAssertion', 'TSNonNullExpression', 'TSSatisfiesExpression', 'ParenthesizedExpression', 'ChainExpression', 'TSInstantiationExpression']) {
      wrapped = { type, expression: wrapped };
    }
    expect(unwrapIdentifier(wrapped)).toBe(identifier);
    expect(unwrapIdentifier({ type: 'SequenceExpression', expressions: [identifier] })).toBeNull();
    expect(summary(`const props = {}; (props<string>).type = 'range';`)).toEqual([
      { key: 'type', value: 'range', poison: false },
    ]);
  });

  it('reports direct, computed, alias and multihop writes in source order', () => {
    expect(summary(`
      const props = {}; const alias = props; const hop = alias; const key = 'type';
      props.type = 'range'; hop[key] = 'number'; alias['feedback-policy'] = 'feedback-integrated';
    `)).toEqual([
      { key: 'type', value: 'range', poison: false },
      { key: 'type', value: 'number', poison: false },
      { key: 'feedback-policy', value: 'feedback-integrated', poison: false },
    ]);
  });

  it('resolves emitted TypeScript wrappers for writes and call escapes', () => {
    expect(summary(`
      const props = {}; const alias = props satisfies Record<string, unknown>;
      (props as Record<string, unknown>).type = 'range';
      (<Record<string, unknown>>alias)['feedback-policy'] = 'radio-backed';
      mutate(props!); mutate(alias as Record<string, unknown>);
    `)).toEqual([
      { key: 'type', value: 'range', poison: false },
      { key: 'feedback-policy', value: 'radio-backed', poison: false },
      { key: null, value: null, poison: true },
      { key: null, value: null, poison: true },
    ]);
  });

  it('poisons unknown computed writes and calls through an alias', () => {
    expect(summary(`
      const props = {}; const alias = props; let key = 'type';
      alias[key] = 'range'; mutate(alias);
    `)).toEqual([
      { key: null, value: null, poison: true },
      { key: null, value: null, poison: true },
    ]);
  });

  it('keeps unrelated and shadowed objects separate', () => {
    expect(summary(`
      const props = {}; const other = {}; other.type = 'range';
      { const props = {}; const alias = props; alias.type = 'number'; mutate(alias); }
      for (const props of []) { props.type = 'range'; }
    `)).toEqual([]);
  });

  it('does not trust mutable or reassigned aliases', () => {
    expect(summary(`
      const props = {}; let late; late = props; late.type = 'range';
      let mutable = props; mutable.type = 'number';
      const alias = props; alias = {}; alias.type = 'toggle';
    `)).toEqual([
      { key: null, value: null, poison: true },
      { key: 'type', value: 'number', poison: false },
      { key: null, value: null, poison: true },
      { key: 'type', value: 'toggle', poison: false },
    ]);
  });

  it('fails closed for updates, deletes, nested escapes, and method receivers', () => {
    expect(summary(`
      const props = {}; props.type++; delete props.type;
      sink({ nested: [props] }); props.items.push(1);
    `)).toEqual([
      { key: null, value: null, poison: true },
      { key: null, value: null, poison: true },
      { key: null, value: null, poison: true },
      { key: null, value: null, poison: true },
    ]);
  });

  it('declares named function-expression and destructured function/loop bindings', () => {
    expect(summary(`
      const props = {};
      (function props() { props.type = 'range'; })();
      function shadow({ props }: { props: Record<string, unknown> }) { props.type = 'number'; }
      for (const { props } of []) { props.type = 'toggle'; }
    `)).toEqual([]);
  });

  it('keeps cycles isolated and lets the shared evaluator resolve sequence aliases', () => {
    expect(collectObjectFlow(program(`const a = b; const b = a; a.type = 'range';`)).size).toBe(0);
    expect(summary(`const props = {}; const alias = (0, props); alias.type = 'range';`)).toEqual([
      { key: 'type', value: 'range', poison: false },
    ]);
  });

  it('links unshadowed module identities to ordered instance mutations', () => {
    const scopes = linked(
      `const props = {}; props.type = 'button';`,
      `const alias = props; props.type = 'range'; alias.feedbackPolicy = 'radio-backed';`,
    );
    expect(eventSummary(scopes.module.get('props'))).toEqual([
      { key: 'type', value: 'button', poison: false },
      { key: 'type', value: 'range', poison: false },
      { key: 'feedback-policy', value: 'radio-backed', poison: false },
    ]);
    expect(scopes.instance.get('props')).toBe(scopes.module.get('props'));
  });

  it('preserves poison and recovery order across wrappers and nested escapes', () => {
    const scopes = linked(
      `const props = {};`,
      `let key = 'type'; (props as Record<string, unknown>)[key] = 'range';
       mutate(props as Record<string, unknown>); sink({ nested: [props!] });
       (props as Record<string, unknown>).type = 'number';
       props.items.push(1);`,
    );
    expect(eventSummary(scopes.instance.get('props'))).toEqual([
      { key: null, value: null, poison: true },
      { key: null, value: null, poison: true },
      { key: null, value: null, poison: true },
      { key: 'type', value: 'number', poison: false },
      { key: null, value: null, poison: true },
    ]);
  });

  it('keeps module and instance shadows isolated in both directions', () => {
    const instanceRange = linked(
      `const props = {}; props.type = 'button';`,
      `const props = {}; props.type = 'range';`,
    );
    expect(eventSummary(instanceRange.module.get('props'))).toEqual([
      { key: 'type', value: 'button', poison: false },
    ]);
    expect(eventSummary(instanceRange.instance.get('props'))).toEqual([
      { key: 'type', value: 'range', poison: false },
    ]);

    const moduleRange = linked(
      `const props = {}; props.type = 'range';`,
      `const props = {}; props.type = 'button';`,
    );
    expect(eventSummary(moduleRange.module.get('props'))).toEqual([
      { key: 'type', value: 'range', poison: false },
    ]);
    expect(eventSummary(moduleRange.instance.get('props'))).toEqual([
      { key: 'type', value: 'button', poison: false },
    ]);
  });
});
