"use client";

import { useLocale, useTranslations } from "next-intl";
import { useRef, useState } from "react";

import { type DictionaryEntry, entryName } from "@/lib/api";

type Props<T extends DictionaryEntry> = {
  search: (q: string) => Promise<T[]>;
  onChoose: (entry: T) => void;
  onCancel: () => void;
  /** When given, the typed text can be added as a new entry, under this label. */
  onAddUnlisted?: (name: string) => void;
  unlistedLabel?: (name: string) => string;
};

export function DictionarySearch<T extends DictionaryEntry>({
  search,
  onChoose,
  onCancel,
  onAddUnlisted,
  unlistedLabel,
}: Props<T>) {
  const t = useTranslations("line");
  const locale = useLocale();
  const [query, setQuery] = useState("");
  // Results remember their query, so a slow older response never shows for newer text.
  const [found, setFound] = useState<{ query: string; entries: T[] } | null>(null);
  const latest = useRef("");

  async function update(value: string) {
    setQuery(value);
    latest.current = value;
    if (!value.trim()) {
      setFound(null);
      return;
    }
    let entries: T[];
    try {
      entries = await search(value);
    } catch {
      entries = [];
    }
    if (latest.current === value) {
      setFound({ query: value, entries });
    }
  }

  const results = found?.query === query ? found.entries : null;
  // The same rule as dictionary matching: exact, ignoring case and outer spaces.
  const term = query.trim().toLowerCase();
  const hasExactMatch = (results ?? []).some((entry) =>
    [entry.name_zh, entry.name_en, ...entry.aliases_zh, ...entry.aliases_en].some(
      (name) => name?.trim().toLowerCase() === term,
    ),
  );

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
      {onAddUnlisted && unlistedLabel && results !== null && term && !hasExactMatch && (
        <button type="button" onClick={() => onAddUnlisted(query.trim())}>
          {unlistedLabel(query.trim())}
        </button>
      )}
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
