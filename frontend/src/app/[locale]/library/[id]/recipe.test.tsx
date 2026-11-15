import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RecipePage } from "@/components/RecipePage";
import { callsTo, mockFetch, renderWithProviders, reply, savedRecipe } from "@/test/helpers";

const push = vi.fn();
let search = new URLSearchParams();

vi.mock("next/navigation", () => ({ useSearchParams: () => search }));
vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a>,
  useRouter: () => ({ push, replace: vi.fn() }),
}));

beforeEach(() => {
  search = new URLSearchParams();
  push.mockClear();
});
afterEach(() => vi.unstubAllGlobals());

describe("recipe page", () => {
  it("shows the full detail, the original text, a link back, edit and delete", async () => {
    mockFetch({ "GET /api/recipes/1": () => savedRecipe() });
    renderWithProviders(<RecipePage id={1} />);
    expect(await screen.findByRole("heading", { name: "番茄炒蛋" })).toBeInTheDocument();
    expect(screen.getByText(/Servings: 2/)).toBeInTheDocument();
    // The original text is shown in full, not collapsed.
    expect(screen.getByText("原文：番茄两个")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /recipes\.example/ })).toHaveAttribute(
      "href",
      "https://recipes.example/1",
    );
    expect(screen.getByRole("link", { name: "← Back to the library" })).toHaveAttribute("href", "/library");
    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument();
  });

  it("keeps the library search in the back link", async () => {
    search = new URLSearchParams({ from: "q=鸡蛋&type=vegetable dish&page=2" });
    mockFetch({ "GET /api/recipes/1": () => savedRecipe() });
    renderWithProviders(<RecipePage id={1} />);
    expect(await screen.findByRole("link", { name: "← Back to the library" })).toHaveAttribute(
      "href",
      "/library?q=鸡蛋&type=vegetable dish&page=2",
    );
  });

  it("deletes after asking and returns to the same library list", async () => {
    search = new URLSearchParams({ from: "q=鸡蛋&page=2" });
    const calls = mockFetch({
      "GET /api/recipes/1": () => savedRecipe(),
      "DELETE /api/recipes/1": () => new Response(null, { status: 204 }),
    });
    renderWithProviders(<RecipePage id={1} />);
    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
    expect(screen.getByText("Delete 番茄炒蛋?")).toBeInTheDocument();
    expect(callsTo(calls, "DELETE", "/api/recipes/1")).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Yes, delete" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/library?q=鸡蛋&page=2"));
    expect(callsTo(calls, "DELETE", "/api/recipes/1")).toHaveLength(1);
  });

  it("can cancel a delete", async () => {
    const calls = mockFetch({ "GET /api/recipes/1": () => savedRecipe() });
    renderWithProviders(<RecipePage id={1} />);
    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText("Delete 番茄炒蛋?")).toBeNull();
    expect(callsTo(calls, "DELETE", "/api/recipes/1")).toHaveLength(0);
    expect(push).not.toHaveBeenCalled();
  });

  it("edits in place and sets time_source to user when the time changes", async () => {
    const calls = mockFetch({
      "GET /api/recipes/1": () => savedRecipe(),
      "PUT /api/recipes/1": (body) => ({ ...savedRecipe(), ...(body as object) }),
      "POST /api/recipes/allergy-check": () => ({ conflicts: [], unknown: [] }),
    });
    renderWithProviders(<RecipePage id={1} />);
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    const time = screen.getByRole("spinbutton", { name: "Time (minutes)" });
    await userEvent.clear(time);
    await userEvent.type(time, "30");
    await userEvent.click(screen.getByRole("button", { name: "Save recipe" }));
    await waitFor(() => expect(callsTo(calls, "PUT", "/api/recipes/1")).toHaveLength(1));
    const body = callsTo(calls, "PUT", "/api/recipes/1")[0].body as Record<string, unknown>;
    expect(body).toMatchObject({ time_min: 30, time_source: "user", cookware_ids: [3] });
    expect(body.provenance).toBeNull();
  });

  it("keeps choice groups when editing", async () => {
    const grouped = savedRecipe();
    grouped.ingredients = grouped.ingredients.map((line, i) => ({ ...line, choice_group: i < 2 ? 1 : null }));
    const calls = mockFetch({
      "GET /api/recipes/1": () => grouped,
      "PUT /api/recipes/1": (body) => ({ ...grouped, ...(body as object) }),
      "POST /api/recipes/allergy-check": () => ({ conflicts: [], unknown: [] }),
    });
    renderWithProviders(<RecipePage id={1} />);
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    expect(screen.getByRole("group", { name: /Pick one/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save recipe" }));
    await waitFor(() => expect(callsTo(calls, "PUT", "/api/recipes/1")).toHaveLength(1));
    const body = callsTo(calls, "PUT", "/api/recipes/1")[0].body as {
      ingredients: { choice_group: number | null }[];
    };
    expect(body.ingredients.slice(0, 2).map((line) => line.choice_group)).toEqual([1, 1]);
  });

  it("says so when the recipe does not exist", async () => {
    mockFetch({ "GET /api/recipes/9": () => reply(404, { code: "not_found" }) });
    renderWithProviders(<RecipePage id={9} />);
    expect(await screen.findByText("This recipe no longer exists.")).toBeInTheDocument();
    expect(within(document.body).getByRole("link", { name: "← Back to the library" })).toBeInTheDocument();
  });
});
