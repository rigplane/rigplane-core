import { SvelteMap } from 'svelte/reactivity';
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
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

  it('prints the Hz unit only with a known offset (MOR-2644 correction 1)', () => {
    // Both parts unread → exactly the label. Same for XIT.
    for (const key of ['rit', 'xit'] as const) {
      const label = key.toUpperCase();
      const root = render({ radioWide: {
        ...shared(),
        ritActive: unknown(), ritOffset: unknown(),
        xitActive: unknown(), xitOffset: unknown(),
      } });
      const node = root.querySelector(`[data-indicator-fact="${key}"]`);
      expect(node?.textContent?.trim()).toBe(label);
      expect(node?.textContent).not.toContain('Hz');
    }
    // State known, offset unread → "RIT ON" / "RIT OFF", no Hz.
    const stateOnly = render({ radioWide: {
      ...shared(), ritActive: known(false), ritOffset: unknown(),
    } });
    expect(stateOnly.querySelector('[data-indicator-fact="rit"]')?.textContent?.trim()).toBe('RIT OFF');
    // Both known → same text as main today ("RIT OFF 0 Hz").
    const bothKnown = render({ radioWide: shared() });
    expect(bothKnown.querySelector('[data-indicator-fact="rit"]')?.textContent).toContain('RIT OFF 0 Hz');
    expect(bothKnown.querySelector('[data-indicator-fact="xit"]')?.textContent).toContain('XIT ON 0 Hz');
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

  it.each([
    ['transmitting', 'TX'], ['uncertain', 'TX?'],
  ] as const)('keeps %s RF authority visible as %s', (rfState, label) => {
    const root = render({ radioWide: { ...shared(), rfState } });
    expect(root.querySelector('[data-indicator-fact="rf-authority"]')?.textContent).toBe(label);
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
    const source = readFileSync('src/semantic/VfoIndicatorRow.svelte', 'utf8');
    expect(source).toMatch(
      /\.fact\[data-indicator-fact='rf-gain'\]\s*\{[^}]*min-inline-size:\s*8ch/,
    );
  });

  it('an empty RFG fact draws no visible frame while keeping the reserved slot', () => {
    render({ indicator: indicator({ rfGain: known(1) }) });
    const node = target.querySelector('[data-indicator-fact="rf-gain"]')!;
    expect(node.textContent).toBe('');
    expect(node.getAttribute('data-empty')).toBe('true');
    // The CSS the attribute keys on: the frame goes transparent while the
    // reserved width (min-inline-size) stays — never an empty bordered box.
    const source = readFileSync('src/semantic/VfoIndicatorRow.svelte', 'utf8');
    expect(source).toMatch(
      /\.fact\[data-indicator-fact='rf-gain'\]\[data-empty='true'\]\s*\{[^}]*border-color:\s*transparent/,
    );
    expect(source).toMatch(/\.fact\[data-indicator-fact='rf-gain'\]\s*\{[^}]*min-inline-size:\s*8ch/);
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
});
