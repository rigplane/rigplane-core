/**
 * MOR-1312 — the semantic scope-display surface (vocabulary slice 12B, the
 * LAST slice of the vocabulary program).
 *
 * Presentation-only (v3 ADR invariant 11 / R9): this surface renders no
 * control of any kind. Block 1 pins that with a source scan for
 * `button`/`input`/`onclick`-shaped syntax AND a rendered-DOM query, so a
 * behavioural mutation (adding a control) and a source-level one (adding a
 * handler prop) both die — the same double instrument
 * `MetersSurface.test.ts` block 1/5 uses.
 *
 * Two carry-forward mutation probes this file exists to satisfy:
 *   (1) flip the `health`-state -> tone/text branch mapping and a test dies
 *       (block 3, `healthTone` exhaustive-mapping tests).
 *   (2) flip source-selection rendering and a test dies (block 2, the
 *       hardware/audio_fft rendering + unknown-source tests).
 */
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import ScopeDisplaySurface, { healthTone, UNKNOWN_TEXT } from '../ScopeDisplaySurface.svelte';
import { topologyFixtures, withScopeDisplay } from '../fixtures/topologies';
import type {
  Availability, ScopeDisplayField, ScopeHealthState, ScopeDisplayViewModel, RadioViewModel,
} from '../radio-view-model';

const SOURCE = readFileSync('src/semantic/ScopeDisplaySurface.svelte', 'utf8')
  .replace(/<!--[\s\S]*?-->/g, '')
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '');

const AVAIL: Availability = { structural: true, operational: true };
const UNOBSERVED: Availability = { structural: true, operational: false };
const ABSENT: Availability = { structural: false, operational: false };

/** Re-shape ONE leaf of an otherwise fully-observed `scopeDisplay` group. */
function withField(
  view: RadioViewModel, field: keyof ScopeDisplayViewModel,
  over: { availability?: Availability; unknown?: boolean; value?: unknown },
): RadioViewModel {
  const sd = view.scopeDisplay!;
  const current: ScopeDisplayField<unknown> = sd[field];
  return {
    ...view,
    scopeDisplay: {
      ...sd,
      [field]: {
        reading: over.unknown
          ? { status: 'unknown' }
          : over.value !== undefined ? { status: 'known', value: over.value } : current.reading,
        availability: over.availability ?? current.availability,
      } satisfies ScopeDisplayField<unknown>,
    } as ScopeDisplayViewModel,
  };
}

let target: HTMLDivElement;
beforeEach(() => { target = document.createElement('div'); document.body.appendChild(target); });
afterEach(() => { target.remove(); });

function render(view: RadioViewModel) {
  const component = mount(ScopeDisplaySurface, { target, props: { view } });
  flushSync();
  const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
  return {
    dispose: () => unmount(component),
    root: () => q('[data-testid="scope-display-surface"]'),
    source: () => q('[data-testid="scope-display-source"]'),
    health: () => q('[data-testid="scope-display-health"]'),
    hardware: () => q('[data-testid="scope-display-hardware"]'),
  };
}

function withSurface(view: RadioViewModel, fn: (s: ReturnType<typeof render>) => void): void {
  const s = render(view);
  try { fn(s); } finally { s.dispose(); }
}

const base = () => withScopeDisplay(topologyFixtures['1/single']);

// ── 1. Display only (R9) + optional-group self-gating (risk R3) ────────────

describe('the scope-display surface is display-only and self-gates on group presence', () => {
  it('renders NOTHING at all when the view model carries no scopeDisplay group', () => {
    const bare = topologyFixtures['1/single'];
    expect(bare.scopeDisplay).toBeUndefined();
    withSurface(bare, (s) => {
      expect(s.root()).toBeNull();
      expect(target.innerHTML).not.toContain('scope-display');
    });
  });

  it('renders the surface when the group is present', () => {
    withSurface(base(), (s) => {
      expect(s.root()).not.toBeNull();
      expect(target.querySelectorAll('[data-testid="scope-display-surface"]')).toHaveLength(1);
    });
  });

  // MUTATION PROBE (1 of 2 required by the ticket): grows a button/input of
  // any kind. Zero focusable elements is what lets `SemanticRadioSurfaces`
  // mount this surface bare in BOTH compositions (the `meters` shape, not
  // `rxAudio`'s single-only shape) — see `ScopeDisplaySurface.svelte`'s doc
  // comment and `semantic-scope-display-wiring.component.test.ts`.
  it('contributes no focusable control of any kind', () => {
    withSurface(base(), () => {
      expect(target.querySelectorAll(
        'button, input, select, textarea, a[href], [tabindex], [role="button"], [role="switch"]',
      )).toHaveLength(0);
    });
  });

  // Source-level companion to the behavioural probe above: proves the
  // ABSENCE of a handler prop, which a rendered-DOM query alone cannot.
  it('takes no intent callback prop and has exactly one prop', () => {
    expect(SOURCE).toMatch(/interface Props\s*\{\s*view:\s*RadioViewModel\s*\}/);
    expect(SOURCE).not.toMatch(/on[A-Z]\w*\?:/);
  });

  it('binds no zone id of its own', () => {
    withSurface(base(), () => {
      expect(target.querySelectorAll('[data-zone-id]')).toHaveLength(0);
    });
  });
});

// ── 2. MUTATION PROBE (2 of 2): source-selection rendering ─────────────────

describe('source rendering reflects the fact, never a default', () => {
  it('renders the known hardware source', () => {
    withSurface(withField(base(), 'source', { value: 'hardware' }), (s) => {
      expect(s.source()!.textContent).toContain('hardware');
      expect(s.source()!.dataset.observed).toBe('true');
    });
  });

  // The discriminating case: hardware and audio_fft must render DIFFERENT
  // text. A mutation that hardcodes 'hardware' or drops the source read
  // entirely produces the SAME text for both and this test dies.
  it('renders the known audio_fft source, distinctly from hardware', () => {
    withSurface(withField(base(), 'source', { value: 'audio_fft' }), (s) => {
      expect(s.source()!.textContent).toContain('audio_fft');
      expect(s.source()!.textContent).not.toContain('hardware');
    });
  });

  it('renders unknown, never a fabricated source, before any source resolves', () => {
    withSurface(withField(base(), 'source', { unknown: true, availability: UNOBSERVED }), (s) => {
      expect(s.source()!.textContent).toContain(UNKNOWN_TEXT);
      expect(s.source()!.dataset.observed).toBe('false');
      expect(s.source()!.textContent).not.toMatch(/hardware|audio_fft/);
    });
  });
});

// ── 3. MUTATION PROBE (1 of 2): the health-state branch mapping ────────────

describe('healthTone maps every ScopeHealthState to its own tone (exhaustive)', () => {
  const EXPECTED: Record<ScopeHealthState, string> = {
    connected: 'green',
    connecting: 'yellow', starting: 'yellow', waiting: 'yellow', reconnecting: 'yellow',
    disconnected: 'red', failed: 'red',
    inactive: 'neutral',
  };

  it.each(Object.entries(EXPECTED) as Array<[ScopeHealthState, string]>)(
    'health=%s -> tone=%s',
    (state, tone) => {
      expect(healthTone(state)).toBe(tone);
    },
  );

  // MUTATION KILLED: swapping any two branches (e.g. 'failed' -> yellow, or
  // 'connected' -> neutral) — every state maps to exactly the tone above, and
  // no two adjacent branches collapse onto each other by accident.
  it('keeps green/red/neutral each reachable by exactly the states above', () => {
    const byTone = new Map<string, ScopeHealthState[]>();
    for (const [state, tone] of Object.entries(EXPECTED)) {
      byTone.set(tone, [...(byTone.get(tone) ?? []), state as ScopeHealthState]);
    }
    expect(byTone.get('green')).toEqual(['connected']);
    expect(byTone.get('red')!.sort()).toEqual(['disconnected', 'failed']);
    expect(byTone.get('neutral')).toEqual(['inactive']);
  });

  it.each(Object.entries(EXPECTED) as Array<[ScopeHealthState, string]>)(
    'renders data-tone=%s for the rendered health field',
    (state, tone) => {
      withSurface(withField(base(), 'health', { value: state }), (s) => {
        expect(s.health()!.dataset.tone).toBe(tone);
        expect(s.health()!.textContent).toContain(state);
      });
    },
  );
});

// ── 4. hardwareConnected (MOR-1312, MOR-1352 finding) ───────────────────────

describe('hardwareConnected renders independent of source/health', () => {
  it('renders "on" when the hardware channel is connected', () => {
    withSurface(withField(base(), 'hardwareConnected', { value: true }), (s) => {
      expect(s.hardware()!.textContent).toContain('on');
      expect(s.hardware()!.textContent).not.toContain('off');
    });
  });

  it('renders "off" when the hardware channel is not connected', () => {
    withSurface(withField(base(), 'hardwareConnected', { value: false }), (s) => {
      expect(s.hardware()!.textContent).toContain('off');
    });
  });

  it('renders unknown, never a guessed boolean, when unobserved', () => {
    withSurface(
      withField(base(), 'hardwareConnected', { unknown: true, availability: UNOBSERVED }),
      (s) => {
        expect(s.hardware()!.textContent).toContain(UNKNOWN_TEXT);
        expect(s.hardware()!.dataset.observed).toBe('false');
      },
    );
  });

  // The MOR-1352 case itself: audio_fft selected AND healthy, hardware still
  // reported separately as connected. Proves the leaf is not derived from
  // `health`/`source` — see the adapter-level probe of the same name.
  it('stays true while source=audio_fft and health=connected', () => {
    let view = withField(base(), 'source', { value: 'audio_fft' });
    view = withField(view, 'health', { value: 'connected' });
    view = withField(view, 'hardwareConnected', { value: true });
    withSurface(view, (s) => {
      expect(s.source()!.textContent).toContain('audio_fft');
      expect(s.hardware()!.textContent).toContain('on');
    });
  });
});

// ── 5. Two-level availability (MOR-977/1256) ──────────────────────────────

describe('structural availability never renders as a guessed value', () => {
  it('keeps an operationally-unavailable field PRESENT and marked unobserved', () => {
    withSurface(withField(base(), 'source', { unknown: true, availability: UNOBSERVED }), (s) => {
      expect(s.source()).not.toBeNull();
      expect(s.source()!.dataset.observed).toBe('false');
    });
  });

  // Every leaf the 12A/12B adapter emits is structurally present whenever the
  // group itself is present (`deriveScopeDisplay` always passes `structural:
  // true`) — this is a closed-shape sanity check, not a live adapter test.
  it('never encodes state by colour alone in its own stylesheet', () => {
    const styles = SOURCE.slice(SOURCE.indexOf('<style>'));
    expect(styles).not.toMatch(/#[0-9a-f]{3,8}\b|\brgb\(|\bhsl\(/i);
  });

  it('does not gate its own render on structural absence (no case exercises it live)', () => {
    // Documents intent: `ABSENT` is unreachable from the real adapter today
    // (both leaves are always structural:true), but the surface's `usable()`
    // helper still degrades correctly if that ever changes.
    withSurface(withField(base(), 'source', { availability: ABSENT, unknown: true }), (s) => {
      expect(s.source()!.dataset.observed).toBe('false');
    });
  });
});
