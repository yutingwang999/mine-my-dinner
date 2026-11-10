"use client";

import { useTranslations } from "next-intl";

type Props = {
  page: number;
  pages: number;
  onPage: (page: number) => void;
};

/** Previous and next buttons with the page position. Hidden when there is one page. */
export function Pager({ page, pages, onPage }: Props) {
  const t = useTranslations("common");
  if (pages <= 1) {
    return null;
  }
  return (
    <nav className="pager">
      <button type="button" disabled={page <= 1} onClick={() => onPage(page - 1)}>
        {t("previous")}
      </button>
      <span>{t("pageOf", { page, pages })}</span>
      <button type="button" disabled={page >= pages} onClick={() => onPage(page + 1)}>
        {t("next")}
      </button>
    </nav>
  );
}
