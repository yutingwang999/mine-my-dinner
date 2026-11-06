import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

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
    expect(await friend.findByText(/Egg/)).toBeInTheDocument();
    await userEvent.click(friend.getByRole("button", { name: "Delete" }));
    expect(friend.getByText("Delete 小李?")).toBeInTheDocument();
    expect(friend.getByText(/Their rules are deleted too/)).toBeInTheDocument();
    expect(callsTo(calls, "DELETE", "/api/diners/2")).toHaveLength(0);
    await userEvent.click(friend.getByRole("button", { name: "Yes, delete" }));
    await waitFor(() => expect(callsTo(calls, "DELETE", "/api/diners/2")).toHaveLength(1));
  });
});
