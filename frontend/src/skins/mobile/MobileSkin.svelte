<!--
  Mobile Skin — touch-optimized stacked layout.

  Delegation wrapper during migration. Future phases will compose semantic
  components with compact mobile-specific renderers.
-->
<script lang="ts">
  import '../../components-v2/theme/index';
  import { getTheme, setTheme } from '../../components-v2/theme/theme-switcher';
  import MobileRadioLayout from '../../components-v2/layout/MobileRadioLayout.svelte';

  if (typeof window !== 'undefined') {
    setTheme(getTheme());
  }
</script>

<MobileRadioLayout />
