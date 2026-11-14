import { screen } from "@testing-library/react";
import { expect, it } from "vitest";

import { ReviewForm } from "@/components/ReviewForm";
import { draft, renderWithProviders } from "@/test/helpers";

it("warns when the model saw several recipes", () => {
  renderWithProviders(<ReviewForm draft={draft({ recipe_count: 2 })} onSaved={() => {}} />);
  expect(screen.getByRole("alert")).toHaveTextContent(
    "This text seems to hold 2 recipes; only the first was read.",
  );
});

it("shows no warning for one recipe or a draft queued before the count existed", () => {
  renderWithProviders(<ReviewForm draft={draft({ recipe_count: 1 })} onSaved={() => {}} />);
  expect(screen.queryByRole("alert")).toBeNull();
  renderWithProviders(<ReviewForm draft={draft()} onSaved={() => {}} />);
  expect(screen.queryByRole("alert")).toBeNull();
});
