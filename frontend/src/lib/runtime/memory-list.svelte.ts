import { untrack } from 'svelte';
import type { MemoryEntryDto, MemoryPageDto } from '$lib/types/memory-list';

const PAGE_SIZE = 25;

export type MemoryListStatus = 'idle' | 'loading' | 'ready' | 'error';
type FetchPage = (request: { group?: number; start?: number; count?: number }, signal?: AbortSignal) => Promise<MemoryPageDto>;

/**
 * The radio's stored memory channels, read page by page (MOR-3627). Read-only:
 * nothing here writes to the radio; a newer `load` cancels the one in flight.
 */
export class MemoryListController {
  status = $state<MemoryListStatus>('idle');
  error = $state<string | null>(null);
  group = $state<number | null>(null);
  groups = $state<{ first: number; last: number } | null>(null);
  recall = $state(false);
  entries = $state<MemoryEntryDto[]>([]);
  #next = $state<number | null>(null);
  #abort: AbortController | null = null;
  readonly #fetchPage: FetchPage;

  constructor(fetchPage: FetchPage) {
    this.#fetchPage = fetchPage;
  }

  get hasMore(): boolean {
    return this.#next !== null;
  }

  load(group?: number): Promise<void> {
    this.entries = [];
    this.#next = null;
    this.group = group ?? null; // the selection survives a failed first page; omitted = the default
    return this.#loadPage({ group, count: PAGE_SIZE });
  }

  async loadMore(): Promise<void> {
    if (this.status === 'loading' || this.#next === null) return;
    await this.#loadPage({ group: this.group ?? undefined, start: this.#next, count: PAGE_SIZE });
  }

  async #loadPage(request: Parameters<FetchPage>[0]): Promise<void> {
    this.#abort?.abort();
    const abort = new AbortController();
    this.#abort = abort;
    const current = () => this.#abort === abort && !abort.signal.aborted;
    this.status = 'loading';
    this.error = null;
    try {
      const page = await this.#fetchPage(request, abort.signal);
      if (!current()) return;
      this.group = page.group;
      this.groups = page.groups;
      this.recall = page.recall;
      this.entries = [...this.entries, ...page.entries];
      const last = page.entries.at(-1)?.channel;
      this.#next = last !== undefined && last < page.channels.last ? last + 1 : null;
      this.status = 'ready';
    } catch (error) {
      if (!current()) return;
      this.status = 'error';
      this.error = error instanceof Error ? error.message : String(error);
    }
  }

  reset(): void {
    this.#abort?.abort();
    this.#abort = null;
    this.status = 'idle';
    this.error = null;
    this.entries = [];
    this.#next = null;
    this.group = null;
    this.groups = null;
    this.recall = false;
  }
}

/**
 * Loads the list while `isReadable()` holds and cancels it when the host
 * component is destroyed or the radio stops being listable. Call in a component.
 */
export function trackMemoryList(controller: MemoryListController, isReadable: () => boolean): void {
  $effect(() => {
    if (isReadable()) void untrack(() => controller.load());
    return () => untrack(() => controller.reset());
  });
}
