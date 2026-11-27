import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DinerCard, type DinerDraft } from "@/components/DinerCard";
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

/** "我和朋友吃饭": the user preselected, Xiao Wang not, one person not matched. */
function meAndAFriend(): DinersPending {
  const [me, wang] = card().rows;
  return card({ rows: [me, { ...wang, selected: false, proposed: [] }], guests: [], headcount: 2, others: 1 });
}

function total(): HTMLElement {
  return screen.getByRole("spinbutton", { name: "Number of people" });
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
    expect(total()).toHaveValue(3);
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
      headcount: 3,
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
    expect(total()).toHaveValue(3);
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
    const answer = await confirm(onConfirm);
    expect(answer.others).toBe(50);
    expect(answer.headcount).toBe(51);
  });

  it("counts a deselected person as not matched, so the headcount stays", () => {
    setup(card());
    expect(screen.queryByText(/not matched yet/)).toBeNull();
    return userEvent.click(screen.getByRole("checkbox", { name: "Xiao Wang" })).then(() => {
      expect(screen.getByText("1 person is not matched yet")).toBeInTheDocument();
    });
  });

  it("prefills the editable total from the card", () => {
    setup(card());
    expect(total()).toHaveValue(3);
    expect(screen.getByText("Who is eating?")).toBeInTheDocument();
    expect(screen.getByText("people in all")).toBeInTheDocument();
  });

  it("fills an unmatched place when a friend not preselected is ticked", async () => {
    // "我和朋友吃饭": two people, only the user preselected.
    setup(meAndAFriend());
    expect(total()).toHaveValue(2);
    expect(screen.getByText("1 person is not matched yet")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: "Xiao Wang" }));
    expect(total()).toHaveValue(2);
    expect(screen.queryByText(/not matched yet/)).toBeNull();
  });

  it("fills in the unmatched people as guest rows and keeps the total", async () => {
    const { onConfirm } = setup(meAndAFriend());
    await userEvent.click(screen.getByRole("button", { name: "Yes, I'll fill them in" }));
    expect(screen.getByTestId("guest-row-0")).toBeInTheDocument();
    expect(total()).toHaveValue(2);
    expect(screen.queryByText(/not matched yet/)).toBeNull();
    const answer = await confirm(onConfirm);
    expect(answer.headcount).toBe(2);
    expect(answer.others).toBe(0);
  });

  it("keeps the total when you untick yourself, so one person is not matched", async () => {
    setup(card());
    await userEvent.click(screen.getByRole("checkbox", { name: "Me" }));
    expect(total()).toHaveValue(3);
    expect(screen.getByText("1 person is not matched yet")).toBeInTheDocument();
  });

  it("removes unmatched people when the total is lowered", async () => {
    setup(soloWithOthers());
    expect(screen.getByText("2 people are not matched yet")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "One fewer person" }));
    expect(total()).toHaveValue(2);
    expect(screen.getByText("1 person is not matched yet")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "One fewer person" }));
    expect(total()).toHaveValue(1);
    expect(screen.queryByText(/not matched yet/)).toBeNull();
  });

  it("does not let the total go below the people ticked and added", async () => {
    setup(card());
    expect(screen.getByRole("button", { name: "One fewer person" })).toBeDisabled();
    fireEvent.change(total(), { target: { value: "1" } });
    fireEvent.blur(total());
    expect(total()).toHaveValue(3);
    await userEvent.click(screen.getByRole("button", { name: "One more person" }));
    expect(total()).toHaveValue(4);
    expect(screen.getByText("1 person is not matched yet")).toBeInTheDocument();
    fireEvent.change(total(), { target: { value: "6" } });
    fireEvent.blur(total());
    expect(total()).toHaveValue(6);
    expect(screen.getByText("3 people are not matched yet")).toBeInTheDocument();
  });

  it("lets a multi-digit total be typed and clamps it only when leaving the box", async () => {
    const { onConfirm } = setup(card());
    await userEvent.clear(total());
    await userEvent.type(total(), "12");
    expect(total()).toHaveValue(12);
    await userEvent.tab();
    expect(total()).toHaveValue(12);
    expect(screen.getByText("9 people are not matched yet")).toBeInTheDocument();
    const answer = await confirm(onConfirm);
    expect(answer.headcount).toBe(12);
    expect(answer.others).toBe(9);
  });

  it("raises a typed total below the people on the card when leaving the box", async () => {
    setup(card());
    await userEvent.clear(total());
    await userEvent.type(total(), "1");
    expect(total()).toHaveValue(1);
    await userEvent.tab();
    expect(total()).toHaveValue(3);
  });

  it("commits a typed total on Enter", async () => {
    setup(card());
    await userEvent.clear(total());
    await userEvent.type(total(), "5{Enter}");
    expect(total()).toHaveValue(5);
    expect(screen.getByText("2 people are not matched yet")).toBeInTheDocument();
  });

  it("ignores a total that is not a number", async () => {
    setup(card());
    await userEvent.clear(total());
    await userEvent.type(total(), "abc");
    await userEvent.tab();
    expect(total()).toHaveValue(3);
  });

  it("confirms with the clamped total while the box is still being edited", async () => {
    const { onConfirm } = setup(card());
    await userEvent.clear(total());
    await userEvent.type(total(), "1");
    const answer = await confirm(onConfirm);
    expect(answer.headcount).toBe(3);
    expect(answer.others).toBe(0);
  });

  it("keeps the total within the unmatched limit when someone is unticked", async () => {
    const { onConfirm } = setup(card());
    await userEvent.clear(total());
    await userEvent.type(total(), "53");
    await userEvent.tab();
    expect(total()).toHaveValue(53);
    await userEvent.click(screen.getByRole("checkbox", { name: "Xiao Wang" }));
    expect(total()).toHaveValue(52);
    const answer = await confirm(onConfirm);
    expect(answer.headcount).toBe(52);
    expect(answer.others).toBe(50);
  });

  it("says person for a total of one", () => {
    setup(card({ rows: [card().rows[0]], guests: [], headcount: 1 }));
    expect(screen.getByText("person in all")).toBeInTheDocument();
  });

  it("sends the confirmed total with the answer", async () => {
    const { onConfirm } = setup(soloWithOthers());
    await userEvent.click(screen.getByRole("button", { name: "One more person" }));
    await userEvent.click(screen.getByRole("button", { name: "Not sure" }));
    const answer = await confirm(onConfirm);
    expect(answer.headcount).toBe(4);
    expect(answer.others).toBe(3);
    expect(answer.others_answer).toBe("unsure");
  });

  it("disables Confirm while nobody eats", async () => {
    const { onConfirm } = setup(card({ guests: [], headcount: 2 }));
    const button = screen.getByRole("button", { name: "Confirm and start planning" });
    await userEvent.click(screen.getByRole("checkbox", { name: "Me" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Xiao Wang" }));
    expect(button).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "One fewer person" }));
    await userEvent.click(screen.getByRole("button", { name: "One fewer person" }));
    expect(total()).toHaveValue(0);
    expect(button).toBeDisabled();
    await userEvent.click(button);
    expect(onConfirm).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Add a person" }));
    expect(total()).toHaveValue(1);
    expect(button).toBeEnabled();
  });

  it("keeps Confirm enabled when unmatched people still eat", async () => {
    setup(card({ guests: [], headcount: 2 }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Me" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Xiao Wang" }));
    expect(screen.getByText("2 people are not matched yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm and start planning" })).toBeEnabled();
  });

  it("shows the user's own diet word as the fixed hint on their row", () => {
    const me = { ...card().rows[0], rules: [], diet_words: ["吃素"] };
    setup(card({ rows: [me], guests: [], headcount: 1 }));
    const row = screen.getByTestId("diner-row-1");
    expect(within(row).getByText("吃素")).toBeInTheDocument();
    expect(within(row).getByText(/Rules by food category are not supported yet/)).toBeInTheDocument();
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

  it("sends the settled guest key back", async () => {
    const rule = proposed({ rule_type: "dislike", text: "no peanuts" });
    const { onConfirm } = setup(
      card({
        rows: [card().rows[0]],
        headcount: 2,
        guests: [{ key: "guest:1", name: "小李", proposed: [rule], diet_words: [] }],
      }),
    );
    const answer = await confirm(onConfirm);
    expect(answer.guests).toHaveLength(1);
    expect(answer.guests[0].key).toBe("guest:1");
    expect(answer.guests[0].rules).toEqual([expect.objectContaining({ text: "no peanuts", save: false })]);
  });

  it("does not send a key for a guest added on the card", async () => {
    const { onConfirm } = setup(card({ rows: [card().rows[0]], guests: [], headcount: 1 }));
    await userEvent.click(screen.getByRole("button", { name: "Add a person" }));
    const answer = await confirm(onConfirm);
    expect(answer.guests).toHaveLength(1);
    expect(answer.guests[0].key ?? null).toBeNull();
  });

  it("starts the unmatched answer from the card", () => {
    setup({ ...soloWithOthers(), others_answer: "unsure" });
    expect(screen.getByRole("button", { name: "Not sure" })).toHaveAttribute("aria-pressed", "true");
  });

  it("shows the stated-below notice and still lets the user confirm", async () => {
    const { onConfirm } = setup(card({ stated_total: 1 }));
    expect(screen.getByText("You said 1, and 3 are ticked. Untick anyone not eating.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm and start planning" })).toBeEnabled();
    await userEvent.click(screen.getByRole("checkbox", { name: "Me" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Xiao Wang" }));
    expect(screen.getByText("You said 1, and 1 are ticked. Untick anyone not eating.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "One fewer person" }));
    await userEvent.click(screen.getByRole("button", { name: "One fewer person" }));
    expect(screen.queryByText(/You said 1/)).not.toBeInTheDocument();
    const answer = await confirm(onConfirm);
    expect(answer.headcount).toBe(1);
  });

  it("follow-up card: unticking lowers the total", async () => {
    const { onConfirm } = setup(card({ others_answer: null }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Me" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Xiao Wang" }));
    expect(total()).toHaveValue(1);
    expect(screen.queryByText(/not matched yet/)).not.toBeInTheDocument();
    const answer = await confirm(onConfirm);
    expect(answer.others).toBe(0);
    expect(answer.headcount).toBe(1);
  });

  it("first-turn card: unticking keeps the total", async () => {
    setup(card());
    await userEvent.click(screen.getByRole("checkbox", { name: "Me" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Xiao Wang" }));
    expect(total()).toHaveValue(3);
    expect(screen.getByText("2 people are not matched yet")).toBeInTheDocument();
  });

  it("follow-up card: removing a guest lowers the total", async () => {
    const { onConfirm } = setup(card({ others_answer: null }));
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(total()).toHaveValue(2);
    const answer = await confirm(onConfirm);
    expect(answer.others).toBe(0);
    expect(answer.headcount).toBe(2);
  });

  const settledCard = () =>
    card({
      rows: [card().rows[0]],
      headcount: 2,
      guests: [{ key: "guest:1", name: "小李", proposed: [], diet_words: [] }],
      others_answer: null,
    });

  it("a removed settled guest is not sent", async () => {
    const { onConfirm } = setup(settledCard());
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));
    const answer = await confirm(onConfirm);
    expect(answer.guests).toEqual([]);
  });

  it("a settled guest key survives a draft remount", async () => {
    mockFetch({});
    let saved: DinerDraft | undefined;
    const first = renderWithProviders(
      <DinerCard pending={settledCard()} busy={false} onConfirm={vi.fn()} onDraft={(d) => (saved = d)} />,
    );
    await waitFor(() => expect(saved).toBeDefined());
    first.unmount();
    const onConfirm = vi.fn<(answer: DinerAnswer) => void>();
    renderWithProviders(<DinerCard pending={settledCard()} busy={false} onConfirm={onConfirm} draft={saved} />);
    const answer = await confirm(onConfirm);
    expect(answer.guests[0].key).toBe("guest:1");
  });

  it("follow-up card: untick then re-tick restores the total", async () => {
    setup(card({ headcount: 5, others_answer: null }));
    expect(screen.getByText("2 people are not matched yet")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: "Xiao Wang" }));
    expect(total()).toHaveValue(4);
    await userEvent.click(screen.getByRole("checkbox", { name: "Xiao Wang" }));
    expect(total()).toHaveValue(5);
    expect(screen.getByText("2 people are not matched yet")).toBeInTheDocument();
    expect(screen.queryByText(/The number of people is now/)).not.toBeInTheDocument();
  });

  it("follow-up card: adding the unmatched as guests keeps the total", async () => {
    setup(card({ rows: [card().rows[0]], guests: [], headcount: 4, others_answer: null }));
    expect(total()).toHaveValue(4);
    await userEvent.click(screen.getByRole("button", { name: "Yes, I'll fill them in" }));
    expect(total()).toHaveValue(4);
    expect(screen.getAllByLabelText("Name")).toHaveLength(3);
    expect(screen.queryByText(/not matched yet/)).not.toBeInTheDocument();
    expect(screen.queryByText(/The number of people is now/)).not.toBeInTheDocument();
  });

  it("first-turn card: adding the unmatched as guests keeps the total", async () => {
    setup(card({ rows: [card().rows[0]], guests: [], headcount: 4 }));
    await userEvent.click(screen.getByRole("button", { name: "Yes, I'll fill them in" }));
    expect(total()).toHaveValue(4);
    expect(screen.queryByText(/The number of people is now/)).not.toBeInTheDocument();
  });
});
