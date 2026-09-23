<!--
  Scope capsule key (MOR-2545 PR1 flat key, restyled to the owner's style C
  "capsule groups" in PR3) — the clickable member of the panorama row's ONE
  visual family (`components/spectrum/scope-capsule.css`): 1 px border, 5 px
  radius, no fill at rest, lit = FILLED with the badge active pair. The look
  lives in the shared capsule stylesheet; this component carries only the
  state semantics (data-lit / aria) and the reserved-width custom property.

  PLACEMENT: the key lives here, in `components/spectrum/`, because
  `semantic/ScopeControlsSurface.svelte` already imports
  `components/spectrum/spectrum-toolbar-logic` — a proven allowed path (the
  eslint FORBIDDEN_SEMANTIC_IMPORTS lockdown bans skins/runtime only) — and
  because it shares the scope surface with `ScopeMorePanel.svelte`.

  The class name `scope-flat-key` is historical (PR1's flat lamp look); it
  is kept because the Standard-face override sheets
  (`components-v2/controls/control-button.css`,
  `skins/desktop-v2/semantic-controls.css`) exclude exactly this name from
  the hardware-bezel button family.

  Owner rules honoured here:
  - `lit: null` = declared-but-unread: the key is drawn UNLIT, in place, with
    its label and NO value. A toggle then OMITS `aria-pressed` entirely
    (never "false"); a choice reports `aria-checked="false"`. No placeholder
    text is ever drawn — the label is the content.
  - Nothing blinks or shifts: width comes from `--scope-key-width` (per-key
    reserved width set by the host, a floor — mono text is deterministic)
    and only colour/fill change between unlit/lit — the box never changes
    size.
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
    /** Reserved width, e.g. '44px' — the key's box is at least this wide in EVERY state. */
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
