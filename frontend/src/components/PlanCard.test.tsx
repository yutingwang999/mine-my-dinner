import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, describe, expect, it, vi } from "vitest";

import zh from "../../messages/zh.json";

import { PlanCard } from "@/components/PlanCard";
import { plan, spec } from "@/test/chatFixtures";
import { callsTo, mockFetch, renderWithProviders, reply, savedRecipe } from "@/test/helpers";

describe("PlanCard", () => {
  it("always shows the allergen notice with no way to close it", () => {
    renderWithProviders(<PlanCard plan={plan()} />);
    expect(screen.getByText(/check the actual ingredients for allergens/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /close|dismiss/i })).toBeNull();
    expect(screen.getByText("葱花蛋")).toBeInTheDocument();
    expect(screen.getByText(/Vegetable dish/)).toBeInTheDocument();
    expect(screen.getByText(/10 min \(stated in the source\)/)).toBeInTheDocument();
  });

  it("shows the allergen notice even for a plan with no dishes", () => {
    renderWithProviders(
      <PlanCard
        plan={plan({
          recipes: [],
          passed: false,
          unmet: [{ code: "H2", recipe_id: null, text: "Every plan found had a dish that breaks a diner's allergies" }],
          next_actions: ["fewer_dishes"],
        })}
      />,
    );
    expect(screen.getByText(/check the actual ingredients for allergens/)).toBeInTheDocument();
    expect(screen.getByText(/Every plan found had a dish/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Fewer dishes" })).toBeInTheDocument();
  });

  it("states defaulted values", () => {
    renderWithProviders(<PlanCard plan={plan({ defaults_used: ["headcount", "dishes", "mode"] })} />);
    expect(
      screen.getByText("Assumed: 1 person, 1 dish, fridge only. Say if that's wrong."),
    ).toBeInTheDocument();
  });

  it("states only the defaulted dish count or soup count", () => {
    const twoAndSoup = spec({ dishes: 2, soups: 1 });
    const { unmount } = renderWithProviders(
      <PlanCard plan={plan({ defaults_used: ["soups"], spec: twoAndSoup })} />,
    );
    expect(screen.getByText("Assumed: 1 soup. Say if that's wrong.")).toBeInTheDocument();
    unmount();
    renderWithProviders(<PlanCard plan={plan({ defaults_used: ["dishes"], spec: twoAndSoup })} />);
    expect(screen.getByText("Assumed: 2 dishes. Say if that's wrong.")).toBeInTheDocument();
  });

  it("treats an old composition value as both counts", () => {
    renderWithProviders(
      <PlanCard
        plan={plan({
          defaults_used: ["composition"],
          spec: spec({ dishes: 2, soups: 1 }),
        })}
      />,
    );
    expect(screen.getByText("Assumed: 2 dishes, 1 soup. Say if that's wrong.")).toBeInTheDocument();
  });

  it("states only the defaulted values", () => {
    renderWithProviders(
      <PlanCard
        plan={plan({
          defaults_used: ["mode"],
          headcount: 3,
          spec: spec({ dishes: 2, soups: 1 }),
        })}
      />,
    );
    expect(screen.getByText("Assumed: fridge only. Say if that's wrong.")).toBeInTheDocument();
  });

  it("lists unmet rules with next-action buttons", async () => {
    const onAction = vi.fn();
    renderWithProviders(
      <PlanCard
        onAction={onAction}
        plan={plan({
          passed: false,
          unmet: [{ code: "H1", recipe_id: 1, text: "葱花蛋: 1 ingredient missing (葱)" }],
          next_actions: ["switch_to_shopping", "fewer_dishes", "drop_time_limit"],
        })}
      />,
    );
    expect(screen.getByText("葱花蛋: 1 ingredient missing (葱)")).toBeInTheDocument();
    expect(within(screen.getByRole("group")).getAllByRole("button").map((b) => b.textContent)).toEqual([
      "Allow shopping",
      "Fewer dishes",
      "No time limit",
    ]);
    await userEvent.click(screen.getByRole("button", { name: "Allow shopping" }));
    expect(onAction).toHaveBeenCalledWith("switch_to_shopping");
  });

  it("shows missing items, warnings, and servings notes in plain words", () => {
    renderWithProviders(
      <PlanCard
        plan={plan({
          missing: { "1": ["葱", "盐"] },
          missing_cookware: { "1": ["炒锅"] },
          warnings: [
            { code: "S7", recipe_id: 1, detail: { kind: "estimated", time_min: 15 } },
            { code: "S8", recipe_id: null, detail: { unconfirmed_diners: 2 } },
          ],
          headcount: 3,
          servings_notes: [{ recipe_id: 1, servings: 2, headcount: 3 }],
        })}
      />,
    );
    expect(screen.getByText("Missing ingredients: 葱, 盐")).toBeInTheDocument();
    expect(screen.getByText("Missing cookware: 炒锅")).toBeInTheDocument();
    expect(screen.getByText("葱花蛋: the cooking time (15 min) is only an estimate.")).toBeInTheDocument();
    expect(screen.getByText(/2 other diners have unknown dietary limits/)).toBeInTheDocument();
    expect(screen.getByText("The 葱花蛋 recipe serves 2; you're cooking for 3 people, so scale up the ingredients.")).toBeInTheDocument();
  });

  it("localizes known units in the stock warning and falls back to the raw unit", () => {
    renderWithProviders(
      <PlanCard
        plan={plan({
          warnings: [
            { code: "S1", recipe_id: null, detail: { name: "oil", needed: 2, have: 1, unit: "tablespoon", parts: [{ recipe_id: 1, amount: 2 }] } },
            { code: "S1", recipe_id: null, detail: { name: "rice", needed: 2, have: 1, unit: "bowl", parts: [{ recipe_id: 1, amount: 2 }] } },
          ],
        })}
      />,
    );
    expect(screen.getByText("oil: 葱花蛋 2, 2 tbsp needed, 1 tbsp in stock.")).toBeInTheDocument();
    expect(screen.getByText("rice: 葱花蛋 2, 2 bowl needed, 1 bowl in stock.")).toBeInTheDocument();
  });

  it("counts dishes left out for unknown allergens and lists their titles on request", async () => {
    renderWithProviders(<PlanCard plan={plan({ left_out_unknown_allergens: ["秘制酱鸡", "怪味面"] })} />);
    expect(screen.getByText(/2 dishes were left out because an ingredient has an unknown allergen/)).toBeInTheDocument();
    await userEvent.click(screen.getByText("Show which dishes"));
    const list = screen.getByRole("list", { name: "Dishes left out" });
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["秘制酱鸡", "怪味面"]);
  });

  it("shows the semantic-search notice when paused", () => {
    renderWithProviders(<PlanCard plan={plan({ semantic_paused: true })} />);
    expect(screen.getByText(/Search by meaning is paused/)).toBeInTheDocument();
  });

  it("omits the semantic-search notice when search works", () => {
    renderWithProviders(<PlanCard plan={plan()} />);
    expect(screen.queryByText(/Search by meaning is paused/)).toBeNull();
  });
});

describe("PlanCard what was asked, reasons, and links", () => {
  it("renders link actions as links with the locale", () => {
    const { unmount } = renderWithProviders(
      <PlanCard
        plan={plan({
          passed: false,
          recipes: [],
          next_actions: ["update_inventory", "register_cookware", "import_recipes", "relax_split"],
        })}
      />,
    );
    expect(screen.getByRole("link", { name: "Update your stock" })).toHaveAttribute("href", "/en/inventory");
    expect(screen.getByRole("link", { name: "Register your cookware" })).toHaveAttribute(
      "href",
      "/en/inventory?tab=cookware",
    );
    expect(screen.getByRole("link", { name: "Import more recipes" })).toHaveAttribute("href", "/en/import");
    expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(["Any mix is fine"]);
    unmount();
    render(
      <NextIntlClientProvider locale="zh" messages={zh}>
        <PlanCard plan={plan({ next_actions: ["register_cookware"] })} />
      </NextIntlClientProvider>,
    );
    expect(screen.getByRole("link", { name: "登记厨具" })).toHaveAttribute("href", "/zh/inventory?tab=cookware");
  });

  it("lists named ingredients by kind under what was asked", () => {
    renderWithProviders(
      <PlanCard plan={plan({ defaults_used: ["mode"], spec: spec({ prefer_use: ["鸡蛋"], must_use: ["番茄"], all_use: ["葱"] }) })} />,
    );
    expect(
      screen.getByText("As you asked: 1 dish, Prefer using: 鸡蛋, Must use: 番茄, Use in every dish: 葱."),
    ).toBeInTheDocument();
  });

  it("shows reasons with needed and available counts", () => {
    renderWithProviders(
      <PlanCard
        plan={plan({
          recipes: [],
          passed: false,
          reasons: [
            { reason: "not_enough_dishes", category: "meat", needed: 2, available: 1, text: "Need 2 meat dishes, only 1 can be made." },
            { reason: "no_cookware", category: "soup", needed: null, available: null, text: "No cookware registered." },
            { reason: "no_cookware", category: "meat", needed: null, available: null, text: "No cookware registered." },
          ],
        })}
      />,
    );
    expect(screen.getByText("Need 2 meat dishes, only 1 can be made.")).toBeInTheDocument();
    expect(screen.getAllByText("No cookware registered.")).toHaveLength(1);
    expect(screen.getByText(/check the actual ingredients for allergens/)).toBeInTheDocument();
  });

  it("states what was asked and only then what was assumed", () => {
    const { unmount } = renderWithProviders(
      <PlanCard
        plan={plan({
          defaults_used: [],
          spec: spec({ mode: "shopping", dishes: 3, soups: 1, staples: 1, time_limit_min: 30 }),
        })}
      />,
    );
    expect(
      screen.getByText("As you asked: 3 dishes, 1 soup, 1 staple, shopping allowed, within 30 min."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Assumed/)).toBeNull();
    unmount();
    renderWithProviders(<PlanCard plan={plan({ defaults_used: ["headcount", "mode"], spec: spec({ dishes: 2 }) })} />);
    expect(screen.getByText("As you asked: 2 dishes.")).toBeInTheDocument();
    expect(screen.getByText("Assumed: 1 person, fridge only. Say if that's wrong.")).toBeInTheDocument();
  });

  it("shows 'at least' for a minimum split", () => {
    const { unmount } = renderWithProviders(
      <PlanCard
        plan={plan({
          defaults_used: ["mode"],
          spec: spec({
            dishes: 4,
            meat_dishes: 1,
            vegetable_dishes: 1,
            meat_bound: "at_least",
            vegetable_bound: "at_least",
          }),
        })}
      />,
    );
    expect(
      screen.getByText("As you asked: 4 dishes (at least 1 meat dish, at least 1 vegetable dish)."),
    ).toBeInTheDocument();
    unmount();
    renderWithProviders(
      <PlanCard
        plan={plan({
          defaults_used: ["mode"],
          spec: spec({ dishes: 2, meat_dishes: 1, vegetable_dishes: 1, meat_bound: "exact", vegetable_bound: "exact" }),
        })}
      />,
    );
    expect(screen.getByText("As you asked: 2 dishes (1 meat dish, 1 vegetable dish).")).toBeInTheDocument();
  });

  it("shows the user's own bounds in both languages", () => {
    const atLeast = plan({
      defaults_used: ["mode"],
      spec: spec({ dishes: 8, meat_dishes: 2, meat_bound: "at_least" }),
    });
    const both = plan({
      defaults_used: ["mode"],
      spec: spec({
        dishes: 4,
        meat_dishes: 1,
        meat_bound: "at_least",
        vegetable_dishes: 2,
        vegetable_bound: "at_most",
      }),
    });
    const atMost = plan({
      defaults_used: ["mode"],
      spec: spec({ dishes: 3, vegetable_dishes: 1, vegetable_bound: "at_most" }),
    });
    const none = plan({
      defaults_used: ["mode"],
      spec: spec({ dishes: 3, vegetable_dishes: 0, vegetable_bound: "exact" }),
    });
    for (const [card, text] of [
      [atLeast, "As you asked: 8 dishes (at least 2 meat dishes)."],
      [both, "As you asked: 4 dishes (at least 1 meat dish, at most 2 vegetable dishes)."],
      [atMost, "As you asked: 3 dishes (at most 1 vegetable dish)."],
      [none, "As you asked: 3 dishes (0 vegetable dishes)."],
    ] as const) {
      const { unmount } = renderWithProviders(<PlanCard plan={card} />);
      expect(screen.getByText(text)).toBeInTheDocument();
      unmount();
    }
    for (const [card, text] of [
      [atLeast, "按你的要求：8 道菜（至少 2 道荤菜）。"],
      [both, "按你的要求：4 道菜（至少 1 道荤菜、最多 2 道素菜）。"],
      [atMost, "按你的要求：3 道菜（最多 1 道素菜）。"],
    ] as const) {
      const { unmount } = render(
        <NextIntlClientProvider locale="zh" messages={zh}>
          <PlanCard plan={card} />
        </NextIntlClientProvider>,
      );
      expect(screen.getByText(text)).toBeInTheDocument();
      unmount();
    }
  });

  it("puts asked and assumed lines in their own block after the dish list, with the notice inside the card", () => {
    const { container } = renderWithProviders(
      <PlanCard plan={plan({ defaults_used: ["headcount"], spec: spec({ dishes: 2 }) })} />,
    );
    const card = container.querySelector(".plan-card") as HTMLElement;
    const list = card.querySelector("ul.card-list") as HTMLElement;
    const conditions = card.querySelector(".plan-conditions") as HTMLElement;
    expect(list.compareDocumentPosition(conditions) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(conditions).toContainElement(screen.getByText(/As you asked/));
    expect(conditions).toContainElement(screen.getByText(/Assumed:/));
    expect(list).not.toContainElement(screen.getByText(/As you asked/));
    const notice = within(card).getByText(/check the actual ingredients for allergens/);
    expect(notice).toHaveAttribute("role", "note");
  });

  it("joins the asked and assumed lines with a Chinese comma in Chinese", () => {
    render(
      <NextIntlClientProvider locale="zh" messages={zh}>
        <PlanCard
          plan={plan({
            defaults_used: ["headcount", "soups"],
            spec: spec({ mode: "fridge_only", dishes: 3, soups: 1, time_limit_min: 20 }),
          })}
        />
      </NextIntlClientProvider>,
    );
    expect(screen.getByText("按你的要求：3 道菜，只用冰箱现有食材，20 分钟内。")).toBeInTheDocument();
    expect(screen.getByText(/我假设了：1 人，1 道汤。/)).toBeInTheDocument();
  });

  it("shows a named side when the dish total was assumed", () => {
    renderWithProviders(
      <PlanCard
        plan={plan({
          defaults_used: ["dishes"],
          headcount: 3,
          spec: spec({ mode: "shopping", dishes: 2, vegetable_dishes: 1, vegetable_bound: "at_most" }),
        })}
      />,
    );
    expect(screen.getByText("As you asked: at most 1 vegetable dish, shopping allowed.")).toBeInTheDocument();
    expect(screen.getByText(/Assumed: 2 dishes/)).toBeInTheDocument();
  });

  it("renders the S1 warning with each dish's part", () => {
    renderWithProviders(
      <PlanCard
        plan={plan({
          recipes: [
            { id: 1, title: "番茄炒蛋", dish_type: "vegetable dish", time_min: 10, time_source: "stated", servings: 2 },
            { id: 2, title: "蛋花汤", dish_type: "soup", time_min: 10, time_source: "stated", servings: 2 },
          ],
          warnings: [
            {
              code: "S1",
              recipe_id: null,
              detail: {
                ingredient_id: 7,
                name: "鸡蛋",
                needed: 4,
                have: 3,
                unit: "piece",
                parts: [
                  { recipe_id: 1, amount: 2 },
                  { recipe_id: 2, amount: 2 },
                ],
              },
            },
          ],
        })}
      />,
    );
    expect(screen.getByText("鸡蛋: 番茄炒蛋 2 + 蛋花汤 2, 4 piece needed, 3 piece in stock.")).toBeInTheDocument();
  });
});

describe("PlanCard layout order", () => {
  const noPlan = {
    recipes: [],
    passed: false,
    reasons: [{ reason: "no_cookware", category: null, needed: null, available: null, text: "No cookware registered." }],
    next_actions: ["fewer_dishes"] as never[],
  };

  it("drops the no-dishes line for a card with reasons and puts the actions inside the reasons box", () => {
    renderWithProviders(<PlanCard plan={plan(noPlan)} />);
    expect(screen.queryByText(/No dishes/i)).toBeNull();
    const box = screen.getByText("No cookware registered.").closest(".notice") as HTMLElement;
    expect(box).toHaveClass("notice-warning");
    expect(box).toContainElement(screen.getByRole("group"));
  });

  it("keeps the no-dishes line when a card has no dishes, reasons, or unmet rules", () => {
    renderWithProviders(<PlanCard plan={plan({ recipes: [], passed: false })} />);
    expect(screen.getByText(/No dishes|no plan|could not/i)).toBeInTheDocument();
  });

  it("puts the actions inside the last red box, after the unmet list", () => {
    renderWithProviders(
      <PlanCard
        plan={plan({
          ...noPlan,
          unmet: [{ code: "H2", recipe_id: null, text: "Every plan found broke a rule" }],
        })}
      />,
    );
    const reasons = screen.getByText("No cookware registered.").closest(".notice") as HTMLElement;
    const unmet = screen.getByText("Every plan found broke a rule").closest(".notice") as HTMLElement;
    expect(reasons).not.toContainElement(screen.getByRole("group"));
    expect(unmet).toContainElement(screen.getByRole("group"));
  });

  it("puts asked, assumed, and the diners line in the footer before the allergen notice", () => {
    const { container } = renderWithProviders(
      <PlanCard
        plan={plan({
          defaults_used: ["headcount"],
          spec: spec({ dishes: 2 }),
          diners: [
            { name: null, is_self: true, guest: false },
            { name: null, is_self: false, guest: true },
          ],
          others: 0,
        })}
      />,
    );
    const footer = container.querySelector(".plan-footer") as HTMLElement;
    const asked = screen.getByText(/As you asked/);
    const assumed = screen.getByText(/Assumed:/);
    const diners = screen.getByText(/Checked against/);
    const notice = screen.getByText(/check the actual ingredients for allergens/);
    const order = [asked, assumed, diners, notice];
    for (const node of order) expect(footer).toContainElement(node);
    for (let i = 0; i < order.length - 1; i += 1) {
      expect(order[i].compareDocumentPosition(order[i + 1]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
  });

  it("renders the servings note inside the info box", () => {
    renderWithProviders(
      <PlanCard plan={plan({ headcount: 3, servings_notes: [{ recipe_id: 1, servings: 2, headcount: 3 }] })} />,
    );
    const box = screen.getByText(/recipe serves 2/).closest(".notice") as HTMLElement;
    expect(box).toHaveClass("notice-info");
  });

  it("puts the left-out dishes in their own box after the problem boxes", () => {
    const { container } = renderWithProviders(
      <PlanCard plan={plan({ ...noPlan, left_out_unknown_allergens: ["秘制酱鸡"] })} />,
    );
    const red = container.querySelector(".notice-warning:not(.allergen-notice)") as HTMLElement;
    const box = container.querySelector(".left-out-box") as HTMLElement;
    expect(red.compareDocumentPosition(box) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(box).getByRole("heading", { level: 3 })).toHaveTextContent(/left out/);
  });
});

describe("PlanCard unknown actions", () => {
  it("skips next actions the UI does not know", () => {
    renderWithProviders(
      <PlanCard plan={plan({ next_actions: ["fewer_dishes", "brand_new" as never] })} />,
    );
    expect(within(screen.getByRole("group")).getAllByRole("button").map((b) => b.textContent)).toEqual(["Fewer dishes"]);
  });

  it("disables every action when it is not actionable", () => {
    renderWithProviders(
      <PlanCard
        actionable={false}
        plan={plan({ passed: false, next_actions: ["fewer_dishes", "update_inventory"] })}
        onAction={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Fewer dishes" })).toBeDisabled();
    expect(screen.queryByRole("link")).toBeNull();
  });
});

describe("PlanCard recipe detail", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("opens each panel from its own toggle, switches, closes, and fetches once", async () => {
    const calls = mockFetch({ "GET /api/recipes/1": () => savedRecipe() });
    renderWithProviders(<PlanCard plan={plan()} actionable={false} />);
    const cook = screen.getByRole("button", { name: "How to cook" });
    const original = screen.getByRole("button", { name: "Original text" });
    expect(cook).toHaveAttribute("aria-expanded", "false");
    expect(original).toHaveAttribute("aria-expanded", "false");
    expect(cook).toHaveTextContent("How to cook ▾");
    expect(original).toHaveTextContent("Original text ▾");

    await userEvent.click(cook);
    expect(await screen.findByText("鸡蛋炒熟后撒葱花。")).toBeInTheDocument();
    expect(cook).toHaveAttribute("aria-expanded", "true");
    expect(cook).toHaveTextContent("How to cook ▴");
    expect(original).toHaveTextContent("Original text ▾");
    expect(screen.queryByText(/Servings/)).toBeNull();
    expect(screen.queryByText("原文：番茄两个")).toBeNull();

    await userEvent.click(original);
    expect(screen.getByText("原文：番茄两个")).toBeInTheDocument();
    expect(screen.queryByText("鸡蛋炒熟后撒葱花。")).toBeNull();
    expect(cook).toHaveAttribute("aria-expanded", "false");
    expect(original).toHaveAttribute("aria-expanded", "true");

    await userEvent.click(original);
    expect(screen.queryByText("原文：番茄两个")).toBeNull();
    expect(original).toHaveAttribute("aria-expanded", "false");
    expect(calls.filter((call) => call.path === "/api/recipes/1")).toHaveLength(1);
  });

  it("opens the original text first and says when none was saved", async () => {
    mockFetch({ "GET /api/recipes/1": () => savedRecipe({ source_text: null, source_url: null }) });
    renderWithProviders(<PlanCard plan={plan()} />);
    await userEvent.click(screen.getByRole("button", { name: "Original text" }));
    expect(await screen.findByText("No original text was saved.")).toBeInTheDocument();
  });

  it("marks the dish's missing ingredients in the detail", async () => {
    mockFetch({ "GET /api/recipes/1": () => savedRecipe() });
    renderWithProviders(<PlanCard plan={plan({ missing: { "1": ["番茄"] } })} />);
    await userEvent.click(screen.getByRole("button", { name: "How to cook" }));
    expect(await screen.findByText(/番茄.*300 g.*\(missing\)/)).toBeInTheDocument();
  });

  it("shows a notice when the fetch fails and retries", async () => {
    let fail = true;
    mockFetch({
      "GET /api/recipes/1": () => (fail ? reply(500, { code: "unknown" }) : savedRecipe()),
    });
    renderWithProviders(<PlanCard plan={plan()} />);
    await userEvent.click(screen.getByRole("button", { name: "How to cook" }));
    expect(await screen.findByText(/The recipe could not be loaded/)).toBeInTheDocument();
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("鸡蛋炒熟后撒葱花。")).toBeInTheDocument();
  });
});

describe("PlanCard diners and left-out dishes", () => {
  afterEach(() => vi.unstubAllGlobals());

  const DETAILS = [
    { id: 11, title: "宫保鸡丁", lines: ["花椒油"] },
    { id: 12, title: "麻辣香锅", lines: ["花椒油", "秘制酱"] },
  ];
  const leftOut = {
    left_out_unknown_allergens: ["宫保鸡丁", "麻辣香锅"],
    left_out_details: DETAILS,
  };
  const ENTRY = {
    id: 9, name_zh: "花椒油", name_en: null, aliases_zh: [], aliases_en: [], user_aliases_zh: [],
    user_aliases_en: [], source: "user", category: "seasoning", stock_mode: "level", allergens: null,
    parent_id: null,
  };

  function zhCard(card: ReturnType<typeof plan>, onReplan = vi.fn()) {
    return render(
      <NextIntlClientProvider locale="zh" messages={zh}>
        <PlanCard plan={card} onReplan={onReplan} />
      </NextIntlClientProvider>,
    );
  }

  it("groups the left-out dishes by ingredient name, each with a confirm button", () => {
    zhCard(plan(leftOut));
    const list = screen.getByRole("list", { name: "被排除的菜" });
    expect(within(list).getAllByRole("listitem").map((li) => li.querySelector("span")?.textContent)).toEqual([
      "宫保鸡丁、麻辣香锅：花椒油（过敏原未知）",
      "麻辣香锅：秘制酱（过敏原未知）",
    ]);
    expect(within(list).getAllByRole("button", { name: "确认过敏原" })).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "重新规划" })).toBeNull();
  });

  it("confirms an entry with unknown allergens, then offers plan again", async () => {
    const calls = mockFetch({
      "POST /api/ingredients/match": () => ({ matches: [{ name: "花椒油", entry: ENTRY }] }),
      "POST /api/allergen-suggestions": () => ({ suggested: [], category: null, stock_mode: null }),
      "POST /api/allergens/resolve": () => ENTRY,
    });
    const onReplan = vi.fn();
    renderWithProviders(<PlanCard plan={plan(leftOut)} onReplan={onReplan} />);
    const row = within(screen.getByTestId("left-out-花椒油"));
    await userEvent.click(row.getByRole("button", { name: "Confirm allergens" }));
    await userEvent.click(await row.findByRole("button", { name: "Confirm it contains none" }));
    await userEvent.click(row.getByRole("button", { name: "Save allergens" }));
    await waitFor(() => expect(callsTo(calls, "POST", "/api/allergens/resolve")).toHaveLength(1));
    expect(callsTo(calls, "POST", "/api/allergens/resolve")[0].body).toEqual({ ingredient_id: 9, allergens: [] });
    expect(callsTo(calls, "POST", "/api/ingredients/match")[0].body).toEqual({ names: ["花椒油"] });
    expect(await row.findByText("Confirmed")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Plan again" }));
    expect(onReplan).toHaveBeenCalledTimes(1);
  });

  it("opens the link panel for a name not in the dictionary, and can create an entry instead", async () => {
    const calls = mockFetch({
      "POST /api/ingredients/match": () => ({ matches: [] }),
      "POST /api/allergen-suggestions": () => ({ suggested: ["soy"], category: null, stock_mode: null }),
      "POST /api/allergens/resolve": () => ENTRY,
    });
    renderWithProviders(<PlanCard plan={plan(leftOut)} onReplan={vi.fn()} />);
    const row = within(screen.getByTestId("left-out-秘制酱"));
    await userEvent.click(row.getByRole("button", { name: "Confirm allergens" }));
    expect(await row.findByRole("checkbox", { name: "Remember this link" })).toBeChecked();
    await userEvent.click(row.getByRole("button", { name: "Not listed? Create a new entry" }));
    await userEvent.click(await row.findByRole("button", { name: "Confirm selected allergens" }));
    await userEvent.click(row.getByRole("button", { name: "Save allergens" }));
    await waitFor(() => expect(callsTo(calls, "POST", "/api/allergens/resolve")).toHaveLength(1));
    expect(callsTo(calls, "POST", "/api/allergens/resolve")[0].body).toMatchObject({
      name: "秘制酱",
      allergens: ["soy"],
    });
    expect(await screen.findByRole("button", { name: "Plan again" })).toBeInTheDocument();
  });

  const KNOWN_TEXT =
    "The dictionary knows this name, but this dish's ingredient line is not linked to it, so the dish stays left out. Link the line to an entry below.";
  const BEEF = {
    id: 20, name_zh: "牛肉末", name_en: "minced beef", aliases_zh: [], aliases_en: [], user_aliases_zh: [],
    user_aliases_en: [], source: "seed", category: "meat", stock_mode: "level", allergens: [], parent_id: null,
  };

  async function linkTo(row: ReturnType<typeof within>) {
    await userEvent.type(row.getByRole("searchbox", { name: "Search the dictionary" }), "牛肉");
    await userEvent.click(await row.findByRole("button", { name: "Choose minced beef" }));
    await userEvent.click(row.getByRole("button", { name: "Confirm" }));
  }

  it("does not offer plan again when the name's allergens are known but the line is not linked", async () => {
    const calls = mockFetch({
      "POST /api/ingredients/match": () => ({ matches: [{ name: "花椒油", entry: { ...ENTRY, allergens: [] } }] }),
      "GET /api/ingredients": () => [BEEF],
      "POST /api/allergens/link": () => ({ ...BEEF, linked_lines: 2, remembered: true }),
    });
    renderWithProviders(<PlanCard plan={plan(leftOut)} onReplan={vi.fn()} />);
    const row = within(screen.getByTestId("left-out-花椒油"));
    await userEvent.click(row.getByRole("button", { name: "Confirm allergens" }));
    expect(await row.findByText(KNOWN_TEXT)).toBeInTheDocument();
    expect(row.queryByRole("button", { name: "Save allergens" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Plan again" })).toBeNull();
    // Linking the line fixes it, so planning again is offered.
    await linkTo(row);
    await waitFor(() => expect(callsTo(calls, "POST", "/api/allergens/link")).toHaveLength(1));
    expect(await row.findByText("Confirmed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Plan again" })).toBeInTheDocument();
  });

  it("goes on to confirm allergens when the linked entry's allergens are unknown", async () => {
    const calls = mockFetch({
      "POST /api/ingredients/match": () => ({ matches: [] }),
      "GET /api/ingredients": () => [BEEF],
      "POST /api/allergens/link": () => ({ ...BEEF, allergens: null, linked_lines: 1, remembered: true }),
      "POST /api/allergen-suggestions": () => ({ suggested: [], category: null, stock_mode: null }),
      "POST /api/allergens/resolve": () => BEEF,
    });
    renderWithProviders(<PlanCard plan={plan(leftOut)} onReplan={vi.fn()} />);
    const row = within(screen.getByTestId("left-out-秘制酱"));
    await userEvent.click(row.getByRole("button", { name: "Confirm allergens" }));
    await linkTo(row);
    await userEvent.click(await row.findByRole("button", { name: "Confirm it contains none" }));
    expect(row.queryByText("Confirmed")).toBeNull();
    expect(screen.queryByRole("button", { name: "Plan again" })).toBeNull();
    await userEvent.click(row.getByRole("button", { name: "Save allergens" }));
    await waitFor(() => expect(callsTo(calls, "POST", "/api/allergens/resolve")).toHaveLength(1));
    expect(callsTo(calls, "POST", "/api/allergens/resolve")[0].body).toEqual({ ingredient_id: 20, allergens: [] });
    expect(await screen.findByRole("button", { name: "Plan again" })).toBeInTheDocument();
  });

  it("says so when linking found no line to link", async () => {
    mockFetch({
      "POST /api/ingredients/match": () => ({ matches: [] }),
      "GET /api/ingredients": () => [BEEF],
      "POST /api/allergens/link": () => ({ ...BEEF, linked_lines: 0, remembered: true }),
    });
    renderWithProviders(<PlanCard plan={plan(leftOut)} onReplan={vi.fn()} />);
    const row = within(screen.getByTestId("left-out-秘制酱"));
    await userEvent.click(row.getByRole("button", { name: "Confirm allergens" }));
    await linkTo(row);
    expect(await row.findByText(/No unlinked line with this name was found/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Plan again" })).toBeNull();
  });

  it("states the unknown limits without names when no diner is listed", () => {
    const { unmount } = renderWithProviders(<PlanCard plan={plan({ diners: [], others: 2 })} />);
    expect(screen.getByText("2 people have unknown limits")).toBeInTheDocument();
    unmount();
    zhCard(plan({ diners: [], others: 2 }));
    expect(screen.getByText("另外 2 人的忌口未知")).toBeInTheDocument();
  });

  it("shows an old plan's left-out titles without a confirm button", async () => {
    renderWithProviders(<PlanCard plan={plan({ left_out_unknown_allergens: ["秘制酱鸡"] })} onReplan={vi.fn()} />);
    await userEvent.click(screen.getByText("Show which dishes"));
    expect(screen.getByText("秘制酱鸡")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm allergens" })).toBeNull();
  });

  it("lists the left-out dishes on an early-stop reply with no dishes", () => {
    renderWithProviders(
      <PlanCard
        plan={plan({
          recipes: [],
          passed: false,
          reasons: [{ reason: "not_enough_dishes", category: null, needed: 2, available: 0, text: "Need 2 dishes, none can be made." }],
          ...leftOut,
        })}
        onReplan={vi.fn()}
      />,
    );
    expect(screen.getByText("宫保鸡丁, 麻辣香锅: 花椒油 (allergens unknown)")).toBeInTheDocument();
  });

  it("reads the diners line with the user, friends, and guests", () => {
    const { unmount } = zhCard(
      plan({
        diners: [
          { name: null, is_self: true, guest: false },
          { name: "小王", is_self: false, guest: false },
          { name: "Alex", is_self: false, guest: true },
        ],
        others: 0,
      }),
    );
    expect(screen.getByText("按 我、小王、Alex 排除")).toBeInTheDocument();
    unmount();
    renderWithProviders(
      <PlanCard
        plan={plan({
          diners: [
            { name: null, is_self: true, guest: false },
            { name: null, is_self: false, guest: true },
          ],
          others: 2,
        })}
      />,
    );
    expect(
      screen.getByText("Checked against: you, a guest; 2 more people have unknown limits"),
    ).toBeInTheDocument();
  });

  it("says how many dishes share one type, in one list with the servings notes", () => {
    zhCard(
      plan({
        warnings: [{ code: "S5", recipe_id: null, detail: { dish_type: "vegetable dish", count: 2 } }],
        servings_notes: [{ recipe_id: 1, servings: 1, headcount: 4 }],
      }),
    );
    const box = screen.getByRole("heading", { name: "需要注意" }).parentElement as HTMLElement;
    const items = within(box).getAllByRole("listitem").map((item) => item.textContent);
    expect(items[0]).toBe("2 道菜都是素菜。");
    expect(items[1]).toMatch(/的菜谱是 1 人份，这顿 4 人吃/);
  });

  it("counts guests without a name together on the diners line", () => {
    zhCard(
      plan({
        diners: [
          { name: null, is_self: true, guest: false },
          { name: "Alex", is_self: false, guest: true },
          { name: null, is_self: false, guest: true },
          { name: null, is_self: false, guest: true },
        ],
        others: 0,
      }),
    );
    expect(screen.getByText("按 我、Alex、2 位客人 排除")).toBeInTheDocument();
  });

  it("shows no diners line for a solo plan", () => {
    const { unmount } = renderWithProviders(<PlanCard plan={plan()} />);
    expect(screen.queryByText(/Checked against/)).toBeNull();
    unmount();
    renderWithProviders(<PlanCard plan={plan({ diners: [{ name: null, is_self: true, guest: false }], others: 0 })} />);
    expect(screen.queryByText(/Checked against/)).toBeNull();
  });
});
