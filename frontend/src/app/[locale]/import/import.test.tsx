import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ImportForm } from "@/components/ImportForm";
import { draft, mockFetch, renderWithProviders, reply } from "@/test/helpers";

const push = vi.fn();

vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
  useRouter: () => ({ push }),
}));

afterEach(() => {
  vi.unstubAllGlobals();
  push.mockReset();
});

describe("import", () => {
  it("queues the draft and opens review when text import succeeds", async () => {
    mockFetch({ "POST /api/imports/text": () => draft() });
    renderWithProviders(<ImportForm />);
    await userEvent.type(screen.getByRole("textbox", { name: /Paste one recipe/ }), "葱花蛋");
    await userEvent.click(screen.getByRole("button", { name: "Extract" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/review"));
    expect(screen.getByTestId("queue")).toHaveTextContent("葱花蛋");
  });

  it("keeps the pasted text and shows a message when import fails", async () => {
    mockFetch({ "POST /api/imports/text": () => reply(503, { code: "models_unavailable" }) });
    renderWithProviders(<ImportForm />);
    const box = screen.getByRole("textbox", { name: /Paste one recipe/ });
    await userEvent.type(box, "葱花蛋的做法");
    await userEvent.click(screen.getByRole("button", { name: "Extract" }));
    expect(await screen.findByText(/No model is available/)).toBeInTheDocument();
    expect(box).toHaveValue("葱花蛋的做法");
    expect(push).not.toHaveBeenCalled();
  });

  it("suggests pasting text when a web import fails", async () => {
    mockFetch({
      "POST /api/imports/url": () => reply(422, { code: "no_main_text", suggest_paste: true }),
    });
    renderWithProviders(<ImportForm />);
    const url = screen.getByRole("textbox", { name: "Web page link" });
    await userEvent.type(url, "https://login.example/recipe");
    await userEvent.click(screen.getByRole("button", { name: "Import page" }));
    expect(await screen.findByText(/No recipe text was found/)).toBeInTheDocument();
    expect(screen.getByText(/Copy the recipe text from the page/)).toBeInTheDocument();
    expect(url).toHaveValue("https://login.example/recipe");
  });

  it("shows each failed batch item and queues the rest", async () => {
    mockFetch({
      "POST /api/imports/batch": () => ({
        items: [
          { index: 0, draft: draft({ title: "甲" }), error: null },
          { index: 1, draft: null, error: "models_unavailable" },
          { index: 2, draft: draft({ title: "丙" }), error: null },
        ],
      }),
    });
    renderWithProviders(<ImportForm />);
    const file = new File(["甲\n---\n乙\n---\n丙"], "dishes.txt", { type: "text/plain" });
    await userEvent.upload(screen.getByLabelText("Text file with several dishes"), file);
    await userEvent.click(screen.getByRole("button", { name: "Import file" }));
    expect(await screen.findByText(/Dish 2: No model is available/)).toBeInTheDocument();
    expect(screen.getByText("2 dishes are ready for review.")).toBeInTheDocument();
    expect(screen.getByTestId("queue")).toHaveTextContent("甲|丙");
    expect(push).not.toHaveBeenCalled();
  });

  it("sends screenshots and opens review", async () => {
    const calls = mockFetch({ "POST /api/imports/screenshots": () => draft() });
    renderWithProviders(<ImportForm />);
    const image = new File(["x"], "a.png", { type: "image/png" });
    await userEvent.upload(screen.getByLabelText("Screenshots of one dish"), image);
    await userEvent.click(screen.getByRole("button", { name: "Extract from screenshots" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/review"));
    const body = calls[0].body as FormData;
    expect(body.getAll("images")).toHaveLength(1);
  });

  it("says the paste box and link box take one recipe each", () => {
    renderWithProviders(<ImportForm />);
    expect(screen.getByText(/One recipe at a time/)).toBeInTheDocument();
    expect(screen.getByText(/One web page at a time/)).toBeInTheDocument();
  });

  it("stops several links in the link box before calling the server", async () => {
    const calls = mockFetch({});
    renderWithProviders(<ImportForm />);
    await userEvent.type(
      screen.getByRole("textbox", { name: "Web page link" }),
      "https://a.example/1 https://b.example/2",
    );
    await userEvent.click(screen.getByRole("button", { name: "Import page" }));
    expect(await screen.findByText(/Import one link at a time/)).toBeInTheDocument();
    expect(calls).toHaveLength(0);
  });

  it("stops a paste that is only several links", async () => {
    const calls = mockFetch({});
    renderWithProviders(<ImportForm />);
    await userEvent.type(
      screen.getByRole("textbox", { name: /Paste one recipe/ }),
      "https://a.example/1\nhttps://b.example/2",
    );
    await userEvent.click(screen.getByRole("button", { name: "Extract" }));
    expect(await screen.findByText(/Import one link at a time/)).toBeInTheDocument();
    expect(calls).toHaveLength(0);
  });

  it("stops a paste with a --- separator line and points to the file import", async () => {
    const calls = mockFetch({});
    renderWithProviders(<ImportForm />);
    await userEvent.type(
      screen.getByRole("textbox", { name: /Paste one recipe/ }),
      "番茄炒蛋\n鸡蛋\n  ---  \n土豆丝\n土豆",
    );
    await userEvent.click(screen.getByRole("button", { name: "Extract" }));
    expect(await screen.findByText(/Paste one recipe at a time/)).toBeInTheDocument();
    expect(calls).toHaveLength(0);
  });

  it.each([
    ["a separator before the recipe", "---\n番茄炒蛋\n鸡蛋"],
    ["a separator after the recipe", "番茄炒蛋\n鸡蛋\n---\n"],
    ["separators around the recipe", "---\n\n番茄炒蛋\n---"],
  ])("still sends one recipe with %s", async (_case, pasted) => {
    const calls = mockFetch({ "POST /api/imports/text": () => draft() });
    renderWithProviders(<ImportForm />);
    await userEvent.type(screen.getByRole("textbox", { name: /Paste one recipe/ }), pasted);
    await userEvent.click(screen.getByRole("button", { name: "Extract" }));
    await waitFor(() => expect(calls).toHaveLength(1));
  });

  it("still sends text that mentions one link", async () => {
    const calls = mockFetch({ "POST /api/imports/text": () => draft() });
    renderWithProviders(<ImportForm />);
    await userEvent.type(
      screen.getByRole("textbox", { name: /Paste one recipe/ }),
      "番茄炒蛋，来自 https://a.example/1",
    );
    await userEvent.click(screen.getByRole("button", { name: "Extract" }));
    await waitFor(() => expect(calls).toHaveLength(1));
  });

  it("tells the user that imports reach a model provider", () => {
    renderWithProviders(<ImportForm />);
    expect(screen.getByText(/sent to a third-party AI model/)).toBeInTheDocument();
  });
});
