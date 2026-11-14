import type { ChatPlan } from "@/lib/chat";

/** A passing one-dish plan; override fields per test. */
export function plan(fields: Partial<ChatPlan> = {}): ChatPlan {
  return {
    recipes: [
      { id: 1, title: "葱花蛋", dish_type: "vegetable dish", time_min: 10, time_source: "stated", servings: 2 },
    ],
    missing: {},
    missing_cookware: {},
    warnings: [],
    unmet: [],
    next_actions: [],
    defaults_used: [],
    spec: { mode: "fridge_only", dishes: 1, soups: 0, time_limit_min: null },
    headcount: 1,
    servings_notes: [],
    left_out_unknown_allergens: [],
    semantic_paused: false,
    passed: true,
    ...fields,
  };
}
