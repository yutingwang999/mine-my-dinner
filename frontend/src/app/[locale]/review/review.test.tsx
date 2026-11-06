import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ReviewQueue } from "@/components/ReviewQueue";
import { type Call, callsTo, draft, mockFetch, renderWithProviders, reply } from "@/test/helpers";

vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
  useRouter: () => ({ push: vi.fn() }),
}));

type Routes = Parameters<typeof mockFetch>[0];

const BASE_ROUTES: Routes = {
  "POST /api/recipes/allergy-check": () => ({ conflicts: [], unknown: ["秘制酱"] }),
  "POST /api/allergen-suggestions": () => ({ suggested: ["soy"] }),
  "POST /api/ingredients": () => ({ id: 50, name_zh: "秘制酱", name_en: null, aliases_zh: [], aliases_en: [], source: "user", category: "seasoning", stock_mode: "level", allergens: null, parent_id: null, linked_lines: 0 }),
  "POST /api/cookware": () => ({ id: 60, name_zh: "神奇锅", name_en: null, aliases_zh: [], aliases_en: [], source: "user" }),
  "POST /api/recipes": () => reply(201, { id: 1 }),
};

let calls: Call[];

function setup(routes: Routes = {}, drafts = [draft()]) {
  calls = mockFetch({ ...BASE_ROUTES, ...routes });
  renderWithProviders(<ReviewQueue />, drafts);
}

function row(name: string): HTMLElement {
  return screen.getByTestId(`row-${name}`);
}

function lastBody(path: string, method = "POST"): Record<string, unknown> {
  const matching = callsTo(calls, method, path);
  return matching[matching.length - 1].body as Record<string, unknown>;
}

async function save() {
  await userEvent.click(screen.getByRole("button", { name: "Save recipe" }));
  await waitFor(() => expect(callsTo(calls, "POST", "/api/recipes")).toHaveLength(1));
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("review", () => {
  it("marks an unmatched ingredient and offers link, create, and skip", () => {
    setup();
    const unmatched = within(row("秘制酱"));
    expect(unmatched.getByText("Not in the dictionary")).toBeInTheDocument();
    for (const action of ["Link to existing", "Create entry", "Skip"]) {
      expect(unmatched.getByRole("button", { name: action })).toBeInTheDocument();
    }
    expect(within(row("葱花")).queryByRole("button", { name: "Create entry" })).toBeNull();
  });

  it("keeps allergens unknown until the user confirms a suggestion", async () => {
    setup();
    await userEvent.click(within(row("秘制酱")).getByRole("button", { name: "Create entry" }));
    expect(await screen.findByText(/A model suggested these/)).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Soy" })).toBeChecked();
    expect(screen.getByText("Not confirmed: allergens stay unknown.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Create and link" }));
    await waitFor(() => expect(callsTo(calls, "POST", "/api/ingredients")).toHaveLength(1));
    expect(lastBody("/api/ingredients").allergens).toBeNull();
    expect(await within(row("秘制酱")).findByText(/Linked to/)).toBeInTheDocument();
  });

  it("sends confirmed allergens after the user confirms", async () => {
    setup();
    await userEvent.click(within(row("秘制酱")).getByRole("button", { name: "Create entry" }));
    await userEvent.click(await screen.findByRole("button", { name: "Confirm selected allergens" }));
    await userEvent.click(screen.getByRole("button", { name: "Create and link" }));
    await waitFor(() => expect(lastBody("/api/ingredients").allergens).toEqual(["soy"]));
  });

  it("does not treat an empty suggestion as confirmed none", async () => {
    setup({ "POST /api/allergen-suggestions": () => ({ suggested: [] }) });
    await userEvent.click(within(row("秘制酱")).getByRole("button", { name: "Create entry" }));
    expect(await screen.findByText(/The model suggested none/)).toBeInTheDocument();
    expect(screen.queryByText("Confirmed: contains none")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Confirm it contains none" }));
    expect(screen.getByText("Confirmed: contains none")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Create and link" }));
    await waitFor(() => expect(lastBody("/api/ingredients").allergens).toEqual([]));
  });

  it("still allows saving when suggestions are unavailable", async () => {
    setup({ "POST /api/allergen-suggestions": () => ({ suggested: null }) });
    await userEvent.click(within(row("秘制酱")).getByRole("button", { name: "Create entry" }));
    expect(await screen.findByText(/Suggestions are unavailable/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save recipe" })).toBeEnabled();
  });

  it("links an unmatched ingredient to an existing entry", async () => {
    setup({
      "GET /api/ingredients": () => [
        { id: 7, name_zh: "酱油", name_en: "soy sauce", aliases_zh: [], aliases_en: [], source: "seed", category: "seasoning", stock_mode: "level", allergens: ["soy"], parent_id: null },
      ],
    });
    await userEvent.click(within(row("秘制酱")).getByRole("button", { name: "Link to existing" }));
    await userEvent.type(screen.getByRole("searchbox", { name: "Search the dictionary" }), "酱");
    // The English UI shows the entry's English name.
    await userEvent.click(await screen.findByRole("button", { name: "Choose soy sauce" }));
    await save();
    const lines = lastBody("/api/recipes").ingredients as { ingredient_id: number | null }[];
    expect(lines[1].ingredient_id).toBe(7);
  });

  it("sets time_source to user when the time is changed", async () => {
    setup();
    const time = screen.getByRole("spinbutton", { name: "Time (minutes)" });
    await userEvent.clear(time);
    await userEvent.type(time, "20");
    await save();
    expect(lastBody("/api/recipes")).toMatchObject({ time_min: 20, time_source: "user" });
  });

  it("keeps time_source when the time is unchanged", async () => {
    setup();
    await save();
    expect(lastBody("/api/recipes")).toMatchObject({ time_min: 10, time_source: "stated" });
  });

  it("clears time_source when the time is removed", async () => {
    setup();
    await userEvent.clear(screen.getByRole("spinbutton", { name: "Time (minutes)" }));
    await save();
    expect(lastBody("/api/recipes")).toMatchObject({ time_min: null, time_source: null });
  });

  it("sends provenance back unchanged", async () => {
    setup();
    await save();
    expect(lastBody("/api/recipes").provenance).toEqual(draft().provenance);
  });

  it("asks whether the user owns newly created cookware", async () => {
    setup();
    await userEvent.click(within(row("神奇锅")).getByRole("button", { name: "Create entry" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "I own this cookware" }));
    await userEvent.click(screen.getByRole("button", { name: "Create and link" }));
    await waitFor(() => expect(lastBody("/api/cookware").owned).toBe(true));
    await within(row("神奇锅")).findByText(/Linked to/);
    await save();
    expect(lastBody("/api/recipes").cookware_ids).toEqual([3, 60]);
  });

  it("drops skipped cookware from the save request", async () => {
    setup();
    await userEvent.click(within(row("炒锅")).getByRole("button", { name: "Skip" }));
    await save();
    expect(lastBody("/api/recipes").cookware_ids).toEqual([]);
  });

  it("shows the allergen warning without blocking save", async () => {
    setup({
      "POST /api/recipes/allergy-check": () => ({
        conflicts: [{ line_name: "葱花", allergen: "soy", ingredient_id: null }],
        unknown: [],
      }),
    });
    expect(await screen.findByText(/Allergy warning/)).toBeInTheDocument();
    expect(screen.getByText("葱花 contains Soy, which you are allergic to.")).toBeInTheDocument();
    await save();
  });

  it("lists ingredients with unknown allergens", async () => {
    setup();
    expect(await screen.findByText("Allergens unknown for: 秘制酱")).toBeInTheDocument();
  });

  it("posts one recipe when save is double-clicked", async () => {
    let finish: (value: Response) => void = () => {};
    setup({ "POST /api/recipes": () => new Promise<Response>((resolve) => (finish = resolve)) });
    const button = screen.getByRole("button", { name: "Save recipe" });
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(callsTo(calls, "POST", "/api/recipes")).toHaveLength(1));
    await act(async () => finish(reply(201, { id: 1 })));
    expect(callsTo(calls, "POST", "/api/recipes")).toHaveLength(1);
  });

  it("keeps the edited form and shows an error when save fails", async () => {
    setup({ "POST /api/recipes": () => reply(503, { code: "models_unavailable" }) });
    const title = screen.getByRole("textbox", { name: "Title" });
    await userEvent.clear(title);
    await userEvent.type(title, "葱花炒蛋");
    await save();
    expect(await screen.findByText(/Saving failed: No model is available/)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("葱花炒蛋");
    expect(screen.getByTestId("queue")).toHaveTextContent("葱花蛋");
  });

  it("warns before unload while unsaved drafts exist", () => {
    setup();
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
  });

  it("moves to the next draft after saving in a batch", async () => {
    setup({}, [draft(), draft({ title: "土豆丝" })]);
    expect(screen.getByText("Draft 1 of 2")).toBeInTheDocument();
    await save();
    expect(await screen.findByDisplayValue("土豆丝")).toBeInTheDocument();
    expect(screen.getByText("Draft 1 of 1")).toBeInTheDocument();
  });

  it("does not warn before unload once every draft is saved", async () => {
    setup();
    await save();
    await screen.findByText("There are no drafts to review.");
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false);
  });

  it("clears amount and unit when the amount type becomes vague", async () => {
    setup({}, [
      draft({
        ingredients: [
          { raw: "两个鸡蛋", name: "鸡蛋", amount: 2, unit: "piece", amount_type: "count", role: "main", optional: false, ingredient_id: 1 },
        ],
      }),
    ]);
    await userEvent.selectOptions(
      within(row("鸡蛋")).getByRole("combobox", { name: "Amount type" }),
      "vague",
    );
    await save();
    const lines = lastBody("/api/recipes").ingredients as Record<string, unknown>[];
    expect(lines[0]).toMatchObject({ amount: null, unit: null, amount_type: "vague" });
  });
});
