import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RecipeLibrary } from "@/components/RecipeLibrary";
import type { SavedRecipe } from "@/lib/api";
import { type Call, callsTo, draft, mockFetch, renderWithProviders, reply } from "@/test/helpers";

vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
  useRouter: () => ({ push: vi.fn() }),
}));

function saved(fields: Partial<SavedRecipe> = {}): SavedRecipe {
  // A saved recipe has no provenance; the extra field is ignored.
  return {
    ...draft(),
    id: 1,
    title: "番茄炒蛋",
    source_url: "https://recipes.example/1",
    cookware: [{ name: "炒锅", cookware_id: 3 }],
    cookware_ids: [3],
    embedded: true,
    created_at: "2026-11-01T00:00:00Z",
    ...fields,
  };
}

let calls: Call[];

function setup(items = [saved()]) {
  calls = mockFetch({
    "GET /api/recipes": () => ({ items, total: items.length, page: 1 }),
    "DELETE /api/recipes/1": () => new Response(null, { status: 204 }),
    "PUT /api/recipes/1": (body) => ({ ...saved(), ...(body as object) }),
    "POST /api/recipes/allergy-check": () => ({ conflicts: [], unknown: [] }),
  });
  renderWithProviders(<RecipeLibrary />);
}

afterEach(() => vi.unstubAllGlobals());

describe("library", () => {
  it("searches and filters, and shows name and source link", async () => {
    setup();
    expect(await screen.findByText("番茄炒蛋")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Source" })).toHaveAttribute(
      "href",
      "https://recipes.example/1",
    );
    await userEvent.type(screen.getByRole("searchbox", { name: /Search title/ }), "番茄");
    await waitFor(() =>
      expect(calls.some((call) => call.url.includes(`q=${encodeURIComponent("番茄")}`))).toBe(true),
    );
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Dish type" }), "soup");
    await waitFor(() => expect(calls.some((call) => call.url.includes("dish_type=soup"))).toBe(true));
  });

  it("shows an empty state", async () => {
    setup([]);
    expect(await screen.findByText("No recipes found.")).toBeInTheDocument();
  });

  it("asks before deleting a recipe", async () => {
    setup();
    const item = within(await screen.findByTestId("recipe-1"));
    await userEvent.click(item.getByRole("button", { name: "Delete" }));
    expect(item.getByText("Delete 番茄炒蛋?")).toBeInTheDocument();
    expect(item.getByText(/Saved menus still show this dish's name/)).toBeInTheDocument();
    expect(callsTo(calls, "DELETE", "/api/recipes/1")).toHaveLength(0);
    await userEvent.click(item.getByRole("button", { name: "Yes, delete" }));
    await waitFor(() => expect(callsTo(calls, "DELETE", "/api/recipes/1")).toHaveLength(1));
  });

  it("can cancel a delete", async () => {
    setup();
    const item = within(await screen.findByTestId("recipe-1"));
    await userEvent.click(item.getByRole("button", { name: "Delete" }));
    await userEvent.click(item.getByRole("button", { name: "Cancel" }));
    expect(item.queryByText("Delete 番茄炒蛋?")).toBeNull();
    expect(callsTo(calls, "DELETE", "/api/recipes/1")).toHaveLength(0);
  });

  it("sets time_source to user when an edit changes the time", async () => {
    setup();
    const item = within(await screen.findByTestId("recipe-1"));
    await userEvent.click(item.getByRole("button", { name: "Edit" }));
    const time = screen.getByRole("spinbutton", { name: "Time (minutes)" });
    await userEvent.clear(time);
    await userEvent.type(time, "30");
    await userEvent.click(screen.getByRole("button", { name: "Save recipe" }));
    await waitFor(() => expect(callsTo(calls, "PUT", "/api/recipes/1")).toHaveLength(1));
    const body = callsTo(calls, "PUT", "/api/recipes/1")[0].body as Record<string, unknown>;
    expect(body).toMatchObject({ time_min: 30, time_source: "user", cookware_ids: [3] });
    expect(body.provenance).toBeNull();
  });

  it("keeps the edit form open when saving fails", async () => {
    calls = mockFetch({
      "GET /api/recipes": () => ({ items: [saved()], total: 1, page: 1 }),
      "PUT /api/recipes/1": () => reply(422, { code: "unknown_ingredient" }),
      "POST /api/recipes/allergy-check": () => ({ conflicts: [], unknown: [] }),
    });
    renderWithProviders(<RecipeLibrary />);
    await userEvent.click(within(await screen.findByTestId("recipe-1")).getByRole("button", { name: "Edit" }));
    await userEvent.click(screen.getByRole("button", { name: "Save recipe" }));
    expect(await screen.findByText(/An ingredient link is not valid/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save recipe" })).toBeInTheDocument();
  });
});
