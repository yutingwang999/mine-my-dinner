"use client";

import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";

import { useErrorText } from "@/lib/errors";

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

function personalAliases(entry: DictionaryEntry): string[] {
  const own = entry as Partial<IngredientEntry>;
  return [...(own.user_aliases_zh ?? []), ...(own.user_aliases_en ?? [])];
}

function aliases(entry: DictionaryEntry): string[] {
  return [...entry.aliases_zh, ...entry.aliases_en, ...personalAliases(entry)];
}

function matches(entry: DictionaryEntry, text: string): boolean {
  const term = text.trim().toLowerCase();
  if (!term) return true;
  return [entry.name_zh, entry.name_en, ...aliases(entry)].some((name) =>
    name?.toLowerCase().includes(term),
  );
}

function splitAliases(text: string): string[] {
  return text
    .split(/[,，]/)
    .map((part) => part.trim())
    .filter(Boolean);
}

/** The toggle in an entry's title row; it opens the panel with the user's own wordings. */
function PersonalAliasToggle({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  const t = useTranslations("dictionary");
  return (
    <span className="dish-toggles">
      <button type="button" aria-expanded={open} onClick={onToggle}>
        {t("personalAliases")}
        <span aria-hidden="true"> {open ? "▴" : "▾"}</span>
      </button>
    </span>
  );
}

/** A framed form for the user's own wordings of a built-in ingredient. */
function PersonalAliasPanel({
  entry,
  onSaved,
  onClose,
}: {
  entry: IngredientEntry;
  onSaved: (entry: IngredientEntry) => void;
  onClose: () => void;
}) {
  const t = useTranslations("dictionary");
  const errorText = useErrorText();
  const [zh, setZh] = useState(entry.user_aliases_zh.join(", "));
  const [en, setEn] = useState(entry.user_aliases_en.join(", "));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      onSaved(await api.setPersonalAliases(entry.id, splitAliases(zh), splitAliases(en)));
      onClose();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="dish-panel form-panel">
      <p className="hint">{t("personalAliasesHint")}</p>
      <label className="panel-row">
        {t("personalAliasesZh")}
        <input value={zh} onChange={(event) => setZh(event.target.value)} />
      </label>
      <label className="panel-row">
        {t("personalAliasesEn")}
        <input value={en} onChange={(event) => setEn(event.target.value)} />
      </label>
      {error !== null && (
        <p role="alert" className="notice notice-warning">
          {errorText(error)}
        </p>
      )}
      <div className="panel-actions">
        <button type="button" disabled={busy} onClick={onClose}>
          {t("cancelPersonalAliases")}
        </button>
        <button type="button" disabled={busy} onClick={save}>
          {t("savePersonalAliases")}
        </button>
      </div>
    </div>
  );
}

/**
 * Every dictionary entry, built-in and the user's own, so the user can see which
 * names and aliases will match and what each entry records. Entries are read-only,
 * except that a built-in ingredient can take the user's own aliases.
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
  // The built-in entry whose personal aliases are open; one at a time.
  const [editingId, setEditingId] = useState<number | null>(null);
  // Entries saved with personal aliases, shown in place of the loaded copy.
  const [saved, setSaved] = useState<Map<number, IngredientEntry>>(new Map());
  const ingredients = useLoad(api.listAllIngredients, [refreshKey]);
  const cookware = useLoad(api.listAllCookware, [refreshKey]);

  const all = (ingredients.data ?? []).map((entry) => saved.get(entry.id) ?? entry);
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
    <>
      <section className="card">
        <h2>{t("allIngredients")}</h2>
        <p className="hint">{t("browseHint")}</p>
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
                <div className="dish-head">
                  <span>
                    <strong>{bothNames(entry)}</strong> {sourceBadge(entry)}
                  </span>
                  {entry.source === "seed" && (
                    <PersonalAliasToggle
                      open={editingId === entry.id}
                      onToggle={() => setEditingId(editingId === entry.id ? null : entry.id)}
                    />
                  )}
                </div>
                <div className="browse-details">
                  <span>{tCategory(entry.category)}</span>
                  <span>{tStock(entry.stock_mode)}</span>
                  <span>{allergenText(entry)}</span>
                  {parent && <span>{t("parentName", { name: entryName(parent, locale) })}</span>}
                  {[...entry.aliases_zh, ...entry.aliases_en].length > 0 && (
                    <span>
                      {t("aliasesList", { list: [...entry.aliases_zh, ...entry.aliases_en].join(", ") })}
                    </span>
                  )}
                  {personalAliases(entry).length > 0 && (
                    <span>{t("personalAliasesList", { list: personalAliases(entry).join(", ") })}</span>
                  )}
                </div>
                {entry.source === "seed" && editingId === entry.id && (
                  <PersonalAliasPanel
                    entry={entry}
                    onSaved={(updated) => setSaved((previous) => new Map(previous).set(updated.id, updated))}
                    onClose={() => setEditingId(null)}
                  />
                )}
              </li>
            );
          })}
        </ul>
        <Pager page={current} pages={pages} onPage={setPage} />
      </section>

      <section className="card">
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
    </>
  );
}
