import { describe, expect, it } from 'vitest';
import { parse } from 'svelte/compiler';
// @ts-ignore -- deliberately small parser-only Node ESM helper.
import { evaluateOrderedEffects } from '../../scripts/control-feedback-debt-evaluator.mjs';

const program = (source: string) => parse(`<script lang="ts">${source}</script>`, { modern: true }).instance!.content;
const facts = (source: string) => evaluateOrderedEffects(program(source), {
  isTracked: (node: { type?: string; name?: string }) => node?.type === 'Identifier' && node.name === 'props',
}).map((fact: { kind: string }) => fact.kind);

describe('ordered debt evaluator (MOR-1720)', () => {
  it('runs hoisted and const callables only when invoked', () => {
    expect(facts('call(); function call() { mutate(props); return props; } const later = () => mutate(props); later(); function idle() { mutate(props); }'))
      .toEqual(['escape', 'return', 'escape']);
  });

  it('unwraps parenthesized and TypeScript IIFEs without running idle closures', () => {
    expect(facts('const idle = () => mutate(props); ((value: unknown) => mutate(value))(props as unknown);'))
      .toEqual(['escape']);
  });

  it('uses defaults only for missing or undefined arguments, in parameter order', () => {
    expect(facts('function f(a, b = mutate(props), { x = mutate(props) } = {}) { return a; } f(1, 2, { x: 3 }); f(1, undefined, {});'))
      .toEqual(['escape', 'escape']);
  });

  it('keeps parameter shadowing while nested destructuring defaults see the closure', () => {
    expect(facts('function shadow(props, value = props) { return value; } function nested({ x = mutate(props) } = {}) {} shadow({}); nested({});'))
      .toEqual(['escape']);
  });

  it('orders nested calls, writes, member receivers, and tag substitutions', () => {
    expect(facts('outer(mutate(props), props.value = mutate(props)); props.method(mutate(props)); tag`${mutate(props)}`;'))
      .toEqual(['escape', 'escape', 'mutation', 'escape', 'escape', 'escape', 'escape', 'escape']);
  });

  it('reports recursive and mutual callable cycles without executing unrelated closures', () => {
    expect(facts('function a() { b(); } function b() { a(); } a(); const idle = () => mutate(props);'))
      .toEqual(['cycle']);
  });

  it('has no facts for unrelated code', () => {
    expect(facts('const add = (a: number, b: number) => a + b; add(1, 2);')).toEqual([]);
  });
});
