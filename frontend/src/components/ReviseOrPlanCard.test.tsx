import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import zh from "../../messages/zh.json";
import { ReviseOrPlanCard } from "@/components/ReviseOrPlanCard";
import { renderWithProviders } from "@/test/helpers";

describe("ReviseOrPlanCard", () => {
  it("shows the message and both buttons, and sends the choice", async () => {
    const onChoose = vi.fn();
    renderWithProviders(<ReviseOrPlanCard message="something lighter" onChoose={onChoose} />);
    expect(screen.getByText("something lighter")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Change this plan" }));
    expect(onChoose).toHaveBeenLastCalledWith("revise");
    await userEvent.click(screen.getByRole("button", { name: "Plan a new meal" }));
    expect(onChoose).toHaveBeenLastCalledWith("plan");
  });

  it("disables both buttons while busy", () => {
    renderWithProviders(<ReviseOrPlanCard message="x" busy onChoose={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Change this plan" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Plan a new meal" })).toBeDisabled();
  });

  it("is written in Chinese", () => {
    render(
      <NextIntlClientProvider locale="zh" messages={zh}>
        <ReviseOrPlanCard message="清淡一点" onChoose={vi.fn()} />
      </NextIntlClientProvider>,
    );
    expect(screen.getByRole("button", { name: "在这份方案上改" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新规划一顿" })).toBeInTheDocument();
  });
});
