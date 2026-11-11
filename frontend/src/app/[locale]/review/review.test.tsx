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
  it("shows the linked dictionary entry's own name, not the line's wording", () => {
    setup();
    expect(within(row("葱花")).getByText("Linked to scallion")).toBeInTheDocument();
  });

  it("shows the pasted source text beside the form", () => {
    setup({}, [draft({ provenance: { ...draft().provenance, input_text: "葱花蛋：鸡蛋两个，少量葱花" } })]);
    const source = screen.getByRole("region", { name: "Source text" });
    expect(within(source).getByText("葱花蛋：鸡蛋两个，少量葱花")).toBeInTheDocument();
  });

  it("says a screenshot import keeps its images with the recipe", () => {
    setup({}, [draft({ source_type: "screenshot", provenance: { ...draft().provenance, input_text: "[screenshot]" } })]);
    const source = screen.getByRole("region", { name: "Source text" });
    expect(within(source).getByText(/screenshots are saved with the recipe/)).toBeInTheDocument();
    expect(within(source).queryByText("[screenshot]")).toBeNull();
  });

  it("uploads a screenshot draft's images after saving the recipe", async () => {
    const image = new File(["x"], "a.png", { type: "image/png" });
    setup(
      {
        "POST /api/recipes": () => reply(201, { id: 1, embedded: true, log_id: 7 }),
        "POST /api/recipes/1/images": () => reply(201, []),
      },
      [draft({ source_type: "screenshot", images: [image] })],
    );
    await save();
    await waitFor(() => expect(callsTo(calls, "POST", "/api/recipes/1/images")).toHaveLength(1));
    const body = callsTo(calls, "POST", "/api/recipes/1/images")[0].body as FormData;
    expect(body.getAll("images")).toHaveLength(1);
    expect(body.get("log_id")).toBe("7");
    await screen.findByText("There are no drafts to review.");
  });

  it("keeps the saved recipe and offers a retry when the images fail to upload", async () => {
    const image = new File(["x"], "a.png", { type: "image/png" });
    let attempts = 0;
    setup(
      {
        "POST /api/recipes": () => reply(201, { id: 1, embedded: true, log_id: null }),
        "POST /api/recipes/1/images": () =>
          ++attempts === 1 ? reply(503, { code: "unknown" }) : reply(201, []),
      },
      [draft({ source_type: "screenshot", images: [image] })],
    );
    await save();
    expect(await screen.findByText(/The recipe is saved, but its screenshots are not/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save recipe" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Retry the screenshots" }));
    await screen.findByText("There are no drafts to review.");
    expect(callsTo(calls, "POST", "/api/recipes")).toHaveLength(1);
  });

  it("can continue without the screenshots after a failed upload", async () => {
    const image = new File(["x"], "a.png", { type: "image/png" });
    setup(
      {
        "POST /api/recipes": () => reply(201, { id: 1, embedded: true, log_id: null }),
        "POST /api/recipes/1/images": () => reply(503, { code: "unknown" }),
      },
      [draft({ source_type: "screenshot", images: [image] })],
    );
    await save();
    await userEvent.click(await screen.findByRole("button", { name: "Continue without them" }));
    await screen.findByText("There are no drafts to review.");
  });

  it("marks an unmatched ingredient and offers link, create, and skip", () => {
    setup();
    const unmatched = within(row("秘制酱"));
    expect(unmatched.getByText("Not in the dictionary")).toBeInTheDocument();
    for (const action of ["Link to existing", "Create entry", "Leave unlinked"]) {
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

  it("lets the user say a corrected time is stated in the source", async () => {
    setup();
    const time = screen.getByRole("spinbutton", { name: "Time (minutes)" });
    await userEvent.clear(time);
    await userEvent.type(time, "25");
    const source = screen.getByRole("combobox", { name: "Time source" });
    expect(source).toHaveValue("user");
    await userEvent.selectOptions(source, "stated");
    await save();
    expect(lastBody("/api/recipes")).toMatchObject({ time_min: 25, time_source: "stated" });
  });

  it("lets the user correct the source of an unchanged estimated time", async () => {
    setup({}, [draft({ time_min: 20, time_source: "estimated" })]);
    const source = screen.getByRole("combobox", { name: "Time source" });
    expect(source).toHaveValue("estimated");
    await userEvent.selectOptions(source, "stated");
    await save();
    expect(lastBody("/api/recipes")).toMatchObject({ time_min: 20, time_source: "stated" });
  });

  it("offers estimated only while the model's time is unchanged", async () => {
    setup({}, [draft({ time_min: 20, time_source: "estimated" })]);
    const options = () =>
      within(screen.getByRole("combobox", { name: "Time source" }))
        .getAllByRole("option")
        .map((option) => (option as HTMLOptionElement).value);
    expect(options()).toEqual(["stated", "user", "estimated"]);
    await userEvent.type(screen.getByRole("spinbutton", { name: "Time (minutes)" }), "5");
    expect(options()).toEqual(["stated", "user"]);
  });

  it("shows no time source choice without a time", () => {
    setup({}, [draft({ time_min: null, time_source: null })]);
    expect(screen.queryByRole("combobox", { name: "Time source" })).toBeNull();
  });

  it("undoes a change of link", async () => {
    setup();
    const linked = within(row("葱花"));
    await userEvent.click(linked.getByRole("button", { name: "Change link" }));
    expect(linked.getByText("Not in the dictionary")).toBeInTheDocument();
    await userEvent.click(linked.getByRole("button", { name: "Undo change" }));
    expect(linked.getByText("Linked to scallion")).toBeInTheDocument();
    await save();
    const lines = lastBody("/api/recipes").ingredients as { ingredient_id: number | null }[];
    expect(lines[0].ingredient_id).toBe(1);
  });

  it("stops offering undo once the line is linked elsewhere or left unlinked", async () => {
    setup();
    const linked = within(row("葱花"));
    await userEvent.click(linked.getByRole("button", { name: "Change link" }));
    await userEvent.click(linked.getByRole("button", { name: "Leave unlinked" }));
    expect(linked.queryByRole("button", { name: "Undo change" })).toBeNull();
  });

  it("explains what leaving an ingredient unlinked means", async () => {
    setup();
    await userEvent.click(within(row("秘制酱")).getByRole("button", { name: "Leave unlinked" }));
    expect(within(row("秘制酱")).getByText(/its allergens count as unknown/)).toBeInTheDocument();
  });

  it("explains what marking cookware not needed means", async () => {
    setup();
    await userEvent.click(within(row("炒锅")).getByRole("button", { name: "Not needed" }));
    expect(within(row("炒锅")).getByText(/planning does not check/)).toBeInTheDocument();
  });

  it("gives the step summary room for several lines", () => {
    setup();
    expect(screen.getByRole("textbox", { name: "Step summary" })).toHaveAttribute("rows", "4");
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

  it("adds an ingredient from the dictionary, already linked and named in the recipe's language", async () => {
    setup({
      "GET /api/ingredients": () => [
        { id: 12, name_zh: "土豆", name_en: "potato", aliases_zh: [], aliases_en: [], source: "seed", category: "vegetable", stock_mode: "count", allergens: [], parent_id: null },
      ],
    });
    await userEvent.click(screen.getByRole("button", { name: "Add ingredient" }));
    await userEvent.type(screen.getByRole("searchbox", { name: "Search the dictionary" }), "potato");
    await userEvent.click(await screen.findByRole("button", { name: "Choose potato" }));
    expect(within(row("土豆")).getByText("Linked to potato")).toBeInTheDocument();
    await save();
    const lines = lastBody("/api/recipes").ingredients as Record<string, unknown>[];
    expect(lines[2]).toMatchObject({ raw: "土豆", name: "土豆", ingredient_id: 12 });
  });

  it("keeps the typed wording when the chosen entry has another name", async () => {
    setup({
      "GET /api/ingredients": () => [
        { id: 12, name_zh: "土豆", name_en: "potato", aliases_zh: [], aliases_en: [], source: "seed", category: "vegetable", stock_mode: "count", allergens: [], parent_id: null },
      ],
    });
    await userEvent.click(screen.getByRole("button", { name: "Add ingredient" }));
    await userEvent.type(screen.getByRole("searchbox", { name: "Search the dictionary" }), "土豆丝");
    await userEvent.click(await screen.findByRole("button", { name: "Choose potato" }));
    expect(within(row("土豆丝")).getByText("Linked to potato")).toBeInTheDocument();
    await save();
    const lines = lastBody("/api/recipes").ingredients as Record<string, unknown>[];
    expect(lines[2]).toMatchObject({ raw: "土豆丝", name: "土豆丝", ingredient_id: 12 });
  });

  it.each([
    ["an alias in the recipe's language", "土豆丝", "土豆丝"],
    ["the entry's name in the recipe's language", "土豆", "土豆"],
    ["the entry's name in the other language", "potato", "土豆"],
    ["part of the entry's name, typed while searching", "土", "土豆"],
    ["other wording", "马铃薯丁", "马铃薯丁"],
  ])("names an added line from %s", async (_case, typed, expected) => {
    setup({
      "GET /api/ingredients": () => [
        { id: 12, name_zh: "土豆", name_en: "potato", aliases_zh: ["马铃薯", "土豆丝"], aliases_en: ["potatoes"], source: "seed", category: "vegetable", stock_mode: "count", allergens: [], parent_id: null },
      ],
    });
    await userEvent.click(screen.getByRole("button", { name: "Add ingredient" }));
    await userEvent.type(screen.getByRole("searchbox", { name: "Search the dictionary" }), typed);
    await userEvent.click(await screen.findByRole("button", { name: "Choose potato" }));
    await save();
    const lines = lastBody("/api/recipes").ingredients as Record<string, unknown>[];
    expect(lines[2]).toMatchObject({ name: expected, raw: expected, ingredient_id: 12 });
  });

  it("marks an added line and keeps its raw equal to its name", async () => {
    setup({
      "GET /api/ingredients": () => [
        { id: 12, name_zh: "土豆", name_en: "potato", aliases_zh: [], aliases_en: [], source: "seed", category: "vegetable", stock_mode: "count", allergens: [], parent_id: null },
      ],
    });
    await userEvent.click(screen.getByRole("button", { name: "Add ingredient" }));
    await userEvent.type(screen.getByRole("searchbox", { name: "Search the dictionary" }), "potato");
    await userEvent.click(await screen.findByRole("button", { name: "Choose potato" }));
    const added = within(row("土豆"));
    expect(added.getByText(/added by you/)).toBeInTheDocument();
    const name = added.getByRole("textbox", { name: "Name" });
    await userEvent.clear(name);
    await userEvent.type(name, "土豆块");
    await save();
    const lines = lastBody("/api/recipes").ingredients as Record<string, unknown>[];
    expect(lines[2]).toMatchObject({ raw: "土豆块", name: "土豆块" });
  });

  it("warns when a linked line is renamed to another dictionary entry, and relinks on request", async () => {
    setup({
      "GET /api/ingredients": () => [
        { id: 20, name_zh: "玉米", name_en: "corn", aliases_zh: [], aliases_en: [], source: "seed", category: "vegetable", stock_mode: "count", allergens: [], parent_id: null },
      ],
    });
    const name = within(row("葱花")).getByRole("textbox", { name: "Name" });
    await userEvent.clear(name);
    await userEvent.type(name, "玉米");
    const renamed = within(row("玉米"));
    expect(await renamed.findByText(/is the dictionary entry “corn”/)).toBeInTheDocument();
    await userEvent.click(renamed.getByRole("button", { name: "Link to corn" }));
    expect(renamed.getByText("Linked to corn")).toBeInTheDocument();
    await save();
    const lines = lastBody("/api/recipes").ingredients as { ingredient_id: number | null }[];
    expect(lines[0].ingredient_id).toBe(20);
  });

  it("can keep the link after the warning", async () => {
    setup({
      "GET /api/ingredients": () => [
        { id: 20, name_zh: "玉米", name_en: "corn", aliases_zh: [], aliases_en: [], source: "seed", category: "vegetable", stock_mode: "count", allergens: [], parent_id: null },
      ],
    });
    const name = within(row("葱花")).getByRole("textbox", { name: "Name" });
    await userEvent.clear(name);
    await userEvent.type(name, "玉米");
    const renamed = within(row("玉米"));
    await userEvent.click(await renamed.findByRole("button", { name: "Keep scallion" }));
    expect(renamed.queryByText(/is the dictionary entry/)).toBeNull();
    await save();
    const lines = lastBody("/api/recipes").ingredients as { ingredient_id: number | null }[];
    expect(lines[0].ingredient_id).toBe(1);
  });

  it("keeps the link silently when the new name is no dictionary entry", async () => {
    setup({ "GET /api/ingredients": () => [] });
    const name = within(row("葱花")).getByRole("textbox", { name: "Name" });
    await userEvent.clear(name);
    await userEvent.type(name, "小葱花");
    await new Promise((resolve) => setTimeout(resolve, 600));
    expect(within(row("小葱花")).queryByText(/is the dictionary entry/)).toBeNull();
    expect(within(row("小葱花")).getByText("Linked to scallion")).toBeInTheDocument();
  });

  it("adds an ingredient that is not in the dictionary as an unlinked line", async () => {
    setup({ "GET /api/ingredients": () => [] });
    await userEvent.click(screen.getByRole("button", { name: "Add ingredient" }));
    await userEvent.type(screen.getByRole("searchbox", { name: "Search the dictionary" }), "秘方粉");
    await userEvent.click(await screen.findByRole("button", { name: "New ingredient “秘方粉”" }));
    expect(within(row("秘方粉")).getByText("Not in the dictionary")).toBeInTheDocument();
    await save();
    const lines = lastBody("/api/recipes").ingredients as Record<string, unknown>[];
    expect(lines[2]).toMatchObject({ raw: "秘方粉", name: "秘方粉", ingredient_id: null });
  });

  it("offers no new entry when the dictionary already has that exact name", async () => {
    const corn = { id: 20, name_zh: "玉米", name_en: "corn", aliases_zh: ["苞米"], aliases_en: [], source: "seed", category: "vegetable", stock_mode: "count", allergens: [], parent_id: null };
    setup({ "GET /api/ingredients": () => [corn] });
    await userEvent.click(screen.getByRole("button", { name: "Add ingredient" }));
    const search = screen.getByRole("searchbox", { name: "Search the dictionary" });
    for (const exact of ["玉米", " CORN ", "苞米"]) {
      await userEvent.clear(search);
      await userEvent.type(search, exact);
      await screen.findByRole("button", { name: "Choose corn" });
      expect(screen.queryByRole("button", { name: /New ingredient/ })).toBeNull();
    }
    await userEvent.clear(search);
    await userEvent.type(search, "玉米粒");
    expect(await screen.findByRole("button", { name: "New ingredient “玉米粒”" })).toBeInTheDocument();
  });

  it("waits for search results before offering a new entry", async () => {
    let finish: (value: Response) => void = () => {};
    setup({ "GET /api/ingredients": () => new Promise<Response>((resolve) => (finish = resolve)) });
    await userEvent.click(screen.getByRole("button", { name: "Add ingredient" }));
    await userEvent.type(screen.getByRole("searchbox", { name: "Search the dictionary" }), "玉");
    expect(screen.queryByRole("button", { name: /New ingredient/ })).toBeNull();
    await act(async () => finish(reply(200, [])));
    expect(await screen.findByRole("button", { name: "New ingredient “玉”" })).toBeInTheDocument();
  });

  it("adds cookware from the dictionary", async () => {
    setup({
      "GET /api/cookware": () => [
        { id: 8, name_zh: "烤箱", name_en: "oven", aliases_zh: [], aliases_en: [], source: "seed" },
      ],
    });
    await userEvent.click(screen.getByRole("button", { name: "Add cookware" }));
    await userEvent.type(screen.getByRole("searchbox", { name: "Search the dictionary" }), "oven");
    await userEvent.click(await screen.findByRole("button", { name: "Choose oven" }));
    expect(within(row("oven")).getByText("Linked to oven")).toBeInTheDocument();
    await save();
    expect(lastBody("/api/recipes").cookware_ids).toEqual([3, 8]);
  });

  it("creates an entry for cookware that is not in the dictionary", async () => {
    setup({ "GET /api/cookware": () => [] });
    await userEvent.click(screen.getByRole("button", { name: "Add cookware" }));
    await userEvent.type(screen.getByRole("searchbox", { name: "Search the dictionary" }), "面包机");
    await userEvent.click(await screen.findByRole("button", { name: "New cookware “面包机”" }));
    const added = within(row("面包机"));
    // The create form opens at once, so the cookware is not left unsaved by mistake.
    expect(added.getByRole("checkbox", { name: "I own this cookware" })).toBeInTheDocument();
    await userEvent.click(added.getByRole("button", { name: "Create and link" }));
    await waitFor(() => expect(lastBody("/api/cookware")).toMatchObject({ name_zh: "面包机" }));
    await added.findByText(/Linked to/);
    await save();
    expect(lastBody("/api/recipes").cookware_ids).toEqual([3, 60]);
  });

  it("keeps supplies apart from cookware and lets the user edit them", async () => {
    setup({}, [draft({ supplies: ["保鲜膜", "牙签"] })]);
    const supplies = within(screen.getByRole("group", { name: "Supplies" }));
    await userEvent.click(supplies.getByRole("button", { name: "Remove 牙签" }));
    await userEvent.type(supplies.getByRole("textbox", { name: "Add a supply" }), "锡纸{Enter}");
    expect(supplies.getByText("锡纸")).toBeInTheDocument();
    await save();
    expect(lastBody("/api/recipes")).toMatchObject({ supplies: ["保鲜膜", "锡纸"], cookware_ids: [3] });
  });

  it("offers tablespoon and teaspoon units", () => {
    setup();
    const unit = within(row("葱花")).getByRole("combobox", { name: "Unit" });
    expect(within(unit).getByRole("option", { name: "tbsp" })).toBeInTheDocument();
    expect(within(unit).getByRole("option", { name: "tsp" })).toBeInTheDocument();
  });

  it("drops skipped cookware from the save request", async () => {
    setup();
    await userEvent.click(within(row("炒锅")).getByRole("button", { name: "Not needed" }));
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

  it("asks before discarding a draft", async () => {
    setup({}, [draft(), draft({ title: "土豆丝" })]);
    await userEvent.click(screen.getByRole("button", { name: "Discard this draft" }));
    expect(screen.getByText(/paid extraction is lost/)).toBeInTheDocument();
    expect(screen.getByTestId("queue")).toHaveTextContent("葱花蛋|土豆丝");

    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByTestId("queue")).toHaveTextContent("葱花蛋|土豆丝");

    await userEvent.click(screen.getByRole("button", { name: "Discard this draft" }));
    await userEvent.click(screen.getByRole("button", { name: "Yes, discard" }));
    expect(screen.getByTestId("queue")).toHaveTextContent("土豆丝");
    expect(await screen.findByDisplayValue("土豆丝")).toBeInTheDocument();
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

  const EGGS = { raw: "两个鸡蛋", name: "鸡蛋", amount: 2, unit: "piece", amount_type: "count", role: "main", optional: false, choice_group: null, ingredient_id: 1 } as const;

  it("sets the amount type from the unit, and clears a unit that no longer fits", async () => {
    setup({}, [draft({ ingredients: [EGGS] })]);
    const eggs = within(row("鸡蛋"));
    await userEvent.selectOptions(eggs.getByRole("combobox", { name: "Unit" }), "teaspoon");
    expect(eggs.getByRole("combobox", { name: "Amount type" })).toHaveValue("spoon");
    await userEvent.selectOptions(eggs.getByRole("combobox", { name: "Amount type" }), "exact");
    expect(eggs.getByRole("combobox", { name: "Unit" })).toHaveValue("");
  });

  it("warns about a unit and type that do not match, blocks save, and offers the fix", async () => {
    setup({}, [draft({ ingredients: [{ ...EGGS, raw: "生抽4勺", name: "生抽", amount: 4, unit: "spoon" }] })]);
    const soy = within(row("生抽"));
    expect(soy.getByText("A spoon measure is a household measure.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save recipe" }));
    expect(screen.getByText("Fix the amounts marked above before saving.")).toBeInTheDocument();
    expect(callsTo(calls, "POST", "/api/recipes")).toHaveLength(0);
    await userEvent.click(soy.getByRole("button", { name: "Change to Household measure" }));
    await save();
    const lines = lastBody("/api/recipes").ingredients as Record<string, unknown>[];
    expect(lines[0]).toMatchObject({ amount: 4, unit: "spoon", amount_type: "spoon" });
  });

  it("does not save an amount of zero", async () => {
    setup({}, [draft({ ingredients: [{ ...EGGS, amount: 0 }] })]);
    expect(within(row("鸡蛋")).getByText("The amount must be more than 0.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save recipe" }));
    expect(callsTo(calls, "POST", "/api/recipes")).toHaveLength(0);
  });

  it("does not let the mouse wheel change an amount", async () => {
    setup({}, [draft({ ingredients: [EGGS] })]);
    const amount = within(row("鸡蛋")).getByRole("spinbutton", { name: "Amount" });
    amount.focus();
    fireEvent.wheel(amount);
    expect(amount).not.toHaveFocus();
  });

  it("clears amount and unit when the amount type becomes vague", async () => {
    setup({}, [
      draft({
        ingredients: [
          { raw: "两个鸡蛋", name: "鸡蛋", amount: 2, unit: "piece", amount_type: "count", role: "main", optional: false, choice_group: null, ingredient_id: 1 },
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

describe("choice groups", () => {
  function groupedDraft() {
    const base = draft();
    base.ingredients = base.ingredients.map((line, i) => ({ ...line, choice_group: i < 2 ? 1 : null }));
    return base;
  }

  const dataTransfer = { setData: vi.fn(), getData: vi.fn(), effectAllowed: "", dropEffect: "" };

  function dragOnto(sourceName: string, targetName: string) {
    fireEvent.dragStart(within(row(sourceName)).getByLabelText(`Drag ${sourceName}`), { dataTransfer });
    fireEvent.dragOver(row(targetName), { dataTransfer });
    fireEvent.drop(row(targetName), { dataTransfer });
  }

  it("dragging one line onto another makes them a choice", async () => {
    const base = draft();
    base.ingredients = [
      base.ingredients[0],
      base.ingredients[1],
      { ...base.ingredients[1], raw: "香菜", name: "香菜" },
    ];
    setup({}, [base]);
    dragOnto("香菜", "葱花");
    await save();
    const lines = lastBody("/api/recipes").ingredients as { choice_group: number | null }[];
    expect(lines[0].choice_group).not.toBeNull();
    expect(lines[2].choice_group).toBe(lines[0].choice_group);
    expect(lines[1].choice_group).toBeNull();
  });

  it("dragging a line onto a choice card joins the group", async () => {
    const base = groupedDraft();
    base.ingredients.push({ ...base.ingredients[1], raw: "香菜", name: "香菜", choice_group: null });
    setup({}, [base]);
    dragOnto("香菜", "葱花");
    await save();
    const lines = lastBody("/api/recipes").ingredients as { choice_group: number | null }[];
    expect(lines.map((line) => line.choice_group)).toEqual([1, 1, 1]);
  });

  it("dragging a line out of the card onto the list leaves the choice", async () => {
    const base = groupedDraft();
    base.ingredients.push({ ...base.ingredients[1], raw: "香菜", name: "香菜", choice_group: 1 });
    setup({}, [base]);
    fireEvent.dragStart(within(row("香菜")).getByLabelText("Drag 香菜"), { dataTransfer });
    fireEvent.dragOver(screen.getByTestId("ingredient-list"), { dataTransfer });
    fireEvent.drop(screen.getByTestId("ingredient-list"), { dataTransfer });
    await save();
    const lines = lastBody("/api/recipes").ingredients as { choice_group: number | null }[];
    expect(lines.map((line) => line.choice_group)).toEqual([1, 1, null]);
  });

  it("marks a line drag in dataTransfer and drops the fake button role", () => {
    setup();
    const dt = { setData: vi.fn(), getData: vi.fn(), effectAllowed: "", dropEffect: "" };
    const handle = within(row("葱花")).getByLabelText("Drag 葱花");
    expect(handle).not.toHaveAttribute("role");
    fireEvent.dragStart(handle, { dataTransfer: dt });
    expect(dt.setData).toHaveBeenCalledWith("application/x-mmd-line", expect.any(String));
  });

  it("leaves a drop to the browser when no line is being dragged", () => {
    setup();
    const dt = { setData: vi.fn(), getData: vi.fn(), effectAllowed: "", dropEffect: "" };
    const notCancelled = fireEvent.drop(row("葱花"), { dataTransfer: dt });
    expect(notCancelled).toBe(true);
  });

  it("merging a grouped line with an ungrouped one keeps optional uniform", async () => {
    const base = groupedDraft();
    base.ingredients = base.ingredients.map((line) => ({ ...line, optional: line.choice_group !== null }));
    base.ingredients.push({ ...base.ingredients[1], raw: "香菜", name: "香菜", choice_group: null, optional: false });
    setup({}, [base]);
    dragOnto("葱花", "香菜");
    await save();
    const lines = lastBody("/api/recipes").ingredients as { choice_group: number | null; optional: boolean }[];
    expect(lines.map((line) => line.choice_group)).toEqual([1, 1, 1]);
    expect(new Set(lines.map((line) => line.optional)).size).toBe(1);
  });

  it("dragging a grouped line shows a drop zone right below its card", async () => {
    const base = groupedDraft();
    base.ingredients.push({ ...base.ingredients[1], raw: "香菜", name: "香菜", choice_group: null });
    setup({}, [base]);
    expect(screen.queryByText("Drop here to leave the choice")).toBeNull();
    fireEvent.dragStart(within(row("葱花")).getByLabelText("Drag 葱花"), { dataTransfer });
    const zone = screen.getByText("Drop here to leave the choice");
    const card = screen.getByRole("group", { name: /Pick one/ });
    expect(card.nextElementSibling).toBe(zone);
    fireEvent.dragOver(zone, { dataTransfer });
    fireEvent.drop(zone, { dataTransfer });
    await save();
    const lines = lastBody("/api/recipes").ingredients as { choice_group: number | null }[];
    expect(lines.map((line) => line.choice_group)).toEqual([null, null, null]);
  });

  it("taking one line out of a two-line choice dissolves it", async () => {
    setup({}, [groupedDraft()]);
    await userEvent.click(within(row("秘制酱")).getByRole("button", { name: "Not a choice" }));
    await save();
    const lines = lastBody("/api/recipes").ingredients as { choice_group: number | null }[];
    expect(lines.map((line) => line.choice_group)).toEqual([null, null]);
  });

  it("removing a grouped line clears its partner's group", async () => {
    setup({}, [groupedDraft()]);
    await userEvent.click(within(row("秘制酱")).getByRole("button", { name: "Remove" }));
    await save();
    const lines = lastBody("/api/recipes").ingredients as { choice_group: number | null }[];
    expect(lines.map((line) => line.choice_group)).toEqual([null]);
  });

  it("draft groups are shown as one choice card and kept on save", async () => {
    setup({}, [groupedDraft()]);
    const card = screen.getByRole("group", { name: /Pick one/ });
    expect(within(card).getByTestId("row-葱花")).toBeInTheDocument();
    expect(within(card).getByTestId("row-秘制酱")).toBeInTheDocument();
    await save();
    const lines = lastBody("/api/recipes").ingredients as { choice_group: number | null }[];
    expect(lines.map((line) => line.choice_group)).toEqual([1, 1]);
  });

  it("the card's one checkbox makes the whole group optional", async () => {
    setup({}, [groupedDraft()]);
    const card = screen.getByRole("group", { name: /Pick one/ });
    expect(within(card).getAllByRole("checkbox")).toHaveLength(1);
    await userEvent.click(within(card).getByRole("checkbox", { name: "Whole group optional" }));
    await save();
    const lines = lastBody("/api/recipes").ingredients as { optional: boolean }[];
    expect(lines.map((line) => line.optional)).toEqual([true, true]);
  });
});
