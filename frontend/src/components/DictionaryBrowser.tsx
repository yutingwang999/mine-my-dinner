"use client";

import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";

import { Pager } from "@/components/Pager";
import { api, CATEGORIES, type DictionaryEntry, entryName, type IngredientEntry } from "@/lib/api";
import { useLoad } from "@/lib/useLoad";

const PAGE_SIZE = 50;

/** One page of items. A page past the end shows the last page. */
function paginate<T>(items: T[], page: number) {
  const pages = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
  const current = Math.min(page, pages);
  return { current, pages, visible: items.slice((current - 1) * PAGE_SIZE, current * PAGE_SIZE) };
}

function bothNames(entry: DictionaryEntry): string {
  return [entry.name_zh, entry.name_en].filter(Boolean).join(" / ");
}

function aliases(entry: DictionaryEntry): string[] {
  return [...entry.aliases_zh, ...entry.aliases_en];
}

function matches(entry: DictionaryEntry, text: string): boolean {
  const term = text.trim().toLowerCase();
  if (!term) return true;
  return [entry.name_zh, entry.name_en, ...aliases(entry)].some((name) =>
    name?.toLowerCase().includes(term),
  );
}

/**
 * Every dictionary entry, built-in and the user's own, so the user can see which
 * names and aliases will match and what each entry records. Read-only.
 */
export function DictionaryBrowser({ refreshKey = 0 }: { refreshKey?: number }) {
  const t = useTranslations("dictionary");
  const tCategory = useTranslations("category");
  const tStock = useTranslations("stockMode");
  const tAllergen = useTranslations("allergen");
  const tEntry = useTranslations("entry");
  const locale = useLocale();
  const [text, setText] = useState("");
  const [category, setCategory] = useState("");
  const [page, setPage] = useState(1);
  const [cookwarePage, setCookwarePage] = useState(1);
  const ingredients = useLoad(api.listAllIngredients, [refreshKey]);
  const cookware = useLoad(api.listAllCookware, [refreshKey]);

  const all = ingredients.data ?? [];
  const byId = new Map(all.map((entry) => [entry.id, entry]));
  const shown = all.filter(
    (entry) => matches(entry, text) && (!category || entry.category === category),
  );
  const { current, pages, visible } = paginate(shown, page);
  const shownCookware = (cookware.data ?? []).filter((entry) => matches(entry, text));
  const tools = paginate(shownCookware, cookwarePage);

  function allergenText(entry: IngredientEntry): string {
    if (entry.allergens === null) return t("allergensUnknown");
    if (entry.allergens.length === 0) return t("allergensNone");
    return entry.allergens.map((allergen) => tAllergen(allergen)).join(", ");
  }

  function sourceBadge(entry: DictionaryEntry) {
    return <small className="badge">{entry.source === "seed" ? t("builtIn") : t("mine")}</small>;
  }

  return (
    <section className="dictionary-browser">
      <h2>{t("allIngredients")}</h2>
      <p>{t("browseHint")}</p>
      <div className="browse-filters">
        <input
          type="search"
          aria-label={t("filterText")}
          placeholder={t("filterText")}
          value={text}
          onChange={(event) => {
            setText(event.target.value);
            setPage(1);
            setCookwarePage(1);
          }}
        />
        <label>
          {tEntry("category")}
          <select
            value={category}
            onChange={(event) => {
              setCategory(event.target.value);
              setPage(1);
            }}
          >
            <option value="">{t("allCategories")}</option>
            {CATEGORIES.map((value) => (
              <option key={value} value={value}>
                {tCategory(value)}
              </option>
            ))}
          </select>
        </label>
        {ingredients.data !== null && (
          <span>{t("shownCount", { shown: shown.length, total: all.length })}</span>
        )}
      </div>
      <Pager page={current} pages={pages} onPage={setPage} />
      <ul className="browse-list">
        {visible.map((entry) => {
          const parent = entry.parent_id ? byId.get(entry.parent_id) : undefined;
          return (
            <li key={entry.id} data-testid={`all-ingredient-${entry.id}`}>
              <strong>{bothNames(entry)}</strong> {sourceBadge(entry)}
              <div className="browse-details">
                <span>{tCategory(entry.category)}</span>
                <span>{tStock(entry.stock_mode)}</span>
                <span>{allergenText(entry)}</span>
                {parent && <span>{t("parentName", { name: entryName(parent, locale) })}</span>}
                {aliases(entry).length > 0 && (
                  <span>{t("aliasesList", { list: aliases(entry).join(", ") })}</span>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      <Pager page={current} pages={pages} onPage={setPage} />

      <h2>{t("allCookware")}</h2>
      <Pager page={tools.current} pages={tools.pages} onPage={setCookwarePage} />
      <ul className="browse-list">
        {tools.visible.map((entry) => (
          <li key={entry.id} data-testid={`all-cookware-${entry.id}`}>
            <strong>{bothNames(entry)}</strong> {sourceBadge(entry)}
            {aliases(entry).length > 0 && (
              <div className="browse-details">
                <span>{t("aliasesList", { list: aliases(entry).join(", ") })}</span>
              </div>
            )}
          </li>
        ))}
      </ul>
      <Pager page={tools.current} pages={tools.pages} onPage={setCookwarePage} />
    </section>
  );
}
