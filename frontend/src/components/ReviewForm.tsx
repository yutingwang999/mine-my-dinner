"use client";

import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useEffect, useRef, useState } from "react";

import { CookwareReviewRow, type ReviewCookware } from "@/components/CookwareReviewRow";
import { DictionarySearch, namesEntry } from "@/components/DictionarySearch";
import { IngredientReviewRow, type ReviewLine } from "@/components/IngredientReviewRow";
import {
  type AllergyCheck,
  api,
  type DictionaryEntry,
  entryName,
  DISH_TYPES,
  type DishType,
  type CreatedRecipe,
  type EditableRecipe,
  type Lang,
  LANGUAGES,
  type RecipeWrite,
  type TimeSource,
} from "@/lib/api";
import { useErrorText } from "@/lib/errors";

type Props = {
  /** A draft to review, or a saved recipe to edit. */
  draft: EditableRecipe;
  onSaved: () => void;
  /** Saves a new recipe unless given another action, such as an update. */
  save?: (data: RecipeWrite) => Promise<unknown>;
  /** More buttons beside Save, such as discarding the draft or cancelling an edit. */
  actions?: ReactNode;
};

let nextKey = 0;

function toLine(line: EditableRecipe["ingredients"][number], locale: string): ReviewLine {
  // The entry's own name, such as 葱 for a line written 葱花, in the page's language.
  const entryNames = [
    locale === "zh" ? line.linked_name_zh : line.linked_name_en,
    line.linked_name_zh,
    line.linked_name_en,
    line.name,
  ];
  const linkedName =
    line.ingredient_id !== null ? (entryNames.find((name) => !!name) ?? null) : null;
  return { ...line, key: nextKey++, skipped: false, linkedName, added: false };
}

/**
 * The name for a line added from a dictionary search. The user's own wording wins,
 * including an alias such as 土豆丝; text in the other language, or a fragment typed
 * while searching, becomes the entry's name in the recipe's language.
 */
function addedLineName(entry: DictionaryEntry, typed: string, lang: Lang): string {
  const text = typed.trim();
  const term = text.toLowerCase();
  const sameLanguage =
    lang === "zh" ? [entry.name_zh, ...entry.aliases_zh] : [entry.name_en, ...entry.aliases_en];
  if (sameLanguage.some((name) => name?.trim().toLowerCase() === term)) {
    return text;
  }
  const allNames = [entry.name_zh, entry.name_en, ...entry.aliases_zh, ...entry.aliases_en];
  const isFragment = allNames.some((name) => name?.toLowerCase().includes(term));
  if (namesEntry(entry, text) || isFragment) {
    return entryName(entry, lang);
  }
  return text;
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

export function ReviewForm({
  draft,
  onSaved,
  save: saveRecipe = api.saveRecipe,
  actions,
}: Props) {
  const t = useTranslations("review");
  const tLang = useTranslations("lang");
  const tDish = useTranslations("dishType");
  const tSource = useTranslations("timeSource");
  const tAllergen = useTranslations("allergen");
  const locale = useLocale();
  const errorText = useErrorText();

  const [title, setTitle] = useState(draft.title);
  const [lang, setLang] = useState<Lang>(draft.lang);
  const [dishType, setDishType] = useState<DishType>(draft.dish_type);
  const [time, setTime] = useState(draft.time_min?.toString() ?? "");
  const [sourceChoice, setSourceChoice] = useState<TimeSource | null>(null);
  const [servings, setServings] = useState(draft.servings?.toString() ?? "");
  const [tags, setTags] = useState(draft.tags.join(", "));
  const [summary, setSummary] = useState(draft.steps_summary ?? "");
  const [sourceUrl, setSourceUrl] = useState(draft.source_url ?? "");
  const [lines, setLines] = useState<ReviewLine[]>(() =>
    draft.ingredients.map((line) => toLine(line, locale)),
  );
  const [cookware, setCookware] = useState<ReviewCookware[]>(() =>
    draft.cookware.map((item) => ({ ...item, key: nextKey++, skipped: false, linkedName: null })),
  );
  const [addingLine, setAddingLine] = useState(false);
  const [addingCookware, setAddingCookware] = useState(false);
  const [saving, setSaving] = useState(false);
  const [imagesFailed, setImagesFailed] = useState<{
    created: CreatedRecipe;
    error: unknown;
  } | null>(null);
  const [error, setError] = useState<unknown>(null);
  // A ref, not state, so a second click in the same frame is also ignored.
  const savingRef = useRef(false);
  const allergyCheck = useAllergyCheck(lines);

  const timeMin = parseNumber(time);
  const timeChanged = timeMin !== draft.time_min;
  // "estimated" describes only the model's own number, so it goes once the time changes.
  const sourceOptions: TimeSource[] =
    !timeChanged && draft.time_source === "estimated"
      ? ["stated", "user", "estimated"]
      : ["stated", "user"];
  // A changed time is the user's by default; the user can say the source states it.
  const defaultSource = timeChanged ? "user" : draft.time_source;
  const timeSource =
    timeMin === null
      ? null
      : sourceChoice !== null && sourceOptions.includes(sourceChoice)
        ? sourceChoice
        : defaultSource;

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
        // An added line has no source wording, so its name stands in for it.
        raw: line.added ? line.name : line.raw,
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

  /** The recipe is saved by now, so a failure here never saves it twice. */
  async function uploadImages(created: CreatedRecipe) {
    setImagesFailed(null);
    try {
      await api.uploadRecipeImages(created.id, draft.images ?? [], created.log_id);
      onSaved();
    } catch (caught) {
      setImagesFailed({ created, error: caught });
    }
  }

  async function save() {
    if (savingRef.current) return;
    savingRef.current = true;
    setSaving(true);
    setError(null);
    try {
      const created = await saveRecipe(buildWrite());
      if (draft.images?.length) {
        await uploadImages(created as CreatedRecipe);
        return;
      }
      onSaved();
    } catch (caught) {
      setError(caught);
      savingRef.current = false;
      setSaving(false);
    }
  }

  function addCookware(
    item: Pick<ReviewCookware, "name" | "cookware_id" | "linkedName" | "startCreating">,
  ) {
    setCookware((current) => [...current, { ...item, key: nextKey++, skipped: false }]);
    setAddingCookware(false);
  }

  function addLine(name: string, ingredientId: number | null, linkedName: string | null) {
    const line = toLine(
      {
        raw: name,
        name,
        amount: null,
        unit: null,
        amount_type: "missing",
        role: "secondary",
        optional: false,
        ingredient_id: ingredientId,
      },
      locale,
    );
    setLines((current) => [...current, { ...line, linkedName, added: true }]);
    setAddingLine(false);
  }

  const conflicts = allergyCheck?.conflicts ?? [];
  const unknown = allergyCheck?.unknown ?? [];

  return (
    <form
      className="review-form"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <label className="field">
        {t("recipeTitle")}
        <input value={title} onChange={(event) => setTitle(event.target.value)} />
      </label>
      <label className="field">
        {t("lang")}
        <select value={lang} onChange={(event) => setLang(event.target.value as Lang)}>
          {LANGUAGES.map((value) => (
            <option key={value} value={value}>
              {tLang(value)}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        {t("dishType")}
        <select value={dishType} onChange={(event) => setDishType(event.target.value as DishType)}>
          {DISH_TYPES.map((value) => (
            <option key={value} value={value}>
              {tDish(value)}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        {t("timeMin")}
        <input type="number" min="1" value={time} onChange={(event) => setTime(event.target.value)} />
      </label>
      {timeMin !== null && (
        <label className="field">
          {t("timeSource")}
          <select
            value={timeSource ?? ""}
            onChange={(event) => setSourceChoice(event.target.value as TimeSource)}
          >
            {timeSource === null && <option value="" />}
            {sourceOptions.map((value) => (
              <option key={value} value={value}>
                {tSource(value)}
              </option>
            ))}
          </select>
        </label>
      )}
      <label className="field">
        {t("servings")}
        <input
          type="number"
          min="1"
          value={servings}
          onChange={(event) => setServings(event.target.value)}
        />
      </label>
      <label className="field">
        {t("tags")}
        <input value={tags} onChange={(event) => setTags(event.target.value)} />
      </label>
      <label className="field">
        {t("stepsSummary")}
        <textarea rows={4} value={summary} onChange={(event) => setSummary(event.target.value)} />
      </label>
      <label className="field">
        {t("sourceUrl")}
        <input type="url" value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} />
      </label>

      <h2>{t("ingredients")}</h2>
      <ul className="line-list">
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
      {addingLine ? (
        <DictionarySearch
          search={api.searchIngredients}
          onChoose={(entry, typed) =>
            addLine(addedLineName(entry, typed, lang), entry.id, entryName(entry, locale))
          }
          onAddUnlisted={(name) => addLine(name, null, null)}
          unlistedLabel={(name) => t("newIngredient", { name })}
          onCancel={() => setAddingLine(false)}
        />
      ) : (
        <button type="button" onClick={() => setAddingLine(true)}>
          {t("addLine")}
        </button>
      )}

      <h2>{t("cookware")}</h2>
      <ul className="line-list">
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
      {addingCookware ? (
        <DictionarySearch
          search={api.searchCookware}
          onChoose={(entry) => {
            const name = entryName(entry, locale);
            addCookware({ name, cookware_id: entry.id, linkedName: name });
          }}
          onAddUnlisted={(name) =>
            addCookware({ name, cookware_id: null, linkedName: null, startCreating: true })
          }
          unlistedLabel={(name) => t("newCookware", { name })}
          onCancel={() => setAddingCookware(false)}
        />
      ) : (
        <button type="button" onClick={() => setAddingCookware(true)}>
          {t("addCookware")}
        </button>
      )}

      {conflicts.length > 0 && (
        <div role="alert" className="notice notice-warning">
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
      {unknown.length > 0 && (
        <p className="notice notice-info">{t("unknownAllergens", { names: unknown.join(", ") })}</p>
      )}
      <p>{t("checkYourself")}</p>

      {error !== null && (
        <p role="alert" className="notice notice-warning">
          {t("saveFailed", { error: errorText(error) })}
        </p>
      )}
      {imagesFailed !== null && (
        <div role="alert" className="notice notice-warning actions">
          <span>{t("imagesFailed", { error: errorText(imagesFailed.error) })}</span>
          <button type="button" onClick={() => uploadImages(imagesFailed.created)}>
            {t("retryImages")}
          </button>
          <button type="button" onClick={onSaved}>
            {t("skipImages")}
          </button>
        </div>
      )}
      <div className="form-actions">
        <button type="submit" disabled={saving}>
          {saving && imagesFailed === null ? t("saving") : t("save")}
        </button>
        {actions}
      </div>
    </form>
  );
}
