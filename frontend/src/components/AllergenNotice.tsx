import { useTranslations } from "next-intl";

/** Fixed text, never model output. It has no close control on purpose. */
export function AllergenNotice() {
  const t = useTranslations("chat");
  return (
    <p role="note" className="notice notice-warning allergen-notice">
      {t("allergenNotice")}
    </p>
  );
}
