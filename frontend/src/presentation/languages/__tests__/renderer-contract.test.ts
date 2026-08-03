/**
 * MOR-1072 renderer contract: renderers consume a RendererViewModel plus
 * tokens ONLY. `isRendererViewModel`/`invokeRenderer` are the structural
 * gate that makes a capability fork impossible — a renderer's signature has
 * no slot for a capability object. The gate checks *exact* top-level keys
 * (review cycle 1, B1), not just that `kind`/`fields` are present, so a
 * smuggled sibling key (e.g. `capabilities` riding next to valid
 * `kind`/`fields`) is rejected too — via both `invokeRenderer` directly and
 * the `resolveRenderer` resolve path, which is now a gated wrapper and
 * cannot be called around the check. `resolveRenderer` falls back safely
 * when a language has not registered a renderer for a slot yet.
 */
import { describe, it, expect, vi } from 'vitest';
import {
  isRendererViewModel, invokeRenderer, resolveRenderer, RendererInputError,
  type DesignLanguageTokens, type RendererViewModel,
} from '../contract';
import { validManifest } from './fixtures';

describe('RendererViewModel structural gate', () => {
  it('accepts a flat, primitive-only view model', () => {
    expect(isRendererViewModel({ kind: 'vfo', fields: { freq: 14074000, mode: 'USB', active: true, rit: null } })).toBe(true);
  });

  it('rejects a capability-object fixture: a list of supported modes cannot be a "fields" value', () => {
    const capabilityShaped = { kind: 'capabilities', fields: { modes: ['USB', 'LSB', 'CW'], model: 'test-radio' } };
    expect(isRendererViewModel(capabilityShaped)).toBe(false);
  });

  it('rejects a nested-object "fields" value (e.g. a bundled antenna/scope descriptor)', () => {
    expect(isRendererViewModel({ kind: 'vfo', fields: { scope: { spanHz: 100000 } } })).toBe(false);
  });

  it('rejects a value with no "kind" discriminator', () => {
    expect(isRendererViewModel({ fields: {} })).toBe(false);
  });

  // Review cycle 1, B1 (crux): a valid kind/fields pair riding alongside an
  // extra top-level key must still be rejected — the old check only looked
  // AT kind/fields, never confirmed they were the ONLY keys.
  it('rejects a smuggled sibling key even when kind/fields are individually valid (B1)', () => {
    const smuggled = { kind: 'vfo', fields: { freq: 14074000 }, capabilities: { radioModel: 'IC-7610' } };
    expect(isRendererViewModel(smuggled)).toBe(false);
  });

  it('invokeRenderer throws RendererInputError instead of calling the renderer with a capability object', () => {
    const renderer = vi.fn();
    const capabilityShaped = { kind: 'capabilities', fields: { modes: ['USB', 'LSB'] } };
    const tokens = validManifest().tokens;
    expect(() => invokeRenderer(renderer, capabilityShaped, tokens)).toThrow(RendererInputError);
    expect(renderer).not.toHaveBeenCalled();
  });

  it('invokeRenderer calls the renderer with a valid view model', () => {
    const renderer = vi.fn().mockReturnValue('rendered');
    const viewModel = { kind: 'vfo', fields: { freq: 14074000 } };
    const tokens = validManifest().tokens;
    expect(invokeRenderer(renderer, viewModel, tokens)).toBe('rendered');
    expect(renderer).toHaveBeenCalledWith(viewModel, tokens);
  });

  // Review cycle 2, N1: Object.keys only sees the instance's own enumerable
  // string keys — a getter declared in a class body lives on the
  // PROTOTYPE, not the instance, so the old check never saw it. A plausible
  // accident (not just an attack): any class instance passed where a plain
  // view model is expected.
  it('rejects a class instance whose extra key is a prototype-chain getter, not an own key (N1)', () => {
    class Poisoned {
      kind = 'vfo';
      fields = { freq: 14074000 };
      get capabilities() {
        return { radioModel: 'IC-7610' };
      }
    }
    expect(isRendererViewModel(new Poisoned())).toBe(false);
  });

  it('B1: a smuggled payload is rejected via BOTH invokeRenderer and the resolveRenderer resolve path', () => {
    const smuggled = { kind: 'vfo', fields: { freq: 14074000 }, capabilities: { radioModel: 'IC-7610' } };
    const renderer = vi.fn();
    const manifest = validManifest({ renderers: { meters: renderer } });
    const tokens = manifest.tokens;

    expect(() => invokeRenderer(renderer, smuggled, tokens)).toThrow(RendererInputError);
    // The resolve path must not be a way around the gate: calling what
    // resolveRenderer returns has to hit the same check, not the raw renderer.
    expect(() => resolveRenderer(manifest, 'meters')(smuggled as unknown as RendererViewModel, tokens)).toThrow(RendererInputError);
    expect(renderer).not.toHaveBeenCalled();
  });

  // Review cycle 1, B1(b): the one thing that DOES work at compile time.
  // Runtime shape-smuggling isn't caught by TS (excess properties on a
  // variable don't trigger the literal-only excess-property check), but the
  // opposite direction is: a renderer whose parameter is a NARROWER type
  // than RendererViewModel cannot satisfy a manifest's `renderers` slot —
  // strictFunctionTypes checks function-typed properties contravariantly.
  // The repo has no dedicated type-testing idiom (no expectTypeOf/tsd); this
  // reuses the `@ts-expect-error` mechanism already used elsewhere in the
  // suite (e.g. ws-client.test.ts), pinned by `npm run check` (svelte-check
  // + tsc), which is already part of required verification. If this stops
  // being a type error, `npm run check` fails with "Unused '@ts-expect-error'
  // directive" — that failure IS the pin.
  it('type-level: a renderer requiring extra fields cannot satisfy a manifest renderer slot', () => {
    interface NarrowerViewModel extends RendererViewModel {
      readonly fields: Readonly<Record<string, string | number | boolean | null>> & { readonly segments: number };
    }
    const narrowRenderer = (vm: NarrowerViewModel, _tokens: DesignLanguageTokens): number => vm.fields.segments;
    const manifest = validManifest();
    // @ts-expect-error — narrowRenderer requires `fields.segments`, which a
    // general RendererViewModel does not guarantee; TS must reject this
    // assignment (TS2322, contravariant parameter check). This directive
    // IS the assertion: `npm run check` fails "Unused '@ts-expect-error'"
    // if the contract's slot type ever stops enforcing it. vitest doesn't
    // type-check at runtime, so the line below still executes either way —
    // it is a smoke check only, not the real proof.
    manifest.renderers.meters = narrowRenderer;
    expect(typeof manifest.renderers.meters).toBe('function');
  });
});

describe('missing renderers fall back safely', () => {
  it('resolveRenderer returns a safe no-op when a language declares no renderer for a slot', () => {
    const manifest = validManifest(); // renderers: {} — no visual implementation yet
    const fallback = resolveRenderer(manifest, 'meters');
    expect(() => fallback({ kind: 'meters', fields: {} }, manifest.tokens)).not.toThrow();
  });

  it('resolveRenderer delegates to the registered renderer when present, through the gate', () => {
    const renderer = vi.fn().mockReturnValue('ok');
    const manifest = validManifest({ renderers: { meters: renderer } });
    const viewModel = { kind: 'meters', fields: { segments: 4 } };
    expect(resolveRenderer(manifest, 'meters')(viewModel, manifest.tokens)).toBe('ok');
    expect(renderer).toHaveBeenCalledWith(viewModel, manifest.tokens);
  });
});
