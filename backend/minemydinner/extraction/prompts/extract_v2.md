You extract one recipe into structured JSON. The source may be Chinese or English.
Write every field in the source's language, and set `lang` to `zh` or `en`.

## Ingredients

- `raw`: copy the source wording for the line exactly, including any amount. Never rewrite or translate it.
- `name`: the ingredient name as the source states it, without the amount.
- You must not estimate or invent amounts. When the source gives no number, `amount` and `unit` are null, even when a typical amount seems obvious.
- `amount_type`:
  - `exact`: a precise weight or volume, such as "200克" or "1/2 cup".
  - `count`: a number of items, such as "2个土豆" or "two eggs".
  - `spoon`: a household measure, such as "1勺", "2 tbsp", "一碗", "a handful", or "一撮".
  - `vague`: an amount without a number, such as "少量", "适量", or "a little".
  - `missing`: the whole text says nothing about the amount.
- The whole text is evidence, not only the ingredient list. An amount stated in the method counts: a line listed only as "香油" with "淋上一点点香油" in the steps is `vague`, not `missing`. An ingredient the method uses but the list omits, such as the oil a step fries in or the water a sauce is thinned with, still gets its own line.
- `unit`: one of `g`, `ml`, `piece`, `stalk`, `tablespoon`, `teaspoon`, `spoon`, or null. When the source states a number in another weight or volume unit, convert it: 1斤 = 500 g, 1两 = 50 g, 1 lb = 454 g, 1 oz = 28 g, and a measuring cup in an English recipe, 1 cup = 240 ml. Keep `raw` unchanged.
- Spoons: "大勺", "汤匙", and "tbsp" are `tablespoon`; "小勺", "茶匙", and "tsp" are `teaspoon`. When the spoon's size is not stated, such as "1勺" or "a spoonful", the unit is `spoon`. You must not guess the size.
- Other household measures, such as a bowl ("碗"), a cup ("杯") in a Chinese recipe, a handful ("把"), or a pinch ("撮"): keep the number in `amount`, set `unit` to null, and set `amount_type` to `spoon`.
- You must not fold a specific kind into a broader one. Keep "生抽" as "生抽", never "酱油", and keep "五花肉" as "五花肉", never "猪肉".
- `role`: `main` for the ingredients the dish is built on, `secondary` for supporting ingredients, and `seasoning` for sauces, spices, oil, and salt.
- `optional`: true only when the source says the ingredient can be left out.
- `choice_group`: an integer shared by lines that are alternatives ("pick one of these"), numbered 1, 2, … within the recipe; null for an ordinary line. "A（B）" and "A或B" become two lines sharing one group. A generic line such as "时蔬" or "any greens" must not be kept when the source names the items it means anywhere in the text — a recommendation list, "such as", or the method steps. Replace it with one line per named item, all sharing one group: "时蔬" with "推荐：西兰花、芦笋" becomes the lines 西兰花 and 芦笋 sharing a group. You must not invent items the source does not name; when the source names none, keep the generic line ungrouped. A group needs at least two lines.
- Each ingredient appears exactly once. Before adding any line from the method, a formula, or a recommendation, check the lines you already have: the same ingredient in another wording or preparation (a listed egg reappearing as a fried or boiled egg) is still one line, never a second line and never a choice group. A choice group is for different ingredients, not ways to prepare one. When a pairing or formula names something already listed, add only the items that are new.

## Dish

- `title`: the dish name only. Remove numbering, bullets, and symbols such as "1.1.2." or "■", and a trailing index such as the "1" in "炸鸡块1". Keep words that tell versions apart, such as "（日式）" or "（版本2）" — "炸鸡块（版本2）" keeps "（版本2）". When the source has no title, write a short name for the dish in the source's language.
- `dish_type`:
  - `meat dish`: any dish with meat or seafood.
  - `vegetable dish`: other dishes, including egg and tofu dishes.
  - `soup`: soups.
  - `staple food`: rice, noodle, and bread dishes, including fried rice and noodle dishes.
  - `snack` and `dessert`: as named.
  - `other`: drinks and sauces.
- `tags`: short tags such as "凉菜" or "cold dish" for cold dishes, or a taste such as "辣".
- `time_min` and `time_source`: when the source states the total cooking time, give it in minutes with `stated`. When it does not, give your estimate with `estimated`. You must not mark an estimate as `stated`. When you cannot estimate, both are null.
- `servings`: the number of servings the source states, or null.
- `steps_summary`: a short paraphrase of the steps in one to three sentences. You must not copy the source steps.
- `recipe_count`: the number of distinct recipes the input contains, at least 1. Extract only the first one. A recipe with variations or optional steps is one recipe.
- `cookware`: cookware the steps need that not every kitchen has, such as pots, pans, appliances, steamers, baking trays, and molds, named as in the source. You must not list basic utensils that every kitchen has: spoons, chopsticks, spatulas, ladles, bowls, plates, knives, or cutting boards. When the source only says "锅", name the specific kind the steps imply, such as "炒锅" for stir-frying or "汤锅" for boiling. When the kind is unclear, leave it out.
- `supplies`: consumables the steps use up, such as plastic wrap ("保鲜膜"), foil ("锡纸"), baking paper ("烘焙纸"), kitchen paper, skewers, and toothpicks, named as in the source. They go in `supplies`, never in `cookware` or `ingredients`. When there are none, give an empty list.

## Example (Chinese)

Source:

```
葱油拌面（一人份）
细面条150克，小葱3根，生抽2勺，老抽半勺，白糖少许，食用油适量。
小葱切段，小火炸到焦黄，加生抽、老抽和糖熬成葱油。面煮熟后拌入葱油，撒上香菜（或小葱花）。约20分钟。
```

Output:

```json
{"lang": "zh", "title": "葱油拌面", "dish_type": "staple food", "time_min": 20, "time_source": "stated", "servings": 1, "tags": [], "steps_summary": "小葱小火炸香后加生抽、老抽和糖熬成葱油，拌入煮好的面条。", "ingredients": [
  {"raw": "细面条150克", "name": "细面条", "amount": 150, "unit": "g", "amount_type": "exact", "role": "main", "optional": false, "choice_group": null},
  {"raw": "小葱3根", "name": "小葱", "amount": 3, "unit": "stalk", "amount_type": "count", "role": "main", "optional": false, "choice_group": null},
  {"raw": "生抽2勺", "name": "生抽", "amount": 2, "unit": "spoon", "amount_type": "spoon", "role": "seasoning", "optional": false, "choice_group": null},
  {"raw": "老抽半勺", "name": "老抽", "amount": 0.5, "unit": "spoon", "amount_type": "spoon", "role": "seasoning", "optional": false, "choice_group": null},
  {"raw": "白糖少许", "name": "白糖", "amount": null, "unit": null, "amount_type": "vague", "role": "seasoning", "optional": false, "choice_group": null},
  {"raw": "食用油适量", "name": "食用油", "amount": null, "unit": null, "amount_type": "vague", "role": "seasoning", "optional": false, "choice_group": null},
  {"raw": "香菜（或小葱花）", "name": "香菜", "amount": null, "unit": null, "amount_type": "missing", "role": "secondary", "optional": false, "choice_group": 1},
  {"raw": "香菜（或小葱花）", "name": "小葱花", "amount": null, "unit": null, "amount_type": "missing", "role": "secondary", "optional": false, "choice_group": 1}
], "cookware": ["炒锅", "汤锅"], "supplies": []}
```

## Example (English)

Source:

```
Garlic Butter Shrimp
1 lb shrimp, 3 cloves garlic, 2 tbsp butter, parsley, salt to taste.
Melt the butter, fry the garlic, add the shrimp and cook until pink. Top with parsley.
```

Output:

```json
{"lang": "en", "title": "Garlic Butter Shrimp", "dish_type": "meat dish", "time_min": 15, "time_source": "estimated", "servings": null, "tags": [], "steps_summary": "Fry garlic in melted butter, cook the shrimp until pink, and top with parsley.", "ingredients": [
  {"raw": "1 lb shrimp", "name": "shrimp", "amount": 454, "unit": "g", "amount_type": "exact", "role": "main", "optional": false, "choice_group": null},
  {"raw": "3 cloves garlic", "name": "garlic", "amount": 3, "unit": "piece", "amount_type": "count", "role": "secondary", "optional": false, "choice_group": null},
  {"raw": "2 tbsp butter", "name": "butter", "amount": 2, "unit": "tablespoon", "amount_type": "spoon", "role": "seasoning", "optional": false, "choice_group": null},
  {"raw": "parsley", "name": "parsley", "amount": null, "unit": null, "amount_type": "missing", "role": "secondary", "optional": false, "choice_group": null},
  {"raw": "salt to taste", "name": "salt", "amount": null, "unit": null, "amount_type": "vague", "role": "seasoning", "optional": false, "choice_group": null}
], "cookware": ["frying pan"], "supplies": []}
```

## Example (two recipes pasted together)

Source:

```
Toast with Butter
2 slices bread, 1 tbsp butter. Butter the bread and toast it in a pan until golden.

Plain Rice
1 cup rice, 1.5 cups water. Rinse the rice, add the water, and simmer covered for 15 minutes.
```

Output (only the first recipe is extracted; `recipe_count` is 2):

```json
{"lang": "en", "title": "Toast with Butter", "dish_type": "staple food", "time_min": 5, "time_source": "estimated", "servings": null, "tags": [], "steps_summary": "Butter the bread and toast it in a pan until golden.", "ingredients": [
  {"raw": "2 slices bread", "name": "bread", "amount": 2, "unit": "piece", "amount_type": "count", "role": "main", "optional": false, "choice_group": null},
  {"raw": "1 tbsp butter", "name": "butter", "amount": 1, "unit": "tablespoon", "amount_type": "spoon", "role": "seasoning", "optional": false, "choice_group": null}
], "cookware": ["frying pan"], "supplies": [], "recipe_count": 2}
```
