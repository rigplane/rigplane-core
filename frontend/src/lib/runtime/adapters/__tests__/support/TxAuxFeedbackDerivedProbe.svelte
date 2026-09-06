<script lang="ts">
  import { getTxAuxControlFeedback } from '../../panel-adapters';

  const feedback = $derived(getTxAuxControlFeedback('micGain'));
</script>

<output
  data-phase={feedback.phase}
  data-availability={feedback.availability}
  data-confirmed={feedback.confirmed ?? ''}
  data-target={feedback.target ?? ''}
  data-outcome={feedback.outcome?.phase ?? ''}
  data-provider={feedback.providerGeneration ?? ''}
  data-session={feedback.sessionEpoch}
></output>
