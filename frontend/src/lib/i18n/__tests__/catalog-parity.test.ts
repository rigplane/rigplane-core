import { describe, it, expect } from 'vitest';

import enUS from '../locales/en-US.json' with { type: 'json' };
import jaJP from '../locales/ja-JP.json' with { type: 'json' };
import ruRU from '../locales/ru-RU.json' with { type: 'json' };
import { FACEPLATE_INVARIANT_KEYS } from '../faceplate-invariant-keys';

// MOR-2715: every catalog key present in the en-US source of truth must
// also be present in every other shipped locale, so no locale silently
// falls back to English for operator-visible sentences. This is a
// structural key-equality check; value parity (faceplate vocabulary) and
// placeholder/glossary lint are enforced by faceplate-invariant.test.ts
// and frontend/scripts/i18n-check.mjs respectively.

const enUSCatalog = enUS as unknown as Record<string, string>;

const CATALOGS: Record<string, Record<string, string>> = {
  'ja-JP': jaJP as unknown as Record<string, string>,
  'ru-RU': ruRU as unknown as Record<string, string>,
};

// Faceplate-invariant keys may be OMITTED from a non-English catalog:
// the en-US fallback renders the identical string, which is the
// documented handling in faceplate-invariant.test.ts.
const INVARIANT_KEYS = new Set(FACEPLATE_INVARIANT_KEYS);

// Known untranslated gaps, listed per locale so each one is visible.
// Every entry is a deliberate omission (en-US fallback covers it) that
// predates or is out of scope for MOR-2715; translate and remove an
// entry in the PR that closes its gap. Tracked by MOR-2715.
const KNOWN_GAPS: Record<string, string[]> = {
  'ja-JP': [
    'core.agcPanel.pendingAnnouncement',
    'core.band.entry.reason.boundsUnknown',
    'core.band.entry.reason.receiverUnconfirmed',
    'core.band.tx.caveat.denied',
    'core.band.tx.caveat.unknown',
    'core.band.tx.defaultPermit.label',
    'core.band.tx.defaultPermit.status.allowed',
    'core.band.tx.defaultPermit.status.denied',
    'core.band.tx.reason.bandUnresolved',
    'core.band.tx.reason.receiverUnconfirmed',
    'core.dsp.notch.pendingAnnouncement',
    'core.dsp.pendingAnnouncement',
    'core.filter.select.pendingAnnouncement',
    'core.filter.width.cancelledAnnouncement',
    'core.filter.width.confirmedAnnouncement',
    'core.filter.width.failedAnnouncement',
    'core.filter.width.pendingAnnouncement',
    'core.filter.width.supersededAnnouncement',
    'core.filter.width.timedOutAnnouncement',
    'core.modePanel.pendingAnnouncement',
    'core.repeater.pendingAnnouncement',
    'core.rfFrontEnd.att.pendingAnnouncement',
    'core.rfFrontEnd.preamp.pendingAnnouncement',
    'core.rxTx.blocked.busy',
    'core.rxTx.blocked.fault',
    'core.rxTx.blocked.permitUnknown',
    'core.rxTx.blocked.radioTransmitting',
    'core.rxTx.blocked.rfStateUnknown',
    'core.rxTx.fault.causes',
    'core.rxTx.fault.code',
    'core.rxTx.fault.reason.authorityEpochMismatch',
    'core.rxTx.fault.reason.browserTxAudioUnavailable',
    'core.rxTx.fault.reason.catPttUnavailable',
    'core.rxTx.fault.reason.controlNotLive',
    'core.rxTx.fault.reason.noConfirmedPttOff',
    'core.rxTx.fault.reason.permitNotAllowed',
    'core.rxTx.fault.reason.pttNotAuthoritative',
    'core.rxTx.fault.reason.pttNotOff',
    'core.rxTx.fault.reason.targetUnknown',
    'core.rxTx.fault.reset.action',
    'core.rxTx.fault.reset.blocked',
    'core.rxTx.fault.reset.note',
    'core.rxTx.fault.reset.reason.cleanup',
    'core.rxTx.fault.reset.reason.dekeyPending',
    'core.rxTx.fault.reset.reason.keyHeld',
    'core.rxTx.fault.reset.reason.modRestore',
    'core.rxTx.target.reason.contradiction',
    'core.rxTx.target.reason.notObserved',
    'core.rxTx.target.reason.stale',
    'core.rxTx.target.reason.unsupported',
    'core.rxTx.target.unknown',
    'core.settings.workspace.designLanguageLabel',
    'core.settings.workspace.dismissButton',
    'core.settings.workspace.exportButton',
    'core.settings.workspace.importButton',
    'core.settings.workspace.importFileLabel',
    'core.settings.workspace.importPasteLabel',
    'core.settings.workspace.importRejectedTitle',
    'core.settings.workspace.layoutLabel',
    'core.settings.workspace.noticeForwardReadOnly',
    'core.settings.workspace.noticePersistFailed',
    'core.settings.workspace.noticeRepaired',
    'core.settings.workspace.noticeReset',
    'core.settings.workspace.noticeVersionDiscarded',
    'core.settings.workspace.overrideButton',
    'core.settings.workspace.resetButton',
    'core.settings.workspace.resetHint',
    'core.settings.workspace.themeLabel',
    'core.settings.workspace.title',
    'core.settings.workspace.undoButton',
    'core.spectrum.autoStep.offTitle',
    'core.spectrum.autoStep.onTitle',
    'core.statusbar.txCodecFallback.label',
    'core.statusbar.txCodecFallback.tooltip',
    'core.toast.commandExecutionFailed',
    'core.toast.commandRefusedLinkDegraded',
    'core.vfo.dualWatch.unknownReason',
    'core.vfo.freq.pendingAnnouncement',
    'core.vfo.ops.identityUnknownReason',
    'core.vfo.select.pendingReason',
    'core.vfo.select.receiverUnavailableReason',
    'core.vfo.select.unknownSlotReason',
    'core.vfo.split.unknownReason',
  ],
  'ru-RU': [
    'core.settings.workspace.designLanguageLabel',
    'core.settings.workspace.dismissButton',
    'core.settings.workspace.exportButton',
    'core.settings.workspace.importButton',
    'core.settings.workspace.importFileLabel',
    'core.settings.workspace.importPasteLabel',
    'core.settings.workspace.importRejectedTitle',
    'core.settings.workspace.layoutLabel',
    'core.settings.workspace.noticeForwardReadOnly',
    'core.settings.workspace.noticePersistFailed',
    'core.settings.workspace.noticeRepaired',
    'core.settings.workspace.noticeReset',
    'core.settings.workspace.noticeVersionDiscarded',
    'core.settings.workspace.overrideButton',
    'core.settings.workspace.resetButton',
    'core.settings.workspace.resetHint',
    'core.settings.workspace.themeLabel',
    'core.settings.workspace.title',
    'core.settings.workspace.undoButton',
  ],
};

// A listed gap that has since been translated (or no longer exists in
// en-US) is stale and must be removed from KNOWN_GAPS.
const STALE_GAPS = (locale: string): string[] =>
  KNOWN_GAPS[locale].filter(
    (key) => key in CATALOGS[locale] || !(key in enUSCatalog),
  );

describe('locale catalog parity (MOR-2715)', () => {
  it('the en-US source of truth is non-empty', () => {
    expect(Object.keys(enUSCatalog).length).toBeGreaterThan(0);
  });

  for (const [locale, catalog] of Object.entries(CATALOGS)) {
    it(`${locale} covers every en-US key outside the documented gaps`, () => {
      const exempt = new Set([...KNOWN_GAPS[locale], ...INVARIANT_KEYS]);
      const missing = Object.keys(enUSCatalog)
        .filter((key) => key !== '$schema')
        .filter((key) => !(key in catalog))
        .filter((key) => !exempt.has(key));
      expect(missing).toEqual([]);
    });

    it(`${locale} has no stale entries in its known-gaps list`, () => {
      expect(STALE_GAPS(locale)).toEqual([]);
    });
  }
});
