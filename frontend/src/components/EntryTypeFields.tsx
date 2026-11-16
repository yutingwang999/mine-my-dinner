"use client";

import { useTranslations } from "next-intl";
import { useRef, useState } from "react";

import {
  CATEGORIES,
  type Category,
  type EntrySuggestion,
  STOCK_MODES,
  type StockMode,
} from "@/lib/api";

/**
 * The category and stock mode of a new entry. They start at a default, are replaced by
 * the model's suggestion when one arrives, and stay editable; a value the user already
 * changed is never overwritten by a late suggestion.
 */
export function useEntryType(defaultCategory: Category) {
  const [category, setCategoryValue] = useState<Category>(defaultCategory);
  const [stockMode, setStockModeValue] = useState<StockMode>("level");
  const [suggestion, setSuggestion] = useState<Pick<EntrySuggestion, "category" | "stock_mode"> | null>(null);
  const touched = useRef({ category: false, stockMode: false });

  function applySuggestion({ category: suggestedCategory, stock_mode: suggestedStock }: EntrySuggestion) {
    setSuggestion({ category: suggestedCategory, stock_mode: suggestedStock });
    if (suggestedCategory !== null && !touched.current.category) setCategoryValue(suggestedCategory);
    if (suggestedStock !== null && !touched.current.stockMode) setStockModeValue(suggestedStock);
  }

  // True while a suggested value is still the one chosen.
  const suggested =
    suggestion !== null &&
    ((suggestion.category !== null && suggestion.category === category) ||
      (suggestion.stock_mode !== null && suggestion.stock_mode === stockMode));

  return {
    category,
    stockMode,
    suggested,
    applySuggestion,
    setCategory(value: Category) {
      touched.current.category = true;
      setCategoryValue(value);
    },
    setStockMode(value: StockMode) {
      touched.current.stockMode = true;
      setStockModeValue(value);
    },
  };
}

type Props = {
  type: ReturnType<typeof useEntryType>;
  /** The class of each label, which decides the layout of the field. */
  fieldClass: string;
};

export function EntryTypeFields({ type, fieldClass }: Props) {
  const t = useTranslations("entry");
  const tDictionary = useTranslations("dictionary");
  const tCategory = useTranslations("category");
  const tStock = useTranslations("stockMode");

  return (
    <>
      <label className={fieldClass}>
        {t("category")}
        <select value={type.category} onChange={(event) => type.setCategory(event.target.value as Category)}>
          {CATEGORIES.map((value) => (
            <option key={value} value={value}>
              {tCategory(value)}
            </option>
          ))}
        </select>
      </label>
      <label className={fieldClass}>
        {t("stockMode")}
        <select
          value={type.stockMode}
          onChange={(event) => type.setStockMode(event.target.value as StockMode)}
        >
          {STOCK_MODES.map((value) => (
            <option key={value} value={value}>
              {tStock(value)}
            </option>
          ))}
        </select>
      </label>
      {type.suggested && <p className="hint">{tDictionary("suggestedHint")}</p>}
    </>
  );
}
