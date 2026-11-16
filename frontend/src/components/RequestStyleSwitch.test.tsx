import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RequestStyleSwitch } from "@/components/RequestStyleSwitch";
import { renderWithProviders } from "@/test/helpers";

const name = "Ask me first when details are missing";

describe("RequestStyleSwitch", () => {
  it("reads ask_first as on and offers the other value", async () => {
    const onChange = vi.fn();
    renderWithProviders(<RequestStyleSwitch value="ask_first" onChange={onChange} />);
    const toggle = screen.getByRole("switch", { name });
    expect(toggle).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText("Ask me first")).toBeInTheDocument();
    await userEvent.click(toggle);
    expect(onChange).toHaveBeenCalledWith("plan_then_adjust");
  });

  it("reads plan_then_adjust and null as off", async () => {
    const onChange = vi.fn();
    const { unmount } = renderWithProviders(<RequestStyleSwitch value="plan_then_adjust" onChange={onChange} />);
    expect(screen.getByRole("switch", { name })).toHaveAttribute("aria-checked", "false");
    unmount();
    renderWithProviders(<RequestStyleSwitch value={null} onChange={onChange} />);
    const toggle = screen.getByRole("switch", { name });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText("Plan, then I adjust")).toBeInTheDocument();
    await userEvent.click(toggle);
    expect(onChange).toHaveBeenCalledWith("ask_first");
  });

  it("toggles from the keyboard", async () => {
    const onChange = vi.fn();
    renderWithProviders(<RequestStyleSwitch value={null} onChange={onChange} />);
    screen.getByRole("switch", { name }).focus();
    await userEvent.keyboard(" ");
    expect(onChange).toHaveBeenCalledWith("ask_first");
  });
});
