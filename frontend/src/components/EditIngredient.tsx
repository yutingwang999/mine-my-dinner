"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

import {
  type Allergen,
  ALLERGENS,
  api,
  CATEGORIES,
  type Category,
  type IngredientEntry,
  STOCK_MODES,
  type StockMode,
} from "@/lib/api";
import { useErrorText } from "@/lib/errors";

type AllergenState = "unknown" | "none" | "contains";

function splitAliases(text: string): string[] {
  return text
    .split(/[,，]/)
    .map((alias) => alias.trim())
    .filter(Boolean);
}

/**
 * Edits a user-created entry. Allergens stay as they are unless the user picks
 * another state, so saving a rename never confirms allergens by accident.
 */
export function EditIngredient({
  entry,
  onSaved,
  onCancel,
}: {
  entry: IngredientEntry;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const t = useTranslations("entry");
  const tDictionary = useTranslations("dictionary");
  const tCommon = useTranslations("common");
  const tCategory = useTranslations("category");
  const tStock = useTranslations("stockMode");
  const tAllergen = useTranslations("allergen");
  const errorText = useErrorText();
  const [nameZh, setNameZh] = useState(entry.name_zh ?? "");
  const [nameEn, setNameEn] = useState(entry.name_en ?? "");
  const [aliasesZh, setAliasesZh] = useState(entry.aliases_zh.join(", "));
  const [aliasesEn, setAliasesEn] = useState(entry.aliases_en.join(", "));
  const [category, setCategory] = useState<Category>(entry.category);
  const [stockMode, setStockMode] = useState<StockMode>(entry.stock_mode);
  const [state, setState] = useState<AllergenState>(
    entry.allergens === null ? "unknown" : entry.allergens.length === 0 ? "none" : "contains",
  );
  const [allergens, setAllergens] = useState<Allergen[]>(entry.allergens ?? []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api.updateIngredient(entry.id, {
        name_zh: nameZh.trim() || null,
        name_en: nameEn.trim() || null,
        aliases_zh: splitAliases(aliasesZh),
        aliases_en: splitAliases(aliasesEn),
        category,
        stock_mode: stockMode,
        allergens: state === "unknown" ? null : state === "none" ? [] : allergens,
      });
      onSaved();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  const states: [AllergenState, string][] = [
    ["unknown", tDictionary("allergensUnknownOption")],
    ["none", tDictionary("allergensNoneOption")],
    ["contains", tDictionary("allergensContainsOption")],
  ];

  return (
    <div className="line-panel">
      <label className="field">
        {t("nameZh")}
        <input value={nameZh} onChange={(event) => setNameZh(event.target.value)} />
      </label>
      <label className="field">
        {t("nameEn")}
        <input value={nameEn} onChange={(event) => setNameEn(event.target.value)} />
      </label>
      <label className="field">
        {t("aliasesZh")}
        <input value={aliasesZh} onChange={(event) => setAliasesZh(event.target.value)} />
      </label>
      <label className="field">
        {t("aliasesEn")}
        <input value={aliasesEn} onChange={(event) => setAliasesEn(event.target.value)} />
      </label>
      <label className="field">
        {t("category")}
        <select value={category} onChange={(event) => setCategory(event.target.value as Category)}>
          {CATEGORIES.map((value) => (
            <option key={value} value={value}>
              {tCategory(value)}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        {t("stockMode")}
        <select
          value={stockMode}
          onChange={(event) => setStockMode(event.target.value as StockMode)}
        >
          {STOCK_MODES.map((value) => (
            <option key={value} value={value}>
              {tStock(value)}
            </option>
          ))}
        </select>
      </label>
      <fieldset className="allergen-picker">
        <legend>{tDictionary("allergensLabel")}</legend>
        <div>
          {states.map(([value, label]) => (
            <label key={value} style={{ marginRight: "0.75rem" }}>
              <input
                type="radio"
                name={`allergens-${entry.id}`}
                checked={state === value}
                onChange={() => setState(value)}
              />
              {label}
            </label>
          ))}
        </div>
        {state === "contains" &&
          ALLERGENS.map((allergen) => (
            <label key={allergen}>
              <input
                type="checkbox"
                checked={allergens.includes(allergen)}
                onChange={() =>
                  setAllergens((current) =>
                    current.includes(allergen)
                      ? current.filter((item) => item !== allergen)
                      : ALLERGENS.filter((item) => item === allergen || current.includes(item)),
                  )
                }
              />
              {tAllergen(allergen)}
            </label>
          ))}
      </fieldset>
      <div>
        <button
          type="button"
          className="btn-primary"
          disabled={busy || (state === "contains" && allergens.length === 0)}
          onClick={save}
        >
          {tCommon("save")}
        </button>
        <button type="button" onClick={onCancel}>
          {tCommon("cancel")}
        </button>
      </div>
      {error !== null && <p role="alert">{errorText(error)}</p>}
    </div>
  );
}
