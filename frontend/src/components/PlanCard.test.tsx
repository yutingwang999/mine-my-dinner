import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import zh from "../../messages/zh.json";

import { PlanCard } from "@/components/PlanCard";
import type { ChatPlan } from "@/lib/chat";
import type { Conflict } from "@/lib/planEdits";
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

  it("asks to scale down when fewer people eat than the recipe serves", () => {
    render(
      <NextIntlClientProvider locale="zh" messages={zh}>
        <PlanCard plan={plan({ headcount: 1, servings_notes: [{ recipe_id: 1, servings: 2, headcount: 1 }] })} />
      </NextIntlClientProvider>,
    );
    expect(screen.getByText(/的菜谱是 2 人份，这顿 1 人吃，食材记得按人数减量。/)).toBeInTheDocument();
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
    const box = screen.getByText(/recipe serves 2/).closest(".plan-info") as HTMLElement;
    expect(box.parentElement).toHaveClass("plan-notes");
  });

  it("puts the left-out dishes after the problem boxes", () => {
    const { container } = renderWithProviders(
      <PlanCard plan={plan({ ...noPlan, left_out_unknown_allergens: ["秘制酱鸡"] })} />,
    );
    const red = container.querySelector(".notice-warning:not(.allergen-notice)") as HTMLElement;
    const box = container.querySelector(".left-out-box") as HTMLElement;
    expect(red.compareDocumentPosition(box) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(box).getByRole("heading", { level: 3 })).toHaveTextContent(/left out/);
  });

  it("keeps the warnings and the left-out dishes in one notes block, in that order", () => {
    const { container } = renderWithProviders(
      <PlanCard
        plan={plan({
          headcount: 3,
          servings_notes: [{ recipe_id: 1, servings: 2, headcount: 3 }],
          left_out_unknown_allergens: ["秘制酱鸡"],
        })}
      />,
    );
    const blocks = container.querySelectorAll(".plan-notes");
    expect(blocks).toHaveLength(1);
    const [info, leftOut] = Array.from(blocks[0].children);
    expect(info).toHaveClass("plan-info");
    expect(leftOut).toHaveClass("left-out-box");
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

describe("PlanCard manual edits", () => {
  afterEach(() => vi.unstubAllGlobals());

  const BASE = "/api/chat/threads/t1/plan";
  const TWO = plan({
    recipes: [
      { id: 1, title: "葱花蛋", dish_type: "vegetable dish", time_min: 10, time_source: "stated", servings: 2 },
      { id: 2, title: "牛肉汤", dish_type: "soup", time_min: 30, time_source: "stated", servings: 2 },
    ],
  });
  const EDIT = { overrides: [], log: [], conflicts: [] };
  const edited = (fields: Partial<ChatPlan> = {}) =>
    plan({ edited: true, model_version: 0, edit: EDIT, count_notes: [], notes: [], ...fields });
  const beef: Conflict = {
    recipe_id: 2,
    diner_key: "diner:2",
    diner_name: "小王",
    is_self: false,
    kind: "ingredient",
    allergen: null,
    ingredient_id: 40,
    ingredient_name: "牛肉",
    text: null,
    rule_type: "dietary restriction",
    lines: ["牛肉"],
  };
  const unknownOil: Conflict = {
    ...beef,
    diner_key: "*",
    diner_name: null,
    kind: "unknown_allergens",
    ingredient_id: null,
    ingredient_name: null,
    rule_type: null,
    lines: ["花椒油"],
  };
  const withConflict = (conflicts: Conflict[]) =>
    edited({ recipes: TWO.recipes, passed: false, edit: { ...EDIT, conflicts } });

  function Editable({ initial, onRefused }: { initial: ChatPlan; onRefused?: () => void }) {
    const [current, setCurrent] = useState(initial);
    return <PlanCard plan={current} editable threadId="t1" onEdited={setCurrent} onEditRefused={onRefused} />;
  }

  it("removes a dish through the endpoint and shows the returned card", async () => {
    const calls = mockFetch({ [`POST ${BASE}/remove`]: () => ({ plan: edited() }) });
    renderWithProviders(<Editable initial={TWO} />);
    expect(screen.queryByText("Edited by hand")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Remove 牛肉汤" }));
    await waitFor(() => expect(callsTo(calls, "POST", `${BASE}/remove`)).toHaveLength(1));
    expect(callsTo(calls, "POST", `${BASE}/remove`)[0].body).toEqual({ recipe_id: 2, locale: "en" });
    expect(await screen.findByText("Edited by hand")).toBeInTheDocument();
    expect(screen.queryByText("牛肉汤")).toBeNull();
    expect(screen.getByText(/Sending another message plans again/)).toBeInTheDocument();
    // The removed dish's button is gone; focus goes to "+ Add a dish".
    expect(screen.getByRole("button", { name: "+ Add a dish" })).toHaveFocus();
  });

  it("reports when an edit request starts and ends", async () => {
    let resolve!: (value: Response) => void;
    mockFetch({ [`POST ${BASE}/remove`]: () => new Promise<Response>((r) => (resolve = r)) });
    const onEditing = vi.fn();
    renderWithProviders(
      <PlanCard plan={TWO} editable threadId="t1" onEdited={vi.fn()} onEditing={onEditing} />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Remove 牛肉汤" }));
    expect(onEditing).toHaveBeenLastCalledWith(true);
    resolve(reply(200, { plan: edited() }));
    await waitFor(() => expect(onEditing).toHaveBeenLastCalledWith(false));
  });

  it("drops an edit error once the card no longer has controls", async () => {
    mockFetch({ [`POST ${BASE}/remove`]: () => reply(409, { code: "thread_busy", suggest_paste: false }) });
    // The same card instance loses its controls (a newer reply, or the thread is no longer editable).
    function Lockable() {
      const [editable, setEditable] = useState(true);
      return (
        <>
          <button type="button" onClick={() => setEditable(false)}>
            lock
          </button>
          <PlanCard plan={TWO} editable={editable} threadId="t1" onEdited={vi.fn()} />
        </>
      );
    }
    renderWithProviders(<Lockable />);
    await userEvent.click(screen.getByRole("button", { name: "Remove 牛肉汤" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "lock" }));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("marks a dish with a new conflict in red with the reason, and keeps it or removes it with ×", async () => {
    const calls = mockFetch({
      [`POST ${BASE}/keep`]: () => ({ plan: edited({ recipes: TWO.recipes }) }),
      [`POST ${BASE}/remove`]: () => ({ plan: edited() }),
    });
    const { unmount } = renderWithProviders(<Editable initial={withConflict([beef])} />);
    const row = screen.getByText("牛肉汤").closest("li") as HTMLElement;
    expect(row).toHaveClass("plan-dish-conflict");
    expect(within(row).getByText("Changed since planning: 小王 avoids 牛肉; this dish contains 牛肉.")).toBeInTheDocument();
    expect(screen.getByText(/Some dishes have a new conflict/)).toBeInTheDocument();
    await userEvent.click(within(row).getByRole("button", { name: "Keep it" }));
    await waitFor(() => expect(callsTo(calls, "POST", `${BASE}/keep`)).toHaveLength(1));
    expect(callsTo(calls, "POST", `${BASE}/keep`)[0].body).toEqual({ recipe_id: 2, confirm: [beef], locale: "en" });
    await waitFor(() => expect(screen.queryByText(/Changed since planning/)).toBeNull());
    unmount();

    renderWithProviders(<Editable initial={withConflict([beef])} />);
    const again = screen.getByText("牛肉汤").closest("li") as HTMLElement;
    // Only "Keep it" sits under the conflict; removing goes through the named × button.
    expect(within(again).queryByRole("button", { name: "Remove" })).toBeNull();
    await userEvent.click(within(again).getByRole("button", { name: "Remove 牛肉汤" }));
    await waitFor(() => expect(callsTo(calls, "POST", `${BASE}/remove`)).toHaveLength(1));
    expect(callsTo(calls, "POST", `${BASE}/remove`)[0].body).toEqual({ recipe_id: 2, locale: "en" });
  });

  it("writes the reason sentences in Chinese", () => {
    render(
      <NextIntlClientProvider locale="zh" messages={zh}>
        <PlanCard plan={withConflict([beef, unknownOil])} editable threadId="t1" onEdited={vi.fn()} />
      </NextIntlClientProvider>,
    );
    expect(screen.getByText("规划之后有变化：小王 忌口 牛肉，这道菜含牛肉")).toBeInTheDocument();
    expect(screen.getByText("这道菜里的 花椒油 过敏原未知，而这顿有人过敏")).toBeInTheDocument();
    expect(screen.getByText("已手动修改")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "恢复模型方案" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认保留" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "删除" })).toBeNull();
    expect(screen.getByRole("button", { name: "删除牛肉汤" })).toBeInTheDocument();
    expect(screen.getByText("继续对话会重新规划，手动修改不会带入")).toBeInTheDocument();
  });

  it("names the user and a guest without a name in the reason", () => {
    renderWithProviders(
      <PlanCard
        plan={withConflict([
          {
            ...beef,
            diner_key: "self",
            diner_name: null,
            is_self: true,
            kind: "allergen",
            allergen: "milk",
            ingredient_id: null,
            ingredient_name: null,
            rule_type: "allergy",
            lines: ["牛奶"],
          },
          { ...beef, diner_key: "guest:1", diner_name: null },
        ])}
        editable
        threadId="t1"
        onEdited={vi.fn()}
      />,
    );
    expect(
      screen.getByText("Changed since planning: you are allergic to Milk; this dish contains 牛奶."),
    ).toBeInTheDocument();
    expect(screen.getByText("Changed since planning: a guest avoids 牛肉; this dish contains 牛肉.")).toBeInTheDocument();
  });

  it("offers restore only on an edited card, and restores", async () => {
    const restored = plan({ edited: false, model_version: 0, edit: EDIT, count_notes: [], notes: [] });
    const calls = mockFetch({ [`POST ${BASE}/restore`]: () => ({ plan: restored }) });
    const { unmount } = renderWithProviders(<Editable initial={TWO} />);
    expect(screen.queryByRole("button", { name: "Restore the model's plan" })).toBeNull();
    unmount();
    renderWithProviders(<Editable initial={edited()} />);
    await userEvent.click(screen.getByRole("button", { name: "Restore the model's plan" }));
    await waitFor(() => expect(callsTo(calls, "POST", `${BASE}/restore`)).toHaveLength(1));
    expect(callsTo(calls, "POST", `${BASE}/restore`)[0].body).toEqual({ locale: "en" });
    await waitFor(() => expect(screen.queryByText("Edited by hand")).toBeNull());
    expect(screen.queryByRole("button", { name: "Restore the model's plan" })).toBeNull();
    expect(screen.queryByText(/Sending another message plans again/)).toBeNull();
  });

  it("shows a count note only for the counts the card returns", () => {
    const { unmount } = render(
      <NextIntlClientProvider locale="zh" messages={zh}>
        <PlanCard
          plan={edited({ count_notes: [{ field: "dishes", stated: 3, now: 2 }] })}
          editable
          threadId="t1"
          onEdited={vi.fn()}
        />
      </NextIntlClientProvider>,
    );
    expect(screen.getByText("和你的要求不同：要求 3 道菜，现在 2 道")).toBeInTheDocument();
    unmount();
    renderWithProviders(
      <PlanCard plan={edited({ defaults_used: ["dishes"] })} editable threadId="t1" onEdited={vi.fn()} />,
    );
    expect(screen.queryByText(/Differs from what you asked/)).toBeNull();
  });

  it("shows the allowed hard-check warnings and the deleted-dish note", () => {
    renderWithProviders(
      <PlanCard
        plan={edited({
          warnings: [
            { code: "H1", recipe_id: 1, detail: { missing: ["葱"] }, text: "葱花蛋: 1 ingredient missing (葱)" },
          ],
          notes: [{ field: "deleted_recipes", titles: ["红烧肉"] }],
        })}
        editable
        threadId="t1"
        onEdited={vi.fn()}
      />,
    );
    expect(screen.getByText("葱花蛋: 1 ingredient missing (葱)")).toBeInTheDocument();
    expect(screen.getByText(/红烧肉: no longer in the recipe library/)).toBeInTheDocument();
  });

  it("shows no edit controls on an older card and disables them while busy", () => {
    const { unmount } = renderWithProviders(<PlanCard plan={edited({ recipes: TWO.recipes })} />);
    expect(screen.queryByRole("button", { name: /Remove/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "+ Add a dish" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Restore the model's plan" })).toBeNull();
    expect(screen.getByText("Edited by hand")).toBeInTheDocument();
    unmount();
    renderWithProviders(
      <PlanCard plan={edited({ recipes: TWO.recipes })} editable busy threadId="t1" onEdited={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: "Remove 牛肉汤" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "+ Add a dish" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Restore the model's plan" })).toBeDisabled();
  });

  it("shows add a dish on a reply with no plan", () => {
    renderWithProviders(
      <PlanCard
        plan={plan({
          recipes: [],
          passed: false,
          reasons: [{ reason: "not_enough", category: "dishes", needed: 2, available: 0, text: "No dish can be made." }],
          next_actions: ["import_recipes"],
        })}
        editable
        threadId="t1"
        onEdited={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "+ Add a dish" })).toBeEnabled();
    expect(screen.getByText("No dish can be made.")).toBeInTheDocument();
  });

  it("renders an old plan without edit fields as before", () => {
    renderWithProviders(<PlanCard plan={TWO} editable threadId="t1" onEdited={vi.fn()} />);
    expect(screen.queryByText("Edited by hand")).toBeNull();
    expect(screen.queryByRole("button", { name: "Restore the model's plan" })).toBeNull();
    expect(screen.queryByText(/Sending another message plans again/)).toBeNull();
    expect(screen.queryByText(/Differs from what you asked/)).toBeNull();
    expect(screen.queryByText(/Changed since planning/)).toBeNull();
    expect(screen.getByRole("button", { name: "Remove 葱花蛋" })).toBeEnabled();
  });

  it("shows a refused edit and tells the page the thread is busy", async () => {
    mockFetch({ [`POST ${BASE}/remove`]: () => reply(409, { code: "thread_busy", suggest_paste: false }) });
    const onRefused = vi.fn();
    renderWithProviders(<Editable initial={TWO} onRefused={onRefused} />);
    await userEvent.click(screen.getByRole("button", { name: "Remove 牛肉汤" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/still running or stopped with an error/);
    expect(onRefused).toHaveBeenCalledTimes(1);
    expect(screen.getByText("牛肉汤")).toBeInTheDocument();
  });

  it("shows each confirmed override under its dish and counts the dishes on the diners line", () => {
    const pork: Conflict = { ...beef, ingredient_name: "五花肉", lines: ["五花肉"] };
    render(
      <NextIntlClientProvider locale="zh" messages={zh}>
        <PlanCard
          plan={edited({
            recipes: TWO.recipes,
            edit: { ...EDIT, kept: [pork, unknownOil] },
            diners: [
              { name: null, is_self: true, guest: false },
              { name: "小王", is_self: false, guest: false },
            ],
          })}
          editable
          threadId="t1"
          onEdited={vi.fn()}
        />
      </NextIntlClientProvider>,
    );
    const row = screen.getByText("牛肉汤").closest("li") as HTMLElement;
    expect(within(row).getByText("已确认保留：小王 忌口 五花肉")).toBeInTheDocument();
    expect(within(row).getByText("已确认保留：花椒油 过敏原未知")).toBeInTheDocument();
    expect(row).not.toHaveClass("plan-dish-conflict");
    expect(screen.getByText("按 我、小王 排除（1 道菜已确认保留）")).toBeInTheDocument();
  });

  it("states kept overrides in English and leaves the diners line alone without them", () => {
    const { unmount } = renderWithProviders(
      <PlanCard
        plan={edited({
          recipes: TWO.recipes,
          edit: { ...EDIT, kept: [beef] },
          diners: [{ name: "小王", is_self: false, guest: false }],
        })}
      />,
    );
    expect(screen.getByText("Kept anyway: 小王 avoids 牛肉")).toBeInTheDocument();
    expect(screen.getByText("Checked against: 小王 (1 dish kept anyway)")).toBeInTheDocument();
    unmount();
    renderWithProviders(<PlanCard plan={edited({ diners: [{ name: "小王", is_self: false, guest: false }] })} />);
    expect(screen.getByText("Checked against: 小王")).toBeInTheDocument();
  });

  it("puts add a dish below the reasons on a reply with no plan", () => {
    renderWithProviders(
      <PlanCard
        plan={plan({
          recipes: [],
          passed: false,
          reasons: [{ reason: "not_enough", category: "dishes", needed: 2, available: 0, text: "No dish can be made." }],
        })}
        editable
        threadId="t1"
        onEdited={vi.fn()}
      />,
    );
    const reasons = screen.getByText("No dish can be made.");
    const add = screen.getByRole("button", { name: "+ Add a dish" });
    expect(reasons.compareDocumentPosition(add) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("puts add a dish right after the dishes, above the notes", () => {
    const { container } = renderWithProviders(
      <PlanCard
        plan={plan({ headcount: 3, servings_notes: [{ recipe_id: 1, servings: 2, headcount: 3 }] })}
        editable
        threadId="t1"
        onEdited={vi.fn()}
      />,
    );
    const list = container.querySelector(".card-list") as HTMLElement;
    const add = screen.getByRole("button", { name: "+ Add a dish" }).closest(".plan-add") as HTMLElement;
    const notes = container.querySelector(".plan-notes") as HTMLElement;
    expect(list.nextElementSibling).toBe(add);
    expect(add.compareDocumentPosition(notes) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("asks again when keeping a dish whose conflicts changed", async () => {
    const other: Conflict = { ...beef, diner_key: "diner:3", diner_name: "小李" };
    const calls = mockFetch({
      [`POST ${BASE}/keep`]: (body) =>
        (body as { confirm: Conflict[] }).confirm.length === 2
          ? { plan: edited({ recipes: TWO.recipes }) }
          : reply(409, { code: "needs_confirmation", suggest_paste: false, conflicts: [beef, other] }),
    });
    renderWithProviders(<Editable initial={withConflict([beef])} />);
    await userEvent.click(screen.getByRole("button", { name: "Keep it" }));
    const dialog = await screen.findByRole("group", { name: "This dish conflicts with" });
    expect(within(dialog).getByText("小李 avoids 牛肉")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Keep anyway" }));
    await waitFor(() => expect(callsTo(calls, "POST", `${BASE}/keep`)).toHaveLength(2));
    expect(callsTo(calls, "POST", `${BASE}/keep`)[1].body).toEqual({
      recipe_id: 2,
      confirm: [beef, other],
      locale: "en",
    });
  });

  it("offers the allergen panel for a dish red for unknown allergens, then keeps it again", async () => {
    const ENTRY = {
      id: 9, name_zh: "花椒油", name_en: null, aliases_zh: [], aliases_en: [], user_aliases_zh: [],
      user_aliases_en: [], source: "user", category: "seasoning", stock_mode: "level", allergens: null,
      parent_id: null,
    };
    let resolved = false;
    const calls = mockFetch({
      [`POST ${BASE}/keep`]: (body) =>
        resolved && (body as { confirm: Conflict[] }).confirm.length === 0
          ? { plan: edited({ recipes: TWO.recipes }) }
          : reply(409, { code: "needs_confirmation", suggest_paste: false, conflicts: [unknownOil] }),
      "POST /api/ingredients/match": () => ({ matches: [{ name: "花椒油", entry: ENTRY }] }),
      "POST /api/allergen-suggestions": () => ({ suggested: [], category: null, stock_mode: null }),
      "POST /api/allergens/resolve": () => {
        resolved = true;
        return { ...ENTRY, allergens: [] };
      },
    });
    renderWithProviders(<Editable initial={withConflict([{ ...unknownOil, recipe_id: 2 }])} />);
    await userEvent.click(screen.getByRole("button", { name: "Keep it" }));
    const dialog = await screen.findByRole("group", { name: "This dish conflicts with" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirm allergens" }));
    await userEvent.click(await within(dialog).findByRole("button", { name: "Confirm it contains none" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Save allergens" }));
    await waitFor(() => expect(callsTo(calls, "POST", `${BASE}/keep`)).toHaveLength(2));
    expect(callsTo(calls, "POST", `${BASE}/keep`)[1].body).toEqual({ recipe_id: 2, confirm: [], locale: "en" });
    await waitFor(() => expect(screen.queryByRole("group", { name: "This dish conflicts with" })).toBeNull());
  });
});
