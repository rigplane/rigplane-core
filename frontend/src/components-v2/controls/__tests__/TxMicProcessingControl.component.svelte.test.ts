import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import TxMicProcessingControl from '../TxMicProcessingControl.svelte';
import {
  getTxMicProcessing,
  reloadTxMicProcessingForTests,
  TX_MIC_PROCESSING_KEY,
} from '$lib/stores/tx-mic-processing.svelte';

let component: ReturnType<typeof mount> | null = null;
let target: HTMLElement;

beforeEach(() => {
  localStorage.removeItem(TX_MIC_PROCESSING_KEY);
  reloadTxMicProcessingForTests();
  target = document.createElement('div');
  document.body.append(target);
});
afterEach(() => {
  if (component) unmount(component);
  component = null;
  target.remove();
});

const radio = (mode: string) =>
  target.querySelector<HTMLInputElement>(`[data-testid="tx-mic-processing-${mode}"]`)!;

describe('TxMicProcessingControl (MOR-4596)', () => {
  it('shows raw selected by default', () => {
    component = mount(TxMicProcessingControl, { target });
    expect(radio('raw').checked).toBe(true);
    expect(radio('browser').checked).toBe(false);
  });

  it('switching to browser processing is stored and survives a reload', () => {
    component = mount(TxMicProcessingControl, { target });
    radio('browser').click();
    flushSync();
    expect(getTxMicProcessing()).toBe('browser');
    expect(localStorage.getItem(TX_MIC_PROCESSING_KEY)).toBe('browser');
    expect(radio('browser').checked).toBe(true);

    unmount(component);
    reloadTxMicProcessingForTests();
    component = mount(TxMicProcessingControl, { target });
    expect(radio('browser').checked).toBe(true);
  });
});
