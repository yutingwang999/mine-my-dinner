import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DictionaryManager } from "@/components/DictionaryManager";
import { type Call, callsTo, mockFetch, renderWithProviders, reply } from "@/test/helpers";

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

  it("edits a user entry's category and allergens", async () => {
    setup();
    const own = within(await screen.findByTestId("user-ingredient-9"));
    await userEvent.click(own.getByRole("button", { name: "Edit" }));
    await userEvent.selectOptions(own.getByRole("combobox", { name: "Category" }), "vegetable");
    await userEvent.click(own.getByRole("radio", { name: "No allergens" }));
    await userEvent.click(own.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(callsTo(calls, "PATCH", "/api/ingredients/9")).toHaveLength(1));
    expect(callsTo(calls, "PATCH", "/api/ingredients/9")[0].body).toEqual({
      name_zh: "外婆辣酱",
      name_en: null,
      aliases_zh: [],
      aliases_en: [],
      category: "vegetable",
      stock_mode: "level",
      allergens: [],
    });
  });

  it("keeps allergens unknown unless the user chooses otherwise", async () => {
    setup();
    const own = within(await screen.findByTestId("user-ingredient-9"));
    await userEvent.click(own.getByRole("button", { name: "Edit" }));
    expect(own.getByRole("radio", { name: "Unknown" })).toBeChecked();
    await userEvent.click(own.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(callsTo(calls, "PATCH", "/api/ingredients/9")[0].body).toMatchObject({ allergens: null }),
    );
  });

  it("sends the chosen allergens", async () => {
    setup();
    const own = within(await screen.findByTestId("user-ingredient-9"));
    await userEvent.click(own.getByRole("button", { name: "Edit" }));
    await userEvent.click(own.getByRole("radio", { name: "Contains" }));
    await userEvent.click(own.getByRole("checkbox", { name: "Sesame" }));
    await userEvent.click(own.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(callsTo(calls, "PATCH", "/api/ingredients/9")[0].body).toMatchObject({
        allergens: ["sesame"],
      }),
    );
  });

  it("explains why an edit was refused", async () => {
    calls = mockFetch({
      "GET /api/allergens/unresolved": () => ({ entries: [], unmatched: [] }),
      "GET /api/ingredients": () => [
        { id: 9, name_zh: "外婆辣酱", name_en: null, aliases_zh: [], aliases_en: [], source: "user", category: "seasoning", stock_mode: "level", allergens: null, parent_id: null },
      ],
      "GET /api/cookware": () => [],
      "PATCH /api/ingredients/9": () => reply(409, { code: "in_inventory" }),
    });
    renderWithProviders(<DictionaryManager />);
    const own = within(await screen.findByTestId("user-ingredient-9"));
    await userEvent.click(own.getByRole("button", { name: "Edit" }));
    await userEvent.selectOptions(own.getByRole("combobox", { name: "Stock mode" }), "count");
    await userEvent.click(own.getByRole("button", { name: "Save" }));
    expect(await own.findByText(/remove it from inventory first/i)).toBeInTheDocument();
  });

  it("warns which dishes a delete affects", async () => {
    calls = mockFetch({
      "GET /api/allergens/unresolved": () => ({ entries: [], unmatched: [] }),
      "GET /api/ingredients": (_body, _init, url) =>
        url.includes("source=user")
          ? [
              { id: 9, name_zh: "紫薯", name_en: null, aliases_zh: [], aliases_en: [], source: "user", category: "vegetable", stock_mode: "count", allergens: [], parent_id: null, dish_count: 3 },
              { id: 10, name_zh: "秘酱", name_en: null, aliases_zh: [], aliases_en: [], source: "user", category: "seasoning", stock_mode: "level", allergens: [], parent_id: null, dish_count: 0 },
            ]
          : [],
      "GET /api/cookware": (_body, _init, url) =>
        url.includes("source=user")
          ? [{ id: 60, name_zh: "面包机", name_en: null, aliases_zh: [], aliases_en: [], source: "user", dish_count: 2 }]
          : [],
    });
    renderWithProviders(<DictionaryManager />);
    const used = within(await screen.findByTestId("user-ingredient-9"));
    expect(used.getByText(/3 dishes/)).toBeInTheDocument();
    await userEvent.click(used.getByRole("button", { name: "Delete" }));
    expect(used.getByText(/3 dishes use it\. After deleting, those lines become unlinked/)).toBeInTheDocument();

    const unused = within(screen.getByTestId("user-ingredient-10"));
    await userEvent.click(unused.getByRole("button", { name: "Delete" }));
    expect(unused.queryByText(/use it/)).toBeNull();

    const cookware = within(screen.getByTestId("user-cookware-60"));
    await userEvent.click(cookware.getByRole("button", { name: "Delete" }));
    expect(cookware.getByText(/2 dishes need it/)).toBeInTheDocument();
  });

  it("lists the user's own entries and marks unknown allergens", async () => {
    setup();
    const own = within(await screen.findByTestId("user-ingredient-9"));
    expect(own.getByText(/allergens unknown/)).toBeInTheDocument();
    expect(own.getByRole("button", { name: "Delete" })).toBeInTheDocument();
  });
});
