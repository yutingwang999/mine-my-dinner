import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DictionaryBrowser } from "@/components/DictionaryBrowser";
import { type Call, callsTo, mockFetch, renderWithProviders } from "@/test/helpers";

function ingredient(id: number, fields: Record<string, unknown>) {
  return {
    id,
    name_zh: null,
    name_en: null,
    aliases_zh: [],
    aliases_en: [],
    user_aliases_zh: [],
    user_aliases_en: [],
    source: "seed",
    category: "vegetable",
    stock_mode: "count",
    allergens: [],
    parent_id: null,
    ...fields,
  };
}

const INGREDIENTS = [
  ingredient(1, { name_zh: "土豆", name_en: "potato", aliases_zh: ["马铃薯", "洋芋"] }),
  ingredient(2, {
    name_zh: "酱油",
    name_en: "soy sauce",
    category: "seasoning",
    stock_mode: "availability",
    allergens: ["soy", "wheat"],
  }),
  ingredient(3, { name_zh: "生抽", name_en: "light soy sauce", category: "seasoning", parent_id: 2, allergens: ["soy", "wheat"] }),
  ingredient(4, { name_zh: "外婆辣酱", category: "seasoning", source: "user", allergens: null }),
];
const COOKWARE = [
  { id: 3, name_zh: "炒锅", name_en: "wok", aliases_zh: ["铁锅"], aliases_en: [], source: "seed" },
];

let calls: Call[];

function setup() {
  calls = mockFetch({
    "GET /api/ingredients": () => INGREDIENTS,
    "GET /api/cookware": () => COOKWARE,
  });
  renderWithProviders(<DictionaryBrowser />);
}

beforeEach(() => window.localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("dictionary browser", () => {
  it("loads every entry at once", async () => {
    setup();
    await screen.findByTestId("all-ingredient-1");
    expect(calls.some((call) => call.url.includes("limit=500"))).toBe(true);
  });

  it("shows names, aliases, category, stock mode, allergens, parent, and source", async () => {
    setup();
    const potato = within(await screen.findByTestId("all-ingredient-1"));
    expect(potato.getByText("土豆 / potato")).toBeInTheDocument();
    expect(potato.getByText(/马铃薯, 洋芋/)).toBeInTheDocument();
    expect(potato.getByText("Vegetable")).toBeInTheDocument();
    expect(potato.getByText("Count")).toBeInTheDocument();
    expect(potato.getByText("Built-in")).toBeInTheDocument();

    const lightSoy = within(screen.getByTestId("all-ingredient-3"));
    expect(lightSoy.getByText("Soy, Wheat")).toBeInTheDocument();
    expect(lightSoy.getByText("Parent: soy sauce")).toBeInTheDocument();

    const mine = within(screen.getByTestId("all-ingredient-4"));
    expect(mine.getByText("Mine")).toBeInTheDocument();
    expect(mine.getByText("allergens unknown")).toBeInTheDocument();
  });

  it("filters by text in names or aliases, and by category", async () => {
    setup();
    await screen.findByTestId("all-ingredient-1");
    await userEvent.type(screen.getByRole("searchbox", { name: "Search names and aliases" }), "洋芋");
    expect(screen.getByTestId("all-ingredient-1")).toBeInTheDocument();
    expect(screen.queryByTestId("all-ingredient-2")).toBeNull();

    await userEvent.clear(screen.getByRole("searchbox", { name: "Search names and aliases" }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Category" }), "seasoning");
    await waitFor(() => expect(screen.queryByTestId("all-ingredient-1")).toBeNull());
    expect(screen.getByTestId("all-ingredient-2")).toBeInTheDocument();
    expect(screen.getByText("3 of 4 entries")).toBeInTheDocument();
  });

  it("shows 20 entries per page and goes back to the first page when filtering", async () => {
    const many = Array.from({ length: 120 }, (_, index) =>
      ingredient(index + 1, { name_zh: `食材${String(index + 1).padStart(3, "0")}` }),
    );
    mockFetch({ "GET /api/ingredients": () => many, "GET /api/cookware": () => [] });
    renderWithProviders(<DictionaryBrowser />);
    await screen.findByTestId("all-ingredient-1");
    expect(screen.getAllByTestId(/^all-ingredient-/)).toHaveLength(20);
    expect(screen.getAllByText("Page 1 of 6")).toHaveLength(2);

    await userEvent.click(screen.getAllByRole("button", { name: "Next" })[0]);
    expect(screen.getByTestId("all-ingredient-21")).toBeInTheDocument();
    expect(screen.queryByTestId("all-ingredient-1")).toBeNull();
    for (let step = 0; step < 4; step += 1) {
      await userEvent.click(screen.getAllByRole("button", { name: "Next" })[0]);
    }
    expect(screen.getAllByTestId(/^all-ingredient-/)).toHaveLength(20);
    expect(screen.getAllByRole("button", { name: "Next" })[0]).toBeDisabled();

    await userEvent.type(screen.getByRole("searchbox", { name: "Search names and aliases" }), "食材00");
    expect(screen.getByTestId("all-ingredient-1")).toBeInTheDocument();
    expect(screen.getByText("Page 1 of 1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });

  it("pages cookware separately once there are more than 20", async () => {
    const pots = Array.from({ length: 60 }, (_, index) => ({
      id: index + 1,
      name_zh: `锅${String(index + 1).padStart(2, "0")}`,
      name_en: null,
      aliases_zh: [],
      aliases_en: [],
      source: "user",
    }));
    mockFetch({ "GET /api/ingredients": () => INGREDIENTS, "GET /api/cookware": () => pots });
    renderWithProviders(<DictionaryBrowser />);
    await screen.findByTestId("all-cookware-1");
    expect(screen.getAllByTestId(/^all-cookware-/)).toHaveLength(20);
    const section = within(screen.getByRole("heading", { name: "All cookware" }).closest("section")!);
    await userEvent.click(section.getAllByRole("button", { name: "Next" })[0]);
    expect(section.getAllByText("Page 2 of 3")).toHaveLength(2);
    expect(screen.getAllByTestId(/^all-cookware-/)).toHaveLength(20);
    expect(screen.getByTestId("all-ingredient-1")).toBeInTheDocument();

    await userEvent.type(screen.getByRole("searchbox", { name: "Search names and aliases" }), "锅0");
    expect(screen.getByTestId("all-cookware-1")).toBeInTheDocument();
  });

  it("lets the user pick 50 per page, goes back to page 1, and remembers it", async () => {
    const many = Array.from({ length: 120 }, (_, index) =>
      ingredient(index + 1, { name_zh: `食材${String(index + 1).padStart(3, "0")}` }),
    );
    mockFetch({ "GET /api/ingredients": () => many, "GET /api/cookware": () => [] });
    const first = renderWithProviders(<DictionaryBrowser />);
    await screen.findByTestId("all-ingredient-1");
    await userEvent.click(screen.getAllByRole("button", { name: "Next" })[0]);
    expect(screen.queryByTestId("all-ingredient-1")).toBeNull();

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Rows per page" }), "50");
    expect(screen.getAllByTestId(/^all-ingredient-/)).toHaveLength(50);
    expect(screen.getByTestId("all-ingredient-1")).toBeInTheDocument();
    expect(screen.getAllByText("Page 1 of 3")).toHaveLength(2);
    expect(window.localStorage.getItem("pageSize:all-ingredients")).toBe("50");

    first.unmount();
    renderWithProviders(<DictionaryBrowser />);
    await screen.findByTestId("all-ingredient-1");
    await waitFor(() => expect(screen.getAllByTestId(/^all-ingredient-/)).toHaveLength(50));
  });

  it("falls back to 20 when storage is blocked", async () => {
    const blocked = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const many = Array.from({ length: 30 }, (_, index) => ingredient(index + 1, { name_zh: `食材${index + 1}` }));
    mockFetch({ "GET /api/ingredients": () => many, "GET /api/cookware": () => [] });
    renderWithProviders(<DictionaryBrowser />);
    await screen.findByTestId("all-ingredient-1");
    expect(screen.getAllByTestId(/^all-ingredient-/)).toHaveLength(20);
    blocked.mockRestore();
  });

  it("lists every cookware entry", async () => {
    setup();
    const wok = within(await screen.findByTestId("all-cookware-3"));
    expect(wok.getByText("炒锅 / wok")).toBeInTheDocument();
    expect(wok.getByText(/铁锅/)).toBeInTheDocument();
  });

  it("lets the user add a personal alias to a built-in entry", async () => {
    calls = mockFetch({
      "GET /api/ingredients": () => INGREDIENTS,
      "GET /api/cookware": () => COOKWARE,
      "PUT /api/ingredients/1/personal-aliases": () => ({
        ...INGREDIENTS[0],
        user_aliases_zh: ["马铃薯丁"],
        linked_lines: 1,
      }),
    });
    renderWithProviders(<DictionaryBrowser />);
    const potato = within(await screen.findByTestId("all-ingredient-1"));
    await userEvent.click(potato.getByRole("button", { name: "My aliases" }));
    await userEvent.type(screen.getByLabelText("Chinese aliases"), "马铃薯丁");
    await userEvent.click(screen.getByRole("button", { name: "Save aliases" }));
    expect(callsTo(calls, "PUT", "/api/ingredients/1/personal-aliases")[0].body).toEqual({
      aliases_zh: ["马铃薯丁"],
      aliases_en: [],
    });
    expect(await screen.findByText(/马铃薯丁/)).toBeInTheDocument();
  });

  it("offers personal aliases only on built-in entries and explains a conflict", async () => {
    mockFetch({
      "GET /api/ingredients": () => INGREDIENTS,
      "GET /api/cookware": () => COOKWARE,
      "PUT /api/ingredients/1/personal-aliases": () =>
        new Response(JSON.stringify({ code: "term_conflict", term: "番茄" }), { status: 409 }),
    });
    renderWithProviders(<DictionaryBrowser />);
    const mine = within(await screen.findByTestId("all-ingredient-4"));
    expect(mine.queryByRole("button", { name: "My aliases" })).toBeNull();

    const potato = within(screen.getByTestId("all-ingredient-1"));
    await userEvent.click(potato.getByRole("button", { name: "My aliases" }));
    await userEvent.type(screen.getByLabelText("Chinese aliases"), "番茄");
    await userEvent.click(screen.getByRole("button", { name: "Save aliases" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already belongs to another entry");
  });

  it("shows the personal alias toggle in the title row and the editor in a framed panel", async () => {
    setup();
    const potato = within(await screen.findByTestId("all-ingredient-1"));
    const toggle = potato.getByRole("button", { name: "My aliases" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveTextContent("My aliases ▾");
    expect(toggle.closest(".dish-head")).not.toBeNull();
    expect(potato.queryByLabelText("Chinese aliases")).toBeNull();

    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(toggle).toHaveTextContent("My aliases ▴");
    const panel = potato.getByLabelText("Chinese aliases").closest(".dish-panel") as HTMLElement;
    expect(panel).not.toBeNull();
    expect(within(panel).getByText(/Your own wordings/)).toBeInTheDocument();
    expect(within(panel).getByLabelText("English aliases")).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Cancel" })).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Save aliases" })).toBeInTheDocument();

    // The toggle closes the panel too, and the cancel button does the same.
    await userEvent.click(toggle);
    expect(potato.queryByLabelText("Chinese aliases")).toBeNull();
    await userEvent.click(toggle);
    await userEvent.click(potato.getByRole("button", { name: "Cancel" }));
    expect(potato.queryByLabelText("Chinese aliases")).toBeNull();
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });
});

describe("pager", () => {
  it("shows the total, disabled buttons and the size select even on one page, with no bottom bar", async () => {
    setup();
    await screen.findByTestId("all-ingredient-1");
    const section = within(screen.getByRole("heading", { name: "All ingredients" }).closest("section")!);
    expect(section.getByText("4 items")).toBeInTheDocument();
    expect(section.getByText("Page 1 of 1")).toBeInTheDocument();
    expect(section.getByRole("button", { name: "Previous" })).toBeDisabled();
    expect(section.getByRole("button", { name: "Next" })).toBeDisabled();
    expect(section.getByRole("combobox", { name: "Rows per page" })).toHaveValue("20");
    expect(section.getAllByRole("navigation")).toHaveLength(1);
  });

  it("shows no pager for an empty list", async () => {
    mockFetch({ "GET /api/ingredients": () => [], "GET /api/cookware": () => [] });
    renderWithProviders(<DictionaryBrowser />);
    await waitFor(() => expect(screen.getByText("0 of 0 entries")).toBeInTheDocument());
    expect(screen.queryByRole("navigation")).toBeNull();
  });

  it("adds the bottom bar with only the page buttons when there are several pages", async () => {
    const many = Array.from({ length: 30 }, (_, index) => ingredient(index + 1, { name_zh: `食材${index + 1}` }));
    mockFetch({ "GET /api/ingredients": () => many, "GET /api/cookware": () => [] });
    renderWithProviders(<DictionaryBrowser />);
    await screen.findByTestId("all-ingredient-1");
    const navs = screen.getAllByRole("navigation");
    expect(navs).toHaveLength(2);
    expect(within(navs[1]).queryByRole("combobox")).toBeNull();
    expect(within(navs[1]).queryByText("30 items")).toBeNull();
    expect(within(navs[1]).getByText("Page 1 of 2")).toBeInTheDocument();
  });
});
