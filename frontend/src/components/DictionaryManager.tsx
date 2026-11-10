"use client";

import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";

import { AllergenPicker } from "@/components/AllergenPicker";
import { ConfirmDelete } from "@/components/ConfirmDelete";
import { DictionaryBrowser } from "@/components/DictionaryBrowser";
import { EditIngredient } from "@/components/EditIngredient";
import { type Allergen, api, entryName, type IngredientEntry } from "@/lib/api";
import { useErrorText } from "@/lib/errors";
import { useLoad } from "@/lib/useLoad";

/** Saving needs a confirmed value: a list, or an explicit "contains none". */
function ResolveRow({
  testId,
  label,
  name,
  dishCount,
  target,
  onResolved,
}: {
  testId: string;
  label: string;
  name: string;
  dishCount: number;
  target: { name: string } | { ingredient_id: number };
  onResolved: () => void;
}) {
  const t = useTranslations("dictionary");
  const errorText = useErrorText();
  const [open, setOpen] = useState(false);
  const [allergens, setAllergens] = useState<Allergen[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function save() {
    if (allergens === null) return;
    setBusy(true);
    setError(null);
    try {
      await api.resolveAllergens(target, allergens);
      onResolved();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <li data-testid={testId}>
      <span className="row-main">
        <strong>{label}</strong>
        <span className="hint">{t("dishCount", { count: dishCount })}</span>
      </span>
      {!open && (
        <button type="button" onClick={() => setOpen(true)}>
          {t("resolve")}
        </button>
      )}
      {open && (
        <div className="line-panel">
          <AllergenPicker name={name} onChange={setAllergens} />
          <button type="button" disabled={allergens === null || busy} onClick={save}>
            {t("saveAllergens")}
          </button>
          {error !== null && (
            <p role="alert" className="notice notice-warning">
              {errorText(error)}
            </p>
          )}
        </div>
      )}
    </li>
  );
}

function allergenSummary(entry: IngredientEntry, t: (key: string) => string, tAllergen: (key: string) => string) {
  if (entry.allergens === null) return t("allergensUnknown");
  if (entry.allergens.length === 0) return t("allergensNone");
  return entry.allergens.map((allergen) => tAllergen(allergen)).join(", ");
}

export function DictionaryManager() {
  const t = useTranslations("dictionary");
  const tAllergen = useTranslations("allergen");
  const tCommon = useTranslations("common");
  const tCategory = useTranslations("category");
  const locale = useLocale();
  const [editingId, setEditingId] = useState<number | null>(null);
  // Bumped after any change, so the full list below shows it too.
  const [refreshKey, setRefreshKey] = useState(0);
  const unresolved = useLoad(api.listUnresolved, []);
  const ingredients = useLoad(api.listUserIngredients, []);
  const cookware = useLoad(api.listUserCookware, []);

  function reloadAll() {
    unresolved.reload();
    ingredients.reload();
    setRefreshKey((key) => key + 1);
  }

  const nothingUnresolved =
    unresolved.data !== null &&
    unresolved.data.entries.length === 0 &&
    unresolved.data.unmatched.length === 0;

  return (
    <div>
      <section className="card">
        <h2>{t("unresolved")}</h2>
        <p className="hint">{t("unresolvedHint")}</p>
        {nothingUnresolved && <p>{t("allResolved")}</p>}
        <ul className="row-list">
          {unresolved.data?.entries.map((entry) => (
            <ResolveRow
              key={`entry-${entry.id}`}
              testId={`unresolved-entry-${entry.id}`}
              label={entry.name}
              name={entry.name}
              dishCount={entry.dish_count}
              target={{ ingredient_id: entry.id }}
              onResolved={reloadAll}
            />
          ))}
          {unresolved.data?.unmatched.map((item) => (
            <ResolveRow
              key={`name-${item.name}`}
              testId={`unresolved-name-${item.name}`}
              label={t("unmatchedName", { name: item.name })}
              name={item.name}
              dishCount={item.dish_count}
              target={{ name: item.name }}
              onResolved={reloadAll}
            />
          ))}
        </ul>
      </section>

      <section className="card">
        <h2>{t("userIngredients")}</h2>
        <p className="hint">{t("seedNotice")}</p>
        {ingredients.data?.length === 0 && <p className="hint">{tCommon("none")}</p>}
        <ul className="row-list">
          {ingredients.data?.map((entry) => (
            <li key={entry.id} data-testid={`user-ingredient-${entry.id}`}>
              <span className="row-main">
                <strong>{entryName(entry, locale)}</strong>
                <span className="hint">
                  {tCategory(entry.category)} · {allergenSummary(entry, t, tAllergen)}
                  {!!entry.dish_count && ` · ${t("dishCount", { count: entry.dish_count })}`}
                </span>
              </span>
              <span className="actions">
                {editingId !== entry.id && (
                  <button type="button" onClick={() => setEditingId(entry.id)}>
                    {tCommon("edit")}
                  </button>
                )}
                <ConfirmDelete
                  name={entryName(entry, locale)}
                  notice={
                    entry.dish_count
                      ? t("deleteIngredientNotice", { count: entry.dish_count })
                      : undefined
                  }
                  onDelete={async () => {
                    await api.deleteIngredient(entry.id);
                    reloadAll();
                  }}
                />
              </span>
              {editingId === entry.id && (
                <EditIngredient
                  entry={entry}
                  onSaved={() => {
                    setEditingId(null);
                    reloadAll();
                  }}
                  onCancel={() => setEditingId(null)}
                />
              )}
            </li>
          ))}
        </ul>
      </section>

      <section className="card">
        <h2>{t("userCookware")}</h2>
        {cookware.data?.length === 0 && <p className="hint">{tCommon("none")}</p>}
        <ul className="row-list">
          {cookware.data?.map((entry) => (
            <li key={entry.id} data-testid={`user-cookware-${entry.id}`}>
              <span className="row-main">
                <strong>{entryName(entry, locale)}</strong>
                {!!entry.dish_count && (
                  <span className="hint">{t("dishCount", { count: entry.dish_count })}</span>
                )}
              </span>
              <ConfirmDelete
                name={entryName(entry, locale)}
                notice={
                  entry.dish_count
                    ? t("deleteCookwareNotice", { count: entry.dish_count })
                    : undefined
                }
                onDelete={async () => {
                  await api.deleteCookware(entry.id);
                  cookware.reload();
                  setRefreshKey((key) => key + 1);
                }}
              />
            </li>
          ))}
        </ul>
      </section>
      <DictionaryBrowser refreshKey={refreshKey} />
    </div>
  );
}
