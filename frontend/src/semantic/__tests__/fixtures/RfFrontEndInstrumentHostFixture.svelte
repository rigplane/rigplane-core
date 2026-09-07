<script lang="ts">
  import RfFrontEndInstrumentHost from '../../RfFrontEndInstrumentHost.svelte';
  import type { RadioViewModel } from '../../radio-view-model';
  import type {
    RfFrontEndAuthorityPublication,
    RfFrontEndLevelFeedback,
    RfFrontEndLevelField,
    RfFrontEndLevelHandles,
    RfSqlControlModel,
    SubscribeRfFrontEndAuthority,
  } from '../../rf-front-end-instruments';

  interface Props {
    publication: RfFrontEndAuthorityPublication;
    view: RadioViewModel | null;
    subscribeControlAuthority: SubscribeRfFrontEndAuthority;
    controlModel: RfSqlControlModel;
    rfSqlFeedback?: RfFrontEndLevelFeedback | null;
    layout?: 'grouped' | 'independent';
    onLevelChange?: (field: RfFrontEndLevelField, value: number) => void;
  }

  let {
    publication, view, subscribeControlAuthority, controlModel, rfSqlFeedback,
    layout = 'grouped', onLevelChange,
  }: Props = $props();
  let presentation = $derived({
    ...publication, view, controlModel, rfSqlFeedback,
  });
</script>

<RfFrontEndInstrumentHost
  {presentation} {subscribeControlAuthority} {onLevelChange}
>
  {#snippet children(handles: RfFrontEndLevelHandles)}
    {#key layout}
      <section data-layout={layout} data-handle-kind={handles.kind}>
        {#if handles.kind === 'combined'}
          <div data-slot="rf-sql">{@render handles.rfSql()}</div>
        {:else}
          <div data-slot="rf-gain">{@render handles.rfGain()}</div>
          <div data-slot="squelch">{@render handles.squelch()}</div>
        {/if}
      </section>
    {/key}
  {/snippet}
</RfFrontEndInstrumentHost>
