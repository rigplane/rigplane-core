/**
 * MOR-1066 design-language x layout compatibility handshake. Pure
 * composition of the two manifests' own declarations — the mutation-kill
 * test at the bottom proves it structurally via a Proxy trap: touching any
 * property other than `layout.id` / `language.layoutCompatibility` throws.
 */
import { describe, it, expect } from 'vitest';
import { studioline, fieldline } from '../../languages/declarations';
import type { DesignLanguageManifest } from '../../languages/contract';
import { checkLayoutLanguageCompatibility } from '../compatibility';
import type { LayoutManifest } from '../contract';
import { validLayoutManifest } from './fixtures';

describe('the frozen v3 handshake pair (MOR-977 §4.4)', () => {
  it('fieldline x dual-receiver-cockpit is incompatible', () => {
    const layout = validLayoutManifest({ id: 'dual-receiver-cockpit' });
    const result = checkLayoutLanguageCompatibility(fieldline, layout);
    expect(result).toEqual({
      compatible: false,
      reason: expect.stringContaining('0.6 relative density'),
    });
  });

  it('studioline x dual-receiver-cockpit is compatible (no declaration = compatible)', () => {
    const layout = validLayoutManifest({ id: 'dual-receiver-cockpit' });
    expect(checkLayoutLanguageCompatibility(studioline, layout)).toEqual({ compatible: true });
  });

  // Kills: hardcoding the dual-receiver-cockpit id instead of reading
  // layout.id — any other layout id must also come back compatible with
  // studioline, since studioline declares no incompatibilities at all.
  it('studioline x any other layout id is compatible', () => {
    const layout = validLayoutManifest({ id: 'sdr-test' });
    expect(checkLayoutLanguageCompatibility(studioline, layout)).toEqual({ compatible: true });
  });

  // Kills: fieldline's incompatibility bleeding into an unrelated layout id.
  it('fieldline x an unrelated layout id is compatible', () => {
    const layout = validLayoutManifest({ id: 'sdr-test' });
    expect(checkLayoutLanguageCompatibility(fieldline, layout)).toEqual({ compatible: true });
  });
});

describe('structural purity (mutation-kill)', () => {
  // Kills: the function consulting anything other than layout.id and
  // language.layoutCompatibility (e.g. a capability check, sizing, tokens).
  it('touches only layout.id and language.layoutCompatibility — a Proxy trap on any other property throws', () => {
    const layoutTarget = validLayoutManifest({ id: 'dual-receiver-cockpit' });
    const layout = new Proxy(layoutTarget, {
      get(target, prop, receiver) {
        if (prop !== 'id') throw new Error(`unexpected access to layout.${String(prop)}`);
        return Reflect.get(target, prop, receiver);
      },
    });
    const language = new Proxy(fieldline, {
      get(target, prop, receiver) {
        if (prop !== 'layoutCompatibility') throw new Error(`unexpected access to language.${String(prop)}`);
        return Reflect.get(target, prop, receiver);
      },
    });
    expect(() => checkLayoutLanguageCompatibility(language as DesignLanguageManifest, layout as LayoutManifest))
      .not.toThrow();
  });
});
