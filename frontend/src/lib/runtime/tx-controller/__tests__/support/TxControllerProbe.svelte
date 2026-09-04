<script module lang="ts">
  import type { ManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';

  // Test-only view of the one canonical controller installed by App.svelte.
  // The returned facade exposes the same lifecycle actions and server projection
  // that production presentations consume; it adds no adapter or second owner.
  let captured: ManagedAppTxController | null = null;

  export function capturedController(): ManagedAppTxController | null {
    return captured;
  }

  export function resetCapturedController(): void {
    captured = null;
  }
</script>

<script lang="ts">
  import { getManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';

  captured = getManagedAppTxController();
</script>

<div class="tx-controller-probe"></div>
