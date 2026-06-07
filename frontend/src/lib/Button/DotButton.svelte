<script lang="ts">
  import ControlButton from './ControlButton.svelte';
  import type { DotButtonProps } from './types';

  interface Props extends DotButtonProps {
    children?: any;
  }

  let {
    active = false,
    disabled = false,
    compact = false,
    color = 'cyan',
    glow = 'color',
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
  indicatorStyle="dot"
  indicatorColor={color}
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
