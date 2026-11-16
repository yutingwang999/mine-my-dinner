"use client";

import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";

import type { DictionaryEntry, IngredientEntry } from "@/lib/api";

/** Every name an entry answers to: its names, aliases, and the user's own aliases. */
export function entryNames(entry: DictionaryEntry): (string | null)[] {
  const own = entry as Partial<IngredientEntry>;
  return [
    entry.name_zh,
    entry.name_en,
    ...entry.aliases_zh,
    ...entry.aliases_en,
    ...(own.user_aliases_zh ?? []),
    ...(own.user_aliases_en ?? []),
  ];
}

/**
 * A live "find by name" box for one list. `filtered` follows the text (case-insensitive,
 * any of `names`); `resetKey` changes with the text, so a keyed PagedList starts again
 * on page 1; `empty` is the "nothing matches" note, or null.
 */
export function useListSearch<T>(items: T[], names: (item: T) => (string | null | undefined)[]) {
  const t = useTranslations("common");
  const [query, setQuery] = useState("");
  const term = query.trim().toLowerCase();
  const filtered = term
    ? items.filter((item) => names(item).some((name) => name?.toLowerCase().includes(term)))
    : items;
  const box: ReactNode = (
    <div className="browse-filters">
      <input
        type="search"
        aria-label={t("findByName")}
        placeholder={t("findByName")}
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
    </div>
  );
  const empty: ReactNode =
    term && items.length > 0 && filtered.length === 0 ? <p className="hint">{t("nothingMatches")}</p> : null;
  return { filtered, box, empty, resetKey: term };
}
