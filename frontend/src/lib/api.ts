// Values mirror backend/minemydinner/constants.py.
export const LANGUAGES = ["zh", "en"] as const;
export const DISH_TYPES = [
  "meat dish",
  "vegetable dish",
  "soup",
  "staple food",
  "snack",
  "dessert",
  "other",
] as const;
// "spoon" is a spoon whose size the source does not state.
export const UNITS = [
  "g",
  "ml",
  "piece",
  "stalk",
  "tablespoon",
  "teaspoon",
  "spoon",
] as const;
export const AMOUNT_TYPES = ["exact", "count", "spoon", "vague", "missing"] as const;
export const ROLES = ["main", "secondary", "seasoning"] as const;
export const CATEGORIES = [
  "vegetable",
  "fruit",
  "mushroom",
  "meat",
  "seafood",
  "egg and dairy",
  "soy product",
  "staple food",
  "nut",
  "seasoning",
  "other",
] as const;
export const STOCK_MODES = ["availability", "count", "level"] as const;
export const ALLERGENS = [
  "crustaceans",
  "shellfish",
  "fish",
  "egg",
  "milk",
  "peanut",
  "tree nut",
  "soy",
  "wheat",
  "sesame",
] as const;

export type Lang = (typeof LANGUAGES)[number];
export type DishType = (typeof DISH_TYPES)[number];
export type Unit = (typeof UNITS)[number];
export type AmountType = (typeof AMOUNT_TYPES)[number];
export type Role = (typeof ROLES)[number];
export type Category = (typeof CATEGORIES)[number];
export type StockMode = (typeof STOCK_MODES)[number];
export type Allergen = (typeof ALLERGENS)[number];
export type TimeSource = "stated" | "user" | "estimated";

export type IngredientLine = {
  raw: string;
  name: string;
  amount: number | null;
  unit: Unit | null;
  amount_type: AmountType;
  role: Role;
  optional: boolean;
  /** Lines sharing a value are alternatives: one of the group is required. */
  choice_group: number | null;
  ingredient_id: number | null;
  /** The linked entry's own names, for display only; the server does not read them. */
  linked_name_zh?: string | null;
  linked_name_en?: string | null;
};

export type Provenance = {
  input_text: string;
  model_output: Record<string, unknown>;
  model_name: string;
  prompt_version: string;
};

type RecipeFields = {
  lang: Lang;
  title: string;
  dish_type: DishType;
  time_min: number | null;
  time_source: TimeSource | null;
  servings: number | null;
  tags: string[];
  steps_summary: string | null;
  ingredients: IngredientLine[];
  /** Consumables such as plastic wrap: a reminder, never checked when planning. */
  supplies: string[];
  source_type: string;
  source_url: string | null;
};

export type RecipeDraft = RecipeFields & {
  cookware: { name: string; cookware_id: number | null }[];
  provenance: Provenance;
  /** How many recipes the model saw in the input; absent on drafts queued before it existed. */
  recipe_count?: number;
  /** Screenshots of a screenshot import, held in the browser until the recipe is saved. */
  images?: File[];
};

export type RecipeWrite = RecipeFields & {
  cookware_ids: number[];
  provenance: Provenance | null;
};

export type BatchItem = { index: number; draft: RecipeDraft | null; error: string | null };

export type DictionaryEntry = {
  /** How many recipes use the entry; sent by the dictionary list routes. */
  dish_count?: number;
  id: number;
  name_zh: string | null;
  name_en: string | null;
  aliases_zh: string[];
  aliases_en: string[];
  source: string;
};

export type IngredientEntry = DictionaryEntry & {
  /** The user's own wordings; only meaningful on built-in entries. */
  user_aliases_zh: string[];
  user_aliases_en: string[];
  category: Category;
  stock_mode: StockMode;
  allergens: Allergen[] | null;
  parent_id: number | null;
};

export type IngredientCreate = {
  name_zh: string | null;
  name_en: string | null;
  category: Category;
  stock_mode: StockMode;
  allergens: Allergen[] | null;
};

export type IngredientUpdate = IngredientCreate & { aliases_zh: string[]; aliases_en: string[] };

export type CookwareCreate = { name_zh: string | null; name_en: string | null; owned: boolean };

export const STOCK_LEVELS = ["plenty", "low", "none"] as const;
export const RULE_TYPES = ["allergy", "dietary restriction", "dislike"] as const;
export type StockLevel = (typeof STOCK_LEVELS)[number];
export type RuleType = (typeof RULE_TYPES)[number];

/** A draft or a saved recipe, as the review form edits it. */
export type EditableRecipe = Omit<RecipeDraft, "provenance"> & { provenance: Provenance | null };

export type SavedRecipe = RecipeFields & {
  id: number;
  cookware: { name: string; cookware_id: number | null }[];
  cookware_ids: number[];
  source_text: string | null;
  images: RecipeImage[];
  embedded: boolean;
  created_at: string;
};

export type RecipeImage = { file_name: string; mime_type: string; position: number };

export type CreatedRecipe = { id: number; embedded: boolean; log_id: number | null };

export type RecipePage = { items: SavedRecipe[]; total: number; page: number };

export type EntryRef = { id: number; name_zh: string | null; name_en: string | null };

export type InventoryRow = {
  ingredient_id: number;
  name_zh: string | null;
  name_en: string | null;
  stock_mode: StockMode;
  count: number | null;
  level: StockLevel | null;
  grams: number | null;
  expires_on: string | null;
  updated_at: string;
};

export type InventoryWrite = {
  ingredient_id: number;
  count: number | null;
  level: StockLevel | null;
  grams: number | null;
  expires_on: string | null;
};

export type Diner = { id: number; display_name: string; is_self: boolean };

export type Rule = {
  id: number;
  diner_id: number;
  rule_type: RuleType;
  allergen: Allergen | null;
  ingredient_id: number | null;
  ingredient_name_zh: string | null;
  ingredient_name_en: string | null;
  text: string | null;
};

export type RuleWrite = {
  rule_type: RuleType;
  allergen: Allergen | null;
  ingredient_id: number | null;
  text: string | null;
};

export type Unresolved = {
  entries: { id: number; name: string; dish_count: number }[];
  unmatched: { name: string; dish_count: number }[];
};

export type AllergyCheck = {
  conflicts: { line_name: string; allergen: Allergen | null; ingredient_id: number | null }[];
  unknown: string[];
};

/** An error the UI can explain through `errors.<code>`. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly suggestPaste: boolean = false,
    readonly body: Record<string, unknown> = {},
  ) {
    super(code);
  }
}

async function request<T>(path: string, init: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new ApiError(0, "network");
  }
  if (response.status === 204) {
    return undefined as T;
  }
  if (response.ok) {
    return (await response.json()) as T;
  }
  const body = (await response.json().catch(() => ({}))) as Record<string, unknown>;
  const code = typeof body.code === "string" ? body.code : "unknown";
  throw new ApiError(response.status, code, body.suggest_paste === true, body);
}

function postJson<T>(path: string, data: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

function postForm<T>(path: string, form: FormData): Promise<T> {
  return request<T>(path, { method: "POST", body: form });
}

function sendJson<T>(method: "PUT" | "PATCH", path: string, data?: unknown): Promise<T> {
  return request<T>(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: data === undefined ? undefined : JSON.stringify(data),
  });
}

const get = <T>(path: string) => request<T>(path, { method: "GET" });
const remove = (path: string) => request<void>(path, { method: "DELETE" });

function query(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") {
      search.set(key, String(value));
    }
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const api = {
  importText: (text: string) => postJson<RecipeDraft>("/api/imports/text", { text }),
  importUrl: (url: string) => postJson<RecipeDraft>("/api/imports/url", { url }),
  importScreenshots(files: File[]) {
    const form = new FormData();
    files.forEach((file) => form.append("images", file));
    return postForm<RecipeDraft>("/api/imports/screenshots", form);
  },
  importBatch(file: File) {
    const form = new FormData();
    form.append("file", file);
    return postForm<{ items: BatchItem[] }>("/api/imports/batch", form);
  },
  searchIngredients: (q: string) =>
    request<IngredientEntry[]>(`/api/ingredients?q=${encodeURIComponent(q)}`, { method: "GET" }),
  createIngredient: (data: IngredientCreate) =>
    postJson<IngredientEntry & { linked_lines: number }>("/api/ingredients", data),
  searchCookware: (q: string) =>
    request<DictionaryEntry[]>(`/api/cookware?q=${encodeURIComponent(q)}`, { method: "GET" }),
  createCookware: (data: CookwareCreate) => postJson<DictionaryEntry>("/api/cookware", data),
  suggestAllergens: (name: string) =>
    postJson<{ suggested: Allergen[] | null }>("/api/allergen-suggestions", { name }),
  checkAllergies: (lines: { name: string; ingredient_id: number | null }[]) =>
    postJson<AllergyCheck>("/api/recipes/allergy-check", { lines }),
  saveRecipe: (data: RecipeWrite) => postJson<CreatedRecipe>("/api/recipes", data),
  uploadRecipeImages(recipeId: number, files: File[], logId: number | null) {
    const form = new FormData();
    files.forEach((file) => form.append("images", file));
    if (logId !== null) form.append("log_id", String(logId));
    return postForm<RecipeImage[]>(`/api/recipes/${recipeId}/images`, form);
  },

  listRecipes: (params: { q?: string; dish_type?: string; page?: number }) =>
    get<RecipePage>(`/api/recipes${query(params)}`),
  getRecipe: (id: number) => get<SavedRecipe>(`/api/recipes/${id}`),
  updateRecipe: (id: number, data: RecipeWrite) =>
    sendJson<SavedRecipe>("PUT", `/api/recipes/${id}`, data),
  deleteRecipe: (id: number) => remove(`/api/recipes/${id}`),

  listInventory: (sort: "name" | "expiry") => get<InventoryRow[]>(`/api/inventory${query({ sort })}`),
  putInventory: (data: InventoryWrite) => sendJson<InventoryRow>("PUT", "/api/inventory", data),
  deleteInventory: (ingredientId: number) => remove(`/api/inventory/${ingredientId}`),
  listAlwaysInStock: () => get<EntryRef[]>("/api/always-in-stock"),
  addAlwaysInStock: (id: number) => sendJson<EntryRef>("PUT", `/api/always-in-stock/${id}`),
  removeAlwaysInStock: (id: number) => remove(`/api/always-in-stock/${id}`),
  listOwnedCookware: () => get<EntryRef[]>("/api/cookware-owned"),
  addOwnedCookware: (id: number) => sendJson<EntryRef>("PUT", `/api/cookware-owned/${id}`),
  removeOwnedCookware: (id: number) => remove(`/api/cookware-owned/${id}`),

  listDiners: () => get<Diner[]>("/api/diners"),
  createDiner: (displayName: string) =>
    postJson<Diner>("/api/diners", { display_name: displayName }),
  renameDiner: (id: number, displayName: string) =>
    sendJson<Diner>("PATCH", `/api/diners/${id}`, { display_name: displayName }),
  deleteDiner: (id: number) => remove(`/api/diners/${id}`),
  listRules: (dinerId: number) => get<Rule[]>(`/api/diners/${dinerId}/rules`),
  createRule: (dinerId: number, data: RuleWrite) =>
    postJson<Rule>(`/api/diners/${dinerId}/rules`, data),
  deleteRule: (id: number) => remove(`/api/rules/${id}`),

  listUserIngredients: () => get<IngredientEntry[]>("/api/ingredients?source=user"),
  listAllIngredients: () => get<IngredientEntry[]>("/api/ingredients?limit=500"),
  listAllCookware: () => get<DictionaryEntry[]>("/api/cookware?limit=500"),
  updateIngredient: (id: number, data: IngredientUpdate) =>
    sendJson<IngredientEntry>("PATCH", `/api/ingredients/${id}`, data),
  setPersonalAliases: (id: number, aliasesZh: string[], aliasesEn: string[]) =>
    sendJson<IngredientEntry & { linked_lines: number }>(
      "PUT",
      `/api/ingredients/${id}/personal-aliases`,
      { aliases_zh: aliasesZh, aliases_en: aliasesEn },
    ),
  deleteIngredient: (id: number) => remove(`/api/ingredients/${id}`),
  listUserCookware: () => get<DictionaryEntry[]>("/api/cookware?source=user"),
  deleteCookware: (id: number) => remove(`/api/cookware/${id}`),
  listUnresolved: () => get<Unresolved>("/api/allergens/unresolved"),
  resolveAllergens: (target: { name: string } | { ingredient_id: number }, allergens: Allergen[]) =>
    postJson<IngredientEntry>("/api/allergens/resolve", { ...target, allergens }),
};

export function imageUrl(fileName: string): string {
  return `/api/images/${fileName}`;
}

export function entryName(entry: DictionaryEntry, locale: string): string {
  const preferred = locale === "zh" ? entry.name_zh : entry.name_en;
  return preferred ?? entry.name_zh ?? entry.name_en ?? "";
}
