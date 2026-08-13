/**
 * MOR-1275 — the renderer half of design-language activation.
 *
 * `resolveRenderer` had zero call sites: every registered language's renderers
 * were unit-tested and dead. These tests pin the ONE language-agnostic wiring
 * that makes them live, and — just as important — pin what it must NOT change.
 *
 * Three properties are asserted, each naming the mutation it kills:
 *
 *  1. AGNOSTIC. The same wiring lights up `studioline` and `fieldline` with no
 *     per-language branch anywhere in the semantic vertical. The kill: a
 *     `if (id === 'studioline')` fast path would leave fieldline on the v2
 *     readout, so the two languages are asserted to produce two DIFFERENT
 *     grammars from the same component and the same props.
 *  2. FALLBACK. No attribute, an unregistered id, or a language with no
 *     renderer for the slot all fall back to the component's existing
 *     rendering — byte-identical to the pre-wiring output. The kill: a wiring
 *     that assumed a manifest would blank the readout on a plain app page.
 *  3. R9 / EQUIVALENCE. Renderers DISPLAY; they never decide. The accessible
 *     names, the `data-rf`/`data-session`/`data-vfo-*` attributes and the
 *     disabled set are identical with a language active and without it, and
 *     the state-feedback annotations follow the App TX AUTHORITY (they read
 *     `session`, never a raw ptt).
 *
 * ISOLATED POOL (MOR-1272 trap). This file mutates two pieces of GLOBAL state:
 * `document.documentElement`'s activation attribute and the design-language
 * registry. Under `isolate: false` either would leak into sibling files in the
 * same worker — a sibling would render its surfaces under a design language it
 * never asked for. The `*.component.test.ts` name routes it to the isolated
 * pool (see `vite.config.ts`), which is also correct on its own terms: it
 * mounts real Svelte components.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import MetersSurface from '../MetersSurface.svelte';
import RxTxSurface from '../RxTxSurface.svelte';
import VfoSurface from '../VfoSurface.svelte';
import { topologyFixtures, withMeters } from '../fixtures/topologies';
import type { RadioViewModel } from '../radio-view-model';
import type { TxAuthoritySnapshot } from '../rx-tx-surface';
import {
  listDesignLanguageIds, registerDesignLanguage, RendererInputError,
} from '../../presentation/languages/contract';
import { validManifest } from '../../presentation/languages/__tests__/fixtures';
import { renderSlot } from '../design-language-renderers';

const VIEW: RadioViewModel = topologyFixtures['2/main_sub'];
/** M-A's frequency, the one every frequency assertion below reads. */
const MAIN_A_HZ = 14250000;
// MOR-1482: the v2 fallback now matches `FrequencyDisplayInteractive`'s own
// dot-grouped digit convention (previously a decimal-MHz string that matched
// neither that convention nor either design language's grammar).
const V2_READOUT = '14.250.000';
const THIN_SPACE = ' ';

const IDLE_RX: TxAuthoritySnapshot = {
  phase: 'idle', intent: null, radioTx: 'off', txRisk: 'none', mayOwnKey: false, fault: null,
};
const KEYED: TxAuthoritySnapshot = {
  phase: 'active', intent: 'latched', radioTx: 'on', txRisk: 'confirmed-on',
  mayOwnKey: true, fault: null,
};
/**
 * The R9 discriminator: the radio's transmit bit is ON while the App authority
 * owns no session at all (a foot switch, or another client). `radioTx` and
 * `txSessionState(tx)` disagree here and nowhere else in this file, so anything
 * that reads the raw bit instead of the authority's conclusion shows up.
 */
const EXTERNALLY_KEYED: TxAuthoritySnapshot = {
  phase: 'idle', intent: null, radioTx: 'on', txRisk: 'confirmed-on',
  mayOwnKey: false, fault: null,
};

function activate(id: string | null): void {
  if (id === null) delete document.documentElement.dataset.designLanguage;
  else document.documentElement.dataset.designLanguage = id;
}

afterEach(() => { activate(null); });

/** Mounts `component`, runs `fn` over its DOM, always unmounts. */
function withMounted<P extends Record<string, unknown>>(
  component: unknown, props: P, fn: (root: HTMLElement) => void,
): void {
  const target = document.createElement('div');
  document.body.appendChild(target);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const instance = mount(component as any, { target, props });
  flushSync();
  try { fn(target); } finally { unmount(instance); target.remove(); }
}

/**
 * Every `data-dl-*` annotation in a subtree, as `element:attribute=value`.
 * The element is named by its FIRST class — Svelte appends a per-component
 * scoping class (`s-<hash>`) that changes whenever the file does.
 */
function annotations(root: HTMLElement): string[] {
  return [...root.querySelectorAll<HTMLElement>('*')].flatMap((el) =>
    [...el.attributes]
      .filter((a) => a.name.startsWith('data-dl-'))
      .map((a) => `${el.classList[0] ?? el.tagName.toLowerCase()}:${a.name}=${a.value}`));
}

/** The frequency readouts a VfoSurface renders, in DOM order. */
function frequencies(language: string | null): string[] {
  activate(language);
  let out: string[] = [];
  withMounted(VfoSurface, { viewModel: VIEW }, (root) => {
    out = [...root.querySelectorAll('.vfo-freq')].map((el) => el.textContent ?? '');
  });
  return out;
}

/** Every `data-dl-*` attribute on each `.vfo-freq` region, in DOM order. */
function frequencyRegionAttributes(language: string | null): string[][] {
  activate(language);
  let out: string[][] = [];
  withMounted(VfoSurface, { viewModel: VIEW }, (root) => {
    out = [...root.querySelectorAll<HTMLElement>('.vfo-freq')].map((el) =>
      [...el.attributes].filter((a) => a.name.startsWith('data-dl-')).map((a) => `${a.name}=${a.value}`));
  });
  return out;
}

// ── 1. MOR-1482 (owner ruling, session 19): the frequency SLOT opts the
// language's TEXT out, permanently — the same MOR-1322 option-(b) precedent
// the tunable branch already used, now extended to the non-tunable one. A
// hero-scale grammar (studioline's thin-space-grouped ranked digits,
// fieldline's ungrouped run) flattened to a tile-scale string loses the
// ranking/geometry it depends on and reads as unformatted digits — the
// verifier's live finding was that `studioline` is
// `DEFAULT_WORKSPACE.designLanguage` and is declared compatible with the
// shipped `desktop-v2` skin, so this was the OUT-OF-THE-BOX tile text, not a
// rare state. The region's `data-dl-*` attributes are untouched: a language
// still owns the REGION, only the VALUE's text is no longer its call.
// `frequencyDisplay`'s own `.text` output is unconsumed anywhere in
// production now (see the doc note on `RendererDisplay.text`).

describe('MOR-1482 — the frequency slot opts out of the language\'s TEXT, keeps its attributes', () => {
  it.each(['studioline', 'fieldline'])(
    '%s: the tile text is the v2 dot-grouped fallback, not the language\'s own grammar', (id) => {
      // Kill-mutation: restoring `freq?.text ??` would flip this back to
      // studioline's thin-space run / fieldline's ungrouped run.
      const [mainA] = frequencies(id);
      expect(mainA).toBe(V2_READOUT);
      expect(mainA).not.toBe(`14${THIN_SPACE}250${THIN_SPACE}000`); // studioline's own grammar
      expect(mainA).not.toBe('14250000'); // fieldline's own grammar
    });

  it.each(['studioline', 'fieldline'])(
    '%s: the region still carries the language\'s data-dl-* attributes', (id) => {
      // Kill-mutation: dropping `freq?.attributes` from the spread — the
      // language's claim on the REGION must survive even though it no
      // longer supplies the region's text.
      const [mainARegion] = frequencyRegionAttributes(id);
      expect(mainARegion.length).toBeGreaterThan(0);
    });

  it('the no-language fallback carries no data-dl-* attributes at all', () => {
    const [mainARegion] = frequencyRegionAttributes(null);
    expect(mainARegion).toEqual([]);
  });

  it('every VFO is dot-grouped, matching the v2/no-language fallback, under EITHER language', () => {
    const bare = frequencies(null);
    expect(frequencies('studioline')).toEqual(bare);
    expect(frequencies('fieldline')).toEqual(bare);
    expect(bare).toEqual([V2_READOUT, '14.280.000', '21.295.000', '21.330.000']);
  });

  // The verifier's own probe (BLOCKED finding on MOR-1482, session 19): the
  // exact live scenario — studioline active (the workspace DEFAULT) on the
  // desktop-v2 skin (VfoSurface's only production mount) — pinned permanently
  // so it can never regress silently again.
  it('PERMANENT PROBE: under studioline-active desktop-v2, the unselected tile text is dot-grouped', () => {
    activate('studioline');
    withMounted(VfoSurface, { viewModel: VIEW }, (root) => {
      const unselected = root.querySelector<HTMLElement>('[data-vfo-active="false"] .vfo-freq')!;
      expect(unselected.textContent).toMatch(/^\d{1,3}(\.\d{3}){2}$/);
      expect(unselected.textContent).not.toMatch(/\s/); // no thin-space grammar leak
      expect(unselected.textContent).not.toContain('MHz');
    });
  });
});

// ── 2. Fallback: unchanged behaviour wherever no renderer applies ───────────

describe('MOR-1275 — the wiring falls back to the component\'s own rendering', () => {
  it('renders the v2 readout when no language is active', () => {
    expect(frequencies(null)[0]).toBe(V2_READOUT);
    expect(frequencies(null)).toEqual([
      V2_READOUT, '14.280.000', '21.295.000', '21.330.000',
    ]);
  });

  it('renders the v2 readout when the attribute names an UNREGISTERED language', () => {
    // Kill-mutation: a non-null assertion on `getDesignLanguage(id)` would
    // throw here and take the whole surface down.
    expect(frequencies('no-such-language')[0]).toBe(V2_READOUT);
  });

  it('renders the v2 readout when the language declares no renderer for the slot', () => {
    // A language may register before its visual slice lands (MOR-1073/1074):
    // `resolveRenderer` hands back a safe no-op, and the surface must read
    // that as "fall back", not as "render nothing".
    registerDesignLanguage({ ...validManifest(), id: 'rendererless-line', renderers: {} });
    expect(frequencies('rendererless-line')[0]).toBe(V2_READOUT);
  });

  it('emits no `data-dl-*` annotation at all with no language active', () => {
    activate(null);
    withMounted(VfoSurface, { viewModel: VIEW }, (root) => {
      expect(annotations(root)).toEqual([]);
    });
  });

  it('registers both product languages — the declarations module is imported', () => {
    // Kill-mutation: dropping the side-effect import leaves the registry empty
    // and every language silently falls back forever.
    expect(listDesignLanguageIds()).toEqual(expect.arrayContaining(['studioline', 'fieldline']));
  });
});

// ── 3. R9 + behavioural equivalence ────────────────────────────────────────

/** Everything the operator and assistive tech can observe on a VfoSurface. */
function vfoSignature(language: string | null): unknown {
  activate(language);
  let signature: unknown;
  withMounted(VfoSurface, { viewModel: VIEW }, (root) => {
    signature = {
      group: root.querySelector('[data-testid="vfo-surface"]')?.getAttribute('aria-label'),
      names: [...root.querySelectorAll('button')].map((b) =>
        `${b.getAttribute('aria-label')}|${b.getAttribute('aria-checked')}|${b.disabled}`),
      tiles: [...root.querySelectorAll('[data-vfo-tile]')].map((el) =>
        [...el.attributes].filter((a) => a.name.startsWith('data-vfo-'))
          .map((a) => `${a.name}=${a.value}`).join(',')),
    };
  });
  return signature;
}

describe('MOR-1275 — a design language changes the LOOK, never the behaviour', () => {
  it('leaves accessible names, tri-states, disabled set and data-vfo-* identical', () => {
    // Kill-mutation: a wiring that routed the renderer output into the select
    // button's `aria-label`, or that gated a control on the descriptor.
    const bare = vfoSignature(null);
    expect(vfoSignature('studioline')).toEqual(bare);
    expect(vfoSignature('fieldline')).toEqual(bare);
  });
});

/** The RX/TX facts an operator reads, plus the renderer's own annotations. */
function rxTxProbe(language: string | null, tx: TxAuthoritySnapshot): {
  facts: Record<string, string | null>; annotations: string[];
} {
  activate(language);
  let out = { facts: {} as Record<string, string | null>, annotations: [] as string[] };
  withMounted(RxTxSurface, { view: VIEW, tx }, (root) => {
    const state = root.querySelector('[data-testid="rx-tx-state"]')!;
    const key = root.querySelector<HTMLButtonElement>('[data-testid="rx-tx-key"]')!;
    out = {
      facts: {
        rf: state.getAttribute('data-rf'),
        session: state.getAttribute('data-session'),
        origin: state.getAttribute('data-origin'),
        text: state.textContent!.replace(/\s+/g, ' ').trim(),
        keyDisabled: String(key.disabled),
        keyPressed: key.getAttribute('aria-pressed'),
      },
      annotations: annotations(root),
    };
  });
  return out;
}

describe('MOR-1275 — the state-feedback slot displays the authority, never a second opinion', () => {
  it.each(['studioline', 'fieldline'])(
    '%s: leaves data-rf/data-session, the status text and the key gating untouched', (id) => {
      expect(rxTxProbe(id, IDLE_RX).facts).toEqual(rxTxProbe(null, IDLE_RX).facts);
      expect(rxTxProbe(id, KEYED).facts).toEqual(rxTxProbe(null, KEYED).facts);
    });

  it.each(['studioline', 'fieldline'])(
    '%s: annotates the surface from the AUTHORITY session, not from a raw ptt', (id) => {
      // The meter re-zones with the transmitter: `PO` appears only once the
      // authority reports a keyed session. Kill-mutation: feeding the renderer
      // `radioState.ptt` (or `tx.radioTx`) instead of `txSessionState(tx)`.
      const rx = rxTxProbe(id, IDLE_RX).annotations;
      const keyed = rxTxProbe(id, KEYED).annotations;
      expect(rx).toContain('rx-tx-surface:data-dl-meter-scale-label=S');
      expect(keyed).toContain('rx-tx-surface:data-dl-meter-scale-label=PO');
      expect(keyed).toContain('rx-tx-surface:data-dl-numeral-tone=tx-target');
    });

  it.each(['studioline', 'fieldline'])(
    '%s: an externally-keyed radio does NOT read as a keyed session (R9)', (id) => {
      // The one snapshot where `radioTx` and `txSessionState(tx)` disagree.
      // Kill-mutation: passing `tx.radioTx === 'on' ? 'keyed' : 'idle'` as the
      // session — indistinguishable on every other fixture in this file, and
      // exactly the "second TX opinion" R9 forbids. The authority owns no
      // session here, so the descriptor must land on doubt, not on TX.
      const external = rxTxProbe(id, EXTERNALLY_KEYED).annotations;
      expect(external).toContain('rx-tx-surface:data-dl-meter-scale-label=S');
      expect(external).toContain('rx-tx-surface:data-dl-numeral-tone=primary');
      expect(external).not.toContain('rx-tx-surface:data-dl-meter-scale-label=PO');
    });

  it('emits no annotation when no language is active', () => {
    expect(rxTxProbe(null, KEYED).annotations).toEqual([]);
  });
});

describe('MOR-1275 — the meters slot is wired through the same helper', () => {
  const view = withMeters(VIEW, 'receiving');

  it.each(['studioline', 'fieldline'])('%s annotates the S-meter tile', (id) => {
    activate(id);
    withMounted(MetersSurface, { view }, (root) => {
      const tile = root.querySelector<HTMLElement>('[data-testid="meter-signal"]')!;
      expect([...tile.attributes].map((a) => a.name).filter((n) => n.startsWith('data-dl-')))
        .not.toHaveLength(0);
      // The gauge itself is untouched: availability/relevance stay the surface's
      // decision, never the renderer's.
      expect(tile.dataset.observed).toBe('true');
      expect(tile.dataset.relevant).toBe('true');
    });
  });

  it('leaves the tile bare with no language active', () => {
    activate(null);
    withMounted(MetersSurface, { view }, (root) => {
      expect(annotations(root)).toEqual([]);
    });
  });
});

// ── 4. The three doctrine pins (MOR-1275 review: F1, F2, F3) ───────────────
//
// Each of these guards a promise that was previously enforced by nothing: a
// mutation violating it changed no observable behaviour under the tests above.

const WIRING_PATH = 'src/semantic/design-language-renderers.ts';
/** Source with comments stripped — these pins are about CODE, not about prose. */
const stripped = (path: string): string =>
  readFileSync(path, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

/** Every non-test source file in the semantic vertical. */
function semanticSources(dir = 'src/semantic'): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) return entry === '__tests__' ? [] : semanticSources(path);
    return /\.(ts|svelte)$/.test(entry) ? [path] : [];
  });
}

describe('F1 — [data-design-language] is the ONLY activation source the wiring reads', () => {
  const source = stripped(WIRING_PATH);

  it('reads the activation attribute exactly once, off document.documentElement', () => {
    // Kill-mutation: a second activation source, e.g.
    // `document.documentElement.dataset.designLanguage ?? element.dataset.lang`.
    // Doctrine MOR-1278 allows ONE switch; a fallback would let the renderer
    // half activate a language the stylesheet half never did, and nothing
    // else in this file can see the difference — the decoy tests below only
    // cover the decoys they happen to name.
    const reads = source.match(/dataset\.\w+/g) ?? [];
    expect(reads).toEqual(['dataset.designLanguage']);
    expect(source).toContain('document.documentElement.dataset.designLanguage');
  });

  it('reaches for no other DOM state at all', () => {
    // The whole DOM surface the module is allowed: the `typeof document` guard
    // and the one attribute read above.
    const documentAccess = source.match(/document\.\w+/g) ?? [];
    expect(new Set(documentAccess)).toEqual(new Set(['document.documentElement']));
    expect(source).not.toMatch(/getAttribute|querySelector|closest|classList|\.matches\(/);
  });

  it.each([
    ['a decoy on document.body', () => { document.body.dataset.designLanguage = 'studioline'; },
      () => { delete document.body.dataset.designLanguage; }],
    ['a decoy class on the root', () => { document.documentElement.classList.add('studioline'); },
      () => { document.documentElement.classList.remove('studioline'); }],
    ['a decoy [data-lang] on the root', () => { document.documentElement.dataset.lang = 'studioline'; },
      () => { delete document.documentElement.dataset.lang; }],
    ['a decoy [data-theme] on the root', () => { document.documentElement.dataset.theme = 'studioline'; },
      () => { delete document.documentElement.dataset.theme; }],
  ])('ignores %s while the sanctioned attribute is absent', (_name, set, unset) => {
    activate(null);
    set();
    try {
      expect(renderSlot('frequencyDisplay', { frequencyHz: MAIN_A_HZ })).toBeNull();
    } finally { unset(); }
  });
});

describe('F2 — every renderer call goes through resolveRenderer\'s structural gate', () => {
  it('reaches into no manifest\'s `renderers` map anywhere in the semantic vertical', () => {
    // Kill-mutation: `manifest.renderers[slot]?.(viewModel, manifest.tokens)`
    // in place of `resolveRenderer(manifest, slot)(…)`. MOR-1072's guarantee —
    // "capability objects and per-radio data structurally cannot reach a
    // renderer" — is a property of the GATE, not of the renderers, so it
    // survives only as long as every call site resolves through it.
    const offenders = semanticSources()
      .filter((path) => /\.renderers\b/.test(stripped(path)));
    expect(offenders).toEqual([]);
    expect(stripped(WIRING_PATH)).toMatch(/resolveRenderer\(manifest, slot\)/);
  });

  it('rejects a non-primitive field rather than forwarding it to a renderer', () => {
    // The behavioural half of the same pin: only the gate throws here. A
    // bypassed call would hand the object straight to the renderer, which
    // reads its named fields, finds nothing, and returns a tidy descriptor —
    // silently, which is exactly the failure mode the gate exists to prevent.
    activate('studioline');
    const smuggled = { frequencyHz: { nested: 'payload' } } as unknown as Parameters<typeof renderSlot>[1];
    expect(() => renderSlot('frequencyDisplay', smuggled)).toThrow(RendererInputError);
  });
});

describe('F3 — annotations carry top-level primitives only; private geometry stays private', () => {
  it('never serialises a nested object or array into a data-dl-* attribute', () => {
    // Kill-mutation: `out[key] = JSON.stringify(value)` for object values in
    // `annotate`. A family's descriptor carries its own private geometry
    // (studioline's ranked groups, fieldline's digit cells); flattening that
    // into the DOM would leak per-family shape into a contract that promises
    // exactly two language-agnostic readings.
    registerDesignLanguage({
      ...validManifest(),
      id: 'nestedline',
      renderers: {
        frequencyDisplay: () => ({
          kind: 'nestedline-frequency',
          text: 'READOUT',
          flag: true,
          count: 3,
          groups: [{ digit: '1' }, { digit: '4' }],
          geometry: { private: 'do-not-leak', offsetPx: 12 },
        }),
      },
    });
    activate('nestedline');
    const display = renderSlot('frequencyDisplay', { frequencyHz: MAIN_A_HZ })!;

    expect(display.text).toBe('READOUT');
    expect(Object.keys(display.attributes).sort())
      .toEqual(['data-dl-count', 'data-dl-flag', 'data-dl-kind']);
    // Named explicitly: neither the nested keys nor their values may appear,
    // under any attribute name.
    const serialised = JSON.stringify(display.attributes);
    expect(serialised).not.toContain('do-not-leak');
    expect(serialised).not.toContain('offsetPx');
    expect(serialised).not.toContain('digit');
    // `text` is returned separately and must not be duplicated as an attribute.
    expect(display.attributes['data-dl-text']).toBeUndefined();
  });
});
