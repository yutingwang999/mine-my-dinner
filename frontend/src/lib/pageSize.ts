import { useSyncExternalStore } from "react";

export const PAGE_SIZES = [10, 20, 50, 100] as const;
export const DEFAULT_PAGE_SIZE = 20;

function isPageSize(value: number): boolean {
  return (PAGE_SIZES as readonly number[]).includes(value);
}

const CHANGED = "pageSizeChanged";

function subscribe(listener: () => void) {
  window.addEventListener(CHANGED, listener);
  window.addEventListener("storage", listener);
  return () => {
    window.removeEventListener(CHANGED, listener);
    window.removeEventListener("storage", listener);
  };
}

function readSize(key: string): number {
  try {
    const stored = Number(window.localStorage.getItem(key));
    if (isPageSize(stored)) return stored;
  } catch {
    // Storage is unavailable; the default applies.
  }
  return DEFAULT_PAGE_SIZE;
}

/**
 * The rows per page for one list, remembered in localStorage under `pageSize:<list>`.
 * Storage may be missing or blocked, so every access is guarded and the default applies.
 * The server render and hydration use the default; the stored value follows.
 */
export function usePageSize(list: string): [number, (size: number) => void] {
  const key = `pageSize:${list}`;
  const size = useSyncExternalStore(
    subscribe,
    () => readSize(key),
    () => DEFAULT_PAGE_SIZE,
  );

  function choose(next: number) {
    try {
      window.localStorage.setItem(key, String(next));
    } catch {
      // Without storage the choice cannot be kept, so the size stays as it was.
    }
    window.dispatchEvent(new Event(CHANGED));
  }

  return [size, choose];
}

/** One page of items. A page past the end shows the last page. */
export function paginate<T>(items: T[], page: number, pageSize: number) {
  const pages = Math.max(1, Math.ceil(items.length / pageSize));
  const current = Math.min(page, pages);
  return { current, pages, visible: items.slice((current - 1) * pageSize, current * pageSize) };
}
