<script lang="ts">
  import ControlButton from './ControlButton.svelte';
  import type { HardwarePlainButtonProps } from './types';

  interface Props extends HardwarePlainButtonProps {
    children?: any;
  }

  let {
    active = false,
    disabled = false,
    compact = false,
    glow = 'warm',
    title = null,
    shortcutHint = null,
    onclick,
    onpointerdown,
    onpointerup,
    onpointercancel,
    onpointerleave,
    children
  }: Props = $props();
</script>

<ControlButton
  {active}
  {disabled}
  {compact}
  surface="hardware"
  {glow}
  {title}
  {shortcutHint}
  {onclick}
  {onpointerdown}
  {onpointerup}
  {onpointercancel}
  {onpointerleave}
>
  {@render children?.()}
</ControlButton>
