"use client";

import { useTranslations } from "next-intl";

import { Link, usePathname } from "@/i18n/navigation";

const PAGES = ["home", "chat", "import", "review", "library", "inventory", "people", "dictionary"] as const;

export function MainNav() {
  const t = useTranslations("nav");
  const pathname = usePathname();

  return (
    <nav aria-label={t("label")}>
      {PAGES.map((page) => {
        const href = page === "home" ? "/" : `/${page}`;
        return (
          <Link key={page} href={href} aria-current={pathname === href ? "page" : undefined}>
            {t(page)}
          </Link>
        );
      })}
    </nav>
  );
}
