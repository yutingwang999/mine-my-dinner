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
    await userEvent.type(screen.getByRole("textbox", { name: /Paste recipe text/ }), "葱花蛋");
    await userEvent.click(screen.getByRole("button", { name: "Extract" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/review"));
    expect(screen.getByTestId("queue")).toHaveTextContent("葱花蛋");
  });

  it("keeps the pasted text and shows a message when import fails", async () => {
    mockFetch({ "POST /api/imports/text": () => reply(503, { code: "models_unavailable" }) });
    renderWithProviders(<ImportForm />);
    const box = screen.getByRole("textbox", { name: /Paste recipe text/ });
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

  it("tells the user that imports reach a model provider", () => {
    renderWithProviders(<ImportForm />);
    expect(screen.getByText(/sent to a model provider/)).toBeInTheDocument();
  });
});
