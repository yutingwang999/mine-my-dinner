import type { Allergen, DishType, RuleType } from "@/lib/api";
import { type ChatPlan, type Locale, normalizePlan, post } from "@/lib/chat";

/**
 * One H2 conflict of a dish (backend agent/edits.py `Conflict`). Diners are named by display
 * name; the user is `is_self` with no name; an unnamed guest (or a friend deleted since) has
 * neither. `lines` are the unknown-allergen lines, or the recipe lines that hit the rule.
 */
export type Conflict = {
  recipe_id: number;
  diner_key: string;
  diner_name: string | null;
  is_self: boolean;
  kind: "allergen" | "ingredient" | "text" | "unknown_allergens";
  allergen: Allergen | null;
  ingredient_id: number | null;
  ingredient_name: string | null;
  text: string | null;
  rule_type: RuleType | null;
  lines: string[];
};

/** The `diner_key` of this meal's own wish ("今天不想吃肉"): never a person (plan 3b-1b spec 5.4). */
export const MEAL_KEY = "meal";

/** A diner's dislike a search result touches, named like a conflict. */
export type SearchDislike = { diner_key: string; diner_name: string | null; is_self: boolean; line: string };

/** One result of the card's search (backend api/plan_edits.py). */
export type SearchHit = {
  id: number;
  title: string;
  dish_type: DishType;
  time_min: number | null;
  missing: string[];
  missing_cookware: string[];
  /** The same conflicts adding the dish asks to confirm. */
  conflicts: Conflict[];
  /** Not shown: unknown allergens reach the UI as `unknown_allergens` entries of `conflicts`. */
  blocked_unknown: string[];
  dislikes: SearchDislike[];
  in_plan: boolean;
};

/** A plain text match of the library (no model call), at most 10 hits. */
export type SearchResult = { hits: SearchHit[] };

const base = (threadId: string) => `/api/chat/threads/${threadId}/plan`;

async function edit(threadId: string, action: string, body: Record<string, unknown>): Promise<ChatPlan> {
  const response = await post(`${base(threadId)}/${action}`, body);
  return normalizePlan(((await response.json()) as { plan: ChatPlan }).plan);
}

export async function searchPlan(threadId: string, query: string, locale: Locale): Promise<SearchResult> {
  return (await (await post(`${base(threadId)}/search`, { query, locale })).json()) as SearchResult;
}

export function removeDish(threadId: string, recipeId: number, locale: Locale): Promise<ChatPlan> {
  return edit(threadId, "remove", { recipe_id: recipeId, locale });
}

/** Adds a dish; a conflict not in `confirm` is refused with 409 `needs_confirmation`. */
export function addDish(
  threadId: string,
  recipeId: number,
  confirm: Conflict[],
  locale: Locale,
): Promise<ChatPlan> {
  return edit(threadId, "add", { recipe_id: recipeId, confirm, locale });
}

/** Keeps a dish that gained a conflict since planning, confirming its conflicts. */
export function keepDish(
  threadId: string,
  recipeId: number,
  confirm: Conflict[],
  locale: Locale,
): Promise<ChatPlan> {
  return edit(threadId, "keep", { recipe_id: recipeId, confirm, locale });
}

export function restorePlan(threadId: string, locale: Locale): Promise<ChatPlan> {
  return edit(threadId, "restore", { locale });
}
