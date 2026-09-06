<script lang="ts">
  import VfoPanel from './VfoPanel.svelte';
  import { getCapabilities, receiverLabel, vfoSlotLabel } from '$lib/stores/capabilities.svelte';
  import { findActiveBand } from '../controls/band-utils';
  import { formatBadges } from './vfo-utils';
  import type { VfoLayoutProfile } from '../layout/vfo-layout-tokens';

  interface Props {
    receiver: 'main' | 'sub';
    freq: number;
    mode: string;
    filter: string;
    sValue: number;
    isActive: boolean;
    badges: Record<string, boolean | string>;
    rit?: { active: boolean; offset: number };
    layoutProfile?: VfoLayoutProfile;
    onModeClick?: () => void;
    onVfoClick?: () => void;
    onFreqChange?: (freq: number) => void;
  }

  let {
    receiver, freq, mode, filter, sValue, isActive, badges, rit,
    layoutProfile = 'baseline', onModeClick, onFreqChange,
  }: Props = $props();

  let slot = $derived<'A' | 'B'>(receiver === 'main' ? 'A' : 'B');
  let label = $derived(receiverLabel(receiver === 'main' ? 'MAIN' : 'SUB'));
  let slotTag = $derived(vfoSlotLabel(slot).replace(/^VFO /, ''));
  let bandText = $derived(findActiveBand(freq, getCapabilities()?.freqRanges ?? []));
  let badgeItems = $derived(formatBadges(badges, receiver));
</script>

<VfoPanel
  {receiver} receiverLabel={label} {slotTag}
  freq={Number.isFinite(freq) ? freq : null}
  displayHz={Number.isFinite(freq) ? freq : null}
  {mode} {filter} {sValue} {isActive} {badgeItems} {bandText} {rit}
  {layoutProfile} {onModeClick} {onFreqChange}
/>
