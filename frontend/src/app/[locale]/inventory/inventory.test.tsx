import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { InventoryManager } from "@/components/InventoryManager";
import { type Call, callsTo, mockFetch, renderWithProviders } from "@/test/helpers";

const replace = vi.fn();
let search = new URLSearchParams();

vi.mock("next/navigation", () => ({ useSearchParams: () => search }));
vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
  useRouter: () => ({ push: vi.fn(), replace }),
}));

function entry(id: number, name: string, stockMode: string) {
  return {
    id,
    name_zh: name,
    name_en: null,
    aliases_zh: [],
    aliases_en: [],
    source: "seed",
    category: "other",
    stock_mode: stockMode,
    allergens: [],
    parent_id: null,
  };
}

const ENTRIES = [entry(1, "鸡蛋", "count"), entry(2, "猪肉", "level"), entry(3, "盐", "availability")];

let calls: Call[];

function setup() {
  calls = mockFetch({
    "GET /api/inventory": () => [],
    "GET /api/ingredients": () => ENTRIES,
    "GET /api/always-in-stock": () => [{ id: 3, name_zh: "盐", name_en: "salt" }],
    "GET /api/cookware-owned": () => [],
    "GET /api/cookware": () => [],
    "PUT /api/inventory": (body) => ({ ...(body as object), name_zh: "x", name_en: null, stock_mode: "count", updated_at: "" }),
    "DELETE /api/always-in-stock/3": () => new Response(null, { status: 204 }),
  });
  renderWithProviders(<InventoryManager />);
}

async function choose(name: string) {
  const search = screen.getAllByRole("searchbox", { name: "Search the dictionary" })[0];
  await userEvent.clear(search);
  await userEvent.type(search, name.slice(0, 1));
  await userEvent.click(await screen.findByRole("button", { name: `Choose ${name}` }));
}

function rows(count: number) {
  return Array.from({ length: count }, (_, index) => ({
    ingredient_id: 100 + index,
    name_zh: `库存${String(index + 1).padStart(2, "0")}`,
    name_en: null,
    stock_mode: "availability",
    count: null,
    level: null,
    grams: null,
    expires_on: null,
    updated_at: "",
  }));
}

beforeEach(() => {
  window.localStorage.clear();
  search = new URLSearchParams();
  replace.mockClear();
  window.location.hash = "";
});
afterEach(() => vi.unstubAllGlobals());

describe("inventory", () => {
  it("shows only the field the stock_mode allows", async () => {
    setup();
    await choose("鸡蛋");
    expect(screen.getByRole("spinbutton", { name: "Count" })).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Level" })).toBeNull();

    await choose("猪肉");
    expect(screen.getByRole("combobox", { name: "Level" })).toBeInTheDocument();
    expect(screen.getByRole("spinbutton", { name: "Grams (optional)" })).toBeInTheDocument();
    expect(screen.queryByRole("spinbutton", { name: "Count" })).toBeNull();

    await choose("盐");
    expect(screen.queryByRole("spinbutton", { name: "Count" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Level" })).toBeNull();
    expect(screen.getByText(/Being listed here means you have it/)).toBeInTheDocument();
  });

  it("sends only the allowed field", async () => {
    setup();
    await choose("鸡蛋");
    await userEvent.type(screen.getByRole("spinbutton", { name: "Count" }), "3");
    await userEvent.click(screen.getByRole("button", { name: "Add to inventory" }));
    await waitFor(() => expect(callsTo(calls, "PUT", "/api/inventory")).toHaveLength(1));
    expect(callsTo(calls, "PUT", "/api/inventory")[0].body).toEqual({
      ingredient_id: 1,
      count: 3,
      level: null,
      grams: null,
      expires_on: null,
    });
  });

  it("uses the shared button classes: Remove danger", async () => {
    search = new URLSearchParams("tab=always");
    setup();
    const salt = within(await screen.findByTestId("always-3"));
    expect(salt.getByRole("button", { name: "Remove" })).toHaveClass("btn-danger");
  });

  it("makes the add button of a stock row primary", async () => {
    setup();
    await choose("鸡蛋");
    expect(await screen.findByRole("button", { name: "Add to inventory" })).toHaveClass("btn-primary");
  });

  it("removes an always-in-stock item", async () => {
    search = new URLSearchParams("tab=always");
    setup();
    const salt = await screen.findByTestId("always-3");
    await userEvent.click(salt.querySelector("button") as HTMLButtonElement);
    await waitFor(() => expect(callsTo(calls, "DELETE", "/api/always-in-stock/3")).toHaveLength(1));
  });
});

describe("inventory paging", () => {
  it("shows 20 rows and the rest on page 2, and a chosen size returns to page 1", async () => {
    calls = mockFetch({
      "GET /api/inventory": () => rows(25),
      "GET /api/ingredients": () => [],
      "GET /api/always-in-stock": () => [],
      "GET /api/cookware-owned": () => [],
      "GET /api/cookware": () => [],
    });
    renderWithProviders(<InventoryManager />);
    await screen.findByTestId("inventory-100");
    expect(screen.getAllByTestId(/^inventory-/)).toHaveLength(20);
    expect(screen.getByText("25 items")).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("button", { name: "Next" })[0]);
    expect(screen.getAllByTestId(/^inventory-/)).toHaveLength(5);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Rows per page" }), "50");
    expect(screen.getAllByTestId(/^inventory-/)).toHaveLength(25);
    expect(window.localStorage.getItem("pageSize:inventory")).toBe("50");
    expect(within(screen.getByTestId("inventory-100")).getByText("库存01")).toBeInTheDocument();
  });

  function refs(count: number) {
    return Array.from({ length: count }, (_, index) => ({
      id: 300 + index,
      name_zh: `项${String(index + 1).padStart(2, "0")}`,
      name_en: null,
    }));
  }

  function setupRefs(routes: Record<string, () => unknown>) {
    mockFetch({
      "GET /api/inventory": () => [],
      "GET /api/ingredients": () => [],
      "GET /api/always-in-stock": () => [],
      "GET /api/cookware-owned": () => [],
      "GET /api/cookware": () => [],
      ...routes,
    });
    renderWithProviders(<InventoryManager />);
  }

  it("pages the always-in-stock list", async () => {
    search = new URLSearchParams("tab=always");
    setupRefs({ "GET /api/always-in-stock": () => refs(25) });
    await screen.findByTestId("always-300");
    expect(screen.getAllByTestId(/^always-/)).toHaveLength(20);
    await userEvent.click(screen.getAllByRole("button", { name: "Next" })[0]);
    expect(screen.getAllByTestId(/^always-/)).toHaveLength(5);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Rows per page" }), "50");
    expect(window.localStorage.getItem("pageSize:always-in-stock")).toBe("50");
    expect(screen.getAllByTestId(/^always-/)).toHaveLength(25);
  });

  it("pages the owned cookware list", async () => {
    search = new URLSearchParams("tab=cookware");
    setupRefs({ "GET /api/cookware-owned": () => refs(25) });
    await screen.findByTestId("owned-300");
    expect(screen.getAllByTestId(/^owned-/)).toHaveLength(20);
    await userEvent.click(screen.getAllByRole("button", { name: "Next" })[0]);
    expect(screen.getAllByTestId(/^owned-/)).toHaveLength(5);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Rows per page" }), "50");
    expect(window.localStorage.getItem("pageSize:owned-cookware")).toBe("50");
  });
});

describe("inventory search and descriptions", () => {
  const empty = {
    "GET /api/inventory": () => [],
    "GET /api/ingredients": () => [],
    "GET /api/always-in-stock": () => [],
    "GET /api/cookware-owned": () => [],
    "GET /api/cookware": () => [],
  };

  it("filters the stock list live and returns to page 1", async () => {
    mockFetch({ ...empty, "GET /api/inventory": () => rows(25) });
    renderWithProviders(<InventoryManager />);
    await screen.findByTestId("inventory-100");
    await userEvent.click(screen.getAllByRole("button", { name: "Next" })[0]);
    expect(screen.getAllByTestId(/^inventory-/)).toHaveLength(5);
    const box = screen.getByRole("searchbox", { name: "Find by name" });
    expect(box).toHaveAttribute("placeholder", "Find by name");
    await userEvent.type(box, "库存0");
    expect(screen.getAllByTestId(/^inventory-/)).toHaveLength(9);
    expect(screen.getByText("9 items")).toBeInTheDocument();
    await userEvent.clear(box);
    await userEvent.type(box, "zzz");
    expect(screen.getByText("Nothing matches.")).toBeInTheDocument();
  });

  it.each([
    ["always", "GET /api/always-in-stock", "always-"],
    ["cookware", "GET /api/cookware-owned", "owned-"],
  ])("filters the %s list", async (tab, route, prefix) => {
    search = new URLSearchParams(`tab=${tab}`);
    const items = Array.from({ length: 25 }, (_, index) => ({
      id: 300 + index,
      name_zh: `项${String(index + 1).padStart(2, "0")}`,
      name_en: index === 2 ? "Wok" : null,
    }));
    mockFetch({ ...empty, [route]: () => items });
    renderWithProviders(<InventoryManager />);
    await screen.findByTestId(`${prefix}300`);
    const box = screen.getByRole("searchbox", { name: "Find by name" });
    await userEvent.type(box, "wok");
    expect(screen.getAllByTestId(new RegExp(`^${prefix}`))).toHaveLength(1);
    await userEvent.clear(box);
    await userEvent.type(box, "zzz");
    expect(screen.getByText("Nothing matches.")).toBeInTheDocument();
  });

  it.each([
    ["stock", /What you have at home right now/],
    ["always", /Things you always have, like salt/],
    ["cookware", /Cookware you own\. Plans only suggest/],
  ])("describes the %s tab", async (tab, text) => {
    search = new URLSearchParams(`tab=${tab}`);
    mockFetch(empty);
    renderWithProviders(<InventoryManager />);
    expect(await screen.findByText(text)).toBeInTheDocument();
  });
});

describe("inventory tabs", () => {
  it("shows one section at a time with counts and keeps the tab in the address", async () => {
    setup();
    const tabs = within(await screen.findByRole("tablist", { name: "Inventory sections" }));
    await waitFor(() =>
      expect(tabs.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
        "Stock (0)",
        "Always in stock (1)",
        "Cookware I own (0)",
      ]),
    );
    expect(tabs.getByRole("tab", { name: /Stock/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByTestId("always-3")).toBeNull();

    await userEvent.click(tabs.getByRole("tab", { name: /Always in stock/ }));
    expect(replace).toHaveBeenLastCalledWith("/inventory?tab=always");
    expect(await screen.findByTestId("always-3")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Stock" })).toBeNull();

    await userEvent.click(tabs.getByRole("tab", { name: /Cookware I own/ }));
    expect(replace).toHaveBeenLastCalledWith("/inventory?tab=cookware");
    expect(screen.queryByTestId("always-3")).toBeNull();
  });

  it("opens the tab named in the address and ignores an unknown one", async () => {
    search = new URLSearchParams("tab=cookware");
    setup();
    expect(await screen.findByRole("tab", { name: /Cookware I own/ })).toHaveAttribute("aria-selected", "true");
    expect(replace).not.toHaveBeenCalled();
  });

  it("falls back to stock for an unknown tab", async () => {
    search = new URLSearchParams("tab=nope");
    setup();
    expect(await screen.findByRole("tab", { name: /Stock/ })).toHaveAttribute("aria-selected", "true");
  });

  it("opens the cookware tab for the old #cookware link", async () => {
    window.location.hash = "#cookware";
    setup();
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: /Cookware I own/ })).toHaveAttribute("aria-selected", "true"),
    );
    expect(replace).toHaveBeenCalledWith("/inventory?tab=cookware");
  });
});

describe("inventory sort", () => {
  it("shows the active order as pressed and switches the request when the other is clicked", async () => {
    setup();
    const group = within(await screen.findByRole("group", { name: "Sort:" }));
    expect(group.getByRole("button", { name: "Name" })).toHaveAttribute("aria-pressed", "true");
    expect(group.getByRole("button", { name: "Expiry" })).toHaveAttribute("aria-pressed", "false");
    const before = callsTo(calls, "GET", "/api/inventory").length;

    await userEvent.click(group.getByRole("button", { name: "Name" }));
    expect(callsTo(calls, "GET", "/api/inventory")).toHaveLength(before);

    await userEvent.click(group.getByRole("button", { name: "Expiry" }));
    await waitFor(() =>
      expect(callsTo(calls, "GET", "/api/inventory").at(-1)?.url).toContain("sort=expiry"),
    );
    expect(group.getByRole("button", { name: "Expiry" })).toHaveAttribute("aria-pressed", "true");
    expect(group.getByRole("button", { name: "Name" })).toHaveAttribute("aria-pressed", "false");
  });
});
