"use client";

import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";

import { AllergenPicker } from "@/components/AllergenPicker";
import { DictionarySearch } from "@/components/DictionarySearch";
import { EntryTypeFields, useEntryType } from "@/components/EntryTypeFields";
import { type Allergen, api, entryName, type IngredientEntry } from "@/lib/api";
import { useErrorText } from "@/lib/errors";

/** A name with no dictionary entry yet, or an entry whose allergens are unknown. */
export type Target = { name: string } | { ingredient_id: number };

/** Shown when a link was asked to be remembered but the name belongs to another entry. */
export type LinkNotice = { name: string; entry: string };

/** What linking a name returned: the entry, and how many unlinked lines it now covers. */
export type LinkResult = IngredientEntry & { linked_lines: number; remembered: boolean };

/** Saving needs a confirmed value: a list, or an explicit "contains none". */
export function ConfirmPanel({
  name,
  target,
  onCancel,
  onResolved,
  switchTo,
}: {
  name: string;
  target: Target;
  onCancel: () => void;
  onResolved: () => void;
  /** A link at the left of the action row to the other way of resolving the name. */
  switchTo?: ReactNode;
}) {
  const t = useTranslations("dictionary");
  const tCommon = useTranslations("common");
  const errorText = useErrorText();
  const [allergens, setAllergens] = useState<Allergen[] | null>(null);
  const type = useEntryType("other");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  // Only a name that has no entry yet becomes a new entry, so only it has a category.
  const isNewEntry = "name" in target;

  async function save() {
    if (allergens === null) return;
    setBusy(true);
    setError(null);
    try {
      await api.resolveAllergens(
        target,
        allergens,
        isNewEntry ? { category: type.category, stock_mode: type.stockMode } : undefined,
      );
      onResolved();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="dish-panel form-panel">
      {isNewEntry && <EntryTypeFields type={type} fieldClass="panel-row" />}
      <AllergenPicker name={name} onChange={setAllergens} onSuggestion={type.applySuggestion} />
      {error !== null && (
        <p role="alert" className="notice notice-warning">
          {errorText(error)}
        </p>
      )}
      <div className="panel-actions">
        {switchTo && <span className="panel-switch">{switchTo}</span>}
        <button type="button" disabled={busy} onClick={onCancel}>
          {tCommon("cancel")}
        </button>
        <button type="button" className="btn-primary" disabled={allergens === null || busy} onClick={save}>
          {t("saveAllergens")}
        </button>
      </div>
    </div>
  );
}

/** Links every line with an unmatched name to an entry the user picks. */
export function LinkPanel({
  name,
  onCancel,
  onResolved,
  switchTo,
}: {
  name: string;
  onCancel: () => void;
  /** A link at the left of the action row to the other way of resolving the name. */
  switchTo?: ReactNode;
  /**
   * Gets the link notice to show when the name could not be remembered, else null, and the
   * link result (the entry, with how many lines were linked).
   */
  onResolved: (notice: LinkNotice | null, linked: LinkResult) => void;
}) {
  const t = useTranslations("dictionary");
  const tReview = useTranslations("review");
  const tCommon = useTranslations("common");
  const locale = useLocale();
  const errorText = useErrorText();
  const [chosen, setChosen] = useState<IngredientEntry | null>(null);
  const [remember, setRemember] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function confirm() {
    if (chosen === null) return;
    setBusy(true);
    setError(null);
    try {
      const linked = await api.linkUnknownName(name, chosen.id, remember);
      // Asked to remember, but the name already belongs to another entry.
      onResolved(remember && !linked.remembered ? { name, entry: entryName(chosen, locale) } : null, linked);
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="dish-panel form-panel">
      <DictionarySearch search={api.searchIngredients} onChoose={setChosen} selectedId={chosen?.id ?? null} />
      {chosen !== null && <p role="status">{t("linkChosen", { entry: entryName(chosen, locale) })}</p>}
      <label className="mini-field inline">
        <input type="checkbox" checked={remember} onChange={(event) => setRemember(event.target.checked)} />
        {tReview("rememberLink")}
      </label>
      {error !== null && (
        <p role="alert" className="notice notice-warning">
          {errorText(error)}
        </p>
      )}
      <div className="panel-actions">
        {switchTo && <span className="panel-switch">{switchTo}</span>}
        <button type="button" disabled={busy} onClick={onCancel}>
          {tCommon("cancel")}
        </button>
        <button type="button" className="btn-primary" disabled={chosen === null || busy} onClick={confirm}>
          {t("confirmLink")}
        </button>
      </div>
    </div>
  );
}
