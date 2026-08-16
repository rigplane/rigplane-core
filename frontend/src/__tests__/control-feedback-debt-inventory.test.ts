import { describe, expect, it } from 'vitest';
// @ts-ignore -- the production inventory is an intentionally plain Node ESM script.
import { auditSvelteSource, assertShrinkOnly, scanRepository } from '../../scripts/control-feedback-debt-inventory.mjs';

const wrap = (markup: string, script = '') => `<script>${script}</script>${markup}`;
const scoped = (moduleScript: string, instanceScript: string, markup = '<input {...props}/>') => `<script module>${moduleScript}</script><script>${instanceScript}</script>${markup}`;
const debt = (source: string, file = 'src/semantic/Fixture.svelte') =>
  auditSvelteSource(file, source).filter((site: { policy: string }) => site.policy === 'radio-backed');

describe('AST-backed control feedback debt inventory (MOR-1713)', () => {
  it('resolves direct, barrel, multi-hop, and conditional ValueControl aliases', () => {
    const source = wrap(
      '<Direct label="A" value={a}/><Hop label="B" value={b}/><Maybe label="C" value={c}/><Mutable label="D"/><Assigned label="E"/><Extended label="F"/><Typed label="G"/>',
      `import Direct from '../components-v2/controls/value-control/ValueControl.svelte';
       import { ValueControl as Barrel } from '../components-v2/controls/value-control';
       import { ValueControl as Extended } from '../components-v2/controls/value-control/index.js';
       import { ValueControl as Typed } from '../components-v2/controls/value-control/index.ts';
       const First = Barrel; const Hop = First; const Other = false;
       const Maybe = Other ? Hop : widget; let Mutable = Barrel; let Assigned = widget;
       Assigned = Other ? widget : Extended; let a=0,b=0,c=0;`,
    );
    expect(debt(source).map((site: { identity: string }) => site.identity)).toHaveLength(7);
  });

  it('parses module imports and ignores same-named components without a valid import', () => {
    const valid = `<script context="module">import { ValueControl as VC } from '../components-v2/controls/value-control';</script><VC label="A" value={a}/>`;
    expect(debt(valid)).toHaveLength(1);
    expect(debt(wrap('<ValueControl label="fake"/>', 'const ValueControl = widget;'))).toEqual([]);
    expect(debt(wrap('<Wrong label="fake"/>', "import { ValueControl as Wrong } from '../controls/not-value-control/index.js';"))).toEqual([]);
    expect(debt(wrap('<Wrong label="fake"/>', "import Wrong from '../other/ValueControl.svelte';"))).toEqual([]);
    expect(debt(wrap('<Wrong label="fake"/>', "import { ValueControl as Wrong } from '../other/value-control/index.js';"))).toEqual([]);
  });

  it('keeps module and instance bindings, object flow, and imports lexically isolated', () => {
    expect(debt(scoped("const props={type:'button'}", "const props={type:'range'}"))).toHaveLength(1);
    expect(debt(scoped("const props={type:'range'}", "const props={type:'button'}"))).toEqual([]);
    expect(debt(scoped("const props={type:'button'}; props.type='range'", "const props={type:'button'}"))).toEqual([]);
    expect(debt(scoped("const props={type:'button'}", "const props={type:'button'}; props.type='range'"))).toHaveLength(1);
    const canonical = "import VC from '../components-v2/controls/value-control/ValueControl.svelte'";
    const namesake = "import VC from '../other/ValueControl.svelte'";
    const imported = [
      debt(scoped(canonical, namesake, '<VC label="shadowed"/>')).length,
      debt(scoped(namesake, canonical, '<VC label="instance"/>')).length,
    ];
    expect(imported).toEqual([0, 1]);
  });

  it('conservatively discovers literal, expression, dynamic, and svelte:element ranges', () => {
    const source = wrap(
      `<input type="range" value={a}/><input type={'range'} value={b}/>
       <input type={kind} value={c}/><svelte:element this={tag} type={kind} value={d}/>
       <input type="button"/><svelte:element this="div" type="range"/>`,
      `let kind='number', tag='div', a=0,b=0,c=0,d=0;`,
    );
    expect(debt(source)).toHaveLength(4);
  });

  it('uses source-order last-writer semantics across spreads and attributes', () => {
    const source = wrap(
      `<input {...dynamic} type="button" value={a}/>
       <input type="button" {...dynamic} feedback-policy="radio-backed" value={b}/>
       <input {...rangeProps} value={c}/>
       <input {...dynamic} type="range" feedback-policy="feedback-integrated" value={d}/>
       <input type="range" feedback-policy="future" {...integrated} value={e}/>
       <input type="range" {...integrated} feedback-policy="radio-backed" value={f}/>` ,
      `const rangeProps={type:'range'}, integrated={'feedback-policy':'feedback-integrated'}; let dynamic={},a=0,b=0,c=0,d=0,e=0,f=0;`,
    );
    const sites = auditSvelteSource('src/semantic/Fixture.svelte', source);
    expect(sites.map((site: { value: string }) => site.value)).toEqual(['b', 'c', 'd', 'e', 'f']);
    expect(debt(source).map((site: { value: string }) => site.value)).toEqual(['b', 'c', 'f']);
  });

  it('handles computed spread keys conservatively in source order', () => {
    const script = `const typeKey='type', staticType={['type']:'range'}, integrated={[typeKey]:'range',['feedback-policy']:'feedback-integrated'};
      let dynamicKey='type', unknownComputed={[dynamicKey]:'range'},a=0,b=0,c=0;`;
    const source = wrap(`<input {...staticType} value={a}/><input {...integrated} value={b}/>
      <input {...unknownComputed} feedback-policy="radio-backed" value={c}/><input {...unknownComputed} type="button"/>`, script);
    expect(auditSvelteSource('src/semantic/Fixture.svelte', source).map((site: { value: string }) => site.value)).toEqual(['a', 'b', 'c']);
    expect(debt(source).map((site: { value: string }) => site.value)).toEqual(['a', 'c']);
    expect(() => debt(wrap('<input type="range" feedback-policy="radio-backed" {...unknownComputed}/>', script))).toThrow(/static feedback policy/);
  });

  it('applies spread-object mutations and poisons unknown writes or escapes', () => {
    const script = `const aProps={type:'button'}, bProps={type:'button'}, pProps={type:'range','feedback-policy':'radio-backed'}, key='type';
      const aAlias=aProps,bAlias=bProps,pAlias=pProps;
      aAlias.type='range'; bAlias[key]='range'; pAlias.feedbackPolicy='feedback-integrated'; let a=0,b=0,c=0;`;
    const source = wrap('<input {...aAlias} value={a}/><input {...bAlias} value={b}/><input {...pAlias} value={c}/>', script);
    expect(auditSvelteSource('src/semantic/Fixture.svelte', source).map((site: { value: string }) => site.value)).toEqual(['a', 'b', 'c']);
    expect(debt(source).map((site: { value: string }) => site.value)).toEqual(['a', 'b']);
    const unknown = `const props={type:'button'}; let key='type'; props[key]='range';`;
    expect(() => debt(wrap('<input {...props}/>', unknown))).toThrow(/static feedback policy/);
    expect(debt(wrap('<input {...props} type="button" feedback-policy="radio-backed"/>', unknown))).toEqual([]);
    expect(() => debt(wrap('<input {...props}/>', `const props={type:'button'}, alias=props; mutate(alias);`))).toThrow(/static feedback policy/);
  });

  it('fails closed for TypeScript-wrapped mutation receivers and call escapes', () => {
    const typed = (script: string) => debt(`<script lang="ts">const props={type:'button'}, alias=props; ${script}; let a=0;</script><input {...props} value={a}/>`);
    expect(typed(`(props as Record<string, unknown>).type='range'`).map((site: { value: string }) => site.value)).toEqual(['a']);
    expect(() => typed(`mutate(alias as Record<string, unknown>)`)).toThrow(/static feedback policy/);
    expect(() => typed(`mutate(alias!)`)).toThrow(/static feedback policy/);
  });

  it('rejects unknown or dynamic policies and accepts the closed vocabulary', () => {
    expect(() => debt(wrap('<input type="range" feedback-policy="future"/>'))).toThrow(/unknown feedback policy/);
    expect(() => debt(wrap('<input type="range" feedback-policy={policy}/>', `let policy='radio-backed'`))).toThrow(/static feedback policy/);
    expect(debt(wrap('<input type="range" feedback-policy="feedback-integrated"/>'))).toEqual([]);
    expect(debt(wrap('<input type="range" feedback-policy="radio-backed"/>'))).toHaveLength(1);
  });

  it('exempts only exact MAIN and SUB browser-local gain identities', () => {
    const local = (channel: 'MAIN' | 'SUB', value: string) =>
      `<input type="range" aria-label="${channel} gain in decibels" value={${value}} feedback-policy="local-resource"/>`;
    const file = 'src/components-v2/panels/AudioRoutingControl.svelte';
    expect(debt(wrap(local('MAIN', 'mainGainDb') + local('SUB', 'subGainDb')), file)).toEqual([]);
    expect(debt(wrap(local('MAIN', 'mainGainDb')), 'src/components-v2/panels/../panels/AudioRoutingControl.svelte')).toEqual([]);
    expect(() => debt(wrap(local('MAIN', 'mainGainDb')), `nested/${file}`)).toThrow(/local-resource.*not allowed/);
    expect(debt(wrap(local('MAIN', 'mainGainDb').replace(' feedback-policy="local-resource"', '')), file)).toEqual([]);
    expect(() => debt(wrap(local('MAIN', 'thirdGainDb')), file)).toThrow(/local-resource.*not allowed/);
    const third = wrap(local('MAIN', 'mainGainDb') + local('SUB', 'subGainDb') + '<input type="range" value={monitorGain}/>');
    expect(debt(third, file)).toHaveLength(1);
  });

  it('allows the frozen inventory to shrink but never grow', () => {
    expect(assertShrinkOnly([{ identity: 'a' }], new Set(['a', 'b']))).toEqual(['a']);
    expect(() => assertShrinkOnly([{ identity: 'a' }, { identity: 'c' }], new Set(['a', 'b']))).toThrow(/grew.*c/);
    expect(() => assertShrinkOnly(scanRepository())).not.toThrow();
  });
});
