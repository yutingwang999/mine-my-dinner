import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DictionaryManager } from "@/components/DictionaryManager";
import { type Call, callsTo, mockFetch, renderWithProviders, reply } from "@/test/helpers";

const replace = vi.fn();
let search = new URLSearchParams();

vi.mock("next/navigation", () => ({ useSearchParams: () => search }));
vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
  useRouter: () => ({ push: vi.fn(), replace }),
}));

let calls: Call[];

type Routes = Parameters<typeof mockFetch>[0];

const BEEF = { id: 20, name_zh: "牛肉末", name_en: "minced beef", aliases_zh: [], aliases_en: [], source: "seed", category: "meat", stock_mode: "level", allergens: [], parent_id: null };

function setup(routes: Routes = {}) {
  calls = mockFetch({
    "GET /api/allergens/unresolved": () => ({
      entries: [{ id: 9, name: "外婆辣酱", dish_count: 1 }],
      unmatched: [{ name: "秘制酱", dish_count: 2 }],
    }),
    "GET /api/ingredients": (_body, _init, url) =>
      url.includes("q=")
        ? [BEEF]
        : [
            { id: 9, name_zh: "外婆辣酱", name_en: null, aliases_zh: [], aliases_en: [], source: "user", category: "seasoning", stock_mode: "level", allergens: null, parent_id: null },
          ],
    "GET /api/cookware": () => [],
    "POST /api/allergen-suggestions": () => ({ suggested: ["soy"], category: null, stock_mode: null }),
    "POST /api/allergens/resolve": () => ({}),
    "POST /api/allergens/link": () => ({ ...BEEF, linked_lines: 2, remembered: true }),
    ...routes,
  });
  renderWithProviders(<DictionaryManager />);
}

beforeEach(() => {
  search = new URLSearchParams();
  replace.mockClear();
  window.localStorage.clear();
});
afterEach(() => vi.unstubAllGlobals());

async function openTab(name: RegExp) {
  await userEvent.click(await screen.findByRole("tab", { name }));
}

function userEntries(count: number) {
  return Array.from({ length: count }, (_, index) => ({
    id: 100 + index,
    name_zh: `自制${String(index + 1).padStart(3, "0")}`,
    name_en: null,
    aliases_zh: [],
    aliases_en: [],
    source: "user",
    category: "seasoning",
    stock_mode: "level",
    allergens: [],
    parent_id: null,
  }));
}

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
      category: "other",
      stock_mode: "level",
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
    await openTab(/My ingredients/);
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
    await openTab(/My ingredients/);
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
    await openTab(/My ingredients/);
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
    await openTab(/My ingredients/);
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
    await openTab(/My ingredients/);
    const used = within(await screen.findByTestId("user-ingredient-9"));
    expect(used.getByText(/3 dishes/)).toBeInTheDocument();
    await userEvent.click(used.getByRole("button", { name: "Delete" }));
    expect(used.getByText(/3 dishes use it\. After deleting, those lines become unlinked/)).toBeInTheDocument();

    const unused = within(screen.getByTestId("user-ingredient-10"));
    await userEvent.click(unused.getByRole("button", { name: "Delete" }));
    expect(unused.queryByText(/use it/)).toBeNull();

    await openTab(/My cookware/);
    const cookware = within(await screen.findByTestId("user-cookware-60"));
    await userEvent.click(cookware.getByRole("button", { name: "Delete" }));
    expect(cookware.getByText(/2 dishes need it/)).toBeInTheDocument();
  });

  it("lists the user's own entries and marks unknown allergens", async () => {
    setup();
    await openTab(/My ingredients/);
    const own = within(await screen.findByTestId("user-ingredient-9"));
    expect(own.getByText(/allergens unknown/)).toBeInTheDocument();
    expect(own.getByRole("button", { name: "Delete" })).toBeInTheDocument();
  });

  async function openConfirm() {
    const row = within(await screen.findByTestId("unresolved-name-秘制酱"));
    await userEvent.click(row.getByRole("button", { name: "Confirm allergens" }));
    await waitFor(() => expect(callsTo(calls, "POST", "/api/allergen-suggestions")).toHaveLength(1));
    return row;
  }

  async function chooseBeef(row: ReturnType<typeof within>) {
    await userEvent.click(row.getByRole("button", { name: "Link to an existing entry" }));
    await userEvent.type(row.getByRole("searchbox", { name: "Search the dictionary" }), "牛肉");
    await userEvent.click(await row.findByRole("button", { name: "Choose minced beef" }));
  }

  it("links an unknown name to an existing entry with remember checked by default", async () => {
    setup();
    const row = within(await screen.findByTestId("unresolved-name-秘制酱"));
    await userEvent.click(row.getByRole("button", { name: "Link to an existing entry" }));
    expect(row.getByRole("checkbox", { name: "Remember this link" })).toBeChecked();
    expect(row.getByRole("button", { name: "Confirm" })).toBeDisabled();
    await userEvent.type(row.getByRole("searchbox", { name: "Search the dictionary" }), "牛肉");
    await userEvent.click(await row.findByRole("button", { name: "Choose minced beef" }));
    await userEvent.click(row.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(callsTo(calls, "POST", "/api/allergens/link")).toHaveLength(1));
    expect(callsTo(calls, "POST", "/api/allergens/link")[0].body).toEqual({
      name: "秘制酱",
      ingredient_id: 20,
      remember: true,
    });
  });

  it("removes the linked name from the list", async () => {
    let linked = false;
    setup({
      "GET /api/allergens/unresolved": () => ({
        entries: [],
        unmatched: linked ? [] : [{ name: "秘制酱", dish_count: 2 }],
      }),
      "POST /api/allergens/link": () => {
        linked = true;
        return { ...BEEF, linked_lines: 2, remembered: true };
      },
    });
    const row = within(await screen.findByTestId("unresolved-name-秘制酱"));
    await chooseBeef(row);
    await userEvent.click(row.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(screen.queryByTestId("unresolved-name-秘制酱")).toBeNull());
  });

  it("can link without remembering", async () => {
    setup();
    const row = within(await screen.findByTestId("unresolved-name-秘制酱"));
    await chooseBeef(row);
    await userEvent.click(row.getByRole("checkbox", { name: "Remember this link" }));
    await userEvent.click(row.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(callsTo(calls, "POST", "/api/allergens/link")).toHaveLength(1));
    expect(callsTo(calls, "POST", "/api/allergens/link")[0].body).toMatchObject({ remember: false });
  });

  it("offers linking only for an unmatched name, and no category or stock mode for an entry", async () => {
    setup();
    const entry = within(await screen.findByTestId("unresolved-entry-9"));
    expect(entry.queryByRole("button", { name: "Link to an existing entry" })).toBeNull();
    await userEvent.click(entry.getByRole("button", { name: "Confirm allergens" }));
    await entry.findByText(/A model suggested these/);
    // Only a new entry takes a category and stock mode.
    expect(entry.queryByRole("combobox", { name: "Category" })).toBeNull();
    expect(entry.queryByRole("combobox", { name: "Stock mode" })).toBeNull();
  });

  it("prefills category and stock mode from the suggestion and lets the user change them", async () => {
    setup({
      "POST /api/allergen-suggestions": () => ({ suggested: ["soy"], category: "meat", stock_mode: "count" }),
    });
    const row = await openConfirm();
    await waitFor(() => expect(row.getByRole("combobox", { name: "Category" })).toHaveValue("meat"));
    expect(row.getByRole("combobox", { name: "Stock mode" })).toHaveValue("count");
    expect(row.getByText("Suggested, please confirm")).toBeInTheDocument();
    await userEvent.selectOptions(row.getByRole("combobox", { name: "Category" }), "nut");
    expect(row.getByRole("combobox", { name: "Category" })).toHaveValue("nut");
  });

  it("keeps the old prefill when there is no suggestion", async () => {
    setup({
      "POST /api/allergen-suggestions": () => ({ suggested: null, category: null, stock_mode: null }),
    });
    const row = await openConfirm();
    await row.findByText(/Suggestions are unavailable/);
    expect(row.getByRole("combobox", { name: "Category" })).toHaveValue("other");
    expect(row.getByRole("combobox", { name: "Stock mode" })).toHaveValue("level");
    expect(row.queryByText("Suggested, please confirm")).toBeNull();
  });

  it("sends the confirmed category and stock mode to resolve", async () => {
    setup({
      "POST /api/allergen-suggestions": () => ({ suggested: ["soy"], category: "meat", stock_mode: "count" }),
    });
    const row = await openConfirm();
    await waitFor(() => expect(row.getByRole("combobox", { name: "Category" })).toHaveValue("meat"));
    await userEvent.selectOptions(row.getByRole("combobox", { name: "Stock mode" }), "availability");
    await userEvent.click(row.getByRole("button", { name: "Confirm selected allergens" }));
    await userEvent.click(row.getByRole("button", { name: "Save allergens" }));
    await waitFor(() => expect(callsTo(calls, "POST", "/api/allergens/resolve")).toHaveLength(1));
    expect(callsTo(calls, "POST", "/api/allergens/resolve")[0].body).toEqual({
      name: "秘制酱",
      allergens: ["soy"],
      category: "meat",
      stock_mode: "availability",
    });
  });

  it("closes the confirm panel with cancel and discards the picks", async () => {
    setup();
    const row = await openConfirm();
    await userEvent.click(await row.findByRole("button", { name: "Confirm selected allergens" }));
    expect(row.getByRole("button", { name: "Save allergens" })).toBeEnabled();
    expect(row.getByRole("button", { name: "Cancel" }).closest(".dish-panel")).not.toBeNull();
    await userEvent.click(row.getByRole("button", { name: "Cancel" }));
    expect(row.queryByRole("button", { name: "Save allergens" })).toBeNull();
    expect(callsTo(calls, "POST", "/api/allergens/resolve")).toHaveLength(0);
    await userEvent.click(row.getByRole("button", { name: "Confirm allergens" }));
    expect(row.getByRole("button", { name: "Save allergens" })).toBeDisabled();
  });

  it("opens only one panel per row at a time", async () => {
    setup();
    const row = within(await screen.findByTestId("unresolved-name-秘制酱"));
    await userEvent.click(row.getByRole("button", { name: "Link to an existing entry" }));
    expect(row.getByRole("searchbox", { name: "Search the dictionary" })).toBeInTheDocument();
    expect(row.queryByRole("button", { name: "Confirm allergens" })).toBeNull();
    await userEvent.click(row.getByRole("button", { name: "Cancel" }));
    expect(row.queryByRole("searchbox", { name: "Search the dictionary" })).toBeNull();
    await userEvent.click(row.getByRole("button", { name: "Confirm allergens" }));
    expect(row.queryByRole("searchbox", { name: "Search the dictionary" })).toBeNull();
    expect(row.queryByRole("button", { name: "Link to an existing entry" })).toBeNull();
  });

  it("shows one section at a time under tabs with counts", async () => {
    setup();
    const tabs = within(await screen.findByRole("tablist", { name: "Dictionary sections" }));
    expect(tabs.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Allergens unknown (2)",
      "My ingredients (1)",
      "My cookware (0)",
      "All ingredients",
      "All cookware",
    ]);
    expect(tabs.getByRole("tab", { name: /Allergens unknown/ })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByTestId("unresolved-entry-9")).toBeInTheDocument();
    expect(screen.queryByTestId("user-ingredient-9")).toBeNull();

    await userEvent.click(tabs.getByRole("tab", { name: /My ingredients/ }));
    expect(tabs.getByRole("tab", { name: /My ingredients/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("user-ingredient-9")).toBeInTheDocument();
    expect(screen.queryByTestId("unresolved-entry-9")).toBeNull();
    expect(replace).toHaveBeenLastCalledWith("/dictionary?tab=mine");

    await userEvent.click(tabs.getByRole("tab", { name: "All cookware" }));
    expect(replace).toHaveBeenLastCalledWith("/dictionary?tab=all-cookware");
    expect(await screen.findByRole("heading", { name: "All cookware" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "All ingredients" })).toBeNull();
    await userEvent.click(tabs.getByRole("tab", { name: "All ingredients" }));
    expect(replace).toHaveBeenLastCalledWith("/dictionary?tab=all");
    expect(await screen.findByRole("heading", { name: "All ingredients" })).toBeInTheDocument();
  });

  it("opens the tab named in the address", async () => {
    search = new URLSearchParams("tab=my-cookware");
    setup();
    expect(await screen.findByRole("tab", { name: /My cookware/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("heading", { name: "My cookware entries" })).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });

  it("defaults to the unknown list when it has rows", async () => {
    setup();
    expect(await screen.findByRole("tab", { name: /Allergens unknown/ })).toHaveAttribute("aria-selected", "true");
  });

  it("defaults to all ingredients when nothing is unknown", async () => {
    setup({ "GET /api/allergens/unresolved": () => ({ entries: [], unmatched: [] }) });
    const tab = await screen.findByRole("tab", { name: "All ingredients" });
    await waitFor(() => expect(tab).toHaveAttribute("aria-selected", "true"));
  });

  it("ignores an unknown tab in the address", async () => {
    search = new URLSearchParams("tab=nope");
    setup();
    expect(await screen.findByRole("tab", { name: /Allergens unknown/ })).toHaveAttribute("aria-selected", "true");
  });

  it("paginates my ingredients and returns to page 1 when the page size changes", async () => {
    setup({
      "GET /api/ingredients": (_body, _init, url) => (url.includes("source=user") ? userEntries(120) : []),
    });
    await openTab(/My ingredients/);
    await screen.findByTestId("user-ingredient-100");
    expect(screen.getAllByTestId(/^user-ingredient-/)).toHaveLength(20);
    expect(screen.getAllByText("Page 1 of 6")).toHaveLength(2);
    await userEvent.click(screen.getAllByRole("button", { name: "Next" })[0]);
    expect(screen.getAllByText("Page 2 of 6")).toHaveLength(2);
    expect(screen.queryByTestId("user-ingredient-100")).toBeNull();

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Rows per page" }), "50");
    expect(screen.getAllByTestId(/^user-ingredient-/)).toHaveLength(50);
    expect(screen.getByTestId("user-ingredient-100")).toBeInTheDocument();
    expect(screen.getAllByText("Page 1 of 3")).toHaveLength(2);
    expect(window.localStorage.getItem("pageSize:my-ingredients")).toBe("50");
  });

  it("uses the remembered page size when the list is shown again", async () => {
    window.localStorage.setItem("pageSize:my-ingredients", "50");
    setup({
      "GET /api/ingredients": (_body, _init, url) => (url.includes("source=user") ? userEntries(60) : []),
    });
    await openTab(/My ingredients/);
    await screen.findByTestId("user-ingredient-100");
    await waitFor(() => expect(screen.getAllByTestId(/^user-ingredient-/)).toHaveLength(50));
    expect(screen.getByRole("combobox", { name: "Rows per page" })).toHaveValue("50");
  });

  it("paginates the unknown list and my cookware", async () => {
    const names = Array.from({ length: 25 }, (_, index) => ({
      name: `酱${String(index + 1).padStart(2, "0")}`,
      dish_count: 1,
    }));
    const pots = Array.from({ length: 12 }, (_, index) => ({
      id: 200 + index,
      name_zh: `锅${index + 1}`,
      name_en: null,
      aliases_zh: [],
      aliases_en: [],
      source: "user",
      dish_count: 0,
    }));
    setup({
      "GET /api/allergens/unresolved": () => ({ entries: [], unmatched: names }),
      "GET /api/cookware": (_body, _init, url) => (url.includes("source=user") ? pots : []),
    });
    await screen.findByTestId("unresolved-name-酱01");
    expect(screen.getAllByTestId(/^unresolved-name-/)).toHaveLength(20);
    await userEvent.click(screen.getAllByRole("button", { name: "Next" })[0]);
    expect(screen.getAllByTestId(/^unresolved-name-/)).toHaveLength(5);

    await openTab(/My cookware/);
    await screen.findByTestId("user-cookware-200");
    expect(screen.getAllByTestId(/^user-cookware-/)).toHaveLength(12);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Rows per page" }), "10");
    expect(screen.getAllByTestId(/^user-cookware-/)).toHaveLength(10);
  });

  it("filters the all-cookware tab", async () => {
    setup({
      "GET /api/cookware": () => [
        { id: 3, name_zh: "炒锅", name_en: "wok", aliases_zh: [], aliases_en: [], source: "seed" },
        { id: 4, name_zh: "蒸锅", name_en: "steamer", aliases_zh: [], aliases_en: [], source: "seed" },
      ],
    });
    await openTab(/^All cookware$/);
    await screen.findByTestId("all-cookware-3");
    await userEvent.type(screen.getByRole("searchbox", { name: "Search names and aliases" }), "蒸");
    expect(screen.queryByTestId("all-cookware-3")).toBeNull();
    expect(screen.getByTestId("all-cookware-4")).toBeInTheDocument();
  });
});
