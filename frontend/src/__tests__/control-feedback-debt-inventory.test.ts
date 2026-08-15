import { describe, expect, it } from 'vitest';
// @ts-ignore -- the production inventory is an intentionally plain Node ESM script.
import { auditSvelteSource, assertShrinkOnly, scanRepository } from '../../scripts/control-feedback-debt-inventory.mjs';

const wrap = (markup: string, script = '') => `<script>${script}</script>${markup}`;
const debt = (source: string, file = 'src/semantic/Fixture.svelte') =>
  auditSvelteSource(file, source).filter((site: { policy: string }) => site.policy === 'radio-backed');

describe('AST-backed control feedback debt inventory (MOR-1713)', () => {
  it('resolves direct, barrel, multi-hop, and conditional ValueControl aliases', () => {
    const source = wrap(
      '<Direct label="A" value={a}/><Hop label="B" value={b}/><Maybe label="C" value={c}/>',
      `import Direct from '../controls/value-control/ValueControl.svelte';
       import { ValueControl as Barrel } from '../controls/value-control';
       const First = Barrel; const Hop = First; const Other = false;
       const Maybe = Other ? Hop : widget; let a=0,b=0,c=0;`,
    );
    expect(debt(source).map((site: { identity: string }) => site.identity)).toHaveLength(3);
  });

  it('parses module imports and ignores same-named components without a valid import', () => {
    const valid = `<script context="module">import { ValueControl as VC } from '../controls/value-control';</script><VC label="A" value={a}/>`;
    expect(debt(valid)).toHaveLength(1);
    expect(debt(wrap('<ValueControl label="fake"/>', 'const ValueControl = widget;'))).toEqual([]);
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
