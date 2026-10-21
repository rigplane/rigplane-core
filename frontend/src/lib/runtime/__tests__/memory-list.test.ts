import { describe, expect, it, vi } from 'vitest';
import { MemoryListController } from '../memory-list.svelte';
import type { MemoryEntryDto, MemoryPageDto } from '$lib/types/memory-list';

const slot = (channel: number, empty = true): MemoryEntryDto =>
  empty ? { group: 0, channel, empty } : { group: 0, channel, empty, name: `CH${channel}`, frequencyHz: 146_940_000, mode: 'FM' };

function pageOf(start: number, count: number, last = 5, group = 0): MemoryPageDto {
  const entries = Array.from({ length: Math.min(count, last - start + 1) }, (_, i) => slot(start + i, (start + i) % 2 === 1));
  return { addressing: 'group_channel', group, groups: { first: 0, last: 99 }, channels: { first: 0, last }, recall: true, entries };
}

describe('MemoryListController', () => {
  it('reads the whole group page by page, then reports ready', async () => {
    const fetchPage = vi.fn(async (req: { group?: number; start?: number; count?: number }) => pageOf(req.start ?? 0, 2));
    const list = new MemoryListController(fetchPage);
    await list.load();
    expect(fetchPage.mock.calls.map(([req]) => req.start)).toEqual([undefined, 2, 4]);
    expect(list.status).toBe('ready');
    expect(list.entries.map((e) => e.channel)).toEqual([0, 1, 2, 3, 4, 5]);
    expect(list.recall).toBe(true);
    expect(list.groups).toEqual({ first: 0, last: 99 });
  });

  it('shows a failure and keeps no half list as ready', async () => {
    const list = new MemoryListController(async () => { throw new Error('fetchMemoryPage: 503'); });
    await list.load();
    expect(list.status).toBe('error');
    expect(list.error).toBe('fetchMemoryPage: 503');
  });

  it('a newer load wins over the one still in flight', async () => {
    const release: Array<() => void> = [];
    const fetchPage = vi.fn((req: { group?: number }, signal?: AbortSignal) => new Promise<MemoryPageDto>((resolve, reject) => {
      signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
      release.push(() => resolve(pageOf(0, 10, 1, req.group ?? 0)));
    }));
    const list = new MemoryListController(fetchPage);
    const first = list.load(1);
    const second = list.load(2);
    release.at(-1)?.();
    await Promise.all([first, second]);
    expect(list.group).toBe(2);
    expect(list.status).toBe('ready');
  });

  it('a response that settles after cancellation never repopulates the list', async () => {
    let settle: (page: MemoryPageDto) => void = () => undefined;
    const fetchPage = vi.fn(() => new Promise<MemoryPageDto>((resolve) => { settle = resolve; }));
    const list = new MemoryListController(fetchPage); // ignores the abort signal, like a response already in flight
    const loading = list.load(1);
    list.reset();
    settle(pageOf(0, 10, 1, 1));
    await loading;
    expect(list.status).toBe('idle');
    expect(list.entries).toEqual([]);
    expect(fetchPage).toHaveBeenCalledTimes(1);
  });

  it('an older load that settles late is not mixed into the newer one', async () => {
    const settles: Array<(page: MemoryPageDto) => void> = [];
    const fetchPage = vi.fn(() => new Promise<MemoryPageDto>((resolve) => { settles.push(resolve); }));
    const list = new MemoryListController(fetchPage);
    const older = list.load(1);
    const newer = list.load(2);
    settles[1](pageOf(0, 10, 1, 2));
    settles[0](pageOf(0, 10, 1, 1));
    await Promise.all([older, newer]);
    expect(list.group).toBe(2);
    expect(list.entries.map((e) => e.group)).toEqual([0, 0]);
    expect(list.status).toBe('ready');
  });

  it('keeps the chosen group when its first page fails, so a retry asks for it again', async () => {
    const fetchPage = vi.fn(async (req: { group?: number }) => {
      if (req.group === 7) throw new Error('fetchMemoryPage: 503');
      return pageOf(0, 10, 1, req.group ?? 0);
    });
    const list = new MemoryListController(fetchPage);
    await list.load(0);
    await list.load(7);
    expect(list.status).toBe('error');
    expect(list.group).toBe(7);
    await list.load(list.group ?? undefined);
    expect(fetchPage.mock.calls.at(-1)?.[0].group).toBe(7);
  });

  it('a new radio starts from its default group, with the old radio\'s bounds cleared', async () => {
    let fail = false;
    const fetchPage = vi.fn(async (req: { group?: number }) => {
      if (fail) throw new Error('fetchMemoryPage: 503');
      return pageOf(0, 10, 1, req.group ?? 0);
    });
    const list = new MemoryListController(fetchPage);
    await list.load(7);
    list.reset();
    expect([list.group, list.groups, list.recall]).toEqual([null, null, false]);
    fail = true;
    await list.load();
    expect(list.status).toBe('error');
    expect(list.group).toBeNull();
    fail = false;
    await list.load(list.group ?? undefined);
    expect(fetchPage.mock.calls.at(-1)?.[0].group).toBeUndefined();
  });
});
