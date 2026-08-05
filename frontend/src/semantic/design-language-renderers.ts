/**
 * MOR-1275 — the ONE place the semantic vertical consults a design language's
 * renderers. Before this module `resolveRenderer` had zero call sites: every
 * registered language's renderer bundle was unit-tested and dead.
 *
 * LANGUAGE-AGNOSTIC BY CONSTRUCTION. Nothing here names `studioline` or
 * `fieldline`, and nothing may: the active language is whatever
 * `[data-design-language]` says, and its renderers are whatever its manifest
 * declared. A third family registered tomorrow becomes live with no change to
 * this file and none to the surfaces that call it.
 *
 * ACTIVATION (doctrine MOR-1278). `[data-design-language]` on the
 * semantic-vertical root (`document.documentElement`) is the canonical — and
 * only — activation mechanism, and its value MUST equal a registered manifest
 * id. This module READS that same attribute; it introduces no second switch
 * (no class, no prop, no Svelte context), so the CSS half and the renderer
 * half can never disagree about which language is on. The attribute is read at
 * render time: a host that flips it re-renders the vertical, which is what the
 * cutover (MOR-1048/MOR-1263) will do — this is not a reactive store, and
 * deliberately not, because a store would be a second activation path.
 *
 * SAFETY INVARIANT R9. Renderers DISPLAY; components DECIDE. `renderSlot`
 * takes the flat facts its CALLER already holds as props and hands them
 * straight to the renderer through `resolveRenderer`'s structural gate. It
 * reads no store, opens no state path, and can neither reach `radioState.ptt`
 * nor change when or whether a surface renders a control: every caller keeps
 * its own gating and merely paints the result differently.
 *
 * WHAT A CONSUMER MAY READ BACK. Renderer output is typed `unknown` on
 * purpose — each family's descriptor is its own shape (studioline emits ranked
 * groups, fieldline emits digit cells). A semantic component therefore cannot
 * consume a descriptor structurally without learning that shape, which is the
 * per-language branch this ticket forbids. So exactly two language-agnostic
 * readings are taken, both display-only:
 *
 *   `text`        — the descriptor's own flat rendering of the fact, used in
 *                   place of the component's default string when present.
 *   `attributes`  — every OTHER top-level primitive, as `data-dl-<kebab>`.
 *                   Annotations only: they carry the descriptor's conclusions
 *                   into the DOM where the CSS half, the fixture harness and
 *                   assistive-tech-neutral tests can observe them. Nested
 *                   objects and arrays are skipped rather than serialised —
 *                   a family's private geometry stays private.
 *
 * A descriptor that offers neither is not an error: the caller falls back.
 */
import {
  getDesignLanguage, resolveRenderer,
  type DesignLanguageManifest, type RendererSlotName, type RendererViewModel,
} from '../presentation/languages/contract';
// Side-effect import: the registry is populated by the declarations module,
// which is otherwise imported only by its own tests. Importing the DECLARATIONS
// barrel rather than a family keeps this file agnostic — it registers whatever
// the product declares, in one line, for every slot below.
import '../presentation/languages/declarations';

export type RendererFields = RendererViewModel['fields'];

export interface RendererDisplay {
  /** The renderer's own text for this slot, or `null` when it emits none. */
  readonly text: string | null;
  /** `data-dl-*` display annotations, ready to spread onto an element. */
  readonly attributes: Readonly<Record<string, string>>;
}

/** The manifest named by the activation attribute, or `undefined` when no language is active, the id is unregistered, or there is no DOM. */
export function activeDesignLanguage(): DesignLanguageManifest | undefined {
  if (typeof document === 'undefined') return undefined;
  const id = document.documentElement.dataset.designLanguage;
  return id ? getDesignLanguage(id) : undefined;
}

const kebab = (key: string): string => key.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`);

/** Top-level primitives only — `text` is excluded because it is returned separately. */
function annotate(descriptor: Record<string, unknown>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(descriptor)) {
    if (key === 'text') continue;
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
      out[`data-dl-${kebab(key)}`] = String(value);
    }
  }
  return out;
}

/**
 * Renders `fields` through the active language's renderer for `slot`.
 *
 * Returns `null` — meaning "use your own rendering" — in every case where a
 * design language has nothing to say: no attribute, an unregistered id, a
 * language that declares no renderer for this slot (`resolveRenderer`'s safe
 * no-op returns `null`), or a renderer that returns a non-object. Falling back
 * is the DEFAULT, so a plain app page with no language active behaves exactly
 * as it did before this wiring existed.
 */
export function renderSlot(slot: RendererSlotName, fields: RendererFields): RendererDisplay | null {
  const manifest = activeDesignLanguage();
  if (!manifest) return null;
  const output = resolveRenderer(manifest, slot)({ kind: slot, fields }, manifest.tokens);
  if (typeof output !== 'object' || output === null) return null;
  const descriptor = output as Record<string, unknown>;
  return {
    text: typeof descriptor.text === 'string' ? descriptor.text : null,
    attributes: annotate(descriptor),
  };
}
