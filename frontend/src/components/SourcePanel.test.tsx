import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SourcePanel } from "@/components/SourcePanel";
import { renderWithProviders, savedRecipe } from "@/test/helpers";

const IMAGE = `${"a".repeat(64)}.png`;

describe("SourcePanel", () => {
  it("shows only the link for a web import", () => {
    renderWithProviders(<SourcePanel recipe={savedRecipe({ source_text: null })} />);
    const link = screen.getByRole("link", { name: /recipes\.example/ });
    expect(link).toHaveAttribute("href", "https://recipes.example/1");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noreferrer"));
    expect(screen.queryByText("原文：番茄两个")).toBeNull();
    expect(screen.queryByText("No original text was saved.")).toBeNull();
  });

  it("shows the link and the pasted text", () => {
    renderWithProviders(<SourcePanel recipe={savedRecipe()} />);
    expect(screen.getByText(/^Source:/)).toBeInTheDocument();
    expect(screen.getByText("原文：番茄两个").tagName).toBe("PRE");
  });

  it("shows screenshots", () => {
    renderWithProviders(
      <SourcePanel
        recipe={savedRecipe({
          source_url: null,
          source_text: null,
          images: [{ file_name: IMAGE, mime_type: "image/png", position: 0 }],
        })}
      />,
    );
    expect(screen.getByRole("img", { name: "Screenshot 1" })).toHaveAttribute("src", `/api/images/${IMAGE}`);
  });

  it("says so when nothing was kept", () => {
    renderWithProviders(<SourcePanel recipe={savedRecipe({ source_url: null, source_text: null })} />);
    expect(screen.getByText("No original text was saved.")).toBeInTheDocument();
  });
});
