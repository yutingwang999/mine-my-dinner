import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import * as navigation from "@/i18n/navigation";
import { draft, renderWithProviders } from "@/test/helpers";

// Loading the real next-intl/routing module breaks Vitest's collection, so both
// i18n modules are mocked, as in the other component tests.
vi.mock("@/i18n/routing", () => ({
  routing: { locales: ["zh", "en"], defaultLocale: "zh" },
}));

vi.mock("@/i18n/navigation", () => {
  const push = vi.fn();
  return {
    Link: ({
      href,
      children,
      onClick,
      ...rest
    }: {
      href: string;
      children: React.ReactNode;
      onClick?: React.MouseEventHandler;
    }) => (
      <a href={href} onClick={onClick} {...rest}>
        {children}
      </a>
    ),
    usePathname: () => "/review",
    useRouter: () => ({ push }),
    // Only the mock exports this, so tests can assert on navigation.
    __push: push,
  };
});

const { __push: push } = navigation as unknown as { __push: ReturnType<typeof vi.fn> };

describe("LanguageSwitcher", () => {
  beforeEach(() => push.mockClear());

  // renderWithProviders renders with locale "en", so 中文 is the other language.
  it("switches without asking when there are no drafts", async () => {
    renderWithProviders(<LanguageSwitcher />);

    await userEvent.click(screen.getByRole("link", { name: "中文" }));

    expect(screen.queryByRole("group")).not.toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });

  it("asks before switching while drafts are queued, and switches on confirm", async () => {
    renderWithProviders(<LanguageSwitcher />, [draft(), draft({ title: "第二道" })]);

    await userEvent.click(screen.getByRole("link", { name: "中文" }));

    const box = screen.getByRole("group");
    expect(box).toHaveTextContent("2");
    expect(push).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Switch anyway" }));

    expect(push).toHaveBeenCalledWith("/review", { locale: "zh" });
  });

  it("keeps the drafts and closes the question on cancel", async () => {
    renderWithProviders(<LanguageSwitcher />, [draft()]);

    await userEvent.click(screen.getByRole("link", { name: "中文" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByRole("group")).not.toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
    expect(screen.getByTestId("queue")).toHaveTextContent("葱花蛋");
  });

  it("does not ask when clicking the current language", async () => {
    renderWithProviders(<LanguageSwitcher />, [draft()]);

    await userEvent.click(screen.getByRole("link", { name: "English" }));

    expect(screen.queryByRole("group")).not.toBeInTheDocument();
  });
});
