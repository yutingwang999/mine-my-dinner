import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MainNav } from "@/components/MainNav";
import { renderWithProviders } from "@/test/helpers";

// Loading the real next-intl/routing module breaks Vitest's collection, so the i18n modules are mocked.
vi.mock("@/i18n/routing", () => ({
  routing: { locales: ["zh", "en"], defaultLocale: "zh" },
}));

vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
  usePathname: () => "/",
}));

describe("MainNav", () => {
  it("has no separate chat link; Home leads to the chat", () => {
    renderWithProviders(<MainNav />);
    expect(screen.queryByRole("link", { name: "Chat" })).toBeNull();
    expect(screen.getByRole("link", { name: "Home" })).toHaveAttribute("href", "/");
  });
});
