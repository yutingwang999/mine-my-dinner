"use client";

import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";

import { ConfirmDelete } from "@/components/ConfirmDelete";
import { DictionarySearch } from "@/components/DictionarySearch";
import {
  type Allergen,
  ALLERGENS,
  api,
  type Diner,
  entryName,
  type IngredientEntry,
  type Rule,
  RULE_TYPES,
  type RuleType,
} from "@/lib/api";
import { useErrorText } from "@/lib/errors";
import { useLoad } from "@/lib/useLoad";

type Target = "allergen" | "ingredient" | "text";

/** An allergy names a listed allergen or an entry, never free text, so code can check it. */
function targetsFor(ruleType: RuleType): Target[] {
  return ruleType === "allergy" ? ["allergen", "ingredient"] : ["allergen", "ingredient", "text"];
}

function AddRule({ dinerId, onAdded }: { dinerId: number; onAdded: () => void }) {
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
      await api.createRule(dinerId, {
        rule_type: ruleType,
        allergen: current === "allergen" ? allergen : null,
        ingredient_id: current === "ingredient" ? (ingredient?.id ?? null) : null,
        text: current === "text" ? text : null,
      });
      setIngredient(null);
      setText("");
      onAdded();
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
      <button type="button" onClick={add}>
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

function RuleText({ rule }: { rule: Rule }) {
  const tRule = useTranslations("ruleType");
  const tAllergen = useTranslations("allergen");
  const locale = useLocale();
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
  return (
    <>
      {tRule(rule.rule_type)}: {target}
    </>
  );
}

function DinerCard({ diner, onChanged }: { diner: Diner; onChanged: () => void }) {
  const t = useTranslations("people");
  const tCommon = useTranslations("common");
  const errorText = useErrorText();
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(diner.display_name);
  const [error, setError] = useState<unknown>(null);
  const rules = useLoad(() => api.listRules(diner.id), [diner.id]);

  async function run(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
      return true;
    } catch (caught) {
      setError(caught);
      return false;
    }
  }

  const label = diner.is_self ? `${t("me")} (${diner.display_name})` : diner.display_name;

  return (
    <li data-testid={`diner-${diner.id}`} className="card">
      <div className="actions">
        {renaming ? (
          <span className="actions">
            <input aria-label={t("displayName")} value={name} onChange={(event) => setName(event.target.value)} />
            <button
              type="button"
              onClick={async () => {
                if (await run(() => api.renameDiner(diner.id, name))) {
                  setRenaming(false);
                  onChanged();
                }
              }}
            >
              {tCommon("save")}
            </button>
          </span>
        ) : (
          <strong>{label}</strong>
        )}{" "}
        {!renaming && (
          <button type="button" onClick={() => setRenaming(true)}>
            {t("rename")}
          </button>
        )}
        {!diner.is_self && (
          <ConfirmDelete
            name={diner.display_name}
            notice={t("deleteFriendNotice")}
            onDelete={async () => {
              await api.deleteDiner(diner.id);
              onChanged();
            }}
          />
        )}
      </div>
      <h3>{t("rules")}</h3>
      {rules.data?.length === 0 && <p className="hint">{t("noRules")}</p>}
      <ul className="row-list">
        {rules.data?.map((rule) => (
          <li key={rule.id}>
            <span>
              <RuleText rule={rule} />
            </span>
            <button
              type="button"
              onClick={async () => {
                if (await run(() => api.deleteRule(rule.id))) rules.reload();
              }}
            >
              {tCommon("remove")}
            </button>
          </li>
        ))}
      </ul>
      <AddRule dinerId={diner.id} onAdded={rules.reload} />
      {error !== null && (
        <p role="alert" className="notice notice-warning">
          {errorText(error)}
        </p>
      )}
    </li>
  );
}

export function PeopleManager() {
  const t = useTranslations("people");
  const errorText = useErrorText();
  const [name, setName] = useState("");
  const [error, setError] = useState<unknown>(null);
  const { data, reload } = useLoad(api.listDiners, []);

  async function addFriend() {
    setError(null);
    try {
      await api.createDiner(name);
      setName("");
      reload();
    } catch (caught) {
      setError(caught);
    }
  }

  return (
    <div>
      <ul className="card-list">
        {data?.map((diner) => (
          <DinerCard key={diner.id} diner={diner} onChanged={reload} />
        ))}
      </ul>
      <section className="card">
        <h2>{t("addFriend")}</h2>
        <p className="hint">{t("friendHint")}</p>
        <div className="line-fields">
          <label className="mini-field">
            {t("displayName")}
            <input value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <button type="button" onClick={addFriend}>
            {t("addFriend")}
          </button>
        </div>
        {error !== null && (
          <p role="alert" className="notice notice-warning">
            {errorText(error)}
          </p>
        )}
      </section>
    </div>
  );
}
