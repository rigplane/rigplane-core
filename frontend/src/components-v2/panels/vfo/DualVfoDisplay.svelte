<!--
  DualVfoDisplay — side-by-side MAIN + SUB VFO tiles for dual-RX radios.

  Presentation-only: renders two VfoPanel tiles. The active tile is visually
  highlighted via the `.is-active` class. Receiver activation is performed
  via the segmented [M|S] toggle in VfoOps (see `ActiveReceiverToggle.svelte`,
  issue #825 Option B) — a single primary affordance, no duplicate UX.

  The `onActivate` prop is retained for API compatibility and is used by
  tests, but is no longer wired to any in-tile control.
-->
<script lang="ts">
  import VfoPanel from '../../../components-v2/vfo/VfoPanel.svelte';
  import type { VfoStateProps } from '../../../components-v2/layout/layout-utils';
  import type { VfoLayoutProfile } from '../../../components-v2/layout/vfo-layout-tokens';

  interface Props {
    main: VfoStateProps;
    sub: VfoStateProps;
    active: 'MAIN' | 'SUB';
    layoutProfile?: VfoLayoutProfile;
    onActivate?: (receiver: 'MAIN' | 'SUB') => void;
    onMainModeClick?: () => void;
    onSubModeClick?: () => void;
    onMainFreqChange?: (freq: number) => void;
    onSubFreqChange?: (freq: number) => void;
  }

  // `onActivate` is retained in the Props type for API compatibility
  // but is no longer wired to any in-tile control. Receiver activation
  // lives in the segmented [M|S] toggle (ActiveReceiverToggle) in VfoOps.
  let {
    main,
    sub,
    active,
    layoutProfile = 'baseline',
    onMainModeClick,
    onSubModeClick,
    onMainFreqChange,
    onSubFreqChange,
  }: Props = $props();
</script>

<div
  class="dual-vfo-tile vfo-main-panel"
  class:is-active={active === 'MAIN'}
  data-receiver="main"
  data-layout-profile={layoutProfile}
>
  <VfoPanel
    {...main}
    {layoutProfile}
    onModeClick={onMainModeClick}
    onFreqChange={onMainFreqChange}
  />
</div>

<div
  class="dual-vfo-tile vfo-sub-panel"
  class:is-active={active === 'SUB'}
  data-receiver="sub"
  data-layout-profile={layoutProfile}
>
  <VfoPanel
    {...sub}
    {layoutProfile}
    onModeClick={onSubModeClick}
    onFreqChange={onSubFreqChange}
  />
</div>

<style>
  .dual-vfo-tile {
    min-width: 0;
    display: block;
    position: relative;
    border-radius: 4px;
    transition: box-shadow 150ms ease;
  }
</style>
