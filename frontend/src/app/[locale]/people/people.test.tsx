import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PeopleManager } from "@/components/PeopleManager";
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
    "GET /api/diners": () => [
      { id: 1, display_name: "Me", is_self: true },
      { id: 2, display_name: "小李", is_self: false },
    ],
    "GET /api/diners/1/rules": () => [],
    "GET /api/diners/2/rules": () => [
      { id: 5, diner_id: 2, rule_type: "allergy", allergen: "egg", ingredient_id: null, ingredient_name_zh: null, ingredient_name_en: null, text: null },
    ],
    "POST /api/diners/1/rules": (body) => ({ id: 6, diner_id: 1, ...(body as object), ingredient_name_zh: null, ingredient_name_en: null }),
    "DELETE /api/diners/2": () => new Response(null, { status: 204 }),
  });
  renderWithProviders(<PeopleManager />);
}

beforeEach(() => window.localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("people", () => {
  it("offers the fixed allergen list and a specific ingredient for an allergy rule", async () => {
    setup();
    const me = within(await screen.findByTestId("diner-1"));
    await userEvent.selectOptions(me.getByRole("combobox", { name: "Rule type" }), "allergy");
    const target = me.getByRole("combobox", { name: "Applies to" });
    const targets = within(target).getAllByRole("option").map((option) => option.textContent);
    expect(targets).toEqual(["An allergen from the list", "A specific ingredient"]);
    const allergens = within(me.getByRole("combobox", { name: "Allergen" })).getAllByRole("option");
    expect(allergens).toHaveLength(10);

    await userEvent.selectOptions(me.getByRole("combobox", { name: "Allergen" }), "peanut");
    await userEvent.click(me.getByRole("button", { name: "Add rule" }));
    await waitFor(() => expect(callsTo(calls, "POST", "/api/diners/1/rules")).toHaveLength(1));
    expect(callsTo(calls, "POST", "/api/diners/1/rules")[0].body).toEqual({
      rule_type: "allergy",
      allergen: "peanut",
      ingredient_id: null,
      text: null,
    });
  });

  it("offers free text for a dietary restriction", async () => {
    setup();
    const me = within(await screen.findByTestId("diner-1"));
    await userEvent.selectOptions(me.getByRole("combobox", { name: "Rule type" }), "dietary restriction");
    const options = within(me.getByRole("combobox", { name: "Applies to" })).getAllByRole("option");
    expect(options.map((option) => option.textContent)).toContain("Free text");
  });

  it("does not offer delete for the user's own record", async () => {
    setup();
    const me = within(await screen.findByTestId("diner-1"));
    expect(me.queryByRole("button", { name: "Delete" })).toBeNull();
  });

  it("asks before deleting a friend and says their rules are deleted too", async () => {
    setup();
    const friend = within(await screen.findByTestId("diner-2"));
    // Exact text, because the add-rule form also lists "Egg" as an option.
    expect(await friend.findByText("Allergy: Egg")).toBeInTheDocument();
    await userEvent.click(friend.getByRole("button", { name: "Delete" }));
    expect(friend.getByText("Delete 小李?")).toBeInTheDocument();
    expect(friend.getByText(/dietary rules are deleted too/)).toBeInTheDocument();
    expect(callsTo(calls, "DELETE", "/api/diners/2")).toHaveLength(0);
    await userEvent.click(friend.getByRole("button", { name: "Yes, delete" }));
    await waitFor(() => expect(callsTo(calls, "DELETE", "/api/diners/2")).toHaveLength(1));
  });
});

describe("people paging", () => {
  it("pages the diners but shows all of a diner's rules without a pager", async () => {
    const diners = Array.from({ length: 25 }, (_, index) => ({
      id: index + 1,
      display_name: `朋友${index + 1}`,
      is_self: index === 0,
    }));
    const rules = Array.from({ length: 25 }, (_, index) => ({
      id: 500 + index,
      diner_id: 1,
      rule_type: "dislike",
      allergen: null,
      ingredient_id: null,
      ingredient_name_zh: null,
      ingredient_name_en: null,
      text: `规则${index + 1}`,
    }));
    mockFetch({
      "GET /api/diners": () => diners,
      "GET /api/diners/1/rules": () => rules,
    });
    renderWithProviders(<PeopleManager />);
    const first = within(await screen.findByTestId("diner-1"));
    await first.findByText(/规则1$/);
    expect(screen.getAllByTestId(/^diner-/)).toHaveLength(20);
    expect(screen.getAllByText("25 items")).toHaveLength(1);
    expect(first.getByText(/规则1$/)).toBeInTheDocument();
    expect(first.getByText(/规则25$/)).toBeInTheDocument();
    expect(first.queryByRole("navigation")).toBeNull();
    expect(window.localStorage.getItem("pageSize:rules")).toBeNull();
  });
});

describe("diner labels", () => {
  function setupDiners(diners: { id: number; display_name: string; is_self: boolean }[]) {
    mockFetch({ "GET /api/diners": () => diners, "GET /api/diners/1/rules": () => [], "GET /api/diners/2/rules": () => [] });
    renderWithProviders(<PeopleManager />);
  }

  it("titles the self card Me with no rename button, whatever the stored name", async () => {
    setupDiners([{ id: 1, display_name: "小王", is_self: true }]);
    const card = within(await screen.findByTestId("diner-1"));
    expect(card.getByText("Me", { selector: "strong" })).toBeInTheDocument();
    expect(card.queryByText("小王")).toBeNull();
    expect(card.queryByRole("button", { name: "Rename" })).toBeNull();
  });

  it("shows a friend by name with a rename button", async () => {
    setupDiners([
      { id: 1, display_name: "Me", is_self: true },
      { id: 2, display_name: "小李", is_self: false },
    ]);
    const friend = within(await screen.findByTestId("diner-2"));
    expect(friend.getByText("小李", { selector: "strong" })).toBeInTheDocument();
    expect(friend.getByRole("button", { name: "Rename" })).toBeInTheDocument();
  });
});
