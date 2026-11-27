"use client";

import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";

import { AllergenNotice } from "@/components/AllergenNotice";
import { ResolveUnknownName } from "@/components/AllergenResolvePanels";
import { AddDish, ConfirmConflicts, type PlanEdit, useConflictText, usePlanEdit } from "@/components/PlanEditor";
import { RecipeToggles } from "@/components/RecipeToggles";
import { type ChatPlan, KNOWN_ACTIONS, type NextAction, type PlanFinding } from "@/lib/chat";
import { useErrorText } from "@/lib/errors";
import { type Conflict, MEAL_KEY } from "@/lib/planEdits";

type Props = {
  plan: ChatPlan;
  /** Called with the chosen next action; the buttons are disabled while `busy`. */
  onAction?: (action: NextAction) => void;
  /** Disables the actions and the edit controls (a run in progress, or a card or question pending). */
  busy?: boolean;
  /** False for an older plan: every action button and link action is disabled. */
  actionable?: boolean;
  /** Plans again with the same request, offered once a left-out ingredient is confirmed. */
  onReplan?: () => void;
  /** The newest card of a thread that takes edits: shows the edit controls (plan 3a). */
  editable?: boolean;
  threadId?: string | null;
  /** Gets the card an edit returned. */
  onEdited?: (plan: ChatPlan) => void;
  /** An edit was refused because the thread is busy. */
  onEditRefused?: () => void;
  /** Hears when an edit request starts (true) and ends (false). */
  onEditing?: (saving: boolean) => void;
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
  // A named count always has a bound (backend request.py _bound), hence the `!` below.
  if (spec.meat_dishes != null && stated("meat_dishes")) {
    split.push(t("askedMeat", { count: spec.meat_dishes, bound: spec.meat_bound! }));
  }
  if (spec.vegetable_dishes != null && stated("vegetable_dishes")) {
    split.push(
      t("askedVegetable", { count: spec.vegetable_dishes, bound: spec.vegetable_bound! }),
    );
  }
  if (stated("dishes") && spec.dishes > 0) {
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
  if (stated("soups") && spec.soups > 0) {
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
  if (plan.defaults_used.includes("dishes")) {
    parts.push(t("assumedDishes", { count: plan.spec.dishes }));
  }
  if (plan.defaults_used.includes("soups")) {
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
  // A hard check an edited plan allows (H1, H3, H6, H7) carries its own sentence.
  if (/^H\d+$/.test(finding.code) && typeof finding.text === "string") return [finding.text];
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

/** Whose rules the plan was checked against; none for a solo plan. */
function DinersLine({ plan }: { plan: ChatPlan }) {
  const t = useTranslations("chat.plan");
  const { diners, others } = plan;
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
  // Dishes kept against someone's rule: the line no longer says the plan follows every rule.
  // This meal's own wish is no one's rule, so a dish kept against it is not counted here.
  const kept = new Set(
    (plan.edit?.kept ?? []).filter((conflict) => conflict.diner_key !== MEAL_KEY).map((conflict) => conflict.recipe_id),
  ).size;
  if (kept > 0) text += t("dinersKept", { count: kept });
  return <p className="hint">{text}</p>;
}

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
  return (
    <li data-testid={`left-out-${name}`}>
      <span>{t("leftOutLine", { titles: titles.join(t("nameSeparator")), name })}</span>
      <ResolveUnknownName name={name} onResolved={onResolved} />
    </li>
  );
}

/**
 * Dishes left out for unknown allergens: each ingredient name can be confirmed, and then
 * planning again.
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
  const [resolved, setResolved] = useState(false);
  const details = plan.left_out_details;
  if (details.length === 0) return null;

  // Grouped by ingredient name, in order of first appearance.
  const groups = new Map<string, string[]>();
  for (const dish of details) {
    for (const line of new Set(dish.lines)) {
      const group = groups.get(line) ?? [];
      if (!group.includes(dish.title)) group.push(dish.title);
      groups.set(line, group);
    }
  }
  const count = new Set(details.map((dish) => dish.title)).size;

  return (
    <div className="left-out left-out-box">
      <h3>{t("leftOut", { count })}</h3>
      <ul aria-label={t("leftOutTitles")} className="left-out-list">
        {[...groups].map(([name, groupTitles]) => (
          <LeftOutRow key={name} name={name} titles={groupTitles} onResolved={() => setResolved(true)} />
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

/**
 * The quiet lines of an edited card: how it differs from a count the user stated, dishes
 * dropped because they left the library.
 */
function EditNotes({ plan }: { plan: ChatPlan }) {
  const t = useTranslations("chat.edit");
  const counts = plan.count_notes ?? [];
  const deleted = (plan.notes ?? []).filter((note) => note.field === "deleted_recipes" && note.titles.length > 0);
  if (counts.length === 0 && deleted.length === 0) return null;
  const bound = (field: string) =>
    (field === "meat_dishes"
      ? plan.spec.meat_bound
      : field === "vegetable_dishes"
        ? plan.spec.vegetable_bound
        : null) ?? "exact";
  return (
    <div className="plan-edit-notes">
      {counts.length > 0 && (
        <p className="hint">
          {t("countNote", {
            list: counts
              .map((note) =>
                t("countNoteItem", { field: note.field, stated: note.stated, now: note.now, bound: bound(note.field) }),
              )
              .join(t("countNoteSeparator")),
          })}
        </p>
      )}
      {deleted.map((note, index) => (
        <p key={index} className="hint">
          {t("deletedRecipes", { titles: note.titles.join(t("lineSeparator")) })}
        </p>
      ))}
    </div>
  );
}

/** Why a dish already in the plan is red (a conflict since planning), with keep; removing
 * goes through the dish's named × button. */
function DishConflicts({
  recipeId,
  conflicts,
  edit,
  controls,
  disabled,
}: {
  recipeId: number;
  conflicts: Conflict[];
  edit: PlanEdit;
  controls: boolean;
  disabled: boolean;
}) {
  const t = useTranslations("chat.edit");
  const text = useConflictText();
  return (
    <div className="plan-dish-reasons">
      {conflicts.map((conflict, index) => (
        <p key={index}>{text.reason(conflict)}</p>
      ))}
      {controls && (
        <div className="actions">
          <button type="button" disabled={disabled} onClick={() => void edit.keep(recipeId, conflicts)}>
            {t("keep")}
          </button>
        </div>
      )}
      {controls && edit.asking?.action === "keep" && edit.asking.recipeId === recipeId && (
        <ConfirmConflicts
          asking={edit.asking}
          disabled={disabled}
          onConfirm={() => void edit.confirm()}
          onCancel={edit.cancel}
          onRecheck={() => void edit.keep(recipeId, [])}
        />
      )}
    </div>
  );
}

export function PlanCard({
  plan,
  onAction,
  onReplan,
  busy = false,
  actionable = true,
  editable = false,
  threadId = null,
  onEdited,
  onEditRefused,
  onEditing,
}: Props) {
  const t = useTranslations("chat.plan");
  const tEdit = useTranslations("chat.edit");
  const errorText = useErrorText();
  const edit = usePlanEdit({
    threadId: threadId ?? "",
    onEdited: (next) => onEdited?.(next),
    onRefused: onEditRefused,
    onEditing,
  });
  // Edit controls show on the newest card of a thread that takes edits; busy disables them.
  const controls = editable && threadId !== null && onEdited !== undefined;
  const editDisabled = busy || edit.saving;
  const conflicts = plan.edit?.conflicts ?? [];
  const kept = plan.edit?.kept ?? [];
  const conflictText = useConflictText();
  // After a dish's own remove button disappears with it, focus goes to "+ Add a dish" once
  // the controls are enabled again.
  const addRef = useRef<HTMLButtonElement | null>(null);
  const [removed, setRemoved] = useState(0);
  const focused = useRef(0);
  useEffect(() => {
    if (removed !== focused.current && !editDisabled && addRef.current) {
      focused.current = removed;
      addRef.current.focus();
    }
  }, [removed, editDisabled]);
  async function removeFromRow(recipeId: number) {
    if (await edit.remove(recipeId)) setRemoved((count) => count + 1);
  }
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
  // "+ Add a dish" is the last row of the dish list; with no dishes it follows the reasons,
  // so a reply without a plan explains first, then offers building one.
  const addRow = controls ? (
    <>
      <AddDish
        threadId={threadId as string}
        edit={edit}
        disabled={busy}
        planIds={plan.recipes.map((recipe) => recipe.id)}
        buttonRef={addRef}
      />
      {edit.error !== null && (
        <p role="alert" className="notice notice-warning">
          {errorText(edit.error)}
        </p>
      )}
    </>
  ) : null;
  const hasDishes = plan.recipes.length > 0;

  return (
    <section className="card plan-card">
      <div className="plan-head">
        <h2>{t("title")}</h2>
        {plan.edited && <span className="hint">{tEdit("edited")}</span>}
        {plan.edited && controls && (
          <button type="button" className="btn-text" disabled={editDisabled} onClick={() => void edit.restore()}>
            {tEdit("restore")}
          </button>
        )}
      </div>
      {plan.recipes.length > 0 && (
        <ul className="card-list">
          {plan.recipes.map((recipe) => {
            const missing = plan.missing[String(recipe.id)] ?? [];
            const cookware = plan.missing_cookware[String(recipe.id)] ?? [];
            const dishConflicts = conflicts.filter((conflict) => conflict.recipe_id === recipe.id);
            const dishKept = kept.filter((conflict) => conflict.recipe_id === recipe.id);
            return (
              <li
                key={recipe.id}
                className={dishConflicts.length > 0 ? "plan-dish plan-dish-conflict" : "plan-dish"}
              >
                <RecipeToggles
                  recipeId={recipe.id}
                  title={<strong>{recipe.title}</strong>}
                  missing={missing}
                  actions={
                    controls ? (
                      <button
                        type="button"
                        className="plan-remove"
                        aria-label={tEdit("removeDish", { title: recipe.title })}
                        disabled={editDisabled}
                        onClick={() => void removeFromRow(recipe.id)}
                      >
                        <span aria-hidden="true">×</span>
                      </button>
                    ) : undefined
                  }
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
                  {dishKept.map((conflict, index) => (
                    <p key={`kept-${index}`} className="hint">
                      {conflictText.kept(conflict)}
                    </p>
                  ))}
                  {dishConflicts.length > 0 && (
                    <DishConflicts
                      recipeId={recipe.id}
                      conflicts={dishConflicts}
                      edit={edit}
                      controls={controls}
                      disabled={editDisabled}
                    />
                  )}
                </RecipeToggles>
              </li>
            );
          })}
        </ul>
      )}
      {hasDishes && addRow}
      {showNoDishes && <p className="hint">{t("noDishes")}</p>}
      {conflicts.length > 0 && <p className="notice notice-warning">{tEdit("unresolved")}</p>}
      <EditNotes plan={plan} />
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
      {!hasDishes && addRow}
      {/* Warnings, servings notes, the search pause, then the left-out dishes: one light
          block, its parts divided by a rule; empty when there is nothing to say. */}
      <div className="plan-notes">
        {showInfo && (
          <div className="plan-info">
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
                    // More people than the recipe serves: scale up; fewer: scale down.
                    direction: note.headcount > note.servings ? "up" : "down",
                  })}
                </li>
              ))}
              {plan.semantic_paused && <li>{t("semanticPaused")}</li>}
            </ul>
          </div>
        )}
        <LeftOutDishes plan={plan} onReplan={onReplan} replanDisabled={busy || !actionable} />
      </div>
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
