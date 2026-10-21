import { untrack } from 'svelte';
import { fetchMemoryPage } from '$lib/transport/http-client';
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
  #abort: AbortController | null = null;
  readonly #fetchPage: FetchPage;

  constructor(fetchPage: FetchPage = fetchMemoryPage) {
    this.#fetchPage = fetchPage;
  }

  async load(group?: number): Promise<void> {
    this.#abort?.abort();
    const abort = new AbortController();
    this.#abort = abort;
    const current = () => this.#abort === abort && !abort.signal.aborted;
    this.status = 'loading';
    this.error = null;
    this.entries = [];
    this.group = group ?? null; // the selection survives a failed first page; omitted = the default
    try {
      let page = await this.#fetchPage({ group, count: PAGE_SIZE }, abort.signal);
      while (current()) {
        this.group = page.group;
        this.groups = page.groups;
        this.recall = page.recall;
        this.entries = [...this.entries, ...page.entries];
        const next = (page.entries.at(-1)?.channel ?? page.channels.last) + 1;
        if (next > page.channels.last) {
          this.status = 'ready';
          return;
        }
        page = await this.#fetchPage({ group: page.group, start: next, count: PAGE_SIZE }, abort.signal);
      }
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
