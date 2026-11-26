import { describe, it, expect } from 'vitest';

import ruRU from '../locales/ru-RU.json' with { type: 'json' };

// MOR-4517 (MOR-4257 row 9). Owner decision 2026-11-16: the Russian UI word for the
// operator's own rig is «радио» («трансивер» is long and shows up everywhere).
// «радиостанция» is reserved for a broadcast station (none is named in this
// catalog, so the allow-list is empty on purpose); «радиолюбитель» and «радиосвязь»
// are fixed words.

const strings = Object.entries(ruRU as unknown as Record<string, unknown>).filter(
  (entry): entry is [string, string] =>
    !entry[0].startsWith('_') && typeof entry[1] === 'string',
);
const BROADCAST_ALLOWED = new Set<string>();

describe('ru-RU glossary: the rig is радио', () => {
  it.each([
    'core.toast.txHeldByOther',
    'core.toast.txReleaseInProgress',
    'core.toast.txAuthorityUnavailable',
    'core.toast.txControllerRefused',
    'core.toast.txRadioRefused',
  ])('keeps the MOR-4847 refusal value %s within the rig glossary', (key) => {
    const value = (ruRU as Record<string, string>)[key];
    expect(value).toBeTypeOf('string');
    expect(value).not.toMatch(/радиостанц|трансивер/i);
  });

  it('never calls the operator\'s rig a transceiver', () => {
    expect(strings.filter(([, v]) => /трансивер/i.test(v)).map(([k]) => k)).toEqual([]);
  });

  it('keeps радиостанция for broadcast stations only', () => {
    expect(
      strings
        .filter(([k, v]) => /радиостанц/i.test(v) && !BROADCAST_ALLOWED.has(k))
        .map(([k]) => k),
    ).toEqual([]);
  });

  it('pins the title-bar and tooltip wording of the rig', () => {
    const cat = ruRU as unknown as Record<string, string>;
    expect(cat['core.statusbar.settings.tooltip']).toBe('Регулировки радио');
    expect(cat['core.statusbar.indicator.radio']).toBe('Радио ↔ Сервер: {state}');
    expect(cat['core.toast.radioConnected']).toBe('Радио подключено');
    expect(cat['core.toast.txRadioRefused']).toBe('Радио отказалось перейти на передачу.');
    expect(cat['core.disabledReason.missing']).toBe('Не поддерживается этим радио');
  });
});
