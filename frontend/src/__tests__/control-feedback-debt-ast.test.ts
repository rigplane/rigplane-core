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
    expect(shape('const props={}; function f(value=props){sink(value)} f(); props[0]="x"; props[key]="y";')).toEqual([[null, true], [null, true]]);
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
  it('uses the ordered evaluator for wrapped const callables, defaults, and cycles', () => {
    expect(shape('const props={}; const call=({x=mutate(props)}={})=>{}; (call as unknown)();')).toEqual([[null, true]]);
    expect(shape('const props={}; function a(){b()} function b(){a()} a();')).toEqual([]);
  });
  it('lets the ordered evaluator own callable, default, completion, self, and cycle semantics', () => {
    expect(shape('const props={}; const idle=()=>mutate(props);')).toEqual([]);
    expect(shape('const props={}; function f(value=mutate(props)){} f(1);')).toEqual([]);
    expect(shape('const props={}; function stop(){return;props.type="x"} stop();')).toEqual([]);
    expect(shape('const props={}; const call=function props(){props.type="x"}; call();')).toEqual([]);
    expect(shape('const props={}; function a(){b()} function b(){props.type="x";a()} a();')).toEqual([['type', false], [null, true]]);
  });
  it('preserves exposed-root provenance through evaluator bindings and returns', () => {
    expect(shape('const props={}; function f(x){x.type="x"} f(props);')).toEqual([['type', false]]);
    expect(shape('const props={}; function f(x){mutate(x)} f(props);')).toEqual([[null, true]]);
    expect(shape('const props={}; function object({x}){x.type="x"} function array([x]){x.type="x"} function rest(...xs){xs[0].type="x"} object(props);array(props);rest(props);')).toEqual([['type', false], ['type', false], ['type', false]]);
    expect(shape('const props={}; function supplied(x=props){x.type="x"} supplied(props);')).toEqual([['type', false]]);
    expect(shape('const props={}; function returned(){return props} const x=returned();x.type="x";')).toEqual([[null, true], ['type', false]]);
    expect(shape('const props={}; function f(x){x.feedbackPolicy="y"} props.type=(f(props),"x");')).toEqual([['feedback-policy', false], ['type', false]]);
    expect(shape('const props={}; function f(x){x.type="x"} f(props);f(props);')).toEqual([['type', false], ['type', false]]);
  });
  it('keeps recursive provenance conservative without walking callable bodies', () => {
    expect(shape('const props={}; function f(x){f(x);x.type="x"} f(props);')).toEqual([[null, true], ['type', false]]);
    expect(shape('const props={}; function f({x}){f({x});x.type="x"} f(props);')).toEqual([[null, true], ['type', false]]);
    expect(shape('const props={}; function f([x]){f([x]);x.type="x"} f(props);')).toEqual([[null, true], ['type', false]]);
    expect(shape('const props={}; function f(...xs){f(...xs);xs[0].type="x"} f(props);')).toEqual([[null, true], ['type', false]]);
    expect(shape('const props={}; function f(x=props){f(x);x.type="x"} f();')).toEqual([[null, true], ['type', false]]);
    expect(shape('const props={}; function a(x){b(x)} function b(y){a(y)} a(props);')).toEqual([[null, true]]);
    expect(shape('const props={}; function dead(x){return;x.type="x"} if(false)dead(props);')).toEqual([]);
  });
  it('poisons dynamic container RHS escapes for every bound provenance shape', () => {
    expect(shape('const props={}; let holder={}; function f(x){holder.value=x} f(props);')).toEqual([[null, true]]);
    expect(shape('const props={}; let holder={}; function f({x}){holder.value=x} function g([x]){holder.value=x} function h(...xs){holder.value=xs[0]} function d(x=props){holder.value=x} f(props);g(props);h(props);d(props);')).toEqual([[null, true], [null, true], [null, true], [null, true]]);
    expect(shape('const props={}; let holder={}; function returned(){return props} holder.value=returned();')).toEqual([[null, true], [null, true]]);
  });
  it('keeps all known primitive computed aliases safe', () => {
    expect(shape('const props={}; const n=0, b=true, g=1n, z=null; props[n]="x"; props[b]="x"; props[g]="x"; props[z]="x"; props[unknown]="x";')).toEqual([[null, true]]);
  });
  it('does not report unrelated objects, cycles, or shadowed roots', () => {
    expect(shape('const props={}; const other={}; other.type="x"; { const props={}; props.type="y" }')).toEqual([]);
    expect(collectObjectFlow(ast('const a=b;const b=a;a.type="x";')).size).toBe(0);
  });
  it('keeps related cycle provenance local and unrelated recursion invisible', () => {
    expect(shape('const props={}; function direct(x){x.type="x";direct(x)} direct(props);')).toEqual([['type', false], [null, true]]);
    expect(shape('const props={}; function a(x){x.type="x";b(x)} function b(y){a(y)} a(props);')).toEqual([['type', false], [null, true]]);
    expect(shape('const props={}; props.type="x"; function loop(){loop()} loop(); props.feedbackPolicy="y";')).toEqual([['type', false], ['feedback-policy', false]]);
  });
  it('distinguishes parameter targets from mutable-container RHS escapes', () => {
    expect(shape('const props={}; let holder={}; function f(x){x.type="x";holder.value=x} f(props);')).toEqual([['type', false], [null, true]]);
    expect(shape('const props={}; let holder={}; function f(x){holder.type=x} f(props);')).toEqual([[null, true]]);
  });
  it('maps returned and spread aliases back to the exposed root', () => {
    expect(shape('const props={}; function get(){return props} get().type="x";')).toEqual([[null, true], ['type', false]]);
    expect(shape('const props={}; function f(x){x.type="x"} f({...props});f([...props]);')).toEqual([['type', false], ['type', false]]);
    expect(shape('const props={}; function f({nested:{...rest}}){rest.type="x"} f({nested:{...props}});')).toEqual([['type', false]]);
  });
  it('preserves repeated default deletes in exact evaluator order', () => {
    expect(shape('const props={}; function f(x=props){delete x.type} f();f();delete props.type;delete props.type;')).toEqual([[null, true], [null, true], [null, true], [null, true]]);
  });
});
