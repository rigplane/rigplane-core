<script lang="ts">
  import { createDragReorder } from '../drag-reorder.svelte';

  const left = createDragReorder({
    storageKey: 'test:drag:left',
    defaults: ['alpha', 'beta'],
    containerSelector: '.test-drag-left',
  });
  const bottom = createDragReorder({
    storageKey: 'test:drag:bottom',
    defaults: [],
    containerSelector: '.test-drag-bottom',
  });
  const right = createDragReorder({
    storageKey: 'test:drag:right',
    defaults: ['gamma', 'delta'],
    containerSelector: '.test-drag-right',
  });
  let handleGeneration = $state(0);
</script>

{#snippet dock(name: string, drag: typeof left)}
  <section
    class={`test-drag-${name}`}
    data-zone={name}
    data-drop-target={drag.isDropTarget}
  >
    {#each drag.order as panelId (panelId)}
      <article data-panel-id={panelId} style={drag.dragStyle(panelId)}>
        {#key handleGeneration}
          <button
            data-drag-handle={panelId}
            onpointerdown={(event) => drag.handleDragStart(panelId, event)}
          >{panelId}</button>
        {/key}
      </article>
    {/each}
  </section>
{/snippet}

{@render dock('left', left)}
{@render dock('bottom', bottom)}
{@render dock('right', right)}
<button data-reset-all onclick={() => left.resetAll()}>reset</button>
<button data-replace-handles onclick={() => handleGeneration += 1}>replace handles</button>
