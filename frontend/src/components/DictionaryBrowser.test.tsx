import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

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

  it("shows 50 entries per page and goes back to the first page when filtering", async () => {
    const many = Array.from({ length: 120 }, (_, index) =>
      ingredient(index + 1, { name_zh: `食材${String(index + 1).padStart(3, "0")}` }),
    );
    mockFetch({ "GET /api/ingredients": () => many, "GET /api/cookware": () => [] });
    renderWithProviders(<DictionaryBrowser />);
    await screen.findByTestId("all-ingredient-1");
    expect(screen.getAllByTestId(/^all-ingredient-/)).toHaveLength(50);
    expect(screen.getAllByText("Page 1 of 3")).toHaveLength(2);

    await userEvent.click(screen.getAllByRole("button", { name: "Next" })[0]);
    expect(screen.getByTestId("all-ingredient-51")).toBeInTheDocument();
    expect(screen.queryByTestId("all-ingredient-1")).toBeNull();
    await userEvent.click(screen.getAllByRole("button", { name: "Next" })[0]);
    expect(screen.getAllByTestId(/^all-ingredient-/)).toHaveLength(20);
    expect(screen.getAllByRole("button", { name: "Next" })[0]).toBeDisabled();

    await userEvent.type(screen.getByRole("searchbox", { name: "Search names and aliases" }), "食材00");
    expect(screen.getByTestId("all-ingredient-1")).toBeInTheDocument();
    expect(screen.queryByText(/Page \d of/)).toBeNull();
  });

  it("pages cookware separately once there are more than 50", async () => {
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
    expect(screen.getAllByTestId(/^all-cookware-/)).toHaveLength(50);
    // Four ingredients need no pager, so every pager here belongs to cookware.
    await userEvent.click(screen.getAllByRole("button", { name: "Next" })[0]);
    expect(screen.getAllByTestId(/^all-cookware-/)).toHaveLength(10);
    expect(screen.getByTestId("all-ingredient-1")).toBeInTheDocument();

    await userEvent.type(screen.getByRole("searchbox", { name: "Search names and aliases" }), "锅0");
    expect(screen.getByTestId("all-cookware-1")).toBeInTheDocument();
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
