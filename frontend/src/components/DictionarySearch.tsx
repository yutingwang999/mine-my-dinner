"use client";

import { useLocale, useTranslations } from "next-intl";
import { useRef, useState } from "react";

import { type DictionaryEntry, entryName } from "@/lib/api";

/** The same rule as dictionary matching: a name or alias equal to the text,
 * ignoring case and outer spaces. */
export function namesEntry(entry: DictionaryEntry, text: string): boolean {
  const term = text.trim().toLowerCase();
  return [entry.name_zh, entry.name_en, ...entry.aliases_zh, ...entry.aliases_en].some(
    (name) => !!term && name?.trim().toLowerCase() === term,
  );
}

type Props<T extends DictionaryEntry> = {
  search: (q: string) => Promise<T[]>;
  /** Also gets the typed text, so a caller can keep the user's own wording. */
  onChoose: (entry: T, typed: string) => void;
  /** When given, the search shows its own cancel button; a panel with its own cancel omits it. */
  onCancel?: () => void;
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
  const term = query.trim();
  const hasExactMatch = (results ?? []).some((entry) => namesEntry(entry, query));

  return (
    <div className="dictionary-search">
      <div className="search-bar">
        <input
          type="search"
          aria-label={t("search")}
          placeholder={t("search")}
          value={query}
          onChange={(event) => update(event.target.value)}
        />
        {onCancel && (
          <button type="button" onClick={onCancel}>
            {t("cancel")}
          </button>
        )}
      </div>
      {results !== null && results.length === 0 && <p>{t("noResults")}</p>}
      {onAddUnlisted && unlistedLabel && results !== null && term && !hasExactMatch && (
        <button type="button" onClick={() => onAddUnlisted(query.trim())}>
          {unlistedLabel(query.trim())}
        </button>
      )}
      <ul className="search-results">
        {results?.map((entry) => {
          const name = entryName(entry, locale);
          return (
            <li key={entry.id}>
              <button
                type="button"
                className="chip"
                aria-label={`${t("choose")} ${name}`}
                onClick={() => onChoose(entry, query)}
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
