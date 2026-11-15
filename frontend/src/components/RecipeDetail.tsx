"use client";

import { useTranslations } from "next-intl";

import type { IngredientLine, SavedRecipe } from "@/lib/api";

type Props = {
  recipe: SavedRecipe;
  /** Inside a plan card: no servings line. */
  compact?: boolean;
  /** Ingredient names the plan marks as missing. */
  missing?: string[];
};

/** A read-only recipe: ingredients, cookware, time, steps, and where it came from. */
export function RecipeDetail({ recipe, compact = false, missing = [] }: Props) {
  const t = useTranslations("recipeDetail");
  const tUnit = useTranslations("unit");
  const tAmount = useTranslations("amountType");
  const tSource = useTranslations("timeSource");

  const missingNames = new Set(missing);
  const isMissing = (line: IngredientLine) =>
    [line.name, line.linked_name_zh, line.linked_name_en].some(
      (name) => name != null && missingNames.has(name),
    );

  function amountText(line: IngredientLine): string {
    if (line.amount_type === "vague") return t("toTaste");
    if (line.amount === null) return "";
    // A number with no unit is a household measure, such as a bowl or a handful.
    return `${line.amount} ${line.unit === null ? tAmount("spoon") : tUnit(line.unit)}`;
  }

  function lineText(line: IngredientLine, withOptional: boolean): string {
    let text = [line.name, amountText(line)].filter(Boolean).join(" ");
    if (withOptional && line.optional) text += ` (${t("optional")})`;
    if (isMissing(line)) text += ` (${t("missing")})`;
    return text;
  }

  // One entry per plain line, and one per choice group at the group's first line.
  const entries: { key: string; text: string; missing: boolean }[] = [];
  const seenGroups = new Set<number>();
  recipe.ingredients.forEach((line, index) => {
    if (line.choice_group === null) {
      entries.push({ key: `l${index}`, text: lineText(line, true), missing: isMissing(line) });
      return;
    }
    if (seenGroups.has(line.choice_group)) return;
    seenGroups.add(line.choice_group);
    const members = recipe.ingredients.filter((other) => other.choice_group === line.choice_group);
    // The group's lines share one optional flag; it is marked once, on the whole line.
    const list = members.map((member) => lineText(member, false)).join(t("choiceSeparator"));
    const optional = members.every((member) => member.optional) ? ` (${t("optional")})` : "";
    entries.push({
      key: `g${line.choice_group}`,
      text: t("choice", { list }) + optional,
      missing: members.some(isMissing),
    });
  });

  const time =
    recipe.time_min === null
      ? t("timeUnknown")
      : t("time", {
          minutes: recipe.time_min,
          source: recipe.time_source ? tSource(recipe.time_source) : "",
        });

  return (
    <div className={compact ? "recipe-detail compact" : "recipe-detail"}>
      <h3>{t("ingredients")}</h3>
      <ul className="recipe-ingredients">
        {entries.map((entry) => (
          <li key={entry.key} className={entry.missing ? "is-missing" : undefined}>
            {entry.text}
          </li>
        ))}
      </ul>
      {recipe.cookware.length > 0 && (
        <p>{t("cookware", { list: recipe.cookware.map((item) => item.name).join(", ") })}</p>
      )}
      <p>{time}</p>
      {!compact && recipe.servings !== null && <p>{t("servings", { count: recipe.servings })}</p>}
      <h3>{t("steps")}</h3>
      {recipe.steps_summary ? <p>{recipe.steps_summary}</p> : <p className="hint">{t("noSteps")}</p>}
    </div>
  );
}
