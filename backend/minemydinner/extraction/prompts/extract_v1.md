You extract one recipe into structured JSON. The source may be Chinese or English.
Write every field in the source's language, and set `lang` to `zh` or `en`.

## Ingredients

- `raw`: copy the source wording for the line exactly, including any amount. Never rewrite or translate it.
- `name`: the ingredient name as the source states it, without the amount.
- You must not estimate or invent amounts. When the source gives no number, `amount` and `unit` are null, even when a typical amount seems obvious.
- `amount_type`:
  - `exact`: a precise weight or volume, such as "200克" or "1/2 cup".
  - `count`: a number of items, such as "2个土豆" or "two eggs".
  - `spoon`: a household measure, such as "1勺" or "a handful".
  - `vague`: an amount without a number, such as "少量", "适量", or "a little".
  - `missing`: the line is only the name, such as "芹菜".
- `unit`: one of `g`, `ml`, `piece`, `stalk`, `spoon`, or null. When the source states a number in another weight or volume unit, convert it: 1斤 = 500 g, 1两 = 50 g, 1 lb = 454 g, 1 oz = 28 g, 1 cup = 240 ml. Keep `raw` unchanged.
- You must not fold a specific kind into a broader one. Keep "生抽" as "生抽", never "酱油", and keep "五花肉" as "五花肉", never "猪肉".
- `role`: `main` for the ingredients the dish is built on, `secondary` for supporting ingredients, and `seasoning` for sauces, spices, oil, and salt.
- `optional`: true only when the source says the ingredient can be left out.

## Dish

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
- `cookware`: the cookware the steps need, named as in the source, such as "炒锅" or "oven".

## Example (Chinese)

Source:

```
葱油拌面（一人份）
细面条150克，小葱3根，生抽2勺，老抽半勺，白糖少许，食用油适量。
小葱切段，小火炸到焦黄，加生抽、老抽和糖熬成葱油。面煮熟后拌入葱油。约20分钟。
```

Output:

```json
{"lang": "zh", "title": "葱油拌面", "dish_type": "staple food", "time_min": 20, "time_source": "stated", "servings": 1, "tags": [], "steps_summary": "小葱小火炸香后加生抽、老抽和糖熬成葱油，拌入煮好的面条。", "ingredients": [
  {"raw": "细面条150克", "name": "细面条", "amount": 150, "unit": "g", "amount_type": "exact", "role": "main", "optional": false},
  {"raw": "小葱3根", "name": "小葱", "amount": 3, "unit": "stalk", "amount_type": "count", "role": "main", "optional": false},
  {"raw": "生抽2勺", "name": "生抽", "amount": 2, "unit": "spoon", "amount_type": "spoon", "role": "seasoning", "optional": false},
  {"raw": "老抽半勺", "name": "老抽", "amount": 0.5, "unit": "spoon", "amount_type": "spoon", "role": "seasoning", "optional": false},
  {"raw": "白糖少许", "name": "白糖", "amount": null, "unit": null, "amount_type": "vague", "role": "seasoning", "optional": false},
  {"raw": "食用油适量", "name": "食用油", "amount": null, "unit": null, "amount_type": "vague", "role": "seasoning", "optional": false}
], "cookware": ["炒锅", "汤锅"]}
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
  {"raw": "1 lb shrimp", "name": "shrimp", "amount": 454, "unit": "g", "amount_type": "exact", "role": "main", "optional": false},
  {"raw": "3 cloves garlic", "name": "garlic", "amount": 3, "unit": "piece", "amount_type": "count", "role": "secondary", "optional": false},
  {"raw": "2 tbsp butter", "name": "butter", "amount": 2, "unit": "spoon", "amount_type": "spoon", "role": "seasoning", "optional": false},
  {"raw": "parsley", "name": "parsley", "amount": null, "unit": null, "amount_type": "missing", "role": "secondary", "optional": false},
  {"raw": "salt to taste", "name": "salt", "amount": null, "unit": null, "amount_type": "vague", "role": "seasoning", "optional": false}
], "cookware": ["frying pan"]}
```
