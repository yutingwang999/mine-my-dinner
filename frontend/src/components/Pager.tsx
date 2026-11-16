"use client";

import { useTranslations } from "next-intl";

import { PAGE_SIZES } from "@/lib/pageSize";

type NavProps = {
  page: number;
  pages: number;
  onPage: (page: number) => void;
};

/** Previous and next buttons with the page position. */
function PageButtons({ page, pages, onPage }: NavProps) {
  const t = useTranslations("common");
  return (
    <span className="pager-nav">
      <button type="button" disabled={page <= 1} onClick={() => onPage(page - 1)}>
        {t("previous")}
      </button>
      <span>{t("pageOf", { page, pages })}</span>
      <button type="button" disabled={page >= pages} onClick={() => onPage(page + 1)}>
        {t("next")}
      </button>
    </span>
  );
}

/** The bar under a list: only the page buttons, and only when there is more than one page. */
export function PagerBottom(props: NavProps) {
  if (props.pages <= 1) return null;
  return (
    <nav className="pager pager-bottom">
      <PageButtons {...props} />
    </nav>
  );
}

type Props = NavProps & {
  pageSize: number;
  onPageSize: (size: number) => void;
  /** Rows in the whole list, not on this page. */
  total: number;
};

/** The bar above a list: the row count, the page buttons, and the rows-per-page select. */
export function Pager({ page, pages, onPage, pageSize, onPageSize, total }: Props) {
  const t = useTranslations("common");
  if (total < 1) return null;
  return (
    <nav className="pager">
      <span className="pager-total">{t("total", { total })}</span>
      <PageButtons page={page} pages={pages} onPage={onPage} />
      <label className="pager-size">
        {t("perPageBefore")}
        <select
          aria-label={t("perPageLabel")}
          value={pageSize}
          onChange={(event) => onPageSize(Number(event.target.value))}
        >
          {PAGE_SIZES.map((size) => (
            <option key={size} value={size}>
              {size}
            </option>
          ))}
        </select>
        {t("perPageAfter")}
      </label>
    </nav>
  );
}
