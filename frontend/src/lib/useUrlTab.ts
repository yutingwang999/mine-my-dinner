"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useState, useSyncExternalStore } from "react";

import { useRouter } from "@/i18n/navigation";

/**
 * The chosen tab, kept in the address as `?tab=`. `chosen` is null until the user picks a tab or
 * the address names one, so the page can apply its own default. An address that changes from
 * outside (back/forward, a nav link) wins over an earlier click. `hashTabs` maps an old
 * `#fragment` link to a tab; it applies only when the address names no tab.
 */
export function useUrlTab<T extends string>(
  tabs: readonly T[],
  path: string,
  hashTabs: Record<string, T> = {},
) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const param = searchParams.get("tab");
  const urlTab = (tabs as readonly string[]).includes(param ?? "") ? (param as T) : null;
  // `seen` is the address's tab when the choice was made.
  const [choice, setChoice] = useState<{ tab: T | null; seen: T | null }>({ tab: urlTab, seen: urlTab });
  if (choice.seen !== urlTab) {
    setChoice({ tab: urlTab, seen: urlTab });
  }
  // The fragment is unknown while rendering on the server, so it is read as an external store.
  const hash = useSyncExternalStore(
    (listener) => {
      window.addEventListener("hashchange", listener);
      return () => window.removeEventListener("hashchange", listener);
    },
    () => window.location.hash,
    () => "",
  );
  const fromHash = hashTabs[hash.replace(/^#/, "")] ?? null;
  const chosen = (choice.seen === urlTab ? choice.tab : urlTab) ?? fromHash;

  function choose(tab: T) {
    setChoice({ tab, seen: urlTab });
    router.replace(`${path}?tab=${tab}`);
  }

  // Put the tab into the address too, so a reload or back keeps it.
  const needsAddress = urlTab === null && fromHash !== null;
  useEffect(() => {
    if (needsAddress) router.replace(`${path}?tab=${fromHash}`);
    // The router object changes every render; only the need for the address matters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [needsAddress]);

  return { chosen, choose };
}
