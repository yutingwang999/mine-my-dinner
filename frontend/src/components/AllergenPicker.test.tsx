import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { StrictMode } from "react";
import { afterEach, expect, it, vi } from "vitest";

import en from "../../messages/en.json";
import { AllergenPicker } from "@/components/AllergenPicker";
import { callsTo, mockFetch } from "@/test/helpers";

afterEach(() => vi.unstubAllGlobals());

it("asks the model once when the same name is requested at the same time", async () => {
  const calls = mockFetch({ "POST /api/allergen-suggestions": () => ({ suggested: ["soy"] }) });
  // Development mode runs effects twice, which used to send two paid requests.
  render(
    <StrictMode>
      <NextIntlClientProvider locale="en" messages={en}>
        <AllergenPicker name="紫薯酱" onChange={() => {}} />
      </NextIntlClientProvider>
    </StrictMode>,
  );
  expect(await screen.findByRole("checkbox", { name: "Soy" })).toBeChecked();
  expect(callsTo(calls, "POST", "/api/allergen-suggestions")).toHaveLength(1);
});
