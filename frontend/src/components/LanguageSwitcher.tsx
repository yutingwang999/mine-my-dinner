"use client";

import { useLocale, useTranslations } from "next-intl";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Link, usePathname, useRouter } from "@/i18n/navigation";
import { routing } from "@/i18n/routing";
import { useDrafts } from "@/lib/drafts";

type Target = (typeof routing.locales)[number];

/**
 * Switching the locale remounts the locale layout, which holds the draft queue,
 * so unsaved drafts are lost without passing through beforeunload. While drafts
 * are queued, the switch asks inline first.
 */
export function LanguageSwitcher() {
  // useSearchParams needs a Suspense boundary on prerendered pages; until it resolves,
  // the same links show without the query, so the switcher is never blank.
  return (
    <Suspense fallback={<Switcher search="" />}>
      <SwitcherWithQuery />
    </Suspense>
  );
}

function SwitcherWithQuery() {
  return <Switcher search={useSearchParams().toString()} />;
}

function Switcher({ search }: { search: string }) {
  const t = useTranslations("language");
  const common = useTranslations("common");
  const locale = useLocale();
  const pathname = usePathname();
  const router = useRouter();
  // Keep the query (tab, search, page) across the switch.
  const hrefWithQuery = search ? `${pathname}?${search}` : pathname;
  const { drafts } = useDrafts();
  const [confirming, setConfirming] = useState<Target | null>(null);

  return (
    <nav aria-label={t("label")}>
      {routing.locales.map((target) => (
        <Link
          key={target}
          href={hrefWithQuery}
          locale={target}
          aria-current={target === locale ? "true" : undefined}
          onClick={(event) => {
            if (target !== locale && drafts.length > 0) {
              event.preventDefault();
              setConfirming(target);
            }
          }}
        >
          {t(target)}
        </Link>
      ))}
      {confirming !== null && (
        <span role="group" className="confirm-box">
          <span>{t("switchWarning", { count: drafts.length })}</span>{" "}
          <button
            type="button"
            className="btn-danger-solid"
            onClick={() => router.push(hrefWithQuery, { locale: confirming })}
          >
            {t("switchAnyway")}
          </button>
          <button type="button" onClick={() => setConfirming(null)}>
            {common("cancel")}
          </button>
        </span>
      )}
    </nav>
  );
}
