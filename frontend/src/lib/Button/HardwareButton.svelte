<script lang="ts">
  import ControlButton from './ControlButton.svelte';
  import type { HardwareButtonProps } from './types';

  interface Props extends HardwareButtonProps {
    children?: any;
  }

  let {
    active = false,
    disabled = false,
    compact = false,
    indicator = 'dot',
    color = 'cyan',
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
  indicatorStyle={indicator}
  indicatorColor={color}
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
