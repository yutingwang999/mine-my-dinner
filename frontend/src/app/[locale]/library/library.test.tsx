import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useSyncExternalStore } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RecipeLibrary } from "@/components/RecipeLibrary";
import type { SavedRecipe } from "@/lib/api";
import { type Call, mockFetch, renderWithProviders, savedRecipe } from "@/test/helpers";

const replace = vi.fn();
let search = new URLSearchParams();
const listeners = new Set<() => void>();

/** Change the mocked address from outside the component, as a nav link or back/forward does. */
function navigate(query: string) {
  search = new URLSearchParams(query);
  act(() => listeners.forEach((listener) => listener()));
}

vi.mock("next/navigation", () => ({
  useSearchParams: () =>
    useSyncExternalStore(
      (listener) => {
        listeners.add(listener);
        return () => listeners.delete(listener);
      },
      () => search,
      () => search,
    ),
}));
vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children, className }: { href: string; children: React.ReactNode; className?: string }) => (
    <a href={href} className={className}>
      {children}
    </a>
  ),
  useRouter: () => ({ push: vi.fn(), replace }),
}));

let calls: Call[];

function setup(items: SavedRecipe[] = [savedRecipe()]) {
  calls = mockFetch({
    "GET /api/recipes": () => ({ items, total: items.length, page: 1 }),
  });
  renderWithProviders(<RecipeLibrary />);
}

beforeEach(() => {
  search = new URLSearchParams();
  replace.mockClear();
});
afterEach(() => vi.unstubAllGlobals());
beforeEach(() => window.localStorage.clear());

describe("library", () => {
  it("searches and filters", async () => {
    setup();
    expect(await screen.findByText("番茄炒蛋")).toBeInTheDocument();
    await userEvent.type(screen.getByRole("searchbox", { name: /Search title/ }), "番茄");
    await waitFor(() =>
      expect(calls.some((call) => call.url.includes(`q=${encodeURIComponent("番茄")}`))).toBe(true),
    );
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Dish type" }), "soup");
    await waitFor(() => expect(calls.some((call) => call.url.includes("dish_type=soup"))).toBe(true));
  });

  it("has only the two toggles and a title link to the detail route", async () => {
    setup();
    const item = within(await screen.findByTestId("recipe-1"));
    expect(item.getAllByRole("button").map((button) => button.textContent)).toEqual([
      "How to cook ▾",
      "Original text ▾",
    ]);
    const link = item.getByRole("link", { name: "番茄炒蛋" });
    expect(link).toHaveAttribute("href", "/library/1");
    expect(item.getAllByRole("link")).toHaveLength(1);
  });

  it("opens one panel at a time from the row data, without a fetch", async () => {
    setup([savedRecipe({ source_url: null })]);
    const item = within(await screen.findByTestId("recipe-1"));
    await userEvent.click(item.getByRole("button", { name: "Original text" }));
    expect(item.getByText("原文：番茄两个")).toBeInTheDocument();
    await userEvent.click(item.getByRole("button", { name: "How to cook" }));
    expect(item.queryByText("原文：番茄两个")).toBeNull();
    expect(item.getByText("鸡蛋炒熟后撒葱花。")).toBeInTheDocument();
    await userEvent.click(item.getByRole("button", { name: "How to cook" }));
    expect(item.queryByText("鸡蛋炒熟后撒葱花。")).toBeNull();
    expect(calls.filter((call) => call.path === "/api/recipes/1")).toHaveLength(0);
  });

  it("shows the screenshots in the original text panel", async () => {
    const image = `${"a".repeat(64)}.png`;
    setup([savedRecipe({ images: [{ file_name: image, mime_type: "image/png", position: 0 }] })]);
    const item = within(await screen.findByTestId("recipe-1"));
    await userEvent.click(item.getByRole("button", { name: "Original text" }));
    expect(item.getByRole("img", { name: "Screenshot 1" })).toHaveAttribute("src", `/api/images/${image}`);
  });

  it("lists the supplies to have ready", async () => {
    setup([savedRecipe({ supplies: ["保鲜膜", "锡纸"] })]);
    const item = within(await screen.findByTestId("recipe-1"));
    expect(item.getByText("Have ready: 保鲜膜, 锡纸")).toBeInTheDocument();
  });

  it("shows an empty state", async () => {
    setup([]);
    expect(await screen.findByText("No recipes found.")).toBeInTheDocument();
  });
});

describe("library page size", () => {
  it("sends page_size, defaulting to 20", async () => {
    setup();
    await screen.findByText("番茄炒蛋");
    expect(calls.find((call) => call.path === "/api/recipes")?.url).toContain("page_size=20");
  });

  it("sends the chosen size, returns to page 1, keeps it out of the address, and remembers it", async () => {
    const items = [savedRecipe()];
    calls = mockFetch({ "GET /api/recipes": () => ({ items, total: 120, page: 1 }) });
    search = new URLSearchParams("page=3");
    const view = renderWithProviders(<RecipeLibrary />);
    await screen.findByText("番茄炒蛋");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Rows per page" }), "50");
    await waitFor(() => {
      const last = calls.filter((call) => call.path === "/api/recipes").at(-1)?.url ?? "";
      expect(last).toContain("page_size=50");
      expect(last).toContain("page=1&");
    });
    expect(replace.mock.calls.every(([address]) => !String(address).includes("page_size"))).toBe(true);
    expect(window.localStorage.getItem("pageSize:library")).toBe("50");

    view.unmount();
    search = new URLSearchParams();
    renderWithProviders(<RecipeLibrary />);
    await waitFor(() =>
      expect(calls.filter((call) => call.path === "/api/recipes").at(-1)?.url).toContain("page_size=50"),
    );
  });
});

describe("library search in the address", () => {
  it("reads q, type and page on load", async () => {
    search = new URLSearchParams("q=鸡蛋&type=vegetable dish&page=2");
    setup();
    await screen.findByText("番茄炒蛋");
    const url = calls.find((call) => call.path === "/api/recipes")?.url ?? "";
    expect(url).toContain(`q=${encodeURIComponent("鸡蛋")}`);
    expect(url).toContain("dish_type=vegetable+dish");
    expect(url).toContain("page=2");
    expect(screen.getByRole("searchbox", { name: /Search title/ })).toHaveValue("鸡蛋");
    // Nothing changed, so the address is left alone.
    expect(replace).not.toHaveBeenCalled();
  });

  it("updates the address while typing, replacing the entry", async () => {
    setup();
    await screen.findByText("番茄炒蛋");
    await userEvent.type(screen.getByRole("searchbox", { name: /Search title/ }), "蛋");
    await waitFor(() => expect(replace).toHaveBeenLastCalledWith(`/library?q=${encodeURIComponent("蛋")}`));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Dish type" }), "soup");
    await waitFor(() =>
      expect(replace).toHaveBeenLastCalledWith(`/library?q=${encodeURIComponent("蛋")}&type=soup`),
    );
  });

  it("clears the filters when the address loses its query (nav link)", async () => {
    search = new URLSearchParams("q=鸡蛋");
    setup();
    await screen.findByText("番茄炒蛋");
    const box = screen.getByRole("searchbox", { name: /Search title/ });
    expect(box).toHaveValue("鸡蛋");
    calls.length = 0;
    navigate("");
    await waitFor(() => expect(box).toHaveValue(""));
    await waitFor(() => expect(calls.some((call) => call.path === "/api/recipes")).toBe(true));
    expect(calls.at(-1)?.url).not.toContain("q=");
    expect(replace).not.toHaveBeenCalled();
  });

  it("follows a back/forward change to another search", async () => {
    search = new URLSearchParams("q=鸡蛋");
    setup();
    await screen.findByText("番茄炒蛋");
    navigate("q=番茄&type=soup&page=3");
    const box = screen.getByRole("searchbox", { name: /Search title/ });
    await waitFor(() => expect(box).toHaveValue("番茄"));
    expect(screen.getByRole("combobox", { name: "Dish type" })).toHaveValue("soup");
    await waitFor(() => expect(calls.at(-1)?.url).toContain("page=3"));
    expect(replace).not.toHaveBeenCalled();
  });

  it("keeps what is typed when the address catches up with it", async () => {
    setup();
    await screen.findByText("番茄炒蛋");
    const box = screen.getByRole("searchbox", { name: /Search title/ });
    await userEvent.type(box, "蛋");
    await waitFor(() => expect(replace).toHaveBeenCalled());
    await userEvent.type(box, "汤");
    navigate(`q=${encodeURIComponent("蛋")}`); // the older write lands late
    expect(box).toHaveValue("蛋汤");
  });

  it("passes the search to the detail link as from", async () => {
    search = new URLSearchParams("q=鸡蛋&page=2");
    setup();
    const link = within(await screen.findByTestId("recipe-1")).getByRole("link", { name: "番茄炒蛋" });
    expect(link).toHaveAttribute("href", `/library/1?from=${encodeURIComponent(`q=${encodeURIComponent("鸡蛋")}&page=2`)}`);
  });
});
