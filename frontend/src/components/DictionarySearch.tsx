"use client";

import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";

import { type DictionaryEntry, entryName } from "@/lib/api";

type Props<T extends DictionaryEntry> = {
  search: (q: string) => Promise<T[]>;
  onChoose: (entry: T) => void;
  onCancel: () => void;
};

export function DictionarySearch<T extends DictionaryEntry>({ search, onChoose, onCancel }: Props<T>) {
  const t = useTranslations("line");
  const locale = useLocale();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<T[] | null>(null);

  async function update(value: string) {
    setQuery(value);
    if (!value.trim()) {
      setResults(null);
      return;
    }
    try {
      const found = await search(value);
      setResults(found);
    } catch {
      setResults([]);
    }
  }

  return (
    <div>
      <input
        type="search"
        aria-label={t("search")}
        placeholder={t("search")}
        value={query}
        onChange={(event) => update(event.target.value)}
      />
      <button type="button" onClick={onCancel}>
        {t("cancel")}
      </button>
      {results !== null && results.length === 0 && <p>{t("noResults")}</p>}
      <ul>
        {results?.map((entry) => {
          const name = entryName(entry, locale);
          return (
            <li key={entry.id}>
              <button
                type="button"
                aria-label={`${t("choose")} ${name}`}
                onClick={() => onChoose(entry)}
              >
                {name}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
