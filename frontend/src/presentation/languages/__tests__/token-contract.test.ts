/**
 * MOR-1072 token contract: required token groups, RX/TX symmetry
 * (MOR-1231), mandatory focus ring (MOR-1232), mandatory tabular figures.
 */
import { describe, it, expect } from 'vitest';
import { REQUIRED_TOKEN_GROUPS, validateManifest, DesignLanguageValidationError, type DesignLanguageManifest } from '../contract';
import { validManifest } from './fixtures';

describe('required token groups', () => {
  it('lists exactly the eight groups the contract mandates', () => {
    expect(REQUIRED_TOKEN_GROUPS).toEqual([
      'typography', 'geometry', 'meters', 'frequency', 'motion', 'focusRing', 'rx', 'tx',
    ]);
  });

  it('accepts a manifest that declares every required group', () => {
    expect(() => validateManifest(validManifest())).not.toThrow();
  });

  it.each(REQUIRED_TOKEN_GROUPS)('fails validation when the "%s" group is omitted', (group) => {
    const manifest = validManifest();
    const tokens = { ...manifest.tokens } as Record<string, unknown>;
    delete tokens[group];
    const broken: DesignLanguageManifest = { ...manifest, tokens: tokens as unknown as DesignLanguageManifest['tokens'] };
    expect(() => validateManifest(broken)).toThrow(DesignLanguageValidationError);
    expect(() => validateManifest(broken)).toThrow(new RegExp(group));
  });

  it('requires rx and tx to be symmetric token groups (MOR-1231) — both are StateFeedbackTokens with idle/active/tuning', () => {
    const manifest = validManifest();
    expect(Object.keys(manifest.tokens.rx).sort()).toEqual(Object.keys(manifest.tokens.tx).sort());
    expect(Object.keys(manifest.tokens.rx).sort()).toEqual(['active', 'idle', 'tuning']);
  });

  it('rejects an empty focusRing token (MOR-1232 token half)', () => {
    const manifest = validManifest();
    const broken = { ...manifest, tokens: { ...manifest.tokens, focusRing: '' } };
    expect(() => validateManifest(broken)).toThrow(/focusRing/);
  });

  it('rejects a bundle that declares tabular figures as anything other than "tabular-nums"', () => {
    // Simulates a plain-JS caller bypassing the 'tabular-nums' literal type —
    // the runtime check is what actually enforces MOR-977 §4.5.3.
    const manifest = validManifest();
    const typography = { ...manifest.tokens.typography, fontVariantNumeric: 'lining-nums' } as unknown as DesignLanguageManifest['tokens']['typography'];
    const broken = { ...manifest, tokens: { ...manifest.tokens, typography } };
    expect(() => validateManifest(broken)).toThrow(/tabular/i);
  });
});
