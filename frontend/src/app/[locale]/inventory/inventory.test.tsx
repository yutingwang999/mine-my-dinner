import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { InventoryManager } from "@/components/InventoryManager";
import { type Call, callsTo, mockFetch, renderWithProviders } from "@/test/helpers";

vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
  useRouter: () => ({ push: vi.fn() }),
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
    expect(screen.getByText(/Having an entry means it is available/)).toBeInTheDocument();
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

  it("removes an always-in-stock item", async () => {
    setup();
    const salt = await screen.findByTestId("always-3");
    await userEvent.click(salt.querySelector("button") as HTMLButtonElement);
    await waitFor(() => expect(callsTo(calls, "DELETE", "/api/always-in-stock/3")).toHaveLength(1));
  });
});
