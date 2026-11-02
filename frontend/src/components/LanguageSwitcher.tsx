"use client";

import { useLocale, useTranslations } from "next-intl";

import { Link, usePathname } from "@/i18n/navigation";
import { routing } from "@/i18n/routing";

export function LanguageSwitcher() {
  const t = useTranslations("language");
  const locale = useLocale();
  const pathname = usePathname();

  return (
    <nav aria-label={t("label")}>
      {routing.locales.map((target) => (
        <Link
          key={target}
          href={pathname}
          locale={target}
          aria-current={target === locale ? "true" : undefined}
          style={{ marginRight: "0.75rem" }}
        >
          {t(target)}
        </Link>
      ))}
    </nav>
  );
}
