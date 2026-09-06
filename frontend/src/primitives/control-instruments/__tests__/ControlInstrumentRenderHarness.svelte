<script lang="ts">
  import {
    bindChoiceInstrument, bindToggleInstrument, type InstrumentField,
  } from '../control-instrument-behavior';

  interface Props {
    toggle: InstrumentField<boolean>;
    choice: InstrumentField<string>;
    choices: readonly string[];
    onToggle: (next: boolean) => void;
    onChoice: (value: string) => void;
  }

  let { toggle, choice, choices, onToggle, onChoice }: Props = $props();
  const toggleBehavior = bindToggleInstrument(() => ({ field: toggle, invoke: onToggle }));
  const choiceBehavior = bindChoiceInstrument(() => ({ field: choice, choices, invoke: onChoice }));
</script>

<button
  type="button" role="checkbox" data-testid="alternate-toggle"
  aria-checked={toggleBehavior.confirmed}
  disabled={!toggleBehavior.available}
  onclick={() => toggleBehavior.invoke()}
>Toggle</button>

<div role="grid" aria-label="Alternative choices">
  {#each choices as choice (choice)}
    <button
      type="button" role="radio" data-testid={`alternate-choice-${choice}`}
      aria-checked={choiceBehavior.isSelected(choice)}
      disabled={!choiceBehavior.available}
      onclick={() => choiceBehavior.invoke(choice)}
    >{choice}</button>
  {/each}
</div>
