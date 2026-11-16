"use client";

import { useTranslations } from "next-intl";
import { useSearchParams } from "next/navigation";
import { useState } from "react";

import { ConfirmDelete } from "@/components/ConfirmDelete";
import { RecipeDetail } from "@/components/RecipeDetail";
import { ReviewForm } from "@/components/ReviewForm";
import { SourcePanel } from "@/components/SourcePanel";
import { Link, useRouter } from "@/i18n/navigation";
import { api, ApiError } from "@/lib/api";
import { useLoad } from "@/lib/useLoad";

/** The read-only page of one saved recipe, with a way back, edit, and delete. */
export function RecipePage({ id }: { id: number }) {
  const t = useTranslations("library");
  const tDetail = useTranslations("recipeDetail");
  const tCommon = useTranslations("common");
  const router = useRouter();
  const searchParams = useSearchParams();
  const [editing, setEditing] = useState(false);
  const { data, error, reload } = useLoad(
    () => (Number.isInteger(id) ? api.getRecipe(id) : Promise.reject(new ApiError(404, "not_found"))),
    [id],
  );

  // The library's own query string (search, type, page) travels in `from`, so Back keeps it.
  const from = searchParams.get("from");
  const libraryHref = from ? `/library?${from}` : "/library";
  const back = <Link href={libraryHref}>{t("back")}</Link>;

  if (error !== null) {
    const notFound = error instanceof ApiError && error.status === 404;
    return (
      <section className="recipe-page">
        <p role="alert" className="notice notice-warning">
          {notFound ? t("notFound") : t("loadFailed")}
        </p>
        <div className="recipe-page-links">{back}</div>
      </section>
    );
  }
  if (data === null) return <p>{tCommon("loading")}</p>;

  if (editing) {
    return (
      <section>
        <h2>{t("editTitle", { title: data.title })}</h2>
        <ReviewForm
          // Saved screenshots stay on the server; an edit uploads nothing new.
          draft={{ ...data, provenance: null, images: undefined }}
          save={(write) => api.updateRecipe(data.id, { ...write, provenance: null })}
          onSaved={() => {
            setEditing(false);
            reload();
          }}
          actions={
            <button type="button" onClick={() => setEditing(false)}>
              {tCommon("cancel")}
            </button>
          }
        />
      </section>
    );
  }

  return (
    <section className="recipe-page">
      <div className="recipe-page-links">{back}</div>
      <div className="recipe-page-header">
        <h1>{data.title}</h1>
        <span className="actions recipe-page-actions">
          <button type="button" onClick={() => setEditing(true)}>
            {tCommon("edit")}
          </button>
          <ConfirmDelete
            name={data.title}
            notice={t("deleteNotice")}
            onDelete={async () => {
              await api.deleteRecipe(data.id);
              router.push(libraryHref);
            }}
          />
        </span>
      </div>
      <RecipeDetail recipe={data} />
      <section className="recipe-detail">
        <h3>{tDetail("originalText")}</h3>
        <SourcePanel recipe={data} />
      </section>
    </section>
  );
}
