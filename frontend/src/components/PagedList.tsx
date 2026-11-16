"use client";

import { type ReactNode, useState } from "react";

import { Pager, PagerBottom } from "@/components/Pager";
import { paginate, usePageSize } from "@/lib/pageSize";

/**
 * A client-paginated list: the pager above, the rows, and the page buttons below.
 * The page size is remembered under `pageSize:<list>`; a page past the end shows the last.
 */
export function PagedList<T>({
  items,
  list,
  className = "row-list",
  children,
}: {
  items: T[];
  list: string;
  className?: string;
  children: (visible: T[]) => ReactNode;
}) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = usePageSize(list);
  const { current, pages, visible } = paginate(items, page, pageSize);
  return (
    <>
      <Pager
        page={current}
        pages={pages}
        onPage={setPage}
        pageSize={pageSize}
        onPageSize={(size) => {
          setPageSize(size);
          setPage(1);
        }}
        total={items.length}
      />
      <ul className={className}>{children(visible)}</ul>
      <PagerBottom page={current} pages={pages} onPage={setPage} />
    </>
  );
}
