import { SvelteMap } from 'svelte/reactivity';
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { getLocale, setLocale } from '$lib/i18n';
import { createRawSnippet, flushSync, mount, unmount, type ComponentProps } from 'svelte';
import VfoIndicatorRow, { FACT_SLOT_RESERVATIONS } from '../VfoIndicatorRow.svelte';
import type {
  Availability, RadioWideIndicatorsViewModel,
  ReceiverIndicatorField, ReceiverIndicatorViewModel,
} from '../radio-view-model';

const AVAILABLE: Availability = { structural: true, operational: true };
const known = <T>(value: T): ReceiverIndicatorField<T> => ({
  reading: { status: 'known', value }, availability: AVAILABLE,
});
const unknown = <T = never>(structural = true): ReceiverIndicatorField<T> => ({
  reading: { status: 'unknown' }, availability: { structural, operational: false },
});

function indicator(overrides: Partial<ReceiverIndicatorViewModel> = {}): ReceiverIndicatorViewModel {
  return {
    receiver: 'MAIN', availability: AVAILABLE,
    sMeter: known(0), bandwidthHz: known(2400), agcMode: known(0),
    nbActive: known(false), nrActive: known(true), notchMode: known('off'),
    attenuator: known(0), preamp: known(0), rfGain: known(0),
    digiSel: known(false), ipPlus: known(true), ...overrides,
  };
}

let component: ReturnType<typeof mount> | null = null;
let target: HTMLDivElement;

function render(props: ComponentProps<typeof VfoIndicatorRow>): HTMLElement {
  component = mount(VfoIndicatorRow, { target, props });
  flushSync();
  return target;
}

beforeEach(() => {
  target = document.createElement('div');
  document.body.appendChild(target);
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  target.remove();
});

describe('VfoIndicatorRow', () => {
  it('renders one addressed row and one real S-meter for a known finite zero', () => {
    const root = render({ indicator: indicator() });
    expect(root.querySelectorAll('[data-testid="vfo-indicator-row"]')).toHaveLength(1);
    expect(root.querySelector('[data-indicator-receiver="MAIN"]')).not.toBeNull();
    expect(root.querySelectorAll('[data-testid="receiver-s-meter"]')).toHaveLength(1);
    expect(root.querySelector('[data-testid="receiver-s-meter"] svg')).not.toBeNull();
    expect(root.querySelector('[data-testid="receiver-s-meter-unknown"]')).toBeNull();
  });

  it('renders a true unknown shell and never passes a fabricated zero to LinearSMeter', () => {
    const sMeter = createRawSnippet(() => ({
      render: () => '<span data-hosted-s-meter>host meter</span>',
    }));
    const root = render({ indicator: indicator({ sMeter: unknown() }), sMeter });
    expect(root.querySelector('[data-hosted-s-meter]')).toBeNull();
    expect(root.querySelector('[data-testid="receiver-s-meter"] svg')).toBeNull();
    // MOR-2644 correction 2: the unread box keeps its size and prints no
    // text; its accessible name carries no "unknown".
    const shell = root.querySelector('[data-testid="receiver-s-meter-unknown"]');
    expect(shell?.textContent).toBe('');
    expect(shell?.getAttribute('aria-label')).toBe('MAIN S meter');
    expect(shell?.textContent).not.toContain('—');
    expect(shell?.getAttribute('aria-label')).not.toContain('unknown');
  });

  // MOR-2688 S4b: the known + finite check enters through
  // `finiteValue(readingValue(...))`. A known NaN reading takes the unread
  // shell — red under a `finiteValue` mutation that accepts NaN.
  it('renders the unread shell for a known NaN S-meter reading (MOR-2688 S4b)', () => {
    const root = render({ indicator: indicator({ sMeter: known(Number.NaN) }) });
    expect(root.querySelector('[data-testid="receiver-s-meter"] svg')).toBeNull();
    const shell = root.querySelector('[data-testid="receiver-s-meter-unknown"]');
    expect(shell).not.toBeNull();
    expect(shell?.textContent).toBe('');
    expect(shell?.getAttribute('aria-label')).toBe('MAIN S meter');
  });

  it('uses a caller-owned meter for a known reading in the established receiver seat', () => {
    const sMeter = createRawSnippet(() => ({
      render: () => '<span data-hosted-s-meter>host meter</span>',
    }));
    const root = render({ indicator: indicator(), sMeter });
    expect(root.querySelector('[data-hosted-s-meter]')?.closest('[data-testid="receiver-s-meter"]')).not.toBeNull();
    expect(root.querySelector('[data-testid="receiver-s-meter"] svg')).toBeNull();
  });

  it('preserves known false and zero as observed OFF/0 facts, not unknown', () => {
    const root = render({ indicator: indicator() });
    for (const fact of ['nb', 'digi-sel']) {
      const node = root.querySelector(`[data-indicator-fact="${fact}"]`);
      expect(node?.getAttribute('data-state')).toBe('off');
      expect(node?.textContent).toContain('OFF');
    }
    for (const fact of ['agc', 'attenuator', 'preamp', 'rf-gain']) {
      const node = root.querySelector(`[data-indicator-fact="${fact}"]`);
      expect(node?.getAttribute('data-state')).toBe('known');
      expect(node?.textContent).toContain('0');
    }
  });

  it('renders a capability-provided AGC label verbatim', () => {
    const root = render({ indicator: indicator({ agcMode: known('SLOW') }) });
    expect(root.querySelector('[data-indicator-fact="agc"]')?.textContent).toContain('AGC SLOW');
  });

  it('shows a label with no value text for unread facts, never a dash (MOR-2644)', () => {
    const root = render({ indicator: indicator({
      bandwidthHz: unknown(), agcMode: unknown(), nbActive: unknown(),
      nrActive: unknown(), notchMode: unknown(), attenuator: unknown(),
      preamp: unknown(), ipPlus: unknown(), digiSel: unknown(),
    }) });
    for (const [fact, label] of [
      ['bandwidth', 'BW'], ['agc', 'AGC'], ['nb', 'NB'], ['nr', 'NR'],
      ['notch', 'NOTCH'], ['attenuator', 'ATT'], ['preamp', 'P.AMP'],
      ['ip-plus', 'IP+'], ['digi-sel', 'DIGI-SEL'],
    ] as const) {
      const node = root.querySelector(`[data-indicator-fact="${fact}"]`);
      expect(node, `unread ${fact} keeps its element`).not.toBeNull();
      expect(node?.textContent?.trim()).toBe(label);
      expect(node?.textContent).not.toContain('—');
      expect(node?.getAttribute('data-state')).toMatch(/unknown|off/);
    }
  });

  it('keeps an unavailable structural receiver present, disabled, and explicitly unknown', () => {
    const root = render({ indicator: indicator({
      receiver: 'SUB',
      availability: { structural: true, operational: false },
      sMeter: unknown(), bandwidthHz: unknown(), agcMode: unknown(),
      nbActive: unknown(), nrActive: unknown(), notchMode: unknown(),
      attenuator: unknown(), preamp: unknown(), rfGain: unknown(),
      digiSel: unknown(), ipPlus: unknown(),
    }) });
    const row = root.querySelector('[data-testid="vfo-indicator-row"]');
    expect(row?.getAttribute('data-indicator-receiver')).toBe('SUB');
    expect(row?.getAttribute('data-indicator-operational')).toBe('false');
    expect(row?.querySelector('[data-indicator-rf]')).toBeNull();
    expect(row?.querySelectorAll('[data-state="unknown"]').length).toBeGreaterThan(0);
  });

  it('omits structurally absent facts instead of inventing an unsupported value', () => {
    const root = render({ indicator: indicator({
      bandwidthHz: unknown(false), agcMode: unknown(false), nbActive: unknown(false),
      nrActive: unknown(false), notchMode: unknown(false), attenuator: unknown(false),
      preamp: unknown(false), rfGain: unknown(false), digiSel: unknown(false),
      ipPlus: unknown(false),
    }) });
    expect(root.querySelectorAll('[data-indicator-fact]')).toHaveLength(0);
    expect(root.querySelector('[data-testid="receiver-s-meter"]')).not.toBeNull();
  });

  it('has no raw runtime-state, transport, command, or TX-controller input', () => {
    const source = readFileSync('src/semantic/VfoIndicatorRow.svelte', 'utf8')
      .replace(/<!--[\s\S]*?-->/g, '')
      .replace(/\/\*[\s\S]*?\*\//g, '');
    expect(source).not.toMatch(/radioState|ServerState|fieldStatus|\$lib\/transport|tx-controller|panel-commands/);
  });

  it('keeps source/session on the local fallback while admitting one caller-owned meter', () => {
    const source = readFileSync('src/semantic/VfoIndicatorRow.svelte', 'utf8');
    const call = source.match(/<LinearSMeter([\s\S]*?)\/>/)?.[1] ?? '';
    expect(call).toMatch(/source=\{indicator\.sMeter\.source\}/);
    expect(call).toMatch(/session=\{continuitySession\}/);
    expect(source).toMatch(/continuitySession\?: MeterContinuitySession \| null/);
    expect(source).toMatch(/sMeter\?: Snippet/);
    expect(source).toContain('{@render sMeter()}');
  });
});

function shared(): RadioWideIndicatorsViewModel {
  return {
    rfState: 'receiving', antenna: known(1), atu: known('off'),
    dialLock: known(false),
    ritActive: known(false), ritOffset: known(0),
    xitActive: known(true), xitOffset: known(0),
    actions: {
      main: AVAILABLE, sub: AVAILABLE, equalize: AVAILABLE, swap: AVAILABLE,
    },
  };
}

describe('radio-wide singleton indicators (MOR-2309)', () => {
  it('renders ANT/ATU/RIT/XIT and authority facts once while preserving false and zero', () => {
    const root = render({
      radioWide: shared(),
    });
    expect(root.querySelectorAll('[data-testid="vfo-shared-indicators"]')).toHaveLength(1);
    expect(root.querySelector('[data-indicator-fact="antenna"]')?.textContent).toContain('ANT 1');
    expect(root.querySelector('[data-indicator-fact="atu"]')?.textContent).toContain('TUNE OFF');
    expect(root.querySelector('[data-indicator-fact="rit"]')?.textContent).toContain('RIT OFF 0 Hz');
    expect(root.querySelector('[data-indicator-fact="xit"]')?.textContent).toContain('XIT ON 0 Hz');
    const rf = root.querySelector('[data-indicator-fact="rf-authority"]');
    expect(rf?.getAttribute('data-indicator-rf')).toBe('receiving');
    expect(rf?.textContent).toBe('');
  });

  // One render per test: repeated mounts accumulate in the same target and
  // querySelector would return a stale node from an earlier render.
  it('both parts unread prints exactly the label, no Hz (MOR-2644 correction 1)', () => {
    const root = render({ radioWide: {
      ...shared(),
      ritActive: unknown(), ritOffset: unknown(),
      xitActive: unknown(), xitOffset: unknown(),
    } });
    expect(root.querySelector('[data-indicator-fact="rit"]')?.textContent?.trim()).toBe('RIT');
    expect(root.querySelector('[data-indicator-fact="xit"]')?.textContent?.trim()).toBe('XIT');
    expect(root.querySelector('[data-indicator-fact="rit"]')?.textContent).not.toContain('Hz');
    expect(root.querySelector('[data-indicator-fact="xit"]')?.textContent).not.toContain('Hz');
  });

  it('state known with unread offset prints state only, no Hz (MOR-2644 correction 1)', () => {
    const root = render({ radioWide: {
      ...shared(), ritActive: known(false), ritOffset: unknown(),
    } });
    expect(root.querySelector('[data-indicator-fact="rit"]')?.textContent?.trim()).toBe('RIT OFF');
    expect(root.querySelector('[data-indicator-fact="rit"]')?.textContent).not.toContain('Hz');
  });

  it('both known prints the same text as main today (MOR-2644 correction 1)', () => {
    const root = render({ radioWide: shared() });
    expect(root.querySelector('[data-indicator-fact="rit"]')?.textContent).toContain('RIT OFF 0 Hz');
    expect(root.querySelector('[data-indicator-fact="xit"]')?.textContent).toContain('XIT ON 0 Hz');
  });

  it('keeps RF unknown quiet while retaining its state and omits unsupported facts', () => {
    const root = render({
      radioWide: {
        ...shared(), rfState: 'unknown', antenna: unknown(false), atu: unknown(),
        ritActive: unknown(), ritOffset: unknown(), xitActive: unknown(), xitOffset: unknown(),
      },
    });
    expect(root.querySelector('[data-indicator-fact="antenna"]')).toBeNull();
    const rf = root.querySelector('[data-indicator-fact="rf-authority"]');
    expect(rf?.getAttribute('data-indicator-rf')).toBe('unknown');
    expect(rf?.textContent).toBe('');
    // MOR-2644: unread radio-wide facts keep the label with no value text;
    // RIT/XIT print no Hz unit without a known offset (correction 1).
    expect(root.querySelector('[data-indicator-fact="atu"]')?.textContent?.trim()).toBe('TUNE');
    expect(root.querySelector('[data-indicator-fact="rit"]')?.textContent?.trim()).toBe('RIT');
    expect(root.querySelector('[data-indicator-fact="xit"]')?.textContent?.trim()).toBe('XIT');
    for (const fact of ['atu', 'rit', 'xit'] as const) {
      expect(root.querySelector(`[data-indicator-fact="${fact}"]`)?.textContent).not.toContain('—');
    }
  });

  // MOR-2671: both states read `TX` — the unconfirmed one is hollow (dashed
  // outline) and names itself unconfirmed in its accessible label, never a `?`.
  it.each([
    ['transmitting', 'TX'], ['uncertain', 'TX'],
  ] as const)('keeps %s RF authority visible as %s', (rfState, label) => {
    const root = render({ radioWide: { ...shared(), rfState } });
    expect(root.querySelector('[data-indicator-fact="rf-authority"]')?.textContent).toBe(label);
  });

  it('distinguishes the unconfirmed RF lamp by shape and accessible name, never by a `?`', () => {
    const confirmed = render({ radioWide: { ...shared(), rfState: 'transmitting' } });
    const confirmedLamp = confirmed.querySelector('[data-indicator-fact="rf-authority"]');
    expect(confirmedLamp?.classList.contains('tx')).toBe(true);
    expect(confirmedLamp?.hasAttribute('aria-label')).toBe(false);

    // Second render: unmount the first, otherwise querySelector in the shared
    // target returns the stale confirmed lamp from the first mount.
    if (component) unmount(component);
    component = null;
    const doubt = render({ radioWide: { ...shared(), rfState: 'uncertain' } });
    const doubtLamp = doubt.querySelector('[data-indicator-fact="rf-authority"]');
    expect(doubtLamp?.classList.contains('tx')).toBe(false);
    expect(doubtLamp?.getAttribute('data-indicator-rf')).toBe('uncertain');
    // The accessible sentence, verbatim from the en-US catalog, with no `?`.
    expect(doubtLamp?.getAttribute('aria-label')).toBe('Transmit not confirmed');
  });

  it('renders the unconfirmed lamp hollow — the dashed-outline rule its attribute selects', () => {
    const source = readFileSync('src/semantic/VfoIndicatorRow.svelte', 'utf8');
    expect(source).toMatch(/\.rf-lamp\[data-indicator-rf='uncertain'\]\s*\{[^}]*border-style:\s*dashed/);
  });

  it('marks RIT/XIT aggregate state unknown when either constituent is unknown', () => {
    const root = render({
      radioWide: {
        ...shared(), ritActive: known(false), ritOffset: unknown(),
        xitActive: unknown(), xitOffset: known(0),
      },
    });
    const rit = root.querySelector('[data-indicator-fact="rit"]');
    const xit = root.querySelector('[data-indicator-fact="xit"]');
    expect(rit?.getAttribute('data-state')).toBe('unknown');
    expect(rit?.textContent?.trim()).toBe('RIT OFF');
    expect(rit?.textContent).not.toContain('—');
    expect(rit?.textContent).not.toContain('Hz');
    expect(xit?.getAttribute('data-state')).toBe('unknown');
    expect(xit?.textContent?.trim()).toBe('XIT 0 Hz');
    expect(xit?.textContent).not.toContain('—');
  });
});


describe('MOR-2342 addressed meter appearance', () => {
  it.each(['sdr', 'standard'] as const)('never draws unknown as zero in %s', (appearance) => {
    const root = render({ indicator: indicator({ sMeter: unknown() }), appearance });
    expect(root.querySelector('[data-testid="receiver-s-meter"] svg')).toBeNull();
    // MOR-2644 correction 2: the unread box keeps its size and prints no
    // text; its accessible name carries no "unknown".
    const box = root.querySelector('[data-testid="receiver-s-meter-unknown"]');
    expect(box?.textContent).toBe('');
    expect(box?.getAttribute('aria-label')).toBe('MAIN S meter');
    expect(box?.getAttribute('aria-label')).not.toContain('unknown');
    // MOR-2644: the unread box keeps the main outer height (30px incl.
    // border). With content-box, min-height would cover content only and
    // the 1px borders would push the box to 32px.
    const source = readFileSync('src/semantic/VfoIndicatorRow.svelte', 'utf8');
    expect(source).toMatch(/\.s-meter-unknown\s*\{[^}]*min-height:\s*30px[^}]*box-sizing:\s*border-box/);
    expect(source).not.toMatch(/\.s-meter-unknown\s*\{[^}]*box-sizing:\s*content-box/);
  });
  it('selects the SDR meter without changing a confirmed zero or receiver identity', () => {
    const root = render({ indicator: indicator(), appearance: 'sdr' });
    expect(root.querySelector('[data-indicator-receiver="MAIN"]')).not.toBeNull();
    expect(root.querySelector('svg[data-variant="sdr-screen"]')).not.toBeNull();
    expect(root.querySelector('[data-testid="receiver-s-meter-unknown"]')).toBeNull();
  });
  it('renders only the supplied Standard slot label, never MAIN as A', () => {
    const root = render({ indicator: indicator(), appearance: 'standard', slotLabel: 'A' });
    expect(root.querySelector('.header-badges')?.textContent).toContain('BAR');
    expect(root.querySelector('.header-badges')?.textContent).toContain('A');
  });
  it('an unknown Standard slot keeps its badge slot and draws nothing (MOR-2644 correction 2)', () => {
    const root = render({ indicator: indicator(), appearance: 'standard' });
    const badges = [...root.querySelectorAll('.header-badges .fact')];
    expect(badges).toHaveLength(2);
    const badge = badges[1];
    expect(badge?.textContent).toBe('');
    expect(badge?.textContent).not.toContain('—');
    expect(badge?.getAttribute('data-empty')).toBe('true');
    // Same treatment as the empty RFG fact: slot reserved, frame transparent.
    const source = readFileSync('src/semantic/VfoIndicatorRow.svelte', 'utf8');
    expect(source).toMatch(
      /\.header-badges \.fact\[data-empty='true'\]\s*\{[^}]*border-color:\s*transparent/,
    );
  });
  it('bounds the rendered Standard S-meter at the historical row height', () => {
    const root = render({ indicator: indicator(), appearance: 'standard' });
    const shell = root.querySelector<HTMLElement>('[data-testid="receiver-s-meter"]')!;
    const meter = shell.querySelector<SVGElement>('svg[data-variant="vfo-wide"]')!;
    expect(shell).not.toBeNull();
    expect(meter).not.toBeNull();
    const source = readFileSync('src/semantic/VfoIndicatorRow.svelte', 'utf8');
    expect(source).toMatch(/\[data-indicator-appearance='standard'\] \.s-meter \{\s*width: 100%; max-width: 600px; height: 58px;/);
    expect(source).toContain("svg[data-variant='vfo-wide']");
  });
});


describe('RF gain display observation', () => {
  // MOR-2425/R41: no `◷`, and one accessible name for both provenances.
  it.each(['semantic', 'standard', 'sdr'] as const)('keeps the same text, accessible name and DOM footprint through current/stale/current for %s', (appearance) => {
    const current = indicator({ rfGain: { ...known(0.75), display: { state: 'current', value: 0.75 } } });
    const state = new SvelteMap([['indicator', current]]);
    render({ appearance, get indicator() { return state.get('indicator'); } });
    const node = target.querySelector('[data-indicator-fact="rf-gain"]')!;
    const text = node.textContent;
    expect(text).toContain('RFG 75%');
    expect(target.querySelector('[role="img"][aria-label="RF gain 75%"]')).toBe(node);
    expect(node.hasAttribute('tabindex')).toBe(false);
    expect(node.querySelector('.stale-cue')).toBeNull();
    flushSync(() => state.set('indicator', indicator({ rfGain: {
      ...unknown<number>(), display: { state: 'stale', value: 0.75 },
    } })));
    expect(target.querySelector('[data-indicator-fact="rf-gain"]')).toBe(node);
    expect(node.querySelector('.stale-cue')).toBeNull();
    expect(node.textContent).toBe(text);
    expect(node.getAttribute('data-state')).toBe('unknown');
    expect(node.getAttribute('data-display-state')).toBe('stale');
    expect(target.querySelector('[role="img"][aria-label="RF gain 75%"]')).toBe(node);
    expect(target.querySelector('[role="img"][aria-label*="stale"]')).toBeNull();
    expect(target.querySelector('[aria-live], button, input')).toBeNull();
    flushSync(() => state.set('indicator', current));
    expect(node.textContent).toBe(text);
    expect(node.getAttribute('data-display-state')).toBe('current');
    expect(target.querySelector('[role="img"][aria-label="RF gain 75%"]')).toBe(node);
  });
  it('does not display a strict fallback default when explicit observation is unknown', () => {
    render({ indicator: indicator({ rfGain: {
      ...known(0), display: { state: 'unknown', reason: 'not-observed' },
    } }) });
    const node = target.querySelector('[data-indicator-fact="rf-gain"]')!;
    // Owner ruling 2026-09-23: unknown shows nothing — the slot stays
    // reserved, empty and aria-hidden, never a placeholder value.
    expect(node).not.toBeNull();
    expect(node.textContent).toBe('');
    expect(node.textContent).not.toContain('—');
    expect(node.getAttribute('aria-hidden')).toBe('true');
    expect(node.getAttribute('aria-label')).toBeNull();
  });
  it('keeps a display-unsupported RFgain absent', () => {
    render({ indicator: indicator({ rfGain: { ...known(0), display: { state: 'unsupported' } } }) });
    expect(target.querySelector('[data-indicator-fact="rf-gain"]')).toBeNull();
  });

  // Owner ruling 2026-09-23: RFG exists only while RF gain is reduced.
  // Coordinator decision, same day: "reduced" is decided on the formatted
  // percentage — a raw 254 of 255 is strictly below 1 yet displays as 100%,
  // so nothing is shown (the component sets no aria-hidden while lit).
  it.each([
    ['a reduced gain (0.6) prints its percentage', 0.6, 'RFG 60%', null],
    ['a gain that rounds to 99 (raw 253 of 255) is lit', 253 / 255, 'RFG 99%', null],
    ['raw 254 of 255 is below 1 yet displays as 100 — nothing shown', 254 / 255, '', 'true'],
    ['the maximum (1) prints nothing', 1, '', 'true'],
  ] as const)('%s', (_name, value, expectedText, ariaHidden) => {
    render({ indicator: indicator({ rfGain: known(value) }) });
    const node = target.querySelector('[data-indicator-fact="rf-gain"]')!;
    expect(node, 'the slot stays in the flow').not.toBeNull();
    expect(node.textContent).toBe(expectedText);
    expect(node.getAttribute('aria-hidden')).toBe(ariaHidden);
    expect(node.getAttribute('data-empty')).toBe(expectedText ? null : 'true');
    expect(node.textContent).not.toContain('—');
    expect(node.textContent).not.toContain('?');
  });

  it('reserves wider than the widest lit text (RFG 99%) in the fact slot', () => {
    // The reservation comes from FACT_SLOT_RESERVATIONS (see the rendered
    // pin below); the stylesheet carries no width of its own to drift.
    const source = readFileSync('src/semantic/VfoIndicatorRow.svelte', 'utf8');
    expect(source).not.toMatch(/\.fact\[data-indicator-fact='rf-gain'\]\s*\{[^}]*min-inline-size/);
  });

  it('an empty RFG fact draws no visible frame while keeping the reserved slot', () => {
    render({ indicator: indicator({ rfGain: known(1) }) });
    const node = target.querySelector<HTMLElement>('[data-indicator-fact="rf-gain"]')!;
    expect(node.textContent).toBe('');
    expect(node.getAttribute('data-empty')).toBe('true');
    // The CSS the attribute keys on: the frame goes transparent while the
    // reserved width (min-inline-size) stays — never an empty bordered box.
    const source = readFileSync('src/semantic/VfoIndicatorRow.svelte', 'utf8');
    expect(source).toMatch(
      /\.fact\[data-indicator-fact='rf-gain'\]\[data-empty='true'\]\s*\{[^}]*border-color:\s*transparent/,
    );
    expect(node.style.minInlineSize).toBe(`${FACT_SLOT_RESERVATIONS['rf-gain']}ch`);
  });

  // MOR-2644 correction 3 (owner rule 2026-09-21): every fact reserves the
  // width of its widest lit text, the same way RFG does — jsdom has no
  // layout, so the reserved min-inline-size is asserted against the widest
  // rendered text the component computes (exported for this test).
  it.each([
    ['bandwidth', 'BW 9999 Hz'], ['agc', 'AGC TUNING'], ['nb', 'NB OFF'],
    ['nr', 'NR OFF'], ['notch', 'NOTCH MANUAL'], ['attenuator', 'ATT 45 dB'],
    ['preamp', 'P.AMP 2'], ['ip-plus', 'IP+ OFF'], ['digi-sel', 'DIGI-SEL OFF'],
    ['rf-gain', 'RFG 99%'], ['antenna', 'ANT 2'], ['atu', 'TUNE TUNING'],
    ['rit', 'RIT OFF −9999 Hz'], ['xit', 'XIT OFF −9999 Hz'],
  ] as const)('fact %s reserves at least its widest lit text', (fact, widest) => {
    const reserved = FACT_SLOT_RESERVATIONS[fact as keyof typeof FACT_SLOT_RESERVATIONS];
    expect(reserved, `missing reservation for ${fact}`).toBeDefined();
    expect(reserved, `${fact}: min-inline-size must cover "${widest}"`).toBeGreaterThanOrEqual(widest.length);
  });

  // FACT_SLOT_RESERVATIONS is the only source of the reserved width: every
  // rendered fact's inline min-inline-size must equal its constant, so the
  // stylesheet cannot drift from the table (review finding 1, MOR-2644).
  it('renders every fact reservation inline from FACT_SLOT_RESERVATIONS', () => {
    render({ indicator: indicator(), radioWide: shared() });
    for (const fact of Object.keys(FACT_SLOT_RESERVATIONS)) {
      const node = target.querySelector<HTMLElement>(`[data-indicator-fact="${fact}"]`)!;
      expect(node, `${fact} must render to carry its reservation`).not.toBeNull();
      expect(node.style.minInlineSize, `${fact}: inline reservation must equal the constant`)
        .toBe(`${FACT_SLOT_RESERVATIONS[fact as keyof typeof FACT_SLOT_RESERVATIONS]}ch`);
    }
  });

  // MOR-2706: the BW fact's reservation derives from the mounted profile's
  // widest filter-width value (`bandwidthMaxHz`, a structural fact the
  // adapter emits from the capabilities — IC-7300 AM max_hz 10000, FTX-1
  // FM table 16000), with the 9999-fallback constant as the floor. A
  // reading never changes it.
  describe('BW slot reservation derives from the profile (MOR-2706)', () => {
    const bwReservation = (overrides: Partial<ReceiverIndicatorViewModel>): string => {
      render({ indicator: indicator(overrides) });
      return target.querySelector<HTMLElement>('[data-indicator-fact="bandwidth"]')!
        .style.minInlineSize;
    };

    it('reserves the BW slot for the widest Icom filter width (AM 10000 → BW 10000 Hz = 11ch)', () => {
      expect(bwReservation({ bandwidthMaxHz: 10000 })).toBe('11ch');
      expect('BW 10000 Hz'.length).toBeLessThanOrEqual(11);
    });

    it('reserves the BW slot for the widest FTX-1 filter width (FM 16000 → BW 16000 Hz = 11ch)', () => {
      expect(bwReservation({ bandwidthMaxHz: 16000 })).toBe('11ch');
      expect('BW 16000 Hz'.length).toBeLessThanOrEqual(11);
    });

    it('keeps the 9999-fallback floor when the profile publishes no wider width', () => {
      expect(bwReservation({ bandwidthMaxHz: 3600 }))
        .toBe(`${FACT_SLOT_RESERVATIONS.bandwidth}ch`);
    });

    it('keeps the BW reservation unchanged when a reading arrives (MOR-2706)', () => {
      const unread = bwReservation({ bandwidthMaxHz: 10000, bandwidthHz: unknown() });
      const read = bwReservation({ bandwidthMaxHz: 10000, bandwidthHz: known(10000) });
      expect(read).toBe(unread);
      expect(read).toBe('11ch');
    });
  });
});

// MOR-2852 — the phone meta row's facts subset. Chips draws ONLY the listed
// receiver facts, in the listed order: no receiver header, no S-meter, no
// radio-wide section. Every existing appearance stays byte-identical above.
describe('chips appearance (MOR-2852) — the phone meta facts', () => {
  it('draws exactly the requested facts, in order, with no header and no S-meter', () => {
    const root = render({ indicator: indicator(), appearance: 'chips', facts: ['bandwidth', 'agc', 'nb', 'nr'] });
    const row = root.querySelector('[data-testid="vfo-indicator-row"]')!;
    expect(row.getAttribute('data-indicator-appearance')).toBe('chips');
    expect(row.querySelector('header')).toBeNull();
    expect(row.querySelectorAll('[data-testid="receiver-s-meter"]')).toHaveLength(0);
    // Notch/ATT/P.AMP/IP+/DIGI-SEL are structurally present in the fixture
    // yet must not render: only the listed subset does.
    const facts = [...row.querySelectorAll('[data-indicator-fact]')];
    expect(facts.map((node) => node.getAttribute('data-indicator-fact')))
      .toEqual(['bandwidth', 'agc', 'nb', 'nr']);
    // The radio-wide singleton section is not part of chips either.
    expect(root.querySelectorAll('[data-testid="vfo-shared-indicators"]')).toHaveLength(0);
  });

  it('follows the requested order and subset, omitting unlisted facts', () => {
    const root = render({ indicator: indicator(), appearance: 'chips', facts: ['nr', 'bandwidth'] });
    const facts = [...root.querySelectorAll('[data-indicator-fact]')];
    expect(facts.map((node) => node.getAttribute('data-indicator-fact'))).toEqual(['nr', 'bandwidth']);
  });

  it('renders NB/NR as the bare label with on/off/unknown state, never ON/OFF text', () => {
    const root = render({ indicator: indicator({ nbActive: known(true), nrActive: known(false) }),
      appearance: 'chips', facts: ['bandwidth', 'agc', 'nb', 'nr'] });
    const nb = root.querySelector<HTMLElement>('[data-indicator-fact="nb"]')!;
    const nr = root.querySelector<HTMLElement>('[data-indicator-fact="nr"]')!;
    expect(nb.getAttribute('data-state')).toBe('on');
    expect(nb.textContent).toBe('NB');
    expect(nr.textContent).toBe('NR');
    expect(nr.getAttribute('data-state')).toBe('off');
    for (const node of [nb, nr]) expect(node.textContent).not.toMatch(/ON|OFF/);
    // Each NB/NR slot reserves exactly the label (its text never changes).
    expect(nb.style.minInlineSize).toBe('2ch');
    expect(nr.style.minInlineSize).toBe('2ch');
  });

  it('marks an unread NB/NR dimmed as `unknown` while keeping the bare label', () => {
    const root = render({ indicator: indicator({ nbActive: unknown<boolean>(), nrActive: unknown<boolean>() }),
      appearance: 'chips', facts: ['nb', 'nr'] });
    for (const fact of ['nb', 'nr'] as const) {
      const node = root.querySelector<HTMLElement>(`[data-indicator-fact="${fact}"]`)!;
      expect(node.getAttribute('data-state')).toBe('unknown');
      expect(node.textContent).toBe(fact.toUpperCase());
      expect(node.textContent).not.toContain('—');
    }
  });

  it('keeps BW and AGC texts/rules identical to the existing appearances', () => {
    const root = render({ indicator: indicator({ bandwidthHz: known(2400), agcMode: known('SLOW') }),
      appearance: 'chips', facts: ['bandwidth', 'agc'] });
    expect(root.querySelector('[data-indicator-fact="bandwidth"]')?.textContent?.trim()).toBe('BW 2400 Hz');
    expect(root.querySelector('[data-indicator-fact="agc"]')?.textContent?.trim()).toBe('AGC SLOW');
  });

  it('takes the BW fact unit from the catalog: the Russian chip reads Гц (ru-RU, MOR-2905)', () => {
    const previous = getLocale();
    setLocale('ru-RU');
    try {
      const root = render({ indicator: indicator({ bandwidthHz: known(2400), agcMode: known('SLOW') }),
        appearance: 'chips', facts: ['bandwidth', 'agc'] });
      expect(root.querySelector('[data-indicator-fact="bandwidth"]')?.textContent?.trim()).toBe('BW 2400 Гц');
      expect(root.querySelector('[data-indicator-fact="agc"]')?.textContent?.trim()).toBe('AGC SLOW');
    } finally { setLocale(previous); }
  });

  it('dimmed-label-only unread BW and AGC, reserving the same slot', () => {
    const root = render({ indicator: indicator({ bandwidthHz: unknown(), agcMode: unknown() }),
      appearance: 'chips', facts: ['bandwidth', 'agc'] });
    const bw = root.querySelector<HTMLElement>('[data-indicator-fact="bandwidth"]')!;
    const agc = root.querySelector<HTMLElement>('[data-indicator-fact="agc"]')!;
    expect(bw.textContent?.trim()).toBe('BW');
    expect(agc.textContent?.trim()).toBe('AGC');
    expect(bw.textContent).not.toContain('—');
    expect(agc.textContent).not.toContain('—');
    expect(bw.style.minInlineSize).toBe(`${FACT_SLOT_RESERVATIONS.bandwidth}ch`);
    expect(agc.style.minInlineSize).toBe(`${FACT_SLOT_RESERVATIONS.agc}ch`);
  });

  it('omits a structurally absent fact but keeps the requested ones', () => {
    const root = render({ indicator: indicator({
      bandwidthHz: unknown(false), agcMode: known(1),
      nbActive: unknown(false), nrActive: known(true),
    }),
      appearance: 'chips', facts: ['bandwidth', 'agc', 'nb', 'nr'] });
    const facts = [...root.querySelectorAll('[data-indicator-fact]')];
    expect(facts.map((node) => node.getAttribute('data-indicator-fact'))).toEqual(['agc', 'nr']);
  });

  it.each(['on', 'unknown'] as const)('keeps NB/NR slot width identical in the %s state', (stateName) => {
    const field = stateName === 'on' ? known(true) : unknown<boolean>();
    const root = render({ indicator: indicator({ nbActive: field, nrActive: field }),
      appearance: 'chips', facts: ['nb', 'nr'] });
    for (const fact of ['nb', 'nr'] as const) {
      expect(root.querySelector<HTMLElement>(`[data-indicator-fact="${fact}"]`)!.style.minInlineSize).toBe('2ch');
    }
  });

  it.each(['bw known', 'bw unread'] as const)('holds the BW slot at one width (%s)', (_label) => {
    const field = _label === 'bw known' ? known(2400) : unknown<number>();
    const root = render({ indicator: indicator({ bandwidthHz: field }),
      appearance: 'chips', facts: ['bandwidth'] });
    expect(root.querySelector<HTMLElement>('[data-indicator-fact="bandwidth"]')!.style.minInlineSize)
      .toBe(`${FACT_SLOT_RESERVATIONS.bandwidth}ch`);
  });

  it('lights NB/NR on with the cyan edge-left bar, never colour alone', () => {
    const source = readFileSync('src/semantic/VfoIndicatorRow.svelte', 'utf8');
    const lit = source.match(/\[data-indicator-appearance='chips'\][^/]*\[data-state='on'\]::before\s*\{([^}]*)}/s);
    expect(lit, 'chips on-state bar rule').not.toBeNull();
    expect(lit![1]).toMatch(/left:\s*0/);
    expect(lit![1]).toMatch(/width:\s*2px/);
    expect(lit![1]).toMatch(/var\(--v2-accent-cyan/);
  });

  it('an existing appearance still renders its header, receiver name and S-meter', () => {
    for (const appearance of ['semantic', 'sdr', 'standard'] as const) {
      if (component) unmount(component);
      component = null;
      const root = render({ indicator: indicator(), appearance });
      expect(root.querySelector('header strong')?.textContent).toBe('MAIN');
      expect(root.querySelectorAll('[data-testid="receiver-s-meter"]')).toHaveLength(1);
    }
  });
});
