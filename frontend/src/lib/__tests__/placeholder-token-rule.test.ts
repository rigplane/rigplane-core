/**
 * MOR-2677 — the placeholder-token rule's unit suite. Every example named by
 * the ticket, both ways: what must hit and what must not.
 */
import { describe, expect, it } from 'vitest';
import { findPlaceholderTokens } from '../placeholder-token-rule';

const hits = (text: string, isTitle = false): string[] =>
  findPlaceholderTokens(text, { isTitle }).map((t) => t.kind);

describe('rule 1: dash placeholder', () => {
  it.each(['—', '---', '--- Hz', '-------', '–', ' — ', '--- kHz', '—— SWR'])(
    'hits for %s',
    (text) => expect(hits(text)).toEqual(['dash-run']),
  );
  it('hits for a dash run with a micro and percent unit word', () => {
    expect(hits('--- µV')).toEqual(['dash-run']);
    expect(hits('--- %')).toEqual(['dash-run']);
  });
  it.each([
    'reconnecting — please wait',
    'Turn OFF the radio?',
    '— and beyond',
    '20m — 14.250 MHz',
    'unknown band — see the log',
    '14.250 MHz',
    'RX',
  ])('does not hit for %s', (text) => expect(hits(text)).toEqual([]));
});

describe('rule 2: question token', () => {
  it('hits for "ATU: ?"', () => {
    expect(findPlaceholderTokens('ATU: ?')).toEqual([
      { kind: 'question-token', token: '?' },
    ]);
  });
  it.each(['?', '?.', '?,', '?;', '?;.', '?'])('hits for %s', (text) =>
    expect(hits(text)).toEqual(['question-token']),
  );
  it.each([
    'Turn OFF the radio?',
    'radio?',
    'Are you sure?',
    'What?',
    'Why? Because.',
  ])('does not hit for %s', (text) => expect(hits(text)).toEqual([]));
});

describe('rule 3: word tokens', () => {
  it.each(['unknown', 'Unknown', 'UNKNOWN', 'N/A', 'n/a', 'NaN', 'null', 'undefined'])(
    'hits for %s',
    (text) => expect(hits(text)).toEqual(['placeholder-word']),
  );
  it('hits inside a multi-token string', () => {
    expect(findPlaceholderTokens('SWR: unknown')).toEqual([
      { kind: 'placeholder-word', token: 'unknown' },
    ]);
    expect(hits('Mode N/A')).toEqual(['placeholder-word']);
  });
  it.each(['unknowable', 'unknowning', 'nounknown', 'N/A-value', 'unknow'])(
    'does not hit for %s',
    (text) => expect(hits(text)).toEqual([]),
  );
  it('does not hit for an empty or whitespace-only string', () => {
    expect(hits('')).toEqual([]);
    expect(hits('   ')).toEqual([]);
  });
});

describe('rule 4: title allowance', () => {
  it('skips rules 2–3 for a 3-word title', () => {
    expect(hits('Tuner status is unknown', true)).toEqual([]);
    expect(hits('Radio power state unknown?', true)).toEqual([]);
  });
  it('still applies rules 2–3 to a 1–2-word title', () => {
    expect(hits('Unknown', true)).toEqual(['placeholder-word']);
    expect(hits('ATU: ?', true)).toEqual(['question-token']);
  });
  it('always applies rule 1 to a title, however long', () => {
    expect(hits('Tuner status is —', true)).toEqual(['dash-run']);
    expect(hits('--- Hz', true)).toEqual(['dash-run']);
  });
  it('does not grant the allowance to non-title text', () => {
    expect(hits('Tuner status is unknown')).toEqual(['placeholder-word']);
  });
});
