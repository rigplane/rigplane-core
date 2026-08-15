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

  it('keeps satisfies, chain, optional, assertion and instantiation wrappers executable', () => {
    expect(facts('const once = ((() => mutate(props)) satisfies unknown)!; (once as unknown)(); ((() => mutate(props)) as unknown)?.(); sink<string>(props);'))
      .toEqual(['escape', 'escape', 'escape']);
  });

  it('eagerly visits every child after a tracked child', () => {
    expect(facts('first(props) + second(props); [first(props), second(props)]; ({ a: first(props), b: second(props) });'))
      .toEqual(['escape', 'escape', 'escape', 'escape', 'escape', 'escape']);
  });

  it('uses defaults only for missing or undefined arguments, in parameter order', () => {
    expect(facts('function f(a, b = mutate(props), { x = mutate(props) } = {}) { return a; } f(1, 2, { x: 3 }); f(1, undefined, {});'))
      .toEqual(['escape', 'escape']);
  });

  it('keeps parameter shadowing while nested destructuring defaults see the closure', () => {
    expect(facts('function shadow(props, value = props) { return value; } function nested({ x = mutate(props) } = {}) {} shadow({}); nested({});'))
      .toEqual(['escape']);
  });

  it('preserves supplied array, object and rest values without running their defaults', () => {
    expect(facts('function f([x = mutate(props), ...rest], { y = mutate(props), ...more }) { use(rest); use(more); } f([1, props], { y: 1, z: props });'))
      .toEqual(['escape', 'escape']);
  });

  it('recursively binds nested destructuring defaults', () => {
    expect(facts('function f({ nested: [value = mutate(props)] = [] }) {} f({ nested: [1] }); f({});')).toEqual(['escape']);
  });

  it('retains a spread array value for a rest binding', () => {
    expect(facts('function f([head, ...rest]) { use(rest); } f([1, ...[props]]);')).toEqual(['escape']);
  });

  it('uses void zero and global undefined, but never a shadowed undefined, for defaults', () => {
    expect(facts('function f(value = mutate(props)) {} f(void 0); f(undefined); const undefined = props; f(undefined);'))
      .toEqual(['escape', 'escape']);
  });

  it('orders nested calls, writes, member receivers, and tag substitutions', () => {
    expect(facts('outer(mutate(props), props.value = mutate(props)); props.method(mutate(props)); tag`${mutate(props)}`;'))
      .toEqual(['escape', 'escape', 'mutation', 'escape', 'escape', 'escape', 'escape', 'escape']);
  });

  it('reports recursive and mutual callable cycles without executing unrelated closures', () => {
    expect(facts('function a() { b(); } function b() { a(); } a(); const idle = () => mutate(props);'))
      .toEqual(['cycle']);
  });

  it('binds a named function-expression self identity and stops after completed branches', () => {
    expect(facts('const direct = function self() { return self(); }; direct(); function done(flag) { if (flag) return props; else return props; mutate(props); } done(true);'))
      .toEqual(['cycle', 'return', 'return']);
  });

  it('preserves known undefined and primitive reachability without evaluating dead effects', () => {
    expect(facts('if (false) mutate(props); !true && mutate(props); ~0 && mutate(props); 1 === 2 && mutate(props); void 0 ?? mutate(props); undefined && mutate(props); false || mutate(props);'))
      .toEqual(['escape', 'escape', 'escape']);
  });

  it('short-circuits known-null optional operations before arguments or computed keys', () => {
    expect(facts('const nil = null; nil?.(mutate(props)); nil?.[mutate(props)]; nil?.field;'))
      .toEqual([]);
  });

  it('keeps optional-member short-circuiting through its chain but not parentheses', () => {
    expect(facts('const nil = null; nil?.method(mutate(props)); nil?.[mutate(props)](sink(props));'))
      .toEqual([]);
    expect(facts('const nil = null; (nil?.method)(mutate(props));'))
      .toEqual(['escape', 'escape']);
  });

  it('carries optional short-circuiting through deeper chain members and calls', () => {
    expect(facts('const nil = null; nil?.a.b(mutate(props)); nil?.a().b(mutate(props)); nil?.a.b?.(mutate(props));'))
      .toEqual([]);
  });

  it('retains undefined callable and aggregate values through every reachable consumer', () => {
    expect(facts('function empty() {} function defaults(value = mutate(props)) {} defaults(empty());'))
      .toEqual(['escape']);
    expect(facts('function bare() { return; } bare()?.(mutate(props));')).toEqual([]);
    expect(facts('const nil = null; (nil?.a).b(mutate(props));')).toEqual([]);
    expect(facts('function object({ value }) { sink(value); } object(props);')).toEqual(['escape']);
    expect(facts('function array([value]) { sink(value); } array(props);')).toEqual(['escape']);
    expect(facts('sink({ ...props });')).toEqual(['escape']);
    expect(facts('function slots(first, second) { sink(first); sink(second); } slots(...props);'))
      .toEqual(['escape', 'escape']);
  });

  it('keeps rest, spread, abrupt member, and throw completion execution-ordered', () => {
    expect(facts('function array([first, ...rest]) { sink(rest); } array([props, 1]);')).toEqual([]);
    expect(facts('function object({ first, ...rest }) { sink(rest); } object({ first: props, safe: 1 });')).toEqual([]);
    expect(facts('const nil = null; (nil?.a)[mutate(props)];')).toEqual(['escape']);
    expect(facts('function slots(first, second) { sink(first); sink(second); } slots(0, ...props);')).toEqual(['escape']);
    expect(facts('function direct() { throw mutate(props); mutate(props); } direct();')).toEqual(['escape']);
    expect(facts('function known() { if (true) throw mutate(props); mutate(props); } known();')).toEqual(['escape']);
    expect(facts('function both(flag) { if (flag) throw mutate(props); else throw mutate(props); mutate(props); } both(props);'))
      .toEqual(['escape', 'escape']);
  });

  it('preserves abrupt completion through calls, returns, and expressions', () => {
    expect(facts('function boom() { throw null; } boom(); sink(props);')).toEqual([]);
    expect(facts('function boom() { throw null; } sink(boom(), mutate(props));')).toEqual([]);
    expect(facts('function boom() { throw props; } function outer() { return boom(); } outer();')).toEqual([]);
    expect(facts('const nil = null; nil.value; mutate(props);')).toEqual([]);
  });

  it('has no facts for unrelated code', () => {
    expect(facts('const add = (a: number, b: number) => a + b; add(1, 2);')).toEqual([]);
  });
});
