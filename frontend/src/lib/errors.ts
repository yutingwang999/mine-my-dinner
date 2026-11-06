import { useTranslations } from "next-intl";

import { ApiError } from "@/lib/api";

/** Turns any thrown value into a translated message from `errors.<code>`. */
export function useErrorText(): (error: unknown) => string {
  const t = useTranslations("errors");
  return (error: unknown) => {
    const code = error instanceof ApiError ? error.code : "unknown";
    if (!t.has(code)) {
      return t("unknown");
    }
    const term = error instanceof ApiError ? String(error.body.term ?? "") : "";
    return t(code, { term });
  };
}
