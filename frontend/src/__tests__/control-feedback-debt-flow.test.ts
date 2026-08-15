import { describe, expect, it } from 'vitest';
import { parse } from 'svelte/compiler';
// @ts-ignore -- intentionally plain Node ESM shared by the inventory script.
import { collectObjectFlow, unwrapIdentifier } from '../../scripts/control-feedback-debt-flow.mjs';

const program = (source: string) => parse(`<script lang="ts">${source}</script>`, { modern: true }).instance!.content;
const flow = (source: string, root = 'props') => collectObjectFlow(program(source)).get(root) ?? [];
const summary = (source: string) => flow(source).map(({ key, value, poison }: any) => ({
  key, value: value?.value ?? null, poison,
}));

describe('control feedback object flow (MOR-1715)', () => {
  it('unwraps only the supported identifier wrappers', () => {
    const identifier = { type: 'Identifier', name: 'props' };
    let wrapped: any = identifier;
    for (const type of ['TSAsExpression', 'TSTypeAssertion', 'TSNonNullExpression', 'TSSatisfiesExpression', 'ParenthesizedExpression', 'ChainExpression']) {
      wrapped = { type, expression: wrapped };
    }
    expect(unwrapIdentifier(wrapped)).toBe(identifier);
    expect(unwrapIdentifier({ type: 'SequenceExpression', expressions: [identifier] })).toBeNull();
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
      const props = {}; let mutable = props; mutable.type = 'range';
      const alias = props; alias = {}; alias.type = 'number';
    `)).toEqual([{ key: null, value: null, poison: true }]);
  });

  it('fails closed for cyclic and unsupported aliases', () => {
    expect(collectObjectFlow(program(`const a = b; const b = a; a.type = 'range';`)).size).toBe(0);
    expect(summary(`const props = {}; const alias = (0, props); alias.type = 'range';`)).toEqual([
      { key: null, value: null, poison: true },
    ]);
  });
});
