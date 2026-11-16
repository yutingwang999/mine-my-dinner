"use client";

import { useTranslations } from "next-intl";
import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { Pager } from "@/components/Pager";
import { RecipeToggles } from "@/components/RecipeToggles";
import { Link, useRouter } from "@/i18n/navigation";
import { api, DISH_TYPES } from "@/lib/api";
import { useErrorText } from "@/lib/errors";
import { useLoad } from "@/lib/useLoad";

const PAGE_SIZE = 20;

/** The library's search state as a query string: q, type, and page (omitted when default). */
function searchString(q: string, dishType: string, page: number): string {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (dishType) params.set("type", dishType);
  if (page > 1) params.set("page", String(page));
  return params.toString();
}

export function RecipeLibrary() {
  const t = useTranslations("library");
  const tCommon = useTranslations("common");
  const tDish = useTranslations("dishType");
  const errorText = useErrorText();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [q, setQ] = useState(() => searchParams.get("q") ?? "");
  const [dishType, setDishType] = useState(() => searchParams.get("type") ?? "");
  const [page, setPage] = useState(() => {
    const value = Number(searchParams.get("page"));
    return Number.isInteger(value) && value >= 1 ? value : 1;
  });
  const { data, error } = useLoad(
    () => api.listRecipes({ q, dish_type: dishType, page }),
    [q, dishType, page],
  );

  const qs = searchString(q, dishType, page);
  const urlQs = searchString(
    searchParams.get("q") ?? "",
    searchParams.get("type") ?? "",
    Number(searchParams.get("page")) || 1,
  );
  // Addresses this component wrote that the URL has not reported back yet.
  const written = useRef<string[]>([]);

  // Keep the address in step with the filters, replacing the entry so typing leaves no history.
  useEffect(() => {
    if (urlQs !== qs) {
      written.current.push(qs);
      router.replace(qs ? `/library?${qs}` : "/library");
    }
    // Only the filters drive the address; the router and params objects change every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qs]);

  // Follow the address when it changes from outside (nav link, back/forward). An address this
  // component wrote itself is not an outside change, so it must not reset what is being typed.
  useEffect(() => {
    const index = written.current.indexOf(urlQs);
    if (index !== -1) {
      written.current = written.current.slice(index + 1);
      return;
    }
    written.current = [];
    const next = new URLSearchParams(urlQs);
    setQ(next.get("q") ?? "");
    setDishType(next.get("type") ?? "");
    setPage(Number(next.get("page")) || 1);
  }, [urlQs]);

  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <section className="card">
      <div className="browse-filters">
        <input
          type="search"
          aria-label={t("search")}
          placeholder={t("search")}
          value={q}
          onChange={(event) => {
            setQ(event.target.value);
            setPage(1);
          }}
        />
        <label>
          {t("dishTypeFilter")}
          <select
            value={dishType}
            onChange={(event) => {
              setDishType(event.target.value);
              setPage(1);
            }}
          >
            <option value="">{t("allTypes")}</option>
            {DISH_TYPES.map((value) => (
              <option key={value} value={value}>
                {tDish(value)}
              </option>
            ))}
          </select>
        </label>
      </div>
      {error !== null && (
        <p role="alert" className="notice notice-warning">
          {errorText(error)}
        </p>
      )}
      {data === null && error === null && <p>{tCommon("loading")}</p>}
      {data?.items.length === 0 && <p className="hint">{t("empty")}</p>}
      <ul className="row-list">
        {data?.items.map((recipe) => (
          <li key={recipe.id} className="recipe-row" data-testid={`recipe-${recipe.id}`}>
            <RecipeToggles
              recipeId={recipe.id}
              recipe={recipe}
              title={
                <Link
                  className="recipe-title"
                  href={`/library/${recipe.id}${qs ? `?from=${encodeURIComponent(qs)}` : ""}`}
                >
                  {recipe.title}
                  <span aria-hidden="true"> ›</span>
                </Link>
              }
            >
              <span className="hint">
                {tDish(recipe.dish_type)}
                {recipe.tags.length > 0 && ` · ${recipe.tags.join(", ")}`}
              </span>
              {recipe.supplies.length > 0 && (
                <small className="hint">{t("supplies", { names: recipe.supplies.join(", ") })}</small>
              )}
              {!recipe.embedded && <small className="hint">{t("noEmbedding")}</small>}
            </RecipeToggles>
          </li>
        ))}
      </ul>
      <Pager page={page} pages={pages} onPage={setPage} />
    </section>
  );
}
