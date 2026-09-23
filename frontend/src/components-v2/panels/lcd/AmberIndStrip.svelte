<script lang="ts">
  /**
   * AmberIndStrip — reusable indicator (status token) strip for LCD skins.
   *
   * Zone is chosen by the consumer per-skin, not intrinsic to the token.
   * The same token ID (e.g. 'tx', 'nb') may appear in different zones depending
   * on which skin is using the strip.
   *
   * Zone values:
   *   'global'   — radio-wide indicators (used by lcd-cockpit global strip)
   *   'perVfo'   — per-receiver indicators (used by lcd-cockpit VFO columns)
   *   'frontend' — TX-chain indicators: TX/VOX/PROC/ATT/PRE (used by lcd-scope)
   *   'dsp'      — RX-DSP indicators: NB/NR/NOTCH/ANF/RFG (used by lcd-scope)
   *
   * Token IDs are defined below; labels and active state come from the consumer.
   */

  export type IndTokenId =
    | 'tx' | 'vox' | 'proc' | 'atu' | 'split' | 'lock' | 'data' | 'ipPlus'
    | 'nb' | 'nr' | 'notch' | 'anf' | 'cont' | 'att' | 'pre'
    | 'digisel' | 'rfg' | 'sql' | 'agc' | 'rit';

  export interface IndToken {
    id: IndTokenId;
    label: string;
    active: boolean;
    /** If set, the token is only rendered when this capability is present. */
    capability?: string;
    /** 'tx' → red TX styling; 'tuning' → blinking animation */
    variant?: 'tx' | 'tuning';
    /** Owner ruling 2026-09-23 (MOR-2546): reserve this chip's slot width so
     *  an emptied label never shifts neighbouring chips; the emptied chip
     *  prints no text and is aria-hidden. */
    reserveSlot?: boolean;
  }

  interface Props {
    zone: 'global' | 'perVfo' | 'dsp' | 'frontend';
    tokens: IndToken[];
  }

  let { zone, tokens }: Props = $props();

  let zoneLabel = $derived(
    zone === 'frontend' ? 'FRONT' :
    zone === 'dsp' ? 'DSP' :
    null
  );
</script>

<div
  class="lcd-ind-strip"
  class:zone-global={zone === 'global'}
  class:zone-per-vfo={zone === 'perVfo'}
  class:zone-frontend={zone === 'frontend'}
  class:zone-dsp={zone === 'dsp'}
>
  {#if zoneLabel}
    <span class="ind-zone-label">{zoneLabel}</span>
  {/if}
  {#each tokens as token (token.id)}
    <span
      class="lcd-ind"
      class:active={token.active}
      class:ind-tx={token.variant === 'tx'}
      class:ind-tuning={token.variant === 'tuning'}
      class:slot-reserved={token.reserveSlot === true}
      class:ind-empty={token.label === ''}
      aria-hidden={token.label === '' ? 'true' : undefined}
    >{token.label}</span>
  {/each}
</div>

<style>
  .lcd-ind-strip {
    display: flex;
    gap: 6px;
    align-items: center;
    flex-wrap: wrap;
    position: relative;
    z-index: 2;
    padding: 2px 0;
    overflow: hidden;
  }

  /* Global strip: horizontal flex-wrap, full-width */
  .zone-global {
    flex-direction: row;
    flex-shrink: 1;
  }

  /* Per-VFO strip: compact horizontal, fits inside cockpit column */
  .zone-per-vfo {
    flex-direction: row;
    flex-shrink: 1;
    gap: 5px;
  }

  /* Frontend strip: TX-chain indicators (TX/VOX/PROC/ATT/PRE) */
  .zone-frontend {
    flex-direction: row;
    flex-shrink: 1;
    gap: 5px;
  }

  /* DSP strip: RX-processing indicators (NB/NR/NOTCH/ANF/RFG) */
  .zone-dsp {
    flex-direction: row;
    flex-shrink: 1;
    gap: 5px;
  }

  /* Zone label: small ghost tag prefixing the zone's chips */
  .ind-zone-label {
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.5px;
    color: rgba(26, 16, 0, var(--lcd-alpha-ghost));
    user-select: none;
    white-space: nowrap;
    padding-right: 2px;
  }

  .lcd-ind {
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.5px;
    color: rgba(26, 16, 0, var(--lcd-alpha-inactive));
    border: 1.5px solid rgba(26, 16, 0, var(--lcd-alpha-ghost));
    border-radius: 3px;
    padding: 1px 5px;
    user-select: none;
    white-space: nowrap;
  }

  .lcd-ind.active {
    color: rgba(26, 16, 0, var(--lcd-alpha-active));
    border-color: rgba(26, 16, 0, calc(var(--lcd-alpha-active) * 0.4));
  }

  /* Owner ruling 2026-09-23 (MOR-2546, extends #3591): a reserving token
     (RFG) keeps one fixed slot width whether lit, unlit, or emptied — the
     4ch reservation is wider than the widest text it prints ('RFG'), so a
     gain change between reduced / full never moves a neighbour.
     Emptied: nothing is drawn — no text, transparent border, hidden from
     assistive tech — mirroring the VFO deck (`VfoIndicatorRow`'s
     min-inline-size reservation, `VfoPanel`'s fixed [data-chip='rfg'] lamp). */
  .lcd-ind.slot-reserved {
    min-inline-size: 4ch;
    box-sizing: content-box;
    text-align: center;
  }

  .lcd-ind.ind-empty {
    border-color: transparent;
  }

  /* TX indicator: red accent */
  .lcd-ind.ind-tx {
    color: #5A0800;
    border-color: rgba(90, 8, 0, 0.5);
    font-size: 13px;
  }

  /* ATU TUNE: blink while tuning */
  .lcd-ind.ind-tuning {
    animation: lcd-blink 0.6s steps(1) infinite;
  }

  @keyframes lcd-blink {
    0%, 50% { opacity: 1; }
    51%, 100% { opacity: 0.15; }
  }
</style>
