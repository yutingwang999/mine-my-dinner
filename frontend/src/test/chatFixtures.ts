import type { ChatPlan } from "@/lib/chat";

/** A plan spec with nothing named; override fields per test. */
export function spec(fields: Partial<ChatPlan["spec"]> = {}): ChatPlan["spec"] {
  return {
    mode: "fridge_only",
    dishes: 1,
    soups: 0,
    meat_dishes: null,
    vegetable_dishes: null,
    meat_bound: null,
    vegetable_bound: null,
    staples: 0,
    desserts: 0,
    snacks: 0,
    time_limit_min: null,
    must_use: [],
    all_use: [],
    prefer_use: [],
    ...fields,
  };
}

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
    reasons: [],
    next_actions: [],
    defaults_used: [],
    spec: spec(),
    headcount: 1,
    servings_notes: [],
    left_out_unknown_allergens: [],
    semantic_paused: false,
    passed: true,
    ...fields,
  };
}
