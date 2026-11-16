# Import and Dictionary Experience — Design

Date: 2026-11-16. Branch: `feat/import-and-dictionary`. Source: `docs/phase-1-carryover.md`, "Import, dictionary, and extraction" → "Import and dictionary experience plan", decided with the user during the plan 2a checkpoint (2026-11-15) and in this design conversation (2026-11-16).

## 1. Intent

While testing plan 2a the user found the import review and the dictionary hard to use: names the dictionary does not know had to be linked by hand again and again (five recipe lines named 西蓝花 were each linked to 西兰花 by hand), the "allergens unknown" list could only create new entries, a new entry's category and stock mode were guesses the user had to fix, and the amount controls fought each other. The user wants these fixed before plan 2b (diners and other diners' allergies) builds on the dictionary. Success: importing a recipe needs fewer clicks and fewer corrections, and a link made once is not made again.

## 2. Goals, non-goals, anti-goals

**Goals**

1. A manual link from an unknown name to an ingredient entry can be remembered, so that name links automatically from then on.
2. The "allergens unknown" list can link an unknown name to an existing entry, not only create a new one.
2b. Leaving the page with unsaved drafts asks for confirmation first.
3. The suggestion made for a new entry covers its category and stock mode as well as its allergens, in the same model call.
4. The amount controls in the review form stop fighting the user.
5. The new suggestion prompt is measured once on a small fixed list before it is relied on.

**Non-goals (decided by the user)**

- Cookware gets no "remember this link": cookware entries have no personal aliases (no `user_aliases_*` columns), and adding them needs a migration that this plan does not want. Cookware linking stays as it is.
- No new built-in aliases for common spelling variants (西蓝花/西兰花, 西红柿/番茄, 马铃薯/土豆): the seed dictionary does not change in this plan. Remembered links are the fix.
- No new units. Count words other than 根 stay 个, as `extract_v2` does today ("3 cloves garlic" → 3 个); the source text stays in `raw`.
- Stored amount values, the extraction prompt, and existing data do not change.

**Anti-goals**

- A suggestion must never take effect without the user's confirmation (allergens, category, and stock mode alike).
- A link the user did not ask to remember must not change the dictionary.
- Removing a remembered alias must not unlink lines that were already linked: each of those links was confirmed by the user.

## 3. Constraints

- Project rules (`CLAUDE.md`): the user performs every Git operation that changes the repository; no new packages; never touch `.env`; every repository file in English, Chinese only in UI translations, dictionary names and aliases, and recipe text with expected results in tests and prompt examples. Every user-facing string goes into both `frontend/messages/en.json` and `zh.json`.
- Prompt versions (`docs/prompt-versions.md`): a prompt file any record used is never edited; a change makes a new version file.
- Name matching stays exact on purpose (`find_term_owner` in `extraction/normalize.py`: case and outer spaces ignored, nothing fuzzy).
- Tests use `minemydinner_test` only and never call a real model. Paid model calls run only after the user approves the batch and its expected cost.
- Ingredient values come from `constants.py`: `CATEGORIES` (11 values), `STOCK_MODES` (`availability`, `count`, `level`), `ALLERGENS` (10 values). The carryover note's "grams" stock mode does not exist; the three modes above are the full set.

## 4. Approaches

Considered for remembering a link:

- **(A) Reuse personal aliases (chosen).** A remembered link adds the unknown name as an alias of the chosen entry: a personal alias (`user_aliases_zh` / `user_aliases_en`) on a built-in entry, or an alias (`aliases_zh` / `aliases_en`) on the user's own entry. Matching already reads these (`find_term_owner`), the dictionary page already edits them, and `link_unmatched_lines` already links the other unmatched lines with that name. Removing the alias in the dictionary page stops future links and leaves existing links alone, which is the wanted behaviour.
- **(B) A separate table of remembered links.** Rejected: it duplicates the alias mechanism, needs a migration, and the dictionary page would have to show and remove a second kind of record.

Considered for when the other drafts of a batch learn about a remembered link (the user's case: 20 recipes, 7 say 西蓝花):

- **Write the alias when the box is checked and relink every queued draft in the browser.** Rejected: an alias would stay even if the draft is abandoned, and the browser would have to keep many drafts in step.
- **Hold remembered links in the browser and apply them to every draft, writing them on the first save.** Rejected: the held links live only in memory, and the logic is harder to follow.
- **Write the alias on save, and match each draft again when it is shown (chosen, proposed by the user).** The queue is sequential, so the alias is already in the dictionary when the next draft appears; nothing has to be kept in step, and an abandoned draft leaves no alias.

For the other parts there was one sensible way, given the user's decisions: the "allergens unknown" list reuses the dictionary search the review form already has; the category and stock mode come from the same suggestion call (the user decided no extra call); the amount controls change only in the frontend and the shared unit-to-type mapping comments.

## 5. Decisions

### 5.1 Remember a manual link (ingredients only)

Where: the ingredient row of the review form (`IngredientReviewRow.tsx`), which serves both the import review and the recipe edit page (`RecipePage` uses `ReviewForm`), and the "allergens unknown" list (5.2). The inventory page has no recipe-line name to remember (the user searches the dictionary directly), so it gets no checkbox.

- When the user links a line whose name the dictionary does not know to an entry by hand, a checkbox **"记住这个关联" / "Remember this link"** appears next to the chosen entry, **checked by default** (decided by the user: ask each time, default on, so a one-off link can be left unchecked).
- Nothing is written to the dictionary until the recipe is saved. On save, for each line linked by hand with the box checked, the backend adds the line's name (trimmed) as an alias of the chosen entry in the same transaction as the recipe: zh when the name has CJK characters, otherwise en; a personal alias on a built-in entry, an alias on the user's own entry. Then it links the other unmatched recipe lines with that name (`link_unmatched_lines`).
- In the open draft, other unmatched lines with the same name (one recipe can list a name twice, for example a garnish section or the two alternatives of a choice group) are linked to the same entry at once when the box is checked, so the user does not link them twice in one recipe.
- **Each draft is matched again when it is shown (proposed by the user).** The review queue shows one draft at a time, and the user saves (or leaves) the current draft before the next appears. When a draft becomes the current one, its unmatched ingredient lines are matched against the dictionary again (exact matching as in `match_ingredient`; one request for all of the draft's unmatched names). So a name remembered and saved in an earlier draft, or a new entry created in an earlier draft, links automatically in every later draft: importing 20 recipes of which 7 say 西蓝花 needs one manual link. Lines the user already linked, skipped, or renamed are not touched; only lines still unmatched are. The newly matched lines look like any line the dictionary matched.
- If the name already belongs to an entry by the time of saving (rare after the re-match above: for example the user changed the dictionary in another tab meanwhile), the alias is not added again; if it belongs to a different entry, the save still succeeds, the alias is not added, and the response says so (the line keeps the link the user chose).
- The checkbox does not appear when the line's own name already matched an entry, or when the user relinks a matched line to a different entry (that is a correction, not a new name).

### 5.1b Warn before leaving with unsaved drafts (decided by the user)

The review queue lives only in browser memory (`frontend/src/lib/drafts.tsx`); closing or reloading the page loses every unsaved draft, and re-importing pays for the extraction again. Today only a notice on the review page says so.

- While the queue holds at least one draft, closing, reloading, or leaving the site asks the browser's own "leave this page?" confirmation (`beforeunload`). Browsers show their own fixed text; the custom count of drafts cannot be shown there.
- Moving between pages inside the app keeps the queue (it lives in a provider above the pages), so no confirmation is needed there.
- Drafts are still not stored anywhere else. Keeping drafts across a closed page (stored on the server) is a separate feature, not in this plan.

### 5.2 Link from the "allergens unknown" list

The list (dictionary page, `DictionaryManager.tsx`, backed by `GET /api/dictionary/allergens/unresolved`) shows entries with unknown allergens and names of unmatched recipe lines. For an unmatched name it offers only "确认过敏原", which creates a new entry (`resolve_allergens`).

- Add **"关联到已有条目" / "Link to an existing entry"** next to it: the user picks an ingredient entry with the dictionary search, with the same "remember this link" checkbox, checked by default.
- On confirm, every unmatched recipe line with that name is linked to the chosen entry. With the box checked, the name also becomes an alias as in 5.1; unchecked, only today's lines are linked and future imports will ask again.
- The name then leaves the list. If the chosen entry's allergens are unknown, the entry appears in the list in its own right (as today).

### 5.3 Suggest the category and stock mode with the allergens

Today `POST /api/dictionary/allergen-suggestions` sends only the name (task `allergen_suggestions`, Gemini then OpenAI) and returns allergens; the new-entry form prefills the category ("seasoning" when the line's role is seasoning, otherwise "other") and stock mode "level", and `resolve_allergens` creates entries from the unknown list with "other" and "level".

- The same call also asks for one category from `CATEGORIES` and one stock mode from `STOCK_MODES`. The response gains `category` and `stock_mode` (each null when the model gives no valid value or no model is available).
- The new-entry form (review row) and the "confirm allergens" form of the unknown list prefill category and stock mode from the suggestion, shown as suggestions the user confirms or changes, as the allergens are today. When the suggestion is null, today's prefill stays (seasoning/other, level).
- The confirm-allergens path for an unmatched name sends the confirmed category and stock mode, so `resolve_allergens` no longer hard-codes "other" and "level" for a name the user confirmed.
- Prompt files: the prompt moves out of `allergen_suggest.py` into `backend/minemydinner/extraction/prompts/allergen_suggest_v1.md` (today's text, byte for byte the same wording) and `allergen_suggest_v2.md` (the new one). The code names the current version in a constant, like `extract_v2`. The v2 prompt explains each stock mode in one line: `count` for things counted in pieces (eggs, potatoes), `level` for things used up gradually (oil, salt, rice), `availability` for things one either has or not (for example a dried spice kept for long). `docs/prompt-versions.md` gets the new section and moves "Allergen suggestions" out of "Prompts without a version yet".
- A suggestion is never stored; only what the user confirms is (unchanged).

### 5.4 Check the v2 suggestion once (paid, approved by the user)

- A fixed list of 15-20 common ingredient names (mostly Chinese, a few English), each with the expected category, stock mode, and allergens, committed as test data (for example `backend/eval_data/allergen_suggest/names.yaml`). The user reviews the expected values before the paid run: which stock mode suits an ingredient is partly a household choice.
- A small script runs the current suggestion prompt over the list once on the configured model and reports, per field, how many suggestions match, and lists the mismatches. Expected cost about $0.01 per run; it runs only after the user approves it, on the test database (the call writes an `llm_calls` usage row).
- The outcome is recorded in `docs/prompt-versions.md` next to `allergen_suggest_v2` (date, model, matches per field, notable mismatches). If a field is often wrong, the user decides whether to adjust the prompt (a `_v3`) or keep it.

### 5.5 Amount fields in the review form

Today each line has a number, a unit (none, 克, 毫升, 个, 根, 大勺, 小勺, 勺) and an amount type (精确, 计数, 家用量具, 模糊, 未给出). With a number the type is fixed by the unit (`UNIT_AMOUNT_TYPES` in `constants.py`, mirrored in `frontend/src/lib/amounts.ts`), so the user must first move the type off 模糊 before entering a number, then picking a unit changes the type again, and a mismatch shows an error.

- **The type is derived, not chosen.** With a number it shows as a read-only label from the unit (精确 / 计数 / 家用量具). Typing a number or choosing a unit never clears anything.
- **Two always-visible toggle buttons, "适量" / "没写"** (English: "Unspecified amount" / "Not stated"), with a pressed state (`aria-pressed`). One click clears the number and unit and sets `vague` or `missing`; typing a number releases the pressed button. Clicking the pressed button again releases it and leaves the amount empty for the user to fill in.
- **The unit option for "a number with no unit" is renamed** from "无" to "家用量具（碗、杯、把、撮）" / "Household measure (bowl, cup, handful, pinch)". The type label stays "家用量具" / "Household measure". "无" no longer appears as a unit choice next to a number.
- **Internal naming note.** The stored amount type for household measures is `spoon` and the stored unit is null; the unit `spoon` separately means a spoon of unstated size. Comments next to `UNIT_AMOUNT_TYPES` and in `amounts.ts` say so, so the name is not read as "spoons only".
- Saving still validates on the backend as today; the derived type always agrees with the unit, so the mismatch error no longer appears in normal use.

## 6. Left to the builder

- API shapes: how the review save carries "remember" per line (for example a `remember_link: bool` on each ingredient line of the save request), and the endpoint for linking from the unknown list (a new `POST` under `/api/dictionary/` or an extension of `/allergens/resolve`).
- Whether adding one alias uses `set_personal_aliases` with the current list plus the name, or a new small service function; either must keep the conflict check and call `link_unmatched_lines`.
- Exact layout and wording of the checkbox, the link action in the unknown list, and the toggle buttons, following the existing plain CSS and the review form's look. Every string in both message files.
- The script's name and output format for 5.4, and whether its result folder is committed (no model text beyond the three short fields).
- The endpoint for matching a draft's unmatched names again (for example `POST /api/dictionary/ingredients/match` taking a list of names and returning the entry for each name that matches).
- Test cases, including: a later draft matching a name remembered in an earlier saved draft, and an entry created in an earlier draft; the re-match leaving linked, skipped, and renamed lines alone; the leave-page confirmation only while drafts are queued; a remembered link on a built-in entry and on a user entry; an unchecked link changing nothing in the dictionary; a name that became another entry's alias before saving; same-name lines in one draft; unlink-free alias removal; the unknown-list link with and without remembering; suggestion parsing with invalid category or stock mode; the amount toggles in both directions.
