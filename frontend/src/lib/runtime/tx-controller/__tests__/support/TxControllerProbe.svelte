<script module lang="ts">
  import type { AppTxController } from '$lib/runtime/tx-controller/app-host';

  // Test-only escape hatch (MOR-1089 U6). `getAppTxController()` reads Svelte
  // context, which only resolves from inside a live component's own
  // initialization — there is no way for a `.test.ts` file to call it
  // directly against a real, mounted `App.svelte` tree. This stub replaces
  // `RadioLayoutV2` (the same substitution app-lifecycle.component.test.ts
  // makes, there for jsdom-cost reasons) and, instead of rendering nothing,
  // captures the one real `AppTxController` facade `App.svelte`'s real
  // `provideAppTxControllerHost` put into context — the exact same object a
  // real panel (TxPanel.svelte) would retrieve — so the test can drive
  // `.start()` / `.setIntent()` / `.release()` on it directly.
  let captured: AppTxController | null = null;

  export function capturedController(): AppTxController | null {
    return captured;
  }

  export function resetCapturedController(): void {
    captured = null;
  }
</script>

<script lang="ts">
  import { getAppTxController } from '$lib/runtime/tx-controller/app-host';

  captured = getAppTxController();
</script>

<div class="tx-controller-probe"></div>
