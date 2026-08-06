/**
 * MOR-1308 — the semantic RIT/XIT + scan surface (vocabulary slice 8B).
 *
 * Every test names the mutation/carry-forward it pins:
 *   O1 — `ritOffset`/`xitOffset` are ONE register under TWO capability gates:
 *        exactly one offset control renders, and editing it routes through
 *        whichever underlying command v2's own `xitActive && !ritActive`
 *        selection names — never both, never neither.
 *   S3b (wrong-VFO guard) — every RIT/XIT control disables itself, AND
 *        independently refuses to dispatch, while `activeReceiver` is
 *        unobserved. `disabled` and the in-handler guard are pinned
 *        SEPARATELY per the MOR-1304 F3 lesson: `.click()` on a disabled
 *        button is a jsdom no-op regardless of the guard, so guard tests
 *        dispatch the event directly instead.
 *   `scan` per-field partial-reporter gate — a radio that has only ever
 *        reported `scanning` surfaces exactly that field, no more.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import RitXitScanSurface, { OFFSET_MAX, OFFSET_MIN, OFFSET_STEP, UNKNOWN_TEXT } from '../RitXitScanSurface.svelte';
import { topologyFixtures, withRitXit, withScan } from '../fixtures/topologies';
import type {
  Availability, RadioViewModel, RitXitField, RitXitViewModel, ScanField, ScanViewModel,
} from '../radio-view-model';

const ON: Availability = { structural: true, operational: true };

const base = (): RadioViewModel => withScan(withRitXit(topologyFixtures['1/single']));
const withRx = (over: Partial<RitXitViewModel>): RadioViewModel => {
  const view = base();
  return { ...view, ritXit: { ...view.ritXit!, ...over } };
};
const withSc = (over: Partial<ScanViewModel>): RadioViewModel => {
  const view = base();
  return { ...view, scan: { ...view.scan!, ...over } };
};
const unread = <T>(availability: Availability = ON): RitXitField<T> =>
  ({ reading: { status: 'unknown' }, availability });
const known = <T>(value: T, availability: Availability = ON): RitXitField<T> =>
  ({ reading: { status: 'known', value }, availability });
const unknownActive: RadioViewModel = { ...base(), activeReceiver: { status: 'unknown' } };

let target: HTMLDivElement;
beforeEach(() => { target = document.createElement('div'); document.body.appendChild(target); });
afterEach(() => { target.remove(); });

type Handlers = {
  onRitToggle?: () => void;
  onXitToggle?: () => void;
  onRitOffsetChange?: (hz: number) => void;
  onXitOffsetChange?: (hz: number) => void;
  onClear?: () => void;
  onScanStart?: (type: number) => void;
  onScanStop?: () => void;
  onResumeModeChange?: (mode: number) => void;
};

function render(view: RadioViewModel, handlers: Handlers = {}) {
  const component = mount(RitXitScanSurface, { target, props: { view, ...handlers } });
  flushSync();
  const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
  return {
    dispose: () => unmount(component),
    root: () => q('[data-testid="ritxit-scan-surface"]'),
    el: (id: string) => q<HTMLElement>(`[data-testid="${id}"]`),
    text: (id: string) => q<HTMLElement>(`[data-testid="${id}"]`)?.textContent?.trim(),
    input: () => q<HTMLInputElement>('[data-testid="ritxit-offset"] input'),
    all: (id: string) => target.querySelectorAll(`[data-testid="${id}"]`),
  };
}
/** MOR-1304 F3 recipe: bypasses jsdom's disabled-button `.click()` no-op. */
const bypassClick = (el: HTMLElement) => el.dispatchEvent(new MouseEvent('click', { bubbles: true }));

describe('structural presence: absent groups render nothing extra', () => {
  it('renders nothing when neither ritXit nor scan is present', () => {
    const r = render(topologyFixtures['1/single']);
    expect(r.root()).toBeNull();
    r.dispose();
  });

  it('renders only the ritXit row when scan is absent', () => {
    const r = render(withRitXit(topologyFixtures['1/single']));
    expect(r.el('ritxit')).not.toBeNull();
    expect(r.el('scan')).toBeNull();
    r.dispose();
  });

  it('renders only the scan row when ritXit is absent', () => {
    const r = render(withScan(topologyFixtures['1/single']));
    expect(r.el('scan')).not.toBeNull();
    expect(r.el('ritxit')).toBeNull();
    r.dispose();
  });

  it('shows only RIT when the radio has no XIT capability, offset stays present', () => {
    const r = render(withRx({ xitActive: unread<boolean>(OFF_AVAIL) }));
    expect(r.el('ritxit-rit-toggle')).not.toBeNull();
    expect(r.el('ritxit-xit-toggle')).toBeNull();
    expect(r.el('ritxit-offset')).not.toBeNull();
    r.dispose();
  });

  it('shows only XIT when the radio has no RIT capability, offset stays present', () => {
    const r = render(withRx({ ritActive: unread<boolean>(OFF_AVAIL) }));
    expect(r.el('ritxit-xit-toggle')).not.toBeNull();
    expect(r.el('ritxit-rit-toggle')).toBeNull();
    expect(r.el('ritxit-offset')).not.toBeNull();
    r.dispose();
  });
});

const OFF_AVAIL: Availability = { structural: false, operational: false };

describe('unread facts render honestly, never fabricated', () => {
  it('shows an unread offset as unknown text with the slider at 0, not a guessed position', () => {
    const r = render(withRx({ ritOffset: unread<number>(), xitOffset: unread<number>() }));
    expect(r.text('ritxit-offset-value')).toBe(UNKNOWN_TEXT);
    expect(r.input()!.valueAsNumber).toBe(0);
    expect(r.el('ritxit-offset')!.dataset.observed).toBe('false');
    r.dispose();
  });

  // F3 (fix round, verify-MOR-1308 M6/M7): activeReceiver stays KNOWN here —
  // isolates the offset's OWN observation gate from the S3b wrong-VFO guard.
  // Both halves of "refuse an edit to an unobserved offset" pinned
  // independently: the `disabled` attribute (M7) and the in-handler guard,
  // bypassed via a direct dispatch (M6).
  it('disables the offset slider while the offset itself is unread (activeReceiver known)', () => {
    const r = render(withRx({ ritOffset: unread<number>(), xitOffset: unread<number>() }));
    expect(r.input()!.disabled).toBe(true);
    r.dispose();
  });

  it('refuses an offset edit dispatched directly at the input while the offset is unread, bypassing disabled', () => {
    const onRitOffsetChange = vi.fn();
    const onXitOffsetChange = vi.fn();
    const r = render(
      withRx({ ritOffset: unread<number>(), xitOffset: unread<number>() }),
      { onRitOffsetChange, onXitOffsetChange },
    );
    const input = r.input()!;
    input.value = '300';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(onRitOffsetChange).not.toHaveBeenCalled();
    expect(onXitOffsetChange).not.toHaveBeenCalled();
    r.dispose();
  });
});

describe('S3b wrong-VFO guard: every RIT/XIT control fails closed while activeReceiver is unknown', () => {
  it('disables the RIT toggle', () => {
    const r = render({ ...withRitXit(topologyFixtures['1/single']), activeReceiver: { status: 'unknown' } });
    expect(r.el('ritxit-rit-toggle')!.hasAttribute('disabled')).toBe(true);
    r.dispose();
  });

  it('refuses to toggle RIT even when the click bypasses the disabled attribute', () => {
    const onRitToggle = vi.fn();
    const r = render(
      { ...withRitXit(topologyFixtures['1/single']), activeReceiver: { status: 'unknown' } }, { onRitToggle },
    );
    bypassClick(r.el('ritxit-rit-toggle')!);
    flushSync();
    expect(onRitToggle).not.toHaveBeenCalled();
    r.dispose();
  });

  it('disables the XIT toggle', () => {
    const r = render({ ...withRitXit(topologyFixtures['1/single']), activeReceiver: { status: 'unknown' } });
    expect(r.el('ritxit-xit-toggle')!.hasAttribute('disabled')).toBe(true);
    r.dispose();
  });

  it('refuses to toggle XIT even when the click bypasses the disabled attribute', () => {
    const onXitToggle = vi.fn();
    const r = render(
      { ...withRitXit(topologyFixtures['1/single']), activeReceiver: { status: 'unknown' } }, { onXitToggle },
    );
    bypassClick(r.el('ritxit-xit-toggle')!);
    flushSync();
    expect(onXitToggle).not.toHaveBeenCalled();
    r.dispose();
  });

  it('disables the offset slider', () => {
    const r = render(unknownActive);
    expect(r.input()!.disabled).toBe(true);
    r.dispose();
  });

  it('refuses an offset edit dispatched directly at the input, bypassing disabled', () => {
    const onRitOffsetChange = vi.fn();
    const onXitOffsetChange = vi.fn();
    const r = render(unknownActive, { onRitOffsetChange, onXitOffsetChange });
    const input = r.input()!;
    input.value = '500';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(onRitOffsetChange).not.toHaveBeenCalled();
    expect(onXitOffsetChange).not.toHaveBeenCalled();
    r.dispose();
  });

  it('disables Clear', () => {
    const r = render(unknownActive);
    expect(r.el('ritxit-clear')!.hasAttribute('disabled')).toBe(true);
    r.dispose();
  });

  it('refuses to Clear even when the click bypasses the disabled attribute', () => {
    const onClear = vi.fn();
    const r = render(unknownActive, { onClear });
    bypassClick(r.el('ritxit-clear')!);
    flushSync();
    expect(onClear).not.toHaveBeenCalled();
    r.dispose();
  });

  it('re-enables every RIT/XIT control once activeReceiver is known', () => {
    const r = render(base());
    for (const id of ['ritxit-rit-toggle', 'ritxit-clear']) expect(r.el(id)!.hasAttribute('disabled')).toBe(false);
    expect(r.input()!.disabled).toBe(false);
    r.dispose();
  });
});

describe('F2 (fix round): RIT/XIT toggles fail closed on their OWN unobserved reading', () => {
  // activeReceiver stays KNOWN throughout this block — isolates the field's
  // own observation gate from the S3b wrong-VFO guard above.
  it('disables the RIT toggle while ritActive itself is unread', () => {
    const r = render(withRx({ ritActive: unread<boolean>() }));
    expect(r.el('ritxit-rit-toggle')!.hasAttribute('disabled')).toBe(true);
    r.dispose();
  });

  it('refuses to toggle RIT even when the click bypasses disabled, while ritActive is unread', () => {
    const onRitToggle = vi.fn();
    const r = render(withRx({ ritActive: unread<boolean>() }), { onRitToggle });
    bypassClick(r.el('ritxit-rit-toggle')!);
    flushSync();
    expect(onRitToggle).not.toHaveBeenCalled();
    r.dispose();
  });

  it('omits aria-pressed — never "false" — for an unread RIT reading', () => {
    const r = render(withRx({ ritActive: unread<boolean>() }));
    expect(r.el('ritxit-rit-toggle')!.hasAttribute('aria-pressed')).toBe(false);
    r.dispose();
  });

  it('disables the XIT toggle while xitActive itself is unread', () => {
    const r = render(withRx({ xitActive: unread<boolean>() }));
    expect(r.el('ritxit-xit-toggle')!.hasAttribute('disabled')).toBe(true);
    r.dispose();
  });

  it('refuses to toggle XIT even when the click bypasses disabled, while xitActive is unread', () => {
    const onXitToggle = vi.fn();
    const r = render(withRx({ xitActive: unread<boolean>() }), { onXitToggle });
    bypassClick(r.el('ritxit-xit-toggle')!);
    flushSync();
    expect(onXitToggle).not.toHaveBeenCalled();
    r.dispose();
  });

  it('omits aria-pressed — never "false" — for an unread XIT reading', () => {
    const r = render(withRx({ xitActive: unread<boolean>() }));
    expect(r.el('ritxit-xit-toggle')!.hasAttribute('aria-pressed')).toBe(false);
    r.dispose();
  });

  it('shows aria-pressed once the reading is known, RIT on / XIT off', () => {
    const r = render(withRx({ ritActive: known(true), xitActive: known(false) }));
    expect(r.el('ritxit-rit-toggle')!.getAttribute('aria-pressed')).toBe('true');
    expect(r.el('ritxit-xit-toggle')!.getAttribute('aria-pressed')).toBe('false');
    r.dispose();
  });

  it('CLEAR stays ungated by field observation — writes freq:0 absolutely, not a read-modify-write', () => {
    const onClear = vi.fn();
    const r = render(withRx({ ritActive: unread<boolean>(), xitActive: unread<boolean>() }), { onClear });
    expect(r.el('ritxit-clear')!.hasAttribute('disabled')).toBe(false);
    r.el('ritxit-clear')!.click();
    flushSync();
    expect(onClear).toHaveBeenCalledExactlyOnceWith();
    r.dispose();
  });
});

describe('F2 (fix round): scan-toggle aria-pressed is honest about an unobserved scanning reading', () => {
  it('omits aria-pressed — never "false" — while scanning itself is unread', () => {
    const r = render(withSc({ scanning: unreadScan<boolean>() }));
    expect(r.el('scan-toggle')!.hasAttribute('aria-pressed')).toBe(false);
    r.dispose();
  });

  it('shows aria-pressed="false" once scanning is known idle', () => {
    const r = render(withSc({ scanning: knownScan(false) }));
    expect(r.el('scan-toggle')!.getAttribute('aria-pressed')).toBe('false');
    r.dispose();
  });
});

describe('O1: one offset register under two capability gates', () => {
  it('renders exactly one offset control, never two', () => {
    const r = render(withRx({ ritActive: known(true), xitActive: known(false) }));
    expect(r.all('ritxit-offset').length).toBe(1);
    r.dispose();
  });

  it('routes an edit to onRitOffsetChange when RIT leads (v2 formula: xitActive && !ritActive)', () => {
    const onRitOffsetChange = vi.fn();
    const onXitOffsetChange = vi.fn();
    const r = render(
      withRx({ ritActive: known(true), xitActive: known(false) }), { onRitOffsetChange, onXitOffsetChange },
    );
    const input = r.input()!;
    input.value = '300';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(onRitOffsetChange).toHaveBeenCalledExactlyOnceWith(300);
    expect(onXitOffsetChange).not.toHaveBeenCalled();
    r.dispose();
  });

  it('routes an edit to onXitOffsetChange when XIT leads (xitActive && !ritActive)', () => {
    const onRitOffsetChange = vi.fn();
    const onXitOffsetChange = vi.fn();
    const r = render(
      withRx({ ritActive: known(false), xitActive: known(true) }), { onRitOffsetChange, onXitOffsetChange },
    );
    const input = r.input()!;
    input.value = '-300';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(onXitOffsetChange).toHaveBeenCalledExactlyOnceWith(-300);
    expect(onRitOffsetChange).not.toHaveBeenCalled();
    r.dispose();
  });

  it('falls back to onRitOffsetChange when neither is active, mirroring v2 exactly', () => {
    const onRitOffsetChange = vi.fn();
    const onXitOffsetChange = vi.fn();
    const r = render(
      withRx({ ritActive: known(false), xitActive: known(false) }), { onRitOffsetChange, onXitOffsetChange },
    );
    const input = r.input()!;
    input.value = '0';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(onRitOffsetChange).toHaveBeenCalledExactlyOnceWith(0);
    expect(onXitOffsetChange).not.toHaveBeenCalled();
    r.dispose();
  });

  it('shows the identical displayed value regardless of which side leads (same register)', () => {
    const rLead = render(withRx({ ritActive: known(true), xitActive: known(false), ritOffset: known(250), xitOffset: known(250) }));
    const ritText = rLead.text('ritxit-offset-value');
    rLead.dispose();
    const xLead = render(withRx({ ritActive: known(false), xitActive: known(true), ritOffset: known(250), xitOffset: known(250) }));
    expect(xLead.text('ritxit-offset-value')).toBe(ritText);
    xLead.dispose();
  });

  it('exposes v2\'s own -9999..9999 Hz / 50 Hz-step bounds (O2)', () => {
    const r = render(base());
    const input = r.input()!;
    expect(Number(input.min)).toBe(OFFSET_MIN);
    expect(Number(input.max)).toBe(OFFSET_MAX);
    expect(Number(input.step)).toBe(OFFSET_STEP);
    r.dispose();
  });
});

describe('scan: per-field ever-reported gate (partial reporter, no capability tag)', () => {
  it('surfaces only scanning when scanType/scanResumeMode were never reported', () => {
    const r = render(withSc({
      scanType: unreadScan<number>(OFF_AVAIL), scanResumeMode: unreadScan<number>(OFF_AVAIL),
    }));
    expect(r.el('scan-status')).not.toBeNull();
    expect(r.el('scan-type-value')).toBeNull();
    expect(r.el('scan-resume-value')).toBeNull();
    r.dispose();
  });

  it('surfaces every field once all three have been reported', () => {
    const r = render(base());
    expect(r.el('scan-status')).not.toBeNull();
    expect(r.el('scan-type-value')).not.toBeNull();
    expect(r.el('scan-resume-value')).not.toBeNull();
    r.dispose();
  });
});

function unreadScan<T>(availability: Availability = ON): ScanField<T> {
  return { reading: { status: 'unknown' }, availability };
}
function knownScan<T>(value: T, availability: Availability = ON): ScanField<T> {
  return { reading: { status: 'known', value }, availability };
}

describe('scan start/stop: guarded on a KNOWN scanning state, start also on a known type', () => {
  it('disables the toggle while scanning itself is unobserved', () => {
    const r = render(withSc({ scanning: unreadScan<boolean>() }));
    expect(r.el('scan-toggle')!.hasAttribute('disabled')).toBe(true);
    r.dispose();
  });

  it('refuses to dispatch either start or stop when the click bypasses disabled', () => {
    const onScanStart = vi.fn();
    const onScanStop = vi.fn();
    const r = render(withSc({ scanning: unreadScan<boolean>() }), { onScanStart, onScanStop });
    bypassClick(r.el('scan-toggle')!);
    flushSync();
    expect(onScanStart).not.toHaveBeenCalled();
    expect(onScanStop).not.toHaveBeenCalled();
    r.dispose();
  });

  it('disables start while idle and scanType has never been reported (no type to resume)', () => {
    const r = render(withSc({ scanning: knownScan(false), scanType: unreadScan<number>(OFF_AVAIL) }));
    expect(r.el('scan-toggle')!.hasAttribute('disabled')).toBe(true);
    r.dispose();
  });

  it('starts the last-observed scan type, never a fabricated one', () => {
    const onScanStart = vi.fn();
    const r = render(withSc({ scanning: knownScan(false), scanType: knownScan(0x22) }), { onScanStart });
    r.el('scan-toggle')!.click();
    flushSync();
    expect(onScanStart).toHaveBeenCalledExactlyOnceWith(0x22);
    r.dispose();
  });

  // F3 (fix round, verify-MOR-1308 M10): `scanning` is KNOWN idle here (the
  // button IS disabled in this state, since `!scanningOn && !usable(scanType)`
  // — so the attribute alone would satisfy a naive assertion). The bypass
  // dispatch is the only way to prove the handler itself never falls through
  // to a fabricated `type: 0`, unlike the sibling test above which only
  // covers the `scanning`-unobserved early-return path.
  it('never fabricates type 0 via a bypassed click while idle and scanType is unobserved', () => {
    const onScanStart = vi.fn();
    const r = render(
      withSc({ scanning: knownScan(false), scanType: unreadScan<number>(OFF_AVAIL) }), { onScanStart },
    );
    bypassClick(r.el('scan-toggle')!);
    flushSync();
    expect(onScanStart).not.toHaveBeenCalled();
    r.dispose();
  });

  it('stops an active scan regardless of scanType observation', () => {
    const onScanStop = vi.fn();
    const r = render(withSc({ scanning: knownScan(true), scanType: unreadScan<number>(OFF_AVAIL) }), { onScanStop });
    r.el('scan-toggle')!.click();
    flushSync();
    expect(onScanStop).toHaveBeenCalledExactlyOnceWith();
    r.dispose();
  });
});

describe('scan resume mode: a single honest cycle over the raw masked value', () => {
  it('disables the cycle control while resumeMode is unobserved', () => {
    const r = render(withSc({ scanResumeMode: unreadScan<number>() }));
    expect(r.el('scan-resume-cycle')!.hasAttribute('disabled')).toBe(true);
    r.dispose();
  });

  it('refuses to dispatch when the click bypasses disabled', () => {
    const onResumeModeChange = vi.fn();
    const r = render(withSc({ scanResumeMode: unreadScan<number>() }), { onResumeModeChange });
    bypassClick(r.el('scan-resume-cycle')!);
    flushSync();
    expect(onResumeModeChange).not.toHaveBeenCalled();
    r.dispose();
  });

  it('advances the masked resume value by one, encoded as the full CI-V byte', () => {
    const onResumeModeChange = vi.fn();
    const r = render(withSc({ scanResumeMode: knownScan(1) }), { onResumeModeChange });
    r.el('scan-resume-cycle')!.click();
    flushSync();
    expect(onResumeModeChange).toHaveBeenCalledExactlyOnceWith(0xD2);
    r.dispose();
  });

  it('wraps from the last masked value back to 0xD0', () => {
    const onResumeModeChange = vi.fn();
    const r = render(withSc({ scanResumeMode: knownScan(3) }), { onResumeModeChange });
    r.el('scan-resume-cycle')!.click();
    flushSync();
    expect(onResumeModeChange).toHaveBeenCalledExactlyOnceWith(0xD0);
    r.dispose();
  });

  // F1 (fix round) — the backend validator is `if resume_mode not in
  // range(0xD0, 0xD4): raise ValueError(...)` (control.py:2283-2289). Every
  // one of the four masked cycle positions must dispatch a value inside that
  // accepted range — the assertion whose absence let the masked-only bug ship.
  it.each([
    [0, 0xD1], [1, 0xD2], [2, 0xD3], [3, 0xD0],
  ])('dispatches an accepted-range value (0xD0..0xD3) from masked %i', (masked, expected) => {
    const onResumeModeChange = vi.fn();
    const r = render(withSc({ scanResumeMode: knownScan(masked) }), { onResumeModeChange });
    r.el('scan-resume-cycle')!.click();
    flushSync();
    expect(onResumeModeChange).toHaveBeenCalledExactlyOnceWith(expected);
    expect(expected).toBeGreaterThanOrEqual(0xD0);
    expect(expected).toBeLessThanOrEqual(0xD3);
    r.dispose();
  });
});
