import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import zh from "../../messages/zh.json";

import { PlanCard } from "@/components/PlanCard";
import type { ChatPlan } from "@/lib/chat";
import type { Conflict, SearchHit } from "@/lib/planEdits";
import { plan } from "@/test/chatFixtures";
import { callsTo, mockFetch, renderWithProviders, reply } from "@/test/helpers";

const BASE = "/api/chat/threads/t1/plan";

/** The newest card of thread t1, keeping the card each edit returns. */
function Editable({ initial, onRefused }: { initial: ChatPlan; onRefused?: () => void }) {
  const [current, setCurrent] = useState(initial);
  return <PlanCard plan={current} editable threadId="t1" onEdited={setCurrent} onEditRefused={onRefused} />;
}

function conflict(fields: Partial<Conflict> = {}): Conflict {
  return {
    recipe_id: 7,
    diner_key: "diner:2",
    diner_name: "小王",
    is_self: false,
    kind: "ingredient",
    allergen: null,
    ingredient_id: 30,
    ingredient_name: "豆腐",
    text: null,
    rule_type: "dietary restriction",
    lines: ["豆腐"],
    ...fields,
  };
}

function hit(fields: Partial<SearchHit> = {}): SearchHit {
  return {
    id: 7,
    title: "小白菜豆腐汤",
    dish_type: "soup",
    time_min: 15,
    missing: ["小白菜"],
    missing_cookware: [],
    conflicts: [],
    blocked_unknown: [],
    dislikes: [],
    in_plan: false,
    ...fields,
  };
}

const ADDED = plan({
  recipes: [
    { id: 1, title: "葱花蛋", dish_type: "vegetable dish", time_min: 10, time_source: "stated", servings: 2 },
    { id: 7, title: "小白菜豆腐汤", dish_type: "soup", time_min: 15, time_source: "stated", servings: 2 },
  ],
  edited: true,
  model_version: 0,
  edit: { overrides: [], log: [], conflicts: [] },
  count_notes: [],
  notes: [],
});

async function searchFor(query: string) {
  await userEvent.click(screen.getByRole("button", { name: "+ Add a dish" }));
  await userEvent.type(screen.getByRole("searchbox", { name: "Search by dish name or ingredient" }), query);
  await userEvent.click(screen.getByRole("button", { name: "Search" }));
}

describe("PlanEditor search", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("lists results with dish type, missing items, conflicts, and dishes already in the plan", async () => {
    const calls = mockFetch({
      [`POST ${BASE}/search`]: () => ({
        hits: [
          hit({ id: 1, title: "葱花蛋", dish_type: "vegetable dish", missing: [], in_plan: true }),
          hit({
            missing_cookware: ["砂锅"],
            conflicts: [conflict()],
            dislikes: [{ diner_key: "self", diner_name: null, is_self: true, line: "香菜" }],
          }),
        ],
        semantic_paused: false,
      }),
    });
    renderWithProviders(<Editable initial={plan()} />);
    await searchFor("小白菜");
    const results = await screen.findByRole("list", { name: "Search results" });
    expect(callsTo(calls, "POST", `${BASE}/search`)[0].body).toEqual({ query: "小白菜", locale: "en" });
    const [inPlan, soup] = within(results).getAllByRole("listitem");
    expect(within(inPlan).getByText("Already in the plan")).toBeInTheDocument();
    expect(within(inPlan).queryByRole("button", { name: "Add" })).toBeNull();
    expect(within(soup).getByText(/Soup · 15 min/)).toBeInTheDocument();
    expect(within(soup).getByText("Missing: 小白菜")).toBeInTheDocument();
    expect(within(soup).getByText("Missing cookware: 砂锅")).toBeInTheDocument();
    expect(within(soup).getByText("小王 avoids 豆腐")).toBeInTheDocument();
    expect(within(soup).getByText("you dislike 香菜")).toBeInTheDocument();
    expect(within(soup).getByRole("button", { name: "Add" })).toBeEnabled();
  });

  it("says when nothing was found and when search by meaning is paused", async () => {
    mockFetch({ [`POST ${BASE}/search`]: () => ({ hits: [], semantic_paused: true }) });
    renderWithProviders(<Editable initial={plan()} />);
    await searchFor("xyz");
    expect(await screen.findByText("No dishes found.")).toBeInTheDocument();
    expect(screen.getByText(/Search by meaning is paused/)).toBeInTheDocument();
  });

  it("adds a dish without a conflict and shows the returned card", async () => {
    const calls = mockFetch({
      [`POST ${BASE}/search`]: () => ({ hits: [hit()], semantic_paused: false }),
      [`POST ${BASE}/add`]: () => ({ plan: ADDED }),
    });
    renderWithProviders(<Editable initial={plan()} />);
    await searchFor("小白菜");
    await userEvent.click(await screen.findByRole("button", { name: "Add" }));
    await waitFor(() => expect(callsTo(calls, "POST", `${BASE}/add`)).toHaveLength(1));
    expect(callsTo(calls, "POST", `${BASE}/add`)[0].body).toEqual({ recipe_id: 7, confirm: [], locale: "en" });
    expect(await screen.findByText("Edited by hand")).toBeInTheDocument();
    expect(screen.getAllByText("小白菜豆腐汤").length).toBeGreaterThan(0);
  });

  it("asks before adding a conflicting dish and sends the confirmed conflicts", async () => {
    const asked = [conflict(), conflict({ diner_key: "self", diner_name: null, is_self: true, kind: "allergen", allergen: "soy", ingredient_id: null, ingredient_name: null, rule_type: "allergy" })];
    const calls = mockFetch({
      [`POST ${BASE}/search`]: () => ({ hits: [hit({ conflicts: asked })], semantic_paused: false }),
      [`POST ${BASE}/add`]: (body) =>
        (body as { confirm: unknown[] }).confirm.length === 0
          ? reply(409, { code: "needs_confirmation", suggest_paste: false, conflicts: asked })
          : { plan: ADDED },
    });
    renderWithProviders(<Editable initial={plan()} />);
    await searchFor("豆腐");
    await userEvent.click(await screen.findByRole("button", { name: "Add" }));
    const dialog = await screen.findByRole("group", { name: "This dish conflicts with" });
    expect(within(dialog).getByText("小王 avoids 豆腐")).toBeInTheDocument();
    expect(within(dialog).getByText("you are allergic to Soy")).toBeInTheDocument();
    // Focus moves into the confirmation, on its first button.
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus());
    expect(screen.queryByRole("alert")).toBeNull();
    await userEvent.click(within(dialog).getByRole("button", { name: "Add anyway" }));
    await waitFor(() => expect(callsTo(calls, "POST", `${BASE}/add`)).toHaveLength(2));
    expect(callsTo(calls, "POST", `${BASE}/add`)[1].body).toEqual({ recipe_id: 7, confirm: asked, locale: "en" });
    expect(await screen.findByText("Edited by hand")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "This dish conflicts with" })).toBeNull();
  });

  it("closes the confirmation on cancel without adding", async () => {
    const calls = mockFetch({
      [`POST ${BASE}/search`]: () => ({ hits: [hit({ conflicts: [conflict()] })], semantic_paused: false }),
      [`POST ${BASE}/add`]: () =>
        reply(409, { code: "needs_confirmation", suggest_paste: false, conflicts: [conflict()] }),
    });
    renderWithProviders(<Editable initial={plan()} />);
    await searchFor("豆腐");
    await userEvent.click(await screen.findByRole("button", { name: "Add" }));
    const dialog = await screen.findByRole("group", { name: "This dish conflicts with" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("group", { name: "This dish conflicts with" })).toBeNull();
    expect(callsTo(calls, "POST", `${BASE}/add`)).toHaveLength(1);
  });

  it("offers the allergen panel for an unknown-allergen ingredient, then asks again", async () => {
    const unknown = conflict({ diner_key: "*", diner_name: null, kind: "unknown_allergens", ingredient_id: null, ingredient_name: null, rule_type: null, lines: ["花椒油"] });
    const ENTRY = {
      id: 9, name_zh: "花椒油", name_en: null, aliases_zh: [], aliases_en: [], user_aliases_zh: [],
      user_aliases_en: [], source: "user", category: "seasoning", stock_mode: "level", allergens: null,
      parent_id: null,
    };
    let resolved = false;
    const calls = mockFetch({
      [`POST ${BASE}/search`]: () => ({ hits: [hit({ conflicts: [unknown], blocked_unknown: ["花椒油"] })], semantic_paused: false }),
      [`POST ${BASE}/add`]: () =>
        resolved ? { plan: ADDED } : reply(409, { code: "needs_confirmation", suggest_paste: false, conflicts: [unknown] }),
      "POST /api/ingredients/match": () => ({ matches: [{ name: "花椒油", entry: ENTRY }] }),
      "POST /api/allergen-suggestions": () => ({ suggested: [], category: null, stock_mode: null }),
      "POST /api/allergens/resolve": () => {
        resolved = true;
        return { ...ENTRY, allergens: [] };
      },
    });
    renderWithProviders(<Editable initial={plan()} />);
    await searchFor("花椒");
    expect(await screen.findByText("花椒油: allergens unknown")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    const dialog = await screen.findByRole("group", { name: "This dish conflicts with" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirm allergens" }));
    await userEvent.click(await within(dialog).findByRole("button", { name: "Confirm it contains none" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Save allergens" }));
    await waitFor(() => expect(callsTo(calls, "POST", `${BASE}/add`)).toHaveLength(2));
    expect(callsTo(calls, "POST", `${BASE}/add`)[1].body).toEqual({ recipe_id: 7, confirm: [], locale: "en" });
    expect(await screen.findByText("Edited by hand")).toBeInTheDocument();
  });

  it("names a guest without a name as a guest, in Chinese too", async () => {
    mockFetch({
      [`POST ${BASE}/search`]: () => ({
        hits: [hit({ conflicts: [conflict({ diner_key: "guest:1", diner_name: null }), conflict({ kind: "allergen", allergen: "peanut", ingredient_id: null, ingredient_name: null, rule_type: "allergy" })] })],
        semantic_paused: false,
      }),
    });
    render(
      <NextIntlClientProvider locale="zh" messages={zh}>
        <Editable initial={plan()} />
      </NextIntlClientProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "+ 加一道菜" }));
    await userEvent.type(screen.getByRole("searchbox", { name: "搜索菜名或食材" }), "豆腐");
    await userEvent.click(screen.getByRole("button", { name: "搜索" }));
    expect(await screen.findByText("一位客人：忌口 豆腐")).toBeInTheDocument();
    expect(screen.getByText("小王：过敏 花生")).toBeInTheDocument();
    expect(screen.getByText("缺：小白菜")).toBeInTheDocument();
  });
});
