"use client";

import { useTranslations } from "next-intl";
import { useId, useRef, useState } from "react";

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

  // A field shows the suggested badge while it still holds the suggested value.
  const categorySuggested = suggestion?.category != null && suggestion.category === category;
  const stockSuggested = suggestion?.stock_mode != null && suggestion.stock_mode === stockMode;

  return {
    category,
    stockMode,
    categorySuggested,
    stockSuggested,
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
  const id = useId();

  // The badge is outside the select's name (aria-label) and read as its description.
  function badge(shown: boolean, badgeId: string) {
    return (
      shown && (
        <span id={badgeId} className="suggested-badge">
          {tDictionary("suggestedBadge")}
        </span>
      )
    );
  }

  const categoryBadge = `${id}-category`;
  const stockBadge = `${id}-stock`;

  return (
    <>
      {(type.categorySuggested || type.stockSuggested) && (
        <p className="hint">{tDictionary("suggestedNote")}</p>
      )}
      <label className={fieldClass}>
        <span className="field-label">
          {t("category")}
          {badge(type.categorySuggested, categoryBadge)}
        </span>
        <select
          aria-label={t("category")}
          aria-describedby={type.categorySuggested ? categoryBadge : undefined}
          value={type.category}
          onChange={(event) => type.setCategory(event.target.value as Category)}
        >
          {CATEGORIES.map((value) => (
            <option key={value} value={value}>
              {tCategory(value)}
            </option>
          ))}
        </select>
      </label>
      <label className={fieldClass}>
        <span className="field-label">
          {t("stockMode")}
          {badge(type.stockSuggested, stockBadge)}
        </span>
        <select
          aria-label={t("stockMode")}
          aria-describedby={type.stockSuggested ? stockBadge : undefined}
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
    </>
  );
}
