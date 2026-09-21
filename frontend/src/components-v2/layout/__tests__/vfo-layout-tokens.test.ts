import { describe, expect, it } from 'vitest';

import {
  getVfoLayoutTokens,
  parseVfoLayoutScaleOverrides,
  resolveVfoLayoutProfile,
  vfoLayoutStyleVars,
} from '../vfo-layout-tokens';

describe('resolveVfoLayoutProfile', () => {
  it('returns baseline when width is missing', () => {
    expect(resolveVfoLayoutProfile(undefined)).toBe('baseline');
  });

  it('returns baseline below the wide threshold', () => {
    expect(resolveVfoLayoutProfile(1499)).toBe('baseline');
  });

  it('returns wide at and above the wide threshold', () => {
    expect(resolveVfoLayoutProfile(1500)).toBe('wide');
    expect(resolveVfoLayoutProfile(1720)).toBe('wide');
  });
});

describe('getVfoLayoutTokens', () => {
  it('keeps the baseline VFO composition contract and default meter variant', () => {
    const tokens = getVfoLayoutTokens('baseline');

    expect(tokens.bridgeWidth).toBe('132px');
    expect(tokens.bridgePadX).toBe('4px');
    expect(tokens.headerBadgeHeight).toBe('12px');
    expect(tokens.badgeInsetY).toBe('3px');
    expect(tokens.headerGroupGap).toBe('5px');
    expect(tokens.headerBadgeGap).toBe('3px');
    expect(tokens.controlStripGap).toBe('4px');
    expect(tokens.controlBadgeHeight).toBe('16px');
    expect(tokens.frequencySize).toBe('22px');
    expect(tokens.opsBadgeWidth).toBe('62px');
    expect(tokens.meterVariant).toBe('vfo');
  });

  it('keeps the wide profile proportions stable at the reference width', () => {
    const tokens = getVfoLayoutTokens('wide');

    expect(tokens.bridgeWidth).toBe('132px');
    expect(tokens.bridgePadX).toBe('5px');
    expect(tokens.controlStripGap).toBe('4px');
    expect(tokens.controlBadgeHeight).toBe('16px');
    expect(tokens.frequencySize).toBe('22px');
    expect(tokens.opsBadgeWidth).toBe('64px');
    expect(tokens.meterVariant).toBe('vfo-wide');
  });

  it('applies manual scale overrides as a single proportional system', () => {
    const tokens = getVfoLayoutTokens('wide', {
      width: 1600,
      overrides: {
        topRowScale: 1.05,
        frequencyScale: 0.95,
        badgeScale: 1.08,
      },
    });

    expect(tokens.headerBadgeHeight).toBe('14px');
    expect(tokens.badgeInsetY).toBe('3px');
    expect(tokens.headerGroupGap).toBe('5.67px');
    expect(tokens.headerBadgeGap).toBe('3.4px');
    expect(tokens.controlStripGap).toBe('4.54px');
    expect(tokens.controlBadgeHeight).toBe('18px');
    expect(tokens.frequencySize).toBe('21.95px');
    expect(tokens.controlBadgeMinHeight).toBe('18px');
  });
});

describe('parseVfoLayoutScaleOverrides', () => {
  it('parses manual scale overrides from URL query params', () => {
    expect(parseVfoLayoutScaleOverrides('?vfoScale=1.08&vfoFreqScale=0.92&vfoBadgeScale=1.04&vfoBridgeScale=0.98')).toEqual({
      topRowScale: 1.08,
      frequencyScale: 0.92,
      badgeScale: 1.04,
      bridgeScale: 0.98,
    });
  });

  it('ignores invalid values and clamps high values', () => {
    expect(parseVfoLayoutScaleOverrides('?vfoScale=bad&vfoBadgeScale=2.9')).toEqual({
      topRowScale: undefined,
      frequencyScale: undefined,
      badgeScale: 1.8,
      bridgeScale: undefined,
    });
  });
});

describe('vfoLayoutStyleVars', () => {
  it('serializes shared CSS variables for component consumption', () => {
    const style = vfoLayoutStyleVars('wide', { width: 1600 });

    expect(style).toContain('--vfo-bridge-width: 132px');
    expect(style).toContain('--vfo-bridge-pad-x: 5px');
    expect(style).toContain('--vfo-header-badge-height: 12px');
    expect(style).toContain('--vfo-badge-inset-y: 3px');
    expect(style).toContain('--vfo-header-group-gap: 5px');
    expect(style).toContain('--vfo-control-strip-gap: 4px');
    expect(style).toContain('--vfo-header-badge-padding-x: 5px');
    expect(style).toContain('--vfo-control-badge-padding-x: 6px');
    expect(style).toContain('--vfo-control-badge-height: 16px');
    expect(style).toContain('--vfo-frequency-size: 22px');
    expect(style).toContain('--vfo-ops-badge-width: 64px');
  });
});