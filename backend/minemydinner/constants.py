ALLERGENS = (
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
)
CATEGORIES = (
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
)
DISH_TYPES = (
    "meat dish",
    "vegetable dish",
    "soup",
    "staple food",
    "snack",
    "dessert",
    "other",
)
STOCK_MODES = ("availability", "count", "level")
STOCK_LEVELS = ("plenty", "low", "none")
AMOUNT_TYPES = ("exact", "count", "spoon", "vague", "missing")
ROLES = ("main", "secondary", "seasoning")
TIME_SOURCES = ("stated", "user", "estimated")
# "spoon" is a spoon whose size the source does not state.
UNITS = ("g", "ml", "piece", "stalk", "tablespoon", "teaspoon", "spoon")
# The amount type a line with a number must have. No unit means a household
# measure such as a bowl or a handful.
# Naming note: the stored amount type "spoon" with a null unit covers every
# household measure (bowl, cup, handful, pinch), not only spoons. The unit
# "spoon" is different: a spoon whose size the source does not state.
UNIT_AMOUNT_TYPES = {
    "g": "exact",
    "ml": "exact",
    "piece": "count",
    "stalk": "count",
    "tablespoon": "spoon",
    "teaspoon": "spoon",
    "spoon": "spoon",
    None: "spoon",
}
SOURCE_TYPES = ("text", "web", "screenshot", "batch", "manual")
DICTIONARY_SOURCES = ("seed", "user")
RULE_TYPES = ("allergy", "dietary restriction", "dislike")
# Diet words have no working rule form (spec 5.4): they are flagged, never made into rules.
DIET_WORDS: tuple[str, ...] = ("吃素", "素食", "全素", "vegetarian", "vegan")
LANGUAGES = ("zh", "en")
EMBEDDING_DIMENSION = 768
