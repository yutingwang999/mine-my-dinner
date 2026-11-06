"use client";

import { useTranslations } from "next-intl";

import { Link } from "@/i18n/navigation";

const PAGES = ["home", "import", "review", "library", "inventory", "people", "dictionary"] as const;

export function MainNav() {
  const t = useTranslations("nav");

  return (
    <nav aria-label={t("label")}>
      {PAGES.map((page) => (
        <Link
          key={page}
          href={page === "home" ? "/" : `/${page}`}
          style={{ marginRight: "0.75rem" }}
        >
          {t(page)}
        </Link>
      ))}
    </nav>
  );
}
