import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RecipeDetail } from "@/components/RecipeDetail";
import { renderWithProviders, savedRecipe } from "@/test/helpers";

describe("RecipeDetail", () => {
  it("shows amounts, a household measure, and one line per choice group", () => {
    renderWithProviders(<RecipeDetail recipe={savedRecipe()} />);
    expect(screen.getByText(/番茄.*300 g/)).toBeInTheDocument();
    expect(screen.getByText(/盐.*to taste/)).toBeInTheDocument();
    expect(screen.getByText(/碗.*1 Household measure/)).toBeInTheDocument();
    const choice = screen.getAllByText(/Choose one/);
    expect(choice).toHaveLength(1);
    expect(choice[0]).toHaveTextContent(/白糖.*蜂蜜/);
    expect(screen.getByText(/葱.*optional/)).toBeInTheDocument();
    // A missing amount shows the name alone.
    expect(screen.getByText("秘制酱")).toBeInTheDocument();
  });

  it("marks the missing ingredients", () => {
    renderWithProviders(<RecipeDetail recipe={savedRecipe()} missing={["番茄"]} />);
    expect(screen.getByText(/番茄.*300 g.*missing/)).toBeInTheDocument();
    expect(screen.getByText(/盐.*to taste/)).not.toHaveTextContent(/missing/);
  });

  it("shows cookware, time, servings, steps, and no source link", () => {
    renderWithProviders(<RecipeDetail recipe={savedRecipe()} />);
    expect(screen.getByText(/炒锅/)).toBeInTheDocument();
    expect(screen.getByText(/10 min \(stated in the source\)/)).toBeInTheDocument();
    expect(screen.getByText(/Servings: 2/)).toBeInTheDocument();
    expect(screen.getByText("鸡蛋炒熟后撒葱花。")).toBeInTheDocument();
    // The source link lives in the original text panel.
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("compact hides the servings", () => {
    renderWithProviders(<RecipeDetail recipe={savedRecipe()} compact />);
    expect(screen.queryByText(/Servings/)).toBeNull();
  });
});
