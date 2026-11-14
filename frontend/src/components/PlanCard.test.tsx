import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PlanCard } from "@/components/PlanCard";
import { plan } from "@/test/chatFixtures";
import { renderWithProviders } from "@/test/helpers";

describe("PlanCard", () => {
  it("always shows the allergen notice with no way to close it", () => {
    renderWithProviders(<PlanCard plan={plan()} />);
    expect(screen.getByText(/check the actual ingredients for allergens/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /close|dismiss/i })).toBeNull();
    expect(screen.getByText("葱花蛋")).toBeInTheDocument();
    expect(screen.getByText(/Vegetable dish/)).toBeInTheDocument();
    expect(screen.getByText(/10 min \(stated in the source\)/)).toBeInTheDocument();
  });

  it("shows the allergen notice even for a plan with no dishes", () => {
    renderWithProviders(
      <PlanCard
        plan={plan({
          recipes: [],
          passed: false,
          unmet: [{ code: "H2", recipe_id: null, text: "Every plan found had a dish that breaks a diner's allergies" }],
          next_actions: ["fewer_dishes"],
        })}
      />,
    );
    expect(screen.getByText(/check the actual ingredients for allergens/)).toBeInTheDocument();
    expect(screen.getByText(/Every plan found had a dish/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Fewer dishes" })).toBeInTheDocument();
  });

  it("states defaulted values", () => {
    renderWithProviders(<PlanCard plan={plan({ defaults_used: ["headcount", "dishes", "mode"] })} />);
    expect(
      screen.getByText("Assumed: 1 person, 1 dish, fridge only. Say if that's wrong."),
    ).toBeInTheDocument();
  });

  it("states only the defaulted dish count or soup count", () => {
    const spec = { mode: "fridge_only" as const, dishes: 2, soups: 1, time_limit_min: null };
    const { unmount } = renderWithProviders(
      <PlanCard plan={plan({ defaults_used: ["soups"], spec })} />,
    );
    expect(screen.getByText("Assumed: 1 soup. Say if that's wrong.")).toBeInTheDocument();
    unmount();
    renderWithProviders(<PlanCard plan={plan({ defaults_used: ["dishes"], spec })} />);
    expect(screen.getByText("Assumed: 2 dishes. Say if that's wrong.")).toBeInTheDocument();
  });

  it("treats an old composition value as both counts", () => {
    renderWithProviders(
      <PlanCard
        plan={plan({
          defaults_used: ["composition"],
          spec: { mode: "fridge_only", dishes: 2, soups: 1, time_limit_min: null },
        })}
      />,
    );
    expect(screen.getByText("Assumed: 2 dishes, 1 soup. Say if that's wrong.")).toBeInTheDocument();
  });

  it("states only the defaulted values", () => {
    renderWithProviders(
      <PlanCard
        plan={plan({
          defaults_used: ["mode"],
          headcount: 3,
          spec: { mode: "fridge_only", dishes: 2, soups: 1, time_limit_min: null },
        })}
      />,
    );
    expect(screen.getByText("Assumed: fridge only. Say if that's wrong.")).toBeInTheDocument();
  });

  it("lists unmet rules with next-action buttons", async () => {
    const onAction = vi.fn();
    renderWithProviders(
      <PlanCard
        onAction={onAction}
        plan={plan({
          passed: false,
          unmet: [{ code: "H1", recipe_id: 1, text: "葱花蛋: 1 ingredient missing (葱)" }],
          next_actions: ["switch_to_shopping", "fewer_dishes", "drop_time_limit"],
        })}
      />,
    );
    expect(screen.getByText("葱花蛋: 1 ingredient missing (葱)")).toBeInTheDocument();
    expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual([
      "Allow shopping",
      "Fewer dishes",
      "No time limit",
    ]);
    await userEvent.click(screen.getByRole("button", { name: "Allow shopping" }));
    expect(onAction).toHaveBeenCalledWith("switch_to_shopping");
  });

  it("shows missing items, warnings, and servings notes in plain words", () => {
    renderWithProviders(
      <PlanCard
        plan={plan({
          missing: { "1": ["葱", "盐"] },
          missing_cookware: { "1": ["炒锅"] },
          warnings: [
            { code: "S7", recipe_id: 1, detail: { kind: "estimated", time_min: 15 } },
            { code: "S8", recipe_id: null, detail: { unconfirmed_diners: 2 } },
          ],
          headcount: 3,
          servings_notes: [{ recipe_id: 1, servings: 2, headcount: 3 }],
        })}
      />,
    );
    expect(screen.getByText("Missing ingredients: 葱, 盐")).toBeInTheDocument();
    expect(screen.getByText("Missing cookware: 炒锅")).toBeInTheDocument();
    expect(screen.getByText("葱花蛋: the cooking time (15 min) is only an estimate.")).toBeInTheDocument();
    expect(screen.getByText(/2 other diners have unknown dietary limits/)).toBeInTheDocument();
    expect(screen.getByText("葱花蛋 makes 2 servings; you are cooking for 3 people.")).toBeInTheDocument();
  });

  it("localizes known units in the stock warning and falls back to the raw unit", () => {
    renderWithProviders(
      <PlanCard
        plan={plan({
          warnings: [
            { code: "S1", recipe_id: 1, detail: { name: "oil", needed: 2, have: 1, unit: "tablespoon" } },
            { code: "S1", recipe_id: 1, detail: { name: "rice", needed: 2, have: 1, unit: "bowl" } },
          ],
        })}
      />,
    );
    expect(screen.getByText("葱花蛋: needs 2 tbsp of oil, but you have 1 tbsp.")).toBeInTheDocument();
    expect(screen.getByText("葱花蛋: needs 2 bowl of rice, but you have 1 bowl.")).toBeInTheDocument();
  });

  it("counts dishes left out for unknown allergens and lists their titles on request", async () => {
    renderWithProviders(<PlanCard plan={plan({ left_out_unknown_allergens: ["秘制酱鸡", "怪味面"] })} />);
    expect(screen.getByText(/2 dishes were left out because an ingredient has an unknown allergen/)).toBeInTheDocument();
    await userEvent.click(screen.getByText("Show which dishes"));
    const list = screen.getByRole("list", { name: "Dishes left out" });
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["秘制酱鸡", "怪味面"]);
  });

  it("shows the semantic-search notice when paused", () => {
    renderWithProviders(<PlanCard plan={plan({ semantic_paused: true })} />);
    expect(screen.getByText(/Search by meaning is paused/)).toBeInTheDocument();
  });

  it("omits the semantic-search notice when search works", () => {
    renderWithProviders(<PlanCard plan={plan()} />);
    expect(screen.queryByText(/Search by meaning is paused/)).toBeNull();
  });
});
