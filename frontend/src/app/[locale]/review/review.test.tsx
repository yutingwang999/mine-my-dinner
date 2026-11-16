import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
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
  "POST /api/allergen-suggestions": () => ({ suggested: ["soy"], category: null, stock_mode: null }),
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

  it("uses the shared button classes: Save recipe primary, Remove danger", () => {
    setup();
    expect(screen.getByRole("button", { name: "Save recipe" })).toHaveClass("btn-primary");
    expect(within(row("葱花")).getByRole("button", { name: "Remove" })).toHaveClass("btn-danger");
  });

  it("shows the three unmatched-line actions as one compact group, and the toggles close on a second click", async () => {
    setup();
    const unmatched = within(row("秘制酱"));
    const link = unmatched.getByRole("button", { name: "Link to existing" });
    const create = unmatched.getByRole("button", { name: "Create entry" });
    const skip = unmatched.getByRole("button", { name: "Leave unlinked" });
    for (const button of [link, create, skip]) expect(button.closest(".dish-toggles")).not.toBeNull();
    expect(link.closest(".dish-toggles")).toBe(skip.closest(".dish-toggles"));
    expect(link).toHaveAttribute("aria-expanded", "false");
    expect(skip).not.toHaveAttribute("aria-expanded");
    await userEvent.click(link);
    expect(link).toHaveAttribute("aria-expanded", "true");
    expect(unmatched.getByRole("searchbox", { name: "Search the dictionary" })).toBeInTheDocument();
    await userEvent.click(link);
    expect(link).toHaveAttribute("aria-expanded", "false");
    expect(unmatched.queryByRole("searchbox", { name: "Search the dictionary" })).toBeNull();
    await userEvent.click(create);
    expect(create).toHaveAttribute("aria-expanded", "true");
    await userEvent.click(create);
    expect(create).toHaveAttribute("aria-expanded", "false");
  });

  it("shows the undo action in the compact style", async () => {
    setup();
    await userEvent.click(within(row("秘制酱")).getByRole("button", { name: "Leave unlinked" }));
    expect(within(row("秘制酱")).getByRole("button", { name: /Undo/ }).closest(".dish-toggles")).not.toBeNull();
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

  it("explains when the draft's images can no longer be read", async () => {
    const image = new File(["x"], "a.png", { type: "image/png" });
    Object.defineProperty(image, "arrayBuffer", {
      value: () => Promise.reject(new DOMException("moved", "NotReadableError")),
    });
    setup(
      { "POST /api/recipes": () => reply(201, { id: 1, embedded: true, log_id: null }) },
      [draft({ source_type: "screenshot", images: [image] })],
    );
    await save();
    expect(await screen.findByText(/can no longer be read/)).toBeInTheDocument();
    expect(callsTo(calls, "POST", "/api/recipes/1/images")).toHaveLength(0);
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

  it("scrolls back to the top after finishing a draft", async () => {
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    setup({}, [draft(), draft({ title: "下一道" })]);
    scrollTo.mockClear();
    await save();
    await waitFor(() => expect(scrollTo).toHaveBeenCalledWith(0, 0));
    scrollTo.mockRestore();
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
    setup({ "POST /api/allergen-suggestions": () => ({ suggested: [], category: null, stock_mode: null }) });
    await userEvent.click(within(row("秘制酱")).getByRole("button", { name: "Create entry" }));
    expect(await screen.findByText(/The model suggested none/)).toBeInTheDocument();
    expect(screen.queryByText("Confirmed: contains none")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Confirm it contains none" }));
    expect(screen.getByText("Confirmed: contains none")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Create and link" }));
    await waitFor(() => expect(lastBody("/api/ingredients").allergens).toEqual([]));
  });

  it("still allows saving when suggestions are unavailable", async () => {
    setup({ "POST /api/allergen-suggestions": () => ({ suggested: null, category: null, stock_mode: null }) });
    await userEvent.click(within(row("秘制酱")).getByRole("button", { name: "Create entry" }));
    expect(await screen.findByText(/Suggestions are unavailable/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save recipe" })).toBeEnabled();
  });

  it("prefills category and stock mode from the suggestion, and the user can change them", async () => {
    setup({
      "POST /api/allergen-suggestions": () => ({ suggested: ["soy"], category: "soy product", stock_mode: "count" }),
    });
    await userEvent.click(within(row("秘制酱")).getByRole("button", { name: "Create entry" }));
    const category = within(row("秘制酱")).getByRole("combobox", { name: "Category" });
    await waitFor(() => expect(category).toHaveValue("soy product"));
    const stock = within(row("秘制酱")).getByRole("combobox", { name: "Stock mode" });
    expect(stock).toHaveValue("count");
    expect(within(row("秘制酱")).getByText("Category and stock mode are suggested from the name; you can change them.")).toBeInTheDocument();
    await userEvent.selectOptions(stock, "availability");
    await userEvent.click(screen.getByRole("button", { name: "Create and link" }));
    await waitFor(() => expect(callsTo(calls, "POST", "/api/ingredients")).toHaveLength(1));
    expect(lastBody("/api/ingredients")).toMatchObject({ category: "soy product", stock_mode: "availability" });
  });

  it("keeps the old prefill without a suggestion: seasoning for a seasoning line, and level", async () => {
    setup({
      "POST /api/allergen-suggestions": () => ({ suggested: null, category: null, stock_mode: null }),
    });
    await userEvent.click(within(row("秘制酱")).getByRole("button", { name: "Create entry" }));
    await screen.findByText(/Suggestions are unavailable/);
    expect(within(row("秘制酱")).getByRole("combobox", { name: "Category" })).toHaveValue("seasoning");
    expect(within(row("秘制酱")).getByRole("combobox", { name: "Stock mode" })).toHaveValue("level");
    expect(within(row("秘制酱")).queryByText("Category and stock mode are suggested from the name; you can change them.")).toBeNull();
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

  it("typing a number derives the type from the unit", async () => {
    setup({}, [draft({ ingredients: [EGGS] })]);
    const eggs = within(row("鸡蛋"));
    const unit = eggs.getByRole("combobox", { name: "Unit" });
    expect(eggs.queryByRole("combobox", { name: "Amount type" })).not.toBeInTheDocument();
    await userEvent.selectOptions(unit, "g");
    expect(eggs.getByText("Exact")).toBeInTheDocument();
    await userEvent.selectOptions(unit, "piece");
    expect(eggs.getByText("Count")).toBeInTheDocument();
    await userEvent.selectOptions(unit, "");
    expect(eggs.getByText("Household measure", { selector: "strong" })).toBeInTheDocument();
    const household = within(unit).getByRole("option", { name: "Household measure" });
    expect(household).toHaveAttribute("title", "bowl, cup, handful, pinch, …");
    expect(eggs.getByText("e.g. bowl, cup, handful, pinch")).toBeInTheDocument();
    await userEvent.selectOptions(unit, "g");
    expect(eggs.queryByText("e.g. bowl, cup, handful, pinch")).not.toBeInTheDocument();
    expect(eggs.getByRole("spinbutton", { name: "Amount" })).toHaveValue(2);
  });

  it("shows None for the empty unit only while no number is entered", async () => {
    setup({}, [draft({ ingredients: [EGGS] })]);
    const eggs = within(row("鸡蛋"));
    const unit = eggs.getByRole("combobox", { name: "Unit" });
    expect(within(unit).queryByRole("option", { name: "None" })).not.toBeInTheDocument();
    await userEvent.clear(eggs.getByRole("spinbutton", { name: "Amount" }));
    expect(within(unit).getByRole("option", { name: "None" })).toBeInTheDocument();
  });

  it("disables the unit while the amount is empty and enables it after a number is typed", async () => {
    setup({}, [draft({ ingredients: [{ ...EGGS, amount: null, unit: null, amount_type: "missing" }] })]);
    const eggs = within(row("鸡蛋"));
    const unit = eggs.getByRole("combobox", { name: "Unit" });
    expect(unit).toBeDisabled();
    await userEvent.type(eggs.getByRole("spinbutton", { name: "Amount" }), "2");
    expect(unit).toBeEnabled();
    await userEvent.selectOptions(unit, "g");
    await userEvent.clear(eggs.getByRole("spinbutton", { name: "Amount" }));
    expect(unit).toBeDisabled();
    expect(unit).toHaveValue("g");
    await save();
    const lines = lastBody("/api/recipes").ingredients as Record<string, unknown>[];
    expect(lines[0]).toMatchObject({ amount: null, unit: null, amount_type: "missing" });
  });

  it("Unspecified amount clears number and unit and is pressed", async () => {
    setup({}, [draft({ ingredients: [EGGS] })]);
    const eggs = within(row("鸡蛋"));
    await userEvent.click(eggs.getByRole("button", { name: "Unspecified amount" }));
    expect(eggs.getByRole("button", { name: "Unspecified amount" })).toHaveAttribute("aria-pressed", "true");
    expect(eggs.getByRole("button", { name: "Not stated" })).toHaveAttribute("aria-pressed", "false");
    await save();
    const lines = lastBody("/api/recipes").ingredients as Record<string, unknown>[];
    expect(lines[0]).toMatchObject({ amount: null, unit: null, amount_type: "vague" });
  });

  it("typing a number releases a pressed toggle", async () => {
    setup({}, [draft({ ingredients: [{ ...EGGS, amount: null, unit: null, amount_type: "missing" }] })]);
    const eggs = within(row("鸡蛋"));
    expect(eggs.getByRole("button", { name: "Not stated" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.type(eggs.getByRole("spinbutton", { name: "Amount" }), "2");
    expect(eggs.getByRole("button", { name: "Not stated" })).toHaveAttribute("aria-pressed", "false");
    expect(eggs.getByRole("button", { name: "Unspecified amount" })).toHaveAttribute("aria-pressed", "false");
    expect(eggs.getByText("Household measure", { selector: "strong" })).toBeInTheDocument();
    await userEvent.selectOptions(eggs.getByRole("combobox", { name: "Unit" }), "piece");
    expect(eggs.getByText("Count")).toBeInTheDocument();
  });

  it("clicking a pressed toggle again releases it and leaves the amount empty", async () => {
    setup({}, [draft({ ingredients: [{ ...EGGS, amount: null, unit: null, amount_type: "vague" }] })]);
    const eggs = within(row("鸡蛋"));
    const toggle = eggs.getByRole("button", { name: "Unspecified amount" });
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(eggs.getByRole("button", { name: "Not stated" })).toHaveAttribute("aria-pressed", "false");
    expect(eggs.getByRole("spinbutton", { name: "Amount" })).toHaveValue(null);
    await save();
    const lines = lastBody("/api/recipes").ingredients as Record<string, unknown>[];
    expect(lines[0]).toMatchObject({ amount: null, unit: null, amount_type: "missing" });
  });

  it("renders the same seven slots in the same order for a vague line and a numbered line", () => {
    setup({}, [
      draft({
        ingredients: [
          { ...EGGS, name: "盐", raw: "盐适量", amount: null, unit: null, amount_type: "vague" },
          EGGS,
        ],
      }),
    ]);
    const slots = (name: string) =>
      Array.from(row(name).querySelectorAll(".line-grid > [data-slot]")).map((el) => el.getAttribute("data-slot"));
    const order = ["name", "amount", "unit", "toggles", "type", "role", "optional"];
    expect(slots("盐")).toEqual(order);
    expect(slots("鸡蛋")).toEqual(order);
    expect(row("盐").querySelector('[data-slot="type"]')).toBeEmptyDOMElement();
    // Remove sits on the top line, outside the grid.
    const remove = within(row("盐")).getByRole("button", { name: "Remove" });
    expect(remove.closest(".line-raw")).not.toBeNull();
    expect(remove.closest(".line-grid")).toBeNull();
    expect(row("鸡蛋").querySelector('[data-slot="type"]')).toHaveTextContent("Count");
  });

  it("shows the toggle that matches how the line was extracted", () => {
    setup({}, [
      draft({
        ingredients: [
          { ...EGGS, name: "盐", raw: "盐适量", amount: null, unit: null, amount_type: "vague" },
          { ...EGGS, name: "葱", raw: "葱", amount: null, unit: null, amount_type: "missing" },
        ],
      }),
    ]);
    const salt = within(row("盐"));
    expect(salt.getByRole("button", { name: "Unspecified amount" })).toHaveAttribute("aria-pressed", "true");
    expect(salt.getByRole("button", { name: "Not stated" })).toHaveAttribute("aria-pressed", "false");
    const scallion = within(row("葱"));
    expect(scallion.getByRole("button", { name: "Not stated" })).toHaveAttribute("aria-pressed", "true");
    expect(scallion.getByRole("button", { name: "Unspecified amount" })).toHaveAttribute("aria-pressed", "false");
  });

  it("the save request never has a type that disagrees with the unit", async () => {
    // The model's type disagrees with the unit; the saved type follows the unit.
    setup({}, [draft({ ingredients: [{ ...EGGS, raw: "生抽4勺", name: "生抽", amount: 4, unit: "spoon", amount_type: "exact" }] })]);
    expect(within(row("生抽")).getByText("Household measure", { selector: "strong" })).toBeInTheDocument();
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

  it("scrolls the page while a drag hovers near the window's edge", () => {
    setup();
    const scrollBy = vi.fn();
    const frames: FrameRequestCallback[] = [];
    vi.stubGlobal("scrollBy", scrollBy);
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) =>
      frames.push(callback),
    );
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    try {
      // jsdom has no DragEvent, so a MouseEvent carries the pointer position instead.
      const dragOverAt = (clientY: number) =>
        fireEvent(document.body, new MouseEvent("dragover", { bubbles: true, clientY }));
      fireEvent.dragStart(within(row("葱花")).getByLabelText("Drag 葱花"), { dataTransfer });
      dragOverAt(window.innerHeight - 1);
      frames.shift()?.(0);
      expect(scrollBy).toHaveBeenCalledTimes(1);
      expect(scrollBy.mock.calls[0][1]).toBeGreaterThan(0);
      dragOverAt(1);
      frames.shift()?.(0);
      expect(scrollBy).toHaveBeenCalledTimes(2);
      expect(scrollBy.mock.calls[1][1]).toBeLessThan(0);
      // Back in the middle of the window, the pending frame scrolls no further.
      dragOverAt(window.innerHeight / 2);
      while (frames.length) frames.shift()?.(0);
      expect(scrollBy).toHaveBeenCalledTimes(2);
    } finally {
      vi.unstubAllGlobals();
    }
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

const BROCCOLI = {
  id: 9, name_zh: "西兰花", name_en: "broccoli", aliases_zh: ["西蓝花"], aliases_en: [], user_aliases_zh: [], user_aliases_en: [],
  source: "seed", category: "vegetable", stock_mode: "level", allergens: [], parent_id: null,
};

function broccoliLine(raw = "西蓝花", name = "西蓝花") {
  return { raw, name, amount: null, unit: null, amount_type: "missing", role: "main", optional: false, choice_group: null, ingredient_id: null } as const;
}

async function linkBroccoli(index = 0) {
  const target = screen.getAllByTestId("row-西蓝花")[index];
  await userEvent.click(within(target).getByRole("button", { name: "Link to existing" }));
  await userEvent.type(screen.getByRole("searchbox", { name: "Search the dictionary" }), "西");
  await userEvent.click(await screen.findByRole("button", { name: "Choose broccoli" }));
}

const BROCCOLI_ROUTES: Routes = { "GET /api/ingredients": () => [BROCCOLI] };

function ingredientsSent(): { ingredient_id: number | null; remember_link: boolean }[] {
  const matching = callsTo(calls, "POST", "/api/recipes");
  return (matching[matching.length - 1].body as { ingredients: never[] }).ingredients;
}

describe("review: remembering links", () => {
  it("shows the remember checkbox checked after a manual link of an unmatched line", async () => {
    setup(BROCCOLI_ROUTES, [draft({ ingredients: [broccoliLine()] })]);
    expect(screen.queryByRole("checkbox", { name: "Remember this link" })).toBeNull();
    await linkBroccoli();
    expect(screen.getByRole("checkbox", { name: "Remember this link" })).toBeChecked();
  });

  it("offers no checkbox on a line whose own name matched, or when relinking it", async () => {
    setup(BROCCOLI_ROUTES, [draft()]);
    expect(screen.queryByRole("checkbox", { name: "Remember this link" })).toBeNull();
    await userEvent.click(within(row("葱花")).getByRole("button", { name: "Change link" }));
    await userEvent.click(within(row("葱花")).getByRole("button", { name: "Link to existing" }));
    await userEvent.type(screen.getByRole("searchbox", { name: "Search the dictionary" }), "西");
    await userEvent.click(await screen.findByRole("button", { name: "Choose broccoli" }));
    expect(within(row("葱花")).getByText("Linked to broccoli")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "Remember this link" })).toBeNull();
  });

  it("sends remember_link for a remembered line and false when unchecked", async () => {
    setup(BROCCOLI_ROUTES, [draft({ ingredients: [broccoliLine()] })]);
    await linkBroccoli();
    await save();
    expect(ingredientsSent()).toMatchObject([{ ingredient_id: 9, remember_link: true }]);

    cleanup();
    setup(BROCCOLI_ROUTES, [draft({ ingredients: [broccoliLine()] })]);
    await linkBroccoli();
    await userEvent.click(screen.getByRole("checkbox", { name: "Remember this link" }));
    await save();
    expect(ingredientsSent()).toMatchObject([{ ingredient_id: 9, remember_link: false }]);
  });

  it("links the other unmatched same-name lines of the draft when remembered", async () => {
    setup(BROCCOLI_ROUTES, [draft({ ingredients: [broccoliLine("一颗西蓝花"), broccoliLine("西蓝花少许")] })]);
    await linkBroccoli(0);
    const rows = screen.getAllByTestId("row-西蓝花");
    expect(within(rows[1]).getByText("Linked to broccoli")).toBeInTheDocument();
    await save();
    expect(ingredientsSent()).toMatchObject([
      { ingredient_id: 9, remember_link: true },
      { ingredient_id: 9, remember_link: true },
    ]);
  });

  it("keeps the second line's link but does not remember it when the box is unchecked", async () => {
    setup(BROCCOLI_ROUTES, [draft({ ingredients: [broccoliLine("一颗西蓝花"), broccoliLine("西蓝花少许")] })]);
    await linkBroccoli(0);
    await userEvent.click(within(screen.getAllByTestId("row-西蓝花")[0]).getByRole("checkbox", { name: "Remember this link" }));
    await save();
    expect(ingredientsSent()).toMatchObject([
      { ingredient_id: 9, remember_link: false },
      { ingredient_id: 9, remember_link: false },
    ]);
  });

  it("matches the next draft again when it is shown", async () => {
    let saved = false;
    setup(
      {
        ...BROCCOLI_ROUTES,
        "POST /api/recipes": () => {
          saved = true;
          return reply(201, { id: 1 });
        },
        "POST /api/ingredients/match": () => ({
          matches: saved ? [{ name: "西蓝花", entry: BROCCOLI }] : [],
        }),
      },
      [draft({ ingredients: [broccoliLine()] }), draft({ title: "清炒西蓝花", ingredients: [broccoliLine()] })],
    );
    await linkBroccoli();
    await save();
    expect(await screen.findByDisplayValue("清炒西蓝花")).toBeInTheDocument();
    expect(await within(row("西蓝花")).findByText("Linked to broccoli")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "Remember this link" })).toBeNull();
  });

  it("asks the match endpoint only for unmatched names", async () => {
    setup({ "POST /api/ingredients/match": () => ({ matches: [] }) });
    await waitFor(() => expect(callsTo(calls, "POST", "/api/ingredients/match")).toHaveLength(1));
    expect(callsTo(calls, "POST", "/api/ingredients/match")[0].body).toEqual({ names: ["秘制酱"] });
  });

  it("the re-match leaves touched lines alone", async () => {
    let answer: (response: unknown) => void = () => {};
    setup(
      { "POST /api/ingredients/match": () => new Promise((resolve) => (answer = resolve)) },
      [draft({ ingredients: [broccoliLine("西蓝花", "西蓝花"), broccoliLine("青花菜", "青花菜"), broccoliLine("花椰菜", "花椰菜")] })],
    );
    // One line is skipped and another renamed (and renamed back) before the answer arrives.
    await userEvent.click(within(row("西蓝花")).getByRole("button", { name: "Leave unlinked" }));
    const renamed = within(row("青花菜")).getByRole("textbox", { name: "Name" });
    await userEvent.type(renamed, "x");
    await userEvent.type(renamed, "{Backspace}");
    await act(async () =>
      answer(
        reply(200, {
          matches: ["西蓝花", "青花菜", "花椰菜"].map((name) => ({ name, entry: BROCCOLI })),
        }),
      ),
    );
    expect(within(row("西蓝花")).getByText(/Not linked/)).toBeInTheDocument();
    expect(within(row("青花菜")).getByText("Not in the dictionary")).toBeInTheDocument();
    expect(within(row("花椰菜")).getByText("Linked to broccoli")).toBeInTheDocument();
  });

  it("a failed re-match leaves the draft usable", async () => {
    setup({ "POST /api/ingredients/match": () => reply(500, { code: "unknown" }) });
    await waitFor(() => expect(callsTo(calls, "POST", "/api/ingredients/match")).toHaveLength(1));
    expect(within(row("秘制酱")).getByText("Not in the dictionary")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    await save();
  });

  it("shows the alias conflict notice after saving", async () => {
    setup(
      { "POST /api/recipes": () => reply(201, { id: 1, alias_conflicts: ["西蓝花"] }) },
      [draft(), draft({ title: "下一道" })],
    );
    await save();
    expect(await screen.findByDisplayValue("下一道")).toBeInTheDocument();
    expect(screen.getByText("Not remembered, already a name or alias of another entry: 西蓝花")).toBeInTheDocument();
  });

  it("shows the alias conflict notice on the empty-queue page", async () => {
    setup({ "POST /api/recipes": () => reply(201, { id: 1, alias_conflicts: ["西蓝花"] }) });
    await save();
    await screen.findByText("There are no drafts to review.");
    expect(screen.getByText("Not remembered, already a name or alias of another entry: 西蓝花")).toBeInTheDocument();
  });

  it("warns before unload only while drafts are queued", async () => {
    setup({}, [draft(), draft({ title: "下一道" })]);
    const before = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(before);
    expect(before.defaultPrevented).toBe(true);
    await save();
    await userEvent.click(await screen.findByRole("button", { name: "Save recipe" }));
    await screen.findByText("There are no drafts to review.");
    const after = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(after);
    expect(after.defaultPrevented).toBe(false);
  });
});

describe("review: undoing a link change", () => {
  it("restores a hand link with its remember choice", async () => {
    setup(BROCCOLI_ROUTES, [draft({ ingredients: [broccoliLine()] })]);
    await linkBroccoli();
    await userEvent.click(screen.getByRole("checkbox", { name: "Remember this link" }));
    const target = within(screen.getByTestId("row-西蓝花"));
    await userEvent.click(target.getByRole("button", { name: "Change link" }));
    expect(target.queryByRole("checkbox", { name: "Remember this link" })).toBeNull();
    await userEvent.click(target.getByRole("button", { name: "Undo change" }));
    expect(target.getByText("Linked to broccoli")).toBeInTheDocument();
    expect(target.getByRole("checkbox", { name: "Remember this link" })).not.toBeChecked();
  });
});

describe("review: owned names in the create form", () => {
  // The first call is the draft's own re-match; only the create form's check finds an owner.
  let matchCalls = 0;
  const OWNER_ROUTES: Routes = {
    "POST /api/ingredients/match": () =>
      ++matchCalls > 1 ? { matches: [{ name: "秘制酱", entry: BROCCOLI }] } : { matches: [] },
  };

  beforeEach(() => {
    matchCalls = 0;
  });

  async function openCreate() {
    await userEvent.click(within(row("秘制酱")).getByRole("button", { name: "Create entry" }));
  }

  it("shows the notice and disables submit when the name belongs to an entry", async () => {
    setup(OWNER_ROUTES);
    await openCreate();
    expect(await screen.findByText("\"秘制酱\" is already a name or alias of \"broccoli\".")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Manage in the dictionary" })).toHaveAttribute("href", "/dictionary");
    expect(screen.getByRole("button", { name: "Create and link" })).toBeDisabled();
  });

  it("links the line to the owner entry from the notice", async () => {
    setup(OWNER_ROUTES);
    await openCreate();
    await userEvent.click(await screen.findByRole("button", { name: "Link to \"broccoli\"" }));
    expect(within(row("秘制酱")).getByText("Linked to broccoli")).toBeInTheDocument();
    // A link to an existing entry is a link by hand, so the user may remember it.
    expect(within(row("秘制酱")).getByRole("checkbox", { name: "Remember this link" })).toBeChecked();
    await save();
    expect(ingredientsSent()[1]).toMatchObject({ ingredient_id: 9, remember_link: true });
  });

  it("shows no notice for an unowned name", async () => {
    setup({ "POST /api/ingredients/match": () => ({ matches: [] }) });
    await openCreate();
    await waitFor(() => expect(callsTo(calls, "POST", "/api/ingredients/match").length).toBeGreaterThan(1));
    expect(screen.queryByText(/is already a name or alias/)).toBeNull();
    expect(screen.getByRole("button", { name: "Create and link" })).toBeEnabled();
  });

  it("leaves submit enabled when the check fails", async () => {
    setup({ "POST /api/ingredients/match": () => reply(500, { code: "unknown" }) });
    await openCreate();
    await waitFor(() => expect(callsTo(calls, "POST", "/api/ingredients/match").length).toBeGreaterThan(1));
    expect(screen.queryByText(/is already a name or alias/)).toBeNull();
    expect(screen.getByRole("button", { name: "Create and link" })).toBeEnabled();
  });
});
