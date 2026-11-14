"use client";

import { useFormatter, useTranslations } from "next-intl";
import { useState } from "react";

import { AllergenNotice } from "@/components/AllergenNotice";
import type { ChatPlan, NextAction, PlanFinding } from "@/lib/chat";

type Props = {
  plan: ChatPlan;
  /** Called with the chosen next action; the buttons are disabled while `busy`. */
  onAction?: (action: NextAction) => void;
  busy?: boolean;
};

function Assumed({ plan }: { plan: ChatPlan }) {
  const t = useTranslations("chat.plan");
  const format = useFormatter();
  const parts: string[] = [];
  if (plan.defaults_used.includes("headcount")) {
    parts.push(t("assumedHeadcount", { count: plan.headcount }));
  }
  if (plan.defaults_used.includes("composition")) {
    parts.push(t("assumedDishes", { count: plan.spec.dishes }));
    if (plan.spec.soups > 0) parts.push(t("assumedSoups", { count: plan.spec.soups }));
  }
  if (plan.defaults_used.includes("mode")) {
    parts.push(t(plan.spec.mode === "shopping" ? "assumedShopping" : "assumedFridgeOnly"));
  }
  if (parts.length === 0) return null;
  return <p className="hint">{t("assumed", { list: format.list(parts, { type: "unit" }) })}</p>;
}

function warningText(
  t: ReturnType<typeof useTranslations>,
  finding: PlanFinding,
  titles: Map<number, string>,
): string[] {
  const d = finding.detail;
  const title = finding.recipe_id === null ? "" : (titles.get(finding.recipe_id) ?? "");
  const text = (key: string, values: Record<string, string | number> = {}) =>
    t(key, { title, ...values });
  switch (finding.code) {
    case "S1":
      return [text("S1", { name: String(d.name), needed: Number(d.needed), have: Number(d.have), unit: String(d.unit) })];
    case "S2":
      return [text("S2", { line: String(d.line) })];
    case "S3":
      return [text("S3", { date: String(d.expires_on) })];
    case "S4":
      return [text("S4", { score: Number(d.score) })];
    case "S5":
      return [text("S5")];
    case "S6":
      return [text("S6", { name: String(d.name) })];
    case "S7":
      if (d.kind === "unknown") return [text("S7unknown")];
      if (d.kind === "estimated") return [text("S7estimated", { minutes: Number(d.time_min) })];
      return [text("S7total", { total: Number(d.total_min), limit: Number(d.limit) })];
    case "S8": {
      if (Array.isArray(d.unrecognized)) {
        return [text("S8lines", { list: d.unrecognized.map(String).join(", ") })];
      }
      return [text("S8diners", { count: Number(d.unconfirmed_diners) })];
    }
    default:
      return [text("other", { code: finding.code })];
  }
}

export function PlanCard({ plan, onAction, busy = false }: Props) {
  const t = useTranslations("chat.plan");
  const tWarning = useTranslations("chat.warning");
  const tAction = useTranslations("chat.action");
  const tDish = useTranslations("dishType");
  const tSource = useTranslations("timeSource");
  const [showLeftOut, setShowLeftOut] = useState(false);

  const titles = new Map(plan.recipes.map((recipe) => [recipe.id, recipe.title]));
  const warnings = plan.warnings.flatMap((finding) => warningText(tWarning, finding, titles));

  return (
    <section className="card plan-card">
      <h2>{t("title")}</h2>
      {plan.recipes.length === 0 ? (
        <p className="hint">{t("noDishes")}</p>
      ) : (
        <ul className="card-list">
          {plan.recipes.map((recipe) => {
            const missing = plan.missing[String(recipe.id)] ?? [];
            const cookware = plan.missing_cookware[String(recipe.id)] ?? [];
            return (
              <li key={recipe.id} className="plan-dish">
                <strong>{recipe.title}</strong>
                <p className="hint">
                  {tDish(recipe.dish_type)} ·{" "}
                  {recipe.time_min === null
                    ? t("timeUnknown")
                    : t("timeKnown", {
                        minutes: recipe.time_min,
                        source: recipe.time_source ? tSource(recipe.time_source) : "",
                      })}
                </p>
                {missing.length > 0 && <p>{t("missingIngredients", { list: missing.join(", ") })}</p>}
                {cookware.length > 0 && <p>{t("missingCookware", { list: cookware.join(", ") })}</p>}
              </li>
            );
          })}
        </ul>
      )}
      {plan.servings_notes.map((note) => (
        <p key={note.recipe_id} className="hint">
          {t("servingsNote", {
            title: titles.get(note.recipe_id) ?? "",
            servings: note.servings,
            headcount: note.headcount,
          })}
        </p>
      ))}
      {warnings.length > 0 && (
        <div className="notice notice-info">
          <h3>{t("warnings")}</h3>
          <ul>
            {warnings.map((text, index) => (
              <li key={index}>{text}</li>
            ))}
          </ul>
        </div>
      )}
      {plan.unmet.length > 0 && (
        <div className="notice notice-warning">
          <h3>{t("unmet")}</h3>
          <ul>
            {plan.unmet.map((item, index) => (
              <li key={index}>{item.text}</li>
            ))}
          </ul>
        </div>
      )}
      {plan.next_actions.length > 0 && (
        <div className="actions" aria-label={t("nextActions")} role="group">
          {plan.next_actions.map((action) => (
            <button key={action} type="button" disabled={busy} onClick={() => onAction?.(action)}>
              {tAction(action)}
            </button>
          ))}
        </div>
      )}
      <Assumed plan={plan} />
      {plan.left_out_unknown_allergens.length > 0 && (
        <div>
          <p className="hint">{t("leftOut", { count: plan.left_out_unknown_allergens.length })}</p>
          <button type="button" aria-expanded={showLeftOut} onClick={() => setShowLeftOut(!showLeftOut)}>
            {t("showLeftOut")}
          </button>
          {showLeftOut && (
            <ul aria-label={t("leftOutTitles")}>
              {plan.left_out_unknown_allergens.map((title) => (
                <li key={title}>{title}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {plan.semantic_paused && <p className="notice notice-info">{t("semanticPaused")}</p>}
      <AllergenNotice />
    </section>
  );
}
