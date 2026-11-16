"use client";

import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";

import { AllergenNotice } from "@/components/AllergenNotice";
import { RecipeToggles } from "@/components/RecipeToggles";
import { type ChatPlan, KNOWN_ACTIONS, type NextAction, type PlanFinding } from "@/lib/chat";

type Props = {
  plan: ChatPlan;
  /** Called with the chosen next action; the buttons are disabled while `busy`. */
  onAction?: (action: NextAction) => void;
  busy?: boolean;
  /** False for an older plan: every action button and link action is disabled. */
  actionable?: boolean;
};

const LINK_ACTIONS: Partial<Record<NextAction, string>> = {
  update_inventory: "/inventory",
  register_cookware: "/inventory?tab=cookware",
  import_recipes: "/import",
};

/** What the user stated: the spec fields that were not filled by a default. */
function Asked({ plan }: { plan: ChatPlan }) {
  const t = useTranslations("chat.plan");
  const { spec, defaults_used: defaults } = plan;
  const stated = (name: string) => !defaults.includes(name);
  const items: string[] = [];
  // The user's own bounds, not the tightened ranges the planner reads (design 5.11).
  const split: string[] = [];
  if (spec.meat_dishes != null && stated("meat_dishes")) {
    split.push(t("askedMeat", { count: spec.meat_dishes, bound: spec.meat_bound ?? "exact" }));
  }
  if (spec.vegetable_dishes != null && stated("vegetable_dishes")) {
    split.push(
      t("askedVegetable", { count: spec.vegetable_dishes, bound: spec.vegetable_bound ?? "exact" }),
    );
  }
  // An old record's "composition" stood for both counts, so neither was stated.
  if (stated("dishes") && !defaults.includes("composition") && spec.dishes > 0) {
    const dishes = t("askedDishes", { count: spec.dishes });
    items.push(
      split.length > 0
        ? t("askedDishesSplit", { dishes, split: split.join(t("askedSplitSeparator")) })
        : dishes,
    );
  } else {
    // An assumed total ("三个人，最多一个素菜"): the named sides are still what the user said.
    items.push(...split);
  }
  if (stated("soups") && !defaults.includes("composition") && spec.soups > 0) {
    items.push(t("askedSoups", { count: spec.soups }));
  }
  for (const [name, key] of [
    ["staples", "askedStaples"],
    ["desserts", "askedDesserts"],
    ["snacks", "askedSnacks"],
  ] as const) {
    if (stated(name) && spec[name] > 0) items.push(t(key, { count: spec[name] }));
  }
  if (stated("mode")) items.push(t(spec.mode === "shopping" ? "askedShopping" : "askedFridgeOnly"));
  if (spec.time_limit_min !== null) items.push(t("askedTime", { minutes: spec.time_limit_min }));
  for (const [list, key] of [
    [spec.prefer_use, "askedPrefer"],
    [spec.must_use, "askedMust"],
    [spec.all_use, "askedAll"],
  ] as const) {
    if (list.length > 0) items.push(t(key, { list: list.join(", ") }));
  }
  if (items.length === 0) return null;
  return <p className="hint">{t("asked", { list: items.join(t("listSeparator")) })}</p>;
}

function Assumed({ plan }: { plan: ChatPlan }) {
  const t = useTranslations("chat.plan");
  const parts: string[] = [];
  if (plan.defaults_used.includes("headcount")) {
    parts.push(t("assumedHeadcount", { count: plan.headcount }));
  }
  // An old record may carry "composition", which stood for both counts.
  const legacy = plan.defaults_used.includes("composition");
  if (legacy || plan.defaults_used.includes("dishes")) {
    parts.push(t("assumedDishes", { count: plan.spec.dishes }));
  }
  if (legacy ? plan.spec.soups > 0 : plan.defaults_used.includes("soups")) {
    parts.push(t("assumedSoups", { count: plan.spec.soups }));
  }
  for (const [name, key] of [
    ["staples", "assumedStaples"],
    ["desserts", "assumedDesserts"],
    ["snacks", "assumedSnacks"],
  ] as const) {
    if (plan.defaults_used.includes(name) && plan.spec[name] > 0) {
      parts.push(t(key, { count: plan.spec[name] }));
    }
  }
  if (plan.defaults_used.includes("meat_dishes") && plan.spec.meat_dishes != null) {
    parts.push(t("assumedMeat", { count: plan.spec.meat_dishes }));
  }
  if (plan.defaults_used.includes("vegetable_dishes") && plan.spec.vegetable_dishes != null) {
    parts.push(t("assumedVegetable", { count: plan.spec.vegetable_dishes }));
  }
  if (plan.defaults_used.includes("mode")) {
    parts.push(t(plan.spec.mode === "shopping" ? "assumedShopping" : "assumedFridgeOnly"));
  }
  if (parts.length === 0) return null;
  return <p className="hint">{t("assumed", { list: parts.join(t("listSeparator")) })}</p>;
}

function unitLabel(tUnit: ReturnType<typeof useTranslations>, unit: string): string {
  return tUnit.has(unit) ? tUnit(unit) : unit;
}

function warningText(
  t: ReturnType<typeof useTranslations>,
  tUnit: ReturnType<typeof useTranslations>,
  finding: PlanFinding,
  titles: Map<number, string>,
): string[] {
  const d = finding.detail;
  const title = finding.recipe_id === null ? "" : (titles.get(finding.recipe_id) ?? "");
  const text = (key: string, values: Record<string, string | number> = {}) =>
    t(key, { title, ...values });
  switch (finding.code) {
    case "S1": {
      // One finding per ingredient across dishes; `parts` says what each dish needs.
      const parts = Array.isArray(d.parts)
        ? (d.parts as { recipe_id: number; amount: number }[])
        : [{ recipe_id: finding.recipe_id ?? -1, amount: Number(d.needed) }];
      const list = parts.map((part) => `${titles.get(part.recipe_id) ?? ""} ${part.amount}`.trim()).join(" + ");
      const unit = unitLabel(tUnit, String(d.unit));
      return [text("S1", { name: String(d.name), parts: list, needed: Number(d.needed), have: Number(d.have), unit })];
    }
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

export function PlanCard({ plan, onAction, busy = false, actionable = true }: Props) {
  const t = useTranslations("chat.plan");
  const tWarning = useTranslations("chat.warning");
  const tAction = useTranslations("chat.action");
  const tDish = useTranslations("dishType");
  const tSource = useTranslations("timeSource");
  const tUnit = useTranslations("unit");
  const [showLeftOut, setShowLeftOut] = useState(false);

  const titles = new Map(plan.recipes.map((recipe) => [recipe.id, recipe.title]));
  const locale = useLocale();
  const actions = plan.next_actions.filter((action) => KNOWN_ACTIONS.includes(action));
  const reasonTexts = [...new Set(plan.reasons.map((reason) => reason.text))];
  const warnings = plan.warnings.flatMap((finding) => warningText(tWarning, tUnit, finding, titles));

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
                <RecipeToggles
                  recipeId={recipe.id}
                  title={<strong>{recipe.title}</strong>}
                  missing={missing}
                >
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
                </RecipeToggles>
              </li>
            );
          })}
        </ul>
      )}
      {/* What was asked and assumed: its own block, not part of the last dish. */}
      <div className="plan-conditions">
        <Asked plan={plan} />
        <Assumed plan={plan} />
      </div>
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
      {reasonTexts.length > 0 && (
        <div className="notice notice-warning">
          <h3>{t("reasons")}</h3>
          <ul>
            {reasonTexts.map((text) => (
              <li key={text}>{text}</li>
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
      {actions.length > 0 && (
        <div className="actions" aria-label={t("nextActions")} role="group">
          {actions.map((action) => {
            const href = LINK_ACTIONS[action];
            return href ? (
              actionable ? (
                <Link key={action} href={`/${locale}${href}`}>
                  {tAction(action)}
                </Link>
              ) : (
                <span key={action} aria-disabled="true" className="hint">
                  {tAction(action)}
                </span>
              )
            ) : (
              <button key={action} type="button" disabled={busy || !actionable} onClick={() => onAction?.(action)}>
                {tAction(action)}
              </button>
            );
          })}
        </div>
      )}
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
