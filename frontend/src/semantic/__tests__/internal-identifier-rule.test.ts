/**
 * MOR-2716 — the internal-identifier rule's unit suite. The two acceptance
 * pins from the ticket, both ways: the strings the RX/TX surface rendered
 * before #3774 must hit, and a normal catalog sentence must pass.
 */
import { describe, expect, it } from 'vitest';
import {
  findInternalIdentifiers,
  INTERNAL_IDENTIFIER_VOCABULARY,
} from '../internal-identifier-rule';
import {
  DISABLED_REASON_CODES,
  TX_TARGET_UNKNOWN_REASONS,
  VFO_SLOT_KINDS,
} from '../radio-view-model';

describe('MOR-2716: the vocabulary comes from the code\'s own constants', () => {
  it('contains every disabled-reason code, slot kind and TX-target reason', () => {
    for (const code of DISABLED_REASON_CODES) {
      expect(INTERNAL_IDENTIFIER_VOCABULARY).toContain(code);
    }
    for (const kind of VFO_SLOT_KINDS) {
      expect(INTERNAL_IDENTIFIER_VOCABULARY).toContain(kind);
    }
    for (const reason of TX_TARGET_UNKNOWN_REASONS) {
      expect(INTERNAL_IDENTIFIER_VOCABULARY).toContain(reason);
    }
  });
  it('adds a new code through the constants alone, with no second list', () => {
    expect(INTERNAL_IDENTIFIER_VOCABULARY).toContain('field-not-observed');
    expect(INTERNAL_IDENTIFIER_VOCABULARY).toContain('unslotted');
  });
});

describe('MOR-2716: the two strings the RX/TX surface rendered before #3774', () => {
  it('hits for a planted "txTarget: field-not-observed"', () => {
    expect(findInternalIdentifiers('txTarget: field-not-observed')).toEqual([
      { kind: 'internal-identifier', token: 'field-not-observed' },
    ]);
  });
  it('hits for a planted slot kind "unslotted"', () => {
    expect(findInternalIdentifiers('unslotted')).toEqual([
      { kind: 'internal-identifier', token: 'unslotted' },
    ]);
    expect(findInternalIdentifiers('slot kind unslotted here')).toEqual([
      { kind: 'internal-identifier', token: 'unslotted' },
    ]);
  });
  it('hits inside a long sentence and in a title-length sentence alike', () => {
    expect(findInternalIdentifiers('The TX target reason is not-observed right now')).toEqual([
      { kind: 'internal-identifier', token: 'not-observed' },
    ]);
  });
});

describe('MOR-2716: a normal catalog sentence passes', () => {
  it.each([
    'Active receiver: MAIN',
    'frequency outside the configured TX ranges',
    'keying in progress',
    '14.250 MHz',
  ])('passes for %s', (text) => expect(findInternalIdentifiers(text)).toEqual([]));
});

describe('MOR-2716: whole identifiers only', () => {
  it.each([
    'field-not-observed-yet',
    'prefixed-field-not-observed',
    'unslotted-thing',
    'slotted-in-text',
    'relatively',
  ])('does not hit for %s', (text) => expect(findInternalIdentifiers(text)).toEqual([]));
  it('does not hit for an empty or whitespace-only string', () => {
    expect(findInternalIdentifiers('')).toEqual([]);
    expect(findInternalIdentifiers('   ')).toEqual([]);
  });
});
