import type { AmountType, IngredientLine, Unit } from "@/lib/api";

/** Mirrors UNIT_AMOUNT_TYPES in backend/minemydinner/constants.py. */
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

export type AmountProblem = { kind: "zero" } | { kind: "type"; expected: AmountType };

/** What the server would reject in a line's amount, or null when it is fine. */
export function amountProblem(
  line: Pick<IngredientLine, "amount" | "unit" | "amount_type">,
): AmountProblem | null {
  if (line.amount === null) return null;
  if (line.amount <= 0) return { kind: "zero" };
  const expected = typeForUnit(line.unit);
  return expected === line.amount_type ? null : { kind: "type", expected };
}
