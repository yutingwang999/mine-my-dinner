import { describe, expect, it, vi } from "vitest";

import ChatRoute from "./page";

const redirect = vi.hoisted(() => vi.fn());

vi.mock("@/i18n/routing", () => ({
  routing: { locales: ["zh", "en"], defaultLocale: "zh" },
}));

vi.mock("@/i18n/navigation", () => ({ redirect }));

describe("chat route", () => {
  it("redirects to the home route in the same locale", async () => {
    await ChatRoute({ params: Promise.resolve({ locale: "en" }) });
    expect(redirect).toHaveBeenCalledWith({ href: "/", locale: "en" });
  });
});
