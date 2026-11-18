import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DinerCard } from "@/components/DinerCard";
import type { DinerAnswer, DinersPending, ProposedRule } from "@/lib/chat";
import { callsTo, mockFetch, renderWithProviders } from "@/test/helpers";

function proposed(fields: Partial<ProposedRule>): ProposedRule {
  return {
    rule_type: "dietary restriction",
    allergen: null,
    ingredient_id: null,
    ingredient_name: null,
    text: null,
    by_wording: false,
    ...fields,
  };
}

function savedRule(id: number, dinerId: number, fields: Record<string, unknown>) {
  return {
    id,
    diner_id: dinerId,
    rule_type: "allergy" as const,
    allergen: null,
    ingredient_id: null,
    ingredient_name_zh: null,
    ingredient_name_en: null,
    text: null,
    ...fields,
  };
}

/** The user, a friend named in the chat with two stated rules, a friend not named, and Alex. */
function card(fields: Partial<DinersPending> = {}): DinersPending {
  return {
    kind: "diners",
    headcount: 3,
    rows: [
      {
        diner_id: 1,
        name: null,
        is_self: true,
        selected: true,
        rules: [savedRule(10, 1, { allergen: "peanut" })],
        proposed: [],
        diet_words: [],
      },
      {
        diner_id: 2,
        name: "Xiao Wang",
        is_self: false,
        selected: true,
        rules: [],
        proposed: [
          proposed({ ingredient_id: 7, ingredient_name: "cilantro" }),
          proposed({ rule_type: "dislike", text: "spicy sauce", by_wording: true }),
        ],
        diet_words: [],
      },
      {
        diner_id: 3,
        name: "Lao Li",
        is_self: false,
        selected: false,
        rules: [
          savedRule(30, 3, {
            rule_type: "dietary restriction",
            ingredient_id: 9,
            ingredient_name_zh: "牛肉",
            ingredient_name_en: "beef",
          }),
        ],
        proposed: [],
        diet_words: [],
      },
    ],
    guests: [{ name: "Alex", proposed: [proposed({ rule_type: "allergy", allergen: "peanut" })], diet_words: [] }],
    others: 0,
    nudge: false,
    ...fields,
  };
}

/** Only the user is known; two more people eat. */
function soloWithOthers(): DinersPending {
  return card({ rows: [card().rows[0]], guests: [], others: 2, headcount: 3 });
}

function setup(pending: DinersPending) {
  const calls = mockFetch({
    "DELETE /api/rules/30": () => new Response(null, { status: 204 }),
  });
  const onConfirm = vi.fn<(answer: DinerAnswer) => void>();
  renderWithProviders(<DinerCard pending={pending} busy={false} onConfirm={onConfirm} />);
  return { onConfirm, calls };
}

async function confirm(onConfirm: ReturnType<typeof setup>["onConfirm"]): Promise<DinerAnswer> {
  await userEvent.click(screen.getByRole("button", { name: "Confirm and start planning" }));
  expect(onConfirm).toHaveBeenCalledTimes(1);
  return onConfirm.mock.calls[0][0];
}

afterEach(() => vi.unstubAllGlobals());

describe("DinerCard", () => {
  it("renders the preselection as checked and lists each person's rules", () => {
    setup(card());
    expect(screen.getByText("Who is eating? (3 in all)")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Me" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Xiao Wang" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Lao Li" })).not.toBeChecked();
    expect(within(screen.getByTestId("diner-row-1")).getByText("Allergy: Peanut")).toBeInTheDocument();
    expect(within(screen.getByTestId("diner-row-3")).getByText("Dietary restriction: beef")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Dietary restriction: cilantro" })).toBeChecked();
    expect(screen.getByLabelText("Name")).toHaveValue("Alex");
  });

  it("marks a rule matched by wording", () => {
    setup(card());
    const row = screen.getByTestId("diner-row-2");
    const label = within(row).getByText("Dislike: spicy sauce").closest("label") as HTMLElement;
    expect(within(label).getByText("Matched by wording")).toBeInTheDocument();
    const cilantro = within(row).getByText("Dietary restriction: cilantro").closest("label") as HTMLElement;
    expect(within(cilantro).queryByText("Matched by wording")).toBeNull();
  });

  it("confirms the expected answer", async () => {
    const { onConfirm } = setup(card());
    expect(await confirm(onConfirm)).toEqual({
      selected: [1, 2],
      stated: {
        2: [
          { rule_type: "dietary restriction", allergen: null, ingredient_id: 7, text: null, save: false },
          { rule_type: "dislike", allergen: null, ingredient_id: null, text: "spicy sauce", save: false },
        ],
      },
      guests: [
        {
          name: "Alex",
          rules: [{ rule_type: "allergy", allergen: "peanut", ingredient_id: null, text: null, save: false }],
          save: false,
        },
      ],
      others: 0,
      others_answer: null,
    });
  });

  it("leaves an unticked proposed rule out of the answer and saves a ticked one when asked", async () => {
    const { onConfirm } = setup(card());
    await userEvent.click(screen.getByRole("checkbox", { name: "Dietary restriction: cilantro" }));
    const row = screen.getByTestId("diner-row-2");
    const spicy = within(row).getByText("Dislike: spicy sauce").closest("li") as HTMLElement;
    await userEvent.click(within(spicy).getByRole("checkbox", { name: "Save to profile" }));
    const answer = await confirm(onConfirm);
    expect(answer.stated).toEqual({
      2: [{ rule_type: "dislike", allergen: null, ingredient_id: null, text: "spicy sauce", save: true }],
    });
  });

  it("drops the stated rules of a person who is deselected", async () => {
    const { onConfirm } = setup(card());
    await userEvent.click(screen.getByRole("checkbox", { name: "Xiao Wang" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Lao Li" }));
    const answer = await confirm(onConfirm);
    expect(answer.selected).toEqual([1, 3]);
    expect(answer.stated).toEqual({});
  });

  it("adds an empty guest row per unmatched person for 'Yes, I'll fill them in'", async () => {
    const { onConfirm } = setup(soloWithOthers());
    expect(screen.getByText("2 people are not matched yet")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Yes, I'll fill them in" }));
    expect(screen.getAllByLabelText("Name")).toHaveLength(2);
    expect(screen.queryByText(/not matched yet/)).toBeNull();
    const answer = await confirm(onConfirm);
    expect(answer.others).toBe(0);
    expect(answer.others_answer).toBeNull();
    expect(answer.guests).toEqual([
      { name: null, rules: [], save: false },
      { name: null, rules: [], save: false },
    ]);
  });

  it("answers 'unsure' for the unmatched people", async () => {
    const { onConfirm } = setup(soloWithOthers());
    await userEvent.click(screen.getByRole("button", { name: "Not sure" }));
    const answer = await confirm(onConfirm);
    expect(answer.others).toBe(2);
    expect(answer.others_answer).toBe("unsure");
  });

  it("answers 'none' for the unmatched people", async () => {
    const { onConfirm } = setup(soloWithOthers());
    await userEvent.click(screen.getByRole("button", { name: "No dietary limits" }));
    expect((await confirm(onConfirm)).others_answer).toBe("none");
  });

  it("shows a diet word as the fixed hint, never as a rule", async () => {
    const { onConfirm } = setup(
      card({ guests: [{ name: "Alex", proposed: [], diet_words: ["vegetarian"] }] }),
    );
    const guest = screen.getByTestId("guest-row-0");
    expect(within(guest).getByText(/Rules by food category are not supported yet/)).toBeInTheDocument();
    expect(within(guest).queryByRole("checkbox", { name: /vegetarian/ })).toBeNull();
    const answer = await confirm(onConfirm);
    expect(answer.guests).toEqual([{ name: "Alex", rules: [], save: false }]);
  });

  it("disables saving a guest without a name", async () => {
    const { onConfirm } = setup(card({ guests: [] }));
    await userEvent.click(screen.getByRole("button", { name: "Add a person" }));
    const guest = screen.getByTestId("guest-row-0");
    const save = within(guest).getByRole("checkbox", { name: /Save to friend profiles/ });
    expect(save).toBeDisabled();
    const name = within(guest).getByRole("textbox", { name: "Name" });
    expect(name).toHaveAttribute("placeholder", "Name");
    await userEvent.type(name, "Mia");
    expect(save).toBeEnabled();
    await userEvent.click(save);
    const answer = await confirm(onConfirm);
    expect(answer.guests).toEqual([{ name: "Mia", rules: [], save: true }]);
  });

  it("removes a guest", async () => {
    const { onConfirm } = setup(card());
    await userEvent.click(within(screen.getByTestId("guest-row-0")).getByRole("button", { name: "Remove" }));
    expect((await confirm(onConfirm)).guests).toEqual([]);
  });

  it("shows the nudge notice only when asked", () => {
    setup(card({ nudge: true }));
    expect(screen.getByText("You mentioned a dietary limit. Please check that it is filled in.")).toBeInTheDocument();
  });

  it("shows no nudge notice otherwise", () => {
    setup(card());
    expect(screen.queryByText(/You mentioned a dietary limit/)).toBeNull();
  });

  it("raises the headcount when more people are selected than stated", async () => {
    setup(card({ headcount: 2 }));
    expect(screen.getByText("Who is eating? (3 in all)")).toBeInTheDocument();
    expect(screen.getByText("The number of people is now 3.")).toBeInTheDocument();
  });

  it("removes a friend's saved rule from the profile at once", async () => {
    const { calls } = setup(card());
    const row = screen.getByTestId("diner-row-3");
    await userEvent.click(within(row).getByRole("button", { name: "Edit" }));
    await userEvent.click(within(row).getByRole("button", { name: "Remove" }));
    expect(callsTo(calls, "DELETE", "/api/rules/30")).toHaveLength(1);
    expect(await within(row).findByText("No rules.")).toBeInTheDocument();
  });

  it("reads each person's saved rules again when it opens", async () => {
    const calls = mockFetch({
      "GET /api/diners/1/rules": () => [savedRule(10, 1, { allergen: "peanut" }), savedRule(11, 1, { allergen: "egg" })],
      "GET /api/diners/3/rules": () => [],
    });
    renderWithProviders(<DinerCard pending={card()} busy={false} onConfirm={vi.fn()} />);
    // The interrupt's lists show first.
    expect(within(screen.getByTestId("diner-row-3")).getByText("Dietary restriction: beef")).toBeInTheDocument();
    expect(await within(screen.getByTestId("diner-row-3")).findByText("No rules.")).toBeInTheDocument();
    expect(within(screen.getByTestId("diner-row-1")).getByText("Allergy: Egg")).toBeInTheDocument();
    // A failed read (friend 2 has no route) keeps the interrupt's list.
    expect(callsTo(calls, "GET", "/api/diners/2/rules")).toHaveLength(1);
    expect(within(screen.getByTestId("diner-row-2")).getByText("No rules.")).toBeInTheDocument();
  });

  it("allows at most 20 guest rows", async () => {
    const twenty = Array.from({ length: 20 }, (_, index) => ({ name: `G${index}`, proposed: [], diet_words: [] }));
    setup(card({ guests: twenty, headcount: 22 }));
    expect(screen.getByRole("button", { name: "Add a person" })).toBeDisabled();
  });

  it("does not fill in more unmatched people than the guest limit allows", () => {
    setup(card({ rows: [card().rows[0]], guests: [], headcount: 22, others: 21 }));
    expect(screen.getByText("21 people are not matched yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Yes, I'll fill them in" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Add a person" })).toBeEnabled();
  });

  it("sends at most 50 unmatched people", async () => {
    const { onConfirm } = setup(card({ rows: [card().rows[0]], guests: [], headcount: 80, others: 79 }));
    expect((await confirm(onConfirm)).others).toBe(50);
  });

  it("counts a deselected person as not matched, so the headcount stays", () => {
    setup(card());
    expect(screen.queryByText(/not matched yet/)).toBeNull();
    return userEvent.click(screen.getByRole("checkbox", { name: "Xiao Wang" })).then(() => {
      expect(screen.getByText("1 person is not matched yet")).toBeInTheDocument();
    });
  });

  it("toggles a person's checkbox from the name, but not from the Edit button", async () => {
    setup(card());
    const row = screen.getByTestId("diner-row-3");
    const box = screen.getByRole("checkbox", { name: "Lao Li" });
    expect(box).not.toBeChecked();
    await userEvent.click(within(row).getByText("Lao Li"));
    expect(box).toBeChecked();
    await userEvent.click(within(row).getByRole("button", { name: "Edit" }));
    expect(box).toBeChecked();
  });
});
