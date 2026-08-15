import { describe, expect, it } from 'vitest';
import { parse } from 'svelte/compiler';
// @ts-ignore Node ESM utility consumed later by the inventory flow.
import { boundNames, collectObjectFlow, unwrapExpression } from '../../scripts/control-feedback-debt-ast.mjs';

const ast = (s: string): any => parse(`<script lang="ts">${s}</script>`, { modern: true }).instance!.content;
const events = (s: string) => collectObjectFlow(ast(s)).get('props') ?? [];
const shape = (s: string) => events(s).map(({ key, poison }: any) => [key, poison]);

describe('control-feedback AST semantics (MOR-1716)', () => {
  it('normalizes emitted wrappers and poisons unsupported tracked wrappers', () => {
    const n: any = ast('const props = {}; (props as object).type = "x";').body[1].expression.left.object;
    expect(unwrapExpression(n).node.name).toBe('props');
    expect(shape('const props={}; (props<string>).type="x";')).toEqual([['type', false]]);
  });
  it('finds every nested binding-pattern identifier', () => {
    expect(boundNames({ type: 'ObjectPattern', properties: [{ value: { type: 'ArrayPattern', elements: [{ type: 'Identifier', name: 'a' }, { type: 'RestElement', argument: { type: 'AssignmentPattern', left: { type: 'Identifier', name: 'b' } } }] } }] })).toEqual(['a', 'b']);
  });
  it('keeps safe writes ordered but fails closed for aliases and mutations', () => {
    expect(shape('const props={}; props.type="x"; props["feedback-policy"]="y"; let a; a=props; a.type="z";')).toEqual([['type', false], ['feedback-policy', false], [null, true]]);
    expect(shape('const props={}; props.type++; delete props.type; props[key]="x";')).toEqual([[null, true], [null, true], [null, true]]);
  });
  it('attributes tracked RHS escapes even through another tracked container', () => {
    expect(shape('const props={}; const holder={}; holder.value=props;')).toEqual([[null, true]]);
  });
  it('keeps RHS effects before their enclosing write, including sequences', () => {
    expect(shape('const props={}; props.type=(props.feedbackPolicy="y", f(props));')).toEqual([['feedback-policy', false], [null, true], ['type', false]]);
  });
  it('poisons nested argument containers and root method receivers', () => {
    expect(shape('const props={}; f({...props}); holder.value=props; f(props); props`tag`; props.items.push(1); function surface(){return props}')).toEqual([[null, true], [null, true], [null, true], [null, true], [null, true]]);
  });
  it('poisons default-parameter escapes but keeps known primitive keys safe', () => {
    expect(shape('const props={}; function f(value=props){} f(); props[0]="x"; props[key]="y";')).toEqual([[null, true], [null, true]]);
  });
  it('uses parameter scope order and recursively traverses binding defaults', () => {
    expect(shape('const props={}; function shadow(props, value=props){} shadow(); function escape({x=mutate(props)}){} escape({});')).toEqual([[null, true]]);
  });
  it('declares function, catch, block and loop bindings lexically', () => {
    expect(shape('const props={}; (function props(){ props.type="x"; })(); try{}catch({props}){props.type="x"} {let props={};props.type="x"} for(const {props} of [])props.type="x";')).toEqual([]);
  });
  it('hoists var to the nearest function or program scope', () => {
    expect(shape('const props={}; function f(){ { var props={}; } props.type="x"; }')).toEqual([]);
    expect(shape('const props={}; function f(){ props.type="x"; for(;false;){var props={}} } f();')).toEqual([]);
  });
  it('only executes invoked function surfaces and template substitutions', () => {
    expect(shape('const props={}; function idle(){return props} (()=>{props.type="x"; return props})() ; tag`${props}`;')).toEqual([['type', false], [null, true], [null, true]]);
  });
  it('keeps all known primitive computed aliases safe', () => {
    expect(shape('const props={}; const n=0, b=true, g=1n, z=null; props[n]="x"; props[b]="x"; props[g]="x"; props[z]="x"; props[unknown]="x";')).toEqual([[null, true]]);
  });
  it('does not report unrelated objects, cycles, or shadowed roots', () => {
    expect(shape('const props={}; const other={}; other.type="x"; { const props={}; props.type="y" }')).toEqual([]);
    expect(collectObjectFlow(ast('const a=b;const b=a;a.type="x";')).size).toBe(0);
  });
});
