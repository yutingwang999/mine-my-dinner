import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import en from "../../messages/en.json";
import { Composer } from "@/components/Composer";

function Harness({ busy = false, onSend }: { busy?: boolean; onSend: () => void }) {
  const [value, setValue] = useState("");
  return <Composer busy={busy} onSend={onSend} value={value} onChange={setValue} />;
}

function renderComposer(props: { busy?: boolean; onSend: () => void }) {
  return render(
    <NextIntlClientProvider locale="en" messages={en}>
      <Harness {...props} />
    </NextIntlClientProvider>,
  );
}

const box = () => screen.getByRole("textbox", { name: "Your message" });

describe("Composer", () => {
  it("keeps the composer sizing class and gives Send the primary class", () => {
    renderComposer({ onSend: vi.fn() });
    const send = screen.getByRole("button", { name: "Send" });
    expect(send).toHaveClass("composer-action");
    expect(send).toHaveClass("btn-primary");
  });

  it("sends on Enter", async () => {
    const onSend = vi.fn();
    renderComposer({ onSend });
    await userEvent.type(box(), "hi{Enter}");
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("inserts a line break on Shift+Enter and does not send", async () => {
    const onSend = vi.fn();
    renderComposer({ onSend });
    await userEvent.type(box(), "a{Shift>}{Enter}{/Shift}b");
    expect(box()).toHaveValue("a\nb");
    expect(onSend).not.toHaveBeenCalled();
  });

  it("sends on Enter with keyCode 229 when no composition is active", async () => {
    const onSend = vi.fn();
    renderComposer({ onSend });
    await userEvent.type(box(), "ni");
    fireEvent.keyDown(box(), { key: "Enter", keyCode: 229 });
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("does not send on Enter during composition", async () => {
    const onSend = vi.fn();
    renderComposer({ onSend });
    await userEvent.type(box(), "ni");
    fireEvent.compositionStart(box());
    fireEvent.keyDown(box(), { key: "Enter" });
    fireEvent.keyDown(box(), { key: "Enter", isComposing: true });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("sends on a later Enter after compositionend", async () => {
    const onSend = vi.fn();
    renderComposer({ onSend });
    await userEvent.type(box(), "ni");
    fireEvent.compositionStart(box());
    fireEvent.compositionEnd(box());
    await new Promise((resolve) => setTimeout(resolve, 5));
    fireEvent.keyDown(box(), { key: "Enter" });
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("ignores the Enter that arrives in the same tick as compositionend", async () => {
    const onSend = vi.fn();
    renderComposer({ onSend });
    await userEvent.type(box(), "ni");
    fireEvent.compositionStart(box());
    fireEvent.compositionEnd(box());
    fireEvent.keyDown(box(), { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("disables the send button when empty or busy and ignores Enter then", async () => {
    const onSend = vi.fn();
    const { unmount } = renderComposer({ onSend });
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    await userEvent.type(box(), "{Enter}");
    expect(onSend).not.toHaveBeenCalled();
    unmount();
    renderComposer({ onSend, busy: true });
    await userEvent.type(box(), "text{Enter}");
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    expect(onSend).not.toHaveBeenCalled();
  });
});
