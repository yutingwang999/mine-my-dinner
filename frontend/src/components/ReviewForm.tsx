"use client";

import { useLocale, useTranslations } from "next-intl";
import { Fragment, type ReactNode, useEffect, useRef, useState } from "react";

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
import { amountProblem } from "@/lib/amounts";
import { useErrorText } from "@/lib/errors";
import { useDragAutoScroll } from "@/lib/useDragAutoScroll";

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
  // A draft queued before choice groups existed has no field at all; undefined would
  // render as a one-line card, since the form compares with null.
  return {
    ...line,
    choice_group: line.choice_group ?? null,
    key: nextKey++,
    skipped: false,
    linkedName,
    added: false,
  };
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

/**
 * A choice group needs two lines; a lone member goes back to being an ordinary line.
 * `keep` spares the group the user just created, which waits for its second line.
 */
function clearOrphanGroups(lines: ReviewLine[], keep: number | null = null): ReviewLine[] {
  const groups = lines.map((line) => line.choice_group).filter((group) => group !== null);
  return lines.map((line) =>
    line.choice_group !== null &&
    line.choice_group !== keep &&
    groups.filter((group) => group === line.choice_group).length < 2
      ? { ...line, choice_group: null }
      : line,
  );
}

/** The lines as the form shows them: ordinary rows, and one card per choice group. */
function renderItems(lines: ReviewLine[]): { group: number | null; indexes: number[] }[] {
  const items: { group: number | null; indexes: number[] }[] = [];
  lines.forEach((line, index) => {
    const open = line.choice_group !== null && items.find((item) => item.group === line.choice_group);
    if (open) {
      open.indexes.push(index);
    } else {
      items.push({ group: line.choice_group, indexes: [index] });
    }
  });
  return items;
}

/** Lines of one group are optional together, so a toggle reaches every member. */
function syncGroupOptional(lines: ReviewLine[], changed: ReviewLine): ReviewLine[] {
  if (changed.choice_group === null) {
    return lines;
  }
  return lines.map((line) =>
    line.choice_group === changed.choice_group ? { ...line, optional: changed.optional } : line,
  );
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
  const tLine = useTranslations("line");
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
  // Drafts queued before supplies existed have none.
  const [supplies, setSupplies] = useState<string[]>(() => draft.supplies ?? []);
  const [newSupply, setNewSupply] = useState("");
  const [addingLine, setAddingLine] = useState(false);
  const [addingCookware, setAddingCookware] = useState(false);
  const [saving, setSaving] = useState(false);
  const [imagesFailed, setImagesFailed] = useState<{
    created: CreatedRecipe;
    error: unknown;
  } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [amountsBlocked, setAmountsBlocked] = useState(false);
  // The line being dragged, so a drop on another line knows what to merge.
  const [dragKey, setDragKey] = useState<number | null>(null);
  // A ref, not state, so a second click in the same frame is also ignored.
  const savingRef = useRef(false);
  const allergyCheck = useAllergyCheck(lines);
  useDragAutoScroll(dragKey !== null);

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
        choice_group: line.choice_group,
        ingredient_id: line.ingredient_id,
      })),
      cookware_ids: [...new Set(cookwareIds)],
      supplies,
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

  const hasAmountProblem = lines.some((line) => amountProblem(line) !== null);
  // While a grouped line is dragged, a zone below its own card offers taking it out.
  const draggedGroup =
    dragKey !== null
      ? (lines.find((line) => line.key === dragKey)?.choice_group ?? null)
      : null;

  async function save() {
    if (savingRef.current) return;
    // The server would reject these lines with no clear message, so stop here.
    setAmountsBlocked(hasAmountProblem);
    if (hasAmountProblem) return;
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
        choice_group: null,
        ingredient_id: ingredientId,
      },
      locale,
    );
    setLines((current) => [...current, { ...line, linkedName, added: true }]);
    setAddingLine(false);
  }

  /** Dropping one line onto another makes them a choice, or joins the target's group. */
  function mergeLines(sourceKey: number, targetKey: number) {
    setLines((current) => {
      const source = current.find((line) => line.key === sourceKey);
      const target = current.find((line) => line.key === targetKey);
      if (!source || !target || sourceKey === targetKey) return current;
      if (source.choice_group !== null && source.choice_group === target.choice_group) {
        return current;
      }
      const group =
        target.choice_group ??
        source.choice_group ??
        Math.max(0, ...current.map((line) => line.choice_group ?? 0)) + 1;
      // The merged group's members are optional together, following the target's value.
      const next = current.map((line) =>
        line.key === sourceKey || line.key === targetKey || line.choice_group === group
          ? { ...line, choice_group: group, optional: target.optional }
          : line,
      );
      return clearOrphanGroups(next, group);
    });
  }

  /** The card's one checkbox: the whole group is optional together. */
  function setGroupOptional(group: number, optional: boolean) {
    setLines((current) =>
      current.map((line) => (line.choice_group === group ? { ...line, optional } : line)),
    );
  }

  /** Takes a line out of its choice; a group left with one line dissolves. */
  function ungroupLine(key: number) {
    setLines((current) =>
      clearOrphanGroups(
        current.map((line) => (line.key === key ? { ...line, choice_group: null } : line)),
      ),
    );
  }

  function addSupply() {
    const name = newSupply.trim();
    if (name && !supplies.includes(name)) {
      setSupplies((current) => [...current, name]);
    }
    setNewSupply("");
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
      <p className="hint">{t("choiceHint")}</p>
      <ul
        className="line-list"
        data-testid="ingredient-list"
        onDragOver={(event) => {
          if (dragKey !== null) event.preventDefault();
        }}
        onDrop={(event) => {
          // A drop on the list background, outside any card: the line leaves its choice.
          if (dragKey === null) return;
          event.preventDefault();
          ungroupLine(dragKey);
          setDragKey(null);
        }}
      >
        {renderItems(lines).map((item) => {
          const rows = item.indexes.map((index, position) => {
            const line = lines[index];
            return (
              <Fragment key={line.key}>
                {position > 0 && (
                  <li className="choice-or" aria-hidden="true">
                    {t("choiceOr")}
                  </li>
                )}
                <IngredientReviewRow
                  line={line}
                  lang={lang}
                  dragLabel={tLine("dragLine", { name: line.name })}
                  dragging={dragKey !== null}
                  onDragStart={() => setDragKey(line.key)}
                  onDragEnd={() => setDragKey(null)}
                  onDropLine={() => {
                    if (dragKey !== null) mergeLines(dragKey, line.key);
                    setDragKey(null);
                  }}
                  grouped={line.choice_group !== null}
                  onUngroup={line.choice_group !== null ? () => ungroupLine(line.key) : null}
                  onChange={(changed) =>
                    setLines((current) =>
                      syncGroupOptional(
                        current.map((other, i) => (i === index ? changed : other)),
                        changed,
                      ),
                    )
                  }
                  onRemove={() =>
                    setLines((current) => clearOrphanGroups(current.filter((_, i) => i !== index)))
                  }
                />
              </Fragment>
            );
          });
          if (item.group === null) {
            return rows;
          }
          const groupOptional = lines[item.indexes[0]].optional;
          return (
            <Fragment key={`group-${item.group}`}>
            <li
              className="line-group"
              role="group"
              aria-label={t("choiceCard")}
              onDragOver={(event) => {
                if (dragKey !== null) event.preventDefault();
              }}
              onDrop={(event) => {
                // A drop on the card itself, between its rows, joins the group.
                if (dragKey === null) return;
                event.preventDefault();
                event.stopPropagation();
                mergeLines(dragKey, lines[item.indexes[0]].key);
                setDragKey(null);
              }}
            >
              <div className="choice-head">
                <p className="choice-title">
                  {groupOptional ? t("choiceCardOptional") : t("choiceCard")}
                </p>
                <label className="mini-field inline">
                  <input
                    type="checkbox"
                    checked={groupOptional}
                    onChange={(event) =>
                      setGroupOptional(item.group as number, event.target.checked)
                    }
                  />
                  {t("choiceOptional")}
                </label>
              </div>
              <ul className="line-list">{rows}</ul>
            </li>
            {draggedGroup === item.group && (
              <li
                className="drop-out"
                onDragOver={(event) => event.preventDefault()}
                onDrop={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  if (dragKey !== null) ungroupLine(dragKey);
                  setDragKey(null);
                }}
              >
                {t("dropOut")}
              </li>
            )}
            </Fragment>
          );
        })}
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

      <fieldset className="supplies" aria-labelledby="supplies-heading">
        <h2 id="supplies-heading">{t("supplies")}</h2>
        <p className="hint">{t("suppliesHint")}</p>
        <ul className="line-list">
          {supplies.map((name) => (
            <li key={name} className="actions">
              <span>{name}</span>
              <button
                type="button"
                aria-label={t("removeSupply", { name })}
                onClick={() => setSupplies((current) => current.filter((item) => item !== name))}
              >
                {tLine("remove")}
              </button>
            </li>
          ))}
        </ul>
        <span className="actions">
          <input
            aria-label={t("addSupply")}
            value={newSupply}
            onChange={(event) => setNewSupply(event.target.value)}
            onKeyDown={(event) => {
              // Enter adds the supply instead of submitting the form.
              if (event.key === "Enter") {
                event.preventDefault();
                addSupply();
              }
            }}
          />
          <button type="button" onClick={addSupply} disabled={!newSupply.trim()}>
            {t("addSupply")}
          </button>
        </span>
      </fieldset>

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

      {amountsBlocked && hasAmountProblem && (
        <p role="alert" className="notice notice-warning">
          {t("fixAmounts")}
        </p>
      )}
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
