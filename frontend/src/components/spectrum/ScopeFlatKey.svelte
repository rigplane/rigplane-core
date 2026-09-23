<!--
  Flat scope key (MOR-2545) — the clickable member of the VFO-deck lamp
  visual grammar (`components-v2/vfo/VfoPanel.svelte` `.lamp`, markup ~222–229
  / CSS ~609–639): flat, no button chrome, dim when unlit, lit colour/glow
  when on, fixed width, tabular digits. The lamp is a display-only span; this
  is its `<button type="button">` counterpart with native Enter/Space.

  PLACEMENT (v3 ADR): `semantic/` may not import `components-v2/*`, so the
  key cannot live next to the lamp it quotes. It lives here, in
  `components/spectrum/`, because `semantic/ScopeControlsSurface.svelte`
  already imports `components/spectrum/spectrum-toolbar-logic` — a proven
  allowed path (the eslint semantic lockdown bans skins/runtime only) — and
  because it shares the scope surface with `ScopeMorePanel.svelte`.
  `primitives/` would also be legal (Svelte-only atom) but is reserved for
  theme-level building blocks; this key is scope-surface furniture.

  Search-before-write: `primitives/` and `components/spectrum/` have no
  clickable flat key — only chrome-carrying toolbar buttons
  (`SpectrumToolbar.svelte`) and the display-only VFO lamp spans — so this
  component is new.

  Owner rules honoured here:
  - `lit: null` = declared-but-unread: the key is drawn UNLIT, in place, with
    its label and NO value. A toggle then OMITS `aria-pressed` entirely
    (never "false"); a choice reports `aria-checked="false"`. No placeholder
    text is ever drawn — the label is the content.
  - Nothing blinks or shifts: width comes from `--scope-key-width` (per-key
    reserved width set by the host) and only colour/weight change between
    unlit/lit — the box never changes size.
  - No focus frame (MOR-2522): `app.css` already clears `:focus` outlines
    globally; this key adds no ring of its own.

  Colours reuse the lamp variables — `--vfo-lamp-color` when a host sets it,
  then the design-language `--dl-*` chain with the same fallbacks VfoPanel
  uses — never a new palette.
-->
<script lang="ts">
  interface Props {
    /** The key's engraved label — always drawn, read or unread. */
    label: string;
    /** Lit state; `null` = declared-but-unread (drawn unlit, label only). */
    lit: boolean | null;
    /**
     * 'toggle' → `aria-pressed` when read, omitted when unread.
     * 'choice' → `role="radio"` + `aria-checked` ("false" when unread).
     * 'action' → plain button (e.g. the ⋯ More key), never pressed/checked.
     */
    kind?: 'toggle' | 'choice' | 'action';
    /** Unusable (unreadable or operationally stale) — drawn, never hidden. */
    disabled?: boolean;
    testid?: string;
    ariaLabel?: string;
    ariaExpanded?: boolean;
    /** Reserved width, e.g. '44px' — the key's box is this wide in EVERY state. */
    width?: string;
    element?: HTMLElement | null;
    onclick?: (event: MouseEvent) => void;
  }
  let {
    label, lit, kind = 'toggle', disabled = false, testid, ariaLabel, ariaExpanded,
    width, element = $bindable(null), onclick,
  }: Props = $props();
</script>

<button
  type="button"
  class="scope-flat-key"
  data-lit={lit === true}
  data-testid={testid}
  role={kind === 'choice' ? 'radio' : undefined}
  aria-checked={kind === 'choice' ? lit === true : undefined}
  aria-pressed={kind === 'toggle' && lit !== null ? lit : undefined}
  aria-label={ariaLabel}
  aria-expanded={ariaExpanded}
  {disabled}
  style={width === undefined ? undefined : `--scope-key-width: ${width}`}
  bind:this={element}
  {onclick}
>{label}</button>

<style>
  .scope-flat-key {
    appearance: none;
    background: transparent;
    border: none;
    border-radius: 0;
    padding: 0;
    margin: 0;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    box-sizing: border-box;
    flex: none;
    width: var(--scope-key-width, 48px);
    height: 22px;
    font-family: inherit;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.03em;
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
    color: var(--vfo-lamp-color, var(--dl-vfo-red-text, var(--dl-vfo-red, #e2362c)));
    cursor: pointer;
  }

  .scope-flat-key[data-lit='false'] {
    color: var(--dl-vfo-unlit-text, var(--v2-text-muted, #5a6875));
    font-weight: 400;
  }

  .scope-flat-key[data-lit='true'] {
    text-shadow: var(--dl-vfo-red-glow, none);
  }

  .scope-flat-key:disabled {
    cursor: not-allowed;
  }
</style>
