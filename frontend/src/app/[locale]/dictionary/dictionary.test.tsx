import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DictionaryManager } from "@/components/DictionaryManager";
import { type Call, callsTo, mockFetch, renderWithProviders } from "@/test/helpers";

vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
  useRouter: () => ({ push: vi.fn() }),
}));

let calls: Call[];

function setup() {
  calls = mockFetch({
    "GET /api/allergens/unresolved": () => ({
      entries: [{ id: 9, name: "外婆辣酱", dish_count: 1 }],
      unmatched: [{ name: "秘制酱", dish_count: 2 }],
    }),
    "GET /api/ingredients": () => [
      { id: 9, name_zh: "外婆辣酱", name_en: null, aliases_zh: [], aliases_en: [], source: "user", category: "seasoning", stock_mode: "level", allergens: null, parent_id: null },
    ],
    "GET /api/cookware": () => [],
    "POST /api/allergen-suggestions": () => ({ suggested: ["soy"] }),
    "POST /api/allergens/resolve": () => ({}),
  });
  renderWithProviders(<DictionaryManager />);
}

afterEach(() => vi.unstubAllGlobals());

describe("dictionary", () => {
  it("lists unresolved items with dish counts and suggested allergens", async () => {
    setup();
    const unmatched = within(await screen.findByTestId("unresolved-name-秘制酱"));
    expect(unmatched.getByText("秘制酱 (not in the dictionary)")).toBeInTheDocument();
    expect(unmatched.getByText("2 dishes")).toBeInTheDocument();
    expect(within(screen.getByTestId("unresolved-entry-9")).getByText("1 dish")).toBeInTheDocument();
    await userEvent.click(unmatched.getByRole("button", { name: "Confirm allergens" }));
    expect(await unmatched.findByText(/A model suggested these/)).toBeInTheDocument();
    expect(unmatched.getByRole("checkbox", { name: "Soy" })).toBeChecked();
  });

  it("does not send a value until containsNone or confirm is clicked", async () => {
    setup();
    const unmatched = within(await screen.findByTestId("unresolved-name-秘制酱"));
    await userEvent.click(unmatched.getByRole("button", { name: "Confirm allergens" }));
    await unmatched.findByText(/A model suggested these/);
    expect(unmatched.getByRole("button", { name: "Save allergens" })).toBeDisabled();
    await userEvent.click(unmatched.getByRole("button", { name: "Confirm it contains none" }));
    await userEvent.click(unmatched.getByRole("button", { name: "Save allergens" }));
    await waitFor(() => expect(callsTo(calls, "POST", "/api/allergens/resolve")).toHaveLength(1));
    expect(callsTo(calls, "POST", "/api/allergens/resolve")[0].body).toEqual({
      name: "秘制酱",
      allergens: [],
    });
  });

  it("resolves a dictionary entry by id", async () => {
    setup();
    const entry = within(await screen.findByTestId("unresolved-entry-9"));
    await userEvent.click(entry.getByRole("button", { name: "Confirm allergens" }));
    await userEvent.click(await entry.findByRole("button", { name: "Confirm selected allergens" }));
    await userEvent.click(entry.getByRole("button", { name: "Save allergens" }));
    await waitFor(() =>
      expect(callsTo(calls, "POST", "/api/allergens/resolve")[0].body).toEqual({
        ingredient_id: 9,
        allergens: ["soy"],
      }),
    );
  });

  it("lists the user's own entries and marks unknown allergens", async () => {
    setup();
    const own = within(await screen.findByTestId("user-ingredient-9"));
    expect(own.getByText(/allergens unknown/)).toBeInTheDocument();
    expect(own.getByRole("button", { name: "Delete" })).toBeInTheDocument();
  });
});
