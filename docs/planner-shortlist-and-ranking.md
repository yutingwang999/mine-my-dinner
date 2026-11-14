# How the Planner's Candidate List Is Built

This explains how the code chooses the dishes the planning model sees first: how recipes are searched, ranked, spread across main ingredients, and protected so that enough usable dishes reach the model. It describes the behaviour of Phase 1 plan 2a (`backend/minemydinner/planning/search.py` and `shortlist.py`). The design is in `docs/superpowers/specs/2026-11-14-phase-1-plan-2a-planner-efficiency-design.md`, Sections 5.4 and 5.6.

The model still chooses the dishes. Code only decides which candidates it sees first and in what order. Hard rules (allergies, dish counts, named ingredients, time limits) are checked by code after the model chooses, whatever the order was.

## 1. The pipeline

The shortlist is built in five steps, in this order:

1. **Fetch.** For each dish type the meal needs, fetch the recipes the diners can eat. Allergy and dietary exclusions, dishes left out for unknown allergens, the time limit (firm times only), "exclude recently cooked" (only when asked), and "every dish must use X" (`all_use`) are applied here. A recipe removed here never appears in any later step.
2. **Rank.** Order the fetched recipes by the nine levels in Section 2.
3. **Fill, spreading main ingredients.** Walk down the ranked list and add recipes one by one, at most two per identical set of main ingredients, until the category's slots are full (Section 4).
4. **Protect.** Check that the list holds enough makeable dishes and at least one dish for each "must use" ingredient; if not, put the right recipes back (Section 5).
5. **Attach facts.** Each chosen recipe carries its missing ingredients, missing cookware, the soon-to-expire ingredients it uses, dislike hits, time and its source, and servings.

**Slots per dish type:** `max(5, needed + 3)`, where `needed` is how many dishes of that type the meal requires. For "8 dishes, 2 of them meat", the vegetable list gets `max(5, 6 + 3) = 9` slots. Only the dish types the request needs are shortlisted, and the type "other" (drinks, sauces) never is.

If the shortlist is not enough, the model can search again with `exclude_ids` to get the next batch, so recipes cut by the slot limit are still reachable.

## 2. Ranking: nine levels

Ranking compares level 1 first; only recipes tied on level 1 are compared on level 2, and so on.

| Level | Compares | Notes |
|---|---|---|
| 1 | Requested tags (for example "凉菜") found in the title or tags | Matching recipes first |
| 2 | Makeable now | **Fridge-only mode only.** Recipes with nothing missing come first |
| 3 | Relevance to the request text | Meaning-based (vector) closeness when the recipe has an embedding; otherwise the number of query words found in title, tags, and ingredient names |
| 4 | Uses a named ingredient ("must use", "prefer to use") | Recipes using more of them first |
| 5 | Fewer missing ingredients | |
| 6 | Uses ingredients close to expiry | Only where an expiry date was entered |
| 7 | Hits a diner's dislike | Disliked dishes go last; they are not removed |
| 8 | Time source | Only when a time limit is set: a time stated in the recipe before an estimated one |
| 9 | Rating, then most recently imported | Ratings arrive with T1.6 |

**When a query is given, level 3 almost never ties**, because meaning-based closeness differs for every recipe. Levels 4 to 9 then act only as rare tie-breakers. This is why, in fridge-only mode, "makeable" sits at level 2, before relevance: a dish that cannot be made from the fridge fails H1 anyway, so it should never sit above one that can. In shopping mode every dish can be made, and relevance stays first.

**When there is no query**, level 3 ties for every recipe, and levels 4 to 9 decide the order fully.

### What the meaning-based search compares

Each recipe's vector is computed at import and after every edit from: its title, its ingredient names (no amounts), its tags, and its steps summary. The original source text, amounts, cookware, time, servings, and dish type are not part of it. A query is turned into a vector with the same model; within one planning run, the same query text is turned into a vector only once and then reused. Recipes still without a vector (for example when the embedding service was unavailable at import) fall back to word matching until the vector is filled in later.

### Example: levels 1 to 3

Request: "来两道清淡的素菜，最好是凉菜." The query is "清淡"; the model passes "凉菜" as a tag.

| Dish | Tag "凉菜" | Makeable from the fridge | Closeness to "清淡" (smaller is closer) |
|---|---|---|---|
| A 凉拌黄瓜 | yes | yes | 0.20 |
| B 凉拌木耳 | yes | no (missing 木耳) | 0.15 |
| C 清炒西兰花 | no | yes | 0.10 |
| D 蒜蓉菠菜 | no | no (missing 菠菜) | 0.12 |
| E 白灼生菜 | no | yes | 0.18 |
| F 干煸豆角 | no | yes | 0.60 |

**Fridge-only mode:**

```
Level 1 (tag):       [A, B]            [C, D, E, F]
Level 2 (makeable):  [A] [B]           [C, E, F] [D]
Level 3 (closeness): A, B,             C (0.10), E (0.18), F (0.60), D
Result:              A 凉拌黄瓜, B 凉拌木耳, C 清炒西兰花, E 白灼生菜, F 干煸豆角, D 蒜蓉菠菜
```

B is closer to "清淡" than A but cannot be made, so it comes after A. D is very close to "清淡" but cannot be made, so it comes after every makeable non-cold dish.

**Shopping mode** (level 2 does not apply):

```
Result: B 凉拌木耳 (0.15), A 凉拌黄瓜 (0.20), C 清炒西兰花 (0.10), D 蒜蓉菠菜 (0.12), E 白灼生菜 (0.18), F 干煸豆角 (0.60)
```

Now B comes before A: its missing 木耳 can be bought, so being closer to "清淡" decides.

### Example: levels 4 to 9

Request: "今晚做两道素菜，可以买菜，30分钟以内，优先用鸡蛋." There is no query, so levels 1 to 3 tie for every recipe. The tomatoes and spinach in the fridge are close to expiry, and one diner dislikes spring onion.

| Dish | 4 Uses egg | 5 Missing | 6 Uses expiring | 7 Disliked | 8 Time source | 9 Rating |
|---|---|---|---|---|---|---|
| 番茄炒蛋 | yes | 0 | tomato | | stated | 4 |
| 水煮蛋 | yes | 0 | | | estimated | – |
| 葱花蒸蛋 | yes | 0 | | spring onion | stated | 5 |
| 韭菜炒蛋 | yes | 1 (韭菜) | | | stated | 5 |
| 清炒菠菜 | | 0 | spinach | | estimated | 3 |
| 白灼菜心 | | 0 | | | stated | 5 |
| 手撕包菜 | | 0 | | | stated | 3 |
| 蒜蓉生菜 | | 0 | | | estimated | 4 |
| 地三鲜 | | 2 | | | stated | 5 |

Level by level:

- **Level 4:** the four egg dishes come first.
- **Level 5:** 韭菜炒蛋 (missing one) drops to the end of the egg group; 地三鲜 (missing two) drops to the very end.
- **Level 6:** 番茄炒蛋 leads the egg group and 清炒菠菜 leads the other group, both using ingredients close to expiry.
- **Level 7:** 水煮蛋 and 葱花蒸蛋 are still tied; 葱花蒸蛋 uses spring onion, which a diner dislikes, so it goes after 水煮蛋.
- **Level 8** (a 30-minute limit is set): 白灼菜心, 手撕包菜, and 蒜蓉生菜 are still tied; the first two have stated times, so 蒜蓉生菜 (estimated) goes after them.
- **Level 9:** 白灼菜心 (rating 5) before 手撕包菜 (rating 3).

Final order:

```
1 番茄炒蛋   uses egg, nothing missing, uses expiring tomato
2 水煮蛋     uses egg, nothing missing
3 葱花蒸蛋   uses egg, nothing missing, a diner dislikes spring onion
4 韭菜炒蛋   uses egg, missing 韭菜
5 清炒菠菜   nothing missing, uses expiring spinach
6 白灼菜心   nothing missing, stated time, rating 5
7 手撕包菜   nothing missing, stated time, rating 3
8 蒜蓉生菜   nothing missing, estimated time
9 地三鲜     missing two
```

Ranking only decides which recipes enter the shortlist first; it never removes one. "Must use" and "every dish uses" are enforced by the `all_use` filter and by validation (H7); the time limit by search and validation (H6). A disliked dish ranks low but can still be chosen, and the plan card then warns about it.

## 3. Named ingredients

The request can name ingredients in three ways (design 5.1):

| The user says | Field | Shortlist | Validation |
|---|---|---|---|
| "优先用鸡蛋" | `prefer_use` | ranks dishes using it first (level 4) | none |
| "这顿一定要用鸡蛋" | `must_use` | ranks them first and keeps at least one (Section 5) | H7: at least one dish uses it |
| "全做鸡蛋" | `all_use` | only dishes using it (step 1) | H7: every dish uses it |

A dish "uses" an ingredient when it has a line linked to that dictionary entry or one of its children ("猪肉" is satisfied by 猪肉末), or, for an unlinked line or a name the dictionary does not know, when the name appears in the line's name or original text. One implementation of this rule (`planning/named.py`) serves search, the shortlist, the feasibility check, and validation.

## 4. Spreading main ingredients

While filling the list (step 3), the code walks down the ranked recipes and adds each one unless two recipes with the same **set of main ingredients** are already in the list.

- A recipe's main ingredients are its lines marked `main` that are linked to the dictionary, each compared through its parent entry when it has one (so 猪肉末 and 猪里脊 both count as 猪肉).
- Whole sets are compared, not single ingredients: {番茄, 鸡蛋} and {鸡蛋} are different sets.
- A recipe with no linked main line is never grouped with another.
- **Named ingredients are removed from every set before comparing.** If the user asked for eggs in any of the three ways, eggs are set aside, so the spread varies what goes with the eggs and never removes what the user asked for. A set that is empty after removal counts as unique.

### Example: filling with the spread

Request: "今晚两道素菜，可以买菜." No ingredient is named. Two vegetable dishes are needed, so there are `max(5, 2 + 3) = 5` slots.

| Rank | Dish | Main ingredients |
|---|---|---|
| 1 | 番茄炒蛋 | {番茄, 鸡蛋} |
| 2 | 水煮蛋 | {鸡蛋} |
| 3 | 葱花蒸蛋 | {鸡蛋} |
| 4 | 溏心蛋 | {鸡蛋} |
| 5 | 韭菜炒蛋 | {韭菜, 鸡蛋} |
| 6 | 清炒菠菜 | {菠菜} |
| 7 | 白灼菜心 | {菜心} |
| 8 | 手撕包菜 | {包菜} |

```
1 番茄炒蛋 {番茄,鸡蛋}  added (first of its set)          1 in list
2 水煮蛋   {鸡蛋}       added (first {鸡蛋})               2 in list
3 葱花蒸蛋 {鸡蛋}       added (second {鸡蛋})              3 in list
4 溏心蛋   {鸡蛋}       skipped ({鸡蛋} already has two)
5 韭菜炒蛋 {韭菜,鸡蛋}  added (a different set)            4 in list
6 清炒菠菜 {菠菜}       added                              5 in list, slots full
7 白灼菜心, 8 手撕包菜  not added (slots full; reachable by searching with exclude_ids)
```

Shortlist: 番茄炒蛋, 水煮蛋, 葱花蒸蛋, 韭菜炒蛋, 清炒菠菜.

**The same request with "优先用鸡蛋":** eggs are removed before comparing, so the sets become 番茄炒蛋 {番茄}, 水煮蛋 {}, 葱花蒸蛋 {}, 溏心蛋 {}, 韭菜炒蛋 {韭菜}. The three empty sets are each unique, so 溏心蛋 is no longer skipped for being "another egg dish".

## 5. Protections

After filling, two checks run:

1. **Enough makeable dishes.** If the list holds fewer makeable dishes than the type needs (fridge-only: nothing missing; shopping: every dish), the highest-ranked makeable recipes that were skipped by the spread or cut by the slot limit are put back.
2. **At least one dish per "must use" ingredient.** If no dish in the list uses a `must_use` ingredient while the library has one, the highest-ranked such dish is put back (in fridge-only mode, a makeable one when it exists, because makeable dishes rank first).

A recipe put back replaces the lowest-ranked entry that is not itself protected, and it ignores the "two per set" limit: being able to cook the meal matters more than variety. In the rare case where every entry is protected (for example, several "must use" ingredients each with its only dish in the list), the list grows past its slots by one per such dish.

### Example: only eggs in the fridge

Request: "三道素菜，就用冰箱里的." The fridge holds only eggs (plus always-in-stock seasonings). Three vegetable dishes are needed; slots are `max(5, 3 + 3) = 6`.

The makeable vegetable dishes are all egg-only: 蒸蛋羹, 葱花煎蛋, 水煮蛋, 溏心蛋, each with main ingredients {鸡蛋}. The other vegetable dishes are missing something.

- **Feasibility check** (before the shortlist): four makeable vegetable dishes exist and three are needed, so planning goes ahead.
- **Fill with the spread:** makeable dishes rank first (level 2), but {鸡蛋} allows only two, so 蒸蛋羹 and 葱花煎蛋 go in and 水煮蛋 and 溏心蛋 are skipped. The remaining four slots go to dishes that are missing ingredients.
- **Protection 1:** only two makeable dishes are in the list, but three are needed. The highest-ranked skipped makeable dish, 水煮蛋, is put back in place of the lowest-ranked dish that is missing ingredients.
- **Result:** the model sees three dishes it can actually cook and can plan the meal on the first attempt.

Without this protection, the model would see only two makeable dishes, try to fill the third slot with a dish that is missing ingredients, fail H1, and retry or search again.
