"use client";

import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";

import { DictionarySearch } from "@/components/DictionarySearch";
import {
  type Allergen,
  ALLERGENS,
  api,
  entryName,
  type IngredientEntry,
  type Rule,
  RULE_TYPES,
  type RuleType,
  type RuleWrite,
} from "@/lib/api";
import { useErrorText } from "@/lib/errors";

type Target = "allergen" | "ingredient" | "text";

/** An allergy names a listed allergen or an entry, never free text, so code can check it. */
function targetsFor(ruleType: RuleType): Target[] {
  return ruleType === "allergy" ? ["allergen", "ingredient"] : ["allergen", "ingredient", "text"];
}

type AddRuleProps = {
  /** Saves the rule; a thrown error is shown under the form. `entry` is the chosen ingredient. */
  onAdd: (rule: RuleWrite, entry: IngredientEntry | null) => Promise<void>;
};

/** The form that adds one dietary rule (people page and diner card). */
export function AddRule({ onAdd }: AddRuleProps) {
  const t = useTranslations("people");
  const tRule = useTranslations("ruleType");
  const tAllergen = useTranslations("allergen");
  const errorText = useErrorText();
  const locale = useLocale();
  const [ruleType, setRuleType] = useState<RuleType>("allergy");
  const [target, setTarget] = useState<Target>("allergen");
  const [allergen, setAllergen] = useState<Allergen>(ALLERGENS[0]);
  const [ingredient, setIngredient] = useState<IngredientEntry | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState<unknown>(null);

  const targets = targetsFor(ruleType);
  const current = targets.includes(target) ? target : targets[0];
  const targetLabels: Record<Target, string> = {
    allergen: t("targetAllergen"),
    ingredient: t("targetIngredient"),
    text: t("targetText"),
  };

  async function add() {
    setError(null);
    try {
      await onAdd(
        {
          rule_type: ruleType,
          allergen: current === "allergen" ? allergen : null,
          ingredient_id: current === "ingredient" ? (ingredient?.id ?? null) : null,
          text: current === "text" ? text : null,
        },
        current === "ingredient" ? ingredient : null,
      );
      setIngredient(null);
      setText("");
    } catch (caught) {
      setError(caught);
    }
  }

  return (
    <div className="line-fields">
      <label className="mini-field">
        {t("ruleType")}
        <select value={ruleType} onChange={(event) => setRuleType(event.target.value as RuleType)}>
          {RULE_TYPES.map((value) => (
            <option key={value} value={value}>
              {tRule(value)}
            </option>
          ))}
        </select>
      </label>
      <label className="mini-field">
        {t("target")}
        <select value={current} onChange={(event) => setTarget(event.target.value as Target)}>
          {targets.map((value) => (
            <option key={value} value={value}>
              {targetLabels[value]}
            </option>
          ))}
        </select>
      </label>
      {current === "allergen" && (
        <label className="mini-field">
          {t("allergen")}
          <select value={allergen} onChange={(event) => setAllergen(event.target.value as Allergen)}>
            {ALLERGENS.map((value) => (
              <option key={value} value={value}>
                {tAllergen(value)}
              </option>
            ))}
          </select>
        </label>
      )}
      {current === "ingredient" &&
        (ingredient ? (
          <span>
            {entryName(ingredient, locale)}{" "}
            <button type="button" onClick={() => setIngredient(null)}>
              ×
            </button>
          </span>
        ) : (
          <DictionarySearch
            search={api.searchIngredients}
            onChoose={setIngredient}
            onCancel={() => setIngredient(null)}
          />
        ))}
      {current === "text" && (
        <label className="mini-field">
          {t("text")}
          <input value={text} onChange={(event) => setText(event.target.value)} />
        </label>
      )}
      <button type="button" className="btn-primary" onClick={add}>
        {t("addRule")}
      </button>
      {error !== null && (
        <p role="alert" className="notice notice-warning">
          {errorText(error)}
        </p>
      )}
    </div>
  );
}

/** What a rule shows: its target names, without the id. */
export type ShownRule = Pick<Rule, "rule_type" | "allergen" | "ingredient_id" | "text"> & {
  ingredient_name_zh: string | null;
  ingredient_name_en: string | null;
};

/** "Allergy: Peanut", in the current language. */
export function useRuleText(): (rule: ShownRule) => string {
  const tRule = useTranslations("ruleType");
  const tAllergen = useTranslations("allergen");
  const locale = useLocale();
  return (rule) => {
    const target = rule.allergen
      ? tAllergen(rule.allergen)
      : rule.ingredient_id
        ? entryName(
            {
              id: rule.ingredient_id,
              name_zh: rule.ingredient_name_zh,
              name_en: rule.ingredient_name_en,
              aliases_zh: [],
              aliases_en: [],
              source: "",
            },
            locale,
          )
        : rule.text;
    return `${tRule(rule.rule_type)}: ${target}`;
  };
}

export function RuleText({ rule }: { rule: ShownRule }) {
  return <>{useRuleText()(rule)}</>;
}
