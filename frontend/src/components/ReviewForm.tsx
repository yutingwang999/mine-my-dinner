"use client";

import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";

import { CookwareReviewRow, type ReviewCookware } from "@/components/CookwareReviewRow";
import { IngredientReviewRow, type ReviewLine } from "@/components/IngredientReviewRow";
import {
  type AllergyCheck,
  api,
  DISH_TYPES,
  type DishType,
  type EditableRecipe,
  type Lang,
  LANGUAGES,
  type RecipeWrite,
} from "@/lib/api";
import { useErrorText } from "@/lib/errors";

type Props = {
  /** A draft to review, or a saved recipe to edit. */
  draft: EditableRecipe;
  onSaved: () => void;
  /** Saves a new recipe unless given another action, such as an update. */
  save?: (data: RecipeWrite) => Promise<unknown>;
};

let nextKey = 0;

function toLine(line: EditableRecipe["ingredients"][number]): ReviewLine {
  return { ...line, key: nextKey++, skipped: false, linkedName: null };
}

function parseNumber(value: string): number | null {
  return value.trim() === "" ? null : Number(value);
}

function useAllergyCheck(lines: ReviewLine[]): AllergyCheck | null {
  const [result, setResult] = useState<AllergyCheck | null>(null);
  const refs = lines.map((line) => ({ name: line.name, ingredient_id: line.ingredient_id }));
  const signature = JSON.stringify(refs);

  useEffect(() => {
    let active = true;
    api
      .checkAllergies(JSON.parse(signature))
      .then((checked) => active && setResult(checked))
      .catch(() => active && setResult(null));
    return () => {
      active = false;
    };
  }, [signature]);

  return result;
}

export function ReviewForm({ draft, onSaved, save: saveRecipe = api.saveRecipe }: Props) {
  const t = useTranslations("review");
  const tLang = useTranslations("lang");
  const tDish = useTranslations("dishType");
  const tSource = useTranslations("timeSource");
  const tAllergen = useTranslations("allergen");
  const errorText = useErrorText();

  const [title, setTitle] = useState(draft.title);
  const [lang, setLang] = useState<Lang>(draft.lang);
  const [dishType, setDishType] = useState<DishType>(draft.dish_type);
  const [time, setTime] = useState(draft.time_min?.toString() ?? "");
  const [servings, setServings] = useState(draft.servings?.toString() ?? "");
  const [tags, setTags] = useState(draft.tags.join(", "));
  const [summary, setSummary] = useState(draft.steps_summary ?? "");
  const [sourceUrl, setSourceUrl] = useState(draft.source_url ?? "");
  const [lines, setLines] = useState<ReviewLine[]>(() => draft.ingredients.map(toLine));
  const [cookware, setCookware] = useState<ReviewCookware[]>(() =>
    draft.cookware.map((item) => ({ ...item, key: nextKey++, skipped: false, linkedName: null })),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  // A ref, not state, so a second click in the same frame is also ignored.
  const savingRef = useRef(false);
  const allergyCheck = useAllergyCheck(lines);

  const timeMin = parseNumber(time);
  // A changed time is the user's; an unchanged one keeps the extraction's source.
  const timeSource =
    timeMin === null ? null : timeMin === draft.time_min ? draft.time_source : "user";

  function buildWrite(): RecipeWrite {
    const cookwareIds = cookware
      .filter((item) => !item.skipped && item.cookware_id !== null)
      .map((item) => item.cookware_id as number);
    return {
      lang,
      title,
      dish_type: dishType,
      time_min: timeMin,
      time_source: timeSource,
      servings: parseNumber(servings),
      tags: tags
        .split(/[,，]/)
        .map((tag) => tag.trim())
        .filter(Boolean),
      steps_summary: summary.trim() || null,
      ingredients: lines.map((line) => ({
        raw: line.raw,
        name: line.name,
        amount: line.amount,
        unit: line.unit,
        amount_type: line.amount_type,
        role: line.role,
        optional: line.optional,
        ingredient_id: line.ingredient_id,
      })),
      cookware_ids: [...new Set(cookwareIds)],
      source_type: draft.source_type,
      source_url: sourceUrl.trim() || null,
      provenance: draft.provenance,
    };
  }

  async function save() {
    if (savingRef.current) return;
    savingRef.current = true;
    setSaving(true);
    setError(null);
    try {
      await saveRecipe(buildWrite());
      onSaved();
    } catch (caught) {
      setError(caught);
      savingRef.current = false;
      setSaving(false);
    }
  }

  function addLine() {
    setLines((current) => [
      ...current,
      toLine({
        raw: "",
        name: "",
        amount: null,
        unit: null,
        amount_type: "missing",
        role: "secondary",
        optional: false,
        ingredient_id: null,
      }),
    ]);
  }

  const conflicts = allergyCheck?.conflicts ?? [];
  const unknown = allergyCheck?.unknown ?? [];

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <label>
        {t("recipeTitle")}
        <input value={title} onChange={(event) => setTitle(event.target.value)} />
      </label>
      <label>
        {t("lang")}
        <select value={lang} onChange={(event) => setLang(event.target.value as Lang)}>
          {LANGUAGES.map((value) => (
            <option key={value} value={value}>
              {tLang(value)}
            </option>
          ))}
        </select>
      </label>
      <label>
        {t("dishType")}
        <select value={dishType} onChange={(event) => setDishType(event.target.value as DishType)}>
          {DISH_TYPES.map((value) => (
            <option key={value} value={value}>
              {tDish(value)}
            </option>
          ))}
        </select>
      </label>
      <label>
        {t("timeMin")}
        <input type="number" min="1" value={time} onChange={(event) => setTime(event.target.value)} />
      </label>
      {timeSource && <span>{t("timeSource", { source: tSource(timeSource) })}</span>}
      <label>
        {t("servings")}
        <input
          type="number"
          min="1"
          value={servings}
          onChange={(event) => setServings(event.target.value)}
        />
      </label>
      <label>
        {t("tags")}
        <input value={tags} onChange={(event) => setTags(event.target.value)} />
      </label>
      <label>
        {t("stepsSummary")}
        <textarea value={summary} onChange={(event) => setSummary(event.target.value)} />
      </label>
      <label>
        {t("sourceUrl")}
        <input type="url" value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} />
      </label>

      <h2>{t("ingredients")}</h2>
      <ul>
        {lines.map((line, index) => (
          <IngredientReviewRow
            key={line.key}
            line={line}
            lang={lang}
            onChange={(changed) =>
              setLines((current) => current.map((item, i) => (i === index ? changed : item)))
            }
            onRemove={() => setLines((current) => current.filter((_, i) => i !== index))}
          />
        ))}
      </ul>
      <button type="button" onClick={addLine}>
        {t("addLine")}
      </button>

      <h2>{t("cookware")}</h2>
      <ul>
        {cookware.map((item, index) => (
          <CookwareReviewRow
            key={item.key}
            item={item}
            lang={lang}
            onChange={(changed) =>
              setCookware((current) => current.map((entry, i) => (i === index ? changed : entry)))
            }
          />
        ))}
      </ul>

      {conflicts.length > 0 && (
        <div role="alert">
          <p>{t("allergyWarning")}</p>
          <ul>
            {conflicts.map((conflict, index) => (
              <li key={index}>
                {conflict.allergen
                  ? t("allergenConflict", {
                      name: conflict.line_name,
                      allergen: tAllergen(conflict.allergen),
                    })
                  : t("ruleConflict", { name: conflict.line_name })}
              </li>
            ))}
          </ul>
        </div>
      )}
      {unknown.length > 0 && <p>{t("unknownAllergens", { names: unknown.join(", ") })}</p>}
      <p>{t("checkYourself")}</p>

      <button type="submit" disabled={saving}>
        {saving ? t("saving") : t("save")}
      </button>
      {error !== null && <p role="alert">{t("saveFailed", { error: errorText(error) })}</p>}
    </form>
  );
}
