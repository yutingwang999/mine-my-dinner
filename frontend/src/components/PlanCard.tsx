"use client";

import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";

import { AllergenNotice } from "@/components/AllergenNotice";
import { ConfirmPanel, type LinkNotice, LinkPanel, type LinkResult } from "@/components/AllergenResolvePanels";
import { RecipeToggles } from "@/components/RecipeToggles";
import { api } from "@/lib/api";
import { type ChatPlan, KNOWN_ACTIONS, type NextAction, type PlanFinding } from "@/lib/chat";
import { useErrorText } from "@/lib/errors";

type Props = {
  plan: ChatPlan;
  /** Called with the chosen next action; the buttons are disabled while `busy`. */
  onAction?: (action: NextAction) => void;
  busy?: boolean;
  /** False for an older plan: every action button and link action is disabled. */
  actionable?: boolean;
  /** Plans again with the same request, offered once a left-out ingredient is confirmed. */
  onReplan?: () => void;
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
  tDish: ReturnType<typeof useTranslations>,
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
      // Counts only meat and vegetable dishes, so say how many and which type.
      return [text("S5", { count: Number(d.count), dishType: tDish(String(d.dish_type)) })];
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

/** Whose rules the plan was checked against; none for a solo plan or one saved before 2b. */
function DinersLine({ plan }: { plan: ChatPlan }) {
  const t = useTranslations("chat.plan");
  const diners = plan.diners ?? [];
  const others = plan.others ?? 0;
  if (!diners.some((diner) => !diner.is_self) && others === 0) return null;
  // Named people one by one; guests without a name counted together ("3 位客人").
  const unnamed = diners.filter((diner) => !diner.is_self && !diner.name).length;
  const parts = diners
    .filter((diner) => diner.is_self || diner.name)
    .map((diner) => (diner.is_self ? t("dinerSelf") : (diner.name as string)));
  if (unnamed > 0) parts.push(t("dinerGuests", { count: unnamed }));
  const names = parts.join(t("nameSeparator"));
  let text: string;
  if (diners.length === 0) text = t("dinersOthersOnly", { count: others });
  else if (others > 0) text = t("dinersLineOthers", { names, count: others });
  else text = t("dinersLine", { names });
  return <p className="hint">{text}</p>;
}

type RowState =
  | { kind: "idle" }
  | { kind: "checking" }
  /** A name with no dictionary entry: link it, or create an entry with its allergens. */
  | { kind: "link" }
  | { kind: "create" }
  /** An entry whose allergens are unknown. */
  | { kind: "entry"; id: number }
  /**
   * The name matches an entry whose allergens are known, so the dish's line is not linked
   * to it (an unknown-allergen line is unlinked or on an entry with unknown allergens).
   */
  | { kind: "known" }
  /** A link found no unlinked line with this name: nothing changed for the dish. */
  | { kind: "unchanged" }
  | { kind: "done" };

/** One ingredient name with the dishes it left out, and the way to confirm its allergens. */
function LeftOutRow({
  name,
  titles,
  onResolved,
}: {
  name: string;
  titles: string[];
  onResolved: () => void;
}) {
  const t = useTranslations("chat.plan");
  const tDictionary = useTranslations("dictionary");
  const errorText = useErrorText();
  const [state, setState] = useState<RowState>({ kind: "idle" });
  const [error, setError] = useState<unknown>(null);
  // Shown when a link was asked to be remembered but the name belongs to another entry.
  const [linkNotice, setLinkNotice] = useState<LinkNotice | null>(null);
  const idle = () => setState({ kind: "idle" });
  const done = () => {
    setState({ kind: "done" });
    onResolved();
  };
  // A link fixes the dish only when it linked a line to an entry whose allergens are known.
  const linked = (notice: LinkNotice | null, result: LinkResult) => {
    setLinkNotice(notice);
    if (result.linked_lines === 0) setState({ kind: "unchanged" });
    else if (result.allergens === null) setState({ kind: "entry", id: result.id });
    else done();
  };

  async function check() {
    setState({ kind: "checking" });
    setError(null);
    try {
      // The dictionary decides the panel: no entry, an entry with unknown allergens, or known ones.
      const { matches } = await api.matchIngredients([name]);
      const entry = matches.find((match) => match.name === name)?.entry;
      if (entry === undefined) {
        setState({ kind: "link" });
      } else if (entry.allergens === null) {
        setState({ kind: "entry", id: entry.id });
      } else {
        setState({ kind: "known" });
      }
    } catch (caught) {
      setError(caught);
      idle();
    }
  }

  return (
    <li data-testid={`left-out-${name}`}>
      <span>{t("leftOutLine", { titles: titles.join(t("nameSeparator")), name })}</span>
      {state.kind === "idle" && (
        <span className="actions">
          <button type="button" onClick={check}>
            {t("leftOutConfirm")}
          </button>
        </span>
      )}
      <div className="left-out-panel">
      {state.kind === "checking" && <p className="hint">{t("leftOutChecking")}</p>}
      {error !== null && (
        <p role="alert" className="notice notice-warning">
          {errorText(error)}
        </p>
      )}
      {state.kind === "link" && (
        <LinkPanel
          name={name}
          onCancel={idle}
          onResolved={linked}
          switchTo={
            <button type="button" className="btn-text" onClick={() => setState({ kind: "create" })}>
              {t("leftOutCreateInstead")}
            </button>
          }
        />
      )}
      {state.kind === "create" && (
        <ConfirmPanel
          name={name}
          target={{ name }}
          onCancel={idle}
          onResolved={done}
          switchTo={
            <button type="button" className="btn-text" onClick={() => setState({ kind: "link" })}>
              {t("leftOutLinkInstead")}
            </button>
          }
        />
      )}
      {state.kind === "entry" && (
        <ConfirmPanel
          name={name}
          target={{ ingredient_id: state.id }}
          onCancel={idle}
          onResolved={done}
        />
      )}
      {state.kind === "known" && (
        <>
          <p role="status">{t("leftOutKnown")}</p>
          <LinkPanel name={name} onCancel={idle} onResolved={linked} />
        </>
      )}
      {state.kind === "unchanged" && <p role="status">{t("leftOutNothingLinked")}</p>}
      {state.kind === "done" && <p role="status">{t("leftOutConfirmed")}</p>}
      {linkNotice !== null && (state.kind === "done" || state.kind === "entry") && (
        <p className="notice notice-info">{tDictionary("linkNotRemembered", linkNotice)}</p>
      )}
      </div>
    </li>
  );
}

/**
 * Dishes left out for unknown allergens. A plan saved before their ingredient names were
 * kept lists titles only; otherwise each name can be confirmed, and then planning again.
 */
function LeftOutDishes({
  plan,
  onReplan,
  replanDisabled,
}: {
  plan: ChatPlan;
  onReplan?: () => void;
  replanDisabled: boolean;
}) {
  const t = useTranslations("chat.plan");
  const [showTitles, setShowTitles] = useState(false);
  const [resolved, setResolved] = useState(false);
  const titles = plan.left_out_unknown_allergens;
  const details = plan.left_out_details ?? [];
  if (titles.length === 0 && details.length === 0) return null;

  // Grouped by ingredient name, in order of first appearance.
  const groups = new Map<string, string[]>();
  for (const dish of details) {
    for (const line of new Set(dish.lines)) {
      const group = groups.get(line) ?? [];
      if (!group.includes(dish.title)) group.push(dish.title);
      groups.set(line, group);
    }
  }
  const grouped = new Set([...groups.values()].flat());
  const count = new Set([...titles, ...details.map((dish) => dish.title)]).size;

  if (groups.size === 0) {
    return (
      <div className="left-out-box">
        <h3>{t("leftOut", { count })}</h3>
        <span className="dish-toggles">
          <button type="button" aria-expanded={showTitles} onClick={() => setShowTitles(!showTitles)}>
            {t("showLeftOut")}
          </button>
        </span>
        {showTitles && (
          <ul aria-label={t("leftOutTitles")}>
            {titles.map((title) => (
              <li key={title}>{title}</li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  return (
    <div className="left-out left-out-box">
      <h3>{t("leftOut", { count })}</h3>
      <ul aria-label={t("leftOutTitles")} className="left-out-list">
        {[...groups].map(([name, groupTitles]) => (
          <LeftOutRow key={name} name={name} titles={groupTitles} onResolved={() => setResolved(true)} />
        ))}
        {/* Titles kept without their ingredient names (an older reply in the same thread). */}
        {titles
          .filter((title) => !grouped.has(title))
          .map((title) => (
            <li key={`title-${title}`}>
              <span>{title}</span>
            </li>
          ))}
      </ul>
      {resolved && onReplan && (
        <div className="actions">
          <button type="button" className="btn-primary" disabled={replanDisabled} onClick={onReplan}>
            {t("replan")}
          </button>
        </div>
      )}
    </div>
  );
}

export function PlanCard({ plan, onAction, onReplan, busy = false, actionable = true }: Props) {
  const t = useTranslations("chat.plan");
  const tWarning = useTranslations("chat.warning");
  const tAction = useTranslations("chat.action");
  const tDish = useTranslations("dishType");
  const tSource = useTranslations("timeSource");
  const tUnit = useTranslations("unit");

  const titles = new Map(plan.recipes.map((recipe) => [recipe.id, recipe.title]));
  const locale = useLocale();
  const actions = plan.next_actions.filter((action) => KNOWN_ACTIONS.includes(action));
  const reasonTexts = [...new Set(plan.reasons.map((reason) => reason.text))];
  const warnings = plan.warnings.flatMap((finding) => warningText(tWarning, tUnit, finding, titles, tDish));

  const redBoxes = [
    reasonTexts.length > 0 ? "reasons" : null,
    plan.unmet.length > 0 ? "unmet" : null,
  ].filter((box) => box !== null);
  const lastRed = redBoxes[redBoxes.length - 1];

  const actionsRow =
    actions.length > 0 ? (
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
    ) : null;

  const showInfo = warnings.length > 0 || plan.servings_notes.length > 0 || plan.semantic_paused;
  // The card is never empty: with no dishes and nothing explaining why, say so.
  const showNoDishes = plan.recipes.length === 0 && redBoxes.length === 0;

  return (
    <section className="card plan-card">
      <h2>{t("title")}</h2>
      {plan.recipes.length > 0 && (
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
      {showNoDishes && <p className="hint">{t("noDishes")}</p>}
      {reasonTexts.length > 0 && (
        <div className="notice notice-warning">
          <h3>{t("reasons")}</h3>
          <ul>
            {reasonTexts.map((text) => (
              <li key={text}>{text}</li>
            ))}
          </ul>
          {lastRed === "reasons" && actionsRow}
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
          {lastRed === "unmet" && actionsRow}
        </div>
      )}
      {lastRed === undefined && actionsRow}
      {showInfo && (
        <div className="notice notice-info">
          {/* Warnings, servings notes and the search pause: one list, one style. */}
          <h3>{t("warnings")}</h3>
          <ul>
            {warnings.map((text, index) => (
              <li key={`warning-${index}`}>{text}</li>
            ))}
            {plan.servings_notes.map((note) => (
              <li key={`servings-${note.recipe_id}`}>
                {t("servingsNote", {
                  title: titles.get(note.recipe_id) ?? "",
                  servings: note.servings,
                  headcount: note.headcount,
                })}
              </li>
            ))}
            {plan.semantic_paused && <li>{t("semanticPaused")}</li>}
          </ul>
        </div>
      )}
      <LeftOutDishes plan={plan} onReplan={onReplan} replanDisabled={busy || !actionable} />
      {/* What was asked and assumed, then the allergen notice: one quiet footer. */}
      <footer className="plan-footer">
        <div className="plan-conditions">
          <Asked plan={plan} />
          <Assumed plan={plan} />
          <DinersLine plan={plan} />
        </div>
        <AllergenNotice />
      </footer>
    </section>
  );
}
