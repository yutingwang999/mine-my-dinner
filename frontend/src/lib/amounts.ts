import type { AmountType, IngredientLine, Unit } from "@/lib/api";

/**
 * Mirrors UNIT_AMOUNT_TYPES in backend/minemydinner/constants.py.
 *
 * Naming note: the stored amount type "spoon" with a null unit means any household
 * measure (a bowl, a cup, a handful, a pinch), not only spoons. The unit "spoon" is
 * a different thing: a spoon whose size the source does not state.
 */
const UNIT_AMOUNT_TYPES: Record<Unit, AmountType> = {
  g: "exact",
  ml: "exact",
  piece: "count",
  stalk: "count",
  tablespoon: "spoon",
  teaspoon: "spoon",
  spoon: "spoon",
};

/** The amount type a line with a number must have. No unit means a bowl, a handful, and so on. */
export function typeForUnit(unit: Unit | null): AmountType {
  return unit === null ? "spoon" : UNIT_AMOUNT_TYPES[unit];
}

export type AmountProblem = { kind: "zero" };

/** What the server would reject in a line's amount, or null when it is fine. */
export function amountProblem(line: Pick<IngredientLine, "amount">): AmountProblem | null {
  return line.amount !== null && line.amount <= 0 ? { kind: "zero" } : null;
}

/**
 * The amount fields as they are saved. With a number the type always follows the unit;
 * without one the line is vague or missing, and has no unit.
 */
export function savedAmount(
  line: Pick<IngredientLine, "amount" | "unit" | "amount_type">,
): Pick<IngredientLine, "amount" | "unit" | "amount_type"> {
  if (line.amount !== null) {
    return { amount: line.amount, unit: line.unit, amount_type: typeForUnit(line.unit) };
  }
  return {
    amount: null,
    unit: null,
    amount_type: line.amount_type === "vague" ? "vague" : "missing",
  };
}
