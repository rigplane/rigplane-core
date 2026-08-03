import { describe, expect, it } from 'vitest';
import { validateRadioViewModel, type RadioViewModel } from '../radio-view-model';

function valid(): RadioViewModel {
  return {
    topologyId: '1/single',
    vfoScheme: 'single',
    activeReceiver: { status: 'known', receiver: 'MAIN' },
    vfos: [{
      receiver: 'MAIN', slot: { kind: 'unslotted' }, label: 'MAIN', frequencyHz: 14195000,
      mode: 'USB', filter: 'WIDE', isActive: true, isTxTarget: true,
    }],
    split: { status: 'known', value: false },
    dualWatch: { status: 'known', value: false },
    txTarget: { status: 'known', receiver: 'MAIN', slot: { kind: 'unslotted' }, frequencyHz: 14195000 },
    txPermit: { status: 'allowed', band: '20m' },
    scope: {
      hardwareScope: { structural: true, operational: true },
      audioFftScope: { structural: false, operational: false },
    },
    disabledReasons: [],
  };
}

describe('validateRadioViewModel', () => {
  it('accepts a well-formed view model and round-trips it unchanged', () => {
    const model = valid();
    expect(validateRadioViewModel(model)).toEqual(model);
  });

  it('accepts an unknown txTarget with its reason preserved', () => {
    const model = {
      ...valid(),
      vfos: [{ ...valid().vfos[0], isTxTarget: false }],
      txTarget: { status: 'unknown', reason: 'stale' },
      txPermit: { status: 'unknown', reason: 'tx-target-unknown' },
    };
    expect(validateRadioViewModel(model).txTarget).toEqual({ status: 'unknown', reason: 'stale' });
  });

  it('accepts a denied and an unknown txPermit distinctly', () => {
    const denied = validateRadioViewModel({
      ...valid(), txPermit: { status: 'denied', reason: 'outside-configured-ranges' },
    });
    const unknown = validateRadioViewModel({
      ...valid(),
      vfos: [{ ...valid().vfos[0], isTxTarget: false }],
      txTarget: { status: 'unknown', reason: 'not-observed' },
      txPermit: { status: 'unknown', reason: 'ranges-unconfigured' },
    });
    expect(denied.txPermit.status).toBe('denied');
    expect(unknown.txPermit.status).toBe('unknown');
    expect(denied.txPermit).not.toEqual(unknown.txPermit);
  });

  // ── B1 (review cycle 1): ActiveRx, MOR-988 §3.2 verbatim ─────────────────
  it('accepts an unknown activeReceiver — never fabricates MAIN', () => {
    expect(validateRadioViewModel({ ...valid(), activeReceiver: { status: 'unknown' } }).activeReceiver)
      .toEqual({ status: 'unknown' });
  });

  it('rejects an activeReceiver with neither known nor unknown status', () => {
    expect(() => validateRadioViewModel({ ...valid(), activeReceiver: { status: 'stale' } })).toThrow(TypeError);
  });

  // ── B2 (review cycle 1): split/dualWatch are independent BooleanFacts ────
  it('accepts split and dualWatch true at the same time (the combination the old enum could not represent)', () => {
    const model = validateRadioViewModel({
      ...valid(), split: { status: 'known', value: true }, dualWatch: { status: 'known', value: true },
    });
    expect(model.split).toEqual({ status: 'known', value: true });
    expect(model.dualWatch).toEqual({ status: 'known', value: true });
  });

  it('accepts split known while dualWatch is unknown, independently', () => {
    const model = validateRadioViewModel({
      ...valid(), split: { status: 'known', value: true }, dualWatch: { status: 'unknown' },
    });
    expect(model.split).toEqual({ status: 'known', value: true });
    expect(model.dualWatch).toEqual({ status: 'unknown' });
  });

  it('rejects a BooleanFact with a non-boolean value', () => {
    expect(() => validateRadioViewModel({ ...valid(), split: { status: 'known', value: 'yes' } })).toThrow(TypeError);
  });

  // ── B3 (review cycle 1): VfoSlot — slotted/unslotted/unknown ─────────────
  it('accepts a slotted VFO slot with an A/B id', () => {
    const model = validateRadioViewModel({
      ...valid(),
      vfos: [{ ...valid().vfos[0], slot: { kind: 'slotted', id: 'B' } }],
      txTarget: { status: 'known', receiver: 'MAIN', slot: { kind: 'slotted', id: 'B' }, frequencyHz: 14195000 },
    });
    expect(model.vfos[0].slot).toEqual({ kind: 'slotted', id: 'B' });
  });

  it('accepts an unknown VFO slot distinct from unslotted (never conflates "no A/B" with "not observed")', () => {
    const model = validateRadioViewModel({
      ...valid(), vfos: [{ ...valid().vfos[0], slot: { kind: 'unknown' }, isTxTarget: false }],
    });
    expect(model.vfos[0].slot).toEqual({ kind: 'unknown' });
    expect(model.vfos[0].slot).not.toEqual({ kind: 'unslotted' });
  });

  it('rejects a slotted VFO slot carrying an extra key', () => {
    expect(() => validateRadioViewModel({
      ...valid(), vfos: [{ ...valid().vfos[0], slot: { kind: 'slotted', id: 'A', extra: 1 } }],
    })).toThrow(TypeError);
  });

  it('rejects a VFO slot with an unrecognized kind', () => {
    expect(() => validateRadioViewModel({
      ...valid(), vfos: [{ ...valid().vfos[0], slot: { kind: 'bogus' } }],
    })).toThrow(TypeError);
  });

  // ── V1a (review cycle 1): fail-open cross-field check ────────────────────
  // Every per-field check alone accepts this object — only the cross-field
  // guard rejects it. Removing that guard would make this test fail (a
  // mutation-kill proof, since no mutation tool is wired into this repo).
  //
  // C1 (review cycle 2): the fixture below deliberately clears isTxTarget so
  // this object violates ONLY the fail-open invariant — nothing else about it
  // is invalid. If the V1a check were deleted, every remaining per-field and
  // V1b check would accept this object and validateRadioViewModel would
  // return normally, so this specific test — not some other guard — is what
  // would go red.
  it('rejects txPermit "allowed" while txTarget is unknown (fail-open) — isolated from the V1b guard', () => {
    const smuggled = {
      ...valid(),
      vfos: [{ ...valid().vfos[0], isTxTarget: false }],
      txTarget: { status: 'unknown', reason: 'not-observed' },
      txPermit: { status: 'allowed', band: '20m' },
    };
    expect(() => validateRadioViewModel(smuggled)).toThrow(TypeError);
  });

  it('still accepts txPermit "allowed" when txTarget is known (the V1a guard must not over-block the legal case)', () => {
    expect(() => validateRadioViewModel(valid())).not.toThrow();
  });

  // ── V1b (review cycle 1): isTxTarget must match the known txTarget ───────
  it('rejects isTxTarget=true on a VFO whose receiver does not match a known txTarget', () => {
    const smuggled = {
      ...valid(),
      txTarget: { status: 'known', receiver: 'MAIN', slot: { kind: 'unslotted' }, frequencyHz: 14195000 },
      vfos: [{ ...valid().vfos[0], receiver: 'SUB', isTxTarget: true }],
    };
    expect(() => validateRadioViewModel(smuggled)).toThrow(TypeError);
  });

  it('rejects isTxTarget=true on any VFO when txTarget is unknown', () => {
    const smuggled = {
      ...valid(),
      txTarget: { status: 'unknown', reason: 'not-observed' },
      txPermit: { status: 'unknown', reason: 'tx-target-unknown' },
      vfos: [{ ...valid().vfos[0], isTxTarget: true }],
    };
    expect(() => validateRadioViewModel(smuggled)).toThrow(TypeError);
  });

  it('rejects isTxTarget=true on the matching receiver but the wrong slot', () => {
    const smuggled = {
      ...valid(),
      vfoScheme: 'ab' as const,
      txTarget: { status: 'known', receiver: 'MAIN', slot: { kind: 'slotted', id: 'A' }, frequencyHz: 14195000 },
      vfos: [{ ...valid().vfos[0], slot: { kind: 'slotted' as const, id: 'B' as const }, isTxTarget: true }],
    };
    expect(() => validateRadioViewModel(smuggled)).toThrow(TypeError);
  });

  it('accepts isTxTarget=true only on the VFO whose receiver AND slot both match', () => {
    const legal = {
      ...valid(),
      vfoScheme: 'ab' as const,
      txTarget: { status: 'known', receiver: 'MAIN', slot: { kind: 'slotted', id: 'A' }, frequencyHz: 14195000 },
      vfos: [
        { ...valid().vfos[0], slot: { kind: 'slotted' as const, id: 'A' as const }, isTxTarget: true },
        { ...valid().vfos[0], slot: { kind: 'slotted' as const, id: 'B' as const }, isTxTarget: false },
      ],
    };
    expect(() => validateRadioViewModel(legal)).not.toThrow();
  });

  // ── Rejects a fixture that smuggles a raw capability object or module path ──
  it('rejects a top-level raw capabilities array smuggled onto the model', () => {
    const smuggled = { ...valid(), capabilities: ['scope', 'audio', 'tx'] };
    expect(() => validateRadioViewModel(smuggled)).toThrow(TypeError);
  });

  it('rejects a module path smuggled onto the model', () => {
    const smuggled = { ...valid(), modulePath: '$lib/runtime/frontend-runtime' };
    expect(() => validateRadioViewModel(smuggled)).toThrow(TypeError);
  });

  it('rejects a raw capability object smuggled into scope', () => {
    const smuggled = {
      ...valid(),
      scope: { ...valid().scope, raw: { scope: true, audioFftAvailable: true } },
    };
    expect(() => validateRadioViewModel(smuggled)).toThrow(TypeError);
  });

  it('rejects an unexpected key inside a VFO entry', () => {
    const smuggled = { ...valid(), vfos: [{ ...valid().vfos[0], capabilityTags: ['tx'] }] };
    expect(() => validateRadioViewModel(smuggled)).toThrow(TypeError);
  });

  // ── Structural rejection per field ──────────────────────────────────────
  it('rejects a non-object', () => {
    expect(() => validateRadioViewModel(null)).toThrow(TypeError);
    expect(() => validateRadioViewModel('1/single')).toThrow(TypeError);
  });

  it('rejects an invalid vfoScheme', () => {
    expect(() => validateRadioViewModel({ ...valid(), vfoScheme: 'quad' })).toThrow(TypeError);
  });

  it('rejects a txTarget with neither known nor unknown status', () => {
    expect(() => validateRadioViewModel({ ...valid(), txTarget: { status: 'stale' } })).toThrow(TypeError);
  });

  it('rejects a txPermit reason that does not belong to its status', () => {
    // 'tx-target-unknown' is a valid reason for 'unknown', not 'denied'.
    expect(() => validateRadioViewModel({
      ...valid(), txPermit: { status: 'denied', reason: 'tx-target-unknown' },
    })).toThrow(TypeError);
  });

  it('rejects a non-boolean Availability field', () => {
    expect(() => validateRadioViewModel({
      ...valid(),
      scope: { ...valid().scope, hardwareScope: { structural: 'yes', operational: true } },
    })).toThrow(TypeError);
  });

  it('rejects a disabledReasons entry with an unknown code', () => {
    expect(() => validateRadioViewModel({
      ...valid(), disabledReasons: [{ field: 'txTarget', code: 'operator-error' }],
    })).toThrow(TypeError);
  });
});
