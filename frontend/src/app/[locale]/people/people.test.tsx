import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PeopleManager } from "@/components/PeopleManager";
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
    "DELETE /api/rules/5": () => new Response(null, { status: 204 }),
  });
  renderWithProviders(<PeopleManager />);
}

beforeEach(() => window.localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("people", () => {
  it("offers the fixed allergen list and a specific ingredient for an allergy rule", async () => {
    setup();
    const me = within(await screen.findByTestId("diner-1"));
    await userEvent.click(me.getByRole("button", { name: "New rule" }));
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

  it("shows the actions as small text buttons, delete in red", async () => {
    setup();
    const friend = within(await screen.findByTestId("diner-2"));
    expect(friend.getByRole("button", { name: "New rule" })).toHaveClass("btn-text");
    expect(friend.getByRole("button", { name: "Rename" })).toHaveClass("btn-text");
    expect(friend.getByRole("button", { name: "Delete" })).toHaveClass("btn-text", "btn-danger");
    expect(friend.queryByRole("heading", { name: "Rules" })).toBeNull();
  });

  it("keeps the add-rule form closed until New rule is clicked, then opens it compact", async () => {
    setup();
    const friend = within(await screen.findByTestId("diner-2"));
    expect(friend.queryByRole("button", { name: "Add rule" })).toBeNull();
    expect(friend.queryByRole("combobox", { name: "Rule type" })).toBeNull();
    const toggle = friend.getByRole("button", { name: "New rule" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const add = friend.getByRole("button", { name: "Add rule" });
    expect(add.closest(".diner-edit")).not.toBeNull();
    // Only this person's form opens.
    expect(within(screen.getByTestId("diner-1")).queryByRole("button", { name: "Add rule" })).toBeNull();
    await userEvent.click(toggle);
    expect(friend.queryByRole("button", { name: "Add rule" })).toBeNull();
  });

  it("shows each rule as a chip whose × removes it", async () => {
    setup();
    const friend = within(await screen.findByTestId("diner-2"));
    const remove = await friend.findByRole("button", { name: "Remove Allergy: Egg" });
    expect(remove.closest(".rule-chip")).toHaveTextContent("Allergy: Egg");
    expect(friend.queryByRole("button", { name: "Remove" })).toBeNull();
    await userEvent.click(remove);
    await waitFor(() => expect(callsTo(calls, "DELETE", "/api/rules/5")).toHaveLength(1));
  });

  it("keeps the diner list in one card and hides the pager when everyone fits on one page", async () => {
    calls = mockFetch({
      "GET /api/diners": () =>
        [1, 2, 3, 4].map((id) => ({ id, display_name: id === 1 ? "Me" : `朋友${id}`, is_self: id === 1 })),
      "GET /api/diners/1/rules": () => [],
      "GET /api/diners/2/rules": () => [],
      "GET /api/diners/3/rules": () => [],
      "GET /api/diners/4/rules": () => [],
    });
    renderWithProviders(<PeopleManager />);
    const last = await screen.findByTestId("diner-4");
    expect(last.closest(".card")).toContainElement(screen.getByTestId("diner-1"));
    expect(screen.queryByRole("navigation")).toBeNull();
    expect(screen.queryByText("4 items")).toBeNull();
  });

  it("offers free text for a dietary restriction", async () => {
    setup();
    const me = within(await screen.findByTestId("diner-1"));
    await userEvent.click(me.getByRole("button", { name: "New rule" }));
    await userEvent.selectOptions(me.getByRole("combobox", { name: "Rule type" }), "dietary restriction");
    const options = within(me.getByRole("combobox", { name: "Applies to" })).getAllByRole("option");
    expect(options.map((option) => option.textContent)).toContain("Free text");
  });

  it("does not offer rename or delete for the user's own record, but keeps their slots", async () => {
    setup();
    const row = await screen.findByTestId("diner-1");
    const me = within(row);
    expect(me.queryByRole("button", { name: "Delete" })).toBeNull();
    expect(me.queryByRole("button", { name: "Rename" })).toBeNull();
    expect(me.getByRole("button", { name: "New rule" })).toBeInTheDocument();
    expect(row.querySelectorAll(".person-slot")).toHaveLength(2);
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

const NICKNAME_WARNING =
  "This nickname also appears in ingredient names and may be misread when planning; consider a longer nickname.";
const DIET_HINT =
  "Rules by food category are not supported yet. Add each ingredient they do not eat (such as pork, beef, chicken, fish, shrimp).";

describe("people warnings", () => {
  const me = { id: 1, display_name: "Me", is_self: true, nickname_warning: false };

  it("shows the nickname warning next to a friend whose name looks like food", async () => {
    mockFetch({
      "GET /api/diners": () => [me, { id: 2, display_name: "鱼", is_self: false, nickname_warning: true }],
      "GET /api/diners/1/rules": () => [],
      "GET /api/diners/2/rules": () => [],
    });
    renderWithProviders(<PeopleManager />);
    const friend = within(await screen.findByTestId("diner-2"));
    expect(friend.getByText(NICKNAME_WARNING)).toBeInTheDocument();
    expect(within(screen.getByTestId("diner-1")).queryByText(NICKNAME_WARNING)).toBeNull();
  });

  it("shows no warning for an ordinary nickname", async () => {
    mockFetch({
      "GET /api/diners": () => [me, { id: 2, display_name: "小李", is_self: false, nickname_warning: false }],
      "GET /api/diners/1/rules": () => [],
      "GET /api/diners/2/rules": () => [],
    });
    renderWithProviders(<PeopleManager />);
    await screen.findByTestId("diner-2");
    expect(screen.queryByText(NICKNAME_WARNING)).toBeNull();
  });

  it("shows the warning right after a rename and still saves", async () => {
    let name = "小李";
    let warning = false;
    const calls = mockFetch({
      "GET /api/diners": () => [me, { id: 2, display_name: name, is_self: false, nickname_warning: warning }],
      "GET /api/diners/1/rules": () => [],
      "GET /api/diners/2/rules": () => [],
      "PATCH /api/diners/2": (body) => {
        name = (body as { display_name: string }).display_name;
        warning = true;
        return { id: 2, display_name: name, is_self: false, nickname_warning: true };
      },
    });
    renderWithProviders(<PeopleManager />);
    const friend = within(await screen.findByTestId("diner-2"));
    await userEvent.click(friend.getByRole("button", { name: "Rename" }));
    const input = friend.getByRole("textbox", { name: "Name or nickname" });
    await userEvent.clear(input);
    await userEvent.type(input, "鱼");
    await userEvent.click(friend.getByRole("button", { name: "Save" }));
    expect(await within(screen.getByTestId("diner-2")).findByText(NICKNAME_WARNING)).toBeInTheDocument();
    expect(callsTo(calls, "PATCH", "/api/diners/2")).toHaveLength(1);
  });

  it("shows the warning right after adding a friend with a food-like nickname", async () => {
    let added = false;
    mockFetch({
      "GET /api/diners": () =>
        added ? [me, { id: 3, display_name: "鱼", is_self: false, nickname_warning: true }] : [me],
      "GET /api/diners/1/rules": () => [],
      "GET /api/diners/3/rules": () => [],
      "POST /api/diners": () => {
        added = true;
        return { id: 3, display_name: "鱼", is_self: false, nickname_warning: true };
      },
    });
    renderWithProviders(<PeopleManager />);
    await screen.findByTestId("diner-1");
    await userEvent.type(screen.getByLabelText("Name or nickname"), "鱼");
    await userEvent.click(screen.getAllByRole("button", { name: "Add a friend" })[0]);
    expect(await within(await screen.findByTestId("diner-3")).findByText(NICKNAME_WARNING)).toBeInTheDocument();
  });

  it("shows the diet-word hint next to the person when the server refuses a text rule", async () => {
    const calls = mockFetch({
      "GET /api/diners": () => [me, { id: 2, display_name: "小李", is_self: false, nickname_warning: false }],
      "GET /api/diners/1/rules": () => [],
      "GET /api/diners/2/rules": () => [],
      "POST /api/diners/2/rules": () => reply(422, { code: "diet_word" }),
    });
    renderWithProviders(<PeopleManager />);
    const friend = within(await screen.findByTestId("diner-2"));
    expect(friend.queryByText(DIET_HINT)).toBeNull();
    await userEvent.click(friend.getByRole("button", { name: "New rule" }));
    await userEvent.selectOptions(friend.getByRole("combobox", { name: "Rule type" }), "dietary restriction");
    await userEvent.selectOptions(friend.getByRole("combobox", { name: "Applies to" }), "text");
    await userEvent.type(friend.getByRole("textbox", { name: "Description" }), "吃素");
    await userEvent.click(friend.getByRole("button", { name: "Add rule" }));
    await waitFor(() => expect(callsTo(calls, "POST", "/api/diners/2/rules")).toHaveLength(1));
    expect(await friend.findAllByText(DIET_HINT)).toHaveLength(1);
    expect(within(screen.getByTestId("diner-1")).queryByText(DIET_HINT)).toBeNull();
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
