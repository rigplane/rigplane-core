/**
 * MOR-1064 — the semantic RX/TX status and action surface.
 *
 * SAFETY-CRITICAL. Every test below names the mutation it kills, because a
 * surface that merely "looks right" is worthless here: the failure modes are
 * (a) showing RX while the browser may own the key, (b) offering a key action
 * the authority would refuse, and (c) gating the *unkey* path.
 *
 * ADR invariant 11: TX authority lives in the App-owned TX controller. This
 * surface is NOT a second authority — it renders the authority snapshot and
 * emits intents. It never derives TX truth from the radio view model, and it
 * never keys anything itself. See `AppGlobalHost.svelte` (MOR-1059) for the
 * authoritative global lamp, which this surface must not duplicate.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import RxTxSurface from '../RxTxSurface.svelte';
import { topologyFixtures, withAudioOnlyScope, type TopologyFixtureId } from '../fixtures/topologies';
import type { RadioViewModel } from '../radio-view-model';
import { blockedLabel, targetUnknownMessage, type KeyBlockedReason, type TxAuthoritySnapshot } from '../rx-tx-surface';
import { t } from '$lib/i18n';

const IDS: readonly TopologyFixtureId[] = ['1/single', '1/ab', '2/ab_shared', '2/main_sub'];
/** The only fixture whose permit is 'allowed' AND whose txTarget is known. */
const PERMITTED: TopologyFixtureId[] = ['1/single', '2/main_sub'];

const IDLE_RX: TxAuthoritySnapshot = {
  phase: 'idle', intent: null, radioTx: 'off', txRisk: 'none', mayOwnKey: false, fault: null,
};
const snap = (over: Partial<TxAuthoritySnapshot> = {}): TxAuthoritySnapshot => ({ ...IDLE_RX, ...over });

let target: HTMLDivElement;
beforeEach(() => { target = document.createElement('div'); document.body.appendChild(target); });
afterEach(() => { target.remove(); });

type Handlers = { onRequestKey?: () => void; onRequestUnkey?: () => void };
function render(view: RadioViewModel, tx: TxAuthoritySnapshot, handlers: Handlers = {}) {
  const component = mount(RxTxSurface, { target, props: { view, tx, ...handlers } });
  flushSync();
  const q = (sel: string) => target.querySelector(sel);
  return {
    dispose: () => unmount(component),
    state: () => q('[data-testid="rx-tx-state"]') as HTMLElement,
    key: () => q('[data-testid="rx-tx-key"]') as HTMLButtonElement,
    unkey: () => q('[data-testid="rx-tx-unkey"]') as HTMLButtonElement,
    reasons: () => [...target.querySelectorAll('[data-testid="rx-tx-blocked"] [data-reason]')]
      .map((el) => el.getAttribute('data-reason')),
    root: () => q('[data-testid="rx-tx-surface"]') as HTMLElement,
  };
}
function withSurface(
  view: RadioViewModel, tx: TxAuthoritySnapshot,
  fn: (s: ReturnType<typeof render>) => void, handlers: Handlers = {},
): void {
  const s = render(view, tx, handlers);
  try { fn(s); } finally { s.dispose(); }
}

// ── 1. TX state display mirrors the AUTHORITY snapshot, and only it ─────────

describe('RX/TX status mirrors the App TX authority snapshot', () => {
  it.each(IDS)('%s: an idle authority with an observed OFF radio reads RX', (id) => {
    withSurface(topologyFixtures[id], IDLE_RX, (s) => {
      expect(s.state().dataset.rf).toBe('receiving');
      expect(s.state().textContent).toContain('RX');
    });
  });

  it.each(IDS)('%s: txRisk "uncertain" NEVER renders as RX-idle (fail closed)', (id) => {
    // Kill-mutation: `if (txRisk === 'uncertain') return 'uncertain'` -> deleted,
    // so an unconfirmed key-down falls through to 'receiving'. radioTx is still
    // 'off' here (no readback yet) — exactly the state that tempts a naive
    // implementation into showing RX while the key may be down.
    withSurface(topologyFixtures[id], snap({ txRisk: 'uncertain', mayOwnKey: true, phase: 'key-confirm-pending' }), (s) => {
      expect(s.state().dataset.rf).toBe('uncertain');
      expect(s.state().dataset.rf).not.toBe('receiving');
      expect(s.state().textContent).not.toContain('RX');
    });
  });

  it.each(IDS)('%s: confirmed-on and radioTx=on both read TX', (id) => {
    for (const over of [{ txRisk: 'confirmed-on' as const }, { radioTx: 'on' as const }]) {
      withSurface(topologyFixtures[id], snap(over), (s) => {
        expect(s.state().dataset.rf).toBe('transmitting');
        expect(s.state().textContent).toContain('TX');
      });
    }
  });

  it.each(IDS)('%s: an unknown radioTx (control loss / not yet observed) is never RX', (id) => {
    // Kill-mutation: `return 'receiving'` as the unconditional fallback.
    // Connection loss rebases the authority epoch and resets radioTx to
    // 'unknown'; claiming RX there is a lie about the RF state.
    withSurface(topologyFixtures[id], snap({ radioTx: 'unknown' }), (s) => {
      expect(s.state().dataset.rf).toBe('unknown');
      expect(s.state().textContent).not.toContain('RX');
    });
  });

  it('is a pure function of the authority: same snapshot + different view models render the same RF state', () => {
    // Kill-mutation: rewire the display to `view.txTarget`/`view.txPermit`.
    // These four view models disagree on target/permit/topology; the RF state
    // must not move, because none of that is RF truth.
    const rendered = IDS.map((id) => {
      let rf = '';
      withSurface(topologyFixtures[id], snap({ radioTx: 'on' }), (s) => { rf = s.state().dataset.rf ?? ''; });
      return rf;
    });
    expect(new Set(rendered)).toEqual(new Set(['transmitting']));
  });

  it('is a pure function of the authority: same view model + different snapshots render different RF states', () => {
    // The non-vacuity half of the test above: proves the display is wired to
    // *something* that moves, not hard-coded.
    const view = topologyFixtures['1/single'];
    const seen = ([IDLE_RX, snap({ radioTx: 'on' }), snap({ txRisk: 'uncertain' }), snap({ radioTx: 'unknown' })]).map((tx) => {
      let rf = '';
      withSurface(view, tx, (s) => { rf = s.state().dataset.rf ?? ''; });
      return rf;
    });
    expect(seen).toEqual(['receiving', 'transmitting', 'uncertain', 'unknown']);
  });

  it.each([
    ['audio-start-pending', 'pending'], ['key-confirm-pending', 'pending'],
    ['active', 'keyed'], ['releasing', 'releasing'], ['failed', 'failed'], ['idle', 'idle'],
  ] as const)('session phase %s renders as %s', (phase, session) => {
    withSurface(topologyFixtures['1/single'], snap({ phase }), (s) => {
      expect(s.state().dataset.session).toBe(session);
    });
  });

  it('an unrecognised phase falls back to "pending", never to "idle"', () => {
    // Kill-mutation: `?? 'idle'` as the lookup fallback. A phase this surface
    // has not been taught about must read as "something is happening", not as
    // "nothing is happening".
    const bogus = { ...IDLE_RX, phase: 'brand-new-phase' as TxAuthoritySnapshot['phase'] };
    withSurface(topologyFixtures['1/single'], bogus, (s) => {
      expect(s.state().dataset.session).toBe('pending');
      expect(s.state().dataset.session).not.toBe('idle');
    });
  });

  it.each(['momentary', 'latched'] as const)('surfaces a %s (held/latched) intent', (intent) => {
    withSurface(topologyFixtures['1/single'], snap({ phase: 'active', intent, mayOwnKey: true, radioTx: 'on' }), (s) => {
      expect(s.state().dataset.intent).toBe(intent);
      expect(s.state().textContent?.toLowerCase()).toContain(intent);
    });
  });

  it('distinguishes external TX from locally-owned TX', () => {
    withSurface(topologyFixtures['1/single'], snap({ radioTx: 'on' }), (s) => {
      expect(s.state().dataset.origin).toBe('external');
    });
    withSurface(topologyFixtures['1/single'], snap({ radioTx: 'on', mayOwnKey: true, phase: 'active' }), (s) => {
      expect(s.state().dataset.origin).toBe('local');
    });
  });

  it('never labels TX "external" while the browser may own the key', () => {
    // Kill-mutation: `origin = mayOwnKey ? 'local' : 'external'` narrowed to
    // `phase === 'idle' ? 'external' : 'local'` (or vice versa). Telling the
    // operator "not you" while a lease is live is the dangerous direction.
    for (const tx of [
      snap({ mayOwnKey: true, txRisk: 'uncertain' }),
      snap({ phase: 'releasing', mayOwnKey: true, txRisk: 'confirmed-on', radioTx: 'on' }),
      snap({ phase: 'audio-start-pending' }),
      snap({ phase: 'failed', fault: 'on-timeout' }),
    ]) {
      withSurface(topologyFixtures['1/single'], tx, (s) => {
        expect(s.state().dataset.origin).toBe('local');
      });
    }
  });
});

// ── 4. No duplicate authority: this surface displays, it does not indicate ──

describe('fault display without a second authority', () => {
  it('displays the authority fault verbatim', () => {
    withSurface(topologyFixtures['1/single'], snap({ phase: 'failed', fault: 'release-not-confirmed' }), (s) => {
      const fault = target.querySelector('[data-testid="rx-tx-fault"]') as HTMLElement;
      expect(fault).not.toBeNull();
      expect(fault.dataset.fault).toBe('release-not-confirmed');
      expect(fault.textContent).toContain('release-not-confirmed');
    });
  });

  it('shows no fault region when the authority reports none', () => {
    withSurface(topologyFixtures['1/single'], IDLE_RX, () => {
      expect(target.querySelector('[data-testid="rx-tx-fault"]')).toBeNull();
    });
  });

  it('does not claim the assertive/alert channel owned by the global TX lamp (MOR-1059)', () => {
    // Kill-mutation: copy AppGlobalHost's `aria-live="assertive"` / `role="alert"`
    // onto this surface. Two assertive announcers for one fact double-speak over
    // each other and make the authoritative lamp ambiguous. This surface is
    // polite status; the App-root lamp stays the alert.
    withSurface(topologyFixtures['1/single'], snap({ radioTx: 'on', phase: 'failed', fault: 'backend-dekeyed' }), (s) => {
      const root = s.root();
      expect(root.querySelector('[aria-live="assertive"]')).toBeNull();
      expect(root.querySelector('[role="alert"]')).toBeNull();
      expect(root.querySelector('[role="status"]')).not.toBeNull();
    });
  });
});

// ── 2. TX action intents ────────────────────────────────────────────────────

describe('key intent gating', () => {
  it.each(PERMITTED)('%s: emits exactly one key intent when permit is allowed and the authority is idle/RX', (id) => {
    const onRequestKey = vi.fn();
    withSurface(topologyFixtures[id], IDLE_RX, (s) => {
      expect(s.key().disabled).toBe(false);
      s.key().click();
      flushSync();
      expect(onRequestKey).toHaveBeenCalledTimes(1);
    }, { onRequestKey });
  });

  it.each([
    ['1/ab', 'tx-permit-unknown'],
    ['2/ab_shared', 'tx-permit-denied'],
  ] as const)('%s: a non-allowed permit disables keying and names the reason (%s)', (id, reason) => {
    // Kill-mutation: `permit.status !== 'denied'` as the gate (treats the
    // tri-state 'unknown' as permission). The contract validator already
    // rejects permit='allowed' + unknown target; the surface must independently
    // treat 'unknown' as NOT allowed.
    const onRequestKey = vi.fn();
    withSurface(topologyFixtures[id], IDLE_RX, (s) => {
      expect(s.key().disabled).toBe(true);
      expect(s.reasons()).toContain(reason);
      s.key().click();
      flushSync();
      expect(onRequestKey).not.toHaveBeenCalled();
    }, { onRequestKey });
  });

  it.each(IDS)('%s: audio-only scope does not change the key gate (scope is not a TX fact)', (id) => {
    const base = topologyFixtures[id];
    let plain = false;
    let audioOnly = false;
    withSurface(base, IDLE_RX, (s) => { plain = s.key().disabled; });
    withSurface(withAudioOnlyScope(base), IDLE_RX, (s) => { audioOnly = s.key().disabled; });
    expect(audioOnly).toBe(plain);
  });

  it.each([
    ['external TX in progress', snap({ radioTx: 'on' }), 'radio-transmitting'],
    ['RF state unknown', snap({ radioTx: 'unknown' }), 'rf-state-unknown'],
    ['a lease already pending', snap({ phase: 'audio-start-pending' }), 'tx-busy'],
    ['a key already owned', snap({ phase: 'active', mayOwnKey: true, txRisk: 'confirmed-on', radioTx: 'on' }), 'tx-busy'],
    ['a release in flight', snap({ phase: 'releasing', mayOwnKey: true, txRisk: 'uncertain' }), 'tx-busy'],
    ['an unresolved fault', snap({ phase: 'failed', fault: 'not-eligible' }), 'tx-fault'],
  ] as const)('%s: the authority forbids keying even with an allowed permit', (_name, tx, reason) => {
    // Kill-mutation: gate on `view.txPermit` alone. The permit says the
    // FREQUENCY is legal; only the authority knows whether the transmitter is
    // free. Both must agree before a key intent may leave this surface.
    const onRequestKey = vi.fn();
    withSurface(topologyFixtures['1/single'], tx, (s) => {
      expect(s.key().disabled).toBe(true);
      expect(s.reasons()).toContain(reason);
      s.key().click();
      flushSync();
      expect(onRequestKey).not.toHaveBeenCalled();
    }, { onRequestKey });
  });

  it('surfaces the view model disabled reasons that concern TX', () => {
    withSurface(topologyFixtures['1/ab'], IDLE_RX, (s) => {
      expect(s.reasons()).toContain('field-not-observed');
    });
    // ...and does not import unrelated scope reasons into the TX gate.
    withSurface(topologyFixtures['2/main_sub'], IDLE_RX, (s) => {
      expect(s.reasons()).not.toContain('capability-unavailable');
    });
  });
});

describe('unkey intent is never gated (fail-safe direction)', () => {
  const EVERY_STATE: TxAuthoritySnapshot[] = [
    IDLE_RX,
    snap({ radioTx: 'unknown' }),
    snap({ radioTx: 'on' }),
    snap({ phase: 'audio-start-pending' }),
    snap({ phase: 'key-confirm-pending', mayOwnKey: true, txRisk: 'uncertain' }),
    snap({ phase: 'active', mayOwnKey: true, txRisk: 'confirmed-on', radioTx: 'on', intent: 'latched' }),
    snap({ phase: 'releasing', mayOwnKey: true, txRisk: 'confirmed-on' }),
    snap({ phase: 'failed', fault: 'on-command-failed' }),
  ];

  it('stays enabled and emits for every topology × every authority state', () => {
    // Kill-mutation: add ANY condition to the unkey path — `disabled={...}`,
    // `{#if}` around the button, or an early return in the handler. Stopping
    // transmission must never require the surface to agree that TX is happening;
    // the whole point of the uncertain/unknown states is that it may not know.
    for (const id of IDS) {
      for (const tx of EVERY_STATE) {
        const onRequestUnkey = vi.fn();
        withSurface(topologyFixtures[id], tx, (s) => {
          expect(s.unkey()).not.toBeNull();
          expect(s.unkey().disabled).toBe(false);
          expect(s.unkey().hasAttribute('aria-disabled')).toBe(false);
          s.unkey().click();
          flushSync();
          expect(onRequestUnkey).toHaveBeenCalledTimes(1);
        }, { onRequestUnkey });
      }
    }
  });

  it('emits unkey even when a denied permit blocks keying', () => {
    const onRequestKey = vi.fn();
    const onRequestUnkey = vi.fn();
    withSurface(topologyFixtures['2/ab_shared'], snap({ phase: 'active', mayOwnKey: true, radioTx: 'on' }), (s) => {
      expect(s.key().disabled).toBe(true);
      s.unkey().click();
      flushSync();
      expect(onRequestUnkey).toHaveBeenCalledTimes(1);
      expect(onRequestKey).not.toHaveBeenCalled();
    }, { onRequestKey, onRequestUnkey });
  });
});

// ── 3. txTarget unknown ⇒ target-unknown state, never a guess ───────────────

describe('unknown TX target', () => {
  it('renders an explicit target-unknown state with its reason and disables keying', () => {
    // Kill-mutation: fall back to the active VFO / vfos[0] as the target.
    // MOR-988 §3.2: missing observation never synthesizes a target.
    withSurface(topologyFixtures['1/ab'], IDLE_RX, (s) => {
      const t = target.querySelector('[data-testid="rx-tx-target"]') as HTMLElement;
      expect(t.dataset.target).toBe('unknown');
      expect(t.dataset.reason).toBe('not-observed');
      expect(s.key().disabled).toBe(true);
      expect(s.reasons()).toContain('tx-target-unknown');
    });
  });

  it('does not name any receiver, slot or frequency when the target is unknown', () => {
    const view = topologyFixtures['1/ab'];
    withSurface(view, IDLE_RX, () => {
      const t = target.querySelector('[data-testid="rx-tx-target"]') as HTMLElement;
      const text = t.textContent ?? '';
      for (const vfo of view.vfos) {
        expect(text).not.toContain(String(vfo.frequencyHz));
        expect(text).not.toContain(vfo.label);
      }
      expect(t.dataset.receiver).toBeUndefined();
    });
  });

  it.each(PERMITTED)('%s: names the known target exactly as the view model reports it', (id) => {
    const view = topologyFixtures[id];
    const known = view.txTarget;
    if (known.status !== 'known') throw new Error('fixture precondition');
    withSurface(view, IDLE_RX, () => {
      const t = target.querySelector('[data-testid="rx-tx-target"]') as HTMLElement;
      expect(t.dataset.target).toBe('known');
      expect(t.dataset.receiver).toBe(known.receiver);
      expect(t.dataset.slot).toBe(known.slot.kind === 'slotted' ? known.slot.id : known.slot.kind);
    });
  });
});

// ── 5. Accessibility ───────────────────────────────────────────────────────

describe('accessibility', () => {
  it('gives both actions unambiguous accessible names', () => {
    withSurface(topologyFixtures['1/single'], IDLE_RX, (s) => {
      expect((s.key().getAttribute('aria-label') ?? s.key().textContent ?? '').toLowerCase()).toContain('key');
      const unkeyName = (s.unkey().getAttribute('aria-label') ?? s.unkey().textContent ?? '').toLowerCase();
      expect(unkeyName).toContain('unkey');
      // The two names must not be confusable by prefix alone.
      expect(unkeyName).not.toBe((s.key().getAttribute('aria-label') ?? s.key().textContent ?? '').toLowerCase());
    });
  });

  it('reports the key action pressed state from the authority', () => {
    withSurface(topologyFixtures['1/single'], IDLE_RX, (s) => {
      expect(s.key().getAttribute('aria-pressed')).toBe('false');
    });
    for (const tx of [
      snap({ phase: 'key-confirm-pending', mayOwnKey: true, txRisk: 'uncertain' }),
      snap({ phase: 'active', mayOwnKey: true, txRisk: 'confirmed-on', radioTx: 'on' }),
      snap({ phase: 'releasing', mayOwnKey: true, txRisk: 'confirmed-on' }),
    ]) {
      withSurface(topologyFixtures['1/single'], tx, (s) => {
        expect(s.key().getAttribute('aria-pressed')).toBe('true');
      });
    }
  });

  it('keeps the unkey action focusable in every state (it must never be reached only by mouse)', () => {
    withSurface(topologyFixtures['1/single'], snap({ phase: 'active', mayOwnKey: true, radioTx: 'on' }), (s) => {
      s.unkey().focus();
      expect(document.activeElement).toBe(s.unkey());
    });
  });

  it('encodes RX/TX structurally, not by colour or class alone (forced-colors survival, MOR-977)', () => {
    // Kill-mutation: drop the text/shape and keep only `data-rf` + a CSS class.
    // Under forced-colors the class-driven colour is overridden and the states
    // become visually identical. Each state must carry distinct TEXT and a
    // distinct SHAPE glyph.
    const texts = new Map<string, string>();
    const marks = new Map<string, string>();
    for (const tx of [IDLE_RX, snap({ radioTx: 'on' }), snap({ txRisk: 'uncertain' }), snap({ radioTx: 'unknown' })]) {
      withSurface(topologyFixtures['1/single'], tx, (s) => {
        const rf = s.state().dataset.rf ?? '';
        texts.set(rf, (s.state().querySelector('[data-testid="rx-tx-rf-label"]')?.textContent ?? '').trim());
        marks.set(rf, (s.state().querySelector('[data-testid="rx-tx-rf-mark"]')?.textContent ?? '').trim());
      });
    }
    expect(texts.size).toBe(4);
    expect(new Set(texts.values()).size).toBe(4);
    expect(new Set(marks.values()).size).toBe(4);
    for (const value of [...texts.values(), ...marks.values()]) expect(value).not.toBe('');
  });

  it('associates the blocked reasons with the key action for screen readers', () => {
    withSurface(topologyFixtures['1/ab'], IDLE_RX, (s) => {
      const describedBy = s.key().getAttribute('aria-describedby');
      expect(describedBy).toBeTruthy();
      expect(target.querySelector(`#${describedBy}`)).not.toBeNull();
    });
  });
});

/* ── MOR-1474: operator-legible wording for the unknown-target line and the
   blocked-reason list — pinned against the contract-speak regression the
   MOR-1448 wording fix already removed one surface over. Every reason code
   is pinned to its catalog key (not just today's rendered text), so a
   future edit that silently reintroduces a raw enum word or an interpolated
   status fails a test by name. ────────────────────────────────────────── */

describe('MOR-1474 — unknown TX target names the reason through the catalog', () => {
  it.each([
    ['not-observed', 'core.rxTx.target.reason.notObserved'],
    ['stale', 'core.rxTx.target.reason.stale'],
    ['unsupported', 'core.rxTx.target.reason.unsupported'],
    ['contradiction', 'core.rxTx.target.reason.contradiction'],
  ] as const)('reason %s maps to catalog key %s', (reason, key) => {
    expect(targetUnknownMessage(reason)).toBe(
      t('core.rxTx.target.unknown', { reason: t(key) }),
    );
  });

  it('renders the composed catalog message for the fixture\'s own not-observed reason', () => {
    withSurface(topologyFixtures['1/ab'], IDLE_RX, () => {
      const t2 = target.querySelector('[data-testid="rx-tx-target"]') as HTMLElement;
      expect(t2.dataset.reason).toBe('not-observed');
      expect(t2.textContent?.trim()).toBe(targetUnknownMessage('not-observed'));
    });
  });

  it('never leaks the raw reason enum word as a bare, untranslated fragment', () => {
    // Kill-mutation: interpolating `reason` (the enum value itself) straight
    // into `core.rxTx.target.unknown` instead of resolving it through
    // `TARGET_REASON_KEY` first — the exact MOR-1448 F4 class of defect.
    for (const reason of ['not-observed', 'stale', 'unsupported', 'contradiction'] as const) {
      expect(targetUnknownMessage(reason)).not.toContain(reason);
    }
  });

  // R1 (verifier round 1): 'TX target unknown' is the exact phrase
  // BandSurface.test.ts's own CONTRACT_SPEAK list (lines 301-304) already
  // bans as contract-speak — 'target' is not a faceplate/glossary term, so
  // it must localize, and the bare compound read as jargon on this surface
  // for the same reason it would on BandSurface. Mirrors that guard here.
  it('never renders the contract-speak "TX target unknown" / bare "TX target" phrasing', () => {
    for (const reason of ['not-observed', 'stale', 'unsupported', 'contradiction'] as const) {
      const text = targetUnknownMessage(reason);
      expect(text).not.toContain('TX target unknown');
      expect(text).not.toContain('TX target');
    }
  });
});

describe('MOR-1474 — every key-blocked reason resolves to operator-legible copy', () => {
  const ALL_CODES: readonly KeyBlockedReason[] = [
    'tx-target-unknown', 'tx-permit-denied', 'tx-permit-unknown',
    'tx-fault', 'tx-busy', 'radio-transmitting', 'rf-state-unknown',
  ];

  it.each([
    ['tx-target-unknown', 'core.band.tx.reason.targetUnknown'],
    ['tx-permit-denied', 'core.band.tx.reason.outOfBand'],
    ['tx-permit-unknown', 'core.rxTx.blocked.permitUnknown'],
    ['tx-fault', 'core.rxTx.blocked.fault'],
    ['tx-busy', 'core.rxTx.blocked.busy'],
    ['radio-transmitting', 'core.rxTx.blocked.radioTransmitting'],
    ['rf-state-unknown', 'core.rxTx.blocked.rfStateUnknown'],
  ] as const)('code %s maps to catalog key %s', (code, key) => {
    expect(blockedLabel(code)).toBe(t(key));
  });

  it('has a mapping for every KeyBlockedReason the shared predicate can return', () => {
    for (const code of ALL_CODES) expect(blockedLabel(code)).not.toBe('');
  });

  it('renders the catalog-resolved text on the real blocked-reasons list', () => {
    withSurface(topologyFixtures['1/single'], snap({ radioTx: 'on' }), (s) => {
      const li = target.querySelector('[data-testid="rx-tx-blocked"] [data-reason="radio-transmitting"]');
      expect(li?.textContent?.trim()).toBe(blockedLabel('radio-transmitting'));
      expect(s.reasons()).toContain('radio-transmitting');
    });
  });
});
