# MineMyDinner Design Document

**MineMyDinner — your AI dinner agent**

Dig tonight's dinner out of your saved recipes and your fridge.

> **Status: in planning; implementation has not started (2026-11-01).** Open items are listed in Section 10.
>
> **Naming:** always write the project name as MineMyDinner, never in all lowercase. The repository name is `mine-my-dinner`.

---

## 1. Product Scope

**Target users:** people who have saved many cooking videos and written recipes, but cannot think of what to cook or find a recipe when it is time to cook.

**Core features:**

1. Extract a dish's ingredients from web pages, screenshots, and text, then store the dish in a personal recipe library with its **original source link**. Video import is planned for Phase 3.
2. Record the ingredients, seasonings, and cookware the user has.
3. Generate a meal plan from the user's request, either **using only what is in the fridge** or **allowing shopping**.
4. Batch import text descriptions of dishes from a text file.
5. Record taste preferences, dietary restrictions, dish ratings, and dish notes.
6. Support ingredient substitution: the model suggests and the user decides.
7. Support manual entry of a dish name and its ingredients.
8. Support meals for several people: confirm who is eating each time and combine their allergies and dietary restrictions. The user decides whether to save friends' information.

**Engineering focus:** model-driven tool calling, long-term memory, multi-model comparison, a self-tuned and self-deployed extraction model, constraints validated by code, and measurable evaluation.

---

## 2. Phases

**Principle:** simple data acquisition comes first, because everything else depends on having data in the library. Complex video acquisition comes last, because it takes the most work and platform restrictions are significant, so it is run as a comparison experiment.

| Phase | Estimate | Scope |
|---|---|---|
| **Phase 0: Data foundation** | 11-14.5 working days | Database design; simple import (pasted text, web page text, screenshots, batch text files); structured extraction through APIs; **evaluation set collection starts here** |
| **Phase 1: Agent** | 16.5-20.5 working days | LangGraph orchestration; tool calling; fridge-only and shopping-allowed modes; multi-turn plan revision; long-term memory; ratings; ingredient substitution; manual entry; recommendation ranking; meals for several people; planning evaluation |
| **Phase 2: Model** | Not yet broken down | Complete the evaluation set (100-200 human-labeled items); synthesize and quality-check SFT data (2,000-5,000 items); QLoRA tuning for 1.5B and 3B, optionally 7B; vLLM deployment; comparison against the API baseline. Check the model provider's terms before synthesizing data |
| **Phase 3: Extensions** | Prioritized by available time | Video import (approach comparison); MCP server so DeepSeek, OpenAI, and Gemini share one set of tools; cloud deployment, with a separately deployed demo that uses its own sample database (see 7.6), is reachable through a direct link, and is protected by access limits and call quotas; browser extension; ingredient synonyms and substitution relationships (optional knowledge graph) |

---

## 3. Technology Choices

| Area | Choice |
|---|---|
| Agent orchestration | **LangGraph** |
| LLM APIs | **DeepSeek, OpenAI, and Gemini**, connected through an extensible model factory (see 3.1) |
| Frontend | **Next.js / React** web app |
| Database | **PostgreSQL + pgvector**: business data, vector search, and the LangGraph checkpointer and Store share one database |
| Backend | Python (FastAPI) |
| Fine-tuning | QLoRA; LLaMA-Factory or Unsloth (built on PEFT, TRL, and bitsandbytes) |
| Inference | vLLM (OpenAI-compatible API; supports output constrained by JSON Schema) |
| Experiment tracking | Weights & Biases or TensorBoard |
| Labeling | Label Studio or Argilla (a spreadsheet and scripts are fine at small volumes) |
| Development process | Claude Code + Superpowers (planning, TDD, systematic debugging, subagent execution, code review) |
| Repository | Start as a private GitHub repository and make it public after Phase 1. From the first commit, `.gitignore` excludes `.env`, and only `.env.example` is committed. Before going public, check the full history for secrets and add a README, architecture diagram, and LICENSE |
| Languages | **Chinese and English**: switchable UI (such as next-intl); recipes in both languages; the ingredient dictionary holds Chinese and English names and aliases in both languages; each recipe records its original language |
| Embeddings | **Always use Gemini embeddings** (`gemini-embedding-001`), which are multilingual and allow Chinese and English content to be searched across languages. There is no embedding-model failover; failed calls are retried or queued. The pgvector HNSW index limits `vector` columns to about 2000 dimensions, so set the output dimension to 768 or 1536 before creating tables (follow the official documentation). Changing models requires recomputing all vectors |
| API costs | **Monthly cap**: set budget alerts on each provider. Code records the tokens, cost, and latency of each call in `llm_calls`, and stops non-essential calls once the monthly cap is exceeded |
| Data storage | **No evaluation data or local development recipe data is uploaded to the Git repository.** It stays in the local database or in `data/`, which `.gitignore` excludes. Source text taken from other people's web pages or videos is limited in quantity and never published; prefer self-written or licensed content. The repository holds only evaluation scripts and code. The exception is small fictional data hand-written for unit tests, which does not come from real recipes |

### 3.1 Multi-Model Design

- **Unified call layer:** built on LangChain `init_chat_model`.
- **Model factory:** business code asks the factory for a model by task and does not depend on a specific provider. Adding a provider only requires registering a builder function and adding configuration, with no business-code changes.
- **Capability registry:** each model's capabilities (tool calling, strict JSON, images, video, speech-to-text, embeddings) are registered in a configuration file, and the system selects models by the capabilities a task requires.
- **Per-task model configuration:** extraction, planning, image understanding, allergen suggestions, and video understanding are configured independently. Embeddings always use Gemini.
- **Automatic failover:** when the primary model fails or times out, the system switches to the fallback model without a configuration change or restart. Each switch is recorded in `llm_calls.fallback_used`. Embeddings do not fail over.
- **Self-hosted models:** the tuned Qwen connects through vLLM's OpenAI-compatible API and is treated like any other provider, so comparison experiments only require a configuration change.

**Provider capabilities (verify exact models, prices, and limits in the official documentation before use):**

| Capability | OpenAI | Gemini | DeepSeek | Qwen (Bailian) | Self-hosted Qwen (vLLM) |
|---|---|---|---|---|---|
| Tool calling | ✅ | ✅ | ✅ | ✅ | ✅ (depends on the model and parser) |
| Structured output | ✅ strict JSON Schema | ✅ | ⚠ JSON mode; schema conformance is not guaranteed | ✅ | ✅ constrained decoding |
| Images | ✅ | ✅ | ❌ (text-only API) | ✅ | Depends on the model |
| Direct video understanding | ❌ (needs frame sampling) | ✅ (includes links from some video platforms) | ❌ | ✅ | — |
| Speech-to-text | ✅ | ✅ | ❌ | ✅ | — |
| Embeddings | ✅ | ✅ | ❌ | ✅ | Can be deployed separately |
| OpenAI-compatible API | — | ✅ | ✅ | ✅ | ✅ |
| Relative price | Medium | Low to medium | Very low | Low | GPU cost only |

**Default assignment (Phases 0 and 1):**

| Task | Primary | Fallback |
|---|---|---|
| Recipe extraction, screenshot understanding, allergen suggestions | Gemini | OpenAI |
| Planning agent (tool calling) | DeepSeek | OpenAI |
| Embeddings | Gemini | None (retry or queue) |

**Data sent to model providers (all models):** check each provider's data storage and retention terms before use. Recipe and inventory data has low sensitivity, but prompts must not contain secrets or personally identifying information. Allergies and dietary restrictions are health or personal information: code performs the filtering, and before any model call, including a failover call, the unified call layer replaces saved friends' nicknames with codes such as "Diner 1", and it restores the nicknames in model responses before they are shown to the user. Constraints use the same form, such as "Diner 1 does not eat cilantro". Local code cannot recognize temporary guests' names, so anonymity cannot be guaranteed for them, and the UI must state that conversation content may be sent to the model provider.

---

## 4. Data Acquisition

### 4.1 Platform Types

| Platform type | Content | Acquisition difficulty |
|---|---|---|
| Long-video platforms | Long videos, often with human or AI subtitles; ingredients often appear in the description and pinned comment | Relatively easy: use only official APIs or subtitles pasted by the user |
| Short-video platforms | Short videos; ingredients are often spoken or shown on screen | Hard: strict anti-scraping measures, and open APIs usually expose only the user's own authorized content |
| Image-and-text social platforms | Many posts with ingredients in the text or images | Hard: usually no public API and strict anti-scraping measures, but screenshots are sufficient |
| Video platforms whose links a model can read directly | Fairly complete subtitles and descriptions | Easy: the Gemini API can take these video links as input |

### 4.2 Acquisition Methods (Most to Least Compliant)

1. **Provided by the user (main path):** pasted shared text, uploaded screenshots, uploaded video files (Phase 3; the user must confirm they have the right to use the file, and it is not retained after processing), and pasted subtitles or transcripts.
2. **Browser extension:** the user clicks "Save to recipe library" on the current page, and the extension reads what the page already shows. It is read-only, user-triggered, and limited to one page at a time (Phase 3; check the relevant sites' terms before development).
3. **Official APIs:** send video links supported by Gemini to Gemini, and use a platform's subtitle API only when it is official.

**Not used:** unofficial subtitle APIs and video download tools. The system does not bypass login or anti-scraping measures.

**Storage principle:** store refined structured data (dish name, ingredients, step summary), the original link, and the source the user provided, so the user can cook from the full method later:

| Import | Kept as the full method |
|---|---|
| Web link only | The link. The page is the method, so its text is not copied |
| Pasted text, with or without a link | The pasted text, plus the first link if there is one |
| Batch file | That dish's part of the file |
| Screenshots | The images, as files on the local disk |

Source text and images stay on the user's own machine. They are never published, never shown in the public demo, and never committed to the repository. The import page asks the user to import only content they may use, such as recipes they wrote, their own notes, or content shared for personal use. Videos are not stored. Step summaries are short paraphrases and are not publicly displayed.

### 4.3 Tiered Extraction (Cheapest First)

1. Text: title, description, pinned comment, and text pasted by the user.
2. Subtitles.
3. If there are no subtitles, transcribe the audio (OpenAI transcription model or Gemini audio understanding).
4. Finally, inspect the visuals: send key frames to a vision model, or have Gemini understand the whole video.

Each tier checks whether the information is sufficient (for example, whether main ingredients or amounts are missing) and moves on only if it is not. Tiers 2-4 involve video content and are planned for Phase 3.

### 4.4 Video Approach Comparison (Phase 3)

| Approach | Method |
|---|---|
| A | Gemini understands the whole video directly |
| B | Transcription + key frames + text LLM |
| C | Text and subtitles only |

Compare ingredient extraction F1, cost per item, and latency on the same evaluation set.

### 4.5 Scope for Phases 0 and 1

- Supported: pasted links and text; web page text (a single public link from the user, respecting robots.txt, with no batch scraping and no bypassing of login or anti-scraping measures; on failure, ask the user to paste the text); uploaded screenshots; and batch text file import.
- Deferred to Phase 3: video files, video links, browser extension, and official platform subtitle APIs.
- Not built: automatic scraping of short-video and image-and-text social platforms.

---

## 5. Data Model

### 5.1 Ingredients in a Recipe

Each row below describes one field of `recipe_ingredients`, which holds one line per ingredient used in a dish. The `ingredients` table in 5.3 is the shared dictionary those lines link to.

Gram amounts are not required. Keep the source wording, structure it where possible, and mark how certain the amount is.

| Field | Description |
|---|---|
| `raw` | Source wording, always kept ("少量葱花", "2个土豆", "芹菜") |
| `name` | Ingredient name as the source states it, without the amount ("葱花", "土豆") |
| `ingredient_id` | The normalized dictionary entry (葱花 → 葱, 生抽 → 生抽, never 生抽 → 酱油); empty when unmatched (see 5.5) |
| `amount` | Number; may be empty |
| `unit` | Grams, milliliters, pieces, stalks, tablespoons, teaspoons, spoons (size not stated), or empty. A spoon whose size the source does not state stays a spoon. A household measure with no unit in this list, such as a bowl, cup, handful, or pinch, keeps its number with an empty unit, and the source wording names the measure |
| `amount_type` | `exact` (precise weight or volume) / `count` (number of items) / `spoon` (household measures such as spoonfuls, bowls, cups, or handfuls) / `vague` (such as "a little" or "to taste") / `missing` (name only) |
| `role` | Main ingredient / secondary ingredient / seasoning |
| `optional` | Whether the dish can be made without it; H1 does not require optional ingredients |

**Rule:** the model must not overwrite "a little" with an estimated gram amount. When an estimate is genuinely needed, such as a shopping list hint, store it separately and label it as an estimate.

### 5.2 Inventory (Precision by Category)

| Category | How it is recorded |
|---|---|
| Seasonings (salt, soy sauce, sugar, oil) | Whether it is available |
| Countable items (potatoes, eggs, tomatoes, chicken wings) | Count |
| Items that are hard to count (meat, scallions, celery, leafy greens) | Approximate level: plenty / low / none; grams if the user wants |
| Cookware | Whether it is available |

### 5.3 Database Tables (PostgreSQL + pgvector)

**Decided:** the product is single-user, so there are no accounts, sign-up, or login, and tables have no `user_id`. The real instance is not publicly reachable. Expiry dates are optional. The model assigns the dish category at import, and the user can change it. A dish with meat or seafood is a `meat dish`; other dishes, including egg and tofu dishes, are a `vegetable dish`. Fried rice and noodle dishes are `staple food`. Cold dishes are marked in `tags`. Drinks and sauces are `other`.

**Recipes**

| Table | Main fields | Notes |
|---|---|---|
| `ingredients` (ingredient dictionary) | `name_zh`, `name_en` (normalized names), `aliases_zh[]`, `aliases_en[]`, `category` (vegetable / fruit / mushroom / meat / seafood / egg and dairy / soy product / staple food / nut / seasoning / other), `stock_mode` (availability / count / level), `allergens[]` (values from the fixed list in 5.5; empty = confirmed none, null = unknown), `parent_id` (nullable; see "Ingredient hierarchy" below), `source` (`seed` = reviewed seed data / `user` = created by the user) | Normalization, inventory precision, and allergy checks depend on this table. Chinese and English recipes normalize to the same `ingredient_id`, and the UI displays the name in the current language. For example, "海米" is tagged crustaceans and "蚝油" is tagged shellfish |
| `cookware` (cookware dictionary) | `name_zh`, `name_en`, `aliases_zh[]`, `aliases_en[]`, `source` (`seed` / `user`) | Normalizes "炒锅" and "wok" to one `cookware_id` |
| `recipes` | `lang` (original language, zh/en), `title`, `dish_type` (meat dish / vegetable dish / soup / staple food / snack / dessert / other), `time_min` (nullable), `time_source` (`stated` / `user` / `estimated` / empty), `servings`, `tags[]`, `steps_summary`, `source_type`, `source_url`, `source_text` (the pasted text or batch part; null for web and screenshot imports), `supplies[]` (consumables the method uses, such as plastic wrap, foil, baking paper, or skewers: shown as a reminder and never checked when planning), `parent_recipe_id` (a variant points to its original), `variant_note`, `embedding vector` (null until computed) | Stores structured information, the step summary, the link, and the source the user provided (see the storage principle in 4.2) |
| `recipe_images` | `recipe_id`, `file_name` (content hash plus extension), `mime_type`, `size_bytes`, `position` | Screenshots kept as the full method. The files live in `backend/data/images/`, which `.gitignore` excludes. A file is deleted once no recipe and no extraction log refers to it |
| `recipe_ingredients` | `recipe_id`, `raw`, `name`, `ingredient_id` (nullable), `amount`, `unit`, `amount_type`, `role` (main / secondary / seasoning), `optional` | See 5.1. See 5.5 for handling an empty `ingredient_id` |
| `recipe_cookware` | `recipe_id`, `cookware_id` | Availability only |

**Ingredient hierarchy:** normalization maps only different wordings or forms of the same ingredient and keeps the specific kind the source names. A kind of a broader ingredient is its own dictionary entry that points to the broader one through `parent_id`, such as 酱油 → 生抽 and 老抽, or 猪肉 → 五花肉. The hierarchy has one level.

| Use | Rule |
|---|---|
| Inventory | A recipe needing 酱油 is satisfied by 酱油, 生抽, or 老抽. A recipe needing 老抽 is satisfied only by 老抽 |
| Shopping list | List the ingredient as the recipe names it |
| Allergies and dietary restrictions | A rule on a parent also covers its children, and a child inherits its parent's `allergens[]` |
| Substitution | `parent_id` does not mean one child can replace another. Substitution still goes through the suggestion flow, and the user decides |

**Inventory and diners**

| Table | Main fields | Notes |
|---|---|---|
| `diners` | `display_name` (a form of address or nickname only), `is_self` | The user's own record is created automatically. A friend's record is created only after the user chooses to save it (see 7.6) |
| `inventory` | `ingredient_id`, `count`, `level` (plenty / low / none), `grams`, `expires_on` (optional), `updated_at` | The dictionary's `stock_mode` decides which field is filled. In `availability` mode no quantity field is filled: a row existing means the item is available |
| `cookware_owned` | `cookware_id` | |
| `always_in_stock` | `ingredient_id` | Default always-in-stock list (salt, cooking oil, water, sugar, soy sauce, vinegar, and so on); the user can add or remove items |

**Memory (structured and semantic)**

| Table / store | Content | Use |
|---|---|---|
| `dietary_rules` | `diner_id`, `rule_type` (allergy / dietary restriction / dislike), and one of `allergen` (from the fixed list in 5.5), `ingredient_id`, or `text` | Allergies and dietary restrictions are hard constraints; dislikes are soft preferences. Planning reads only the rules of the diners selected for this meal, and code filters directly |
| `ratings` | `recipe_id`, `score` (1-5, optional) | Ranking signal; a rating from the user is written directly |
| LangGraph Store (with vector search) | Dish notes, confirmed inferred preferences, substitution experience | Retrieved by semantic relevance during planning |

Substituted recipe variants are stored in `recipes` and point to the original through `parent_recipe_id`.

**Deleting a recipe:** saved plans keep the dish by name: `meal_plan_items.recipe_title` stays and `recipe_id` becomes null. The recipe's ratings and notes are deleted, and so are its source text and screenshots, except screenshots an extraction log still refers to. Its variants stay, with `parent_recipe_id` set to null.

**Planning and evaluation**

| Table | Content |
|---|---|
| `meal_plans` | `thread_id`, `constraints` (JSON with total headcount, selected `diner_id` values, and anonymous rules for temporary guests, never temporary guests' names), `confirmed_at`, `edits` (JSON; see "Manual plan edits" in 7.5). Holds only plans the user accepted; drafts and versions stay in the checkpointer |
| `meal_plan_items` | `plan_id`, `recipe_id` (nullable), `recipe_title` (snapshot), `servings` |
| `shopping_lists` | `plan_id`, `items` (JSON). Saved only after the user confirms the list |
| `extraction_logs` | `input_text`, `model_output`, `user_corrected`, `model_name`, `image_files` (the saved screenshots, so screenshot samples can be rerun) |
| `llm_calls` | `task` (extraction / planning / image understanding / embeddings, and so on), `provider`, `model`, `input_tokens`, `output_tokens`, `cost_estimate`, `latency_ms`, `success`, `fallback_used`, `created_at` |

**Purpose of `llm_calls`:** it is the basis for the monthly cap, the record of whether failover occurred, and the data source for evaluation cost and latency metrics. Per-model prices live in the configuration file with the date they were last updated.

The LangGraph checkpointer and Store create their own tables in the same database.

**`extraction_logs` is separate from recipes:** it keeps the input text, or references to the saved screenshots, together with the model output and the user's correction, so the evaluation set survives when a recipe is edited or deleted. It is used only for private evaluation. It stays local, is limited in quantity, is not displayed in the UI, and is not part of the public demo. The public demo does not write to it.

### 5.4 Inventory Deduction

- **Phase 1: the user maintains inventory manually.** The system does not deduct automatically.
- Rules to add later: deduct by count for countable items; do not deduct seasonings; for hard-to-count or vague items, ask for the remaining level (plenty / low / none) instead of deducting automatically; confirm with the user before deducting.

### 5.5 Allergy Information and Unrecognized Ingredients

**Dishes containing allergens stay in the recipe library.** Allergy rules change with the diners, so import only shows a warning and deletes nothing. Each planning run filters by that meal's diners. A dish can also be saved as a variant with the allergen substituted, and the variant can be selected if it contains no allergen.

**When an ingredient does not match the dictionary (`ingredient_id` is empty):**

1. Import review marks unrecognized ingredients and lets the user link them to an existing ingredient or create a dictionary entry with `source` = `user`.
2. If the user skips this:
   - H1 treats the ingredient as "not in inventory", so the dish is not selected in fridge-only mode.
   - If a diner in this meal has an allergy, dishes containing unrecognized ingredients are not selected.
   - If the diners have only dietary restrictions, use text matching on the source wording and name. If a dish with unrecognized ingredients is selected, remind the user to check it themselves.

**Fixed allergen list:** `crustaceans`, `shellfish`, `fish`, `egg`, `milk`, `peanut`, `tree nut`, `soy`, `wheat`, `sesame`. `allergens[]` and allergy rules accept only these values, so a free-text label can never silently fail to match. An allergy outside the list, or narrower than a tag, is stored as a rule on a specific ingredient through `ingredient_id`.

**Unknown allergens:** an unrecognized ingredient, or a user-created dictionary entry whose `allergens[]` is null, has unknown allergens. A missing value never counts as "contains no allergens". Seed entries are reviewed by a person and are never null.

- **Selection:** if any diner in this meal has an allergy of any kind, the model cannot select dishes containing such an ingredient, because it cannot be shown to be free of that allergen. The user can still add one manually after confirming (see "Manual plan edits" in 7.5).
- **Visibility:** planning tells the user how many dishes were left out for this reason, such as "3 dishes were not used because they contain ingredients with unknown allergens", and the user can see which ones.
- **Resolution:** a model suggests which listed allergens the ingredient may contain, and code discards values outside the list. The user then confirms once which allergens it contains, or that it contains none. A suggestion has no effect until confirmed, and if the user skips this, `allergens[]` stays null. The result is saved to a user-created dictionary entry. Code then links other `recipe_ingredients` rows that have the same `name` and an empty `ingredient_id` to that entry. That dish and other dishes using the ingredient become selectable again unless the confirmed allergens rule them out.

**When cookware does not match the dictionary:** import review and manual entry let the user link it to existing cookware, create a dictionary entry with `source` = `user`, or skip it. A new entry records only that the dish needs this cookware, so the review also asks whether the user owns it and, if so, adds it to `cookware_owned`. Skipped cookware is not stored, and H3 does not check it. A dish needing that cookware could then be selected, which is an inconvenience rather than a safety risk, so it is not treated like unknown allergens.

**Permanent allergen notice:** the dictionary and extraction can both be wrong, so wherever the UI shows a meal plan or a substituted dish, including revised and saved plans, it shows a notice telling the user to check the actual ingredients for allergens themselves. The notice appears whether or not any diner has a recorded allergy and cannot be dismissed or turned off. It is fixed UI text, not model output, so it does not depend on the model including it. System output must not be presented as a safety guarantee.

---

## 6. Model Roles

| Role | Handled by |
|---|---|
| Meal planning, multi-turn revision, intent detection, substitution suggestions | DeepSeek primary, OpenAI fallback |
| Ingredient extraction (text → structured JSON) | Gemini with OpenAI fallback in Phases 0 and 1. Phase 2 runs a comparison experiment, and **the result decides whether to switch to the tuned small Qwen model** |
| Screenshot extraction | Vision LLM: Gemini primary, OpenAI fallback |
| Allergen suggestions for unknown ingredients (see 5.5); the user confirms, and code performs all allergy filtering | Gemini primary, OpenAI fallback |
| Video understanding (Phase 3) | Gemini (direct understanding) or transcription + key frames (see 4.4) |

**Purpose of the tuned model:** validate cost and speed, the stability of format and normalization rules, and offline availability, and run the full "data synthesis → tuning → deployment → evaluation" workflow. **Treat it as a comparison experiment, report measured results, and do not assume tuning is better.**

**Optional second tuning task:** parse user requests into structured conditions (taste, duration, constraint mode).

---

## 7. Agent Design

### 7.1 Tools (Function Calling)

| Tool | Purpose |
|---|---|
| `get_inventory(category)` | Look up ingredients, seasonings, and cookware; can sort by expiry date |
| `update_inventory(items)` | Add, change, or remove ingredients, seasonings, and cookware the user states in conversation |
| `search_recipes(query, max_time, tags, must_use, exclude_recent_days)` | Search the personal recipe library (vector search + filters). `exclude_recent_days` leaves out dishes in plans saved within that many days, and is set only when the user asks for it in the request. Code automatically excludes dishes that the current plan's diners cannot eat, and the model cannot turn this off |
| `get_recipe(recipe_id)` | Get a dish's full information and source link |
| `compute_missing(recipe_ids)` | Compare with inventory and compute what is missing (done by code) |
| `check_cookware(recipe_ids)` | Check whether the required cookware is available |
| `get_preferences()` | Read the current plan's anonymous constraints and taste preferences. Never returns information about friends who were not selected |
| `get_notes(recipe_id)` | Read dish notes |
| `save_note` / `update_preference` / `save_rating` | Write memory (notes and inferred preferences go to the Store; ratings go to `ratings`) |
| `save_dietary_rule(diner_id, rule)` | Write a dietary rule. An allergy rule takes effect only after the user confirms it |
| `make_shopping_list(items)` | Generate a shopping list |
| `extract_recipe(text)` | Text → structured data (uses the currently configured extraction model) |
| `load_skill(name)` | Load a Skill's full instructions |

**Principle:** code handles calculation, comparison, and counting. The model understands the request, chooses tools, and organizes results. All models share one set of tool definitions (JSON Schema), which Phase 3 wraps as an MCP server. `save_recipe` and saving a plan are graph nodes that run after user confirmation, not tools the model can call, so a model cannot skip the confirmation.

### 7.2 Skills (Model-Independent Implementation)

| Skill | Content |
|---|---|
| Meal design | Balance of meat and vegetables, number of dishes and soups, total time, cookware availability, preference for ingredients close to expiry |
| Fridge-clearing mode | Use only existing ingredients; seasoning substitutions are allowed |
| Shopping planning | What is missing, how much to buy, and merging into one list |
| Recipe import | Identify the source type, choose the extraction method, validate before saving |
| Ingredient substitution | Judge whether a substitution is common and what adjustments it needs |

**Implementation:** each Skill is a folder containing `SKILL.md` (name, description, instructions, examples). The system prompt contains only each Skill's name and one-line description. The model calls `load_skill(name)` when it needs the full content, which keeps context small through progressive loading. Plan revisions send only "the current plan + the revision instruction".

### 7.3 LangGraph Graph

**Interaction design:**

- Multi-turn conversation; plans can be revised.
- One meal is planned at a time (a number of dishes and soups).
- Actions that require user confirmation: **writing inferred preferences and notes, turning an allergy stated in conversation into an allergy rule, saving friends' dietary information, setting the diner list for a meal with several people, generating a shopping list, saving an imported recipe, and adopting an ingredient substitution**.
- Actions that do not require confirmation: **content the user states explicitly** (a rating, or "note that this dish needs less salt") is written directly, and the agent replies "Noted".

**Overall structure:**

```
User message → route_intent
  ├─ Plan a meal / revise a plan (subgraph A)
  ├─ Import a recipe (subgraph B)
  ├─ Enter a dish manually (subgraph C)
  ├─ Substitute an ingredient (subgraph D)
  ├─ Rate a dish (write directly to ratings)
  └─ Manage or query inventory (tool calls)
→ extract_memories (find possible preferences or notes in the conversation)
→ if candidates exist: [human confirmation] → write to long-term memory
→ END (wait for the next message)
```

**Subgraph A: meal planning and multi-turn revision**

```
parse_request ─key information missing→ [interrupt to ask, such as "How many people are eating?"]
  ─several diners→ [interrupt to select diners, see 7.6]
  → load_context (inventory snapshot, this meal's diner constraints, ratings, relevant notes)
  → planner (LLM) ⇄ tools (may load_skill)
  → validate_plan (code check) ─fails→ return to planner with specific errors (at most N times)
  → present_plan → END
Next turn, "swap the second dish / make it spicier" → revise_plan (only the current plan + the instruction) → validate_plan → present_plan
"Xiao Li is coming too / Xiao Wang isn't coming" → update diners → validate_plan → replace only dishes that break the rules → present_plan
"This one is good" → save the plan → if ingredients are missing: make_shopping_list → [interrupt to confirm] → save the list
```

**Subgraph B: recipe import**

```
detect_source → extract (text: the configured extraction model; screenshot: vision LLM)
  → normalize → validate (Pydantic; flag missing main ingredients)
  → [interrupt to show the extraction result, editable] → save_recipe (with the original link)
  → record "extraction result vs. user correction" to build the evaluation set automatically
```

**Subgraph C: manual entry**

```
parse_manual → normalize → enrich (optional: taste tags, estimated duration, cookware)
  → [interrupt to confirm] → save_recipe (source: user entry)
```

**Subgraph D: ingredient substitution**

```
parse_substitution (such as curry beef: beef → chicken)
  → assess (load the "Ingredient substitution" Skill: is it common, what adjustments are needed, are there similar dishes in the library)
  → suggest: workable / workable with adjustments / not recommended, with reasons
  → [interrupt for the user's decision] → if adopted, save as a recipe variant or note
```

Web search to verify whether a substitution is common is not in Phase 1 and may be added later.

### 7.4 State Design

| Content | Location |
|---|---|
| `messages`, `constraints`, `current_plan`, `plan_versions`, `validation_errors`, `pending_action`, `loaded_skills`, `plan_diners` (this meal's diners and temporary guests' rules) | LangGraph checkpointer (one conversation) |
| Dish notes, confirmed inferred preferences, substitution experience | LangGraph Store (semantic memory across conversations) |
| Recipes (including variants), inventory, seasonings, cookware, diner profiles, allergies and dietary restrictions, ratings, saved plans and shopping lists | PostgreSQL business tables |

### 7.5 validate_plan Rules

**Assumptions:**

- Dishes in a plan **must come from the user's own recipe library**; dishes outside it are not recommended.
- Seasonings have a **default always-in-stock list** (salt, cooking oil, water, sugar, soy sauce, vinegar, and so on) that the user can edit. Only seasonings outside the list need separate entry.
- Dietary limits have two levels: **allergies and dietary restrictions are hard constraints**; **dislikes are soft preferences** (avoid where possible, rank lower, and warn when used).
- Only the rules of **the diners selected for this meal** are checked. If anyone has an allergy or dietary restriction, it applies to the whole table. For allergies, this is also because shared cookware and dishes carry a cross-contact risk.
- Cookware checks **consider only availability**, not two dishes competing for one pan at the same time, and compare by `cookware_id`.
- Duration is not required. It is checked only when the user **explicitly gives a time limit**; see H6.

**Hard constraints (a failure sends the plan back for replanning; the constraint satisfaction rate is calculated at this level):**

| ID | Rule |
|---|---|
| H1 | Fridge-only mode: each dish's main and secondary ingredients (except `optional`) must be in inventory and not at level "none", with matches following the ingredient hierarchy in 5.3. Seasonings are checked for availability only. Items in `always_in_stock` are assumed available. Unrecognized ingredients count as "not in inventory" (see 5.5) |
| H2 | No allergens or restricted ingredients of this meal's diners. Allergy rules on a listed allergen are checked through the dictionary's `allergens[]`; rules on a specific ingredient and dietary restrictions are checked through `ingredient_id`, normalized names, and aliases. All checks include the ingredient hierarchy in 5.3. Variants are checked too. Unrecognized ingredients follow 5.5 |
| H3 | The user has all required cookware. Supplies such as plastic wrap or foil are not checked |
| H4 | The composition matches the requested number of dishes and soups (`meat dish` and `vegetable dish` count as dishes, and `soup` as soups) |
| H5 | No duplicates; each `recipe_id` must exist in the user's recipe library |
| H6 | When the user gives a time limit: a dish whose duration source is `stated` or `user` must not exceed it |

**Soft warnings (shown only; they do not fail the plan):**

| ID | Rule |
|---|---|
| S1 | The quantity may be insufficient (compared only when units match) |
| S2 | An ingredient this meal's diner dislikes is used; the warning says who |
| S3 | An ingredient close to expiry is unused (only when an expiry date was entered) |
| S4 | A low-rated dish was selected |
| S5 | Unbalanced selection (such as all meat dishes) |
| S6 | A vague amount meets "low" inventory; ask the user to take care |
| S7 | With a time limit: warn "duration is estimated and may exceed the limit" for `estimated` dishes and "duration unknown" for empty ones, and warn when the total duration clearly exceeds the limit (dishes can be cooked in parallel, so this is not a hard constraint) |
| S8 | When any diner's dietary limits are unconfirmed, warn "N diners have unconfirmed dietary limits". When a selected dish contains unrecognized ingredients, remind the user to check it themselves |

**Duration source (`time_source`):**

| Value | Source | With a time limit |
|---|---|---|
| `stated` | Written in the source recipe | Part of the H6 hard constraint |
| `user` | Entered or changed by the user | Part of the H6 hard constraint |
| `estimated` | Not in the source; estimated by the model at import | Can be selected with an S7 warning; dishes with known durations are preferred when otherwise equal |
| Empty | No estimate is available | Can be selected with an S7 warning and ranks lower |

Import confirmation shows the duration and its source. A changed duration becomes `user` by default, and the user can mark it `stated` when the source gives it, which also corrects an extraction that mislabeled a stated time. `estimated` is offered only while the model's own number is unchanged. A later "cooked this meal" feature could let the user record actual time.

**Manual plan edits:** the user can remove a dish from the plan card, or browse the recipe library and add one. After each edit, code reruns `validate_plan` and recomputes missing ingredients without calling a model, and the result is written back to `current_plan`, so the next conversation turn sees it. The user can also add, remove, and change items when confirming the shopping list.

| Rule | When the user adds a dish that breaks it |
|---|---|
| H2 | Show which diner and which allergen or restriction is affected. The dish is added only after the user confirms again. A dish with an ingredient of unknown allergens (see 5.5) is handled the same way when any diner has an allergy: the prompt names the ingredient and offers to resolve its allergens |
| H1, H3 | Allowed with a warning. Missing ingredients go on the shopping list |
| H4 | Allowed. The edited plan is what counts |
| H5 | Not allowed |

**Edit log:** `meal_plans.edits` records each manual edit: the action, `recipe_id`, time, and any rule overridden, identified by `diner_id` and allergen or ingredient, never by name. A dish with a confirmed H2 override is not sent back for replanning or silently replaced by later validation, and the override covers only that diner and rule, so a new conflict asks again. The log is used to review overrides and to analyze how often users change model plans. It does not change preferences by itself; any preference inferred from it goes through the usual user confirmation.

**Planning metrics:** computed only on plans as the model produced them, before manual edits:

- Constraint satisfaction rate = the share of plans that pass all hard constraints on the first attempt;
- Final pass rate = the share of plans that pass within N retries;
- Average number of retries.

### 7.6 Meals for Several People

**Principle:** the user's own information is stored long term. Other people's information applies only to the current meal by default and enters a friend profile only if the user chooses to save it. A saved friend's rules take effect only when the user selects that friend.

| Type | Location | When it applies |
|---|---|---|
| The user | `diners` and `dietary_rules` | By default; can be deselected if the user is not eating this meal |
| Saved friends | `diners` and `dietary_rules`, nickname only | Only when selected as a diner for this meal |
| Temporary guests | This plan's `plan_diners`; `constraints` stores only anonymous rules | This meal only; no friend profile is created |

**Selecting diners:**

- When one person is eating, plan directly without asking and without mentioning friends.
- When several people are eating, use an interrupt to show a selection card. The card lists each person's rules, which the user can edit on the spot.
- The user is selected by default; saved friends named in the conversation are preselected; other friends are not selected.
- The card lists all saved friends, sorted by the most recent saved plan that included them.
- Saved friends' names are matched by local code against nicknames and are not sent to a model for recognition. Code writes the constraints after the user confirms the final list.
- When fewer people are selected than the total headcount, ask whether the rest have dietary limits, with the options "No / Yes, I'll enter them / Not sure". "Not sure" allows planning to continue with an S8 warning.

**Saving friend information:** when the user mentions a new friend's rule, ask once, with the options "Use for this meal only (default) / Save to friend profiles", and remind the user to get the friend's agreement before saving. After "Use for this meal only", do not ask again in this conversation. The friend profile page supports viewing, editing, and deleting, and deleting a person also deletes their rules.

**Deleting a friend:** in one transaction, code deletes the person and their rules, and removes their `diner_id` and related allergen and rule details from `meal_plans.constraints` and `meal_plans.edits`.

- Saved plans stay. Their dishes, shopping lists, and total headcount do not change, and the plan page shows "deleted diner" in place of the person. The edit log keeps only that a dish was added and a rule was overridden.
- The delete dialog says that related conversations may still contain this friend's information and links to conversation deletion. Conversations are not cleaned automatically, because their content cannot be scrubbed reliably.
- If an open conversation's plan includes a deleted diner, code asks the user to confirm again who is eating this meal. It must not silently drop the person, because their limits would then stop applying without notice.

**Changes mid-conversation:** when someone is added, rerun `validate_plan` and replace only the dishes that break the rules. When someone is removed, the plan does not need to change unless the user asks to replan.

**Conversation records:** temporary guests' information does not enter friend profiles, but what the user said stays in the checkpointer's conversation records. Saved friends' nicknames are replaced with codes before model calls, but anonymity cannot be guaranteed for temporary guests' names. The product must therefore provide conversation deletion and explain in the UI how conversations are kept and that their content may be sent to the model provider.

**Public demo:** a separate deployment with its own database, which holds only hand-written or licensed sample data and none of the real instance's data. Visitors share that database. Each browser session has its own `thread_id`, so visitors cannot see each other's conversations. Visitors can plan and revise meals, but cannot change shared recipes, inventory, or dictionaries. Import can show an extraction result but does not save it, so no source text or images are stored. Long-term memory writes are off, friend profiles are not available, and the database is reset on a schedule.

**Scope:** Phase 1 supports rules and filtering for several diners. Ratings still record only the user's own scores.

---

## 8. Evaluation

| Target | Metrics |
|---|---|
| Ingredient extraction | Ingredient name F1 (primary metric); amount accuracy (only for lines whose source gives a number: weights, volumes, counts, and household measures, with the number and unit both right); amount type accuracy (whether vague amounts are `vague` and absent amounts are `missing`); **fabrication rate** (the share of outputs with a number where the source has no amount); valid JSON rate |
| Planning | Constraint satisfaction rate (hard constraints); preference adherence rate (LLM as judge); average number of retries |
| Cost and performance | Cost per item, latency, vLLM throughput |
| Video approaches (Phase 3) | F1, cost, and latency comparison across approaches |

**Evaluation set source:** from Phase 0, the user reviews and corrects each import's extraction result, which builds human labels naturally. Phase 2 completes the set to 100-200 items, which are not used for training; prefer self-written or licensed content. The evaluation set **must include Chinese and English recipes**, and metrics are reported separately by language. Evaluation data is not uploaded to the repository (see Section 3). Evaluation runs continuously from Phase 0. CI has no access to the evaluation data and does not run this evaluation.

**Regression gate:** the gate runs manually on the local machine after changes to prompts, models, or graph structure, because it calls real models and has a cost.

- Scripts and gate logic live in the repository. The evaluation set and baseline scores live in `data/`, with a private backup outside Git.
- The script compares new scores with the baseline, allows a tolerance for normal model variation, reports pass or fail, and exits with a non-zero code on failure.
- The baseline records the evaluation set's version or hash. If the set has changed, the script reports the difference instead of a pass or fail, and the user confirms a new baseline.
- CI tests the score calculations and pass or fail logic with fictional data and mocked responses. This verifies the script, not model quality.

**Three layers of verification:**

| Layer | What it checks | When it runs | Calls a real model |
|---|---|---|---|
| Unit tests | Whether code logic such as `validate_plan` is correct, including whether it rejects known-bad plans | Locally before each commit, and in CI on pull requests to `main` | No |
| Runtime validation | Whether this generated plan satisfies hard constraints; if not, it goes back for replanning | Every planning run | Yes |
| Model evaluation | First-attempt pass rate, final pass rate, and average retries on fixed requests and inventories | Locally, after changing prompts, models, or graph structure | Yes |

Passing evaluation does not prove the validator is bug-free, so generated results must be spot-checked by a person regularly. Whether preferences are met is scored by an LLM judge, and the score is for reference only. Evaluation costs count toward `llm_calls` and the monthly cap.

---

## 9. Fine-Tuning Plan

### 9.1 Hardware

- Local: RTX 4060 Ti **8GB** (WSL Ubuntu). Suitable for coding, debugging, QLoRA training on 1.5B, and vLLM deployment of 1.5B.
- **Google Colab:** the free tier is usually a T4 16GB (not guaranteed). It can train 1.5B-3B, and Unsloth can run 7B-8B QLoRA. Paid tiers offer L4 and A100.
- Alternatives: Kaggle (T4, about 30 hours per week) and AutoDL (4090, billed hourly).

### 9.2 Starting Configuration for 1.5B (Local 8GB)

- Unsloth (or LLaMA-Factory), loaded in 4-bit (QLoRA).
- LoRA r=16, alpha=32, applied to all linear layers.
- Sequence length 1024 (measure the data's token lengths first).
- Batch size 2 with 8 gradient accumulation steps; gradient checkpointing on.
- Use **bf16** on the 4060 Ti. Colab's T4 **does not support bf16, so use fp16**.
- 2-3 epochs.
- If VRAM runs out: first reduce sequence length, then reduce batch size and increase gradient accumulation, and finally switch to a smaller model.

### 9.3 Colab Notes

- Keep checkpoints and datasets on Google Drive so training can resume after a disconnect.
- Put install commands in the first cell and pin versions.
- Clean up the training notebook and add it to the repository (such as `notebooks/finetune_qwen.ipynb`). Clear outputs before committing to avoid including data samples.

### 9.4 Experiment Matrix

| Approach | Where it runs |
|---|---|
| API baseline (OpenAI and Gemini small models, few-shot) | API |
| Qwen 1.5B, untuned | Local |
| Qwen 1.5B + QLoRA | Local |
| Qwen 3B + QLoRA | Colab |
| Qwen 7B + QLoRA (optional) | Colab or AutoDL |

### 9.5 Data

- Evaluation set: 100-200 human-labeled items, not used for training.
- Training set: synthesized through APIs, keeping 2,000-5,000 items after quality checks. Check provider terms first, and use only providers that allow their output to be used to train other models.
- Quality checks: Pydantic format validation, deduplication (vector similarity or MinHash), spot checks with an LLM judge, and human sampling. The key rule is that if the source has no amount, the output must not contain one.

---

## 10. Open Items

- [ ] JSON Schema for each tool.
- [ ] Synthetic data design (seed sources, diversity, difficulty distribution, quality rules).
- [ ] Phase 2 task breakdown and Qwen version choice.

---

## 11. Task Breakdown (Phases 0 and 1)

Estimates assume 4-6 hours per day, with 1 day ≈ one working day. They are only estimates; Phases 0 and 1 total 27.5-35 days. Each task follows the Superpowers process: plan → test first → implement → review → commit.

### Phase 0: Data Foundation (11-14.5 days)

| ID | Task | Deliverable / done criteria | Estimate |
|---|---|---|---|
| T0.1 | Repository skeleton | Private GitHub repository `mine-my-dinner`; backend (FastAPI) and frontend (Next.js with internationalization configured) in one repository; docker-compose starts PostgreSQL + pgvector; `.gitignore` (including `.env` and `data/`), `.env.example`; pytest runs; minimal CI runs unit tests on pull requests to `main`, with no real model calls or evaluation data | 1-1.5 days |
| T0.2 | Database tables and migrations | Create the tables in 5.3 (Alembic migrations, with the vector dimension set per Section 3); **bilingual ingredient dictionary seed data** (about 200 common ingredients with Chinese and English names and aliases, category, `stock_mode`, and `parent_id`; may be generated by an LLM and then checked by a person); the fixed allergen list and ingredient allergen tags (never null for seed entries); cookware dictionary seed data; default always-in-stock list; automatic creation of the user's own `diners` record | 1.5-2 days |
| T0.3 | Multi-model call layer | Implement the model factory, unified calls, capability registry, per-task model selection, and automatic failover per 3.1; pre-call and post-call hooks for nickname replacement and restoration, which T1.4 fills in; embedding calls with no failover; **write every call to `llm_calls` and enforce the monthly cap**; unit tests use mocked (or recorded) responses, plus one smoke script with real calls | 1.5-2 days |
| T0.4 | Extraction format and normalization | Pydantic models (recipes with `lang`, `dish_type`, `time_min`, `time_source`, `servings`, `tags`, `steps_summary`; ingredients with `amount_type`, `role`, `optional`; cookware); extraction prompt v1; normalization (dictionary + alias matching, flagging unmatched ingredients and cookware) | 1 day |
| T0.5 | Import sources | Pasted text; web page text (a single public link from the user, extracting the main text, respecting robots.txt, prompting to paste on failure); screenshots (vision model); batch text file import. All store the source type and link | 1-1.5 days |
| T0.6 | Review and save | Backend API and minimal frontend page: show extraction results, allow edits, and confirm saving; mark unrecognized ingredients, which can be linked to existing ingredients or become user-created dictionary entries, with model-suggested allergens confirmed or left unknown per 5.5; handle unmatched cookware per 5.5; warn when a dish contains the user's own allergens; compute the recipe embedding on save, keeping the recipe and queuing a retry if the call fails; write `extraction_logs` (model output vs. user correction) | 1.5-2 days |
| T0.7 | Inventory, cookware, always-in-stock items, and diner management | CRUD APIs and pages; optional expiry date; manage the user's own allergies (selected from the fixed list or set on a specific ingredient) and dietary restrictions; friend profiles can be viewed, edited, and deleted; a recipe library page for browsing, searching, viewing, editing, and deleting dishes (see 5.3), recomputing the embedding after an edit; management of user-created dictionary entries, including resolving unknown allergens per 5.5 | 2-2.5 days |
| T0.8 | Import real data | Import and review 30-50 real recipes, including both Chinese and English recipes, and start building the evaluation set | 1 day |
| T0.9 | Extraction evaluation script | Export reviewed results from `extraction_logs` to local evaluation data; compute ingredient name F1, amount accuracy, amount type accuracy, fabrication rate, and valid JSON rate, separately by language; report cost per item and latency from `llm_calls`. Data does not enter the repository | 0.5-1 day |

**Phase 0 acceptance:** 30-50 real recipes are imported, the evaluation set has started, and the extraction evaluation script outputs metrics locally.

### Phase 1: Agent (16.5-20.5 days)

| ID | Task | Deliverable / done criteria | Estimate |
|---|---|---|---|
| T1.1 | Deterministic tools | Implement the tool functions in 7.1, with full unit tests for `compute_missing`, `check_cookware`, and `search_recipes` (vector search + filters, excluding dishes the diners cannot eat, `exclude_recent_days`), covering the ingredient hierarchy and unknown allergens | 1-1.5 days |
| T1.2 | `validate_plan` | Implement all of H1-H6 and S1-S8, tested with constructed plans, inventories, and multi-diner scenarios, including the unrecognized-ingredient rules, null `allergens[]`, the ingredient hierarchy, and confirmed H2 overrides | 1 day |
| T1.3 | LangGraph skeleton | State definition, `route_intent`, PostgreSQL checkpointer; multi-turn conversations can resume | 1 day |
| T1.4 | Subgraph A: planning | `parse_request` (interrupt to ask when information is missing) → `load_context` → planner ⇄ tools → `validate_plan` retry loop → `present_plan`; rank candidates by preference, dislikes, rating, inventory, expiry, and duration source; exclude recently eaten dishes only when the request asks for it; report dishes left out for unknown allergens and let the user resolve them per 5.5; use an interrupt to select diners and handle headcount mismatches; match saved friends' nicknames locally, replace them with codes before model calls, and restore them in responses; build a minimal planning evaluation (5-10 scenarios) | 2.5-3 days |
| T1.5 | Plan revision and shopping list | `revise_plan` (only the current plan + the instruction) and plan versions; revalidate when diners change and replace only dishes that break the rules; save the accepted plan; when ingredients are missing, generate a shopping list with interrupt confirmation; APIs for manual plan and shopping list edits, their validation, and the edit log (see 7.5) | 1.5-2 days |
| T1.6 | Memory | Write `ratings`, notes the user states explicitly, and non-allergy `dietary_rules` directly; an allergy stated in conversation becomes a suggested rule that the user confirms; LangGraph Store semantic memory; `extract_memories` with human confirmation; ask whether to save friend information (this meal only by default, with a reminder to get the friend's agreement); clean up saved plans and handle open conversations when a friend is deleted (see 7.6) | 1.5 days |
| T1.7 | Skills | `SKILL.md` for each Skill; the `load_skill` tool; the system prompt contains only Skill names and descriptions | 0.5-1 day |
| T1.8 | Subgraphs B, C, and D | Import through the graph (interrupt for review); manual entry; ingredient substitution (suggestion, user decision, saved variant) | 2.5-3 days |
| T1.9 | Chat UI | Next.js chat page: streaming output, confirmation buttons during interrupts, diner selection card, and plan and shopping list display with manual editing; the permanent allergen notice from 5.5; a page for viewing recent saved plans and their shopping lists; a view of dishes left out for unknown allergens, with a way to resolve them; conversation deletion and an explanation of how conversations are kept and that their content may be sent to the model provider; the delete-friend dialog's warning about conversations and its link to conversation deletion; **Chinese and English UI switching**, with the agent replying in the user's language | 2.5-3.5 days |
| T1.10 | Planning evaluation | Expand the minimal evaluation from T1.4 to 30-50 test requests with varied inventory scenarios; metrics: constraint satisfaction rate, final pass rate, average retries, preference adherence rate (LLM as judge), and cost and latency from `llm_calls`; **compare DeepSeek, OpenAI, and Gemini as planning models**; implement the local regression gate from Section 8 for extraction and planning | 2 days |
| T1.11 | Wrap-up and publication | README, architecture diagram, LICENSE, a check of the full history for secrets, cleanup of secrets and private data, and making the repository public | 0.5-1 day |

**Phase 1 acceptance:** fridge-only and shopping-allowed planning, multi-turn revision, manual plan edits, meals for several people, memory reads and writes, and all four subgraphs work; the permanent allergen notice appears wherever 5.5 requires it; planning evaluation has numbers; the repository is public.

**Note:** web import handles only a single public link from the user, with no batch scraping and no bypassing of login or anti-scraping measures.
