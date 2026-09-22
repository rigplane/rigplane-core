<script lang="ts">
  import VfoPanel, { type VfoPanelSections } from './VfoPanel.svelte';
  import { getCapabilities, receiverLabel } from '$lib/stores/capabilities.svelte';
  import { findActiveBand } from '../controls/band-utils';
  import { formatBadges, formatRitOffset } from './vfo-utils';
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

  let label = $derived(receiverLabel(receiver === 'main' ? 'MAIN' : 'SUB'));
  // The v7 meter face no longer paints the slot label (MOR-2509 package B),
  // so the i18n lookup retired with it; the meter's label channel still
  // receives the plain slot id.
  let slotTag = $derived(receiver === 'main' ? 'A' : 'B');
  let bandText = $derived(findActiveBand(freq, getCapabilities()?.freqRanges ?? []));

  // The legacy deck has no indicator view model, so its facts map onto the
  // panel sections by badge key: nb/nr/notch become DSP chips, pre/att/atu
  // become front-end lamps, and every other key an amber lamp. `formatBadges`
  // walks `Object.entries(badges)` in order, so its output stays aligned with
  // `Object.keys(badges)` used for the classification.
  let sections = $derived.by(() => {
    const built: VfoPanelSections = {
      tray: {
        band: bandText ? { key: 'band', text: bandText.toUpperCase(), lit: true } : undefined,
      },
      annunciators: [],
      dsp: [],
      under: rit
        ? {
          rit: {
            key: 'rit',
            text: rit.active ? `RIT ${formatRitOffset(rit.offset)}` : 'RIT',
            lit: rit.active,
            state: rit.active ? 'on' : 'off',
          },
        }
        : {},
      tx: { lit: false, state: 'unknown' },
    };
    const keys = Object.keys(badges);
    formatBadges(badges, receiver).forEach((item, index) => {
      const key = keys[index];
      const lit = item.active;
      if (key === 'nb' || key === 'nr' || key === 'notch') {
        built.dsp.push({
          key,
          text: key === 'notch' && typeof badges.notch === 'string'
            ? `NOTCH ${String(badges.notch).toUpperCase()}`
            : item.label,
          lit,
        });
      } else if (key === 'pre' || key === 'att' || key === 'atu') {
        built.annunciators.push({
          key, group: 'front', family: key === 'atu' ? 'amber' : 'red',
          text: item.label, lit, color: item.color,
        });
      } else {
        built.annunciators.push({
          key, group: 'front', family: 'amber', text: item.label, lit, color: item.color,
        });
      }
    });
    return built;
  });
</script>

<VfoPanel
  {receiver} receiverLabel={label} {slotTag}
  freq={Number.isFinite(freq) ? freq : null}
  displayHz={Number.isFinite(freq) ? freq : null}
  {mode} {filter} {sValue} {isActive} {sections} {layoutProfile} {onModeClick} {onFreqChange}
/>
