import { expect, it } from "vitest";

import en from "../../messages/en.json";
import zh from "../../messages/zh.json";

function flatKeys(value: object, prefix = ""): string[] {
  return Object.entries(value).flatMap(([key, child]) =>
    typeof child === "object" && child !== null
      ? flatKeys(child, `${prefix}${key}.`)
      : [`${prefix}${key}`],
  );
}

it("zh and en define the same message keys", () => {
  expect(flatKeys(zh).sort()).toEqual(flatKeys(en).sort());
});
