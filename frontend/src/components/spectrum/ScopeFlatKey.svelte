<!--
  Scope flat key (MOR-2545 PR1; PR3 round 4 made the HOSTED row's look the
  Standard-face raised-key family). The styles BELOW are the default flat
  lamp grammar — every mount that is not the hosted toolbar row (the LCD
  skins, mobile, the bare zone mounts and the desktop standalone audio_fft
  surfaces) renders exactly this; inside `.spectrum-toolbar.hosted` the
  family rules of components-v2/controls/control-button.css take over (the
  family's exclusion lists admit the key ONLY there, via
  `.scope-flat-key:not(.spectrum-toolbar.hosted *)`), with scope-capsule.css
  pinning the row's geometry and resetting the two scoped colours below
  that outrank the family's :where() base.

  PLACEMENT: the key lives here, in `components/spectrum/`, because
  `semantic/ScopeControlsSurface.svelte` already imports
  `components/spectrum/spectrum-toolbar-logic` — a proven allowed path (the
  eslint FORBIDDEN_SEMANTIC_IMPORTS lockdown bans skins/runtime only) — and
  because it shares the scope surface with `ScopeMorePanel.svelte`.

  The class name `scope-flat-key` is historical (PR1's flat lamp look); it
  is kept because tests pin the family-sheet exclusion split by name: the
  exclusion lists carry the name in their hosted-only conditional form (the
  family reaches the key only inside `.spectrum-toolbar.hosted`), while
  `.scope-step-key` stays excluded outright.

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
-->
<script lang="ts">
  import './scope-capsule.css';

  interface Props {
    /** The key's engraved label — always drawn, read or unread. */
    label: string;
    /** Lit state; `null` = declared-but-unread (drawn unlit, label only). */
    lit: boolean | null;
    /**
     * 'toggle' → `aria-pressed` when read, omitted when unread.
     * 'choice' → `role="radio"` + `aria-checked` ("false" when unread).
     * 'action' → plain button (e.g. the More key), never pressed/checked.
     */
    kind?: 'toggle' | 'choice' | 'action';
    /** Unusable (unreadable or operationally stale) — drawn, never hidden. */
    disabled?: boolean;
    testid?: string;
    ariaLabel?: string;
    ariaExpanded?: boolean;
    /** Tooltip text (MOR-2545 PR3: the More key carries one). */
    title?: string;
    /** Reserved width, e.g. '44px' — the key's box is this wide in EVERY state. */
    width?: string;
    element?: HTMLElement | null;
    onclick?: (event: MouseEvent) => void;
  }
  let {
    label, lit, kind = 'toggle', disabled = false, testid, ariaLabel, ariaExpanded,
    title, width, element = $bindable(null), onclick,
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
  {title}
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

  /* Hover brightens the text only — lamp grammar, never chrome. */
  .scope-flat-key:hover:not(:disabled) { filter: brightness(1.3); }

  .scope-flat-key:disabled {
    cursor: not-allowed;
  }
</style>
