<script module lang="ts">
  export type LcdDisplayVariantId = 'peer' | 'dominant' | 'centerstage' | 'panadapter';
</script>

<script lang="ts">
  import type { Snippet } from 'svelte';

  interface Props {
    variant: LcdDisplayVariantId;
    peer: Snippet;
    dominant: Snippet;
    centerstage: Snippet;
    panadapter: Snippet;
  }

  let {
    variant,
    peer,
    dominant,
    centerstage,
    panadapter,
  }: Props = $props();
</script>

{#if variant === 'peer'}
  {@render peer()}
{:else if variant === 'dominant'}
  {@render dominant()}
{:else if variant === 'centerstage'}
  {@render centerstage()}
{:else if variant === 'panadapter'}
  {@render panadapter()}
{/if}
