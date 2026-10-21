import { describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { MemoryListController, trackMemoryList } from '../memory-list.svelte';
import type { MemoryPageDto } from '$lib/types/memory-list';

const pageOf = (): MemoryPageDto => ({
  addressing: 'group_channel', group: 0, groups: { first: 0, last: 99 }, channels: { first: 0, last: 1 }, recall: true,
  entries: [{ group: 0, channel: 0, empty: true }, { group: 0, channel: 1, empty: true }],
});

describe('trackMemoryList', () => {
  it('loads while readable and cancels when the host is destroyed', async () => {
    let signal: AbortSignal | undefined;
    let settle: (page: MemoryPageDto) => void = () => undefined;
    const fetchPage = vi.fn((_req: object, s?: AbortSignal) => new Promise<MemoryPageDto>((resolve) => { signal = s; settle = resolve; }));
    const list = new MemoryListController(fetchPage);
    const destroy = $effect.root(() => trackMemoryList(list, () => true));
    flushSync();
    expect(fetchPage).toHaveBeenCalledTimes(1);
    destroy();
    expect(signal?.aborted).toBe(true);
    expect(list.status).toBe('idle');
    settle(pageOf());
    await Promise.resolve();
    expect(list.entries).toEqual([]);
    expect(fetchPage).toHaveBeenCalledTimes(1);
  });
});
