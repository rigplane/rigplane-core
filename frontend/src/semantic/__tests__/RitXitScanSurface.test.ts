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
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRawSnippet, flushSync, mount, unmount } from 'svelte';
import RitXitScanSurface, {
  DF_SPANS, OFFSET_MAX, OFFSET_MIN, OFFSET_STEP, RESUME_MODES, SCAN_TYPES, UNKNOWN_TEXT,
} from '../RitXitScanSurface.svelte';
import type { RitXitScanInstrumentHandles } from '../RitXitScanInstrumentHost.svelte';
import RitXitScanInstrumentHostFixture from './fixtures/RitXitScanInstrumentHostFixture.svelte';
import { topologyFixtures, withRitXit, withScan } from '../fixtures/topologies';
import type {
  Availability, RadioViewModel, RitXitField, RitXitViewModel, ScanField, ScanViewModel,
} from '../radio-view-model';

const ON: Availability = { structural: true, operational: true };
const SOURCE = readFileSync('src/semantic/RitXitScanSurface.svelte', 'utf8');
const HOST_SOURCE = readFileSync('src/semantic/RitXitScanInstrumentHost.svelte', 'utf8');

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
  presentation?: 'grouped' | 'independent';
  ritDomain?: import('$lib/types/capabilities').ControlDomain | null;
};

function render(view: RadioViewModel, handlers: Handlers = {}) {
  const component = mount(RitXitScanInstrumentHostFixture, { target, props: { view, ...handlers } });
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

/**
 * MOR-2425 restore — the TYPE/SPAN/RESUME groups live entirely in the SCAN
 * row, which never renders `handles` (that only happens inside `{#if rx}`).
 * Mounting `RitXitScanSurface` directly with a stub `handles` (never invoked
 * for a scan-only view) avoids widening `RitXitScanInstrumentHostFixture`'s
 * fixed prop list for props only these tests need.
 */
const stubHandles: RitXitScanInstrumentHandles = {
  rit: createRawSnippet(() => ({ render: () => '<span></span>' })),
  xit: createRawSnippet(() => ({ render: () => '<span></span>' })),
  clear: createRawSnippet(() => ({ render: () => '<span></span>' })),
};
type ScanHandlers = {
  onScanStart?: (type: number) => void;
  onScanStop?: () => void;
  onDfSpanChange?: (span: number) => void;
  onResumeModeChange?: (mode: number) => void;
  scanCapable?: boolean;
};
function renderScan(view: RadioViewModel, handlers: ScanHandlers = {}) {
  const component = mount(RitXitScanSurface, {
    target, props: { view, handles: stubHandles, ...handlers },
  });
  flushSync();
  const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
  return {
    dispose: () => unmount(component),
    el: (id: string) => q<HTMLElement>(`[data-testid="${id}"]`),
    all: (id: string) => target.querySelectorAll(`[data-testid="${id}"]`),
  };
}

describe('current-input action bindings', () => {
  // MOR-2425 restore: the single-action resume CYCLE is gone (superseded by
  // four explicit literal-value buttons below), so this no longer pins a
  // `bindActionInstrument` action — only the toggles remain.
  it('uses toggles for RIT, XIT and scan', () => {
    expect(SOURCE).toContain('bindToggleInstrument');
    expect(HOST_SOURCE).toContain('const ritToggle = bindToggleInstrument');
    expect(HOST_SOURCE).toContain('const xitToggle = bindToggleInstrument');
    expect(SOURCE).toContain('const scanToggle = bindToggleInstrument');
  });

  it('keeps RIT and XIT callbacks as zero-argument intents', () => {
    expect(HOST_SOURCE).toContain('invoke: () => onRitToggle?.()');
    expect(HOST_SOURCE).toContain('invoke: () => onXitToggle?.()');
  });
});

describe('hosted finite placement', () => {
  it.each(['grouped', 'independent'] as const)('preserves RIT, XIT, offset, CLEAR order in %s', (presentation) => {
    const r = render(base(), { presentation });
    const order = [...target.querySelectorAll('[data-testid="ritxit"] button, [data-testid="ritxit"] label')]
      .map(element => element.getAttribute('data-testid'));
    expect(order).toEqual(['ritxit-rit-toggle', 'ritxit-xit-toggle', 'ritxit-offset', 'ritxit-clear']);
    expect(r.all('ritxit-offset')).toHaveLength(1);
    expect(r.all('scan')).toHaveLength(1);
    r.dispose();
  });
});

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

describe('scan start/stop: guarded on a KNOWN scanning state only (MOR-1495 review R2)', () => {
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

  // Review R2 (verifier-caught bootstrap deadlock): CI-V 0x0E is SET-ONLY,
  // so `scanType` can never become "known" before a scan has ever been
  // started — gating START on it made a cold start impossible, since
  // nothing could ever send the first scan_start that would have made the
  // field known. `scanning` alone is enough to enable START now; the type
  // is owned locally (below), never observed.
  it('does NOT disable start merely because scanType has never been reported', () => {
    const r = render(withSc({ scanning: knownScan(false), scanType: unreadScan<number>(OFF_AVAIL) }));
    expect(r.el('scan-toggle')!.hasAttribute('disabled')).toBe(false);
    r.dispose();
  });

  // MOR-2425 restore supersedes this title's OLD framing ("always starts
  // with PROG regardless of the last-observed type"): the new contract is
  // "starts with the surface's own SELECTED type; default still PROG until
  // a TYPE button changes it" (see the `scan TYPE selection` describe block
  // below for the selection half). This case never clicks a TYPE button, so
  // the selection stays at its default and the assertion is unchanged.
  it('starts with the surface\'s own SELECTED type (default PROG, 0x01) via a normal click, from a cold start where scanType has never been reported', () => {
    const onScanStart = vi.fn();
    const r = render(
      withSc({ scanning: knownScan(false), scanType: unreadScan<number>(OFF_AVAIL) }), { onScanStart },
    );
    r.el('scan-toggle')!.click();
    flushSync();
    expect(onScanStart).toHaveBeenCalledExactlyOnceWith(0x01);
    r.dispose();
  });

  // MOR-2425 restore supersedes this title's OLD framing too — same
  // rationale as above: the SELECTED type (not the OBSERVED `scanType`
  // fact) still decides START's argument; no TYPE button is clicked here,
  // so the selection is still the default.
  it('starts with the locally SELECTED type (still the default here) even when a DIFFERENT type happens to be the last-observed one — type is never read from the observed fact', () => {
    const onScanStart = vi.fn();
    const r = render(withSc({ scanning: knownScan(false), scanType: knownScan(0x22) }), { onScanStart });
    r.el('scan-toggle')!.click();
    flushSync();
    expect(onScanStart).toHaveBeenCalledExactlyOnceWith(0x01);
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

/**
 * MOR-2425 restore. `renderScan` mounts `RitXitScanSurface` directly (no
 * `ritXit` group, so `handles` is never invoked) — the shared fixture-backed
 * `render()`/`withSc()` above cannot express `scanCapable` or `onDfSpanChange`
 * without widening `RitXitScanInstrumentHostFixture`'s fixed prop list, which
 * no other test in this file needs.
 */
function coldStart(over: Partial<ScanViewModel> = {}): RadioViewModel {
  const view = withScan(topologyFixtures['1/single']);
  return {
    ...view,
    scan: {
      ...view.scan!, scanning: knownScan(false), scanType: unreadScan<number>(OFF_AVAIL), ...over,
    },
  };
}
const hexId = (value: number) => `0x${value.toString(16).padStart(2, '0')}`;
const scanTypeCases = SCAN_TYPES.map(([value, label]) => ({ value, label, hex: hexId(value) }));
const dfSpanCases = DF_SPANS.map(([value, label]) => ({ value, label, hex: hexId(value) }));
const resumeCases = RESUME_MODES.map(([value, label]) => ({ value, label, hex: hexId(value) }));

describe('scan TYPE selection: six buttons restore v2.11.1, MOR-2425', () => {
  it('is hidden entirely — not merely disabled — while scanCapable is false', () => {
    const r = renderScan(coldStart(), { scanCapable: false });
    expect(r.el('scan-type-group')).toBeNull();
    r.dispose();
  });

  it.each(scanTypeCases)(
    '$label ($hex) dispatches onScanStart with its own byte exactly once, from a cold start where scanType has never been reported',
    ({ value, hex }) => {
      const onScanStart = vi.fn();
      const r = renderScan(coldStart(), { onScanStart, scanCapable: true });
      r.el(`scan-type-${hex}`)!.click();
      flushSync();
      expect(onScanStart).toHaveBeenCalledExactlyOnceWith(value);
      r.dispose();
    },
  );

  it('restarts an ACTIVE scan with the newly selected type — v2.11.1 handleTypeClick fires unconditionally, even mid-scan', () => {
    const onScanStart = vi.fn();
    const r = renderScan(coldStart({ scanning: knownScan(true) }), { onScanStart, scanCapable: true });
    r.el('scan-type-0x22')!.click();
    flushSync();
    expect(onScanStart).toHaveBeenCalledExactlyOnceWith(0x22);
    r.dispose();
  });

  it('a SUBSEQUENT click on scan-toggle START reuses the just-selected type, not the PROG default', () => {
    const onScanStart = vi.fn();
    const r = renderScan(coldStart(), { onScanStart, scanCapable: true });
    r.el('scan-type-0x22')!.click();
    r.el('scan-toggle')!.click();
    flushSync();
    expect(onScanStart).toHaveBeenNthCalledWith(1, 0x22);
    expect(onScanStart).toHaveBeenNthCalledWith(2, 0x22);
    r.dispose();
  });
});

describe('ΔF SPAN: seven buttons, visible only for the ΔF type, MOR-2425', () => {
  it('is absent from the DOM by default (PROG selected, no active ΔF scan)', () => {
    const r = renderScan(coldStart(), { scanCapable: true });
    expect(r.el('scan-span-group')).toBeNull();
    r.dispose();
  });

  it('stays absent while scanCapable is false — the TYPE group that would select ΔF is itself hidden', () => {
    const r = renderScan(coldStart(), { scanCapable: false });
    expect(r.el('scan-type-group')).toBeNull();
    expect(r.el('scan-span-group')).toBeNull();
    r.dispose();
  });

  it.each(dfSpanCases)(
    '$label ($hex) dispatches onDfSpanChange with its own byte exactly once, once ΔF is SELECTED — never observed',
    ({ value, hex }) => {
      const onDfSpanChange = vi.fn();
      // Cold start: scanType has never been reported at all (matches the
      // TYPE describe block's bootstrap case) — only the LOCAL selection
      // (set by clicking the ΔF type button) may decide SPAN's visibility.
      const r = renderScan(coldStart(), { onDfSpanChange, scanCapable: true });
      r.el('scan-type-0x03')!.click();
      flushSync();
      r.el(`scan-span-${hex}`)!.click();
      flushSync();
      expect(onDfSpanChange).toHaveBeenCalledExactlyOnceWith(value);
      r.dispose();
    },
  );

  it('also shows once an OBSERVED active scan reports ΔF, independent of the local selection', () => {
    const r = renderScan(
      coldStart({ scanning: knownScan(true), scanType: knownScan(0x03) }), { scanCapable: true },
    );
    expect(r.el('scan-span-group')).not.toBeNull();
    r.dispose();
  });
});

describe('scan RESUME mode: four explicit literal buttons, MOR-2425 (replacing the cycle)', () => {
  it('renders only when scanResumeMode is structural', () => {
    const r = renderScan(coldStart({
      scanResumeMode: { reading: { status: 'unknown' }, availability: { structural: false, operational: false } },
    }), { scanCapable: true });
    expect(r.el('scan-resume-group')).toBeNull();
    r.dispose();
  });

  it('is hidden entirely — not merely disabled — while scanCapable is false', () => {
    const r = renderScan(coldStart(), { scanCapable: false });
    expect(r.el('scan-resume-group')).toBeNull();
    r.dispose();
  });

  // The load-bearing divergence from the old cycle button (which required
  // `reading.status === 'known'` to compute a next value): v2.11.1's four
  // explicit buttons carried no such precondition, and this restoration
  // preserves that — none of the four reads `scanResumeMode.reading` at all.
  it.each(resumeCases)(
    '$label ($hex) dispatches onResumeModeChange with its own literal byte exactly once, WHILE scanResumeMode is unobserved',
    ({ value, hex }) => {
      const onResumeModeChange = vi.fn();
      const r = renderScan(coldStart({ scanResumeMode: unreadScan<number>() }), { onResumeModeChange, scanCapable: true });
      r.el(`scan-resume-${hex}`)!.click();
      flushSync();
      expect(onResumeModeChange).toHaveBeenCalledExactlyOnceWith(value);
      r.dispose();
    },
  );
});
